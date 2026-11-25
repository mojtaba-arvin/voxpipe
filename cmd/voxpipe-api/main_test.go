package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCheckReadinessURL(t *testing.T) {
	t.Run("ready", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) }))
		defer server.Close()
		if err := checkReadinessURL(server.URL); err != nil { t.Fatalf("healthy readiness endpoint should pass: %v", err) }
	})
	t.Run("not ready", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { http.Error(w, "private dependency error", http.StatusServiceUnavailable) }))
		defer server.Close()
		if err := checkReadinessURL(server.URL); err == nil { t.Fatal("unready endpoint must fail the container probe") }
	})
	t.Run("redirect is not followed", func(t *testing.T) {
		called := false
		target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { called = true }))
		defer target.Close()
		redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, http.StatusFound) }))
		defer redirect.Close()
		if err := checkReadinessURL(redirect.URL); err == nil { t.Fatal("redirect must not count as readiness") }
		if called { t.Fatal("healthcheck followed an unexpected redirect") }
	})
}

func TestCheckReadinessUsesConfiguredLoopbackPort(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health/ready" { t.Errorf("unexpected path: %s", r.URL.Path) }
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("VOXPIPE_LISTEN_ADDR", strings.TrimPrefix(server.URL, "http://"))
	if err := checkReadiness(); err != nil { t.Fatalf("healthcheck should probe the configured local port: %v", err) }
}

func TestCheckReadinessRejectsInvalidListenAddress(t *testing.T) {
	t.Setenv("VOXPIPE_LISTEN_ADDR", "not-an-address")
	if err := checkReadiness(); err == nil { t.Fatal("invalid listen address must fail closed") }
}
