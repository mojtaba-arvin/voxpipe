# Dev Speedups

## 1. Local smoke + pre-commit

Developers should not push every change to CI. Locally:

- ruff (millisecond lint)
- \`pytest -m "not slow" -x --timeout=60\` (fast tests only, stop
  on first failure)
- scripts/smoke_eval.py: 3 conversations through #32 + #48 +
  metrics, signals "pipeline broken or not" in 30 seconds.

## 2. dry-run mode

Every eval script supports \`--dry-run\`: loads 1–2 samples,
validates schema, paths, and error handling without any inference.
Allows debugging logic without model weights.

## 3. PR small-sample gate + nightly full

- PR-triggered CI: only \`unit-test\` (xdist) and \`smoke-eval\`
  (3 samples).
- Nightly or \`workflow_dispatch\`: full \`eval-32\`, \`eval-48\`,
  \`eval-47\`.

PR feedback drops from hours to minutes; full eval does not block
daily development.

## 4. Physical isolation

    eval/
    ├── issue32/   # group A
    ├── issue48/   # group B (CORPUS_PATH injection)
    └── issue47/   # group C

Group B receives corpus path via \`CORPUS_PATH\` env var; points to
placeholder during dev, to group A output when ready. Group B
developer never waits for group A CI.
