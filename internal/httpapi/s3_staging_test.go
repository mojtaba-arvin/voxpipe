package httpapi

import (
	"bytes"
	"crypto/sha256"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/aws/smithy-go"
)

type fakeS3StagingObject struct {
	body       []byte
	checksum   string
	metadata   map[string]string
	encryption types.ServerSideEncryption
	kmsKeyID   string
}

type fakeS3StagingClient struct {
	objects       map[string]fakeS3StagingObject
	omitChecksum  bool
}

func newFakeS3StagingClient() *fakeS3StagingClient {
	return &fakeS3StagingClient{objects: make(map[string]fakeS3StagingObject)}
}

func (f *fakeS3StagingClient) PutObject(_ context.Context, input *s3.PutObjectInput, _ ...func(*s3.Options)) (*s3.PutObjectOutput, error) {
	key := aws.ToString(input.Key)
	if _, exists := f.objects[key]; exists && aws.ToString(input.IfNoneMatch) == "*" {
		return nil, &smithy.GenericAPIError{Code: "PreconditionFailed", Message: "conditional write failed", Fault: smithy.FaultClient}
	}
	body, err := io.ReadAll(input.Body)
	if err != nil { return nil, err }
	if int64(len(body)) != aws.ToInt64(input.ContentLength) {
		return nil, fmt.Errorf("content length mismatch")
	}
	checksum := base64.StdEncoding.EncodeToString(sha256Bytes(body))
	if checksum != aws.ToString(input.ChecksumSHA256) {
		return nil, fmt.Errorf("provider checksum mismatch")
	}
	f.objects[key] = fakeS3StagingObject{
		body: body,
		checksum: checksum,
		metadata: input.Metadata,
		encryption: input.ServerSideEncryption,
		kmsKeyID: aws.ToString(input.SSEKMSKeyId),
	}
	return &s3.PutObjectOutput{}, nil
}

func (f *fakeS3StagingClient) HeadObject(_ context.Context, input *s3.HeadObjectInput, _ ...func(*s3.Options)) (*s3.HeadObjectOutput, error) {
	object, ok := f.objects[aws.ToString(input.Key)]
	if !ok {
		return nil, &smithy.GenericAPIError{Code: "NoSuchKey", Message: "not found", Fault: smithy.FaultClient}
	}
	output := &s3.HeadObjectOutput{
		ContentLength: aws.Int64(int64(len(object.body))),
		Metadata: object.metadata,
		ServerSideEncryption: object.encryption,
	}
	if object.kmsKeyID != "" { output.SSEKMSKeyId = aws.String(object.kmsKeyID) }
	if !f.omitChecksum { output.ChecksumSHA256 = aws.String(object.checksum) }
	return output, nil
}

func (f *fakeS3StagingClient) GetObject(_ context.Context, input *s3.GetObjectInput, _ ...func(*s3.Options)) (*s3.GetObjectOutput, error) {
	object, ok := f.objects[aws.ToString(input.Key)]
	if !ok {
		return nil, &smithy.GenericAPIError{Code: "NoSuchKey", Message: "not found", Fault: smithy.FaultClient}
	}
	output := &s3.GetObjectOutput{
		Body: io.NopCloser(bytes.NewReader(object.body)),
		ServerSideEncryption: object.encryption,
	}
	if object.kmsKeyID != "" { output.SSEKMSKeyId = aws.String(object.kmsKeyID) }
	return output, nil
}

func (f *fakeS3StagingClient) DeleteObject(_ context.Context, input *s3.DeleteObjectInput, _ ...func(*s3.Options)) (*s3.DeleteObjectOutput, error) {
	delete(f.objects, aws.ToString(input.Key))
	return &s3.DeleteObjectOutput{}, nil
}

func sha256Bytes(value []byte) []byte {
	digest := sha256.Sum256(value)
	return digest[:]
}

func TestS3StagerWriteVerifyAndRemove(t *testing.T) {
	client := newFakeS3StagingClient()
	stager, err := NewS3Stager(client, "voxpipe-test", 1024, "AES256", "")
	if err != nil { t.Fatal(err) }
	tenantID, sessionID := "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10", "e41a2e87-b4e2-4b1c-8e3a-8c2e2b8c3e21"
	body := []byte("shared staging fixture")
	digest := fmt.Sprintf("%x", sha256Bytes(body))
	size, reference, err := stager.Write(context.Background(), tenantID, sessionID, bytes.NewReader(body), int64(len(body)), 1024, digest)
	if err != nil { t.Fatal(err) }
	if reference != "staging/v1/"+tenantID+"/"+sessionID+"/source" { t.Fatalf("unexpected reference: %s", reference) }
	if size != int64(len(body)) { t.Fatalf("unexpected size: %d", size) }
	verifiedSize, verifiedDigest, err := stager.Verify(context.Background(), tenantID, sessionID)
	if err != nil { t.Fatal(err) }
	if verifiedSize != size || verifiedDigest != digest { t.Fatalf("unexpected verification result: %d %s", verifiedSize, verifiedDigest) }
	if err := stager.Remove(context.Background(), tenantID, sessionID); err != nil { t.Fatal(err) }
	if _, _, err := stager.Verify(context.Background(), tenantID, sessionID); !errors.Is(err, ErrStagingNotFound) {
		t.Fatalf("expected ErrStagingNotFound, got %v", err)
	}
}

func TestS3StagerConditionalWriteIsIdempotentAndConflictsOnDifferentBytes(t *testing.T) {
	client := newFakeS3StagingClient()
	stager, err := NewS3Stager(client, "voxpipe-test", 1024, "AES256", "")
	if err != nil { t.Fatal(err) }
	tenantID, sessionID := "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10", "e41a2e87-b4e2-4b1c-8e3a-8c2e2b8c3e21"
	first := []byte("same source")
	firstDigest := fmt.Sprintf("%x", sha256Bytes(first))
	if _, _, err := stager.Write(context.Background(), tenantID, sessionID, bytes.NewReader(first), int64(len(first)), 1024, firstDigest); err != nil {
		t.Fatal(err)
	}
	if _, _, err := stager.Write(context.Background(), tenantID, sessionID, bytes.NewReader(first), int64(len(first)), 1024, firstDigest); err != nil {
		t.Fatalf("identical retry should be idempotent: %v", err)
	}
	second := []byte("different source")
	secondDigest := fmt.Sprintf("%x", sha256Bytes(second))
	if _, _, err := stager.Write(context.Background(), tenantID, sessionID, bytes.NewReader(second), int64(len(second)), 1024, secondDigest); !errors.Is(err, ErrUploadConflict) {
		t.Fatalf("expected ErrUploadConflict, got %v", err)
	}
}

func TestS3StagerStreamsChecksumWhenHeadOmitsProviderChecksum(t *testing.T) {
	client := newFakeS3StagingClient()
	client.omitChecksum = true
	stager, err := NewS3Stager(client, "voxpipe-test", 1024, "AES256", "")
	if err != nil { t.Fatal(err) }
	tenantID, sessionID := "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10", "e41a2e87-b4e2-4b1c-8e3a-8c2e2b8c3e21"
	body := []byte("provider checksum fallback")
	digest := fmt.Sprintf("%x", sha256Bytes(body))
	if _, _, err := stager.Write(context.Background(), tenantID, sessionID, bytes.NewReader(body), int64(len(body)), 1024, digest); err != nil {
		t.Fatal(err)
	}
}

func TestS3StagerRequiresExplicitEncryptionAndKMSKey(t *testing.T) {
	client := newFakeS3StagingClient()
	if _, err := NewS3Stager(client, "voxpipe-test", 1024, "", ""); err == nil {
		t.Fatal("expected encryption configuration to be required")
	}
	if _, err := NewS3Stager(client, "voxpipe-test", 1024, "aws:kms", ""); err == nil {
		t.Fatal("expected KMS key identifier to be required")
	}
	if _, err := NewS3Stager(client, "voxpipe-test", 1024, "AES256", "key-id"); err == nil {
		t.Fatal("expected KMS key identifier to be rejected for AES256")
	}
}

func TestS3StagerRejectsNonUUIDReferences(t *testing.T) {
	stager, err := NewS3Stager(newFakeS3StagingClient(), "voxpipe-test", 1024, "AES256", "")
	if err != nil { t.Fatal(err) }
	if _, err := stager.Reference("../tenant", "session"); err == nil {
		t.Fatal("expected invalid tenant/session identifiers to be rejected")
	}
}

func TestNewS3StagerFromEnvRejectsRemoteHTTP(t *testing.T) {
	t.Setenv("VOXPIPE_S3_BUCKET", "voxpipe-test")
	t.Setenv("VOXPIPE_S3_REGION", "us-east-1")
	t.Setenv("VOXPIPE_S3_SERVER_SIDE_ENCRYPTION", "AES256")
	t.Setenv("VOXPIPE_S3_ENDPOINT_URL", "http://objects.example.com")
	if _, err := NewS3StagerFromEnv(context.Background(), 1024); err == nil {
		t.Fatal("expected non-loopback HTTP endpoint to be rejected")
	}
}
