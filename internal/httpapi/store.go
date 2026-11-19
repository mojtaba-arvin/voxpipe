package httpapi

import (
    "context"
    "encoding/json"
    "errors"
    "fmt"
    "time"

    "github.com/jackc/pgx/v5"
    "github.com/jackc/pgx/v5/pgxpool"
)

var (
    ErrNotFound = errors.New("resource not found")
    ErrConflict = errors.New("resource state conflict")
)

type Store interface {
    CreateSession(context.Context, string, string, map[string]any) (map[string]any, bool, error)
    GetSession(context.Context, string, string) (map[string]any, error)
    UpdateSession(context.Context, string, string, string, map[string]any) error
    FinalizeSession(context.Context, string, string, string, int, map[string]any) (map[string]any, error)
    GetStatus(context.Context, string, string) (map[string]any, map[string]any, error)
    Cancel(context.Context, string, string) (bool, error)
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

func (s *PostgresStore) CreateSession(ctx context.Context, tenantID, key string, session map[string]any) (map[string]any, bool, error) {
    tx, err := s.beginTenant(ctx, tenantID); if err != nil { return nil, false, err }; defer tx.Rollback(ctx)
    payload, err := json.Marshal(session); if err != nil { return nil, false, err }
    var result []byte
    err = tx.QueryRow(ctx, `
        INSERT INTO ingestion_sessions (session_id, tenant_id, idempotency_key, state, payload, updated_at)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb, $6::timestamptz)
        ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING payload
    `, session["session_id"], tenantID, key, session["state"], payload, session["updated_at"]).Scan(&result)
    if err == nil {
        if err := tx.Commit(ctx); err != nil { return nil, false, err }
        value, err := decodePayload(result); return value, true, err
    }
    if !errors.Is(err, pgx.ErrNoRows) { return nil, false, err }
    err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1 AND idempotency_key=$2", tenantID, key).Scan(&result)
    if errors.Is(err, pgx.ErrNoRows) { return nil, false, ErrConflict }
    if err != nil { return nil, false, err }
    if err := tx.Commit(ctx); err != nil { return nil, false, err }
    value, err := decodePayload(result); return value, false, err
}

func (s *PostgresStore) GetSession(ctx context.Context, tenantID, sessionID string) (map[string]any, error) {
    tx, err := s.beginTenant(ctx, tenantID); if err != nil { return nil, err }; defer tx.Rollback(ctx)
    var payload []byte
    err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1::uuid AND session_id=$2::uuid", tenantID, sessionID).Scan(&payload)
    if errors.Is(err, pgx.ErrNoRows) { return nil, ErrNotFound }; if err != nil { return nil, err }
    if err := tx.Commit(ctx); err != nil { return nil, err }
    return decodePayload(payload)
}

func (s *PostgresStore) UpdateSession(ctx context.Context, tenantID, sessionID, expectedState string, updated map[string]any) error {
    if updated["tenant_id"] != tenantID { return ErrConflict }
    payload, err := json.Marshal(updated); if err != nil { return err }
    tx, err := s.beginTenant(ctx, tenantID); if err != nil { return err }; defer tx.Rollback(ctx)
    tag, err := tx.Exec(ctx, `
        UPDATE ingestion_sessions SET state=$1, payload=$2::jsonb, updated_at=$3
        WHERE tenant_id=$4::uuid AND session_id=$5::uuid AND state=$6
    `, updated["state"], payload, updated["updated_at"], tenantID, sessionID, expectedState)
    if err != nil { return err }; if tag.RowsAffected()!=1 { return ErrConflict }
    return tx.Commit(ctx)
}

func (s *PostgresStore) FinalizeSession(ctx context.Context, tenantID, sessionID, jobID string, maxAttempts int, updated map[string]any) (map[string]any, error) {
    if maxAttempts<1 || maxAttempts>100 { return nil, fmt.Errorf("max_attempts must be between 1 and 100") }
    tx, err := s.beginTenant(ctx, tenantID); if err != nil { return nil, err }; defer tx.Rollback(ctx)
    var raw []byte
    err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1::uuid AND session_id=$2::uuid FOR UPDATE", tenantID, sessionID).Scan(&raw)
    if errors.Is(err, pgx.ErrNoRows) { return nil, ErrNotFound }; if err != nil { return nil, err }
    current, err := decodePayload(raw); if err != nil { return nil, err }
    state, _ := current["state"].(string)
    if state!="receiving" {
        if state=="finalizing" || state=="queued" || state=="processing" || state=="completed" {
            var jobJSON []byte
            err := tx.QueryRow(ctx, `
                SELECT jsonb_build_object('job_id',job_id,'state',state,'attempts',attempts,'max_attempts',max_attempts,'last_error_code',last_error_code)
                FROM ingestion_jobs WHERE tenant_id=$1 AND session_id=$2
            `, tenantID, sessionID).Scan(&jobJSON)
            if errors.Is(err, pgx.ErrNoRows) { return nil, ErrConflict }; if err != nil { return nil, err }
            var job map[string]any; if err := json.Unmarshal(jobJSON,&job); err != nil { return nil, err }
            if err := tx.Commit(ctx); err != nil { return nil, err }; return job,nil
        }
        return nil, ErrConflict
    }
    payload, err := json.Marshal(updated); if err != nil { return nil, err }
    tag, err := tx.Exec(ctx, "UPDATE ingestion_sessions SET state='finalizing',payload=$1::jsonb,updated_at=$2 WHERE tenant_id=$3 AND session_id=$4 AND state='receiving'", payload, updated["updated_at"], tenantID, sessionID)
    if err != nil { return nil, err }; if tag.RowsAffected()!=1 { return nil, ErrConflict }
    var jobJSON []byte
    err = tx.QueryRow(ctx, `
        INSERT INTO ingestion_jobs (job_id,tenant_id,session_id,state,max_attempts,available_at,created_at,updated_at)
        VALUES ($1::uuid,$2::uuid,$3::uuid,'pending',$4,now(),now(),now())
        ON CONFLICT (tenant_id,session_id) DO NOTHING
        RETURNING jsonb_build_object('job_id',job_id,'state',state,'attempts',attempts,'max_attempts',max_attempts,'last_error_code',last_error_code)
    `, jobID, tenantID, sessionID, maxAttempts).Scan(&jobJSON)
    if errors.Is(err, pgx.ErrNoRows) {
        err = tx.QueryRow(ctx, `
            SELECT jsonb_build_object('job_id',job_id,'state',state,'attempts',attempts,'max_attempts',max_attempts,'last_error_code',last_error_code)
            FROM ingestion_jobs WHERE tenant_id=$1 AND session_id=$2
        `, tenantID, sessionID).Scan(&jobJSON)
    }
    if err != nil { return nil, err }
    var job map[string]any; if err := json.Unmarshal(jobJSON,&job); err != nil { return nil, err }
    if err := tx.Commit(ctx); err != nil { return nil, err }; return job,nil
}

func (s *PostgresStore) GetStatus(ctx context.Context, tenantID, sessionID string) (map[string]any, map[string]any, error) {
    tx, err := s.beginTenant(ctx, tenantID); if err != nil { return nil,nil,err }; defer tx.Rollback(ctx)
    var raw []byte
    err = tx.QueryRow(ctx, "SELECT payload FROM ingestion_sessions WHERE tenant_id=$1 AND session_id=$2", tenantID, sessionID).Scan(&raw)
    if errors.Is(err,pgx.ErrNoRows) { return nil,nil,ErrNotFound }; if err != nil { return nil,nil,err }
    var jobRaw []byte
    err = tx.QueryRow(ctx, `
        SELECT jsonb_build_object('job_id',job_id,'state',state,'attempts',attempts,'max_attempts',max_attempts,'last_error_code',last_error_code)
        FROM ingestion_jobs WHERE tenant_id=$1 AND session_id=$2
    `, tenantID, sessionID).Scan(&jobRaw)
    var job map[string]any
    if err==nil { if err:=json.Unmarshal(jobRaw,&job); err!=nil { return nil,nil,err } } else if !errors.Is(err,pgx.ErrNoRows) { return nil,nil,err }
    if err:=tx.Commit(ctx); err!=nil { return nil,nil,err }
    session,err:=decodePayload(raw); return session,job,err
}

func (s *PostgresStore) Cancel(ctx context.Context, tenantID, sessionID string) (bool,error) {
    tx,err:=s.beginTenant(ctx,tenantID); if err!=nil { return false,err }; defer tx.Rollback(ctx)
    var raw []byte
    err=tx.QueryRow(ctx,"SELECT payload FROM ingestion_sessions WHERE tenant_id=$1 AND session_id=$2 FOR UPDATE",tenantID,sessionID).Scan(&raw)
    if errors.Is(err,pgx.ErrNoRows) { return false,ErrNotFound }; if err!=nil { return false,err }
    session,err:=decodePayload(raw); if err!=nil { return false,err }
    state,_:=session["state"].(string)
    switch state { case "completed","failed","cancelled","expired": return false,ErrConflict }
    session["state"]="cancelled"; delete(session,"lease")
    session["updated_at"]=time.Now().UTC().Format("2006-01-02T15:04:05.000Z")
    payload,err:=json.Marshal(session); if err!=nil { return false,err }
    _,err=tx.Exec(ctx,"UPDATE ingestion_sessions SET state='cancelled',payload=$1::jsonb,updated_at=now() WHERE tenant_id=$2::uuid AND session_id=$3::uuid",payload,tenantID,sessionID)
    if err!=nil { return false,err }
    _,err=tx.Exec(ctx,"UPDATE ingestion_jobs SET state='cancelled',lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE tenant_id=$1::uuid AND session_id=$2::uuid AND state IN ('pending','leased')",tenantID,sessionID)
    if err!=nil { return false,err }
    if err:=tx.Commit(ctx); err!=nil { return false,err }; return true,nil
}
