package httpapi

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

type deadlineRecorder struct {
	*httptest.ResponseRecorder
	deadline time.Time
}

func (r *deadlineRecorder) SetReadDeadline(deadline time.Time) error {
	r.deadline = deadline
	return nil
}

type timeoutReader struct{}

type timeoutNetError struct{}

func (timeoutReader) Read([]byte) (int, error) { return 0, timeoutNetError{} }
func (timeoutNetError) Error() string { return "read timed out" }
func (timeoutNetError) Timeout() bool { return true }
func (timeoutNetError) Temporary() bool { return true }

func TestBoundedUploadReaderCapsReadDeadlineAtAbsoluteDuration(t *testing.T) {
	writer := &deadlineRecorder{ResponseRecorder: httptest.NewRecorder()}
	started := time.Now()
	reader := &boundedUploadReader{
		reader: bytes.NewReader([]byte("data")),
		controller: http.NewResponseController(writer),
		started: started,
		inactivityTimeout: 10 * time.Second,
		maxDuration: 3 * time.Second,
	}
	buffer := make([]byte, 4)
	n, err := reader.Read(buffer)
	if err != nil || n != 4 { t.Fatalf("read failed: n=%d err=%v", n, err) }
	if writer.deadline.After(started.Add(3*time.Second)) {
		t.Fatalf("read deadline exceeded absolute duration: %v", writer.deadline)
	}
}

func TestBoundedUploadReaderNormalizesInactivityTimeout(t *testing.T) {
	writer := &deadlineRecorder{ResponseRecorder: httptest.NewRecorder()}
	reader := &boundedUploadReader{
		reader: timeoutReader{},
		controller: http.NewResponseController(writer),
		started: time.Now(),
		inactivityTimeout: time.Second,
		maxDuration: 5 * time.Second,
	}
	_, err := reader.Read(make([]byte, 1))
	if !errors.Is(err, ErrUploadTimeout) { t.Fatalf("expected upload timeout, got %v", err) }
}

func TestUploadHandlerReturnsRequestTimeoutForInactiveBody(t *testing.T) {
	server, _, _, token := testServer(t)
	if err := server.SetUploadTimeouts(time.Second, 2*time.Second); err != nil { t.Fatal(err) }
	content := []byte("timeout upload bytes")
	digest := sha256.Sum256(content)
	payload, _ := json.Marshal(map[string]any{
		"expected_size_bytes": len(content),
		"expected_sha256": hex.EncodeToString(digest[:]),
		"source": map[string]any{"kind": "file_upload"},
	})
	create := httptest.NewRequest(http.MethodPost, "/v1/ingestions", bytes.NewReader(payload))
	create.Header.Set("Authorization", "Bearer "+token)
	create.Header.Set("Idempotency-Key", "upload-timeout-test-0001")
	created := httptest.NewRecorder()
	server.Handler().ServeHTTP(created, create)
	if created.Code != http.StatusCreated { t.Fatalf("create failed: %d %s", created.Code, created.Body.String()) }
	sessionID := decodeResponse(t, created)["session_id"].(string)

	upload := httptest.NewRequest(http.MethodPut, "/v1/ingestions/"+sessionID+"/content", timeoutReader{})
	upload.Header.Set("Authorization", "Bearer "+token)
	upload.Header.Set("Content-Type", "application/octet-stream")
	response := &deadlineRecorder{ResponseRecorder: httptest.NewRecorder()}
	server.Handler().ServeHTTP(response, upload)
	if response.Code != http.StatusRequestTimeout {
		t.Fatalf("inactive upload should return 408, got %d: %s", response.Code, response.Body.String())
	}
}

func TestServerRejectsInvalidUploadDeadlineConfiguration(t *testing.T) {
	server, _, _, _ := testServer(t)
	if err := server.SetUploadTimeouts(time.Second, time.Second/2); err == nil {
		t.Fatal("maximum duration shorter than inactivity timeout must be rejected")
	}
	if err := server.SetUploadTimeouts(time.Second, 2*time.Second); err != nil {
		t.Fatalf("valid deadline configuration rejected: %v", err)
	}
}
