package httpapi

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"
)

func (s *Server) hybridSearch(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeTranscriptRead)
	if !ok { return }
	if s.retrieval == nil {
		writeError(w, http.StatusServiceUnavailable, "RETRIEVAL_NOT_CONFIGURED", "Local hybrid retrieval is not configured")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 64*1024)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	var request HybridSearchRequest
	if err := decoder.Decode(&request); err != nil || ensureJSONEOF(decoder) != nil {
		writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "Retrieval request must be one valid JSON object")
		return
	}
	request.Query = strings.TrimSpace(request.Query)
	if len([]rune(request.Query)) < 1 || len([]rune(request.Query)) > 256 {
		writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "query must contain between 1 and 256 characters")
		return
	}
	if request.Limit == 0 { request.Limit = 20 }
	if request.Limit < 1 || request.Limit > 100 {
		writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "limit must be between 1 and 100")
		return
	}
	for name, value := range map[string]*string{
		"transcript_id": request.TranscriptID, "asset_id": request.AssetID, "speaker_id": request.SpeakerID,
	} {
		if value != nil && !isUUID(*value) {
			writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", name+" must be a UUID")
			return
		}
	}
	if request.Revision != nil && *request.Revision < 1 {
		writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "revision must be a positive integer")
		return
	}
	if request.Language != nil && !languagePattern.MatchString(*request.Language) {
		writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "language filter is invalid")
		return
	}
	if request.StartMS != nil && *request.StartMS < 0 {
		writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "start_ms must be non-negative")
		return
	}
	if request.EndMS != nil && *request.EndMS < 1 {
		writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "end_ms must be positive")
		return
	}
	if request.StartMS != nil && request.EndMS != nil && *request.EndMS <= *request.StartMS {
		writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "end_ms must be greater than start_ms")
		return
	}
	result, err := s.retrieval.Search(r.Context(), principal.TenantID, request)
	if err != nil {
		writeRetrievalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) indexTranscript(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeTranscriptIndex)
	if !ok { return }
	if s.retrieval == nil {
		writeError(w, http.StatusServiceUnavailable, "RETRIEVAL_NOT_CONFIGURED", "Local hybrid retrieval is not configured")
		return
	}
	transcriptID := r.PathValue("transcript_id")
	if !isUUID(transcriptID) {
		writeError(w, http.StatusBadRequest, "INVALID_TRANSCRIPT_ID", "transcript_id must be a UUID")
		return
	}
	for key, values := range r.URL.Query() {
		if key != "revision" || len(values) != 1 {
			writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "Only one optional revision filter is supported")
			return
		}
	}
	var revision *int
	if values, exists := r.URL.Query()["revision"]; exists {
		value, err := strconv.Atoi(values[0])
		if err != nil || value < 1 {
			writeError(w, http.StatusBadRequest, "INVALID_RETRIEVAL_REQUEST", "revision must be a positive integer")
			return
		}
		revision = &value
	}
	result, err := s.retrieval.IndexTranscript(r.Context(), principal.TenantID, transcriptID, revision)
	if err != nil {
		writeRetrievalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func writeRetrievalError(w http.ResponseWriter, err error) {
	var serviceErr *RetrievalServiceError
	if !errors.As(err, &serviceErr) {
		writeError(w, http.StatusBadGateway, "RETRIEVAL_SERVICE_UNAVAILABLE", "Local retrieval service is unavailable")
		return
	}
	switch serviceErr.Code {
	case "RETRIEVAL_NOT_CONFIGURED":
		writeError(w, http.StatusServiceUnavailable, serviceErr.Code, "Local embedding model is not configured")
	case "TRANSCRIPT_NOT_FOUND":
		writeError(w, http.StatusNotFound, serviceErr.Code, "Transcript revision was not found")
	case "INVALID_RETRIEVAL_REQUEST", "UNSUPPORTED_ENTITY_FILTER":
		writeError(w, http.StatusBadRequest, serviceErr.Code, "Retrieval request is not supported")
	default:
		writeError(w, http.StatusBadGateway, "RETRIEVAL_SERVICE_UNAVAILABLE", "Local retrieval service is unavailable")
	}
}
