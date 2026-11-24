package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type memoryStore struct {
	mu       sync.Mutex
	sessions map[string]map[string]any
	keys     map[string]string
	jobs     map[string]map[string]any
	transcriptResults []map[string]any
	lastTranscriptQuery string
	lastTranscriptTenant string
	lastTranscriptFilters TranscriptSearchFilters
	lastTranscriptLimit int
	lastTranscriptOffset int
}

func newMemoryStore() *memoryStore {
	return &memoryStore{
		sessions: make(map[string]map[string]any),
		keys: make(map[string]string),
		jobs: make(map[string]map[string]any),
	}
}
func cloneMap(input map[string]any) map[string]any {
	data, _ := json.Marshal(input)
	var result map[string]any
	_ = json.Unmarshal(data, &result)
	return result
}
func sessionKey(tenant, id string) string { return tenant + "/" + id }

func (m *memoryStore) CreateSession(_ context.Context, tenant, key string, session map[string]any, limits TenantLimits) (map[string]any, bool, error) {
	m.mu.Lock(); defer m.mu.Unlock()
	unique := tenant + "/" + key
	if id, ok := m.keys[unique]; ok { return cloneMap(m.sessions[sessionKey(tenant,id)]), false, nil }
	m.sessions[sessionKey(tenant,session["session_id"].(string))] = cloneMap(session)
	m.keys[unique] = session["session_id"].(string)
	return cloneMap(session), true, nil
}
func (m *memoryStore) GetSession(_ context.Context, tenant, id string) (map[string]any,error) {
	m.mu.Lock(); defer m.mu.Unlock()
	session,ok:=m.sessions[sessionKey(tenant,id)]; if !ok { return nil,ErrNotFound }; return cloneMap(session),nil
}
func (m *memoryStore) UpdateSession(_ context.Context, tenant,id,expected string, updated map[string]any) error {
	m.mu.Lock(); defer m.mu.Unlock()
	key:=sessionKey(tenant,id); current,ok:=m.sessions[key]; if !ok { return ErrNotFound }
	if current["state"]!=expected { return ErrConflict }; m.sessions[key]=cloneMap(updated); return nil
}
func (m *memoryStore) FinalizeSession(_ context.Context, tenant,id,jobID string,maxAttempts int,updated map[string]any)(map[string]any,error){
	m.mu.Lock(); defer m.mu.Unlock()
	key:=sessionKey(tenant,id); session,ok:=m.sessions[key]; if !ok { return nil,ErrNotFound }
	if job,ok:=m.jobs[key]; ok { return cloneMap(job),nil }
	if session["state"]!="receiving" { return nil,ErrConflict }
	m.sessions[key]=cloneMap(updated)
	job:=map[string]any{"job_id":jobID,"state":"pending","attempts":0,"max_attempts":maxAttempts}
	m.jobs[key]=job; return cloneMap(job),nil
}
func (m *memoryStore) GetStatus(_ context.Context,tenant,id string)(map[string]any,map[string]any,error){
	m.mu.Lock(); defer m.mu.Unlock()
	key:=sessionKey(tenant,id); session,ok:=m.sessions[key]; if !ok { return nil,nil,ErrNotFound }
	var job map[string]any; if value,ok:=m.jobs[key]; ok { job=cloneMap(value) }
	return cloneMap(session),job,nil
}
func (m *memoryStore) GetAsset(_ context.Context, tenant, id string) (map[string]any, error) {
	return nil, ErrNotFound
}
func (m *memoryStore) SearchAssets(_ context.Context, tenant, query string, limit int) ([]map[string]any, error) {
	return []map[string]any{}, nil
}
func (m *memoryStore) SearchTranscriptSegments(_ context.Context, tenant, query string, filters TranscriptSearchFilters, limit, offset int) ([]map[string]any, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.lastTranscriptTenant, m.lastTranscriptQuery = tenant, query
	m.lastTranscriptFilters, m.lastTranscriptLimit, m.lastTranscriptOffset = filters, limit, offset
	result := make([]map[string]any, 0, len(m.transcriptResults))
	for _, item := range m.transcriptResults { result = append(result, cloneMap(item)) }
	return result, nil
}
func (m *memoryStore) BeginUpload(_ context.Context, tenant, session, owner string, leaseSeconds, maxConcurrent int) error { return nil }
func (m *memoryStore) EndUpload(_ context.Context, tenant, session, owner, outcome, errorCode string) error { return nil }
func (m *memoryStore) RecordAuditEvent(_ context.Context, tenant, session, asset, eventType, outcome, errorCode string, metadata map[string]any) error { return nil }
func (m *memoryStore) Cancel(_ context.Context,tenant,id string)(string,error){
	m.mu.Lock(); defer m.mu.Unlock()
	key:=sessionKey(tenant,id); session,ok:=m.sessions[key]; if !ok { return "",ErrNotFound }
	state,_:=session["state"].(string)
	switch state { case "completed","failed","cancelled","expired": return "",ErrConflict }
	session["state"]="cancelled"; delete(session,"lease")
	if job,ok:=m.jobs[key]; ok { job["state"]="cancelled" }
	return state,nil
}

func testServer(t *testing.T) (*Server,*memoryStore,string,string) {
	t.Helper()
	tenant:="11111111-1111-4111-8111-111111111111"
	token:="voxpipe-test-token"
	digest:=sha256.Sum256([]byte(token))
	auth,err:=NewStaticTokenAuthenticator([]string{hex.EncodeToString(digest[:])+":"+tenant+":ingestion:read,ingestion:write,transcript:read"})
	if err!=nil { t.Fatal(err) }
	stager,err:=NewLocalStager(t.TempDir()); if err!=nil { t.Fatal(err) }
	store:=newMemoryStore()
	server,err:=NewServer(store,auth,stager,1024,time.Hour,3); if err!=nil { t.Fatal(err) }
	return server,store,tenant,token
}
func request(handler http.Handler,method,path,token string,body []byte) *httptest.ResponseRecorder {
	recorder:=httptest.NewRecorder()
	req:=httptest.NewRequest(method,path,bytes.NewReader(body))
	if token!="" { req.Header.Set("Authorization","Bearer "+token) }
	handler.ServeHTTP(recorder,req)
	return recorder
}
func decodeResponse(t *testing.T,recorder *httptest.ResponseRecorder) map[string]any {
	t.Helper(); var value map[string]any
	if err:=json.Unmarshal(recorder.Body.Bytes(),&value); err!=nil { t.Fatalf("decode response: %v; body=%s",err,recorder.Body.String()) }
	return value
}

func TestAuthenticatedUploadLifecycle(t *testing.T) {
	server,_,_,token:=testServer(t); handler:=server.Handler()
	content:=[]byte("synthetic audio bytes for upload API test")
	digest:=sha256.Sum256(content)
	payload:=map[string]any{
		"expected_size_bytes":len(content),
		"expected_sha256":hex.EncodeToString(digest[:]),
		"source":map[string]any{"kind":"file_upload"},
	}
	body,_:=json.Marshal(payload)
	create:=httptest.NewRequest(http.MethodPost,"/v1/ingestions",bytes.NewReader(body))
	create.Header.Set("Authorization","Bearer "+token)
	create.Header.Set("Idempotency-Key","api-upload-idempotency-0001")
	created:=httptest.NewRecorder(); handler.ServeHTTP(created,create)
	if created.Code!=http.StatusCreated { t.Fatalf("create: got %d: %s",created.Code,created.Body.String()) }
	session:=decodeResponse(t,created); sessionID:=session["session_id"].(string)

	upload:=httptest.NewRequest(http.MethodPut,"/v1/ingestions/"+sessionID+"/content",bytes.NewReader(content))
	upload.Header.Set("Authorization","Bearer "+token)
	upload.Header.Set("Content-Type","application/octet-stream")
	uploaded:=httptest.NewRecorder(); handler.ServeHTTP(uploaded,upload)
	if uploaded.Code!=http.StatusNoContent { t.Fatalf("upload: got %d: %s",uploaded.Code,uploaded.Body.String()) }
	retryUpload:=httptest.NewRequest(http.MethodPut,"/v1/ingestions/"+sessionID+"/content",bytes.NewReader(content))
	retryUpload.Header.Set("Authorization","Bearer "+token)
	retryUpload.Header.Set("Content-Type","application/octet-stream")
	retried:=httptest.NewRecorder(); handler.ServeHTTP(retried,retryUpload)
	if retried.Code!=http.StatusNoContent { t.Fatalf("idempotent upload retry: got %d: %s",retried.Code,retried.Body.String()) }

	finalized:=request(handler,http.MethodPost,"/v1/ingestions/"+sessionID+"/finalize",token,nil)
	if finalized.Code!=http.StatusAccepted { t.Fatalf("finalize: got %d: %s",finalized.Code,finalized.Body.String()) }
	job:=decodeResponse(t,finalized)
	if job["state"]!="pending" { t.Fatalf("expected pending job, got %#v",job) }
	repeatedFinalize:=request(handler,http.MethodPost,"/v1/ingestions/"+sessionID+"/finalize",token,nil)
	if repeatedFinalize.Code!=http.StatusAccepted { t.Fatalf("idempotent finalize: got %d: %s",repeatedFinalize.Code,repeatedFinalize.Body.String()) }
	if decodeResponse(t,repeatedFinalize)["job_id"]!=job["job_id"] { t.Fatal("finalize retry returned a different job") }

	status:=request(handler,http.MethodGet,"/v1/ingestions/"+sessionID,token,nil)
	if status.Code!=http.StatusOK { t.Fatalf("status: got %d: %s",status.Code,status.Body.String()) }
	statusValue:=decodeResponse(t,status)
	if statusValue["state"]!="finalizing" { t.Fatalf("unexpected session state: %#v",statusValue) }
	if _,ok:=statusValue["job"]; !ok { t.Fatalf("status response omitted job") }

	cancel:=request(handler,http.MethodDelete,"/v1/ingestions/"+sessionID,token,nil)
	if cancel.Code!=http.StatusAccepted { t.Fatalf("cancel: got %d: %s",cancel.Code,cancel.Body.String()) }
}

func TestPublicAssetOmitsTenantAndPrivateReferences(t *testing.T) {
	asset := map[string]any{
		"asset_id": "11111111-1111-4111-8111-111111111111",
		"tenant_id": "22222222-2222-4222-8222-222222222222",
		"source": map[string]any{"kind": "file_upload", "recorded_at": "2026-11-19T00:00:00Z", "external_reference": "private-ref", "declared_by": "private-user"},
		"storage": map[string]any{"object_key": "private/object/key"},
		"media": map[string]any{"container_format": "wav"},
		"integrity": map[string]any{"algorithm": "sha256", "digest": strings.Repeat("a", 64)},
		"state": "accepted",
		"retention_policy_id": "deployment-default",
		"retention_expires_at": "2027-11-19T00:00:00Z",
		"created_at": "2026-11-19T00:00:00Z",
		"updated_at": "2026-11-19T00:00:00Z",
	}
	public := publicAsset(asset)
	if _, ok := public["tenant_id"]; ok { t.Fatal("tenant ID leaked in public asset") }
	if _, ok := public["storage"]; ok { t.Fatal("private storage reference leaked in public asset") }
	source := public["source"].(map[string]any)
	if _, ok := source["external_reference"]; ok { t.Fatal("external reference leaked in public asset") }
	if _, ok := source["declared_by"]; ok { t.Fatal("declared_by leaked in public asset") }
	if source["kind"] != "file_upload" { t.Fatal("public source kind was omitted") }
	if public["retention_policy_id"] != "deployment-default" || public["retention_expires_at"] == nil { t.Fatal("server-assigned retention metadata was omitted") }
}

func TestAssetMetadataEndpointsAreTenantAuthenticated(t *testing.T) {
	server, _, _, token := testServer(t)
	handler := server.Handler()
	missing := request(handler, http.MethodGet, "/v1/assets/11111111-1111-4111-8111-111111111111", token, nil)
	if missing.Code != http.StatusNotFound { t.Fatalf("missing asset should be hidden: %d", missing.Code) }
	invalid := request(handler, http.MethodGet, "/v1/assets/search?q=x", token, nil)
	if invalid.Code != http.StatusBadRequest { t.Fatalf("short search query should fail: %d", invalid.Code) }
	search := request(handler, http.MethodGet, "/v1/assets/search?q=wav&limit=10", token, nil)
	if search.Code != http.StatusOK { t.Fatalf("search failed: %d %s", search.Code, search.Body.String()) }
	if _, ok := decodeResponse(t, search)["items"]; !ok { t.Fatal("search response omitted items") }
	unauthorized := request(handler, http.MethodGet, "/v1/assets/search?q=wav", "", nil)
	if unauthorized.Code != http.StatusUnauthorized { t.Fatalf("unauthenticated search should fail: %d", unauthorized.Code) }
}



func TestNormalizePersianTranscriptSearchQuery(t *testing.T) {
	got := normalizePersianSearchQuery("كِتاب يَك ۱۲۳ ١٢٣")
	if got != "کتاب یک 123 123" {
		t.Fatalf("unexpected Persian normalization: %q", got)
	}
	if got := normalizePersianSearchQuery("\"جلسه فروش\" -لغو"); got != "\"جلسه فروش\" -لغو" {
		t.Fatalf("search syntax should be preserved: %q", got)
	}
	if got := normalizePersianSearchQuery("می‌روم   API"); got != "می روم api" {
		t.Fatalf("ZWNJ and whitespace normalization failed: %q", got)
	}
}

func TestTranscriptSearchEndpointUsesDedicatedScopeAndReturnsSafeProvenance(t *testing.T) {
	server, store, tenant, token := testServer(t)
	store.transcriptResults = []map[string]any{{
		"tenant_id": tenant, "transcript_id": "11111111-1111-4111-8111-111111111111",
		"revision": 2, "asset_id": "22222222-2222-4222-8222-222222222222",
		"language": "fa", "provider": "faster-whisper", "model": "small",
		"model_version": "v1", "audio_sha256": strings.Repeat("a", 64),
		"segment_id": "33333333-3333-4333-8333-333333333333",
		"start_ms": int64(1200), "end_ms": int64(2400), "text": "جلسه فروش فردا",
		"speaker_id": "44444444-4444-4444-8444-444444444444", "rank": 0.75,
	}}
	response := request(server.Handler(), http.MethodGet,
		"/v1/transcripts/search?q=جلسه%20فروش&limit=10&offset=2&language=fa&revision=2&start_ms=1000&end_ms=3000",
		token, nil)
	if response.Code != http.StatusOK { t.Fatalf("search failed: %d %s", response.Code, response.Body.String()) }
	body := decodeResponse(t, response)
	if body["limit"] != float64(10) || body["offset"] != float64(2) { t.Fatalf("pagination missing: %#v", body) }
	items := body["items"].([]any)
	if len(items) != 1 { t.Fatalf("expected one result, got %#v", items) }
	item := items[0].(map[string]any)
	for _, private := range []string{"tenant_id", "audio_sha256", "storage", "object_key"} {
		if _, exists := item[private]; exists { t.Fatalf("private field %q leaked", private) }
	}
	if item["text"] != "جلسه فروش فردا" || item["provider"] != "faster-whisper" { t.Fatalf("result provenance missing: %#v", item) }
	if store.lastTranscriptTenant != tenant || store.lastTranscriptQuery != "جلسه فروش" { t.Fatalf("wrong tenant/query forwarded: %#v", store) }
	if store.lastTranscriptLimit != 10 || store.lastTranscriptOffset != 2 { t.Fatalf("wrong pagination forwarded") }
	if store.lastTranscriptFilters.Language == nil || *store.lastTranscriptFilters.Language != "fa" ||
		store.lastTranscriptFilters.Revision == nil || *store.lastTranscriptFilters.Revision != 2 ||
		store.lastTranscriptFilters.StartMS == nil || *store.lastTranscriptFilters.StartMS != 1000 ||
		store.lastTranscriptFilters.EndMS == nil || *store.lastTranscriptFilters.EndMS != 3000 {
		t.Fatalf("filters were not forwarded: %#v", store.lastTranscriptFilters)
	}
}

func TestTranscriptSearchRejectsInvalidFiltersAndRequiresDedicatedScope(t *testing.T) {
	server, _, _, token := testServer(t)
	handler := server.Handler()
	for _, path := range []string{
		"/v1/transcripts/search",
		"/v1/transcripts/search?q=",
		"/v1/transcripts/search?q=meeting&transcript_id=bad",
		"/v1/transcripts/search?q=meeting&start_ms=2000&end_ms=1000",
		"/v1/transcripts/search?q=meeting&limit=101",
		"/v1/transcripts/search?q=meeting&unknown=value",
		"/v1/transcripts/search?q=meeting&language=",
		"/v1/transcripts/search?q=meeting&limit=",
		"/v1/transcripts/search?q=one&q=two",
	} {
		response := request(handler, http.MethodGet, path, token, nil)
		if response.Code != http.StatusBadRequest { t.Errorf("%s: expected 400, got %d %s", path, response.Code, response.Body.String()) }
	}
	tenant := "11111111-1111-4111-8111-111111111111"
	digest := sha256.Sum256([]byte("no-transcript-scope"))
	auth, err := NewStaticTokenAuthenticator([]string{hex.EncodeToString(digest[:])+":"+tenant+":ingestion:read,ingestion:write"})
	if err != nil { t.Fatal(err) }
	stager, err := NewLocalStager(t.TempDir())
	if err != nil { t.Fatal(err) }
	restricted, err := NewServer(newMemoryStore(), auth, stager, 1024, time.Hour, 3)
	if err != nil { t.Fatal(err) }
	response := request(restricted.Handler(), http.MethodGet, "/v1/transcripts/search?q=meeting", "no-transcript-scope", nil)
	if response.Code != http.StatusForbidden { t.Fatalf("missing transcript:read scope should be forbidden, got %d", response.Code) }
}

func TestRetentionPolicyIsServerAuthoritative(t *testing.T) {
	server, store, tenantID, token := testServer(t)
	if err := server.SetRetentionDays(30); err != nil { t.Fatal(err) }
	payload, _ := json.Marshal(map[string]any{
		"expected_size_bytes": 12,
		"expected_sha256": strings.Repeat("a", 64),
		"source": map[string]any{"kind": "file_upload"},
	})
	req := httptest.NewRequest(http.MethodPost, "/v1/ingestions", bytes.NewReader(payload))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Idempotency-Key", "retention-server-policy-0001")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, req)
	if recorder.Code != http.StatusCreated { t.Fatalf("create failed: %d %s", recorder.Code, recorder.Body.String()) }
	sessionID := decodeResponse(t, recorder)["session_id"].(string)
	session, err := store.GetSession(context.Background(), tenantID, sessionID)
	if err != nil { t.Fatal(err) }
	if session["retention_policy_id"] != "deployment-default" { t.Fatalf("unexpected policy: %#v", session["retention_policy_id"]) }
	expiry, ok := session["retention_expires_at"].(string)
	if !ok { t.Fatal("server did not assign retention expiry") }
	parsed, err := time.Parse(time.RFC3339Nano, expiry)
	if err != nil || !parsed.After(time.Now().UTC()) { t.Fatalf("invalid retention expiry %q: %v", expiry, err) }
	if err := server.SetRetentionDays(-1); err == nil { t.Fatal("negative retention period must be rejected") }
}

func TestClientCannotChooseRetentionPolicy(t *testing.T) {
	server, _, _, token := testServer(t)
	payload := []byte(`{
		"expected_size_bytes": 12,
		"expected_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		"source": {"kind": "file_upload"},
		"retention_policy_id": "client-selected-policy"
	}`)
	req := httptest.NewRequest(http.MethodPost, "/v1/ingestions", bytes.NewReader(payload))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Idempotency-Key", "retention-policy-client-0001")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, req)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("client-selected retention policy must be rejected, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestCreateIsIdempotentAndRejectsChangedPayload(t *testing.T) {
	server,_,_,token:=testServer(t); handler:=server.Handler()
	content:=[]byte("same bytes")
	digest:=sha256.Sum256(content)
	makeRequest:=func(source string) *httptest.ResponseRecorder {
		payload,_:=json.Marshal(map[string]any{
			"expected_size_bytes":len(content),"expected_sha256":hex.EncodeToString(digest[:]),
			"source":map[string]any{"kind":source},
		})
		req:=httptest.NewRequest(http.MethodPost,"/v1/ingestions",bytes.NewReader(payload))
		req.Header.Set("Authorization","Bearer "+token)
		req.Header.Set("Idempotency-Key","api-upload-idempotency-0002")
		recorder:=httptest.NewRecorder(); handler.ServeHTTP(recorder,req); return recorder
	}
	first:=makeRequest("file_upload")
	second:=makeRequest("file_upload")
	changed:=makeRequest("import")
	if first.Code!=http.StatusCreated || second.Code!=http.StatusOK { t.Fatalf("idempotency statuses: %d, %d",first.Code,second.Code) }
	if changed.Code!=http.StatusConflict { t.Fatalf("changed payload should conflict, got %d",changed.Code) }
	if decodeResponse(t,first)["session_id"]!=decodeResponse(t,second)["session_id"] { t.Fatal("idempotent replay returned a different session") }
}

func TestUploadIntegrityFailureDoesNotFinalize(t *testing.T) {
	server,_,_,token:=testServer(t); handler:=server.Handler()
	content:=[]byte("bad checksum bytes")
	payload,_:=json.Marshal(map[string]any{
		"expected_size_bytes":len(content),"expected_sha256":strings.Repeat("0",64),
		"source":map[string]any{"kind":"file_upload"},
	})
	create:=httptest.NewRequest(http.MethodPost,"/v1/ingestions",bytes.NewReader(payload))
	create.Header.Set("Authorization","Bearer "+token)
	create.Header.Set("Idempotency-Key","api-upload-idempotency-0003")
	created:=httptest.NewRecorder(); handler.ServeHTTP(created,create)
	if created.Code!=http.StatusCreated { t.Fatalf("create failed: %s",created.Body.String()) }
	id:=decodeResponse(t,created)["session_id"].(string)
	upload:=httptest.NewRequest(http.MethodPut,"/v1/ingestions/"+id+"/content",bytes.NewReader(content))
	upload.Header.Set("Authorization","Bearer "+token); upload.Header.Set("Content-Type","application/octet-stream")
	recorder:=httptest.NewRecorder(); handler.ServeHTTP(recorder,upload)
	if recorder.Code!=http.StatusBadRequest { t.Fatalf("expected checksum rejection, got %d",recorder.Code) }
	finalize:=request(handler,http.MethodPost,"/v1/ingestions/"+id+"/finalize",token,nil)
	if finalize.Code!=http.StatusConflict { t.Fatalf("incomplete upload should not finalize, got %d",finalize.Code) }
}

func TestAuthenticationAndTenantIsolation(t *testing.T) {
	server, store, tenant, token := testServer(t)
	handler := server.Handler()
	unauthorized := request(handler, http.MethodPost, "/v1/ingestions", "", []byte("{}"))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", unauthorized.Code)
	}

	readOnlyToken := "read-only-token"
	digest := sha256.Sum256([]byte(readOnlyToken))
	auth, err := NewStaticTokenAuthenticator([]string{
		fmt.Sprintf("%x:%s:%s", digest, tenant, scopeIngestionRead),
	})
	if err != nil { t.Fatal(err) }
	readOnlyServer, err := NewServer(store, auth, server.stager, 1024, time.Hour, 3)
	if err != nil { t.Fatal(err) }
	forbidden := request(readOnlyServer.Handler(), http.MethodPost, "/v1/ingestions", readOnlyToken, []byte("{}"))
	if forbidden.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for missing write scope, got %d", forbidden.Code)
	}

	_ = token
}

func TestTenantCannotReadAnotherTenantsSession(t *testing.T) {
	server, store, _, tokenA := testServer(t)
	content := []byte("tenant isolated upload")
	digest := sha256.Sum256(content)
	payload, _ := json.Marshal(map[string]any{
		"expected_size_bytes": len(content),
		"expected_sha256": hex.EncodeToString(digest[:]),
		"source": map[string]any{"kind": "file_upload"},
	})
	create := httptest.NewRequest(http.MethodPost, "/v1/ingestions", bytes.NewReader(payload))
	create.Header.Set("Authorization", "Bearer "+tokenA)
	create.Header.Set("Idempotency-Key", "tenant-isolation-upload-001")
	created := httptest.NewRecorder()
	server.Handler().ServeHTTP(created, create)
	if created.Code != http.StatusCreated { t.Fatalf("create failed: %s", created.Body.String()) }
	sessionID := decodeResponse(t, created)["session_id"].(string)

	tenantB := "33333333-3333-4333-8333-333333333333"
	tokenB := "other-tenant-read-token"
	digestB := sha256.Sum256([]byte(tokenB))
	authB, err := NewStaticTokenAuthenticator([]string{
		fmt.Sprintf("%x:%s:%s", digestB, tenantB, scopeIngestionRead),
	})
	if err != nil { t.Fatal(err) }
	serverB, err := NewServer(store, authB, server.stager, 1024, time.Hour, 3)
	if err != nil { t.Fatal(err) }
	response := request(serverB.Handler(), http.MethodGet, "/v1/ingestions/"+sessionID, tokenB, nil)
	if response.Code != http.StatusNotFound { t.Fatalf("cross-tenant read should be hidden, got %d", response.Code) }
}

func TestCancelReceivingSessionRemovesStagedBytes(t *testing.T) {
	server, _, _, token := testServer(t)
	handler := server.Handler()
	content := []byte("cancel this staged upload")
	digest := sha256.Sum256(content)
	payload, _ := json.Marshal(map[string]any{
		"expected_size_bytes": len(content),
		"expected_sha256": hex.EncodeToString(digest[:]),
		"source": map[string]any{"kind": "file_upload"},
	})
	create := httptest.NewRequest(http.MethodPost, "/v1/ingestions", bytes.NewReader(payload))
	create.Header.Set("Authorization", "Bearer "+token)
	create.Header.Set("Idempotency-Key", "cancel-receiving-upload-01")
	created := httptest.NewRecorder()
	handler.ServeHTTP(created, create)
	if created.Code != http.StatusCreated { t.Fatalf("create failed: %s", created.Body.String()) }
	sessionID := decodeResponse(t, created)["session_id"].(string)
	upload := httptest.NewRequest(http.MethodPut, "/v1/ingestions/"+sessionID+"/content", bytes.NewReader(content))
	upload.Header.Set("Authorization", "Bearer "+token)
	upload.Header.Set("Content-Type", "application/octet-stream")
	uploaded := httptest.NewRecorder()
	handler.ServeHTTP(uploaded, upload)
	if uploaded.Code != http.StatusNoContent { t.Fatalf("upload failed: %s", uploaded.Body.String()) }
	if _, _, err := server.stager.Verify("11111111-1111-4111-8111-111111111111", sessionID); err != nil {
		t.Fatalf("staged file should exist before cancellation: %v", err)
	}
	cancelled := request(handler, http.MethodDelete, "/v1/ingestions/"+sessionID, token, nil)
	if cancelled.Code != http.StatusAccepted { t.Fatalf("cancel failed: %s", cancelled.Body.String()) }
	if _, _, err := server.stager.Verify("11111111-1111-4111-8111-111111111111", sessionID); err == nil {
		t.Fatal("staged file remained after pre-processing cancellation")
	}
}

type gatedUploadReader struct {
	data []byte
	started chan struct{}
	release chan struct{}
	read bool
}

func (reader *gatedUploadReader) Read(buffer []byte) (int, error) {
	if reader.read {
		return 0, io.EOF
	}
	reader.read = true
	close(reader.started)
	<-reader.release
	return copy(buffer, reader.data), nil
}

func TestCancellationRacingWithUploadDoesNotLeaveStagedBytes(t *testing.T) {
	server, _, tenantID, token := testServer(t)
	handler := server.Handler()
	content := []byte("upload racing with cancellation")
	digest := sha256.Sum256(content)
	payload, _ := json.Marshal(map[string]any{
		"expected_size_bytes": len(content),
		"expected_sha256": hex.EncodeToString(digest[:]),
		"source": map[string]any{"kind": "file_upload"},
	})
	create := httptest.NewRequest(http.MethodPost, "/v1/ingestions", bytes.NewReader(payload))
	create.Header.Set("Authorization", "Bearer "+token)
	create.Header.Set("Idempotency-Key", "cancel-race-upload-0001")
	created := httptest.NewRecorder()
	handler.ServeHTTP(created, create)
	if created.Code != http.StatusCreated {
		t.Fatalf("create failed: %s", created.Body.String())
	}
	sessionID := decodeResponse(t, created)["session_id"].(string)

	reader := &gatedUploadReader{
		data: content,
		started: make(chan struct{}),
		release: make(chan struct{}),
	}
	upload := httptest.NewRequest(
		http.MethodPut,
		"/v1/ingestions/"+sessionID+"/content",
		reader,
	)
	upload.Header.Set("Authorization", "Bearer "+token)
	upload.Header.Set("Content-Type", "application/octet-stream")
	uploadResult := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, upload)
		uploadResult <- recorder
	}()

	<-reader.started
	cancelled := request(handler, http.MethodDelete, "/v1/ingestions/"+sessionID, token, nil)
	if cancelled.Code != http.StatusAccepted {
		close(reader.release)
		t.Fatalf("cancel failed: %s", cancelled.Body.String())
	}
	close(reader.release)

	var uploaded *httptest.ResponseRecorder
	select {
	case uploaded = <-uploadResult:
	case <-time.After(3 * time.Second):
		t.Fatal("upload handler did not finish after cancellation")
	}
	if uploaded.Code != http.StatusConflict {
		t.Fatalf("racing upload should lose the session state transition, got %d: %s", uploaded.Code, uploaded.Body.String())
	}
	status := request(handler, http.MethodGet, "/v1/ingestions/"+sessionID, token, nil)
	if status.Code != http.StatusOK || decodeResponse(t, status)["state"] != "cancelled" {
		t.Fatalf("session did not remain cancelled: %d %s", status.Code, status.Body.String())
	}
	if _, _, err := server.stager.Verify(tenantID, sessionID); err == nil {
		t.Fatal("upload racing with cancellation left published staged bytes")
	}
}

type interruptedReader struct { sent bool }
func (reader *interruptedReader) Read(buffer []byte) (int, error) {
	if reader.sent { return 0, errors.New("simulated client disconnect") }
	reader.sent = true
	return copy(buffer, []byte("partial")), nil
}

func TestInterruptedUploadDoesNotPublishPartialFile(t *testing.T) {
	stager, err := NewLocalStager(t.TempDir())
	if err != nil { t.Fatal(err) }
	tenant := "11111111-1111-4111-8111-111111111111"
	session := "22222222-2222-4222-8222-222222222222"
	_, _, err = stager.Write(tenant, session, &interruptedReader{}, 32, 64, strings.Repeat("a", 64))
	if err == nil { t.Fatal("interrupted stream should fail") }
	if _, _, verifyErr := stager.Verify(tenant, session); verifyErr == nil {
		t.Fatal("partial upload was published")
	}
}

func TestStagerRejectsOversizedAndChecksumMismatch(t *testing.T) {
	stager,err:=NewLocalStager(t.TempDir()); if err!=nil { t.Fatal(err) }
	tenant:="11111111-1111-4111-8111-111111111111"
	session:="22222222-2222-4222-8222-222222222222"
	if _,_,err:=stager.Write(tenant,session,bytes.NewReader([]byte("1234")),4,3,strings.Repeat("0",64)); err!=ErrUploadTooLarge {
		t.Fatalf("expected size limit error, got %v",err)
	}
	if _,_,err:=stager.Write(tenant,session,bytes.NewReader([]byte("1234")),4,8,strings.Repeat("0",64)); err!=ErrUploadIntegrity {
		t.Fatalf("expected integrity error, got %v",err)
	}
}
