"""Measure cold and warm uv installs in clean virtual environments."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements-dev.txt"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "ci" / "uv_install_benchmark.json"


def _run(command: list[str], env: dict[str, str]) -> None:
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def _install(venv_python: Path, env: dict[str, str]) -> float:
    started = time.perf_counter()
    _run(
        [
            "uv",
            "pip",
            "install",
            "--no-progress",
            "--python",
            str(venv_python),
            "-r",
            str(REQUIREMENTS),
        ],
        env,
    )
    return round(time.perf_counter() - started, 4)


def run_benchmark(output_path: Path) -> dict[str, Any]:
    if not REQUIREMENTS.is_file():
        raise FileNotFoundError(REQUIREMENTS)
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="voxpipe-uv-benchmark-") as temporary:
        root = Path(temporary)
        cache_dir = root / "uv-cache"
        cache_dir.mkdir()
        env = os.environ.copy()
        env["UV_CACHE_DIR"] = str(cache_dir)
        env["UV_NO_PROGRESS"] = "1"

        cold_venv = root / "cold-venv"
        warm_venv = root / "warm-venv"
        _run(["uv", "venv", "--python", sys.executable, str(cold_venv)], env)
        _run(["uv", "venv", "--python", sys.executable, str(warm_venv)], env)

        cold_seconds = _install(cold_venv / "bin" / "python", env)
        warm_seconds = _install(warm_venv / "bin" / "python", env)

        uv_version = subprocess.run(
            ["uv", "--version"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    report = {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "uv_version": uv_version,
        "python_version": platform.python_version(),
        "requirements_file": "requirements-dev.txt",
        "requirements_sha256": digest,
        "cache_scope": "fresh isolated cache shared only by the cold and warm installs in this run",
        "install_target": "two independent clean virtual environments",
        "cold_cache_seconds": cold_seconds,
        "warm_cache_seconds": warm_seconds,
        "cold_to_warm_ratio": round(cold_seconds / warm_seconds, 4) if warm_seconds else None,
        "interpretation": "Measures dependency installation with and without a populated uv package cache; not total CI duration.",
    }
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_benchmark(args.output)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
