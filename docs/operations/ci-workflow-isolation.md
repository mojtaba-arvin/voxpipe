# CI workstream isolation

## Goals
- Keep the fast, required Python/PostgreSQL and Go/PostgreSQL validation jobs independent.
- Run model-backed benchmarks only for changes to their own implementation, dependencies, or workflow, and on explicit dispatch.
- Prevent a newer run of one benchmark from cancelling a different benchmark.
- Cancel only superseded runs of the same benchmark on the same ref.

## Workflow policy

| Workflow | Scope | Automatic trigger | Manual trigger |
|---|---|---|---|
| Validate repository | Python + PostgreSQL; Go + PostgreSQL | PRs and pushes to main | Yes |
| FLEURS VAD | VAD adapter, benchmark, VAD dependencies | Matching paths on main | Yes |
| FLEURS Language ID | LID adapter, benchmark, LID dependencies | Matching paths on main | Yes |
| Neyshekar diarization | Diarization adapter, benchmark, LID dependencies | Matching paths on main | Yes |
| Ollama extraction smoke | Structuring adapter, smoke runner, schema | Matching paths on main | Yes |
| Persian FLEURS STT | STT adapter, benchmark, corpus manifest, dependencies | Matching paths on main | Yes |

The model-backed workflows do not run on every pull-request update. The required lightweight repository validation remains the PR gate. Benchmark workflows run after a relevant change reaches main, or can be dispatched independently against a selected ref.

## Concurrency contract

Every expensive benchmark uses a unique workflow-specific concurrency prefix plus `github.ref`, with `cancel-in-progress: true`. This means:
- a new VAD run can supersede an older VAD run on the same ref;
- a VAD run cannot cancel diarization, language-ID, or extraction smoke;
- separate branches and PR refs do not cancel each other;
- unrelated Python and Go validation jobs remain independent.

Do not reuse a generic concurrency group such as `benchmarks-${{ github.ref }}` across these workflows.

## Cost and artifact policy

- Keep model downloads and CPU inference out of the default PR validation path.
- Pin dataset/model revisions and preserve benchmark summaries as artifacts.
- Use bounded sample counts and workflow timeouts for exploratory runs.
- Increase benchmark coverage only when the dataset/consent and acceptance gates require it.
- Do not add hosted inference or paid resources without explicit approval.