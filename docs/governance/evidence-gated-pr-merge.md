# Evidence-gated PR review and merge

The implementer may self-review and merge a PR when exact-head code-integration evidence is complete. Self-review is not independent peer review; never fabricate reviewer identity or approval.

## Exact-head merge prerequisites

- Record the work item IDs, intended base branch, and merge-base freshness.
- Review the complete diff, including Python/Go contract parity, database migrations, workflow permissions, dependencies, audio/transcript privacy, and operational changes as applicable.
- Run the applicable make check and make go-check targets and relevant focused regression suites.
- Confirm all required GitHub checks pass on the exact head SHA.
- Resolve material review threads or record each accepted limitation and rationale.
- Update the work ledger with evidence-backed state; do not mark a work item done merely because its PR merged.
- Confirm GitHub reports the PR mergeable.

The implementer may author the review record and merge once these conditions pass. Read-only specialist reviews are useful for high-risk changes but are not universal merge prerequisites. GitHub does not permit the PR author to submit a formal approval of their own PR. Never bypass branch protection, rulesets, required checks, or merge queues.

## Separate runtime and release approvals

This policy changes only code-PR review and merge. Preserve durable workflow actor approvals for declared sensitive runtime actions, plus explicit execution-policy authorization for production deployment, spending, paid resources, credential rotation, access-control changes, and destructive actions. Keep consent, retention, tenant, and data-protection boundaries unchanged.

A merged PR does not imply a work item is done, a benchmark is accepted, a product claim is validated, or a release is ready. Each requires its own recorded acceptance evidence.
