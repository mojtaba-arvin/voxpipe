package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

type HybridSearchRequest struct {
	Query        string  `json:"query"`
	Limit        int     `json:"limit,omitempty"`
	TranscriptID *string `json:"transcript_id,omitempty"`
	AssetID      *string `json:"asset_id,omitempty"`
	Revision     *int    `json:"revision,omitempty"`
	Language     *string `json:"language,omitempty"`
	SpeakerID    *string `json:"speaker_id,omitempty"`
	StartMS      *int64  `json:"start_ms,omitempty"`
	EndMS        *int64  `json:"end_ms,omitempty"`
}

type HybridRetrievalClient interface {
	Search(context.Context, string, HybridSearchRequest) (map[string]any, error)
	IndexTranscript(context.Context, string, string, *int) (map[string]any, error)
}

type RetrievalServiceError struct {
	Code   string
	Status int
}

func (e *RetrievalServiceError) Error() string { return e.Code }

type LocalRetrievalClient struct {
	baseURL string
	token   string
	client  *http.Client
}

func NewLocalRetrievalClient(baseURL, token string) (*LocalRetrievalClient, error) {
	parsed, err := url.Parse(strings.TrimSpace(baseURL))
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") ||
		parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, fmt.Errorf("retrieval service URL must be an absolute HTTP(S) URL without credentials or query")
	}
	if len(token) < 32 {
		return nil, fmt.Errorf("retrieval service internal token must contain at least 32 characters")
	}
	return &LocalRetrievalClient{
		baseURL: strings.TrimRight(baseURL, "/"),
		token: token,
		client: &http.Client{
			Timeout: 5 * time.Minute,
			CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
	}, nil
}

// NewLocalRetrievalClientFromEnv leaves retrieval disabled unless both
// internal service settings are explicitly configured.
func NewLocalRetrievalClientFromEnv() (*LocalRetrievalClient, error) {
	baseURL := strings.TrimSpace(os.Getenv("VOXPIPE_RETRIEVAL_URL"))
	token := strings.TrimSpace(os.Getenv("VOXPIPE_RETRIEVAL_INTERNAL_TOKEN"))
	if baseURL == "" && token == "" {
		return nil, nil
	}
	if baseURL == "" || token == "" {
		return nil, fmt.Errorf("VOXPIPE_RETRIEVAL_URL and VOXPIPE_RETRIEVAL_INTERNAL_TOKEN must be configured together")
	}
	return NewLocalRetrievalClient(baseURL, token)
}

func (c *LocalRetrievalClient) call(ctx context.Context, path string, payload map[string]any) (map[string]any, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("encode retrieval request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create retrieval request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("X-VoxPipe-Retrieval-Token", c.token)
	response, err := c.client.Do(request)
	if err != nil {
		return nil, &RetrievalServiceError{Code: "RETRIEVAL_SERVICE_UNAVAILABLE", Status: http.StatusBadGateway}
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, (2<<20)+1))
	if err != nil || len(raw) > 2<<20 {
		return nil, &RetrievalServiceError{Code: "RETRIEVAL_RESPONSE_INVALID", Status: http.StatusBadGateway}
	}
	var result map[string]any
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, &RetrievalServiceError{Code: "RETRIEVAL_RESPONSE_INVALID", Status: http.StatusBadGateway}
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		code, _ := result["code"].(string)
		if code == "" {
			code = "RETRIEVAL_SERVICE_UNAVAILABLE"
		}
		return nil, &RetrievalServiceError{Code: code, Status: response.StatusCode}
	}
	return result, nil
}

func (c *LocalRetrievalClient) Search(ctx context.Context, tenantID string, request HybridSearchRequest) (map[string]any, error) {
	payload := map[string]any{
		"tenant_id": tenantID, "query": request.Query,
	}
	if request.Limit != 0 { payload["limit"] = request.Limit }
	if request.TranscriptID != nil { payload["transcript_id"] = *request.TranscriptID }
	if request.AssetID != nil { payload["asset_id"] = *request.AssetID }
	if request.Revision != nil { payload["revision"] = *request.Revision }
	if request.Language != nil { payload["language"] = *request.Language }
	if request.SpeakerID != nil { payload["speaker_id"] = *request.SpeakerID }
	if request.StartMS != nil { payload["start_ms"] = *request.StartMS }
	if request.EndMS != nil { payload["end_ms"] = *request.EndMS }
	return c.call(ctx, "/internal/v1/search", payload)
}

func (c *LocalRetrievalClient) IndexTranscript(ctx context.Context, tenantID, transcriptID string, revision *int) (map[string]any, error) {
	payload := map[string]any{"tenant_id": tenantID, "transcript_id": transcriptID}
	if revision != nil { payload["revision"] = *revision }
	return c.call(ctx, "/internal/v1/index", payload)
}
