package httpapi

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"
)

func TestPostgresStoreEnforcesTenantStagingAndRateQuotas(t *testing.T) {
	dsn := os.Getenv("VOXPIPE_TEST_POSTGRES_APP_DSN")
	if dsn == "" { t.Skip("PostgreSQL application DSN is not configured") }
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	store, err := NewPostgresStore(ctx, dsn)
	if err != nil { t.Fatal(err) }
	defer store.Close()

	makeSession := func(tenantID, key string, size int64) map[string]any {
		id, _ := newUUID()
		now := time.Now().UTC().Format(time.RFC3339Nano)
		return map[string]any{
			"session_id": id, "tenant_id": tenantID, "idempotency_key": key,
			"state": "created", "staging_reference": tenantID + "/" + id + "/source",
			"upload": map[string]any{"expected_size_bytes": size, "received_size_bytes": int64(0), "expected_sha256": strings.Repeat("a", 64)},
			"created_at": now, "updated_at": now,
			"expires_at": time.Now().UTC().Add(time.Hour).Format(time.RFC3339Nano),
		}
	}
	tenant, _ := newUUID()
	limits := TenantLimits{MaxConcurrentUploads: 2, MaxStagedBytes: 20, MaxIngestionsPerMinute: 10}
	first := makeSession(tenant, "quota-staged-first-0001", 12)
	if _, _, err := store.CreateSession(ctx, tenant, first["idempotency_key"].(string), first, limits); err != nil {
		t.Fatalf("first reservation should fit: %v", err)
	}
	second := makeSession(tenant, "quota-staged-second-0001", 12)
	if _, _, err := store.CreateSession(ctx, tenant, second["idempotency_key"].(string), second, limits); err != ErrQuotaExceeded {
		t.Fatalf("second reservation must exceed staged-byte quota, got %v", err)
	}

	uploadTenant, _ := newUUID()
	uploadLimits := TenantLimits{MaxConcurrentUploads: 1, MaxStagedBytes: 100, MaxIngestionsPerMinute: 10}
	uploadA := makeSession(uploadTenant, "quota-concurrent-first-0001", 1)
	uploadB := makeSession(uploadTenant, "quota-concurrent-second-0001", 1)
	for _, candidate := range []map[string]any{uploadA, uploadB} {
		if _, _, err := store.CreateSession(ctx, uploadTenant, candidate["idempotency_key"].(string), candidate, uploadLimits); err != nil { t.Fatalf("create upload reservation: %v", err) }
		updated := cloneMap(candidate)
		updated["state"] = "receiving"
		updated["updated_at"] = time.Now().UTC().Format(time.RFC3339Nano)
		if err := store.UpdateSession(ctx, uploadTenant, candidate["session_id"].(string), "created", updated); err != nil { t.Fatalf("mark upload receiving: %v", err) }
	}
	owner, _ := newUUID()
	if err := store.BeginUpload(ctx, uploadTenant, uploadA["session_id"].(string), owner, 30, 1); err != nil { t.Fatalf("first upload should be admitted: %v", err) }
	ownerB, _ := newUUID()
	if err := store.BeginUpload(ctx, uploadTenant, uploadB["session_id"].(string), ownerB, 30, 1); err != ErrQuotaExceeded { t.Fatalf("second upload must exceed concurrent quota, got %v", err) }
	if err := store.EndUpload(ctx, uploadTenant, uploadA["session_id"].(string), owner, "succeeded", ""); err != nil { t.Fatal(err) }

	rateTenant, _ := newUUID()
	rateLimits := TenantLimits{MaxConcurrentUploads: 2, MaxStagedBytes: 1024, MaxIngestionsPerMinute: 1}
	rateFirst := makeSession(rateTenant, "quota-rate-first-0001", 1)
	if _, _, err := store.CreateSession(ctx, rateTenant, rateFirst["idempotency_key"].(string), rateFirst, rateLimits); err != nil {
		t.Fatalf("first ingestion should pass rate limit: %v", err)
	}
	rateSecond := makeSession(rateTenant, "quota-rate-second-0001", 1)
	if _, _, err := store.CreateSession(ctx, rateTenant, rateSecond["idempotency_key"].(string), rateSecond, rateLimits); err != ErrRateLimited {
		t.Fatalf("second ingestion must be rate limited, got %v", err)
	}
}

func TestPostgresStoreUploadLifecycle(t *testing.T) {
	dsn := os.Getenv("VOXPIPE_TEST_POSTGRES_APP_DSN")
	if dsn == "" { t.Skip("PostgreSQL application DSN is not configured") }
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	store, err := NewPostgresStore(ctx, dsn)
	if err != nil { t.Fatal(err) }
	defer store.Close()

	tenantID, err := newUUID(); if err != nil { t.Fatal(err) }
	sessionID, err := newUUID(); if err != nil { t.Fatal(err) }
	assetID, err := newUUID(); if err != nil { t.Fatal(err) }
	jobID, err := newUUID(); if err != nil { t.Fatal(err) }
	now := time.Now().UTC().Format(time.RFC3339Nano)
	session := map[string]any{
		"schema_version":"1.0.0",
		"session_id":sessionID,
		"tenant_id":tenantID,
		"mode":"batch",
		"source":map[string]any{"kind":"file_upload"},
		"state":"created",
		"idempotency_key":"go-store-integration-test-0001",
		"asset_id":assetID,
		"staging_reference":tenantID+"/"+sessionID+"/source",
		"upload":map[string]any{
			"expected_size_bytes":int64(12),
			"received_size_bytes":int64(12),
			"expected_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		},
		"created_at":now,
		"updated_at":now,
		"expires_at":time.Now().UTC().Add(time.Hour).Format(time.RFC3339Nano),
	}
	created, inserted, err := store.CreateSession(ctx,tenantID,session["idempotency_key"].(string),session,TenantLimits{})
	if err != nil { t.Fatal(err) }
	if !inserted || created["session_id"]!=sessionID { t.Fatalf("unexpected create result: %#v",created) }
	duplicate, wasInserted, err := store.CreateSession(ctx, tenantID, session["idempotency_key"].(string), session, TenantLimits{})
	if err != nil || wasInserted || duplicate["session_id"] != sessionID {
		t.Fatalf("idempotent create failed: inserted=%v duplicate=%#v err=%v", wasInserted, duplicate, err)
	}
	otherTenant, err := newUUID()
	if err != nil { t.Fatal(err) }
	if _, err := store.GetSession(ctx, otherTenant, sessionID); err != ErrNotFound {
		t.Fatalf("cross-tenant session lookup should be hidden, got %v", err)
	}
	receiving := cloneMap(created)
	receiving["state"]="receiving"
	receiving["updated_at"]=time.Now().UTC().Format(time.RFC3339Nano)
	if err:=store.UpdateSession(ctx,tenantID,sessionID,"created",receiving); err!=nil { t.Fatal(err) }
	ownerID, err := newUUID()
	if err != nil { t.Fatal(err) }
	if err := store.BeginUpload(ctx, tenantID, sessionID, ownerID, 30, 1); err != nil { t.Fatal(err) }
	if err := store.BeginUpload(ctx, tenantID, sessionID, "33333333-3333-4333-8333-333333333333", 30, 1); err != ErrConflict {
		t.Fatalf("concurrent upload lease should conflict, got %v", err)
	}
	finalizing:=cloneMap(receiving)
	finalizing["state"]="finalizing"
	finalizing["updated_at"]=time.Now().UTC().Format(time.RFC3339Nano)
	if _,err:=store.FinalizeSession(ctx,tenantID,sessionID,jobID,3,finalizing); err!=ErrConflict {
		t.Fatalf("finalization must be fenced by active upload lease, got %v",err)
	}
	if err := store.EndUpload(ctx, tenantID, sessionID, ownerID, "succeeded", ""); err != nil { t.Fatal(err) }
	job,err:=store.FinalizeSession(ctx,tenantID,sessionID,jobID,3,finalizing)
	if err!=nil { t.Fatal(err) }
	if job["state"]!="pending" { t.Fatalf("expected pending job: %#v",job) }
	if err := store.RecordAuditEvent(ctx, tenantID, sessionID, "", "test.event", "succeeded", "", map[string]any{"source":"test"}); err != nil { t.Fatal(err) }
	missingAssetID, err := newUUID()
	if err != nil { t.Fatal(err) }
	if _, err := store.GetAsset(ctx, tenantID, missingAssetID); err != ErrNotFound {
		t.Fatalf("missing asset lookup should be tenant-scoped not-found, got %v", err)
	}
	matches, err := store.SearchAssets(ctx, tenantID, "wav", 5)
	if err != nil || len(matches) != 0 {
		t.Fatalf("empty tenant metadata search should return no results: %#v %v", matches, err)
	}
	otherMatches, err := store.SearchAssets(ctx, otherTenant, "wav", 5)
	if err != nil || len(otherMatches) != 0 {
		t.Fatalf("cross-tenant metadata search should return no results: %#v %v", otherMatches, err)
	}
	status,storedJob,err:=store.GetStatus(ctx,tenantID,sessionID)
	if err!=nil { t.Fatal(err) }
	if status["state"]!="finalizing" || storedJob["job_id"]!=jobID { t.Fatalf("unexpected status: %#v %#v",status,storedJob) }
	if priorState,err:=store.Cancel(ctx,tenantID,sessionID); err!=nil || priorState!="finalizing" { t.Fatalf("cancel failed: prior_state=%q err=%v",priorState,err) }
	cancelled,err:=store.GetSession(ctx,tenantID,sessionID)
	if err!=nil || cancelled["state"]!="cancelled" { t.Fatalf("session was not cancelled: %#v %v",cancelled,err) }
}
