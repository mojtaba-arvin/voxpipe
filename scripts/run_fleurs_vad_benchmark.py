"""Compare local Silero and WebRTC VAD on a pinned FLEURS utterance-envelope proxy."""
from __future__ import annotations
import argparse,hashlib,io,itertools,json,platform,resource,sys,time
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
REVISION="6989e5bd2dc37546a93ae5d857bc04325e6a3738"
CONFIGS={
 "fa_ir":{"language":"fa","sha256":"e8982e2e5bb7c505c90aed9c5dde5c533076f6b74d1104d250cfc1ae7cb02a36"},
 "en_us":{"language":"en","sha256":"6428a4d04d3aac29e16b45e039bb1470a8bd7aa334cf92f7984c9c520d1f234d"},
 "de_de":{"language":"de","sha256":"3b40c214cabd603f1ddbf3da0499f7780975d6c56b75240f07a8990dbb7a6c07"},
}
def mean(values): return sum(values)/len(values) if values else None
def aggregate(records):
    keys=("precision","recall","f1","mean_boundary_error_ms")
    return {**{key:mean([r[key] for r in records if r[key] is not None]) for key in keys},
        "false_activation_seconds":sum(r["false_activation_seconds"] for r in records),
        "missed_speech_seconds":sum(r["missed_speech_seconds"] for r in records),
        "runtime_seconds_total":sum(r["runtime_seconds"] for r in records),
        "runtime_seconds_per_sample":mean([r["runtime_seconds"] for r in records]),
        "sample_count":len(records)}
def run(max_samples:int,output_dir:Path)->dict:
    import numpy as np
    import soundfile as sf
    from datasets import Audio,load_dataset
    from importlib.metadata import version
    from voxpipe.speech.vad import SileroVADAdapter,WebRTCVADAdapter,SpeechRegion,evaluate_vad
    if not 1<=max_samples<=264: raise ValueError("max_samples must be between 1 and 264")
    silero=SileroVADAdapter(threshold=0.5,min_speech_duration_ms=100,min_silence_duration_ms=100)
    webrtc=WebRTCVADAdapter(aggressiveness=2,frame_duration_ms=30)
    records={"silero":[],"webrtc":[]};rows=[]
    for config,metadata in CONFIGS.items():
        url=f"https://huggingface.co/datasets/google/fleurs/resolve/{REVISION}/{config}/test-00000-of-00001.parquet"
        dataset=load_dataset("parquet",data_files={"test":url},split="test",streaming=True)
        dataset=dataset.cast_column("audio",Audio(decode=False))
        for row in itertools.islice(dataset,max_samples):
            raw=row["audio"].get("bytes")
            if not raw: raise RuntimeError(f"missing raw audio bytes in FLEURS {config}")
            waveform,sample_rate=sf.read(io.BytesIO(raw),dtype="float32",always_2d=False)
            waveform=np.asarray(waveform,dtype=np.float32)
            if waveform.ndim>1: waveform=waveform.mean(axis=1)
            if sample_rate!=16000: raise RuntimeError("FLEURS audio is expected at 16 kHz")
            duration_ms=round(len(waveform)*1000/sample_rate)
            padded=np.concatenate((np.zeros(8000,dtype=np.float32),waveform,np.zeros(8000,dtype=np.float32)))
            audio_sha=hashlib.sha256(raw).hexdigest()
            expected=(SpeechRegion(500,500+duration_ms),)
            pcm16=(np.clip(padded,-1,1)*32767).astype("<i2").tobytes()
            row_metrics={}
            for name,adapter,payload in (("silero",silero,padded.tolist()),("webrtc",webrtc,pcm16)):
                start=time.perf_counter()
                result=adapter.detect(payload,sample_rate=16000,audio_sha256=audio_sha)
                elapsed=time.perf_counter()-start
                scores=evaluate_vad(expected,result.regions,duration_ms=duration_ms+1000,frame_ms=10)
                row_metrics[name]={**scores,"runtime_seconds":elapsed,
                    "regions":[{"start_ms":x.start_ms,"end_ms":x.end_ms} for x in result.regions]}
                records[name].append(row_metrics[name])
            rows.append({"sample_id":f"{config}:{len(rows)}:{row.get('id',len(rows))}",
                "language":metadata["language"],"dataset_config":config,"audio_sha256":audio_sha,
                "duration_ms":duration_ms,"vad":row_metrics})
    if len(rows)!=max_samples*len(CONFIGS): raise RuntimeError("unexpected FLEURS VAD sample count")
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"predictions.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in rows),encoding="utf-8")
    summary={"dataset":{"id":"google/fleurs","revision":REVISION,"split":"test","license":"CC BY 4.0",
        "attribution":"FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech; Google FLEURS dataset, CC BY 4.0.",
        "configs":{k:{"language":v["language"],"parquet_sha256":v["sha256"]} for k,v in CONFIGS.items()},
        "samples_per_language":max_samples,"total_samples":len(rows)},
        "evaluation_basis":"synthetic utterance-envelope proxy",
        "ground_truth_warning":"FLEURS provides utterance-level audio/text, not frame-level speech/non-speech labels. The full utterance envelope is treated as speech, so internal pauses are counted as misses; these are not gold frame-level VAD precision/recall scores.",
        "models":{"silero":{"package_version":version("silero-vad"),"threshold":0.5,"min_speech_duration_ms":100,"metrics":aggregate(records["silero"])},
                  "webrtc":{"package_version":version("webrtcvad-wheels"),"aggressiveness":2,"frame_duration_ms":30,"metrics":aggregate(records["webrtc"])}},
        "process_peak_rss_bytes":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1024 if platform.system()!="Darwin" else 1),
        "environment":{"python":sys.version,"platform":platform.platform(),"device":"cpu"},
        "generated_at":datetime.now(timezone.utc).isoformat()}
    (output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-samples",type=int,default=20)
    parser.add_argument("--output-dir",type=Path,default=Path("benchmark-results/fleurs-vad"))
    args=parser.parse_args()
    print(json.dumps(run(args.max_samples,args.output_dir),ensure_ascii=False,indent=2))
if __name__=="__main__": main()
