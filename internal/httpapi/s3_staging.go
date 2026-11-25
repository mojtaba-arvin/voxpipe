package httpapi

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
)

const maxS3SinglePutBytes int64 = 5 * 1024 * 1024 * 1024

var ErrStagingNotFound = errors.New("staged upload does not exist")

type S3StagingClient interface {
	PutObject(context.Context, *s3.PutObjectInput, ...func(*s3.Options)) (*s3.PutObjectOutput, error)
	HeadObject(context.Context, *s3.HeadObjectInput, ...func(*s3.Options)) (*s3.HeadObjectOutput, error)
	GetObject(context.Context, *s3.GetObjectInput, ...func(*s3.Options)) (*s3.GetObjectOutput, error)
	DeleteObject(context.Context, *s3.DeleteObjectInput, ...func(*s3.Options)) (*s3.DeleteObjectOutput, error)
}

type S3Stager struct {
	client     S3StagingClient
	bucket     string
	maxSize    int64
	encryption types.ServerSideEncryption
	kmsKeyID   string
}

func NewS3Stager(
	client S3StagingClient,
	bucket string,
	maxSize int64,
	encryption string,
	kmsKeyID string,
) (*S3Stager, error) {
	if client == nil || strings.TrimSpace(bucket) == "" {
		return nil, fmt.Errorf("S3 client and bucket are required")
	}
	if maxSize < 1 || maxSize > maxS3SinglePutBytes {
		return nil, fmt.Errorf("S3 staging size limit must be between 1 byte and 5 GiB")
	}
	var algorithm types.ServerSideEncryption
	switch encryption {
	case "AES256":
		algorithm = types.ServerSideEncryptionAes256
		if strings.TrimSpace(kmsKeyID) != "" {
			return nil, fmt.Errorf("KMS key ID is only valid with aws:kms encryption")
		}
	case "aws:kms":
		algorithm = types.ServerSideEncryptionAwsKms
		if strings.TrimSpace(kmsKeyID) == "" {
			return nil, fmt.Errorf("KMS key ID is required with aws:kms encryption")
		}
	default:
		return nil, fmt.Errorf("S3 server-side encryption must be explicitly AES256 or aws:kms")
	}
	return &S3Stager{
		client:     client,
		bucket:     strings.TrimSpace(bucket),
		maxSize:    maxSize,
		encryption: algorithm,
		kmsKeyID:   strings.TrimSpace(kmsKeyID),
	}, nil
}

func NewS3StagerFromEnv(ctx context.Context, maxSize int64) (*S3Stager, error) {
	env := func(name string) string { return strings.TrimSpace(os.Getenv(name)) }
	bucket, region := env("VOXPIPE_S3_BUCKET"), env("VOXPIPE_S3_REGION")
	encryption, kmsKeyID := env("VOXPIPE_S3_SERVER_SIDE_ENCRYPTION"), env("VOXPIPE_S3_KMS_KEY_ID")
	endpoint, accessKey, secretKey := env("VOXPIPE_S3_ENDPOINT_URL"), env("VOXPIPE_S3_ACCESS_KEY_ID"), env("VOXPIPE_S3_SECRET_ACCESS_KEY")
	addressingStyle := strings.ToLower(env("VOXPIPE_S3_ADDRESSING_STYLE"))
	if addressingStyle == "" {
		addressingStyle = "path"
	}
	if bucket == "" || region == "" {
		return nil, fmt.Errorf("VOXPIPE_S3_BUCKET and VOXPIPE_S3_REGION are required")
	}
	if (accessKey == "") != (secretKey == "") {
		return nil, fmt.Errorf("VOXPIPE_S3_ACCESS_KEY_ID and VOXPIPE_S3_SECRET_ACCESS_KEY must be configured together")
	}
	if addressingStyle != "path" && addressingStyle != "virtual" {
		return nil, fmt.Errorf("VOXPIPE_S3_ADDRESSING_STYLE must be path or virtual")
	}
	if encryption != "AES256" && encryption != "aws:kms" {
		return nil, fmt.Errorf("VOXPIPE_S3_SERVER_SIDE_ENCRYPTION must explicitly be AES256 or aws:kms")
	}
	if encryption == "aws:kms" && kmsKeyID == "" {
		return nil, fmt.Errorf("VOXPIPE_S3_KMS_KEY_ID is required with aws:kms encryption")
	}
	if encryption == "AES256" && kmsKeyID != "" {
		return nil, fmt.Errorf("VOXPIPE_S3_KMS_KEY_ID is only valid with aws:kms encryption")
	}
	if endpoint != "" {
		if err := validateS3Endpoint(endpoint); err != nil {
			return nil, err
		}
	}

	maxAttempts, err := envInt("VOXPIPE_S3_MAX_ATTEMPTS", 3)
	if err != nil || maxAttempts < 1 || maxAttempts > 5 {
		return nil, fmt.Errorf("VOXPIPE_S3_MAX_ATTEMPTS must be between 1 and 5")
	}
	connectTimeout, err := envDurationSeconds("VOXPIPE_S3_CONNECT_TIMEOUT_SECONDS", 5, 30)
	if err != nil {
		return nil, err
	}
	readTimeout, err := envDurationSeconds("VOXPIPE_S3_READ_TIMEOUT_SECONDS", 60, 120)
	if err != nil {
		return nil, err
	}

	transport := http.DefaultTransport.(*http.Transport).Clone()
	dialer := &net.Dialer{Timeout: connectTimeout, KeepAlive: 30 * time.Second}
	transport.DialContext = dialer.DialContext
	transport.TLSHandshakeTimeout = connectTimeout
	transport.ResponseHeaderTimeout = readTimeout
	transport.ExpectContinueTimeout = time.Second
	transport.IdleConnTimeout = 90 * time.Second
	httpClient := &http.Client{Transport: transport}

	options := []func(*awsconfig.LoadOptions) error{
		awsconfig.WithRegion(region),
		awsconfig.WithRetryMaxAttempts(maxAttempts),
		awsconfig.WithHTTPClient(httpClient),
	}
	if accessKey != "" {
		options = append(options, awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(accessKey, secretKey, "")))
	}
	configuration, err := awsconfig.LoadDefaultConfig(ctx, options...)
	if err != nil {
		return nil, fmt.Errorf("configure S3-compatible credentials and region")
	}
	client := s3.NewFromConfig(configuration, func(options *s3.Options) {
		options.UsePathStyle = addressingStyle == "path"
		if endpoint != "" {
			options.BaseEndpoint = aws.String(endpoint)
		}
	})
	return NewS3Stager(client, bucket, maxSize, encryption, kmsKeyID)
}

func validateS3Endpoint(value string) error {
	parsed, err := url.Parse(value)
	if err != nil || parsed.Hostname() == "" || parsed.User != nil ||
		(parsed.Scheme != "https" && parsed.Scheme != "http") ||
		(parsed.Path != "" && parsed.Path != "/") || parsed.RawQuery != "" || parsed.Fragment != "" {
		return fmt.Errorf("VOXPIPE_S3_ENDPOINT_URL must be an HTTP(S) origin without paths or embedded credentials")
	}
	host := strings.ToLower(parsed.Hostname())
	loopback := host == "localhost" || host == "::1"
	if ip := net.ParseIP(host); ip != nil && ip.IsLoopback() {
		loopback = true
	}
	if parsed.Scheme != "https" && !loopback {
		return fmt.Errorf("VOXPIPE_S3_ENDPOINT_URL must use HTTPS outside loopback development endpoints")
	}
	return nil
}

func envInt(name string, fallback int) (int, error) {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback, nil
	}
	return strconv.Atoi(value)
}

func envDurationSeconds(name string, fallback, maximum int) (time.Duration, error) {
	value, err := envInt(name, fallback)
	if err != nil || value < 1 || value > maximum {
		return 0, fmt.Errorf("%s must be between 1 and %d seconds", name, maximum)
	}
	return time.Duration(value) * time.Second, nil
}

func (s *S3Stager) Backend() string { return "s3_compatible" }

func (s *S3Stager) Reference(tenantID, sessionID string) (string, error) {
	if !isUUID(tenantID) || !isUUID(sessionID) {
		return "", fmt.Errorf("tenant and session identifiers must be UUIDs")
	}
	return "staging/v1/" + tenantID + "/" + sessionID + "/source", nil
}

func (s *S3Stager) Write(
	ctx context.Context,
	tenantID, sessionID string,
	source io.Reader,
	expectedSize, maxSize int64,
	expectedSHA256 string,
) (int64, string, error) {
	reference, err := s.Reference(tenantID, sessionID)
	if err != nil {
		return 0, "", err
	}
	if expectedSize < 1 || maxSize < 1 || expectedSize > maxSize || expectedSize > s.maxSize {
		return 0, "", ErrUploadTooLarge
	}
	if len(expectedSHA256) != sha256.Size*2 {
		return 0, "", ErrUploadIntegrity
	}
	if _, err := hex.DecodeString(expectedSHA256); err != nil {
		return 0, "", ErrUploadIntegrity
	}
	if err := ctx.Err(); err != nil {
		return 0, "", err
	}

	temporary, err := os.CreateTemp("", ".voxpipe-staging-*.part")
	if err != nil {
		return 0, "", fmt.Errorf("create private upload spool: %w", err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(0600); err != nil {
		_ = temporary.Close()
		return 0, "", fmt.Errorf("secure private upload spool: %w", err)
	}
	digest := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(temporary, digest), io.LimitReader(contextReader{ctx: ctx, reader: source}, expectedSize+1))
	if copyErr != nil {
		_ = temporary.Close()
		return 0, "", fmt.Errorf("stage upload stream: %w", copyErr)
	}
	if written > maxSize || written > s.maxSize {
		_ = temporary.Close()
		return 0, "", ErrUploadTooLarge
	}
	actualDigest := hex.EncodeToString(digest.Sum(nil))
	if written != expectedSize || actualDigest != strings.ToLower(expectedSHA256) {
		_ = temporary.Close()
		return 0, "", ErrUploadIntegrity
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return 0, "", fmt.Errorf("sync private upload spool: %w", err)
	}
	if _, err := temporary.Seek(0, io.SeekStart); err != nil {
		_ = temporary.Close()
		return 0, "", fmt.Errorf("rewind private upload spool: %w", err)
	}

	input := &s3.PutObjectInput{
		Bucket:               aws.String(s.bucket),
		Key:                  aws.String(reference),
		Body:                 temporary,
		ContentLength:        aws.Int64(written),
		ChecksumSHA256:       aws.String(base64.StdEncoding.EncodeToString(digest.Sum(nil))),
		IfNoneMatch:          aws.String("*"),
		Metadata:             map[string]string{"voxpipe-sha256": actualDigest},
		ServerSideEncryption: s.encryption,
	}
	if s.kmsKeyID != "" {
		input.SSEKMSKeyId = aws.String(s.kmsKeyID)
	}
	_, putErr := s.client.PutObject(ctx, input)
	closeErr := temporary.Close()
	if putErr != nil {
		existingSize, existingDigest, verifyErr := s.Verify(ctx, tenantID, sessionID)
		if verifyErr == nil {
			if existingSize == written && existingDigest == actualDigest {
				return written, reference, nil
			}
			return 0, "", ErrUploadConflict
		}
		if !isS3NotFound(verifyErr) {
			return 0, "", fmt.Errorf("store staged upload: provider write could not be reconciled")
		}
		return 0, "", fmt.Errorf("store staged upload: S3-compatible provider rejected the write")
	}
	if closeErr != nil {
		return 0, "", fmt.Errorf("close private upload spool: %w", closeErr)
	}
	existingSize, existingDigest, verifyErr := s.Verify(ctx, tenantID, sessionID)
	if verifyErr != nil || existingSize != written || existingDigest != actualDigest {
		return 0, "", ErrUploadIntegrity
	}
	return written, reference, nil
}

func (s *S3Stager) Verify(ctx context.Context, tenantID, sessionID string) (int64, string, error) {
	reference, err := s.Reference(tenantID, sessionID)
	if err != nil {
		return 0, "", err
	}
	head, err := s.client.HeadObject(ctx, &s3.HeadObjectInput{
		Bucket: aws.String(s.bucket), Key: aws.String(reference), ChecksumMode: types.ChecksumModeEnabled,
	})
	if err != nil && isUnsupportedS3ChecksumMode(err) {
		head, err = s.client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(s.bucket), Key: aws.String(reference)})
	}
	if err != nil {
		if isS3NotFound(err) {
			return 0, "", ErrStagingNotFound
		}
		return 0, "", fmt.Errorf("read staged object metadata: S3-compatible provider request failed")
	}
	if head.ContentLength == nil || *head.ContentLength < 0 || *head.ContentLength > s.maxSize {
		return 0, "", ErrUploadIntegrity
	}
	if err := s.verifyEncryption(head.ServerSideEncryption, head.SSEKMSKeyId); err != nil {
		return 0, "", err
	}
	metadataDigest := head.Metadata["voxpipe-sha256"]
	if head.ChecksumSHA256 != nil {
		decoded, decodeErr := base64.StdEncoding.DecodeString(*head.ChecksumSHA256)
		if decodeErr != nil || len(decoded) != sha256.Size {
			return 0, "", ErrUploadIntegrity
		}
		digest := hex.EncodeToString(decoded)
		if metadataDigest != "" && metadataDigest != digest {
			return 0, "", ErrUploadIntegrity
		}
		return *head.ContentLength, digest, nil
	}
	output, getErr := s.client.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(s.bucket), Key: aws.String(reference)})
	if getErr != nil {
		if isS3NotFound(getErr) {
			return 0, "", ErrStagingNotFound
		}
		return 0, "", fmt.Errorf("read staged object: S3-compatible provider request failed")
	}
	defer output.Body.Close()
	if err := s.verifyEncryption(output.ServerSideEncryption, output.SSEKMSKeyId); err != nil {
		return 0, "", err
	}
	digest := sha256.New()
	size, readErr := io.Copy(digest, output.Body)
	if readErr != nil {
		return 0, "", fmt.Errorf("verify staged object stream: %w", readErr)
	}
	if size != *head.ContentLength {
		return 0, "", ErrUploadIntegrity
	}
	checksum := hex.EncodeToString(digest.Sum(nil))
	if metadataDigest != "" && metadataDigest != checksum {
		return 0, "", ErrUploadIntegrity
	}
	return size, checksum, nil
}

func (s *S3Stager) Remove(ctx context.Context, tenantID, sessionID string) error {
	reference, err := s.Reference(tenantID, sessionID)
	if err != nil {
		return err
	}
	_, err = s.client.DeleteObject(ctx, &s3.DeleteObjectInput{Bucket: aws.String(s.bucket), Key: aws.String(reference)})
	if err != nil && !isS3NotFound(err) {
		return fmt.Errorf("delete staged object: S3-compatible provider request failed")
	}
	return nil
}

func (s *S3Stager) verifyEncryption(actual types.ServerSideEncryption, actualKMSKeyID *string) error {
	if actual != s.encryption {
		return ErrUploadIntegrity
	}
	if s.encryption == types.ServerSideEncryptionAwsKms &&
		(aws.ToString(actualKMSKeyID) == "" || aws.ToString(actualKMSKeyID) != s.kmsKeyID) {
		return ErrUploadIntegrity
	}
	return nil
}

func isS3NotFound(err error) bool {
	var apiError smithy.APIError
	if errors.As(err, &apiError) {
		code := apiError.ErrorCode()
		if code == "NoSuchKey" || code == "NotFound" || code == "404" {
			return true
		}
	}
	var responseError *smithyhttp.ResponseError
	return errors.As(err, &responseError) && responseError.HTTPStatusCode() == http.StatusNotFound
}

func isUnsupportedS3ChecksumMode(err error) bool {
	var apiError smithy.APIError
	if errors.As(err, &apiError) {
		code := apiError.ErrorCode()
		if code == "InvalidArgument" || code == "NotImplemented" {
			return true
		}
	}
	var responseError *smithyhttp.ResponseError
	return errors.As(err, &responseError) && responseError.HTTPStatusCode() == http.StatusNotImplemented
}
