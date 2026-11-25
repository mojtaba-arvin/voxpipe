package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

func TestS3StagerSiloIntegration(t *testing.T) {
	if os.Getenv("VOXPIPE_S3_ENDPOINT_URL") == "" {
		t.Skip("S3 integration endpoint is not configured")
	}
	ctx := context.Background()
	region := os.Getenv("VOXPIPE_S3_REGION")
	bucket := os.Getenv("VOXPIPE_S3_BUCKET")
	if region == "" || bucket == "" {
		t.Fatal("S3 integration bucket and region must be configured")
	}
	configuration, err := awsconfig.LoadDefaultConfig(
		ctx,
		awsconfig.WithRegion(region),
		awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(
			os.Getenv("VOXPIPE_S3_ACCESS_KEY_ID"),
			os.Getenv("VOXPIPE_S3_SECRET_ACCESS_KEY"),
			"",
		)),
	)
	if err != nil {
		t.Fatalf("load integration AWS config: %v", err)
	}
	addressingStyle := os.Getenv("VOXPIPE_S3_ADDRESSING_STYLE")
	client := s3.NewFromConfig(configuration, func(options *s3.Options) {
		options.BaseEndpoint = aws.String(os.Getenv("VOXPIPE_S3_ENDPOINT_URL"))
		options.UsePathStyle = addressingStyle != "virtual"
	})
	if _, err := client.CreateBucket(ctx, &s3.CreateBucketInput{Bucket: aws.String(bucket)}); err != nil {
		t.Fatalf("create S3 integration bucket: %v", err)
	}
	defer func() {
		_, _ = client.DeleteBucket(context.Background(), &s3.DeleteBucketInput{Bucket: aws.String(bucket)})
	}()

	stager, err := NewS3StagerFromEnv(ctx, 512*1024*1024)
	if err != nil {
		t.Fatalf("configure S3 stager: %v", err)
	}
	tenantID := "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10"
	sessionID := "e41a2e87-b4e2-4b1c-8e3a-8c2e2b8c3e21"
	defer func() {
		_ = stager.Remove(context.Background(), tenantID, sessionID)
	}()
	body := []byte("cross-language staging integration fixture")
	sum := sha256.Sum256(body)
	digest := hex.EncodeToString(sum[:])

	size, reference, err := stager.Write(ctx, tenantID, sessionID, bytes.NewReader(body), int64(len(body)), 512*1024*1024, digest)
	if err != nil {
		t.Fatalf("write S3 staging object: %v", err)
	}
	if size != int64(len(body)) || reference != "staging/v1/"+tenantID+"/"+sessionID+"/source" {
		t.Fatalf("unexpected staged object reference/size: %s %d", reference, size)
	}
	if verifiedSize, verifiedDigest, err := stager.Verify(ctx, tenantID, sessionID); err != nil || verifiedSize != size || verifiedDigest != digest {
		t.Fatalf("verify S3 staging object: size=%d digest=%s err=%v", verifiedSize, verifiedDigest, err)
	}
	if _, _, err := stager.Write(ctx, tenantID, sessionID, bytes.NewReader(body), int64(len(body)), 512*1024*1024, digest); err != nil {
		t.Fatalf("identical S3 staging retry should be idempotent: %v", err)
	}
	different := []byte("different source bytes")
	differentSum := sha256.Sum256(different)
	if _, _, err := stager.Write(ctx, tenantID, sessionID, bytes.NewReader(different), int64(len(different)), 512*1024*1024, hex.EncodeToString(differentSum[:])); err != ErrUploadConflict {
		t.Fatalf("expected conflicting immutable write, got %v", err)
	}
	if err := stager.Remove(ctx, tenantID, sessionID); err != nil {
		t.Fatalf("remove S3 staging object: %v", err)
	}
	if _, _, err := stager.Verify(ctx, tenantID, sessionID); err != ErrStagingNotFound {
		t.Fatalf("expected missing staging object after delete, got %v", err)
	}
}
