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

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


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


def api_request(
    method: str,
    path: str,
    token: str,
    body: bytes | None = None,
    content_type: str = "application/json",
    idempotency_key: str | None = None,
) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        os.environ["VOXPIPE_API_URL"].rstrip("/") + path,
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"VoxPipe API {method} {path} returned HTTP {error.code}: {detail}") from None
    return json.loads(raw) if raw else {}



def verify_api_identity_cannot_access_durable(
    tenant_id: str, session_id: str, asset_id: str
) -> None:
    env = os.environ
    client = boto3.client(
        "s3",
        endpoint_url=env["VOXPIPE_S3_ENDPOINT_URL"],
        region_name=env["VOXPIPE_S3_REGION"],
        aws_access_key_id=env["VOXPIPE_S3_STAGING_ACCESS_KEY_ID"],
        aws_secret_access_key=env["VOXPIPE_S3_STAGING_SECRET_ACCESS_KEY"],
        config=Config(s3={"addressing_style": env.get("VOXPIPE_S3_ADDRESSING_STYLE", "path")}),
    )
    bucket = env["VOXPIPE_S3_BUCKET"]
    staging_key = f"staging/v1/{tenant_id}/{session_id}/source"
    durable_key = f"tenant/{tenant_id}/assets/{asset_id}/source"
    client.head_object(Bucket=bucket, Key=staging_key)
    for operation in (client.head_object, client.delete_object):
        try:
            operation(Bucket=bucket, Key=durable_key)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code not in {"AccessDenied", "403"}:
                raise
        else:
            raise RuntimeError("API staging identity accessed a durable tenant object")

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
        idempotency_key="vp0045-s3-deployment-e2e-2026",
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
            verify_api_identity_cannot_access_durable(tenant_id, session_id, asset_id)
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
