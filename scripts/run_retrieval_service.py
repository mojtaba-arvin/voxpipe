"""Private internal HTTP adapter for local-only hybrid retrieval."""
from __future__ import annotations

import hmac
import json
import os
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from voxpipe.retrieval.runtime import LocalHybridRetrievalRuntime

MAX_BODY_BYTES = 64 * 1024


def _uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ValueError(f"{name} must be a UUID") from None


def _validate_search(payload: dict) -> dict:
    allowed = {
        "tenant_id", "query", "limit", "transcript_id", "asset_id", "revision",
        "language", "speaker_id", "start_ms", "end_ms",
    }
    if set(payload) - allowed or not {"tenant_id", "query"}.issubset(payload):
        raise ValueError("search request contains unsupported or missing fields")
    result = dict(payload)
    result["tenant_id"] = _uuid(payload["tenant_id"], "tenant_id")
    query = payload["query"]
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 256:
        raise ValueError("query must contain between 1 and 256 characters")
    result["query"] = query.strip()
    limit = payload.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    result["limit"] = limit
    for name in ("transcript_id", "asset_id", "speaker_id"):
        if name in payload:
            result[name] = _uuid(payload[name], name)
    if "revision" in payload and (type(payload["revision"]) is not int or payload["revision"] < 1):
        raise ValueError("revision must be a positive integer")
    if "language" in payload and (
        not isinstance(payload["language"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{1,34}", payload["language"])
    ):
        raise ValueError("language filter is invalid")
    for name, minimum in (("start_ms", 0), ("end_ms", 1)):
        if name in payload and (type(payload[name]) is not int or payload[name] < minimum):
            raise ValueError(f"{name} is outside the allowed range")
    if "start_ms" in result and "end_ms" in result and result["end_ms"] <= result["start_ms"]:
        raise ValueError("end_ms must be greater than start_ms")
    return result


def _validate_index(payload: dict) -> dict:
    if set(payload) - {"tenant_id", "transcript_id", "revision"} or not {
        "tenant_id", "transcript_id"
    }.issubset(payload):
        raise ValueError("index request contains unsupported or missing fields")
    result = {
        "tenant_id": _uuid(payload["tenant_id"], "tenant_id"),
        "transcript_id": _uuid(payload["transcript_id"], "transcript_id"),
    }
    if "revision" in payload:
        if type(payload["revision"]) is not int or payload["revision"] < 1:
            raise ValueError("revision must be a positive integer")
        result["revision"] = payload["revision"]
    return result


def build_runtime():
    model = os.environ.get("VOXPIPE_EMBEDDING_MODEL", "").strip()
    version = os.environ.get("VOXPIPE_EMBEDDING_MODEL_VERSION", "").strip()
    if not model and not version:
        return None
    if not model or not version:
        raise SystemExit("embedding model and immutable model version must be configured together")
    dsn = os.environ.get("VOXPIPE_RETRIEVAL_DATABASE_DSN", "").strip()
    token = os.environ.get("VOXPIPE_RETRIEVAL_INTERNAL_TOKEN", "").strip()
    if not dsn or len(token) < 32:
        raise SystemExit("retrieval database DSN and a 32-character internal token are required")
    return LocalHybridRetrievalRuntime(
        database_dsn=dsn,
        model_path=os.environ.get("VOXPIPE_EMBEDDING_MODEL_PATH", "/models"),
        model=model,
        model_version=version,
        query_prefix=os.environ.get("VOXPIPE_EMBEDDING_QUERY_PREFIX", ""),
        document_prefix=os.environ.get("VOXPIPE_EMBEDDING_DOCUMENT_PREFIX", ""),
    )


class RetrievalHandler(BaseHTTPRequestHandler):
    runtime = None
    internal_token = ""

    def log_message(self, _format, *_args):
        # Query and transcript content must never enter default HTTP access logs.
        return

    def _respond(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path not in {"/internal/v1/search", "/internal/v1/index"}:
            self._respond(404, {"code": "NOT_FOUND"})
            return
        supplied = self.headers.get("X-VoxPipe-Retrieval-Token", "")
        if not self.internal_token or not hmac.compare_digest(supplied, self.internal_token):
            self._respond(401, {"code": "UNAUTHENTICATED"})
            return
        if self.runtime is None:
            self._respond(503, {"code": "RETRIEVAL_NOT_CONFIGURED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY_BYTES:
                raise ValueError("request body size is invalid")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            if self.path.endswith("/search"):
                request = _validate_search(payload)
                result = self.runtime.search(request.pop("tenant_id"), request)
            else:
                request = _validate_index(payload)
                tenant_id = request.pop("tenant_id")
                transcript_id = request.pop("transcript_id")
                result = self.runtime.index_transcript(
                    tenant_id, transcript_id, request.get("revision"),
                )
            self._respond(200, result)
        except LookupError:
            self._respond(404, {"code": "TRANSCRIPT_NOT_FOUND"})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._respond(400, {"code": "INVALID_RETRIEVAL_REQUEST"})
        except Exception:
            self._respond(500, {"code": "RETRIEVAL_SERVICE_ERROR"})


def main() -> int:
    RetrievalHandler.runtime = build_runtime()
    RetrievalHandler.internal_token = os.environ.get("VOXPIPE_RETRIEVAL_INTERNAL_TOKEN", "").strip()
    address = os.environ.get("VOXPIPE_RETRIEVAL_LISTEN_ADDR", ":8090")
    host, separator, raw_port = address.rpartition(":")
    if not separator:
        raise SystemExit("VOXPIPE_RETRIEVAL_LISTEN_ADDR must use host:port form")
    try:
        port = int(raw_port)
    except ValueError:
        raise SystemExit("retrieval listen port must be an integer") from None
    if not 1 <= port <= 65535:
        raise SystemExit("retrieval listen port is outside the valid range")
    server = ThreadingHTTPServer((host or "0.0.0.0", port), RetrievalHandler)
    print("Local retrieval service ready; model configured=" + str(RetrievalHandler.runtime is not None))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
