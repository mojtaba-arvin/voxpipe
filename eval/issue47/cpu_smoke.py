"""CPU-only Qwen3-ASR runtime smoke; this is not a Persian quality benchmark."""
from __future__ import annotations

import argparse
import json
import os
import platform
import tempfile
import time
import urllib.request
import wave
from importlib.metadata import version
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "Qwen/Qwen3-ASR-0.6B"
MODEL_REVISION = "5eb144179a02acc5e5ba31e748d22b0cf3e303b0"
MODEL_LICENSE = "Apache-2.0"
SAMPLE_URL = "https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_en.wav"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue47" / "cpu_smoke.json"
OUTPUT_SCHEMA = ROOT / "eval" / "issue47" / "cpu_smoke.schema.json"


def audio_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as stream:
        frames = stream.getnframes()
        rate = stream.getframerate()
    if rate <= 0 or frames <= 0:
        raise ValueError("sample audio has invalid frame count or sample rate")
    return frames / rate


def build_report(
    *,
    model_download_seconds: float,
    model_size_bytes: int,
    model_load_seconds: float,
    inference_seconds: float,
    audio_seconds: float,
    cpu_count: int,
    torch_num_threads: int,
    detected_language: str,
    transcript: str,
    torch_version: str,
    qwen_asr_version: str,
) -> dict[str, Any]:
    if min(model_load_seconds, inference_seconds, audio_seconds) <= 0:
        raise ValueError("model load, inference and audio durations must be positive")
    if model_download_seconds < 0 or model_size_bytes <= 0:
        raise ValueError("model download duration and model size must be valid")
    if cpu_count < 1 or not detected_language.strip() or not transcript.strip():
        raise ValueError("CPU count, detected language and transcript must be non-empty")
    result = {
        "schema_version": 1,
        "status": "passed",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_license": MODEL_LICENSE,
        "device": "cpu",
        "cpu_count": cpu_count,
        "torch_num_threads": torch_num_threads,
        "model_download_seconds": round(model_download_seconds, 4),
        "model_size_bytes": model_size_bytes,
        "evaluation_language": "English",
        "detected_language": detected_language,
        "source_kind": "public_model_card_sample",
        "sample_source_url": SAMPLE_URL,
        "audio_duration_seconds": round(audio_seconds, 4),
        "model_load_seconds": round(model_load_seconds, 4),
        "inference_seconds": round(inference_seconds, 4),
        "real_time_factor": round(inference_seconds / audio_seconds, 6),
        "transcript_characters": len(transcript),
        "timestamps_returned": False,
        "torch_version": torch_version,
        "qwen_asr_version": qwen_asr_version,
        "python_version": platform.python_version(),
        "warnings": [
            "This smoke uses the model publisher's English sample to measure CPU feasibility.",
            "It does not measure Persian WER, alignment, natural-conversation quality or production accuracy.",
            "Qwen3-ForcedAligner does not list Persian; this smoke intentionally emits no timestamps.",
        ],
    }
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError(f"CPU smoke report failed schema validation: {errors[0].message}")
    return result


def _download_sample(destination: Path) -> None:
    request = urllib.request.Request(SAMPLE_URL, headers={"User-Agent": "VoxPipe-Evaluation/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def run_smoke(output_path: Path, cache_dir: Path, sample_path: Path | None = None) -> dict[str, Any]:
    import torch
    from huggingface_hub import snapshot_download
    from qwen_asr import Qwen3ASRModel

    torch.set_num_threads(max(1, min(os.cpu_count() or 1, 4)))
    download_started = time.perf_counter()
    model_dir = Path(
        snapshot_download(
            repo_id=MODEL_ID,
            revision=MODEL_REVISION,
            cache_dir=str(cache_dir),
        )
    )
    model_download_seconds = time.perf_counter() - download_started
    model_size_bytes = sum(item.stat().st_size for item in model_dir.rglob("*") if item.is_file())
    os.environ["HF_HUB_OFFLINE"] = "1"

    with tempfile.TemporaryDirectory(prefix="voxpipe-qwen-smoke-") as temporary:
        audio_path = sample_path or (Path(temporary) / "asr_en.wav")
        if sample_path is None:
            _download_sample(audio_path)
        duration = audio_duration_seconds(audio_path)

        started = time.perf_counter()
        model = Qwen3ASRModel.from_pretrained(
            str(model_dir),
            dtype=torch.float32,
            device_map="cpu",
            max_inference_batch_size=1,
            max_new_tokens=128,
        )
        model_load_seconds = time.perf_counter() - started

        started = time.perf_counter()
        results = model.transcribe(audio=str(audio_path), language="English")
        inference_seconds = time.perf_counter() - started
        if not results:
            raise RuntimeError("Qwen3-ASR returned no transcription result")
        transcript = str(results[0].text or "")
        detected_language = str(results[0].language or "")

    report = build_report(
        model_download_seconds=model_download_seconds,
        model_size_bytes=model_size_bytes,
        model_load_seconds=model_load_seconds,
        inference_seconds=inference_seconds,
        audio_seconds=duration,
        cpu_count=os.cpu_count() or 1,
        torch_num_threads=torch.get_num_threads(),
        detected_language=detected_language,
        transcript=transcript,
        torch_version=torch.__version__,
        qwen_asr_version=version("qwen-asr"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache" / "huggingface")
    parser.add_argument("--sample", type=Path)
    args = parser.parse_args()
    run_smoke(args.output, args.cache_dir, args.sample)


if __name__ == "__main__":
    main()
