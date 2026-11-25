"""First-party VoxPipe CLI for batch ingestion, status, and transcript search."""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path
from typing import Any, BinaryIO

class APIError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message); self.code, self.status = code, status

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None

class APIClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname: raise ValueError("API URL must be absolute HTTP(S)")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}: raise ValueError("HTTPS is required for non-loopback API URLs")
        if parsed.username or parsed.password or parsed.query or parsed.fragment: raise ValueError("API URL must not contain credentials, query, or fragment")
        if not token: raise ValueError("VOXPIPE_API_TOKEN is required")
        if timeout <= 0: raise ValueError("timeout must be positive")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None,
                body: BinaryIO | None = None, content_length: int | None = None,
                idempotency_key: str | None = None, request_timeout: float | None = None) -> dict[str, Any]:
        if not path.startswith("/") or path.startswith("//"): raise ValueError("API path must be absolute")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"; data = json.dumps(payload, separators=(",", ":")).encode()
        elif body is not None:
            if content_length is None or content_length < 0: raise ValueError("content_length is required for uploads")
            headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(content_length)})
            data = body
        if idempotency_key: headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=request_timeout or self.timeout) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576: raise APIError("RESPONSE_TOO_LARGE", "API response exceeded 1 MiB")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            try: detail = json.loads(exc.read(65_536))
            except (json.JSONDecodeError, UnicodeDecodeError): detail = {}
            raise APIError(detail.get("code", "UPSTREAM_HTTP_ERROR"), detail.get("message", "API rejected the request"), exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise APIError("UPSTREAM_UNAVAILABLE", "VoxPipe API is unavailable") from None

def sha256_file(path: Path) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024): size += len(chunk); digest.update(chunk)
    return size, digest.hexdigest()

def ingest(client: APIClient, path: Path, source_kind: str, external_reference: str | None,
           wait: bool, poll_interval: float, timeout: float, upload_timeout: float = 1800.0) -> dict[str, Any]:
    if not path.is_file(): raise ValueError("audio path must be a regular file")
    size, digest = sha256_file(path)
    if size < 1: raise ValueError("audio file must not be empty")
    payload = {"expected_size_bytes": size, "expected_sha256": digest, "source": {"kind": source_kind}}
    if external_reference: payload["source"]["external_reference"] = external_reference
    created = client.request("POST", "/v1/ingestions", payload=payload, idempotency_key=uuid.uuid4().hex)
    session_id = created.get("session_id")
    if not isinstance(session_id, str): raise APIError("INVALID_API_RESPONSE", "Create response omitted session_id")
    with path.open("rb") as stream:
        client.request("PUT", f"/v1/ingestions/{session_id}/content", body=stream, content_length=size, request_timeout=upload_timeout)
    accepted = client.request("POST", f"/v1/ingestions/{session_id}/finalize")
    result = {"session_id": session_id, "job": accepted, "status": "queued"}
    if not wait: return result
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.request("GET", f"/v1/ingestions/{session_id}")
        state = status.get("state")
        if state == "completed": return {"session_id": session_id, "status": state, "result": status}
        if state in {"failed", "cancelled", "expired"}: raise APIError("INGESTION_" + str(state).upper(), "Ingestion ended in state: " + str(state))
        time.sleep(poll_interval)
    raise APIError("WAIT_TIMEOUT", f"Ingestion {session_id} did not complete before timeout")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voxpipe", description="Upload audio and work with VoxPipe.")
    parser.add_argument("--api-url", default=os.environ.get("VOXPIPE_API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--timeout", type=float, default=30.0)
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("ingest", help="Upload and enqueue an audio file")
    upload.add_argument("file", type=Path)
    upload.add_argument("--source-kind", choices=("file_upload","device_capture","telephony","live_stream","import"), default="file_upload")
    upload.add_argument("--external-reference")
    upload.add_argument("--wait", action="store_true")
    upload.add_argument("--poll-interval", type=float, default=2.0)
    upload.add_argument("--wait-timeout", type=float, default=1800.0)
    upload.add_argument("--upload-timeout", type=float, default=1800.0)
    status = commands.add_parser("status", help="Read an ingestion session status")
    status.add_argument("session_id")
    search = commands.add_parser("search", help="Search transcript segments")
    search.add_argument("query"); search.add_argument("--limit", type=int, default=20); search.add_argument("--offset", type=int, default=0)
    search.add_argument("--language"); search.add_argument("--asset-id"); search.add_argument("--transcript-id")
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = APIClient(args.api_url, os.environ.get("VOXPIPE_API_TOKEN", ""), args.timeout)
        if args.command == "ingest":
            if args.poll_interval <= 0 or args.wait_timeout <= 0 or args.upload_timeout <= 0: raise ValueError("poll interval, wait timeout, and upload timeout must be positive")
            result = ingest(client, args.file, args.source_kind, args.external_reference, args.wait, args.poll_interval, args.wait_timeout, args.upload_timeout)
        elif args.command == "status":
            result = client.request("GET", f"/v1/ingestions/{uuid.UUID(args.session_id)}")
        else:
            if not args.query.strip() or len(args.query) > 256: raise ValueError("query must contain between 1 and 256 characters")
            if not 1 <= args.limit <= 100 or not 0 <= args.offset <= 10000: raise ValueError("limit or offset is outside allowed bounds")
            params = {"q": args.query.strip(), "limit": args.limit, "offset": args.offset}
            for key in ("language", "asset_id", "transcript_id"):
                value = getattr(args, key)
                if value: params[key] = str(uuid.UUID(value)) if key.endswith("_id") else value
            result = client.request("GET", "/v1/transcripts/search?" + urllib.parse.urlencode(params))
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (APIError, ValueError, OSError) as exc:
        code = exc.code if isinstance(exc, APIError) else "INVALID_ARGUMENT"
        print(json.dumps({"error": code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr); return 1

if __name__ == "__main__": raise SystemExit(main())
