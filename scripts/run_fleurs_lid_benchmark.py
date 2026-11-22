"""Evaluate local VoxLingua107 language ID on pinned multilingual FLEURS test subsets."""
from __future__ import annotations
import argparse, hashlib, io, itertools, json, platform, sys
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REVISION="6989e5bd2dc37546a93ae5d857bc04325e6a3738"
CONFIGS={
 "fa_ir":{"language":"fa","sha256":"e8982e2e5bb7c505c90aed9c5dde5c533076f6b74d1104d250cfc1ae7cb02a36"},
 "en_us":{"language":"en","sha256":"6428a4d04d3aac29e16b45e039bb1470a8bd7aa334cf92f7984c9c520d1f234d"},
 "de_de":{"language":"de","sha256":"3b40c214cabd603f1ddbf3da0499f7780975d6c56b75240f07a8990dbb7a6c07"},
}
MODEL_ID="speechbrain/lang-id-voxlingua107-ecapa"

def hash_file(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()

def run(max_samples:int,output_dir:Path,threshold:float=0.65)->dict:
    if not 1<=max_samples<=264: raise ValueError("max_samples must be between 1 and 264")
    if not 0.0<=threshold<=1.0: raise ValueError("threshold must be between 0 and 1")
    import numpy as np
    import soundfile as sf
    from datasets import Audio,load_dataset
    from huggingface_hub import model_info,snapshot_download
    from voxpipe.speech.language_id import SpeechBrainLanguageIdentifier,evaluate_language_id

    info=model_info(MODEL_ID)
    model_revision=info.sha
    model_dir=Path(snapshot_download(repo_id=MODEL_ID,revision=model_revision))
    model=SpeechBrainLanguageIdentifier(model_dir=model_dir,threshold=threshold,model_version=model_revision)
    references=[];predictions=[];rows=[]
    for config,metadata in CONFIGS.items():
        url=f"https://huggingface.co/datasets/google/fleurs/resolve/{REVISION}/{config}/test-00000-of-00001.parquet"
        dataset=load_dataset("parquet",data_files={"test":url},split="test",streaming=True)
        dataset=dataset.cast_column("audio",Audio(decode=False))
        for row in itertools.islice(dataset,max_samples):
            audio=row["audio"]; raw=audio.get("bytes")
            if not raw: raise RuntimeError(f"missing raw audio bytes in FLEURS {config}")
            waveform,sample_rate=sf.read(io.BytesIO(raw),dtype="float32",always_2d=False)
            waveform=np.asarray(waveform,dtype=np.float32)
            if waveform.ndim>1: waveform=waveform.mean(axis=1)
            if sample_rate!=16000: raise RuntimeError("FLEURS audio is expected at 16 kHz")
            prediction=model.identify(waveform.tolist(),sample_rate=sample_rate)
            reference=metadata["language"]
            references.append(reference);predictions.append(prediction)
            rows.append({"sample_id":f"{config}:{row.get('id',len(rows))}",
                "reference_language":reference,"predicted_language":prediction.language,
                "confidence":prediction.confidence,"audio_sha256":hashlib.sha256(raw).hexdigest(),
                "duration_ms":round(len(waveform)*1000/sample_rate),"dataset_config":config,
                "dataset_revision":REVISION,"model_id":MODEL_ID,"model_revision":model_revision})
    if len(rows)!=max_samples*len(CONFIGS): raise RuntimeError("multilingual benchmark sample count mismatch")
    metrics=evaluate_language_id(references,predictions)
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"predictions.jsonl").write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in rows),encoding="utf-8")
    weight_hashes={p.name:hash_file(p) for p in sorted(model_dir.iterdir()) if p.is_file() and p.suffix in (".ckpt",".txt",".yaml")}
    summary={"dataset":{"id":"google/fleurs","revision":REVISION,"split":"test","license":"CC BY 4.0",
        "attribution":"FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech; Google FLEURS dataset, CC BY 4.0.",
        "configs":{k:{"language":v["language"],"parquet_sha256":v["sha256"]} for k,v in CONFIGS.items()},
        "samples_per_language":max_samples,"total_samples":len(rows)},
        "model":{"id":MODEL_ID,"revision":model_revision,"license":"Apache-2.0","weights_sha256":weight_hashes,
                 "device":"cpu","threshold":threshold,"threshold_policy":"fixed before evaluation; calibrate on a disjoint validation split"},
        "metrics":metrics,"environment":{"python":sys.version,"platform":platform.platform(),"processor":platform.processor()},
        "generated_at":datetime.now(timezone.utc).isoformat()}
    (output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-samples",type=int,default=20)
    parser.add_argument("--output-dir",type=Path,default=Path("benchmark-results/fleurs-lid"))
    parser.add_argument("--threshold",type=float,default=0.65,help="pre-calibrated confidence threshold; do not tune on the test split")
    args=parser.parse_args()
    print(json.dumps(run(args.max_samples,args.output_dir,args.threshold),ensure_ascii=False,indent=2))

if __name__=="__main__": main()
