# Versioning and Release Policy

**Status:** Initial policy; revisit before the first public API release.

## Versioning surfaces

Track versions independently for:

- Public HTTP API.
- Event and job payload schemas.
- Structured extraction schemas.
- SDK packages.
- Deployable application releases.

Internal module refactors do not require a public version change unless they alter an exposed contract.

## Compatibility

- Additive optional fields are preferred for backward-compatible evolution.
- Removing or changing the meaning/type of a field is breaking.
- Event consumers must tolerate unknown optional fields.
- Schema changes must include compatibility tests and migration notes.
- Persisted jobs must declare the contract/schema version used to create them.
- Never silently reinterpret historical transcript or extraction data using a new schema.

## Release process

A release candidate must pass CI, relevant contract/integration tests, dependency/security checks, and migration validation. Production releases should use staged rollout, health checks, and a documented rollback path.

Do not publish a stable API or promise semantic-versioning guarantees until the first public contract is approved. Before then, APIs are explicitly pre-release and may change with migration notes.
