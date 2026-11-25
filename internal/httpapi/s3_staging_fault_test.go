package httpapi

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"testing"

	"github.com/aws/aws-sdk-go-v2/service/s3"
)

type faultInjectingS3StagingClient struct {
	*fakeS3StagingClient
	failPutAfterCommit bool
	failHead            bool
	failDelete          int
}

func (f *faultInjectingS3StagingClient) PutObject(ctx context.Context, input *s3.PutObjectInput, options ...func(*s3.Options)) (*s3.PutObjectOutput, error) {
	output, err := f.fakeS3StagingClient.PutObject(ctx, input, options...)
	if err != nil {
		return output, err
	}
	if f.failPutAfterCommit {
		f.failPutAfterCommit = false
		return nil, errors.New("simulated provider response timeout")
	}
	return output, nil
}

func (f *faultInjectingS3StagingClient) HeadObject(ctx context.Context, input *s3.HeadObjectInput, options ...func(*s3.Options)) (*s3.HeadObjectOutput, error) {
	if f.failHead {
		return nil, errors.New("simulated object-store read outage with private provider detail")
	}
	return f.fakeS3StagingClient.HeadObject(ctx, input, options...)
}

func (f *faultInjectingS3StagingClient) DeleteObject(ctx context.Context, input *s3.DeleteObjectInput, options ...func(*s3.Options)) (*s3.DeleteObjectOutput, error) {
	if f.failDelete > 0 {
		f.failDelete--
		return nil, errors.New("simulated object-store delete outage with private provider detail")
	}
	return f.fakeS3StagingClient.DeleteObject(ctx, input, options...)
}

func newFaultInjectingStager(t *testing.T, client S3StagingClient) *S3Stager {
	t.Helper()
	stager, err := NewS3Stager(client, "voxpipe-test", 1024, "AES256", "")
	if err != nil {
		t.Fatal(err)
	}
	return stager
}

func TestS3StagerReconcilesAmbiguousPutAfterProviderCommit(t *testing.T) {
	client := &faultInjectingS3StagingClient{
		fakeS3StagingClient: newFakeS3StagingClient(),
		failPutAfterCommit: true,
	}
	stager := newFaultInjectingStager(t, client)
	tenantID := "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10"
	sessionID := "e41a2e87-b4e2-4b1c-8e3a-8c2e2b8c3e21"
	body := []byte("committed despite lost response")
	digest := fmt.Sprintf("%x", sha256Bytes(body))

	size, reference, err := stager.Write(context.Background(), tenantID, sessionID, bytes.NewReader(body), int64(len(body)), 1024, digest)
	if err != nil {
		t.Fatalf("ambiguous response should reconcile to the committed object: %v", err)
	}
	if size != int64(len(body)) || reference == "" {
		t.Fatalf("unexpected write result: size=%d reference=%q", size, reference)
	}
}

func TestS3StagerDoesNotMisclassifyVerificationOutageAsCorruptUpload(t *testing.T) {
	client := &faultInjectingS3StagingClient{
		fakeS3StagingClient: newFakeS3StagingClient(),
		failHead:            true,
	}
	stager := newFaultInjectingStager(t, client)
	tenantID := "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10"
	sessionID := "e41a2e87-b4e2-4b1c-8e3a-8c2e2b8c3e21"
	body := []byte("object stored while verification endpoint is unavailable")
	digest := fmt.Sprintf("%x", sha256Bytes(body))

	_, _, err := stager.Write(context.Background(), tenantID, sessionID, bytes.NewReader(body), int64(len(body)), 1024, digest)
	if err == nil {
		t.Fatal("expected a retryable verification error")
	}
	if errors.Is(err, ErrUploadIntegrity) {
		t.Fatalf("provider outage must not be classified as corrupt content: %v", err)
	}
	if bytes.Contains([]byte(err.Error()), []byte("private provider detail")) {
		t.Fatalf("provider error details leaked: %v", err)
	}
	if len(client.objects) != 1 {
		t.Fatalf("committed staging object must be preserved for retry, got %d objects", len(client.objects))
	}
}

func TestS3StagerCleanupCanBeRetriedAfterProviderOutage(t *testing.T) {
	client := &faultInjectingS3StagingClient{
		fakeS3StagingClient: newFakeS3StagingClient(),
		failDelete:          1,
	}
	stager := newFaultInjectingStager(t, client)
	tenantID := "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10"
	sessionID := "e41a2e87-b4e2-4b1c-8e3a-8c2e2b8c3e21"
	body := []byte("terminal staging object")
	digest := fmt.Sprintf("%x", sha256Bytes(body))
	if _, _, err := stager.Write(context.Background(), tenantID, sessionID, bytes.NewReader(body), int64(len(body)), 1024, digest); err != nil {
		t.Fatal(err)
	}

	if err := stager.Remove(context.Background(), tenantID, sessionID); err == nil {
		t.Fatal("first cleanup must surface the transient provider failure")
	}
	if len(client.objects) != 1 {
		t.Fatal("failed cleanup must preserve the staging object")
	}
	if err := stager.Remove(context.Background(), tenantID, sessionID); err != nil {
		t.Fatalf("cleanup retry should succeed: %v", err)
	}
	if len(client.objects) != 0 {
		t.Fatal("successful cleanup retry must remove the staging object")
	}
}
