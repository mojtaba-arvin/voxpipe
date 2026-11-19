package httpapi

import (
	"context"
	"os"
	"testing"
	"time"
)

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
	created, inserted, err := store.CreateSession(ctx,tenantID,session["idempotency_key"].(string),session)
	if err != nil { t.Fatal(err) }
	if !inserted || created["session_id"]!=sessionID { t.Fatalf("unexpected create result: %#v",created) }
	duplicate, wasInserted, err := store.CreateSession(ctx, tenantID, session["idempotency_key"].(string), session)
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
	if err := store.BeginUpload(ctx, tenantID, sessionID, ownerID, 30); err != nil { t.Fatal(err) }
	if err := store.BeginUpload(ctx, tenantID, sessionID, "33333333-3333-4333-8333-333333333333", 30); err != ErrConflict {
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
