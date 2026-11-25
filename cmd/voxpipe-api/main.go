package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/mojtaba-arvin/voxpipe/internal/httpapi"
)

var version = "dev"

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		if err := checkReadiness(); err != nil { os.Exit(1) }
		return
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	dsn := strings.TrimSpace(os.Getenv("VOXPIPE_DATABASE_DSN"))
	auth, err := httpapi.NewAuthenticatorFromEnv()
	if err != nil { log.Fatalf("configure authentication: %v", err) }
	storageBackend := envOr("VOXPIPE_STORAGE_BACKEND", "local")
	maxUpload, err := envInt64("VOXPIPE_MAX_UPLOAD_BYTES", 512*1024*1024)
	if err != nil { log.Fatalf("configure upload limit: %v", err) }
	sessionTTLSeconds, err := envInt64("VOXPIPE_SESSION_TTL_SECONDS", 86400)
	if err != nil { log.Fatalf("configure session TTL: %v", err) }
	maxAttempts, err := envInt("VOXPIPE_JOB_MAX_ATTEMPTS", 5)
	if err != nil { log.Fatalf("configure job attempts: %v", err) }
	retentionDays, err := envInt("VOXPIPE_DEFAULT_RETENTION_DAYS", 0)
	if err != nil { log.Fatalf("configure retention policy: %v", err) }
	maxConcurrentUploads, err := envInt("VOXPIPE_MAX_CONCURRENT_UPLOADS_PER_TENANT", 3)
	if err != nil { log.Fatalf("configure concurrent upload quota: %v", err) }
	maxStagedBytes, err := envInt64("VOXPIPE_MAX_STAGED_BYTES_PER_TENANT", 2*1024*1024*1024)
	if err != nil { log.Fatalf("configure staged byte quota: %v", err) }
	maxIngestionsPerMinute, err := envInt("VOXPIPE_MAX_INGESTIONS_PER_MINUTE_PER_TENANT", 60)
	if err != nil { log.Fatalf("configure ingestion rate limit: %v", err) }
	uploadIdleSeconds, err := envInt64("VOXPIPE_UPLOAD_INACTIVITY_TIMEOUT_SECONDS", 60)
	if err != nil { log.Fatalf("configure upload inactivity timeout: %v", err) }
	uploadMaxSeconds, err := envInt64("VOXPIPE_UPLOAD_MAX_DURATION_SECONDS", 1800)
	if err != nil { log.Fatalf("configure upload maximum duration: %v", err) }

	store, err := httpapi.NewPostgresStore(ctx, dsn)
	if err != nil { log.Fatalf("connect database: %v", err) }
	defer store.Close()
	var stager httpapi.Stager
	switch storageBackend {
	case "local":
		stagingRoot := envOr("VOXPIPE_STAGING_ROOT", "./var/staging")
		stager, err = httpapi.NewLocalStager(stagingRoot)
	case "s3_compatible":
		stager, err = httpapi.NewS3StagerFromEnv(ctx, maxUpload)
	default:
		err = fmt.Errorf("VOXPIPE_STORAGE_BACKEND must be local or s3_compatible")
	}
	if err != nil { log.Fatalf("configure upload staging: %v", err) }
	api, err := httpapi.NewServer(store, auth, stager, maxUpload, time.Duration(sessionTTLSeconds)*time.Second, maxAttempts)
	if err != nil { log.Fatalf("configure HTTP API: %v", err) }
	retrievalClient, err := httpapi.NewLocalRetrievalClientFromEnv()
	if err != nil { log.Fatalf("configure local retrieval service: %v", err) }
	if retrievalClient != nil { api.SetHybridRetrievalClient(retrievalClient) }
	if err := api.SetRetentionDays(retentionDays); err != nil { log.Fatalf("configure retention policy: %v", err) }
	if err := api.SetTenantLimits(httpapi.TenantLimits{MaxConcurrentUploads: maxConcurrentUploads, MaxStagedBytes: maxStagedBytes, MaxIngestionsPerMinute: maxIngestionsPerMinute}); err != nil { log.Fatalf("configure tenant quotas: %v", err) }
	if err := api.SetUploadTimeouts(time.Duration(uploadIdleSeconds)*time.Second, time.Duration(uploadMaxSeconds)*time.Second); err != nil { log.Fatalf("configure upload deadlines: %v", err) }

	server := &http.Server{
		Addr:              envOr("VOXPIPE_LISTEN_ADDR", ":8080"),
		Handler:           api.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    1 << 20,
	}
	go func() {
		log.Printf("VoxPipe ingestion API %s listening on %s", version, server.Addr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("HTTP server failed: %v", err)
			cancel()
		}
	}()
	<-ctx.Done()

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer shutdownCancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		log.Printf("graceful shutdown failed: %v", err)
	}
}

func envOr(name, fallback string) string {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" { return fallback }
	return value
}
func envInt64(name string, fallback int64) (int64, error) {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" { return fallback, nil }
	return strconv.ParseInt(value, 10, 64)
}
func envInt(name string, fallback int) (int, error) {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" { return fallback, nil }
	return strconv.Atoi(value)
}

// checkReadiness probes only loopback and never contacts a configured external endpoint.
func checkReadiness() error {
	address := envOr("VOXPIPE_LISTEN_ADDR", ":8080")
	_, port, err := net.SplitHostPort(address)
	if err != nil { return fmt.Errorf("invalid VOXPIPE_LISTEN_ADDR") }
	portNumber, err := strconv.Atoi(port)
	if err != nil || portNumber < 1 || portNumber > 65535 { return fmt.Errorf("invalid VOXPIPE_LISTEN_ADDR port") }
	return checkReadinessURL("http://127.0.0.1:" + strconv.Itoa(portNumber) + "/health/ready")
}

func checkReadinessURL(target string) error {
	req, err := http.NewRequest(http.MethodGet, target, nil)
	if err != nil { return err }
	client := &http.Client{
		Timeout: 2 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse },
	}
	response, err := client.Do(req)
	if err != nil { return err }
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK { return fmt.Errorf("readiness returned %s", response.Status) }
	return nil
}
