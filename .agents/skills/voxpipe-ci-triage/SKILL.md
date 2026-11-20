---
name: voxpipe-ci-triage
description: Use when VoxPipe CI, local validation, unit tests, integration tests, or builds fail. Finds the first failing gate, extracts root causes from logs, adds regression coverage, and verifies the fix without masking failures.
---

# VoxPipe CI failure triage

1. Identify the exact commit SHA and workflow run. Confirm that the run tests the current PR head, not an earlier commit.
2. Fetch the failing job, step summaries, and full logs. Find the earliest failing command; later skipped steps are consequences, not independent failures.
3. Extract every failing test name, exception type, source location, SQL statement, and relevant environment/version.
4. Group failures by root cause. Distinguish test assertions from expected error logs emitted by negative-path tests.
5. Reproduce the failure with the narrowest deterministic test. For database issues, use the same PostgreSQL major version, role privileges, RLS policies, and concurrency shape as CI.
6. Fix the underlying invariant, not only the observed assertion. Add or strengthen a regression test that fails before the fix.
7. Check adjacent code paths and both runtimes when a contract or shared database is involved.
8. Run focused tests, then the complete relevant validation target. Never skip or weaken a failing test to obtain green CI.
9. Push the fix, wait for a workflow run on the exact new head SHA, and inspect every job's conclusion. A green Python job does not imply Go passed, and vice versa.
10. Record the original failure, root cause, corrective change, regression test, and final run URL in the work item/PR.

## VoxPipe recurring hazards to check

- PostgreSQL cannot infer an untyped parameter inside polymorphic JSON constructors such as `jsonb_build_object`; explicitly cast values where needed.
- Concurrent idempotency must handle every applicable unique constraint, then read back and compare the canonical existing row.
- Go and Python must use the same session/job lock order and state-transition rules.
- A passing test suite on SQLite does not validate PostgreSQL-specific SQL, RLS, transaction isolation, or locking.
- Formatting and dependency checks must be read-only in CI; do not let validation silently rewrite the worktree.
