# Security, Privacy, and Audio Data Lifecycle Baseline

**Status:** Initial requirements baseline; the detailed threat register is maintained in [the threat model](../security/threat-model.md). Implementation controls still require feature-specific verification.

## Data classification

Treat the following as sensitive by default:

- Raw audio and video containing audio.
- Transcripts, speaker labels, voice-derived identifiers, and diarization output.
- Extracted entities, events, summaries, and embeddings that may reveal transcript content.
- Credentials, tenant identifiers, access tokens, and audit metadata.

Derived data is not automatically anonymous. Embeddings and summaries may disclose information from their source.

## Trust boundaries

1. Client/device to ingestion API.
2. API to object storage and metadata database.
3. Workers to speech/model providers.
4. Model output and retrieved content to application logic.
5. Tenant boundary in storage, caches, indexes, and retrieval.
6. Agent/tool boundary for repository, infrastructure, and external actions.

All audio, transcripts, files, model outputs, and retrieved documents are untrusted data. Instructions contained in them must never override system/developer policy or authorize tool actions.

## Minimum controls

- Authenticate callers and authorize every asset, job, transcript, and search operation.
- Enforce tenant scope in the database and search layer; do not rely on prompt instructions for isolation.
- Encrypt transport and stored sensitive data.
- Use least-privilege service identities and short-lived credentials where supported.
- Keep secrets and raw sensitive content out of logs, traces, metrics, and error messages.
- Record access, processing, export, retention, and deletion events.
- Validate file type from content, enforce size/duration limits, and isolate parsers/codecs.
- Apply request budgets, timeouts, bounded retries, rate limits, and provider allowlists.
- Make deletion propagate to object storage, derived artifacts, caches, indexes, backups according to documented retention guarantees.
- Use synthetic or explicitly consented data in development and evaluation.

## Retention and deletion

No universal retention duration is assumed at this stage. Before production use, each deployment must define:

- Purpose and lawful basis for processing.
- Data owner/controller responsibilities.
- Retention duration for raw media, transcripts, embeddings, logs, and backups.
- Deletion request behavior and propagation guarantees.
- Provider/subprocessor retention and training-use settings.
- Export and access procedures.

The product must support configurable retention and deletion workflows. A successful API deletion response must not claim that all backups or downstream providers have erased data unless that guarantee is verified.

## Agent operational limits

Autonomous agents may perform bounded repository changes and tests. Production deployment, paid-resource creation, data deletion, credential rotation, access-control changes, and external data transfer require an explicit policy grant for the specific action and environment.

## Open questions

- Which jurisdictions and customer deployment models must be supported first?
- Will processing be local/self-hosted, managed cloud, or both?
- What consent and recording-notice workflows are required by target use cases?
- Which providers may receive raw audio versus transcript-only content?


## Retention, deletion, and audit

The API assigns a deployment-default asset expiry using VOXPIPE_DEFAULT_RETENTION_DAYS. The default is 0 (no automatic asset expiry); clients cannot select or extend this policy. Positive values are copied into durable asset metadata and enforced by the tenant-scoped reconciliation process.

Reconciliation expires stale created/receiving sessions only when their session expiry has passed and no upload or worker lease is active. It removes staging files and interrupted parts for completed and terminal sessions. For failed, cancelled, expired, or retention-expired assets, it deletes object data before deleting metadata. Every state transition and cleanup outcome is recorded in the tenant-scoped audit event table. Audit retention and backup deletion remain deployment-specific and must be configured separately.

Run per tenant with:
PYTHONPATH=src python scripts/reconcile_ingestion.py --tenant-id <UUID>

Required environment: VOXPIPE_DATABASE_DSN, VOXPIPE_STAGING_ROOT, VOXPIPE_OBJECT_STORE_ROOT. API and worker must use the same private staging and object-store volumes. The operation is destructive but idempotent; it should be scheduled only after confirming the deployment's retention setting and backup policy.


## API identity and abuse controls

The Go API production mode validates RS256 JWT signatures against a trusted HTTPS JWKS endpoint, exact issuer and audience, expiry, not-before, UUID tenant mapping, and allowlisted ingestion scopes. Unknown signing key IDs trigger JWKS refresh. OAuth token introspection is required for every request so revoked/inactive tokens and identity-provider outages fail closed. Static SHA-256 grants require an explicit bootstrap/development mode.

Tenant limits are enforced transactionally in PostgreSQL: reserved staged bytes and new-session rate are checked under a tenant advisory lock, and active upload leases enforce concurrent upload limits. Upload streams have a resettable inactivity deadline and a separate absolute duration ceiling. Defaults are 3 concurrent uploads, 2 GiB reserved staging bytes, 60 new sessions per minute, 60 seconds inactivity, and 30 minutes maximum body-read duration. Configure these against deployment capacity and customer commitments.


## Audit redaction enforcement

Audit persistence is allowlist-based in both Go API and Python PostgreSQL repository adapters. Only bounded lifecycle facts are retained: state transitions (`from`, `to`, `prior_state`), retention expiry, and cleanup outcome booleans. Unknown keys and nested objects are discarded. Error codes must be bounded uppercase machine identifiers; free-form values are replaced with `AUDIT_ERROR_REDACTED`. Tokens, authorization headers, audio/transcript content, provider payloads, local paths, and arbitrary request metadata are never valid audit metadata. Tests exercise both runtime sanitizers. This is defense in depth; callers must still avoid logging sensitive data outside the audit repository.
