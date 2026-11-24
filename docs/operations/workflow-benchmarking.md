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
Increase worker counts gradually and stop when database saturation, lock waits,
connection exhaustion or error rates rise. A timeout or incomplete drain is
a failed run, not a capacity result.

## Interpretation

- `throughput_workflows_per_second` is completed one-step journal workflows
  divided by measured drain time; enqueue/setup time is reported separately.
- `queue_wait_ms` measures local enqueue-to-claim delay within this run.
- `claim_to_complete_ms` measures the claim/complete interval around a no-op
  step; it excludes external handler execution.
- `complete=false`, any error, or a duplicate claim invalidates the run.
- Results apply only to the recorded hardware, database and workload.

The benchmark does not replace a target-host systemd restart/shutdown drill,
production backup objectives, or a representative speech-processing load test.
