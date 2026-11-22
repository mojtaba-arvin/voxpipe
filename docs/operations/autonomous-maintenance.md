# Bounded autonomous maintenance

## Enabled automation

- Dependabot opens grouped weekly update PRs for Go modules, Python requirements and GitHub Actions.
- A scheduled and dependency-change-triggered workflow runs `pip-audit` and `govulncheck` at pinned tool versions.
- Existing required Python/PostgreSQL and Go/PostgreSQL CI remains the merge-quality gate.
- All maintenance changes remain PR-based; no workflow pushes directly to `main`.

## Approval and blast-radius policy

- Dependency updates are not auto-merged.
- Human approval is required for database migrations, tenant/RLS changes, authentication, retention/deletion behavior, provider/data-egress changes, release/deploy workflows and production configuration.
- High-impact security remediation must include a regression test and independent CI evidence.
- Automation has read-only repository permissions unless a narrowly scoped workflow explicitly needs more.
- Never expose repository secrets to untrusted pull-request code.
- Never fabricate reviews, bypass branch protection, or use force-push to replace shared history.
- No paid model API, hosted inference, GPU service or third-party autonomous agent is enabled by this policy.

## Failure handling

1. A failing audit opens no automatic remediation commit; inspect the advisory and dependency path.
2. Create a focused PR with the smallest compatible update and regression coverage.
3. Run Python and Go validation independently.
4. For a bad merged update, revert through a new PR and rerun the same gates.
5. Record the advisory, affected versions, fix version and verification run in the PR.

## Limits

This is bounded dependency/security automation, not a general autonomous coding agent. Automatic issue triage, patch generation, human-approval orchestration and canary rollback are not enabled. Keep those tasks human-controlled until a permission model, audit trail, budget and stop/rollback behavior are tested.
