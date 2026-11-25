# Tenant-scoped operational summary

## Contract

The Go API exposes `GET /v1/operations/ingestion/summary?window_hours=24`. The `window_hours` parameter is optional, defaults to 24, and is bounded to 1–168. The first-party CLI exposes the same capability as:

```bash
PYTHONPATH=src python scripts/voxpipe_cli.py health --window-hours 24
```

Both paths require the dedicated `operations:read` scope. A normal `ingestion:read` or `transcript:read` grant is insufficient. Tenant identity is always taken from the verified bearer principal; callers cannot choose a tenant ID. The database query runs in a transaction with `voxpipe.tenant_id` set and forced RLS remains authoritative.

## Returned metrics

- `ingestions`: total and state counts for sessions updated within the requested window.
- `jobs`: total and state counts for jobs created within the requested window; retry count is the sum of attempts beyond the first for those jobs.
- `processing_failure_count`: jobs in the window whose latest `last_error_code` is still present and whose last update is within the window. This is a count of affected jobs, not an event-level history of every failed attempt.
- `completed_processing_seconds_avg`: average `updated_at - started_at` for completed jobs in the requested creation window.
- `queue`: current pending depth, age of the oldest pending job, active and expired leases, and current dead-letter depth. These are a point-in-time snapshot across the tenant's current queue, not limited to the historical window.
- `observed_at`: database server timestamp for the snapshot.

The query is aggregate-only. It does not select or serialize session payloads, audio/transcript content, storage references, credentials, raw error codes or audit metadata. Query windows are bounded and indexed by tenant plus update/create time.

## Operational and privacy boundaries

This endpoint is a tenant-level view, not a cross-tenant administrator dashboard. Operators receive metrics only for the tenant bound to their credential. A platform-wide view would require a separately designed privileged identity and audit model; this feature does not introduce one.

The view reports observed counts and ages; it does not infer service health from process liveness, claim an SLO, send alerts, or promise queue completion time. It does not create metric snapshots or extend data retention: existing session/job retention and cleanup policies remain authoritative, so purged rows naturally disappear from historical aggregates. `observed_at` makes snapshot freshness explicit. Retry counts are cumulative per job within the selected creation window; recent failure counts reflect the last retained job error code, not every intermediate failure event.

## Access provisioning

For static-token deployments, grant `operations:read` only to an operator token that needs this view. Keep the token digest-only in API configuration. For OIDC deployments, the verified access token must carry `operations:read`. Do not add this scope to ordinary ingestion client credentials by default.

## Rollback

The feature is additive: remove the CLI invocation or revoke `operations:read` to disable access. The endpoint can be removed in a later release without changing ingestion lifecycle semantics. The two supporting indexes can be dropped independently if query-plan evidence shows they are not useful, but do not remove them without checking production query plans.
