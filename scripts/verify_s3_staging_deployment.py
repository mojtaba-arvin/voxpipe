#!/usr/bin/env python3
"""Exercise a real Go API -> Python queue worker S3-staging deployment."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
import time
import urllib.error
import urllib.request
import wave


def sample_audio() -> bytes:
    sample_rate = 16_000
    frames = bytearray()
    for index in range(sample_rate):
        sample = int(3_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        frames.extend(struct.pack("<h", sample))
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(frames)
    return output.getvalue()


def api_request(method: str, path: str, token: str, body: bytes | None = None, content_type: str = "application/json") -> dict:
    request = urllib.request.Request(
        os.environ["VOXPIPE_API_URL"].rstrip("/") + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"VoxPipe API {method} {path} returned HTTP {error.code}: {detail}") from None
    return json.loads(raw) if raw else {}


def main() -> int:
    token = os.environ["VOXPIPE_API_TOKEN"]
    tenant_id = os.environ["TENANT_ID"]
    payload = sample_audio()
    digest = hashlib.sha256(payload).hexdigest()

    created = api_request(
        "POST",
        "/v1/ingestions",
        token,
        json.dumps({
            "expected_size_bytes": len(payload),
            "expected_sha256": digest,
            "source": {"kind": "file_upload", "declared_by": "vp0045-deployment-e2e"},
        }).encode("utf-8"),
    )
    if "staging_backend" in created or "staging_reference" in created:
        raise RuntimeError("public API leaked private staging metadata")
    session_id = created.get("session_id")
    if not session_id:
        raise RuntimeError("create response did not contain a session_id")

    api_request(
        "PUT",
        f"/v1/ingestions/{session_id}/content",
        token,
        payload,
        "application/octet-stream",
    )
    api_request("POST", f"/v1/ingestions/{session_id}/finalize", token)

    deadline = time.monotonic() + 120
    last_status: dict = {}
    while time.monotonic() < deadline:
        last_status = api_request("GET", f"/v1/ingestions/{session_id}", token)
        state = last_status.get("state")
        if state == "completed":
            asset_id = last_status.get("asset_id")
            if not asset_id:
                raise RuntimeError("completed ingestion did not return an asset_id")
            print(json.dumps({
                "tenant_id": tenant_id,
                "session_id": session_id,
                "asset_id": asset_id,
                "expected_sha256": digest,
                "expected_size_bytes": len(payload),
                "state": state,
            }, sort_keys=True))
            return 0
        if state in {"failed", "cancelled", "expired"}:
            raise RuntimeError(f"ingestion reached terminal state {state}: {last_status}")
        time.sleep(1)

    raise TimeoutError(f"Python queue worker did not complete the ingestion: {last_status}")


if __name__ == "__main__":
    raise SystemExit(main())
