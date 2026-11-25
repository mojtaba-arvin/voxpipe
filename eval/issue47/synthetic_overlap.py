"""Generate temporary Persian synthetic overlap fixtures; persist annotations only."""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import tempfile
import urllib.request
from array import array
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator

from eval.issue32.templates import generate_conversations

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "eval" / "artifacts" / "issue47"
MANIFEST_SCHEMA = ROOT / "eval" / "issue47" / "overlap_manifest.schema.json"
ANNOTATION_SCHEMA = ROOT / "eval" / "issue47" / "overlap_annotation.schema.json"
SAMPLE_RATE = 22050
DEFAULT_COUNT = 50
DEFAULT_SEED = 20261125

VOICE_MODELS = {
    "customer": {
        "name": "fa_IR-amir-medium-int8",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-fa_IR-amir-medium-int8.tar.bz2",
        "sha256": "b79dbf0b6b9629a36fd541bfda83817ec12e2697557d87b918638445971eac46",
    },
    "agent": {
        "name": "fa_IR-ganji-medium-int8",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-fa_IR-ganji-medium-int8.tar.bz2",
        "sha256": "2ae4658c3a69ef4f92e09d0014f4af31ca6c95e9ae417de3c34544b5e3a98120",
    },
}


def download_verified(url: str, expected_sha256: str, destination: Path) -> int:
    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(url, headers={"User-Agent": "VoxPipe-Synthetic-Evaluation/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    actual = digest.hexdigest()
    if actual != expected_sha256:
        destination.unlink(missing_ok=True)
        raise ValueError(f"SHA-256 mismatch for {destination.name}: expected {expected_sha256}, got {actual}")
    return size


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive, mode="r:bz2") as stream:
        members = stream.getmembers()
        for member in members:
            target = (root / member.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"unsafe archive member path: {member.name}")
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported archive member type: {member.name}")
        stream.extractall(destination, members=members, filter="data")


def _model_paths(root: Path) -> tuple[Path, Path, Path]:
    onnx_files = sorted(root.rglob("*.onnx"))
    tokens_files = sorted(root.rglob("tokens.txt"))
    data_dirs = sorted(path for path in root.rglob("espeak-ng-data") if path.is_dir())
    if not onnx_files or not tokens_files or not data_dirs:
        raise FileNotFoundError(f"incomplete sherpa-onnx VITS model archive: {root}")
    model = next((path for path in onnx_files if "int8" in path.name), onnx_files[0])
    return model, tokens_files[0], data_dirs[0]


def build_tts(model_root: Path):
    import sherpa_onnx

    model, tokens, data_dir = _model_paths(model_root)
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(model),
                data_dir=str(data_dir),
                tokens=str(tokens),
            ),
            num_threads=1,
        ),
    )
    if not config.validate():
        raise ValueError(f"invalid sherpa-onnx TTS configuration for {model_root}")
    return sherpa_onnx.OfflineTts(config)


def calculate_overlap_window(duration_a: float, duration_b: float) -> tuple[float, float, float]:
    if duration_a <= 0 or duration_b <= 0:
        raise ValueError("both utterance durations must be positive")
    delay = min(0.75, duration_a * 0.35)
    overlap_end = min(duration_a, delay + duration_b)
    overlap_duration = overlap_end - delay
    if overlap_duration < 0.2:
        raise ValueError("generated utterances are too short to create a meaningful overlap")
    return round(delay, 6), round(overlap_end, 6), round(overlap_duration, 6)


def mix_samples(
    first: Sequence[float], second: Sequence[float], sample_rate: int, delay_seconds: float
) -> tuple[array, int]:
    if sample_rate <= 0 or len(first) == 0 or len(second) == 0:
        raise ValueError("sample rate and both audio streams must be non-empty")
    delay_samples = round(delay_seconds * sample_rate)
    if delay_samples < 0 or delay_samples >= len(first):
        raise ValueError("second utterance must start before the first utterance ends")
    mixed = array("f", [0.0]) * max(len(first), delay_samples + len(second))
    for index, sample in enumerate(first):
        mixed[index] += float(sample)
    for index, sample in enumerate(second):
        mixed[delay_samples + index] += float(sample)
    peak = max(abs(sample) for sample in mixed)
    if peak == 0:
        raise ValueError("mixed audio is silent")
    if peak > 0.98:
        scale = 0.98 / peak
        for index in range(len(mixed)):
            mixed[index] *= scale
    return mixed, delay_samples


def build_annotation(
    conversation: dict[str, Any],
    duration_customer: float,
    duration_agent: float,
    sample_rate: int = SAMPLE_RATE,
) -> dict[str, Any]:
    delay, overlap_end, overlap_duration = calculate_overlap_window(duration_customer, duration_agent)
    result = {
        "conversation_id": conversation["conversation_id"],
        "scenario": conversation["scenario"],
        "source_kind": "synthetic_template",
        "sample_rate": sample_rate,
        "segments": [
            {
                "role": "customer",
                "voice_model": VOICE_MODELS["customer"]["name"],
                "start_seconds": 0.0,
                "end_seconds": round(duration_customer, 6),
            },
            {
                "role": "agent",
                "voice_model": VOICE_MODELS["agent"]["name"],
                "start_seconds": delay,
                "end_seconds": round(delay + duration_agent, 6),
            },
        ],
        "overlap": {
            "start_seconds": delay,
            "end_seconds": overlap_end,
            "duration_seconds": overlap_duration,
        },
    }
    schema = json.loads(ANNOTATION_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError(f"overlap annotation failed schema validation: {errors[0].message}")
    return result


def _synthesize(tts, text: str):
    audio = tts.generate(text=text, sid=0, speed=1.0)
    samples = audio.samples
    if len(samples) == 0:
        raise RuntimeError("sherpa-onnx returned empty TTS audio")
    return samples, int(audio.sample_rate)


def run_synthesis(
    count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED, output_dir: Path = DEFAULT_OUTPUT_DIR
) -> dict[str, Any]:
    if not 50 <= count <= 100:
        raise ValueError("count must be between 50 and 100")
    records = generate_conversations(count=100, seed=seed)[:count]
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="voxpipe-tts-overlap-") as temporary:
        work = Path(temporary)
        model_paths: dict[str, Path] = {}
        model_sizes: dict[str, int] = {}
        for role, spec in VOICE_MODELS.items():
            archive = work / f"{role}.tar.bz2"
            model_sizes[role] = download_verified(spec["url"], spec["sha256"], archive)
            destination = work / role
            destination.mkdir()
            _safe_extract_tar(archive, destination)
            model_paths[role] = destination

        customer_tts = build_tts(model_paths["customer"])
        agent_tts = build_tts(model_paths["agent"])
        import numpy as np
        import soundfile as sf

        annotations = []
        overlap_seconds = []
        mixed_audio_seconds = 0.0
        audio_dir = work / "mixed"
        audio_dir.mkdir()
        for record in records:
            utterances = {item["speaker_id"]: item["text"] for item in record["utterances"]}
            if set(utterances) != {"customer", "agent"}:
                raise ValueError(f"{record['conversation_id']}: expected customer and agent utterances")
            customer_samples, customer_rate = _synthesize(customer_tts, utterances["customer"])
            agent_samples, agent_rate = _synthesize(agent_tts, utterances["agent"])
            if customer_rate != agent_rate or customer_rate != SAMPLE_RATE:
                raise ValueError(f"{record['conversation_id']}: unexpected TTS sample rates")
            customer_duration = len(customer_samples) / customer_rate
            agent_duration = len(agent_samples) / agent_rate
            delay, _, overlap_duration = calculate_overlap_window(customer_duration, agent_duration)
            mixed, _ = mix_samples(customer_samples, agent_samples, customer_rate, delay)
            mix_path = audio_dir / f"{record['conversation_id']}.wav"
            sf.write(mix_path, np.asarray(mixed, dtype=np.float32), customer_rate, subtype="PCM_16")
            if not mix_path.is_file() or mix_path.stat().st_size <= 44:
                raise RuntimeError(f"{record['conversation_id']}: mixed WAV was not written")
            annotations.append(build_annotation(record, customer_duration, agent_duration, customer_rate))
            overlap_seconds.append(overlap_duration)
            mixed_audio_seconds += len(mixed) / customer_rate

        annotations_payload = "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in annotations
        )
        annotations_path = output_dir / "overlap_annotations.jsonl"
        annotations_path.write_text(annotations_payload, encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "status": "passed",
            "source_kind": "synthetic_template",
            "seed": seed,
            "conversation_count": count,
            "overlap_count": len(annotations),
            "sample_rate": SAMPLE_RATE,
            "tts_runtime": "sherpa-onnx",
            "tts_runtime_version": version("sherpa-onnx"),
            "tts_runtime_license": "Apache-2.0",
            "voice_models": {role: spec["name"] for role, spec in VOICE_MODELS.items()},
            "voice_model_license": "CC0-1.0",
            "voice_asset_sha256": {role: spec["sha256"] for role, spec in VOICE_MODELS.items()},
            "voice_asset_download_bytes": model_sizes,
            "mean_overlap_seconds": round(sum(overlap_seconds) / len(overlap_seconds), 6),
            "total_mixed_audio_seconds": round(mixed_audio_seconds, 6),
            "annotation_file": annotations_path.name,
            "annotation_sha256": hashlib.sha256(annotations_payload.encode("utf-8")).hexdigest(),
            "audio_files_generated": len(annotations),
            "audio_files_persisted": False,
            "audio_artifact_uploaded": False,
            "transcript_text_uploaded": False,
            "warnings": [
                "Audio is generated from synthetic template conversations only; it is not natural speech.",
                "Amir and Ganji are related single-speaker voice variants; role labels are not evidence of distinct-speaker diarization.",
                "No voice cloning, customer data, Common Voice audio or transcript text is used.",
                "Generated WAV files are temporary and deleted after validation; only synthetic timing annotations and aggregate metadata persist.",
            ],
        }
        schema = json.loads(MANIFEST_SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(manifest))
        if errors:
            raise ValueError(f"overlap manifest failed schema validation: {errors[0].message}")
        manifest_path = output_dir / "overlap_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    run_synthesis(args.count, args.seed, args.output_dir)


if __name__ == "__main__":
    main()
