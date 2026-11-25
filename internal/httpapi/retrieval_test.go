package httpapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type fakeHybridRetrieval struct {
	tenant string
	search HybridSearchRequest
	indexTenant string
	indexTranscript string
	indexRevision *int
}

func (f *fakeHybridRetrieval) Search(_ context.Context, tenant string, request HybridSearchRequest) (map[string]any, error) {
	f.tenant, f.search = tenant, request
	return map[string]any{"model": "test-local", "model_version": "sha256:test", "items": []any{}}, nil
}
func (f *fakeHybridRetrieval) IndexTranscript(_ context.Context, tenant, transcript string, revision *int) (map[string]any, error) {
	f.indexTenant, f.indexTranscript, f.indexRevision = tenant, transcript, revision
	return map[string]any{"transcript_id": transcript, "revision": 1, "indexed_segments": 3}, nil
}

func retrievalTestServer(t *testing.T, scopes string) (*Server, string, string) {
	t.Helper()
	tenant, token := "11111111-1111-4111-8111-111111111111", "voxpipe-retrieval-test-token"
	digest := sha256.Sum256([]byte(token))
	auth, err := NewStaticTokenAuthenticator([]string{hex.EncodeToString(digest[:]) + ":" + tenant + ":" + scopes})
	if err != nil { t.Fatal(err) }
	stager, err := NewLocalStager(t.TempDir())
	if err != nil { t.Fatal(err) }
	server, err := NewServer(newMemoryStore(), auth, stager, 1024, time.Hour, 3)
	if err != nil { t.Fatal(err) }
	return server, tenant, token
}

func TestHybridSearchUsesAuthenticatedTenantAndRejectsCallerTenant(t *testing.T) {
	server, tenant, token := retrievalTestServer(t, "transcript:read")
	client := &fakeHybridRetrieval{}
	server.SetHybridRetrievalClient(client)
	asset := "22222222-2222-4222-8222-222222222222"
	response := request(server.Handler(), http.MethodPost, "/v1/retrieval/hybrid-search", token,
		[]byte(`{"query":"جلسه فروش","limit":7,"asset_id":"22222222-2222-4222-8222-222222222222","language":"fa"}`))
	if response.Code != http.StatusOK { t.Fatalf("search failed: %d %s", response.Code, response.Body.String()) }
	if client.tenant != tenant || client.search.Query != "جلسه فروش" || client.search.Limit != 7 {
		t.Fatalf("authenticated search context was not forwarded: %#v", client)
	}
	if client.search.AssetID == nil || *client.search.AssetID != asset || client.search.Language == nil || *client.search.Language != "fa" {
		t.Fatalf("search filters were not forwarded: %#v", client.search)
	}
	response = request(server.Handler(), http.MethodPost, "/v1/retrieval/hybrid-search", token,
		[]byte(`{"query":"meeting","tenant_id":"33333333-3333-4333-8333-333333333333"}`))
	if response.Code != http.StatusBadRequest { t.Fatalf("caller tenant must be rejected: %d %s", response.Code, response.Body.String()) }
}

func TestHybridSearchFailsClosedWithoutConfiguredLocalModel(t *testing.T) {
	server, _, token := retrievalTestServer(t, "transcript:read")
	response := request(server.Handler(), http.MethodPost, "/v1/retrieval/hybrid-search", token, []byte(`{"query":"meeting"}`))
	if response.Code != http.StatusServiceUnavailable { t.Fatalf("expected disabled retrieval, got %d", response.Code) }
	body := decodeResponse(t, response)
	if body["code"] != "RETRIEVAL_NOT_CONFIGURED" { t.Fatalf("unexpected disabled response: %#v", body) }
}

func TestTranscriptIndexRequiresDedicatedScopeAndUsesAuthenticatedTenant(t *testing.T) {
	server, tenant, token := retrievalTestServer(t, "transcript:index")
	client := &fakeHybridRetrieval{}
	server.SetHybridRetrievalClient(client)
	transcript := "33333333-3333-4333-8333-333333333333"
	response := request(server.Handler(), http.MethodPost, "/v1/transcripts/"+transcript+"/index?revision=2", token, nil)
	if response.Code != http.StatusOK { t.Fatalf("index failed: %d %s", response.Code, response.Body.String()) }
	if client.indexTenant != tenant || client.indexTranscript != transcript || client.indexRevision == nil || *client.indexRevision != 2 {
		t.Fatalf("index request did not preserve authenticated scope: %#v", client)
	}
}

func TestLocalRetrievalClientRejectsPlainHTTPToNonLocalHosts(t *testing.T) {
	if _, err := NewLocalRetrievalClient("http://example.com:8090", strings.Repeat("s", 32)); err == nil {
		t.Fatal("plain HTTP to a non-local retrieval host must be rejected")
	}
	if _, err := NewLocalRetrievalClient("http://retrieval:8090", strings.Repeat("s", 32)); err != nil {
		t.Fatalf("private compose service name should be allowed: %v", err)
	}
	if _, err := NewLocalRetrievalClient("https://retrieval.example.com", strings.Repeat("s", 32)); err != nil {
		t.Fatalf("HTTPS service URL should be allowed: %v", err)
	}
}

func TestLocalRetrievalClientForwardsInternalIdentityAndMapsServiceErrors(t *testing.T) {
	var tenant string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-VoxPipe-Retrieval-Token") != strings.Repeat("s", 32) {
			t.Errorf("internal service token missing")
		}
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil { t.Fatal(err) }
		tenant, _ = payload["tenant_id"].(string)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"items":[],"model":"local","model_version":"sha256:abc"}`))
	}))
	defer server.Close()
	client, err := NewLocalRetrievalClient(server.URL, strings.Repeat("s", 32))
	if err != nil { t.Fatal(err) }
	_, err = client.Search(context.Background(), "11111111-1111-4111-8111-111111111111", HybridSearchRequest{Query: "meeting"})
	if err != nil { t.Fatal(err) }
	if tenant != "11111111-1111-4111-8111-111111111111" { t.Fatalf("wrong tenant forwarded: %q", tenant) }

	failing := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`{"code":"RETRIEVAL_NOT_CONFIGURED"}`))
	}))
	defer failing.Close()
	client, err = NewLocalRetrievalClient(failing.URL, strings.Repeat("s", 32))
	if err != nil { t.Fatal(err) }
	_, err = client.Search(context.Background(), "11111111-1111-4111-8111-111111111111", HybridSearchRequest{Query: "meeting"})
	serviceErr, ok := err.(*RetrievalServiceError)
	if !ok || serviceErr.Code != "RETRIEVAL_NOT_CONFIGURED" { t.Fatalf("service error was not preserved: %#v", err) }
}
