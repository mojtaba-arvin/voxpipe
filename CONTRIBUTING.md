# Contributing to VoxPipe

## Before you start

- Read [AGENTS.md](AGENTS.md) and the relevant architecture documents.
- Select or create a scoped item in [planning/WORK_ITEMS.json](planning/WORK_ITEMS.json).
- Check open pull requests and recent commits to avoid duplicate work.

## Local requirements

- Python 3.11+
- Make

Run the standard repository check:

```bash
make check
```

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
