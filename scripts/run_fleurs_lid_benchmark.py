"""Calibrate local VoxLingua107 abstention on FLEURS dev; evaluate only on held-out test."""
from __future__ import annotations
import argparse, hashlib, importlib.metadata, io, itertools, json, platform, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REVISION="6989e5bd2dc37546a93ae5d857bc04325e6a3738"
CONFIGS={
 "fa_ir":{"language":"fa","sha256":"e8982e2e5bb7c505c90aed9c5dde5c533076f6b74d1104d250cfc1ae7cb02a36"},
 "en_us":{"language":"en","sha256":"6428a4d04d3aac29e16b45e039bb1470a8bd7aa334cf92f7984c9c520d1f234d"},
 "de_de":{"language":"de","sha256":"3b40c214cabd603f1ddbf3da0499f7780975d6c56b75240f07a8990dbb7a6c07"},
}
MODEL_ID="speechbrain/lang-id-voxlingua107-ecapa"
MODEL_REVISION="0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9"


def hash_file(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()


def load_split(split, max_samples, model, *, dataset_loader, audio_type, soundfile, numpy):
    references=[]; predictions=[]; rows=[]
    for config,metadata in CONFIGS.items():
        url=f"https://huggingface.co/datasets/google/fleurs/resolve/{REVISION}/{config}/{split}-00000-of-00001.parquet"
        dataset=dataset_loader("parquet",data_files={split:url},split=split,streaming=True)
        dataset=dataset.cast_column("audio",audio_type(decode=False))
        for row in itertools.islice(dataset,max_samples):
            audio=row["audio"]; raw=audio.get("bytes")
            if not raw: raise RuntimeError(f"missing raw audio bytes in FLEURS {config}/{split}")
            waveform,sample_rate=soundfile.read(io.BytesIO(raw),dtype="float32",always_2d=False)
            waveform=numpy.asarray(waveform,dtype=numpy.float32)
            if waveform.ndim>1: waveform=waveform.mean(axis=1)
            if sample_rate!=16000: raise RuntimeError("FLEURS audio is expected at 16 kHz")
            prediction=model.identify(waveform.tolist(),sample_rate=sample_rate)
            reference=metadata["language"]
            references.append(reference);predictions.append(prediction)
            rows.append({"sample_id":f"{config}:{row.get('id',len(rows))}",
                "reference_language":reference,"predicted_language":prediction.language,
                "confidence":prediction.confidence,"audio_sha256":hashlib.sha256(raw).hexdigest(),
                "duration_ms":round(len(waveform)*1000/sample_rate),"dataset_config":config,
                "dataset_split":split,"dataset_revision":REVISION,"model_id":MODEL_ID,
                "model_revision":model.model_version})
    expected=max_samples*len(CONFIGS)
    if len(rows)!=expected:
        raise RuntimeError(f"FLEURS {split} sample count mismatch: expected {expected}, found {len(rows)}")
    return references,predictions,rows


def run(max_samples:int,output_dir:Path,threshold:float|None=None,*,calibration_samples:int=20,
        min_selective_accuracy:float=0.95,min_coverage:float=0.50)->dict:
    if not isinstance(max_samples,int) or isinstance(max_samples,bool) or not 1<=max_samples<=264:
        raise ValueError("max_samples must be between 1 and 264")
    if not isinstance(calibration_samples,int) or isinstance(calibration_samples,bool) or calibration_samples<1:
        raise ValueError("calibration_samples must be a positive integer")
    if threshold is not None and (not isinstance(threshold,(int,float)) or isinstance(threshold,bool)
            or not 0.0<=threshold<=1.0):
        raise ValueError("threshold must be between 0 and 1")
    for value,name in ((min_selective_accuracy,"min_selective_accuracy"),(min_coverage,"min_coverage")):
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not 0<=value<=1:
            raise ValueError(f"{name} must be between 0 and 1")

    import numpy as np
    import soundfile as sf
    from datasets import Audio,load_dataset
    from huggingface_hub import snapshot_download
    from voxpipe.speech.language_id import (SpeechBrainLanguageIdentifier,evaluate_language_id,
        calibrate_abstention_threshold)

    model_revision=MODEL_REVISION
    model_dir=Path(snapshot_download(repo_id=MODEL_ID,revision=model_revision))
    raw_model=SpeechBrainLanguageIdentifier(model_dir=model_dir,threshold=0.0,model_version=model_revision)

    if threshold is None:
        calibration_references,calibration_predictions,calibration_rows=load_split(
            "validation",calibration_samples,raw_model,dataset_loader=load_dataset,audio_type=Audio,
            soundfile=sf,numpy=np)
        calibration=calibrate_abstention_threshold(
            calibration_references,calibration_predictions,
            min_selective_accuracy=min_selective_accuracy,min_coverage=min_coverage)
        calibration_report={
            "mode":"validation-calibrated","split":"validation","dataset_revision":REVISION,
            "samples_per_language":calibration_samples,"total_samples":len(calibration_rows),
            "constraints":{"min_selective_accuracy":min_selective_accuracy,"min_coverage":min_coverage},
            "result":calibration,
        }
        output_dir.mkdir(parents=True,exist_ok=True)
        (output_dir/"calibration_predictions.jsonl").write_text(
            "".join(json.dumps(row,ensure_ascii=False)+"\n" for row in calibration_rows),encoding="utf-8")
        if not calibration["feasible"]:
            (output_dir/"calibration.json").write_text(
                json.dumps(calibration_report,ensure_ascii=False,indent=2),encoding="utf-8")
            raise RuntimeError("no abstention threshold satisfies the configured validation accuracy and coverage floors")
        selected_threshold=float(calibration["threshold"])
    else:
        selected_threshold=float(threshold)
        calibration_report={
            "mode":"operator-fixed","split":None,"dataset_revision":REVISION,
            "result":{"feasible":True,"threshold":selected_threshold,
                "note":"Operator-supplied threshold; no validation calibration was performed."},
        }

    model=SpeechBrainLanguageIdentifier(model=raw_model.model,threshold=selected_threshold,
        model_version=model_revision)
    references,predictions,rows=load_split(
        "test",max_samples,model,dataset_loader=load_dataset,audio_type=Audio,soundfile=sf,numpy=np)
    metrics=evaluate_language_id(references,predictions)
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"predictions.jsonl").write_text(
        "".join(json.dumps(row,ensure_ascii=False)+"\n" for row in rows),encoding="utf-8")
    weight_hashes={p.name:hash_file(p) for p in sorted(model_dir.iterdir())
        if p.is_file() and p.suffix in (".ckpt",".txt",".yaml")}
    summary={"dataset":{"id":"google/fleurs","revision":REVISION,"split":"test",
        "license":"CC BY 4.0",
        "attribution":"FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech; Google FLEURS dataset, CC BY 4.0.",
        "configs":{k:{"language":v["language"],"test_parquet_sha256":v["sha256"]} for k,v in CONFIGS.items()},
        "samples_per_language":max_samples,"total_samples":len(rows)},
        "model":{"id":MODEL_ID,"revision":model_revision,"license":"Apache-2.0",
            "weights_sha256":weight_hashes,"device":"cpu","threshold":selected_threshold,
            "threshold_policy":"calibrated on disjoint pinned FLEURS dev split" if threshold is None else "operator-fixed override"},
        "calibration":calibration_report,"metrics":metrics,
        "environment":{"python":sys.version,"platform":platform.platform(),"processor":platform.processor(),"dependencies":{name:importlib.metadata.version(name) for name in ("numpy","soundfile","datasets","huggingface-hub","speechbrain","torch")}},
        "generated_at":datetime.now(timezone.utc).isoformat()}
    (output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-samples",type=int,default=20)
    parser.add_argument("--calibration-samples",type=int,default=20,
        help="per-language FLEURS dev samples used only to calibrate abstention")
    parser.add_argument("--output-dir",type=Path,default=Path("benchmark-results/fleurs-lid"))
    parser.add_argument("--threshold",type=float,default=None,
        help="operator-fixed override; when omitted, calibrate on dev and evaluate on test")
    parser.add_argument("--min-selective-accuracy",type=float,default=0.95)
    parser.add_argument("--min-coverage",type=float,default=0.50)
    args=parser.parse_args()
    print(json.dumps(run(args.max_samples,args.output_dir,args.threshold,
        calibration_samples=args.calibration_samples,
        min_selective_accuracy=args.min_selective_accuracy,min_coverage=args.min_coverage),
        ensure_ascii=False,indent=2))


if __name__=="__main__": main()
