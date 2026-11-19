---
name: voxpipe-task-execution
description: Use when planning, resuming, implementing, or closing any VoxPipe work item. Enforces repository-first context, dependency-aware task selection, bounded execution, evidence, review, and durable progress.
---

# VoxPipe task execution

1. Read root `AGENTS.md`, this skill, `docs/operations/agent-execution-protocol.md`, the relevant architecture docs, and `planning/WORK_ITEMS.json`.
2. Inspect the current branch, base branch, open PRs, recent commit history, working tree, and CI status. Never infer repository state from conversation history.
3. Select the highest-priority unblocked item. Do not start an item whose dependencies are not `done`. Avoid duplicate work already present in another branch or PR.
4. Before implementation, record the work item, problem statement, smallest planned change, expected tests, security/compatibility risks, and rollback path in the PR or work log.
5. Keep one bounded implementation slice per worker. Parallelize read-only research/review; do not let multiple agents write the same files or coupled interfaces.
6. After each meaningful slice, run focused tests. Before review, run all required repository checks and inspect their complete output.
7. Ask a separate read-only reviewer to inspect the diff for correctness, security, contracts, and missing failure tests. Resolve findings before claiming completion.
8. Update the work ledger and docs only after evidence exists. A successful API call, generated code, or local mock test is not completion evidence.
9. Report exact commands, environment, pass/fail outcomes, commit/PR, residual risks, and the next unblocked item.
10. After two materially different failed approaches, stop repeating attempts. Record evidence and change strategy or delegate.

## Required verification

- Python ingestion/repository changes: `make check`.
- Go API or cross-runtime changes: `make check` and `make go-check`.
- PostgreSQL/RLS/queue changes: run integration tests against PostgreSQL 16 with a non-owner, non-superuser, NOBYPASSRLS role.
- Public contract changes: validate OpenAPI/JSON Schema, fixtures, and both runtime implementations.
- Security-sensitive changes: review tenant isolation, trust boundaries, file/path handling, data retention, auditability, and failure behavior.

Never mark an item `done` while any required check is skipped, pending, or failing.
