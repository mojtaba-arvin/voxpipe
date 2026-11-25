"""Start the local VoxPipe stack and ingest a generated audio fixture."""
from __future__ import annotations
import argparse
import hashlib
import math
import os
import secrets
import socket
import stat
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SAMPLE_PATH = ROOT / ".voxpipe" / "sample-tone.wav"

def ensure_environment_file(path: Path = ENV_PATH) -> dict[str, str]:
    if path.exists():
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
        required = {"POSTGRES_PASSWORD", "APP_DB_PASSWORD", "TENANT_ID", "VOXPIPE_API_TOKEN", "API_TOKEN_HASHES"}
        if not required.issubset(values):
            raise RuntimeError(".env exists but is not a complete VoxPipe quickstart environment; refusing to overwrite it")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError(".env permissions are too broad; run chmod 600 .env")
        return values
    token = secrets.token_urlsafe(32)
    tenant_id = str(uuid.uuid4())
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    values = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "APP_DB_PASSWORD": secrets.token_urlsafe(32),
        "TENANT_ID": tenant_id,
        "VOXPIPE_API_TOKEN": token,
        "API_TOKEN_HASHES": f"{digest}:{tenant_id}:ingestion:read,ingestion:write,transcript:read",
    }
    content = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    return values

def create_sample_audio(path: Path = SAMPLE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    sample_rate = 16_000
    frames = bytearray()
    for index in range(sample_rate * 2):
        sample = int(4_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(frames)
    return path

def wait_for_api(timeout_seconds: float = 180.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 8080), timeout=1):
                return
        except OSError:
            time.sleep(1)
    raise TimeoutError("VoxPipe API did not accept local connections within the startup timeout")

def compose(*arguments: str) -> None:
    subprocess.run(["docker", "compose", "-f", "compose.yaml", *arguments], cwd=ROOT, check=True)

def run_up() -> int:
    values = ensure_environment_file()
    sample = create_sample_audio()
    compose("up", "--build", "--detach")
    wait_for_api()
    environment = os.environ.copy()
    environment.update({
        "VOXPIPE_API_URL": "http://127.0.0.1:8080",
        "VOXPIPE_API_TOKEN": values["VOXPIPE_API_TOKEN"],
        "PYTHONPATH": str(ROOT / "src"),
    })
    subprocess.run([
        sys.executable, "scripts/voxpipe_cli.py", "ingest", str(sample),
        "--wait", "--poll-interval", "1", "--wait-timeout", "180", "--upload-timeout", "60",
    ], cwd=ROOT, env=environment, check=True)
    print("\nVoxPipe is running at http://127.0.0.1:8080")
    print("The demo audio is a generated 440 Hz tone; this quickstart verifies ingestion, not speech recognition.")
    print("Run python scripts/dev_quickstart.py down to stop containers while preserving local data.")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local VoxPipe API, worker and PostgreSQL quickstart.")
    parser.add_argument("command", choices=("up", "down"))
    args = parser.parse_args()
    if args.command == "down":
        if not ENV_PATH.exists():
            print("No .env found; the quickstart stack has not been initialized.")
            return 0
        compose("down")
        print("Containers stopped. PostgreSQL and object volumes were preserved.")
        return 0
    try:
        return run_up()
    except subprocess.CalledProcessError as error:
        return error.returncode or 1

if __name__ == "__main__":
    raise SystemExit(main())
