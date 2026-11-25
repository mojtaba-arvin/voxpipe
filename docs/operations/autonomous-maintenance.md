# Bounded autonomous maintenance

## Enabled automation

- Dependabot opens grouped weekly update PRs for Go modules, Python requirements and GitHub Actions.
- A scheduled and dependency-change-triggered workflow runs `pip-audit` and `govulncheck` at pinned tool versions.
- Existing required Python/PostgreSQL and Go/PostgreSQL CI remains the merge-quality gate.
- All maintenance changes remain PR-based; no workflow pushes directly to `main`.
- The active `protect main` ruleset requires PRs, strict Python/PostgreSQL and Go/PostgreSQL checks, resolved review threads, and blocks branch deletion/non-fast-forward updates. It has no bypass actors. Its required approving-review count is currently zero; do not describe code-review approval as a required branch rule.

## Approval and blast-radius policy

- Dependency updates are not auto-merged.
- Durable workflow steps that declare a sensitive action pause in `waiting_approval` until an actor explicitly approves or rejects them. The actor ID and decision timestamp are persisted; rejection fails the workflow. These runtime approval gates do not imply that GitHub requires an approving review on every code PR.
- High-impact security remediation must include a regression test and independent CI evidence.
- Automation has read-only repository permissions unless a narrowly scoped workflow explicitly needs more.
- Never expose repository secrets to untrusted pull-request code.
- Never fabricate reviews, bypass branch protection, or use force-push to replace shared history.
- No paid model API, hosted inference, GPU service or third-party autonomous agent is enabled by this policy.

## Failure handling

1. A failing audit opens no automatic remediation commit; inspect the advisory and dependency path.
2. Create a focused PR with the smallest compatible update and regression coverage.
3. Run Python and Go validation independently.
4. For a bad merged update, revert through a new PR and rerun the same gates; never rewrite shared history.
5. The supervised worker treats SIGTERM and SIGINT as safe-stop requests and exits its polling loop cleanly.
5. Record the advisory, affected versions, fix version and verification run in the PR.

## Limits

This is bounded dependency/security automation, not a general autonomous coding agent. Automatic issue triage, patch generation, human-approval orchestration and canary rollback are not enabled. Keep those tasks human-controlled until a permission model, audit trail, budget and stop/rollback behavior are tested.
