#!/usr/bin/env python3
"""Create ephemeral credentials for the disposable S3 deployment E2E."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from pathlib import Path


def main() -> int:
    token = secrets.token_urlsafe(32)
    tenant = str(uuid.uuid4())
    values = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "APP_DB_PASSWORD": secrets.token_urlsafe(40),
        "TENANT_ID": tenant,
        "VOXPIPE_API_TOKEN": token,
        "API_TOKEN_HASHES": hashlib.sha256(token.encode("utf-8")).hexdigest()
        + f":{tenant}:ingestion:read,ingestion:write,transcript:read",
        "VOXPIPE_S3_BUCKET": "voxpipe-staging-e2e",
        "VOXPIPE_S3_REGION": "us-east-1",
        "VOXPIPE_S3_ENDPOINT_URL": "http://127.0.0.1:9000",
        "VOXPIPE_S3_ACCESS_KEY_ID": "voxpipe-ci",
        "VOXPIPE_S3_SECRET_ACCESS_KEY": "voxpipe-ci-secret-password",
        "VOXPIPE_S3_SERVER_SIDE_ENCRYPTION": "AES256",
        "VOXPIPE_S3_ADDRESSING_STYLE": "path",
        "VOXPIPE_S3_MAX_ATTEMPTS": "3",
        "VOXPIPE_S3_CONNECT_TIMEOUT_SECONDS": "5",
        "VOXPIPE_S3_READ_TIMEOUT_SECONDS": "60",
    }
    Path(".env").write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    os.chmod(".env", 0o600)
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as stream:
            stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
