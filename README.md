# VoxPipe

**Autonomous Audio Intelligence & Structured Knowledge Platform**

VoxPipe turns audio into traceable, structured, searchable knowledge that both people and software agents can consume.

> Audio → Speech → Structured Intelligence → Searchable Knowledge → Actions

## Project status

**Stage:** Phase 1 — Audio Ingestion Foundation  
**Status:** Active development  
**Default branch:** `main`  
**Active foundation branch:** `bootstrap/phase-0-foundation`

This repository is intentionally starting with engineering governance, executable validation, and architecture contracts before committing to a large runtime stack.

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
├── AGENTS.md
├── README.md
├── Makefile
├── contracts/
│   ├── schemas/
│   └── examples/
├── planning/
│   └── WORK_ITEMS.json
├── docs/
│   ├── ROADMAP.md
│   ├── architecture/
│   │   ├── overview.md
│   │   └── storage.md
│   ├── decisions/
│   │   └── 0001-agent-native-foundation.md
│   └── operations/
│       └── agent-execution-protocol.md
└── scripts/
    └── validate_repo.py
```

## Quick start

Requirements: Python 3.11+ and Make. The repository pins Python 3.12 in `.python-version`.

Create an isolated environment and install the development/test dependencies:

```bash
make bootstrap
. .venv/bin/activate
make check
```

The work-ledger validator uses the Python standard library. Contract tests use `jsonschema`; media inspection uses system FFmpeg/ffprobe binaries. Runtime services and additional providers will be introduced only when a phase has a concrete, testable requirement.

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
