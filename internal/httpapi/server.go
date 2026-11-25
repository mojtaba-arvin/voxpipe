package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
)

const (
	scopeIngestionRead  = "ingestion:read"
	scopeIngestionWrite = "ingestion:write"
	scopeTranscriptRead = "transcript:read"
	scopeTranscriptIndex = "transcript:index"
	scopeOperationsRead = "operations:read"
)

var (
	idempotencyPattern = regexp.MustCompile(`^[A-Za-z0-9._:-]{16,128}$`)
	sha256Pattern      = regexp.MustCompile(`^[a-fA-F0-9]{64}$`)
	languagePattern    = regexp.MustCompile("^[A-Za-z0-9][A-Za-z0-9-]{1,34}$")
)

type Server struct {
	store        Store
	auth         Authenticator
	stager       Stager
	maxUpload    int64
	sessionTTL   time.Duration
	maxAttempts  int
	retentionDays int
	tenantLimits TenantLimits
	uploadIdleTimeout time.Duration
	uploadMaxDuration time.Duration
	clock        func() time.Time
	retrieval    HybridRetrievalClient
}

type TenantLimits struct {
	MaxConcurrentUploads int
	MaxStagedBytes int64
	MaxIngestionsPerMinute int
}

type CreateIngestionRequest struct {
	ExpectedSizeBytes int64          `json:"expected_size_bytes"`
	ExpectedSHA256    string         `json:"expected_sha256"`
	ContentTypeHint   string         `json:"content_type_hint,omitempty"`
	Source            SourceMetadata `json:"source"`
}

type SourceMetadata struct {
	Kind              string `json:"kind"`
	ExternalReference string `json:"external_reference,omitempty"`
	RecordedAt        string `json:"recorded_at,omitempty"`
	DeclaredBy        string `json:"declared_by,omitempty"`
}

func NewServer(store Store, auth Authenticator, stager Stager, maxUpload int64, sessionTTL time.Duration, maxAttempts int) (*Server, error) {
	if store == nil || auth == nil || stager == nil {
		return nil, fmt.Errorf("store, authenticator, and stager are required")
	}
	if maxUpload < 1 || sessionTTL <= 0 || sessionTTL > 365*24*time.Hour || maxAttempts < 1 || maxAttempts > 100 {
		return nil, fmt.Errorf("invalid upload or session limits")
	}
	return &Server{
		store: store, auth: auth, stager: stager, maxUpload: maxUpload,
		sessionTTL: sessionTTL, maxAttempts: maxAttempts, clock: time.Now,
		tenantLimits: TenantLimits{MaxConcurrentUploads: 3, MaxStagedBytes: 2 << 30, MaxIngestionsPerMinute: 60},
	}, nil
}

func (s *Server) SetHybridRetrievalClient(client HybridRetrievalClient) {
	s.retrieval = client
}

func (s *Server) SetTenantLimits(limits TenantLimits) error {
	if limits.MaxConcurrentUploads < 1 || limits.MaxConcurrentUploads > 100 || limits.MaxStagedBytes < 1 || limits.MaxIngestionsPerMinute < 1 || limits.MaxIngestionsPerMinute > 100000 {
		return fmt.Errorf("tenant quota limits are invalid")
	}
	s.tenantLimits = limits
	return nil
}

func (s *Server) SetUploadTimeouts(idleTimeout, maxDuration time.Duration) error {
	if idleTimeout < time.Second || maxDuration < idleTimeout || maxDuration > 24*time.Hour {
		return fmt.Errorf("upload timeouts must be positive and max duration must be at least the inactivity timeout")
	}
	s.uploadIdleTimeout = idleTimeout
	s.uploadMaxDuration = maxDuration
	return nil
}

func (s *Server) SetRetentionDays(days int) error {
	if days < 0 || days > 36500 { return fmt.Errorf("retention days must be between 0 and 36500") }
	s.retentionDays = days
	return nil
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health/live", s.healthLive)
	mux.HandleFunc("GET /health/ready", s.healthReady)
	mux.HandleFunc("POST /v1/ingestions", s.createIngestion)
	mux.HandleFunc("PUT /v1/ingestions/{session_id}/content", s.uploadContent)
	mux.HandleFunc("POST /v1/ingestions/{session_id}/finalize", s.finalizeIngestion)
	mux.HandleFunc("GET /v1/ingestions/{session_id}", s.getIngestion)
	mux.HandleFunc("DELETE /v1/ingestions/{session_id}", s.cancelIngestion)
	mux.HandleFunc("GET /v1/assets/{asset_id}", s.getAsset)
	mux.HandleFunc("GET /v1/assets/search", s.searchAssets)
	mux.HandleFunc("GET /v1/transcripts/search", s.searchTranscripts)
	mux.HandleFunc("POST /v1/retrieval/hybrid-search", s.hybridSearch)
	mux.HandleFunc("POST /v1/transcripts/{transcript_id}/index", s.indexTranscript)
	mux.HandleFunc("GET /v1/operations/ingestion/summary", s.getOperationalSummary)
	return mux
}

func (s *Server) createIngestion(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeIngestionWrite)
	if !ok { return }
	key := r.Header.Get("Idempotency-Key")
	if !idempotencyPattern.MatchString(key) {
		writeError(w, http.StatusBadRequest, "INVALID_IDEMPOTENCY_KEY", "A valid Idempotency-Key header is required")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 64*1024)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	var request CreateIngestionRequest
	if err := decoder.Decode(&request); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_REQUEST", "Request body is invalid")
		return
	}
	if err := ensureJSONEOF(decoder); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_REQUEST", "Request body must contain one JSON object")
		return
	}
	if request.ExpectedSizeBytes < 1 || request.ExpectedSizeBytes > s.maxUpload {
		writeError(w, http.StatusRequestEntityTooLarge, "UPLOAD_SIZE_LIMIT", "Declared upload size is outside configured limits")
		return
	}
	if !sha256Pattern.MatchString(request.ExpectedSHA256) {
		writeError(w, http.StatusBadRequest, "INVALID_CHECKSUM", "expected_sha256 must be a SHA-256 hex digest")
		return
	}
	if !validSource(request.Source) || len(request.ContentTypeHint) > 128 {
		writeError(w, http.StatusBadRequest, "INVALID_SOURCE", "Source or retention metadata is invalid")
		return
	}

	sessionID, err := newUUID()
	if err != nil { writeError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "Unable to create session"); return }
	assetID, err := newUUID()
	if err != nil { writeError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "Unable to create session"); return }
	reference, err := s.stager.Reference(principal.TenantID, sessionID)
	if err != nil { writeError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "Unable to prepare upload"); return }
	now := s.clock().UTC()
	timestamp := now.Format(time.RFC3339Nano)
	upload := map[string]any{
		"expected_size_bytes": request.ExpectedSizeBytes,
		"received_size_bytes": int64(0),
		"expected_sha256": strings.ToLower(request.ExpectedSHA256),
	}
	if request.ContentTypeHint != "" { upload["content_type_hint"] = request.ContentTypeHint }
	session := map[string]any{
		"schema_version": "1.0.0",
		"session_id": sessionID,
		"tenant_id": principal.TenantID,
		"mode": "batch",
		"source": sourceMap(request.Source),
		"state": "created",
		"idempotency_key": key,
		"asset_id": assetID,
		"staging_reference": reference,
		"staging_backend": s.stager.Backend(),
		"upload": upload,
		"created_at": timestamp,
		"updated_at": timestamp,
		"expires_at": now.Add(s.sessionTTL).Format(time.RFC3339Nano),
	}
	if s.retentionDays > 0 {
		session["retention_policy_id"] = "deployment-default"
		session["retention_expires_at"] = now.Add(time.Duration(s.retentionDays) * 24 * time.Hour).Format(time.RFC3339Nano)
	}

	saved, created, err := s.store.CreateSession(r.Context(), principal.TenantID, key, session, s.tenantLimits)
	if err != nil { writeStoreError(w, err); return }
	savedUpload, _ := saved["upload"].(map[string]any)
	if !sameCreateRequest(saved, savedUpload, request, s.stager.Backend()) {
		writeError(w, http.StatusConflict, "IDEMPOTENCY_CONFLICT", "Idempotency key was already used for a different request")
		return
	}
	id, _ := saved["session_id"].(string)
	state, _ := saved["state"].(string)
	size, _ := numberInt64(savedUpload["expected_size_bytes"])
	checksum, _ := savedUpload["expected_sha256"].(string)
	status := http.StatusCreated
	if !created { status = http.StatusOK }
	writeJSON(w, status, map[string]any{
		"session_id": id,
		"state": state,
		"expected_size_bytes": size,
		"expected_sha256": checksum,
		"upload_url": "/v1/ingestions/" + id + "/content",
	})
}

func (s *Server) uploadContent(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeIngestionWrite)
	if !ok { return }
	sessionID := r.PathValue("session_id")
	if !isUUID(sessionID) { writeError(w, http.StatusNotFound, "NOT_FOUND", "Ingestion session was not found"); return }
	if mediaType := strings.ToLower(strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0])); mediaType != "application/octet-stream" {
		writeError(w, http.StatusUnsupportedMediaType, "UNSUPPORTED_CONTENT_TYPE", "Use application/octet-stream for media bytes")
		return
	}
	session, err := s.store.GetSession(r.Context(), principal.TenantID, sessionID)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	stagingBackend, _ := session["staging_backend"].(string)
	if stagingBackend == "" {
		stagingBackend = "local"
	}
	if stagingBackend != s.stager.Backend() {
		writeError(w, http.StatusConflict, "STAGING_BACKEND_MISMATCH", "Session staging backend differs from this API instance")
		return
	}
	if !s.sessionStagingReferenceMatches(session, principal.TenantID, sessionID) {
		writeError(w, http.StatusConflict, "STAGING_REFERENCE_MISMATCH", "Session staging reference is invalid")
		return
	}
	state, _ := session["state"].(string)
	if state != "created" && state != "receiving" {
		writeError(w, http.StatusConflict, "INVALID_SESSION_STATE", "Session is not accepting upload bytes")
		return
	}
	ownerID, err := newUUID()
	if err != nil { writeError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "Unable to reserve upload lease"); return }
	if err := s.store.BeginUpload(r.Context(), principal.TenantID, sessionID, ownerID, min(31622400, max(1, int((s.sessionTTL + time.Hour).Seconds()))), s.tenantLimits.MaxConcurrentUploads); err != nil {
		writeStoreError(w, err)
		return
	}
	uploadOutcome, uploadErrorCode := "failed", ""
	defer func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = s.store.EndUpload(cleanupCtx, principal.TenantID, sessionID, ownerID, uploadOutcome, uploadErrorCode)
	}()

	upload, _ := session["upload"].(map[string]any)
	expectedSize, sizeOK := numberInt64(upload["expected_size_bytes"])
	expectedDigest, digestOK := upload["expected_sha256"].(string)
	if !sizeOK || !digestOK {
		writeError(w, http.StatusConflict, "INVALID_SESSION_STATE", "Session upload metadata is incomplete")
		return
	}
	if r.ContentLength >= 0 && r.ContentLength != expectedSize {
		writeError(w, http.StatusBadRequest, "UPLOAD_SIZE_MISMATCH", "Content-Length does not match declared upload size")
		return
	}
	var uploadReader io.Reader = r.Body
	var responseController *http.ResponseController
	if s.uploadIdleTimeout > 0 && s.uploadMaxDuration > 0 {
		responseController = http.NewResponseController(w)
		uploadReader = &boundedUploadReader{reader: r.Body, controller: responseController, started: time.Now(), inactivityTimeout: s.uploadIdleTimeout, maxDuration: s.uploadMaxDuration}
	}
	written, _, err := s.stager.Write(r.Context(), principal.TenantID, sessionID, uploadReader, expectedSize, s.maxUpload, expectedDigest)
	if responseController != nil { _ = responseController.SetReadDeadline(time.Time{}) }
	if errors.Is(err, ErrUploadTimeout) {
		writeError(w, http.StatusRequestTimeout, "UPLOAD_TIMEOUT", "Upload exceeded its configured duration or inactivity limit")
		return
	}
	if errors.Is(err, ErrUploadTooLarge) {
		writeError(w, http.StatusRequestEntityTooLarge, "UPLOAD_SIZE_LIMIT", "Upload exceeds configured limits")
		return
	}
	if errors.Is(err, ErrUploadIntegrity) {
		writeError(w, http.StatusBadRequest, "UPLOAD_INTEGRITY_MISMATCH", "Upload size or checksum does not match the declaration")
		return
	}
	if errors.Is(err, ErrUploadConflict) {
		writeError(w, http.StatusConflict, "UPLOAD_CONFLICT", "A different upload is already staged for this session")
		return
	}
	if err != nil { writeError(w, http.StatusInternalServerError, "STAGING_FAILED", "Unable to stage upload"); return }

	updated, err := cloneJSONMap(session)
	if err != nil { writeError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "Unable to update session"); return }
	updatedUpload, _ := updated["upload"].(map[string]any)
	updatedUpload["received_size_bytes"] = written
	updated["state"] = "receiving"
	updated["updated_at"] = s.clock().UTC().Format(time.RFC3339Nano)
	if err := s.store.UpdateSession(r.Context(), principal.TenantID, sessionID, state, updated); err != nil {
		latest, readErr := s.store.GetSession(r.Context(), principal.TenantID, sessionID)
		if readErr == nil {
			latestState, _ := latest["state"].(string)
			latestUpload, _ := latest["upload"].(map[string]any)
			latestSize, _ := numberInt64(latestUpload["received_size_bytes"])
			if latestState == "receiving" && latestSize == expectedSize {
				uploadOutcome = "succeeded"
				w.WriteHeader(http.StatusNoContent)
				return
			}
			if latestState == "cancelled" {
				// Cancellation may have removed staging just before this upload
				// atomically published its file. Re-check after the failed CAS.
				_ = s.stager.Remove(r.Context(), principal.TenantID, sessionID)
			}
		}
		writeStoreError(w, err)
		return
	}
	uploadOutcome = "succeeded"
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) finalizeIngestion(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeIngestionWrite)
	if !ok { return }
	sessionID := r.PathValue("session_id")
	if !isUUID(sessionID) { writeError(w, http.StatusNotFound, "NOT_FOUND", "Ingestion session was not found"); return }
	session, err := s.store.GetSession(r.Context(), principal.TenantID, sessionID)
	if err != nil { writeStoreError(w, err); return }
	stagingBackend, _ := session["staging_backend"].(string)
	if stagingBackend == "" {
		stagingBackend = "local"
	}
	if stagingBackend != s.stager.Backend() {
		writeError(w, http.StatusConflict, "STAGING_BACKEND_MISMATCH", "Session staging backend differs from this API instance")
		return
	}
	if !s.sessionStagingReferenceMatches(session, principal.TenantID, sessionID) {
		writeError(w, http.StatusConflict, "STAGING_REFERENCE_MISMATCH", "Session staging reference is invalid")
		return
	}
	state, _ := session["state"].(string)
	if state != "receiving" && state != "finalizing" && state != "queued" && state != "processing" && state != "completed" {
		writeError(w, http.StatusConflict, "UPLOAD_NOT_READY", "A complete upload is required before finalization")
		return
	}
	upload, _ := session["upload"].(map[string]any)
	expectedSize, sizeOK := numberInt64(upload["expected_size_bytes"])
	expectedDigest, digestOK := upload["expected_sha256"].(string)
	if !sizeOK || !digestOK {
		writeError(w, http.StatusConflict, "UPLOAD_NOT_READY", "Upload metadata is incomplete")
		return
	}
	actualSize, actualDigest, err := s.stager.Verify(r.Context(), principal.TenantID, sessionID)
	if err != nil || actualSize != expectedSize || actualDigest != strings.ToLower(expectedDigest) {
		writeError(w, http.StatusConflict, "UPLOAD_NOT_READY", "Staged upload is missing or failed integrity verification")
		return
	}
	updated, err := cloneJSONMap(session)
	if err != nil { writeError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "Unable to finalize session"); return }
	updated["state"] = "finalizing"
	updated["updated_at"] = s.clock().UTC().Format(time.RFC3339Nano)
	jobID, err := newUUID()
	if err != nil { writeError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "Unable to create job"); return }
	job, err := s.store.FinalizeSession(r.Context(), principal.TenantID, sessionID, jobID, s.maxAttempts, updated)
	if err != nil { writeStoreError(w, err); return }
	writeJSON(w, http.StatusAccepted, map[string]any{
		"session_id": sessionID,
		"job_id": job["job_id"],
		"state": job["state"],
	})
}

func (s *Server) getIngestion(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeIngestionRead)
	if !ok { return }
	sessionID := r.PathValue("session_id")
	if !isUUID(sessionID) { writeError(w, http.StatusNotFound, "NOT_FOUND", "Ingestion session was not found"); return }
	session, job, err := s.store.GetStatus(r.Context(), principal.TenantID, sessionID)
	if err != nil { writeStoreError(w, err); return }
	response := map[string]any{
		"session_id": sessionID,
		"state": session["state"],
		"created_at": session["created_at"],
		"updated_at": session["updated_at"],
	}
	if job != nil { response["job"] = job }
	if assetID, exists := session["asset_id"]; exists && session["state"] == "completed" { response["asset_id"] = assetID }
	if failure, exists := session["failure"]; exists { response["failure"] = failure }
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) cancelIngestion(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeIngestionWrite)
	if !ok {
		return
	}
	sessionID := r.PathValue("session_id")
	if !isUUID(sessionID) {
		writeError(w, http.StatusNotFound, "NOT_FOUND", "Ingestion session was not found")
		return
	}
	// Staging backend is immutable within a session. Read it before the
	// cancellation transaction so cleanup can be routed to the correct adapter.
	session, err := s.store.GetSession(r.Context(), principal.TenantID, sessionID)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	stagingBackend, _ := session["staging_backend"].(string)
	if stagingBackend == "" {
		stagingBackend = "local"
	}
	// The store returns the state observed while holding the session row lock.
	// This prevents deleting a staging object if finalization won the race.
	priorState, err := s.store.Cancel(r.Context(), principal.TenantID, sessionID)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	if (priorState == "created" || priorState == "receiving") && stagingBackend == s.stager.Backend() && s.sessionStagingReferenceMatches(session, principal.TenantID, sessionID) {
		// Cancellation is already durable. Cleanup failure is left for the
		// staging-retention reconciler rather than reversing the state change.
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = s.stager.Remove(cleanupCtx, principal.TenantID, sessionID)
	}
	w.WriteHeader(http.StatusAccepted)
}

func (s *Server) getAsset(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeIngestionRead)
	if !ok { return }
	assetID := r.PathValue("asset_id")
	if !isUUID(assetID) { writeError(w, http.StatusNotFound, "NOT_FOUND", "Asset was not found"); return }
	asset, err := s.store.GetAsset(r.Context(), principal.TenantID, assetID)
	if err != nil { writeStoreError(w, err); return }
	writeJSON(w, http.StatusOK, publicAsset(asset))
}


func normalizePersianSearchQuery(query string) string {
	var normalized strings.Builder
	for _, value := range query {
		switch {
		case value == '\u200c':
			normalized.WriteByte(' ')
		case value == '\u0640':
			continue
		case value >= '\u064b' && value <= '\u065f':
			continue
		case value == '\u0670':
			continue
		case value >= '\u06d6' && value <= '\u06ed':
			continue
		case value == '\u064a' || value == '\u0649':
			normalized.WriteRune('\u06cc')
		case value == '\u0643':
			normalized.WriteRune('\u06a9')
		case value == '\u06c0' || value == '\u0629':
			normalized.WriteRune('\u0647')
		case value == '\u0624':
			normalized.WriteRune('\u0648')
		case value == '\u0623' || value == '\u0625' || value == '\u0671':
			normalized.WriteRune('\u0627')
		case value >= '\u0660' && value <= '\u0669':
			normalized.WriteByte(byte('0' + value - '\u0660'))
		case value >= '\u06f0' && value <= '\u06f9':
			normalized.WriteByte(byte('0' + value - '\u06f0'))
		default:
			normalized.WriteRune(unicode.ToLower(value))
		}
	}
	return strings.Join(strings.Fields(normalized.String()), " ")
}

func (s *Server) searchTranscripts(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeTranscriptRead)
	if !ok { return }
	queryValues := r.URL.Query()
	allowed := map[string]bool{
		"q": true, "limit": true, "offset": true, "transcript_id": true,
		"asset_id": true, "revision": true, "language": true, "speaker_id": true,
		"start_ms": true, "end_ms": true,
	}
	for key, values := range queryValues {
		if !allowed[key] || len(values) != 1 {
			writeError(w, http.StatusBadRequest, "INVALID_SEARCH_FILTER", "Search filters are invalid")
			return
		}
	}
	query := normalizePersianSearchQuery(strings.TrimSpace(queryValues.Get("q")))
	if len([]rune(query)) < 1 || len([]rune(query)) > 256 {
		writeError(w, http.StatusBadRequest, "INVALID_SEARCH_QUERY", "q must contain between 1 and 256 characters")
		return
	}
	limit, err := parseBoundedQueryInt(queryValues, "limit", 20, 1, 100)
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_SEARCH_LIMIT", "limit must be between 1 and 100")
		return
	}
	offset, err := parseBoundedQueryInt(queryValues, "offset", 0, 0, 10000)
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_SEARCH_OFFSET", "offset must be between 0 and 10000")
		return
	}
	filters := TranscriptSearchFilters{}
	if values, exists := queryValues["transcript_id"]; exists {
		value := values[0]
		if !isUUID(value) { writeError(w, http.StatusBadRequest, "INVALID_TRANSCRIPT_ID", "transcript_id must be a UUID"); return }
		filters.TranscriptID = &value
	}
	if values, exists := queryValues["asset_id"]; exists {
		value := values[0]
		if !isUUID(value) { writeError(w, http.StatusBadRequest, "INVALID_ASSET_ID", "asset_id must be a UUID"); return }
		filters.AssetID = &value
	}
	if values, exists := queryValues["speaker_id"]; exists {
		value := values[0]
		if !isUUID(value) { writeError(w, http.StatusBadRequest, "INVALID_SPEAKER_ID", "speaker_id must be a UUID"); return }
		filters.SpeakerID = &value
	}
	if values, exists := queryValues["revision"]; exists {
		parsed, parseErr := strconv.Atoi(values[0])
		if parseErr != nil || parsed < 1 { writeError(w, http.StatusBadRequest, "INVALID_REVISION", "revision must be a positive integer"); return }
		filters.Revision = &parsed
	}
	if values, exists := queryValues["language"]; exists {
		value := values[0]
		if !languagePattern.MatchString(value) { writeError(w, http.StatusBadRequest, "INVALID_LANGUAGE", "language filter is invalid"); return }
		filters.Language = &value
	}
	if values, exists := queryValues["start_ms"]; exists {
		parsed, parseErr := strconv.ParseInt(values[0], 10, 64)
		if parseErr != nil || parsed < 0 { writeError(w, http.StatusBadRequest, "INVALID_START_MS", "start_ms must be a non-negative integer"); return }
		filters.StartMS = &parsed
	}
	if values, exists := queryValues["end_ms"]; exists {
		parsed, parseErr := strconv.ParseInt(values[0], 10, 64)
		if parseErr != nil || parsed < 1 { writeError(w, http.StatusBadRequest, "INVALID_END_MS", "end_ms must be a positive integer"); return }
		filters.EndMS = &parsed
	}
	if filters.StartMS != nil && filters.EndMS != nil && *filters.EndMS <= *filters.StartMS {
		writeError(w, http.StatusBadRequest, "INVALID_TIME_RANGE", "end_ms must be greater than start_ms")
		return
	}
	items, err := s.store.SearchTranscriptSegments(r.Context(), principal.TenantID, query, filters, limit, offset)
	if err != nil { writeStoreError(w, err); return }
	result := make([]map[string]any, 0, len(items))
	for _, item := range items { result = append(result, publicTranscriptSearchResult(item)) }
	writeJSON(w, http.StatusOK, map[string]any{"items": result, "limit": limit, "offset": offset})
}

func parseBoundedQueryInt(values map[string][]string, key string, fallback, minimum, maximum int) (int, error) {
	rawValues, exists := values[key]
	if !exists { return fallback, nil }
	if len(rawValues) != 1 || rawValues[0] == "" { return 0, fmt.Errorf("query parameter is empty or repeated") }
	value, err := strconv.Atoi(rawValues[0])
	if err != nil || value < minimum || value > maximum { return 0, fmt.Errorf("query parameter is outside its allowed bounds") }
	return value, nil
}

// publicTranscriptSearchResult exposes only transcript search fields; tenant IDs,
// audio hashes and storage/object references remain private.
func publicTranscriptSearchResult(item map[string]any) map[string]any {
	result := map[string]any{}
	for _, key := range []string{
		"transcript_id", "revision", "asset_id", "language", "provider", "model",
		"model_version", "segment_id", "start_ms", "end_ms", "text", "speaker_id", "rank",
	} {
		if value, exists := item[key]; exists { result[key] = value }
	}
	return result
}

func (s *Server) searchAssets(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeIngestionRead)
	if !ok { return }
	query := strings.TrimSpace(r.URL.Query().Get("q"))
	if len(query) < 2 || len(query) > 128 {
		writeError(w, http.StatusBadRequest, "INVALID_SEARCH_QUERY", "q must contain between 2 and 128 characters")
		return
	}
	limit := 20
	if raw := r.URL.Query().Get("limit"); raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil || value < 1 || value > 50 {
			writeError(w, http.StatusBadRequest, "INVALID_SEARCH_LIMIT", "limit must be between 1 and 50")
			return
		}
		limit = value
	}
	assets, err := s.store.SearchAssets(r.Context(), principal.TenantID, query, limit)
	if err != nil { writeStoreError(w, err); return }
	result := make([]map[string]any, 0, len(assets))
	for _, asset := range assets { result = append(result, publicAsset(asset)) }
	writeJSON(w, http.StatusOK, map[string]any{"items": result, "limit": limit})
}

// publicAsset deliberately omits tenant identifiers and private storage references.
func publicAsset(asset map[string]any) map[string]any {
	result := map[string]any{}
	for _, key := range []string{"asset_id", "media", "integrity", "state", "created_at", "updated_at", "retention_policy_id", "retention_expires_at"} {
		if value, exists := asset[key]; exists { result[key] = value }
	}
	if source, ok := asset["source"].(map[string]any); ok {
		publicSource := map[string]any{}
		for _, key := range []string{"kind", "recorded_at"} {
			if value, exists := source[key]; exists { publicSource[key] = value }
		}
		result["source"] = publicSource
	}
	return result
}

func (s *Server) principal(w http.ResponseWriter, r *http.Request, scope string) (Principal, bool) {
	header := r.Header.Get("Authorization")
	if !strings.HasPrefix(header, "Bearer ") || strings.TrimSpace(strings.TrimPrefix(header, "Bearer ")) == "" {
		writeError(w, http.StatusUnauthorized, "UNAUTHENTICATED", "Bearer authentication is required")
		return Principal{}, false
	}
	principal, err := s.auth.Authenticate(strings.TrimSpace(strings.TrimPrefix(header, "Bearer ")))
	if err != nil {
		writeError(w, http.StatusUnauthorized, "UNAUTHENTICATED", "Bearer token is invalid")
		return Principal{}, false
	}
	if !principal.Allows(scope) {
		writeError(w, http.StatusForbidden, "FORBIDDEN", "Token does not grant the required capability")
		return Principal{}, false
	}
	return principal, true
}

func validSource(source SourceMetadata) bool {
	switch source.Kind {
	case "file_upload", "device_capture", "telephony", "live_stream", "import":
	default:
		return false
	}
	if len(source.ExternalReference) > 2048 || len(source.DeclaredBy) > 256 { return false }
	if source.RecordedAt != "" {
		if _, err := time.Parse(time.RFC3339, source.RecordedAt); err != nil { return false }
	}
	return true
}

func sourceMap(source SourceMetadata) map[string]any {
	result := map[string]any{"kind": source.Kind}
	if source.ExternalReference != "" { result["external_reference"] = source.ExternalReference }
	if source.RecordedAt != "" { result["recorded_at"] = source.RecordedAt }
	if source.DeclaredBy != "" { result["declared_by"] = source.DeclaredBy }
	return result
}

func (s *Server) sessionStagingReferenceMatches(session map[string]any, tenantID, sessionID string) bool {
	expected, err := s.stager.Reference(tenantID, sessionID)
	if err != nil {
		return false
	}
	actual, _ := session["staging_reference"].(string)
	return actual == expected
}

func sameCreateRequest(session, upload map[string]any, request CreateIngestionRequest, stagingBackend string) bool {
	size, sizeOK := numberInt64(upload["expected_size_bytes"])
	digest, digestOK := upload["expected_sha256"].(string)
	sourceBytes, _ := json.Marshal(session["source"])
	requestSourceBytes, _ := json.Marshal(sourceMap(request.Source))
	contentType, _ := upload["content_type_hint"].(string)
	storedBackend, _ := session["staging_backend"].(string)
	if storedBackend == "" {
		storedBackend = "local"
	}
	return sizeOK && digestOK && size == request.ExpectedSizeBytes &&
		digest == strings.ToLower(request.ExpectedSHA256) &&
		string(sourceBytes) == string(requestSourceBytes) &&
		contentType == request.ContentTypeHint &&
		storedBackend == stagingBackend
}

func numberInt64(value any) (int64, bool) {
	switch number := value.(type) {
	case int: return int64(number), true
	case int64: return number, true
	case float64:
		if math.IsNaN(number) || math.IsInf(number, 0) || math.Trunc(number) != number { return 0, false }
		return int64(number), true
	case json.Number:
		result, err := number.Int64(); return result, err == nil
	default: return 0, false
	}
}

func cloneJSONMap(value map[string]any) (map[string]any, error) {
	encoded, err := json.Marshal(value); if err != nil { return nil, err }
	var cloned map[string]any
	err = json.Unmarshal(encoded, &cloned); return cloned, err
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF { return fmt.Errorf("trailing JSON value") }
	return nil
}

func writeStoreError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrNotFound):
		writeError(w, http.StatusNotFound, "NOT_FOUND", "Ingestion session was not found")
	case errors.Is(err, ErrConflict):
		writeError(w, http.StatusConflict, "STATE_CONFLICT", "Ingestion session changed or is not in the required state")
	case errors.Is(err, ErrQuotaExceeded):
		writeError(w, http.StatusTooManyRequests, "TENANT_QUOTA_EXCEEDED", "Tenant upload capacity is exhausted")
	case errors.Is(err, ErrRateLimited):
		w.Header().Set("Retry-After", "60")
		writeError(w, http.StatusTooManyRequests, "TENANT_RATE_LIMITED", "Tenant ingestion rate limit is exceeded")
	default:
		writeError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "The request could not be completed")
	}
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]string{"code": code, "message": message})
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

// healthLive reports process availability without depending on PostgreSQL.
func (s *Server) healthLive(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// healthReady verifies the core database/schema dependency with a bounded deadline.
// Dependency errors are deliberately not included in the response.
func (s *Server) healthReady(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.store.Ready(ctx); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "not_ready"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
}
