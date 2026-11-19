# VoxPipe Contracts

## Schema format

Version 1 contracts use JSON Schema Draft 2020-12. Schema identifiers are stable URNs and schema versions are explicit in payloads.

- `schemas/audio-asset.v1.schema.json`: immutable media asset metadata, integrity, storage reference, and asset lifecycle.
- `schemas/ingestion-session.v1.schema.json`: batch/stream ingestion lifecycle, idempotency, upload progress, and result/failure reference.
- `examples/*.valid.json`: canonical fixtures used by automated schema tests.

## Boundary rules

- A tenant identifier is mandatory in internal records. It must be derived from authenticated identity at the service boundary, never trusted from an untrusted client payload.
- `object_key` is an opaque storage reference, not a public URL or signed URL.
- Do not place credentials, signed URLs, raw transcript content, or audio bytes in these records.
- The asset describes the media object; the session describes the attempt/lifecycle to ingest it. A session may fail without creating an asset.
- The same asset may be referenced by later processing jobs; the asset itself is not a transcription job.
- Media hints are untrusted declarations. The ingestion validator must inspect the actual file/container and derive authoritative media properties.
- A successful session must reference an accepted asset. The schema checks presence; the application state machine must enforce that the referenced asset is actually accepted and belongs to the same tenant.
- `stream` is a reserved contract mode. This schema does not imply that a real-time streaming transport is implemented.

## Lifecycle guidance

Session transitions are constrained by the application state machine:

```text
created -> receiving -> finalizing -> queued -> processing -> completed
   |           |            |          |           |
   +-----------+------------+----------+-----------+-> failed
   +-----------+------------+----------+-----------+-> cancelled
   +-----------+------------+----------+-----------+-> expired
```

Terminal states are `completed`, `failed`, `cancelled`, and `expired`. A terminal session must not silently return to an active state; retries create a new session and use idempotency to prevent duplicate asset creation.

## Compatibility

- Additive optional fields are preferred.
- Breaking semantic or type changes require a new schema major version.
- Consumers must reject unsupported major versions and tolerate unknown additive fields only when the schema policy permits them.
- Every persisted record and processing job must retain the schema version used at creation.
