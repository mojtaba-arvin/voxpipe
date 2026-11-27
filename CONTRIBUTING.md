# Contributing to VoxPipe

## Before you start

- Read [AGENTS.md](AGENTS.md) and the relevant architecture documents.
- Select or create a scoped item in [planning/WORK_ITEMS.json](planning/WORK_ITEMS.json).
- Check open pull requests and recent commits to avoid duplicate work.

## Local requirements

- Python 3.11+ (3.12 is pinned in `.python-version`)
- Go 1.25+ (public HTTP API)
- Make

Create and activate an isolated environment, then run all checks:

```bash
make bootstrap
. .venv/bin/activate
make check
make go-check
```

The test runner uses Python's standard-library `unittest`; contract validation uses `jsonschema` from `requirements-dev.txt`. No paid services or production credentials are required.

The repository validator and unit suite use Python's standard library plus declared development dependencies. Go validation is read-only and runs module drift checks, formatting checks, vet, and race-enabled tests. CI runs Python and Go/PostgreSQL checks independently.

## Branches and commits

- Use focused branches such as `feat/audio-ingestion-contract` or `fix/work-ledger-validation`.
- Keep commits small and explain the engineering intent.
- Do not force-push shared branches.
- Do not commit secrets, real customer audio, transcripts, credentials, or private datasets.

## Pull requests

Every pull request must:

- Link one or more work item IDs.
- Explain the problem and the chosen approach.
- List tests and commands actually executed, with outcomes.
- Describe compatibility, security, privacy, and operational implications.
- Identify limitations and follow-up work.
- Update the work ledger and documentation when behavior or project state changes.

Use matching repository skills from `.agents/skills/` for repeatable workflows. When supported, request read-only specialist reviews from `.codex/agents/` for security, CI failures, and cross-runtime contracts.

A green CI run is necessary but not sufficient. Review the diff and verify the acceptance criteria.

## GitHub Issue title convention

Use `<type>(<scope>): <short, clear description>` for every Issue title. The type must be one of `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`, `perf`, or `revert`; scope must be concise lowercase kebab-case. Keep priorities, milestones, feature identifiers, agent identity, and run metadata in labels or the Issue body. Do not use bracketed prefixes, standalone priority codes, `Recover:`, or supervisor identity prefixes. Preserve Issue metadata and relationships when renaming. This repository keeps the complete title convention here so contributors do not depend on external/private policy repositories.
