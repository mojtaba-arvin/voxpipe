# Durable workflow journal benchmark

## Purpose

`scripts/benchmark_workflows.py` measures the PostgreSQL workflow journal's
synthetic claim/complete loop. It reports completed workflows per second,
queue-wait percentiles, claim-to-complete percentiles, setup time and failures.
It deliberately uses one no-op step per workflow: results isolate database
journal/coordination overhead and are **not** STT, handler, upload or
end-to-end application throughput.

## Safety

Use a dedicated, disposable PostgreSQL database with the same PostgreSQL major
version, schema migrations, application role, network path and connection
limits intended for the target environment. Set
`VOXPIPE_WORKFLOW_BENCHMARK_DSN` to that database's application-role DSN.
Do not point this variable at production or a shared development database.
The script creates a unique synthetic tenant and retains benchmark rows for
inspection; it does not delete data or apply migrations. Provision migrations
and grants separately before running it.

## Run

From a clean checkout with the optional PostgreSQL driver installed:

```bash
pip install -r requirements-postgres.txt
export VOXPIPE_WORKFLOW_BENCHMARK_DSN='postgresql://...'
PYTHONPATH=src python scripts/benchmark_workflows.py --workflows 1000 --workers 1
PYTHONPATH=src python scripts/benchmark_workflows.py --workflows 1000 --workers 4
PYTHONPATH=src python scripts/benchmark_workflows.py --workflows 1000 --workers 8
PYTHONPATH=src python scripts/benchmark_workflows.py --workflows 5000 --workers 16
```

Each invocation creates a new isolated synthetic tenant. Preserve the JSON
output and record PostgreSQL version, host CPU/RAM, storage class, connection
limits, network topology and competing workload. Run each configuration at
least three times after one warm-up; report median throughput and the range.
For a repeatable matrix, use `scripts/benchmark_workflow_matrix.py`:

```bash
PYTHONPATH=src python scripts/benchmark_workflow_matrix.py \
  --workflows 1000 --workers 1,4,8,16 --repetitions 3 \
  --confirm-disposable \
  --host-label target-like-linux \
  --storage-class 'document the actual volume type' \
  --network-topology 'document the actual database path'
```

The matrix performs one warm-up and three measured runs for each worker count,
then emits raw JSON, median throughput, min/max throughput, median p95 queue
wait and median p95 claim-to-complete. It records PostgreSQL version,
`max_connections`, `shared_buffers`, host platform, CPU count and available
memory; storage class and network topology must be supplied by the operator.
The required `--confirm-disposable` flag is an explicit acknowledgement, not
an automatic safety check. The default total-workflow budget caps the full
matrix at 100,000 created workflows; increase it only deliberately.

Increase worker counts gradually and stop when database saturation, lock waits,
connection exhaustion or error rates rise. A timeout or incomplete drain is
a failed run, not a capacity result. Matrix rows from warm-ups and repetitions
are retained in the disposable database.

## Interpretation

- `throughput_workflows_per_second` is completed one-step journal workflows
  divided by measured drain time; enqueue/setup time is reported separately.
- `queue_wait_ms` measures local enqueue-to-claim delay within this run.
- `claim_to_complete_ms` measures the claim/complete interval around a no-op
  step; it excludes external handler execution.
- `complete=false`, any error, or a duplicate claim invalidates the run.
- Results apply only to the recorded hardware, database and workload.

The benchmark is optional diagnostic tooling only. Its output is not a repository milestone gate, must not be treated as target-host capacity evidence, and must never require a maintainer to run it to complete VP-0034. It does not establish production backup objectives or representative speech-processing capacity.


The benchmark runner is also exercised end-to-end by a PostgreSQL 16 integration test using four independent workers and 24 synthetic workflows. The test asserts a complete drain, unique claims, zero errors and populated measurements; it is skipped when the disposable PostgreSQL test DSN is not configured.
