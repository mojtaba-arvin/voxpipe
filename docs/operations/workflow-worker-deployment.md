# Supervised workflow worker deployment

## Scope and safety boundary

The repository includes a systemd template and an environment-driven runtime for one tenant-scoped worker process. The current composition registers only `speech.transcribe` and uses the local Faster-Whisper adapter with `local_files_only=true`. It does not configure Deepgram, send audio to a hosted provider, or enable paid inference.

Run one systemd instance per tenant. The tenant UUID comes from the service instance name and is not accepted from workflow input. PostgreSQL RLS remains the data isolation boundary. The process uses the local filesystem object store, so this topology is single-node: do not run multiple hosts against independent local object-store directories.

## Runtime configuration

Required environment file: `/etc/voxpipe/workflow-worker.env`

```dotenv
VOXPIPE_WORKFLOW_DATABASE_DSN=postgresql://voxpipe_worker:REPLACE_ME@db.internal:5432/voxpipe
```

Protect the file with root ownership and mode 0640 (or stricter); use a dedicated database role without BYPASSRLS. Do not put audio, transcripts, API keys or tenant secrets in command-line arguments. The worker uses a process-local speech circuit breaker by default. To coordinate breaker state across worker processes, explicitly add `VOXPIPE_CIRCUIT_BREAKER_BACKEND=postgres` and `VOXPIPE_DATABASE_DSN=...` to this file after applying migration 0010 and its least-privilege grants; missing or unavailable PostgreSQL coordination fails closed and never falls back to local state. The systemd unit sets bounded worker, polling, storage and local model defaults. Override only after measuring resource limits.

The service expects:
- Repository checkout at `/opt/voxpipe`.
- Virtual environment at `/opt/voxpipe/.venv`.
- `psycopg` and the optional `faster-whisper` runtime installed.
- Faster-Whisper model weights pre-provisioned under `/var/lib/voxpipe/models`; runtime model downloads are disabled.
- Local object files under `/var/lib/voxpipe/objects`, owned by the `voxpipe` service account.
- PostgreSQL migrations applied by the deployment migration job before the worker is started.

## Install and start

Create the service account and state directory:

```bash
sudo useradd --system --home-dir /opt/voxpipe --shell /usr/sbin/nologin voxpipe
sudo install -d -o voxpipe -g voxpipe -m 0700 /var/lib/voxpipe
sudo install -d -o root -g voxpipe -m 0750 /etc/voxpipe
sudo install -o root -g voxpipe -m 0640 /dev/null /etc/voxpipe/workflow-worker.env
sudoedit /etc/voxpipe/workflow-worker.env
```

Install the unit and activate one tenant instance (replace the placeholder with a real tenant UUID):

```bash
sudo install -D -o root -g root -m 0644 deploy/systemd/voxpipe-workflow-worker@.service /etc/systemd/system/voxpipe-workflow-worker@.service
sudo systemctl daemon-reload
sudo systemctl enable --now voxpipe-workflow-worker@TENANT_UUID.service
sudo systemctl status voxpipe-workflow-worker@TENANT_UUID.service
```

Inspect safe lifecycle logs:

```bash
sudo journalctl -u voxpipe-workflow-worker@TENANT_UUID.service -f
```

The worker handles SIGTERM/SIGINT by setting its stop event. Transient PostgreSQL/lease failures are retried in-process with bounded exponential backoff and content-free logs; malformed runtime configuration fails before polling. It finishes the current synchronous step before exiting; `TimeoutStopSec=90` bounds graceful shutdown. If the process is forcibly terminated, the durable lease expires and another worker can reclaim the step. Lease fencing prevents the stale process from completing a reclaimed step.

## Operational constraints

- `Restart=on-failure` and a bounded restart rate are configured; validate behavior on the target distribution with `systemd-analyze verify` and an actual restart drill.
- The worker is one process per tenant and one step at a time. Increase concurrency by adding independently supervised instances only after PostgreSQL and local storage capacity are measured.
- The local object store is not shared storage. Multi-node deployment requires a shared object-storage adapter and coordinated deployment.
- Long-running STT uses cooperative heartbeat renewal. A lost lease fences result persistence; do not shorten lease or stop timeouts without measuring inference duration.
- The service has no automatic database migration step. Schema changes must be applied and verified before starting new workers.
- This template does not prove production readiness. Backup/restore, load, host restart, storage durability, alert delivery and staging SLO drills remain separate acceptance gates.

## Rollback

Stop and disable the tenant instance:

```bash
sudo systemctl disable --now voxpipe-workflow-worker@TENANT_UUID.service
```

No workflow rows or source assets are deleted by stopping the worker. Resume only after the deployment or configuration issue is corrected; expired leases are recovered through the durable workflow repository.
