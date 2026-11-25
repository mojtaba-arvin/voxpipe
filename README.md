# VoxPipe

**Autonomous Audio Intelligence & Structured Knowledge Platform**

VoxPipe turns audio into traceable, structured, searchable knowledge that both people and software agents can consume.

> Audio → Speech → Structured Intelligence → Searchable Knowledge → Actions

## Project status

**Repository foundation:** VP-0001–VP-0037 are complete for repository scope. Product Experience work is now tracked separately in Phase 7; VP-0038 (first-party CLI) is complete. See [the roadmap](docs/ROADMAP.md) and [machine-readable work ledger](planning/WORK_ITEMS.json).

**Pre-closure main baseline:** `8bf259155d57fb41dba2a25c7ed6b086fcd7527d` (2026-11-25). Validation run `45565589850` passed all five jobs: Python/PostgreSQL, Go/PostgreSQL, OpenTelemetry runtime smoke, PostgreSQL backup/restore rehearsal, and OpenTelemetry Collector/Prometheus integration. Release-candidate proof run `45564559936` built Linux amd64/arm64 artifacts, CycloneDX SBOM, manifest and checksums, verified GitHub attestations, and uploaded the candidate bundle.

**Product maturity:** VoxPipe includes tenant-scoped ingestion and transcript persistence, durable PostgreSQL-backed jobs/workflows, local Faster-Whisper STT, optional disabled-by-default Deepgram integration, VAD and diarization adapters, calibrated language identification, evidence-validated structured extraction, lexical and pgvector hybrid retrieval, adaptive provider routing, privacy-safe telemetry, and release/governance automation. These are repository implementation claims backed by tests; they are not a blanket production-readiness claim.

**Explicit limitations:** FLEURS VAD labels are utterance-envelope proxies, diarization data is controlled read speech rather than natural overlapping conversation, LID has no code-switch/noisy-field evaluation, extraction has no human-reviewed Persian gold set, and retrieval has no human-judged Persian relevance score or selected production embedding model. Deepgram remains disabled pending an API key and separately approved spend (current test budget: USD 0). Production capacity, staging SLOs, live notification delivery, customer-traffic canaries, production backup/PITR and RPO/RTO are not established. Local filesystem storage remains a single-node development/deployment option.

**Default branch:** `main`


## Design principles

1. **Autonomous by default, human-supervisable.** Agents can plan, implement, test, review, and recover routine work without a human in the loop; humans retain optional inspection and override authority.
2. **Evidence over declarations.** A task is complete only when its acceptance criteria have reproducible evidence.
3. **Contracts before implementations.** Components communicate through versioned, testable boundaries.
4. **Replaceable capabilities.** Providers, models, storage engines, and workflow runtimes sit behind explicit ports.
5. **Measure before optimizing.** Language and infrastructure choices require representative benchmarks, not blanket claims.
6. **Safe autonomy.** Least privilege, bounded retries, budgets, audit trails, and rollback are part of the design.
7. **Durable progress.** Work state lives in versioned repository artifacts, not only in an agent's conversation memory.

## Repository map

```text
.
├── .github/workflows/validate.yml
├── .agents/skills/
│   ├── voxpipe-task-execution/SKILL.md
│   ├── voxpipe-ci-triage/SKILL.md
│   ├── voxpipe-postgres-reliability/SKILL.md
│   ├── voxpipe-contract-evolution/SKILL.md
│   ├── voxpipe-ingestion/SKILL.md
│   └── voxpipe-asset-discovery/SKILL.md
├── .codex/agents/
│   ├── failure-triager.toml
│   ├── security-reviewer.toml
│   └── contract-reviewer.toml
├── .codex/config.toml
├── AGENTS.md
├── Makefile
├── README.md
├── LICENSE
├── go.mod
├── cmd/voxpipe-api/main.go
├── internal/httpapi/
│   ├── auth.go
│   ├── oidc.go
│   ├── upload_limits.go
│   ├── server.go
│   ├── staging.go
│   ├── store.go
│   ├── uuid.go
│   ├── server_test.go
│   ├── oidc_test.go
│   ├── upload_limits_test.go
│   └── store_integration_test.go
├── contracts/
│   ├── README.md
│   ├── examples/
│   │   ├── audio-asset.valid.json
│   │   ├── ingestion-session.valid.json
│   │   └── transcript.valid.json
│   ├── mcp/capabilities.v1.json
│   ├── openapi/ingestion.v1.yaml
│   └── schemas/
│       ├── audio-asset.v1.schema.json
│       ├── ingestion-session.v1.schema.json
│       └── transcript.v1.schema.json
├── docs/
│   ├── ROADMAP.md
│   ├── architecture/
│   ├── decisions/
│   └── operations/
├── migrations/postgres/
│   ├── 0001_create_ingestion_tables.sql
│   ├── 0002_add_tenant_rls_and_updated_at.sql
│   ├── 0003_create_durable_ingestion_jobs.sql
│   ├── 0004_add_upload_leases_and_audit.sql
│   └── 0005_add_job_started_at.sql
├── planning/WORK_ITEMS.json
├── requirements-dev.txt
├── requirements-postgres.txt
├── scripts/
│   ├── generate_mcp_docs.py
│   ├── inspect_ingestion_queue.py
│   ├── prepare_test_database.py
│   ├── reconcile_ingestion.py
│   ├── run_ingestion_worker.py
│   ├── validate_repo.py
│   └── voxpipe_cli.py
├── src/voxpipe/
│   ├── __init__.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── jobs.py
│   │   ├── postgres.py
│   │   ├── recovery.py
│   │   ├── repository.py
│   │   ├── service.py
│   │   └── worker.py
│   ├── speech/
│   │   ├── __init__.py
│   │   ├── contracts.py
│   │   ├── ports.py
│   │   └── service.py
│   ├── mcp/
│   │   ├── __init__.py
│   │   └── server.py
│   ├── media/
│   │   ├── __init__.py
│   │   ├── probe.py
│   │   └── validation.py
│   └── storage/
│       ├── __init__.py
│       ├── local.py
│       ├── ports.py
│       └── staging.py
└── tests/
    ├── test_contract_schemas.py
    ├── test_ingestion_recovery.py
    ├── test_ingestion_service.py
    ├── test_ingestion_worker.py
    ├── test_local_storage.py
    ├── test_mcp_server.py
    ├── test_media_probe.py
    ├── test_media_validation.py
    ├── test_postgres_repository.py
    ├── test_staging_cleanup.py
    ├── test_speech_contracts.py
    └── test_validate_repo.py
```

## Quick start

Requirements: Python 3.11+, Go 1.25+, and Make. The repository pins Python 3.12 in `.python-version`; Go is used by the public HTTP API.

Create an isolated environment and install development/test dependencies:

```bash
make bootstrap
. .venv/bin/activate
make check
make go-check
```

The optional local STT runtime is installed separately with `pip install -r requirements-stt.txt`; provision and pin model weights before offline use. `FasterWhisperAdapter` defaults to local-only model loading, CPU int8, and no diarization. See [the STT evaluation](docs/research/stt-provider-evaluation.md) and [ADR-0022](docs/decisions/ADR-0022-first-stt-adapter.md). The optional `DeepgramNova3Adapter` uses the provider-neutral port and standard-library HTTPS transport; it requires `DEEPGRAM_API_KEY`, explicit runtime selection, and an operator-approved positive spend ceiling. Its tests use a fake transport and never send audio or incur provider charges. The adapter does not claim provider-side idempotency; ambiguous network failures are not automatically retried. The benchmark scorer accepts JSONL results from a consented corpus: `python scripts/benchmark_stt.py results.jsonl`.

Media integration tests require FFmpeg/ffprobe on the system. PostgreSQL integration tests run when `VOXPIPE_TEST_POSTGRES_ADMIN_DSN` is configured; CI provisions PostgreSQL 16 and a non-superuser RLS application role.

Run the authenticated MCP stdio adapter with `PYTHONPATH=src python -m voxpipe.mcp.server`; configure `VOXPIPE_MCP_API_URL` and `VOXPIPE_MCP_API_TOKEN`. See [MCP capabilities](docs/architecture/mcp-capabilities.md).

Set VOXPIPE_DEFAULT_RETENTION_DAYS to a positive value for server-assigned asset expiry; 0 (default) disables automatic asset expiry. Run per-tenant cleanup with PYTHONPATH=src python scripts/reconcile_ingestion.py --tenant-id UUID after setting VOXPIPE_DATABASE_DSN, VOXPIPE_STAGING_ROOT, and VOXPIPE_OBJECT_STORE_ROOT.

For a PostgreSQL deployment, install the optional driver with `pip install -r requirements-postgres.txt` and apply the versioned migrations using `voxpipe.ingestion.apply_migrations`. See [PostgreSQL operations](docs/architecture/postgres.md). The authenticated Go upload API and Python worker topology are described in [Upload API operations](docs/architecture/upload-api.md).

## First-party CLI

The CLI covers the core developer workflow without a custom API client:

```bash
export VOXPIPE_API_URL=http://127.0.0.1:8080
export VOXPIPE_API_TOKEN='...'
PYTHONPATH=src python scripts/voxpipe_cli.py ingest ./meeting.wav --wait
PYTHONPATH=src python scripts/voxpipe_cli.py status SESSION_UUID
PYTHONPATH=src python scripts/voxpipe_cli.py search 'جلسه فروش' --language fa
```

See [CLI setup, scopes and safety boundaries](docs/architecture/cli.md). The API token must have the capability required by each command; use HTTPS outside loopback.

## Development workflow

- Read [AGENTS.md](AGENTS.md) before changing the repository.
- Select work from [planning/WORK_ITEMS.json](planning/WORK_ITEMS.json).
- Follow the [agent execution protocol](docs/operations/agent-execution-protocol.md).
- Load the matching repository skill and use read-only specialist agents for independent review when supported.
- Record material architecture decisions as ADRs.
- Run `make check` for Python changes and `make go-check` for Go/cross-runtime changes before reporting completion. CI runs both gates independently.
- Keep changes focused and submit them through a pull request.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for phase goals, gates, and current priorities.

## License

VoxPipe is distributed under the [MIT License](LICENSE).


## Release candidates

The `Release candidate artifacts` workflow builds Linux amd64/arm64 API binaries, a source archive, a CycloneDX dependency SBOM, a release manifest and SHA-256 checksums for tags on `main` or manual dispatch. It does not publish a GitHub Release or deploy to production. Canary rollout, signed provenance and rollback/restore require a configured deployment environment and remain gated.
