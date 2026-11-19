# VoxPipe

**Autonomous Audio Intelligence & Structured Knowledge Platform**

VoxPipe turns audio into traceable, structured, searchable knowledge that both people and software agents can consume.

> Audio → Speech → Structured Intelligence → Searchable Knowledge → Actions

## Project status

**Stage:** Phase 1 — Audio Ingestion Foundation  
**Status:** Active development — batch ingestion and PostgreSQL RLS persistence are CI-verified; the durable queue, worker, and Go upload API are implemented on an active review branch, with new CI verification pending.  
**Default branch:** `main`

## Design principles

1. **Agent-native, not agent-exclusive.** Agents are first-class operators; humans remain able to inspect, direct, and recover the system.
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
├── AGENTS.md
├── Makefile
├── README.md
├── go.mod
├── cmd/voxpipe-api/main.go
├── internal/httpapi/
│   ├── auth.go
│   ├── server.go
│   ├── staging.go
│   ├── store.go
│   ├── uuid.go
│   ├── server_test.go
│   └── store_integration_test.go
├── contracts/
│   ├── openapi/ingestion.v1.yaml
│   ├── examples/
│   └── schemas/
├── docs/
│   ├── ROADMAP.md
│   ├── architecture/
│   ├── decisions/
│   └── operations/
├── migrations/postgres/
│   ├── 0001_create_ingestion_tables.sql
│   ├── 0002_add_tenant_rls_and_updated_at.sql
│   └── 0003_create_durable_ingestion_jobs.sql
├── planning/WORK_ITEMS.json
├── requirements-dev.txt
├── requirements-postgres.txt
├── scripts/
│   ├── validate_repo.py
│   └── run_ingestion_worker.py
├── src/voxpipe/
│   ├── media/
│   ├── storage/
│   └── ingestion/
│       ├── repository.py
│       ├── postgres.py
│       ├── jobs.py
│       ├── worker.py
│       ├── service.py
│       └── recovery.py
└── tests/
    ├── test_contract_schemas.py
    ├── test_media_probe.py
    ├── test_media_validation.py
    ├── test_local_storage.py
    ├── test_ingestion_service.py
    ├── test_ingestion_recovery.py
    ├── test_ingestion_worker.py
    └── test_postgres_repository.py
## Quick start

Requirements: Python 3.11+ and Make. The repository pins Python 3.12 in `.python-version`.

Create an isolated environment and install development/test dependencies:

```bash
make bootstrap
. .venv/bin/activate
make check
```

Media integration tests require FFmpeg/ffprobe on the system. PostgreSQL integration tests run when `VOXPIPE_TEST_POSTGRES_ADMIN_DSN` is configured; CI provisions PostgreSQL 16 and a non-superuser RLS application role.

For a PostgreSQL deployment, install the optional driver with `pip install -r requirements-postgres.txt` and apply the versioned migrations using `voxpipe.ingestion.apply_migrations`. See [PostgreSQL operations](docs/architecture/postgres.md). The authenticated Go upload API and Python worker topology are described in [Upload API operations](docs/architecture/upload-api.md).

## Development workflow

- Read [AGENTS.md](AGENTS.md) before changing the repository.
- Select work from [planning/WORK_ITEMS.json](planning/WORK_ITEMS.json).
- Follow the [agent execution protocol](docs/operations/agent-execution-protocol.md).
- Record material architecture decisions as ADRs.
- Run `make check` before reporting completion.
- Keep changes focused and submit them through a pull request.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for phase goals, gates, and current priorities.

## License

License has not yet been selected. Do not assume that this repository is open-source licensed until a license file is added.
