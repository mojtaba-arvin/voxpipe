package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	ErrNotFound = errors.New("resource not found")
	ErrConflict = errors.New("resource state conflict")
	ErrQuotaExceeded = errors.New("tenant quota exceeded")
	ErrRateLimited = errors.New("tenant ingestion rate limit exceeded")
)

type TranscriptSearchFilters struct {
	TranscriptID *string
	AssetID      *string
	Revision     *int
	Language     *string
	SpeakerID    *string
	StartMS      *int64
	EndMS        *int64
}

type Store interface {
	Ready(context.Context) error
	CreateSession(context.Context, string, string, map[string]any, TenantLimits) (map[string]any, bool, error)
	GetSession(context.Context, string, string) (map[string]any, error)
	UpdateSession(context.Context, string, string, string, map[string]any) error
	FinalizeSession(context.Context, string, string, string, int, map[string]any) (map[string]any, error)
	GetStatus(context.Context, string, string) (map[string]any, map[string]any, error)
	GetAsset(context.Context, string, string) (map[string]any, error)
	SearchAssets(context.Context, string, string, int) ([]map[string]any, error)
	SearchTranscriptSegments(context.Context, string, string, TranscriptSearchFilters, int, int) ([]map[string]any, error)
	GetOperationalSummary(context.Context, string, int) (map[string]any, error)
	BeginUpload(context.Context, string, string, string, int, int) error
	EndUpload(context.Context, string, string, string, string, string) error
	RecordAuditEvent(context.Context, string, string, string, string, string, string, map[string]any) error
	Cancel(context.Context, string, string) (string, error)
}

type PostgresStore struct { pool *pgxpool.Pool }

func NewPostgresStore(ctx context.Context, dsn string) (*PostgresStore, error) {
	if dsn == "" { return nil, fmt.Errorf("PostgreSQL DSN is required") }
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil { return nil, fmt.Errorf("create PostgreSQL pool: %w", err) }
	if err := pool.Ping(ctx); err != nil { pool.Close(); return nil, fmt.Errorf("connect to PostgreSQL: %w", err) }
	return &PostgresStore{pool: pool}, nil
}
func (s *PostgresStore) Close() { s.pool.Close() }

// Ready verifies PostgreSQL connectivity and the core schema required by the API.
// Optional capabilities such as hybrid retrieval are checked by their own endpoints.
func (s *PostgresStore) Ready(ctx context.Context) error {
	if err := s.pool.Ping(ctx); err != nil { return err }
	var schemaReady bool
	if err := s.pool.QueryRow(ctx, `
		SELECT to_regclass('public.ingestion_sessions') IS NOT NULL
		   AND to_regclass('public.ingestion_jobs') IS NOT NULL
	`).Scan(&schemaReady); err != nil { return err }
	if !schemaReady { return fmt.Errorf("required ingestion schema is not installed") }
	return nil
}

func (s *PostgresStore) beginTenant(ctx context.Context, tenantID string) (pgx.Tx, error) {
	if !isUUID(tenantID) { return nil, fmt.Errorf("tenant id must be a UUID") }
	tx, err := s.pool.Begin(ctx)
	if err != nil { return nil, err }
	if _, err := tx.Exec(ctx, "SELECT set_config('voxpipe.tenant_id', $1, true)", tenantID); err != nil {
		_ = tx.Rollback(ctx); return nil, err
	}
	return tx, nil
}
func decodePayload(payload []byte) (map[string]any, error) {
	var result map[string]any
	if err := json.Unmarshal(payload, &result); err != nil { return nil, err }
	return result, nil
}

func (s *PostgresStore) CreateSession(ctx context.Context, tenantID, key string, session map[string]any, limits TenantLimits) (map[string]any, bool, error) {
	tx, err := s.beginTenant(ctx, tenantID)
	if err != nil { return nil, false, err }
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", tenantID); err != nil { return nil, false, err }
	var result []byte
	err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1::text::uuid AND idempotency_key=$2", tenantID, key).Scan(&result)
	if err == nil {
		if err := tx.Commit(ctx); err != nil { return nil, false, err }
		value, decodeErr := decodePayload(result)
		return value, false, decodeErr
	}
	if !errors.Is(err, pgx.ErrNoRows) { return nil, false, err }
	upload, ok := session["upload"].(map[string]any)
	if !ok { return nil, false, ErrConflict }
	expectedBytes, ok := numberInt64(upload["expected_size_bytes"])
	if !ok || expectedBytes < 1 { return nil, false, ErrConflict }
	if limits.MaxIngestionsPerMinute > 0 {
		var recent int
		err = tx.QueryRow(ctx, "SELECT count(*) FROM ingestion_audit_events WHERE tenant_id=$1::text::uuid AND event_type='ingestion.created' AND occurred_at > now() - interval '1 minute'", tenantID).Scan(&recent)
		if err != nil { return nil, false, err }
		if recent >= limits.MaxIngestionsPerMinute { return nil, false, ErrRateLimited }
	}
	if limits.MaxStagedBytes > 0 {
		var reserved int64
		err = tx.QueryRow(ctx, "SELECT COALESCE(sum(CASE WHEN payload #>> '{upload,expected_size_bytes}' ~ '^[0-9]+$' THEN (payload #>> '{upload,expected_size_bytes}')::bigint ELSE 0 END),0) FROM ingestion_sessions WHERE tenant_id=$1::text::uuid AND payload ? 'staging_reference' AND NOT (payload ? 'staging_cleanup_completed_at')", tenantID).Scan(&reserved)
		if err != nil { return nil, false, err }
		if reserved > limits.MaxStagedBytes || expectedBytes > limits.MaxStagedBytes-reserved { return nil, false, ErrQuotaExceeded }
	}
	payload, err := json.Marshal(session)
	if err != nil { return nil, false, err }
	err = tx.QueryRow(ctx, "INSERT INTO ingestion_sessions (session_id, tenant_id, idempotency_key, state, payload, updated_at) VALUES ($1::text::uuid, $2::text::uuid, $3, $4, $5::jsonb, $6::text::timestamptz) ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING payload", session["session_id"], tenantID, key, session["state"], payload, session["updated_at"]).Scan(&result)
	if err == nil {
		if err := insertAuditEvent(ctx, tx, tenantID, session["session_id"].(string), "", "ingestion.created", "succeeded", "", nil); err != nil { return nil, false, err }
		if err := tx.Commit(ctx); err != nil { return nil, false, err }
		value, decodeErr := decodePayload(result)
		return value, true, decodeErr
	}
	if !errors.Is(err, pgx.ErrNoRows) { return nil, false, err }
	err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1::text::uuid AND idempotency_key=$2", tenantID, key).Scan(&result)
	if errors.Is(err, pgx.ErrNoRows) { return nil, false, ErrConflict }
	if err != nil { return nil, false, err }
	if err := tx.Commit(ctx); err != nil { return nil, false, err }
	value, decodeErr := decodePayload(result)
	return value, false, decodeErr
}

func (s *PostgresStore) GetSession(ctx context.Context, tenantID, sessionID string) (map[string]any, error) {
	tx, err := s.beginTenant(ctx, tenantID); if err != nil { return nil, err }; defer tx.Rollback(ctx)
	var payload []byte
	err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid", tenantID, sessionID).Scan(&payload)
	if errors.Is(err, pgx.ErrNoRows) { return nil, ErrNotFound }; if err != nil { return nil, err }
	if err := tx.Commit(ctx); err != nil { return nil, err }
	return decodePayload(payload)
}

func (s *PostgresStore) UpdateSession(ctx context.Context, tenantID, sessionID, expectedState string, updated map[string]any) error {
	if updated["tenant_id"] != tenantID { return ErrConflict }
	payload, err := json.Marshal(updated); if err != nil { return err }
	tx, err := s.beginTenant(ctx, tenantID); if err != nil { return err }; defer tx.Rollback(ctx)
	tag, err := tx.Exec(ctx, `
        UPDATE ingestion_sessions SET state=$1, payload=$2::jsonb, updated_at=$3::text::timestamptz
        WHERE tenant_id=$4::text::uuid AND session_id=$5::text::uuid AND state=$6
    `, updated["state"], payload, updated["updated_at"], tenantID, sessionID, expectedState)
	if err != nil { return err }; if tag.RowsAffected()!=1 { return ErrConflict }
	if expectedState != updated["state"] {
		if err := insertAuditEvent(ctx, tx, tenantID, sessionID, "", "ingestion.state_transition", "succeeded", "", map[string]any{"from": expectedState, "to": updated["state"]}); err != nil { return err }
	}
	return tx.Commit(ctx)
}

func (s *PostgresStore) BeginUpload(ctx context.Context, tenantID, sessionID, ownerID string, leaseSeconds, maxConcurrent int) error {
	if leaseSeconds < 1 || leaseSeconds > 31622400 { return fmt.Errorf("upload lease must be between 1 and 31622400 seconds") }
	tx, err := s.beginTenant(ctx, tenantID); if err != nil { return err }; defer tx.Rollback(ctx)
	var raw []byte
	err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid FOR UPDATE", tenantID, sessionID).Scan(&raw)
	if errors.Is(err, pgx.ErrNoRows) { return ErrNotFound }; if err != nil { return err }
	session, err := decodePayload(raw); if err != nil { return err }
	state, _ := session["state"].(string)
	if state != "created" && state != "receiving" { return ErrConflict }
	expiresAt, ok := session["expires_at"].(string)
	if !ok { return ErrConflict }
	expiry, parseErr := time.Parse(time.RFC3339Nano, expiresAt)
	if parseErr != nil || !expiry.After(time.Now().UTC()) { return ErrConflict }
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", tenantID); err != nil { return err }
	var sameSessionActive bool
	if err := tx.QueryRow(ctx, "SELECT EXISTS (SELECT 1 FROM ingestion_upload_leases WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid AND lease_expires_at > now())", tenantID, sessionID).Scan(&sameSessionActive); err != nil { return err }
	if sameSessionActive { return ErrConflict }
	if maxConcurrent > 0 {
		var active int
		if err := tx.QueryRow(ctx, "SELECT count(*) FROM ingestion_upload_leases WHERE tenant_id=$1::text::uuid AND lease_expires_at > now()", tenantID).Scan(&active); err != nil { return err }
		if active >= maxConcurrent { return ErrQuotaExceeded }
	}
	var claimed string
	err = tx.QueryRow(ctx, `
		INSERT INTO ingestion_upload_leases (tenant_id, session_id, owner_id, lease_expires_at, created_at, updated_at)
		VALUES ($1::text::uuid, $2::text::uuid, $3::text::uuid, now()+make_interval(secs=>$4), now(), now())
		ON CONFLICT (tenant_id, session_id) DO UPDATE
		SET owner_id=EXCLUDED.owner_id, lease_expires_at=EXCLUDED.lease_expires_at, updated_at=now()
		WHERE ingestion_upload_leases.lease_expires_at <= now()
		RETURNING owner_id::text
	`, tenantID, sessionID, ownerID, leaseSeconds).Scan(&claimed)
	if errors.Is(err, pgx.ErrNoRows) { return ErrConflict }; if err != nil { return err }
	if err := insertAuditEvent(ctx, tx, tenantID, sessionID, "", "upload.started", "started", "", nil); err != nil { return err }
	return tx.Commit(ctx)
}

func (s *PostgresStore) EndUpload(ctx context.Context, tenantID, sessionID, ownerID, outcome, errorCode string) error {
	tx, err := s.beginTenant(ctx, tenantID); if err != nil { return err }; defer tx.Rollback(ctx)
	_, err = tx.Exec(ctx, "DELETE FROM ingestion_upload_leases WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid AND owner_id=$3::text::uuid", tenantID, sessionID, ownerID)
	if err != nil { return err }
	if outcome == "failed" && errorCode == "" { errorCode = "UPLOAD_FAILED" }
	if err := insertAuditEvent(ctx, tx, tenantID, sessionID, "", "upload.finished", outcome, errorCode, nil); err != nil { return err }
	return tx.Commit(ctx)
}

func (s *PostgresStore) RecordAuditEvent(ctx context.Context, tenantID, sessionID, assetID, eventType, outcome, errorCode string, metadata map[string]any) error {
	tx, err := s.beginTenant(ctx, tenantID); if err != nil { return err }; defer tx.Rollback(ctx)
	if err := insertAuditEvent(ctx, tx, tenantID, sessionID, assetID, eventType, outcome, errorCode, metadata); err != nil { return err }
	return tx.Commit(ctx)
}

func sanitizeAuditMetadata(metadata map[string]any) map[string]any {
	safe := make(map[string]any)
	for key, value := range metadata {
		switch key {
		case "from", "to", "prior_state", "retention_expires_at":
			if text, ok := value.(string); ok && len(text) <= 64 {
				safe[key] = text
			}
		case "staging_deleted", "asset_deleted", "object_deleted":
			if flag, ok := value.(bool); ok {
				safe[key] = flag
			}
		}
	}
	return safe
}

func sanitizeAuditErrorCode(value string) string {
	if value == "" { return "" }
	if len(value) > 64 { return "AUDIT_ERROR_REDACTED" }
	for _, char := range value {
		if !((char >= 'A' && char <= 'Z') || (char >= '0' && char <= '9') || char == '_') {
			return "AUDIT_ERROR_REDACTED"
		}
	}
	return value
}

func insertAuditEvent(ctx context.Context, tx pgx.Tx, tenantID, sessionID, assetID, eventType, outcome, errorCode string, metadata map[string]any) error {
	payload, err := json.Marshal(sanitizeAuditMetadata(metadata)); if err != nil { return err }
	errorCode = sanitizeAuditErrorCode(errorCode)
	eventID, err := newUUID(); if err != nil { return err }
	_, err = tx.Exec(ctx, `
		INSERT INTO ingestion_audit_events (event_id, tenant_id, session_id, asset_id, event_type, outcome, error_code, metadata, occurred_at)
		VALUES ($1::text::uuid, $2::text::uuid, nullif($3,'')::uuid, nullif($4,'')::uuid, $5, $6, nullif($7,''), $8::jsonb, now())
	`, eventID, tenantID, sessionID, assetID, eventType, outcome, errorCode, payload)
	return err
}

func (s *PostgresStore) FinalizeSession(ctx context.Context, tenantID, sessionID, jobID string, maxAttempts int, updated map[string]any) (map[string]any, error) {
	if maxAttempts<1 || maxAttempts>100 { return nil, fmt.Errorf("max_attempts must be between 1 and 100") }
	tx, err := s.beginTenant(ctx, tenantID); if err != nil { return nil, err }; defer tx.Rollback(ctx)
	var raw []byte
	err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid FOR UPDATE", tenantID, sessionID).Scan(&raw)
	if errors.Is(err, pgx.ErrNoRows) { return nil, ErrNotFound }; if err != nil { return nil, err }
	current, err := decodePayload(raw); if err != nil { return nil, err }
	state, _ := current["state"].(string)
	var uploadActive bool
	if err := tx.QueryRow(ctx, "SELECT EXISTS (SELECT 1 FROM ingestion_upload_leases WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid AND lease_expires_at > now())", tenantID, sessionID).Scan(&uploadActive); err != nil { return nil, err }
	if uploadActive { return nil, ErrConflict }
	if state!="receiving" {
		if state=="finalizing" || state=="queued" || state=="processing" || state=="completed" {
			var jobJSON []byte
			err := tx.QueryRow(ctx, `
                SELECT jsonb_build_object('job_id',job_id,'state',state,'attempts',attempts,'max_attempts',max_attempts,'last_error_code',last_error_code)
                FROM ingestion_jobs WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid
            `, tenantID, sessionID).Scan(&jobJSON)
			if errors.Is(err, pgx.ErrNoRows) { return nil, ErrConflict }; if err != nil { return nil, err }
			var job map[string]any; if err := json.Unmarshal(jobJSON,&job); err != nil { return nil, err }
			if err := tx.Commit(ctx); err != nil { return nil, err }; return job,nil
		}
		return nil, ErrConflict
	}
	payload, err := json.Marshal(updated); if err != nil { return nil, err }
	tag, err := tx.Exec(ctx, "UPDATE ingestion_sessions SET state='finalizing',payload=$1::jsonb,updated_at=$2::text::timestamptz WHERE tenant_id=$3::text::uuid AND session_id=$4::text::uuid AND state='receiving'", payload, updated["updated_at"], tenantID, sessionID)
	if err != nil { return nil, err }; if tag.RowsAffected()!=1 { return nil, ErrConflict }
	var jobJSON []byte
	err = tx.QueryRow(ctx, `
        INSERT INTO ingestion_jobs (job_id,tenant_id,session_id,state,max_attempts,available_at,created_at,updated_at)
        VALUES ($1::text::uuid,$2::text::uuid,$3::text::uuid,'pending',$4,now(),now(),now())
        ON CONFLICT (tenant_id,session_id) DO NOTHING
        RETURNING jsonb_build_object('job_id',job_id,'state',state,'attempts',attempts,'max_attempts',max_attempts,'last_error_code',last_error_code)
    `, jobID, tenantID, sessionID, maxAttempts).Scan(&jobJSON)
	if errors.Is(err, pgx.ErrNoRows) {
		err = tx.QueryRow(ctx, `
            SELECT jsonb_build_object('job_id',job_id,'state',state,'attempts',attempts,'max_attempts',max_attempts,'last_error_code',last_error_code)
            FROM ingestion_jobs WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid
        `, tenantID, sessionID).Scan(&jobJSON)
	}
	if err != nil { return nil, err }
	var job map[string]any; if err := json.Unmarshal(jobJSON,&job); err != nil { return nil, err }
	if err := insertAuditEvent(ctx, tx, tenantID, sessionID, "", "ingestion.queued", "succeeded", "", nil); err != nil { return nil, err }
	if err := tx.Commit(ctx); err != nil { return nil, err }; return job,nil
}

func (s *PostgresStore) GetStatus(ctx context.Context, tenantID, sessionID string) (map[string]any, map[string]any, error) {
	tx, err := s.beginTenant(ctx, tenantID); if err != nil { return nil,nil,err }; defer tx.Rollback(ctx)
	var raw []byte
	err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid", tenantID, sessionID).Scan(&raw)
	if errors.Is(err,pgx.ErrNoRows) { return nil,nil,ErrNotFound }; if err != nil { return nil,nil,err }
	var jobRaw []byte
	err = tx.QueryRow(ctx, `
        SELECT jsonb_build_object('job_id',job_id,'state',state,'attempts',attempts,'max_attempts',max_attempts,'last_error_code',last_error_code)
        FROM ingestion_jobs WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid
    `, tenantID, sessionID).Scan(&jobRaw)
	var job map[string]any
	if err==nil { if err:=json.Unmarshal(jobRaw,&job); err!=nil { return nil,nil,err } } else if !errors.Is(err,pgx.ErrNoRows) { return nil,nil,err }
	if err:=tx.Commit(ctx); err!=nil { return nil,nil,err }
	session,err:=decodePayload(raw); return session,job,err
}

func (s *PostgresStore) GetAsset(ctx context.Context, tenantID, assetID string) (map[string]any, error) {
	tx, err := s.beginTenant(ctx, tenantID)
	if err != nil { return nil, err }
	defer tx.Rollback(ctx)
	var payload []byte
	err = tx.QueryRow(ctx, "SELECT payload FROM audio_assets WHERE tenant_id=$1::text::uuid AND asset_id=$2::text::uuid", tenantID, assetID).Scan(&payload)
	if errors.Is(err, pgx.ErrNoRows) { return nil, ErrNotFound }
	if err != nil { return nil, err }
	if err := tx.Commit(ctx); err != nil { return nil, err }
	return decodePayload(payload)
}

func (s *PostgresStore) SearchAssets(ctx context.Context, tenantID, query string, limit int) ([]map[string]any, error) {
	if limit < 1 || limit > 50 { return nil, fmt.Errorf("asset search limit must be between 1 and 50") }
	tx, err := s.beginTenant(ctx, tenantID)
	if err != nil { return nil, err }
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `
		SELECT payload FROM audio_assets
		WHERE tenant_id=$1::text::uuid AND (
			strpos(lower(asset_id::text), lower($2)) > 0
			OR strpos(lower(payload #>> '{source,kind}'), lower($2)) > 0
			OR strpos(lower(payload #>> '{media,mime_type}'), lower($2)) > 0
			OR strpos(lower(payload #>> '{media,container_format}'), lower($2)) > 0
			OR strpos(lower(payload #>> '{media,codec}'), lower($2)) > 0
		)
		ORDER BY updated_at DESC, asset_id ASC
		LIMIT $3
	`, tenantID, query, limit)
	if err != nil { return nil, err }
	defer rows.Close()
	result := make([]map[string]any, 0, limit)
	for rows.Next() {
		var payload []byte
		if err := rows.Scan(&payload); err != nil { return nil, err }
		value, err := decodePayload(payload)
		if err != nil { return nil, err }
		result = append(result, value)
	}
	if err := rows.Err(); err != nil { return nil, err }
	if err := tx.Commit(ctx); err != nil { return nil, err }
	return result, nil
}


func (s *PostgresStore) SearchTranscriptSegments(
	ctx context.Context,
	tenantID, query string,
	filters TranscriptSearchFilters,
	limit, offset int,
) ([]map[string]any, error) {
	query = normalizePersianSearchQuery(query)
	if len([]rune(query)) < 1 || len([]rune(query)) > 256 {
		return nil, fmt.Errorf("transcript search query must contain 1..256 characters")
	}
	if limit < 1 || limit > 100 || offset < 0 || offset > 10000 {
		return nil, fmt.Errorf("transcript search pagination is outside the allowed bounds")
	}
	for _, value := range []*string{filters.TranscriptID, filters.AssetID, filters.SpeakerID} {
		if value != nil && !isUUID(*value) {
			return nil, fmt.Errorf("transcript search UUID filter is invalid")
		}
	}
	if filters.Revision != nil && *filters.Revision < 1 {
		return nil, fmt.Errorf("transcript revision filter must be positive")
	}
	if filters.Language != nil && (len(*filters.Language) < 2 || len(*filters.Language) > 35) {
		return nil, fmt.Errorf("transcript language filter is invalid")
	}
	if filters.StartMS != nil && *filters.StartMS < 0 {
		return nil, fmt.Errorf("transcript start_ms filter is invalid")
	}
	if filters.EndMS != nil && *filters.EndMS < 1 {
		return nil, fmt.Errorf("transcript end_ms filter is invalid")
	}
	if filters.StartMS != nil && filters.EndMS != nil && *filters.EndMS <= *filters.StartMS {
		return nil, fmt.Errorf("transcript time range is invalid")
	}

	tx, err := s.beginTenant(ctx, tenantID)
	if err != nil { return nil, err }
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `
		WITH search_query AS (
			SELECT websearch_to_tsquery('simple', $2) AS value
		)
		SELECT
			t.transcript_id, t.revision, t.asset_id, t.language,
			t.provider, t.model, t.model_version,
			seg.segment_id, seg.start_ms, seg.end_ms, seg.text, seg.speaker_id,
			ts_rank(seg.search_vector, q.value)::double precision AS rank
		FROM transcript_segments AS seg
		JOIN transcripts AS t
		  ON t.tenant_id = seg.tenant_id
		 AND t.transcript_id = seg.transcript_id
		 AND t.revision = seg.revision
		CROSS JOIN search_query AS q
		WHERE seg.tenant_id = $1::uuid
		  AND seg.search_vector @@ q.value
		  AND ($3::uuid IS NULL OR t.transcript_id = $3::uuid)
		  AND ($4::uuid IS NULL OR t.asset_id = $4::uuid)
		  AND ($5::integer IS NULL OR t.revision = $5::integer)
		  AND ($6::text IS NULL OR t.language = $6::text)
		  AND ($7::uuid IS NULL OR seg.speaker_id = $7::uuid)
		  AND ($8::bigint IS NULL OR seg.end_ms >= $8::bigint)
		  AND ($9::bigint IS NULL OR seg.start_ms < $9::bigint)
		ORDER BY rank DESC, seg.start_ms ASC, seg.segment_id ASC
		LIMIT $10 OFFSET $11
	`, tenantID, query, filters.TranscriptID, filters.AssetID, filters.Revision,
		filters.Language, filters.SpeakerID, filters.StartMS, filters.EndMS, limit, offset)
	if err != nil { return nil, err }
	defer rows.Close()
	result := make([]map[string]any, 0, limit)
	for rows.Next() {
		var transcriptID, assetID, language, provider, model, segmentID, text string
		var modelVersion, speakerID *string
		var revision int
		var startMS, endMS int64
		var rank float64
		if err := rows.Scan(&transcriptID, &revision, &assetID, &language, &provider,
			&model, &modelVersion, &segmentID, &startMS, &endMS, &text, &speakerID, &rank); err != nil {
			return nil, err
		}
		item := map[string]any{
			"transcript_id": transcriptID, "revision": revision, "asset_id": assetID,
			"language": language, "provider": provider, "model": model,
			"segment_id": segmentID, "start_ms": startMS, "end_ms": endMS,
			"text": text, "rank": rank,
		}
		if modelVersion != nil { item["model_version"] = *modelVersion }
		if speakerID != nil { item["speaker_id"] = *speakerID }
		result = append(result, item)
	}
	if err := rows.Err(); err != nil { return nil, err }
	if err := tx.Commit(ctx); err != nil { return nil, err }
	return result, nil
}

func (s *PostgresStore) Cancel(ctx context.Context, tenantID, sessionID string) (string,error) {
	tx,err:=s.beginTenant(ctx,tenantID); if err!=nil { return "",err }; defer tx.Rollback(ctx)
	var raw []byte
	err=tx.QueryRow(ctx,"SELECT payload FROM ingestion_sessions WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid FOR UPDATE",tenantID,sessionID).Scan(&raw)
	if errors.Is(err,pgx.ErrNoRows) { return "",ErrNotFound }; if err!=nil { return "",err }
	session,err:=decodePayload(raw); if err!=nil { return "",err }
	state,_:=session["state"].(string)
	switch state { case "completed","failed","cancelled","expired": return "",ErrConflict }
	session["state"]="cancelled"; delete(session,"lease")
	session["updated_at"]=time.Now().UTC().Format("2006-01-02T15:04:05.000Z")
	payload,err:=json.Marshal(session); if err!=nil { return "",err }
	_,err=tx.Exec(ctx,"UPDATE ingestion_sessions SET state='cancelled',payload=$1::jsonb,updated_at=now() WHERE tenant_id=$2::text::uuid AND session_id=$3::text::uuid",payload,tenantID,sessionID)
	if err!=nil { return "",err }
	_,err=tx.Exec(ctx,"UPDATE ingestion_jobs SET state='cancelled',lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE tenant_id=$1::text::uuid AND session_id=$2::text::uuid AND state IN ('pending','leased')",tenantID,sessionID)
	if err!=nil { return "",err }
	if err := insertAuditEvent(ctx, tx, tenantID, sessionID, "", "ingestion.cancelled", "succeeded", "", nil); err != nil { return "", err }
	if err:=tx.Commit(ctx); err!=nil { return "",err }; return state,nil
}


// GetOperationalSummary returns bounded tenant-scoped aggregates only. It never
// selects payloads, transcript text, storage references, tokens or raw errors.
func (s *PostgresStore) GetOperationalSummary(ctx context.Context, tenantID string, windowHours int) (map[string]any, error) {
	if !isUUID(tenantID) {
		return nil, fmt.Errorf("tenant id must be a UUID")
	}
	if windowHours < 1 || windowHours > 168 {
		return nil, fmt.Errorf("operations summary window must be between 1 and 168 hours")
	}
	tx, err := s.beginTenant(ctx, tenantID)
	if err != nil { return nil, err }
	defer tx.Rollback(ctx)

	var observedAt time.Time
	var ingestionTotal, ingestionCreated, ingestionReceiving, ingestionFinalizing, ingestionQueued, ingestionProcessing, ingestionCompleted, ingestionFailed, ingestionCancelled, ingestionExpired int64
	var jobsTotal, jobsPending, jobsLeased, jobsCompleted, jobsDead, jobsCancelled, retryCount, processingFailures int64
	var completedAverage, pendingDepthAge float64
	var queuePending, activeLeases, expiredLeases, deadLetters int64
	err = tx.QueryRow(ctx, "WITH session_window AS ("+
		" SELECT count(*) AS total, count(*) FILTER (WHERE state='created') AS created,"+
		" count(*) FILTER (WHERE state='receiving') AS receiving, count(*) FILTER (WHERE state='finalizing') AS finalizing,"+
		" count(*) FILTER (WHERE state='queued') AS queued, count(*) FILTER (WHERE state='processing') AS processing,"+
		" count(*) FILTER (WHERE state='completed') AS completed, count(*) FILTER (WHERE state='failed') AS failed,"+
		" count(*) FILTER (WHERE state='cancelled') AS cancelled, count(*) FILTER (WHERE state='expired') AS expired"+
		" FROM ingestion_sessions WHERE tenant_id=$1::uuid AND updated_at >= now()-make_interval(hours => $2)"+
		"), job_window AS ("+
		" SELECT count(*) AS total, count(*) FILTER (WHERE state='pending') AS pending,"+
		" count(*) FILTER (WHERE state='leased') AS leased, count(*) FILTER (WHERE state='completed') AS completed,"+
		" count(*) FILTER (WHERE state='dead') AS dead, count(*) FILTER (WHERE state='cancelled') AS cancelled,"+
		" COALESCE(sum(GREATEST(attempts-1,0)),0) AS retry_count,"+
		" count(*) FILTER (WHERE last_error_code IS NOT NULL AND updated_at >= now()-make_interval(hours => $2)) AS processing_failures,"+
		" COALESCE(avg(extract(epoch FROM (updated_at-started_at))) FILTER (WHERE state='completed' AND started_at IS NOT NULL),0) AS completed_average"+
		" FROM ingestion_jobs WHERE tenant_id=$1::uuid AND created_at >= now()-make_interval(hours => $2)"+
		"), queue_now AS ("+
		" SELECT"+
		" (SELECT count(*) FROM ingestion_jobs WHERE tenant_id=$1::uuid AND state='pending') AS pending,"+
		" COALESCE((SELECT extract(epoch FROM (now()-min(created_at))) FROM ingestion_jobs WHERE tenant_id=$1::uuid AND state='pending'),0) AS pending_age,"+
		" (SELECT count(*) FROM ingestion_jobs WHERE tenant_id=$1::uuid AND state='leased' AND lease_expires_at>now()) AS active_leases,"+
		" (SELECT count(*) FROM ingestion_jobs WHERE tenant_id=$1::uuid AND state='leased' AND lease_expires_at<=now()) AS expired_leases,"+
		" (SELECT count(*) FROM ingestion_jobs WHERE tenant_id=$1::uuid AND state='dead') AS dead_letters"+
		") SELECT now(),s.total,s.created,s.receiving,s.finalizing,s.queued,s.processing,s.completed,s.failed,s.cancelled,s.expired,"+
		"j.total,j.pending,j.leased,j.completed,j.dead,j.cancelled,j.retry_count,j.processing_failures,j.completed_average,"+
		"q.pending,q.pending_age,q.active_leases,q.expired_leases,q.dead_letters"+
		" FROM session_window s CROSS JOIN job_window j CROSS JOIN queue_now q",
		tenantID, windowHours).Scan(
		&observedAt,
		&ingestionTotal,&ingestionCreated,&ingestionReceiving,&ingestionFinalizing,&ingestionQueued,&ingestionProcessing,&ingestionCompleted,&ingestionFailed,&ingestionCancelled,&ingestionExpired,
		&jobsTotal,&jobsPending,&jobsLeased,&jobsCompleted,&jobsDead,&jobsCancelled,&retryCount,&processingFailures,&completedAverage,
		&queuePending,&pendingDepthAge,&activeLeases,&expiredLeases,&deadLetters,
	)
	if err != nil { return nil, err }
	if err := tx.Commit(ctx); err != nil { return nil, err }
	return map[string]any{
		"window_hours": windowHours,
		"observed_at": observedAt.UTC(),
		"ingestions": map[string]any{
			"total": ingestionTotal,
			"by_state": map[string]int64{
				"created":ingestionCreated,"receiving":ingestionReceiving,"finalizing":ingestionFinalizing,
				"queued":ingestionQueued,"processing":ingestionProcessing,"completed":ingestionCompleted,
				"failed":ingestionFailed,"cancelled":ingestionCancelled,"expired":ingestionExpired,
			},
		},
		"jobs": map[string]any{
			"total":jobsTotal,
			"by_state":map[string]int64{
				"pending":jobsPending,"leased":jobsLeased,"completed":jobsCompleted,"dead":jobsDead,"cancelled":jobsCancelled,
			},
			"retry_count":retryCount,
			"processing_failure_count":processingFailures,
			"completed_processing_seconds_avg":roundMetric(completedAverage),
		},
		"queue":map[string]any{
			"pending_depth":queuePending,
			"oldest_pending_age_seconds":roundMetric(pendingDepthAge),
			"active_leases":activeLeases,
			"expired_leases":expiredLeases,
			"dead_letter_count":deadLetters,
		},
	}, nil
}

func roundMetric(value float64) float64 {
	return math.Round(value*1000) / 1000
}
