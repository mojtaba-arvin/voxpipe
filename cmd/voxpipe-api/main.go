package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/mojtaba-arvin/voxpipe/internal/httpapi"
)

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	dsn := strings.TrimSpace(os.Getenv("VOXPIPE_DATABASE_DSN"))
	auth, err := httpapi.NewAuthenticatorFromEnv()
	if err != nil { log.Fatalf("configure authentication: %v", err) }
	stagingRoot := envOr("VOXPIPE_STAGING_ROOT", "./var/staging")
	maxUpload, err := envInt64("VOXPIPE_MAX_UPLOAD_BYTES", 512*1024*1024)
	if err != nil { log.Fatalf("configure upload limit: %v", err) }
	sessionTTLSeconds, err := envInt64("VOXPIPE_SESSION_TTL_SECONDS", 86400)
	if err != nil { log.Fatalf("configure session TTL: %v", err) }
	maxAttempts, err := envInt("VOXPIPE_JOB_MAX_ATTEMPTS", 5)
	if err != nil { log.Fatalf("configure job attempts: %v", err) }

	store, err := httpapi.NewPostgresStore(ctx, dsn)
	if err != nil { log.Fatalf("connect database: %v", err) }
	defer store.Close()
	stager, err := httpapi.NewLocalStager(stagingRoot)
	if err != nil { log.Fatalf("configure private staging: %v", err) }
	api, err := httpapi.NewServer(store, auth, stager, maxUpload, time.Duration(sessionTTLSeconds)*time.Second, maxAttempts)
	if err != nil { log.Fatalf("configure HTTP API: %v", err) }

	server := &http.Server{
		Addr:              envOr("VOXPIPE_LISTEN_ADDR", ":8080"),
		Handler:           api.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    1 << 20,
	}
	go func() {
		log.Printf("VoxPipe ingestion API listening on %s", server.Addr)
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
