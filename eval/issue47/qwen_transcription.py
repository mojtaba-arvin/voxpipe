"""Bounded Qwen3-ASR Persian evaluation over a publisher-provided full archive.

Audio, reference sentences and hypotheses are temporary. Only aggregate metrics,
failure counts and pinned model/dataset provenance are written to the report.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import platform
import resource
import shutil
import tarfile
import tempfile
import time
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DATASET_ID = "cmu5xazgy00finq074vaqo1b3"
DATASET_VERSION = "cv-corpus-27.0-2026-09-11"
MODEL_ID = "Qwen/Qwen3-ASR-0.6B-hf"
MODEL_REVISION = "40946090cac63fc01d723a83174e16515073d7c0"
MODEL_LICENSE = "Apache-2.0"
LID_MODEL_ID = "speechbrain/lang-id-voxlingua107-ecapa"
LID_MODEL_REVISION = "0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9"
LID_MODEL_LICENSE = "Apache-2.0"
LID_CONFIDENCE_THRESHOLD = 0.0
FORCED_LANGUAGE = "Persian"
MAX_NEW_TOKENS = 256
DO_SAMPLE = False
BATCH_SIZE = 1
LID_SAMPLE_RATE = 16000
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue47" / "qwen3_asr_persian.json"
OUTPUT_SCHEMA = ROOT / "eval" / "issue47" / "qwen3_asr_persian.schema.json"
MAX_ARCHIVE_BYTES = 12 * 1024 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 500_000
MAX_AUDIO_FILE_BYTES = 32 * 1024 * 1024
MAX_CLIPS = 50
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
_TRANSLATION = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).translate(_TRANSLATION)
    chars = []
    for char in value:
        category = unicodedata.category(char)
        if category.startswith("P") or category.startswith("S") or category == "Mn":
            chars.append(" ")
        else:
            chars.append(char)
    return " ".join("".join(chars).split())


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for i, ref_token in enumerate(reference, 1):
        current = [i]
        for j, hyp_token in enumerate(hypothesis, 1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (ref_token != hyp_token),
            ))
        previous = current
    return previous[-1]


def corpus_error_rates(references: list[str], hypotheses: list[str]) -> dict[str, Any]:
    if not references or len(references) != len(hypotheses):
        raise ValueError("references and hypotheses must be non-empty and have equal length")
    word_errors = char_errors = reference_words = reference_chars = 0
    for reference, hypothesis in zip(references, hypotheses, strict=True):
        ref = normalize_text(reference)
        hyp = normalize_text(hypothesis)
        ref_words, hyp_words = ref.split(), hyp.split()
        if not ref_words or not ref:
            raise ValueError("reference sentences must contain non-whitespace text")
        word_errors += edit_distance(ref_words, hyp_words)
        char_errors += edit_distance(list(ref.replace(" ", "")), list(hyp.replace(" ", "")))
        reference_words += len(ref_words)
        reference_chars += len(ref.replace(" ", ""))
    return {
        "word_errors": word_errors,
        "character_errors": char_errors,
        "reference_words": reference_words,
        "reference_characters": reference_chars,
        "wer": round(word_errors / reference_words, 6),
        "cer": round(char_errors / reference_chars, 6),
    }


def _read_archive_records(archive: tarfile.TarFile) -> tuple[dict[str, tarfile.TarInfo], list[tuple[tarfile.TarInfo, str]]]:
    members = archive.getmembers()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValueError("Common Voice archive exceeds the 500,000-member safety limit")
    total_size = 0
    audio_by_basename: dict[str, tarfile.TarInfo] = {}
    metadata_members = []
    for member in members:
        if member.issym() or member.islnk() or member.isdev():
            raise ValueError("sample archive contains unsupported link or device entries")
        if not member.isfile():
            continue
        total_size += member.size
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Common Voice archive exceeds the 128 GiB uncompressed safety limit")
        basename = PurePosixPath(member.name).name
        suffix = PurePosixPath(basename).suffix.lower()
        if suffix in AUDIO_SUFFIXES:
            if basename in audio_by_basename:
                raise ValueError("sample archive contains ambiguous duplicate audio basenames")
            if member.size <= 0 or member.size > MAX_AUDIO_FILE_BYTES:
                raise ValueError("sample audio file is empty or exceeds the 32 MiB safety limit")
            audio_by_basename[basename] = member
        elif suffix == ".tsv" and PurePosixPath(member.name).name in {"validated.tsv", "train.tsv", "sample.tsv"}:
            metadata_members.append(member)
    if not metadata_members:
        raise ValueError("sample archive contains no supported Common Voice TSV metadata")
    metadata_members.sort(key=lambda member: {
        "validated.tsv": 0, "sample.tsv": 1, "train.tsv": 2
    }.get(PurePosixPath(member.name).name, 3))
    return audio_by_basename, [(member, "") for member in metadata_members]


def _load_reference_rows(archive: tarfile.TarFile, metadata_member: tarfile.TarInfo,
                         audio_by_basename: dict[str, tarfile.TarInfo], max_clips: int) -> list[tuple[tarfile.TarInfo, str]]:
    stream = archive.extractfile(metadata_member)
    if stream is None:
        raise ValueError("unable to read Common Voice metadata member")
    rows: list[tuple[tarfile.TarInfo, str]] = []
    with io.TextIOWrapper(stream, encoding="utf-8", newline="") as text_stream:
        reader = csv.reader(text_stream, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("Common Voice TSV is empty") from exc
        try:
            path_index = header.index("path")
            sentence_index = header.index("sentence")
        except ValueError as exc:
            raise ValueError("Common Voice TSV must contain path and sentence columns") from exc
        for row in reader:
            if max(path_index, sentence_index) >= len(row):
                continue
            audio_name = PurePosixPath(row[path_index]).name
            sentence = row[sentence_index].strip()
            member = audio_by_basename.get(audio_name)
            if member is None or not sentence:
                continue
            rows.append((member, sentence))
            if len(rows) >= max_clips:
                break
    if not rows:
        raise ValueError("sample archive has no matching audio/reference rows")
    return rows


def build_report(*, metrics: dict[str, Any] | None, sample_count: int, requested_sample_count: int,
                 audio_seconds: float, inference_seconds: float, peak_rss_bytes: int,
                 torch_version: str, transformers_version: str,
                 language_id_failures: dict[str, int],
                 transcription_failures: dict[str, int],
                 language_id_confidence_threshold: float = LID_CONFIDENCE_THRESHOLD,
                 compute_type: str = "float32") -> dict[str, Any]:
    if not 0 <= sample_count <= MAX_CLIPS or not 1 <= requested_sample_count <= MAX_CLIPS:
        raise ValueError("sample counts must remain within the 50-clip experiment bound")
    if sample_count + sum(language_id_failures.values()) + sum(transcription_failures.values()) != requested_sample_count:
        raise ValueError("accepted and failed sample counts must equal the requested sample count")
    if audio_seconds <= 0 or inference_seconds < 0 or peak_rss_bytes <= 0:
        raise ValueError("audio duration, inference duration and peak RSS must be valid")
    if not 0.0 <= language_id_confidence_threshold <= 1.0:
        raise ValueError("language-ID confidence threshold must be between 0 and 1")
    status = "passed" if sample_count == requested_sample_count else "failed"
    if status == "passed" and metrics is None:
        raise ValueError("a complete Persian sample requires aggregate metrics")
    result = {
        "schema_version": 1,
        "status": status,
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "source_kind": "scripted_read_speech",
        "locale": "fa",
        "task": "ASR",
        "license": "CC0-1.0",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_license": MODEL_LICENSE,
        "language_id_model_id": LID_MODEL_ID,
        "language_id_model_revision": LID_MODEL_REVISION,
        "language_id_model_license": LID_MODEL_LICENSE,
        "language_id_confidence_threshold": language_id_confidence_threshold,
        "language_id_supported_languages": ["ar", "fa"],
        "language_id_threshold_calibration": {
            "dataset_id": "google/fleurs",
            "dataset_revision": "6989e5bd2dc37546a93ae5d857bc04325e6a3738",
            "validation_run_id": 45170396629,
            "min_selective_accuracy": 0.99,
            "min_coverage": 0.50,
            "selected_threshold": 0.0,
        },
        "language_id_failures": language_id_failures,
        "transcription_failures": transcription_failures,
        "language_strategy": "independent_voxlingua107_gate_then_forced_persian",
        "forced_language": FORCED_LANGUAGE,
        "max_new_tokens": MAX_NEW_TOKENS,
        "do_sample": DO_SAMPLE,
        "batch_size": BATCH_SIZE,
        "language_id_sample_rate": LID_SAMPLE_RATE,
        "device": "cpu",
        "compute_type": compute_type,
        "sample_count": sample_count,
        "requested_sample_count": requested_sample_count,
        "rejected_sample_count": requested_sample_count - sample_count,
        "audio_duration_seconds": round(audio_seconds, 4),
        "inference_seconds": round(inference_seconds, 6),
        "real_time_factor": round(inference_seconds / audio_seconds, 6),
        "word_errors": metrics["word_errors"] if metrics is not None else None,
        "character_errors": metrics["character_errors"] if metrics is not None else None,
        "reference_words": metrics["reference_words"] if metrics is not None else None,
        "reference_characters": metrics["reference_characters"] if metrics is not None else None,
        "wer": metrics["wer"] if metrics is not None else None,
        "cer": metrics["cer"] if metrics is not None else None,
        "peak_rss_bytes": peak_rss_bytes,
        "torch_version": torch_version,
        "transformers_version": transformers_version,
        "python_version": platform.python_version(),
        "transcript_text_uploaded": False,
        "audio_uploaded": False,
        "speaker_ids_used": False,
        "warnings": [
            "Common Voice is scripted read-speech, not natural conversation.",
            "WER/CER describe only this bounded 50-clip evaluation from the full publisher archive and are not human-gold or production-quality evidence.",
            "Audio, reference sentences and model hypotheses are temporary; only aggregate metrics and pinned provenance are reported.",
            "An independent pinned VoxLingua107 audio-language gate must identify Persian at or above the configured confidence threshold before Qwen inference.",
            "Qwen is conditioned with language=Persian only after the independent language gate; language forcing is not treated as evidence that the audio itself is Persian.",
            "Arabic, low-confidence, other/undetermined language-gate failures and empty transcription outputs are counted separately.",
            "If any requested clip fails language validation or produces an empty hypothesis, the experiment is failed and WER/CER are null rather than computed on a reduced or mixed-language sample.",
            "Qwen3-ForcedAligner does not list Persian; this ASR report contains no timestamps or alignment claims.",
        ],
    }
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result)
    return result


def run_evaluation(archive_path: Path, output_path: Path, cache_dir: Path,
                   max_clips: int = MAX_CLIPS, model: Any | None = None,
                   processor: Any | None = None,
                   model_loader: Callable[[Path], Any] | None = None,
                   language_identifier: Any | None = None,
                   language_identifier_loader: Callable[[Path], Any] | None = None,
                   language_id_confidence_threshold: float = LID_CONFIDENCE_THRESHOLD) -> dict[str, Any]:
    if not archive_path.is_file() or archive_path.stat().st_size <= 0:
        raise ValueError("Common Voice archive must be an existing non-empty file")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Common Voice archive exceeds the 12 GiB safety limit")
    if not 1 <= max_clips <= MAX_CLIPS:
        raise ValueError(f"max_clips must be between 1 and {MAX_CLIPS}")

    temporary_context = tempfile.TemporaryDirectory(prefix="voxpipe-qwen-persian-")
    with temporary_context as temporary:
        temp_root = Path(temporary)
        references: list[str] = []
        hypotheses: list[str] = []
        audio_seconds = 0.0
        inference_seconds = 0.0
        with tarfile.open(archive_path, mode="r:gz") as archive:
            audio_by_basename, metadata_members = _read_archive_records(archive)
            selected = _load_reference_rows(archive, metadata_members[0][0], audio_by_basename, max_clips)
            if len(selected) != max_clips:
                raise ValueError(f"Common Voice archive yielded {len(selected)} matching clips; exactly {max_clips} are required")
            if model is None:
                if model_loader is None:
                    raise ValueError("a model or model_loader is required")
                model_dir = model_loader(cache_dir)
                processor, model = _load_model_from_directory(model_dir)
            if processor is None:
                raise ValueError("a native Transformers processor is required with an injected model")
            if language_identifier is None:
                language_identifier = (language_identifier_loader or _download_language_identifier)(cache_dir)
            language_id_failures = {"arabic": 0, "low_confidence": 0, "other_or_undetermined": 0}
            transcription_failures = {"empty_output": 0}
            for index, (member, reference) in enumerate(selected, start=1):
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError("unable to read selected audio member")
                audio_path = temp_root / f"clip-{index:03d}{PurePosixPath(member.name).suffix.lower()}"
                with extracted, audio_path.open("wb") as output:
                    shutil.copyfileobj(extracted, output, length=1024 * 1024)
                duration = _audio_duration_seconds(audio_path)
                audio_seconds += duration
                started_language_id = time.perf_counter()
                language_prediction = _identify_audio_language(language_identifier, audio_path)
                inference_seconds += time.perf_counter() - started_language_id
                raw_language = str(getattr(language_prediction, "raw_language", None) or language_prediction.language).casefold()
                if language_prediction.language != "fa" or language_prediction.confidence < language_id_confidence_threshold:
                    if raw_language in {"ar", "arabic"}:
                        language_id_failures["arabic"] += 1
                    elif getattr(language_prediction, "abstention_reason", None) == "low_confidence":
                        language_id_failures["low_confidence"] += 1
                    else:
                        language_id_failures["other_or_undetermined"] += 1
                    continue
                started_inference = time.perf_counter()
                parsed = _transcribe_audio(model, processor, audio_path, language=FORCED_LANGUAGE)
                inference_seconds += time.perf_counter() - started_inference
                try:
                    hypothesis = _parse_persian_transcription(parsed)
                except RuntimeError as exc:
                    if "empty Persian transcription" not in str(exc):
                        raise
                    transcription_failures["empty_output"] += 1
                    continue
                references.append(reference)
                hypotheses.append(hypothesis)
        complete_sample = len(references) == len(selected)

        metrics = corpus_error_rates(references, hypotheses) if complete_sample else None
        try:
            import torch
            torch_version = str(torch.__version__)
        except ImportError:
            torch_version = "not-installed"
        try:
            from importlib.metadata import version
            transformers_version = version("transformers")
        except Exception:
            transformers_version = "unknown"
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_rss_bytes = int(peak if __import__("sys").platform == "darwin" else peak * 1024)
        report = build_report(
            metrics=metrics, sample_count=len(references), requested_sample_count=len(selected),
            audio_seconds=audio_seconds, inference_seconds=inference_seconds,
            peak_rss_bytes=peak_rss_bytes, torch_version=torch_version,
            transformers_version=transformers_version, language_id_failures=language_id_failures,
            transcription_failures=transcription_failures,
            language_id_confidence_threshold=language_id_confidence_threshold,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _audio_duration_seconds(path: Path) -> float:
    import wave
    try:
        with wave.open(str(path), "rb") as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
        if frames > 0 and rate > 0:
            return frames / rate
    except (wave.Error, OSError):
        pass
    try:
        import soundfile as sf
        info = sf.info(str(path))
        if info.duration > 0:
            return float(info.duration)
    except Exception:
        pass
    # Qwen's audio loader supports common compressed formats; FFprobe is a
    # runner dependency and avoids decoding audio into memory just for timing.
    import subprocess
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    duration = float(completed.stdout.strip())
    if duration <= 0:
        raise ValueError("audio duration must be positive")
    return duration


def _load_model_from_directory(model_dir: Path) -> tuple[Any, Any]:
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor
    torch.set_num_threads(max(1, min(__import__("os").cpu_count() or 1, 4)))
    processor = AutoProcessor.from_pretrained(str(model_dir), local_files_only=True)
    model = AutoModelForMultimodalLM.from_pretrained(
        str(model_dir), dtype=torch.float32, local_files_only=True
    )
    model.to("cpu")
    model.eval()
    return processor, model


def _parse_persian_transcription(parsed: dict[str, Any]) -> str:
    language = str(parsed.get("language") or "").strip().casefold()
    if language not in {"persian", "fa"}:
        raise RuntimeError(f"Qwen3-ASR detected unexpected language for Persian audio: {language or 'unknown'}")
    transcription = str(parsed.get("transcription") or "").strip()
    if not transcription:
        raise RuntimeError("Qwen3-ASR returned an empty Persian transcription")
    return transcription


def _transcribe_audio(model: Any, processor: Any, audio_path: Path, *, language: str = FORCED_LANGUAGE) -> dict[str, str]:
    import torch
    if language.casefold() not in {"persian", "fa"}:
        raise ValueError("the Common Voice Persian evaluator only permits forced Persian decoding")
    inputs = processor.apply_transcription_request(
        audio=str(audio_path), language=language
    ).to("cpu", model.dtype)
    with torch.inference_mode():
        output_ids = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=DO_SAMPLE)
    generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
    transcription = processor.decode(generated_ids, return_format="transcription_only")[0]
    return {"language": "Persian", "transcription": str(transcription)}


def _identify_audio_language(language_identifier: Any, audio_path: Path) -> Any:
    import librosa
    samples, sample_rate = librosa.load(str(audio_path), sr=LID_SAMPLE_RATE, mono=True)
    return language_identifier.identify(samples.tolist(), sample_rate=int(sample_rate))


def _download_language_identifier(cache_dir: Path) -> Any:
    import sys
    from huggingface_hub import snapshot_download
    sys.path.insert(0, str(ROOT / "src"))
    from voxpipe.speech.language_id import SpeechBrainLanguageIdentifier
    model_dir = snapshot_download(
        repo_id=LID_MODEL_ID, revision=LID_MODEL_REVISION, cache_dir=str(cache_dir)
    )
    return SpeechBrainLanguageIdentifier(
        model_dir=model_dir, threshold=LID_CONFIDENCE_THRESHOLD,
        model_version=LID_MODEL_REVISION, supported_languages=frozenset({"fa", "ar"}),
    )


def _download_model(cache_dir: Path) -> Path:
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(
        repo_id=MODEL_ID, revision=MODEL_REVISION, cache_dir=str(cache_dir)
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache" / "huggingface")
    parser.add_argument("--max-clips", type=int, default=MAX_CLIPS)
    args = parser.parse_args()
    report = run_evaluation(
        args.archive, args.output, args.cache_dir, max_clips=args.max_clips,
        model_loader=_download_model, language_identifier_loader=_download_language_identifier,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
