# VoxPipe

**Autonomous Audio Intelligence & Structured Knowledge Platform**

VoxPipe turns audio into traceable, structured, searchable knowledge that both people and software agents can consume.

> Audio → Speech → Structured Intelligence → Searchable Knowledge → Actions

## Project status

**Stage:** Phase 1 — Audio Ingestion Foundation  
**Status:** VP-0015 authenticated upload and durable queue acceptance criteria are met; independent Python and Go/PostgreSQL CI passed on the implementation head. The initial filesystem staging and static token authenticator remain single-node/bootstrap choices; production hardening is tracked in VP-0018–VP-0020.  
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
│   └── voxpipe-contract-evolution/SKILL.md
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
│   ├── prepare_test_database.py
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

Media integration tests require FFmpeg/ffprobe on the system. PostgreSQL integration tests run when `VOXPIPE_TEST_POSTGRES_ADMIN_DSN` is configured; CI provisions PostgreSQL 16 and a non-superuser RLS application role.

For a PostgreSQL deployment, install the optional driver with `pip install -r requirements-postgres.txt` and apply the versioned migrations using `voxpipe.ingestion.apply_migrations`. See [PostgreSQL operations](docs/architecture/postgres.md). The authenticated Go upload API and Python worker topology are described in [Upload API operations](docs/architecture/upload-api.md).

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
