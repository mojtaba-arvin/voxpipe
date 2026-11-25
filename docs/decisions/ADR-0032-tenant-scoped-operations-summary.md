# ADR-0032: Tenant-scoped operational summary

- Status: Accepted
- Date: 2026-11-25

## Context

VoxPipe has durable ingestion jobs, retry/dead-letter states and queue lease metadata, but operators need a safe way to distinguish backlog, retry pressure and processing failures without inspecting customer media or raw error payloads. A global dashboard would also introduce a privileged cross-tenant access surface that the current product does not need.

## Decision

Expose a bounded aggregate-only summary through an authenticated API and first-party CLI. The caller's tenant comes exclusively from the verified bearer principal and PostgreSQL forced RLS. Access requires a dedicated `operations:read` scope; ingestion and transcript scopes do not imply it. The query window is limited to 1–168 hours, defaulting to 24.

The response contains session/job state counts, retry count, recent jobs with retained failure codes, completed processing duration, current pending depth/age, active/expired leases, dead-letter depth and a database observation timestamp. It never returns raw error codes, session payloads, audio/transcript content, object references, credentials or tenant IDs.

This is intentionally a per-tenant operational surface. Cross-tenant support access, alerts, SLOs and a web dashboard are separate product decisions and are not implied by this ADR.

## Consequences

- Operator tokens must be provisioned with `operations:read` explicitly.
- Aggregates are useful for triage but are not a complete event history; retry/failure semantics are documented.
- Time-window indexes keep the bounded historical scan practical.
- The view reports observed data and freshness rather than asserting that the API/worker is healthy.
- No separate metrics archive is created. Existing session/job retention and cleanup policies remain authoritative; once rows are purged, historical aggregates for those rows naturally decrease or disappear.
