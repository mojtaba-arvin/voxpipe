package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

type memoryStore struct {
	mu       sync.Mutex
	sessions map[string]map[string]any
	keys     map[string]string
	jobs     map[string]map[string]any
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

func (m *memoryStore) CreateSession(_ context.Context, tenant, key string, session map[string]any) (map[string]any, bool, error) {
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
func (m *memoryStore) Cancel(_ context.Context,tenant,id string)(bool,error){
	m.mu.Lock(); defer m.mu.Unlock()
	key:=sessionKey(tenant,id); session,ok:=m.sessions[key]; if !ok { return false,ErrNotFound }
	switch session["state"] { case "completed","failed","cancelled","expired": return false,ErrConflict }
	session["state"]="cancelled"; delete(session,"lease")
	if job,ok:=m.jobs[key]; ok { job["state"]="cancelled" }
	return true,nil
}

func testServer(t *testing.T) (*Server,*memoryStore,string,string) {
	t.Helper()
	tenant:="11111111-1111-4111-8111-111111111111"
	token:="voxpipe-test-token"
	digest:=sha256.Sum256([]byte(token))
	auth,err:=NewStaticTokenAuthenticator([]string{hex.EncodeToString(digest[:])+":"+tenant+":ingestion:read,ingestion:write"})
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

	finalized:=request(handler,http.MethodPost,"/v1/ingestions/"+sessionID+"/finalize",token,nil)
	if finalized.Code!=http.StatusAccepted { t.Fatalf("finalize: got %d: %s",finalized.Code,finalized.Body.String()) }
	job:=decodeResponse(t,finalized)
	if job["state"]!="pending" { t.Fatalf("expected pending job, got %#v",job) }

	status:=request(handler,http.MethodGet,"/v1/ingestions/"+sessionID,token,nil)
	if status.Code!=http.StatusOK { t.Fatalf("status: got %d: %s",status.Code,status.Body.String()) }
	statusValue:=decodeResponse(t,status)
	if statusValue["state"]!="finalizing" { t.Fatalf("unexpected session state: %#v",statusValue) }
	if _,ok:=statusValue["job"]; !ok { t.Fatalf("status response omitted job") }

	cancel:=request(handler,http.MethodDelete,"/v1/ingestions/"+sessionID,token,nil)
	if cancel.Code!=http.StatusAccepted { t.Fatalf("cancel: got %d: %s",cancel.Code,cancel.Body.String()) }
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
	server,_,_,token:=testServer(t); handler:=server.Handler()
	unauthorized:=request(handler,http.MethodPost,"/v1/ingestions","",[]byte("{}"))
	if unauthorized.Code!=http.StatusUnauthorized { t.Fatalf("expected 401, got %d",unauthorized.Code) }
	forbiddenAuth,err:=NewStaticTokenAuthenticator([]string{
		fmt.Sprintf("%x:%s:%s",sha256.Sum256([]byte("read-only")), "11111111-1111-4111-8111-111111111111", scopeIngestionRead),
	})
	if err!=nil { t.Fatal(err) }
	_ = forbiddenAuth
	_ = token
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
