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


func TestPostgresStoreSearchesTranscriptSegmentsWithinTenant(t *testing.T) {
	dsn := os.Getenv("VOXPIPE_TEST_POSTGRES_APP_DSN")
	if dsn == "" { t.Skip("PostgreSQL application DSN is not configured") }
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	store, err := NewPostgresStore(ctx, dsn)
	if err != nil { t.Fatal(err) }
	defer store.Close()

	tenantID, _ := newUUID()
	otherTenant, _ := newUUID()
	assetID, _ := newUUID()
	transcriptID, _ := newUUID()
	segmentID, _ := newUUID()
	speakerID, _ := newUUID()
	now := time.Now().UTC()
	tx, err := store.beginTenant(ctx, tenantID)
	if err != nil { t.Fatal(err) }
	_, err = tx.Exec(ctx, "INSERT INTO audio_assets (asset_id, tenant_id, payload, updated_at) VALUES ($1,$2,$3::jsonb,$4)",
		assetID, tenantID, `{"asset_id":"`+assetID+`","source":{"kind":"test"}}`, now)
	if err != nil { t.Fatal(err) }
	_, err = tx.Exec(ctx, `INSERT INTO transcripts
		(tenant_id, transcript_id, asset_id, revision, language, text, provider, model, audio_sha256, generated_at)
		VALUES ($1,$2,$3,1,'fa','جلسه فروش فردا','faster-whisper','small',$4,$5)`,
		tenantID, transcriptID, assetID, strings.Repeat("a", 64), now)
	if err != nil { t.Fatal(err) }
	_, err = tx.Exec(ctx, "INSERT INTO transcript_speakers (tenant_id,transcript_id,revision,speaker_id,label) VALUES ($1,$2,1,$3,'speaker_1')",
		tenantID, transcriptID, speakerID)
	if err != nil { t.Fatal(err) }
	_, err = tx.Exec(ctx, `INSERT INTO transcript_segments
		(tenant_id,transcript_id,revision,segment_id,start_ms,end_ms,text,speaker_id)
		VALUES ($1,$2,1,$3,1200,2400,'جلسه فروش فردا',$4)`,
		tenantID, transcriptID, segmentID, speakerID)
	if err != nil { t.Fatal(err) }
	if err := tx.Commit(ctx); err != nil { t.Fatal(err) }

	items, err := store.SearchTranscriptSegments(ctx, tenantID, "جلسه فروش", TranscriptSearchFilters{
		AssetID: &assetID, Language: stringPointer("fa"), SpeakerID: &speakerID,
		StartMS: int64Pointer(1000), EndMS: int64Pointer(3000),
	}, 10, 0)
	if err != nil { t.Fatal(err) }
	if len(items) != 1 || items[0]["transcript_id"] != transcriptID || items[0]["text"] != "جلسه فروش فردا" {
		t.Fatalf("unexpected transcript search result: %#v", items)
	}
	crossTenant, err := store.SearchTranscriptSegments(ctx, otherTenant, "جلسه فروش", TranscriptSearchFilters{}, 10, 0)
	if err != nil || len(crossTenant) != 0 {
		t.Fatalf("cross-tenant search must return no rows: %#v %v", crossTenant, err)
	}
	filtered, err := store.SearchTranscriptSegments(ctx, tenantID, "جلسه فروش", TranscriptSearchFilters{Revision: intPointer(2)}, 10, 0)
	if err != nil || len(filtered) != 0 {
		t.Fatalf("revision filter should exclude other revisions: %#v %v", filtered, err)
	}
}

func stringPointer(value string) *string { return &value }
func intPointer(value int) *int { return &value }
func int64Pointer(value int64) *int64 { return &value }

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


func TestPostgresStoreOperationalSummaryIsTenantScopedAndAggregateOnly(t *testing.T) {
	dsn := os.Getenv("VOXPIPE_TEST_POSTGRES_APP_DSN")
	if dsn == "" { t.Skip("PostgreSQL application DSN is not configured") }
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	store, err := NewPostgresStore(ctx, dsn)
	if err != nil { t.Fatal(err) }
	defer store.Close()
	tenant, err := newUUID(); if err != nil { t.Fatal(err) }
	otherTenant, err := newUUID(); if err != nil { t.Fatal(err) }

	insert := func(tenantID, state string, attempts int, lastError string, lease bool, started bool) {
		t.Helper()
		sessionID, err := newUUID(); if err != nil { t.Fatal(err) }
		jobID, err := newUUID(); if err != nil { t.Fatal(err) }
		tx, err := store.beginTenant(ctx, tenantID); if err != nil { t.Fatal(err) }
		defer tx.Rollback(ctx)
		_, err = tx.Exec(ctx, "INSERT INTO ingestion_sessions (session_id,tenant_id,idempotency_key,state,payload,updated_at) VALUES ($1::uuid,$2::uuid,$3,$4,'{}'::jsonb,now()-interval '1 hour')",
			sessionID,tenantID,"ops-summary-"+jobID,"queued")
		if err != nil { t.Fatal(err) }
		var leaseOwner any
		var leaseExpiry any
		if lease {
			owner, err := newUUID(); if err != nil { t.Fatal(err) }
			leaseOwner, leaseExpiry = owner, time.Now().UTC().Add(time.Hour)
		}
		var startedAt any
		if started { startedAt = time.Now().UTC().Add(-10*time.Minute) }
		_, err = tx.Exec(ctx, "INSERT INTO ingestion_jobs (job_id,tenant_id,session_id,state,attempts,max_attempts,lease_owner,lease_expires_at,last_error_code,started_at,created_at,updated_at) VALUES ($1::uuid,$2::uuid,$3::uuid,$4,$5,5,$6,$7,$8,$9,now()-interval '2 hours',now()-interval '5 minutes')",
			jobID,tenantID,sessionID,state,attempts,leaseOwner,leaseExpiry,nullIfEmpty(lastError),startedAt)
		if err != nil { t.Fatal(err) }
		if err := tx.Commit(ctx); err != nil { t.Fatal(err) }
	}
	insert(tenant,"pending",0,"",false,false)
	insert(tenant,"leased",1,"",true,true)
	insert(tenant,"completed",2,"",false,true)
	insert(tenant,"dead",3,"STT_FAILED",false,true)
	insert(otherTenant,"pending",0,"",false,false)

	insertWorker := func(tenantID, state, heartbeatAge, jobAge string) string {
		t.Helper()
		workerID, err := newUUID(); if err != nil { t.Fatal(err) }
		tx, err := store.beginTenant(ctx, tenantID); if err != nil { t.Fatal(err) }
		defer tx.Rollback(ctx)
		statement := "INSERT INTO ingestion_worker_heartbeats (tenant_id,worker_id,state,started_at,heartbeat_at,last_job_started_at,stopped_at,updated_at) VALUES ($1::uuid,$2::uuid,$3,now()-interval '1 hour',now()-$4::interval,CASE WHEN $5::boolean THEN now()-$6::interval ELSE NULL END,CASE WHEN $3='stopped' THEN now() ELSE NULL END,now())"
		_, err = tx.Exec(ctx, statement, tenantID, workerID, state, heartbeatAge, jobAge != "", jobAge)
		if err != nil { t.Fatal(err) }
		if err := tx.Commit(ctx); err != nil { t.Fatal(err) }
		return workerID
	}
	workerIdleID := insertWorker(tenant,"idle","0 seconds","")
	workerBusyID := insertWorker(tenant,"busy","0 seconds","15 seconds")
	workerStaleBusyID := insertWorker(tenant,"busy","2 minutes","3 minutes")
	insertWorker(tenant,"stopped","0 seconds","")
	insertWorker(otherTenant,"idle","0 seconds","")

	summary, err := store.GetOperationalSummary(ctx,tenant,24)
	if err != nil { t.Fatal(err) }
	ingestions := summary["ingestions"].(map[string]any)
	jobs := summary["jobs"].(map[string]any)
	queue := summary["queue"].(map[string]any)
	workers := summary["workers"].(map[string]any)
	if ingestions["total"] != int64(4) || jobs["total"] != int64(4) {
		t.Fatalf("summary must include only four rows from the authenticated tenant: %#v",summary)
	}
	if jobs["retry_count"] != int64(3) || jobs["processing_failure_count"] != int64(1) {
		t.Fatalf("retry/failure aggregates are incorrect: %#v",jobs)
	}
	if queue["pending_depth"] != int64(1) || queue["active_leases"] != int64(1) || queue["dead_letter_count"] != int64(1) {
		t.Fatalf("queue aggregates are incorrect: %#v",queue)
	}
	if workers["active"] != int64(2) || workers["stale"] != int64(1) ||
		workers["idle"] != int64(1) || workers["busy"] != int64(1) || workers["stale_busy"] != int64(1) {
		t.Fatalf("worker aggregates are incorrect: %#v", workers)
	}
	if workers["oldest_busy_seconds"].(float64) < 170 {
		t.Fatalf("busy duration should reveal stale processing: %#v", workers)
	}
	if strings.Contains(fmt.Sprintf("%#v", summary), workerIdleID) ||
		strings.Contains(fmt.Sprintf("%#v", summary), workerBusyID) ||
		strings.Contains(fmt.Sprintf("%#v", summary), workerStaleBusyID) {
		t.Fatal("worker identifiers must not be returned in aggregate summary")
	}
	if _, exists := jobs["last_error_code"]; exists { t.Fatal("raw error codes must not be returned") }
	if _, exists := summary["tenant_id"]; exists { t.Fatal("tenant identity must not be echoed") }
	other, err := store.GetOperationalSummary(ctx,otherTenant,24)
	if err != nil { t.Fatal(err) }
	if other["jobs"].(map[string]any)["total"] != int64(1) {
		t.Fatalf("cross-tenant aggregate leaked rows: %#v",other)
	}
	if other["workers"].(map[string]any)["active"] != int64(1) {
		t.Fatalf("worker aggregates crossed tenant boundaries: %#v", other["workers"])
	}
	if _, err := store.GetOperationalSummary(ctx,tenant,169); err == nil {
		t.Fatal("summary window above 168 hours must be rejected")
	}
}

func nullIfEmpty(value string) any {
	if value == "" { return nil }
	return value
}

func TestPostgresStoreReadinessChecksCoreSchema(t *testing.T) {
	dsn := os.Getenv("VOXPIPE_TEST_POSTGRES_APP_DSN")
	if dsn == "" { t.Skip("PostgreSQL application DSN is not configured") }
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	store, err := NewPostgresStore(ctx, dsn)
	if err != nil { t.Fatal(err) }
	defer store.Close()
	if err := store.Ready(ctx); err != nil { t.Fatalf("migrated PostgreSQL application role should be ready: %v", err) }
}
