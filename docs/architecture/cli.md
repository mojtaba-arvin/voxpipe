# VoxPipe CLI

The first-party CLI provides an operator/developer path for the authenticated batch-ingestion API without creating a second processing or storage path.

## Configure

Start the Go API and Python worker as described in [Upload API operations](upload-api.md), then configure:

```sh
export VOXPIPE_API_URL=http://127.0.0.1:8080
export VOXPIPE_API_TOKEN='...'
```

The bearer token must be kept out of shell history, source control, and command-line arguments. Use HTTPS for non-loopback endpoints. Upload requires `ingestion:write`, status requires `ingestion:read`, and transcript search requires `transcript:read`.

## Commands

Run from a repository checkout:

```sh
PYTHONPATH=src python scripts/voxpipe.py ingest ./meeting.wav
PYTHONPATH=src python scripts/voxpipe.py ingest ./meeting.wav --wait
PYTHONPATH=src python scripts/voxpipe.py status SESSION_UUID
PYTHONPATH=src python scripts/voxpipe.py search 'جلسه فروش' --language fa
```

Ingestion incrementally computes file size and SHA-256, creates an idempotent session, streams the file to private staging, and finalizes a durable job. The command prints the session/job identifiers. With `--wait`, it polls until completion or a bounded timeout. A wait timeout does not cancel the durable job; inspect it later with `status`.

Search returns tenant-scoped transcript segments with source and timing provenance. It uses the existing Persian-normalized lexical API; it does not claim vector/hybrid ranking. The default API URL is loopback, HTTP request timeout is 30 seconds, and wait timeout is 30 minutes.

## Safety boundaries

- Tenant ID, storage paths, retention policy and provider selection remain server-controlled.
- Authentication and PostgreSQL tenant isolation remain authoritative.
- Redirects are not followed; remote plaintext HTTP is rejected; API responses are capped at 1 MiB.
- Upload streams from disk with a declared content length and is not loaded wholesale into memory.
- Process exit or wait timeout does not cancel an already queued workflow.
- Audio is sent only to the configured VoxPipe API. The CLI does not activate hosted STT or incur provider spend.
