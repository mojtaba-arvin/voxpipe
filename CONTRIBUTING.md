# Contributing to VoxPipe

## Before you start

- Read [AGENTS.md](AGENTS.md) and the relevant architecture documents.
- Select or create a scoped item in [planning/WORK_ITEMS.json](planning/WORK_ITEMS.json).
- Check open pull requests and recent commits to avoid duplicate work.

## Local requirements

- Python 3.11+ (3.12 is pinned in `.python-version`)
- Make

Create and activate an isolated environment, then run all checks:

```bash
make bootstrap
. .venv/bin/activate
make check
```

The test runner uses Python's standard-library `unittest`; contract validation uses `jsonschema` from `requirements-dev.txt`. No paid services or production credentials are required.

The validation currently uses only the Python standard library.

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

A green CI run is necessary but not sufficient. Review the diff and verify the acceptance criteria.
