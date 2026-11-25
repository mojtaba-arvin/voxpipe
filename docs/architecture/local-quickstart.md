# Guided local quickstart

This disposable, single-host stack runs the Go API, Python ingestion worker and PostgreSQL 16 with pgvector. It requires Docker Compose v2 and Python 3.11+ on the host. No hosted STT provider, API key, or paid service is enabled.

## Start and verify

From the repository root:

```sh
python3 scripts/dev_quickstart.py up
```

The runner creates a private `.env` file (mode 0600) with random local database passwords, a random tenant UUID and a random API bearer token. The API receives only the token's SHA-256 digest. Do not commit or share `.env`.

The command builds the API and worker images, starts PostgreSQL, applies migrations, creates the `voxpipe_app` role, starts both processes, generates a two-second mono PCM WAV tone and submits it through the first-party CLI. It waits for the durable ingestion job to reach `completed`. The API is published only on `127.0.0.1:8080`; PostgreSQL is not published to the host.

The generated tone is deliberately not speech. This smoke path verifies upload, media inspection/full decode, object persistence and durable job completion. It does not run STT, create a transcript, or claim speech-recognition quality.

## Use the CLI after startup

Load the generated local token into the current shell:

```sh
set -a
. ./.env
set +a
export VOXPIPE_API_URL=http://127.0.0.1:8080
PYTHONPATH=src python scripts/voxpipe_cli.py status SESSION_UUID
```

Replace `SESSION_UUID` with the identifier printed by the initial ingestion. The API token has only the local demo tenant's ingestion read/write and transcript read scopes.

## Stop and preserve data

```sh
python3 scripts/dev_quickstart.py down
```

This stops and removes containers but preserves PostgreSQL, staging and object-store volumes. Running `up` again reuses the same `.env` and data.

## Security and deployment boundary

- The API is bound to loopback; the database has no host-published port.
- Static digest authentication is enabled only for this local bootstrap. It is not a production identity configuration.
- The bootstrap refuses any database other than `voxpipe_dev` and requires the local PostgreSQL superuser to apply migrations and grants.
- API and worker connect as `voxpipe_app`, verified as `NOSUPERUSER NOBYPASSRLS`; tenant RLS remains enabled.
- Audio staging is a private shared named volume, and the object store is a separate named volume. This remains a single-host filesystem topology.
- No STT model weights are downloaded, no remote provider is selected, and no audio leaves the local stack.
- This quickstart does not configure production TLS termination, OIDC, backups, retention scheduling, high availability, or deployment capacity.

The compose stack is intended for local evaluation and development, not production deployment.
