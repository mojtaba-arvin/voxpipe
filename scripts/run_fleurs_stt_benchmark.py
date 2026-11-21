"""Run a reproducible CPU Persian STT benchmark on the pinned FLEURS test split."""
from __future__ import annotations
import argparse, hashlib, io, itertools, json, platform, sys, time
from datetime import datetime, timezone
from pathlib import Path

DATASET_ID="google/fleurs"
DATASET_CONFIG="fa_ir"
DATASET_SPLIT="test"
DATASET_REVISION="6989e5bd2dc37546a93ae5d857bc04325e6a3738"
DATASET_FILE_SHA256="e8982e2e5bb7c505c90aed9c5dde5c533076f6b74d1104d250cfc1ae7cb02a36"
MODEL_ID="Systran/faster-whisper-small"

def sha256_file(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()

def run(max_samples: int, output_dir: Path) -> dict:
    if not 1<=max_samples<=264: raise ValueError("max_samples must be between 1 and 264")
    import numpy as np
    import soundfile as sf
    from datasets import Audio, load_dataset
    from huggingface_hub import model_info, snapshot_download
    from faster_whisper import WhisperModel
    from benchmark_stt import score

    info=model_info(MODEL_ID)
    model_revision=info.sha
    model_path=Path(snapshot_download(repo_id=MODEL_ID,revision=model_revision))
    model_hash=sha256_file(model_path/"model.bin")
    model=WhisperModel(str(model_path),device="cpu",compute_type="int8",local_files_only=True)

    dataset=load_dataset(DATASET_ID,DATASET_CONFIG,split=DATASET_SPLIT,
                         streaming=True,revision=DATASET_REVISION)
    dataset=dataset.cast_column("audio",Audio(decode=False))
    rows=[]; manifest_items=[]
    for row in itertools.islice(dataset,max_samples):
        audio=row["audio"]
        raw=audio.get("bytes")
        if not raw:
            raise RuntimeError("streamed FLEURS sample did not expose immutable audio bytes")
        waveform,sample_rate=sf.read(io.BytesIO(raw),dtype="float32",always_2d=False)
        if getattr(waveform,"ndim",1)>1:
            waveform=np.asarray(waveform,dtype=np.float32).mean(axis=1)
        waveform=np.asarray(waveform,dtype=np.float32)
        if sample_rate!=16000 or waveform.ndim!=1 or not len(waveform):
            raise RuntimeError("unexpected FLEURS audio shape or sample rate")
        reference=row.get("transcription") or row.get("raw_transcription")
        if not isinstance(reference,str) or not reference.strip():
            raise RuntimeError("FLEURS row has no reference transcription")
        started=time.perf_counter()
        segments,_=model.transcribe(waveform,language="fa",beam_size=5,
                                    word_timestamps=True,vad_filter=True)
        hypothesis=" ".join(segment.text.strip() for segment in segments).strip()
        elapsed=time.perf_counter()-started
        duration=len(waveform)/sample_rate
        audio_hash=hashlib.sha256(raw).hexdigest()
        sample_id=str(row.get("id",len(rows)))
        rows.append({"sample_id":sample_id,"reference":reference,"hypothesis":hypothesis,
                     "audio_duration_seconds":duration,"inference_seconds":elapsed,
                     "audio_sha256":audio_hash,"dataset_revision":DATASET_REVISION,
                     "model_id":MODEL_ID,"model_revision":model_revision})
        manifest_items.append({"sample_id":sample_id,"audio_sha256":audio_hash,
            "reference_sha256":hashlib.sha256(reference.encode("utf-8")).hexdigest(),
            "language":"fa-IR","duration_ms":round(duration*1000),
            "speaker_id":None,"session_id":None})
    if len(rows)!=max_samples: raise RuntimeError(f"expected {max_samples} samples, got {len(rows)}")
    output_dir.mkdir(parents=True,exist_ok=True)
    jsonl=output_dir/"results.jsonl"
    jsonl.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in rows),encoding="utf-8")
    metrics=score(rows)
    manifest={"schema_version":"1.0.0","task":"asr","dataset_id":DATASET_ID,
        "dataset_revision":DATASET_REVISION,"config":DATASET_CONFIG,"split":DATASET_SPLIT,
        "license":"cc-by-4.0",
        "attribution":"FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech; Google FLEURS dataset, CC BY 4.0.",
        "consent_basis":"public-dataset-license",
        "consent_note":"This public split exposes no speaker/session consent metadata; no speaker identity is inferred.",
        "source_file_sha256":DATASET_FILE_SHA256,
        "generated_at":datetime.now(timezone.utc).isoformat(),"items":manifest_items}
    schema=json.loads(Path("contracts/schemas/speech-corpus-manifest.v1.schema.json").read_text(encoding="utf-8"))
    from validate_corpus_manifest import validate_manifest
    validate_manifest(manifest,schema)
    (output_dir/"corpus-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    summary={"dataset":{"id":DATASET_ID,"config":DATASET_CONFIG,"split":DATASET_SPLIT,
                "revision":DATASET_REVISION,"file_sha256":DATASET_FILE_SHA256,
                "license":"CC BY 4.0","samples":len(rows)},
        "model":{"id":MODEL_ID,"revision":model_revision,"model_bin_sha256":model_hash,
                 "license":"MIT","device":"cpu","compute_type":"int8"},
        "metrics":metrics,"environment":{"python":sys.version,"platform":platform.platform(),
                 "processor":platform.processor()},
        "result_files":["results.jsonl","corpus-manifest.json"]}
    (output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-samples",type=int,default=100)
    parser.add_argument("--output-dir",type=Path,default=Path("benchmark-results/fleurs-fa-ir"))
    args=parser.parse_args()
    summary=run(args.max_samples,args.output_dir)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
