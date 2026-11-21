"""Tune ECAPA on Neyshekar validation speakers, then test on disjoint speakers.

The recording is a controlled concatenation of read-speech utterances, not
natural conversation. No speaker identity is inferred; dataset IDs are mapped
to anonymous per-run labels.
"""
from __future__ import annotations
import argparse, hashlib, io, itertools, json, platform, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

DATASET_ID="shekar-ai/Neyshekar"
MODEL_ID="speechbrain/spkrec-ecapa-voxceleb"
WINDOW_MS=1500
THRESHOLDS=(0.05,0.10,0.15,0.20,0.30,0.40,0.50,0.60,0.70,0.80,0.90)

def sha256_file(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()

def collect_speakers(split,revision,max_scan_rows):
    import numpy as np
    import soundfile as sf
    from datasets import Audio,load_dataset
    dataset=load_dataset(DATASET_ID,split=split,streaming=True,revision=revision)
    dataset=dataset.cast_column("audio",Audio(decode=False))
    speakers={}
    for row in itertools.islice(dataset,max_scan_rows):
        speaker=str(row.get("speaker_id","")).strip()
        audio_data=row.get("audio") or {}
        raw=audio_data.get("bytes")
        if not speaker or not raw: continue
        waveform,sample_rate=sf.read(io.BytesIO(raw),dtype="float32",always_2d=False)
        waveform=np.asarray(waveform,dtype=np.float32)
        if waveform.ndim>1: waveform=waveform.mean(axis=1)
        if sample_rate!=16000 or waveform.ndim!=1: continue
        window_samples=sample_rate*WINDOW_MS//1000
        usable=(len(waveform)//window_samples)*window_samples
        if usable<sample_rate*3: continue
        waveform=waveform[:usable]
        clips=speakers.setdefault(speaker,[])
        if len(clips)<2:
            clips.append({"audio":waveform,"sha256":hashlib.sha256(raw).hexdigest(),
                          "sample_id":str(row.get("id")),"duration_ms":round(len(waveform)*1000/sample_rate)})
        if sum(len(v)>=2 for v in speakers.values())>=3: break
    selected=[(speaker,clips[:2]) for speaker,clips in speakers.items() if len(clips)>=2][:3]
    if len(selected)!=3:
        raise RuntimeError(f"could not find 3 speakers with 2 suitable clips in {split} within {max_scan_rows} rows")
    return selected

def build_recording(selected):
    import numpy as np
    from voxpipe.speech.vad import SpeechRegion
    from voxpipe.speech.diarization import SpeakerTurn
    order=[(selected[0][0],selected[0][1][0]),(selected[1][0],selected[1][1][0]),
           (selected[2][0],selected[2][1][0]),(selected[0][0],selected[0][1][1]),
           (selected[1][0],selected[1][1][1]),(selected[2][0],selected[2][1][1])]
    aliases={speaker:f"speaker_{i+1}" for i,(speaker,_) in enumerate(selected)}
    silence=np.zeros(int(16000*0.25),dtype=np.float32)
    chunks=[];regions=[];references=[];offset=0;items=[]
    for index,(speaker,clip) in enumerate(order):
        audio=clip["audio"]; duration_ms=round(len(audio)*1000/16000)
        start_ms=round(offset*1000/16000); end_ms=start_ms+duration_ms
        regions.append(SpeechRegion(start_ms,end_ms))
        references.append(SpeakerTurn(start_ms,end_ms,aliases[speaker],1.0))
        chunks.append(audio);offset+=len(audio)
        if index<len(order)-1: chunks.append(silence);offset+=len(silence)
        items.append({"sample_id":clip["sample_id"],"speaker_id":aliases[speaker],
                      "audio_sha256":clip["sha256"],"duration_ms":duration_ms})
    mixed=np.concatenate(chunks).astype(np.float32)
    digest=hashlib.sha256(mixed.astype("<f4").tobytes()).hexdigest()
    return mixed,regions,references,items,digest,aliases

def run(output_dir:Path,max_scan_rows:int=1000)->dict:
    from huggingface_hub import dataset_info,model_info,snapshot_download
    from voxpipe.speech.diarization import ECAPADiarizationAdapter,SpeechBrainECAPAEmbedder,evaluate_diarization

    dataset_revision=dataset_info(DATASET_ID).sha
    model_revision=model_info(MODEL_ID).sha
    dev_selected=collect_speakers("validation",dataset_revision,max_scan_rows)
    test_selected=collect_speakers("test",dataset_revision,max_scan_rows)
    dev_audio,dev_regions,dev_refs,dev_items,dev_sha,dev_aliases=build_recording(dev_selected)
    test_audio,test_regions,test_refs,test_items,test_sha,test_aliases=build_recording(test_selected)
    model_dir=Path(snapshot_download(repo_id=MODEL_ID,revision=model_revision))
    embedder=SpeechBrainECAPAEmbedder(model_dir)
    dev_duration=round(len(dev_audio)*1000/16000)
    sweep={}
    for threshold in THRESHOLDS:
        adapter=ECAPADiarizationAdapter(embedder=embedder,window_ms=WINDOW_MS,similarity_threshold=threshold)
        result=adapter.diarize(dev_audio.tolist(),sample_rate=16000,audio_sha256=dev_sha,speech_regions=dev_regions)
        sweep[str(threshold)]=evaluate_diarization(dev_refs,result.turns,duration_ms=dev_duration,collar_ms=0,frame_ms=100)
    selected_threshold=min(THRESHOLDS,key=lambda value:(
        sweep[str(value)]["der"] if sweep[str(value)]["der"] is not None else 1.0,
        sweep[str(value)]["jer"] if sweep[str(value)]["jer"] is not None else 1.0,
        sweep[str(value)]["speaker_count_error"]))
    adapter=ECAPADiarizationAdapter(embedder=embedder,window_ms=WINDOW_MS,similarity_threshold=selected_threshold)
    result=adapter.diarize(test_audio.tolist(),sample_rate=16000,audio_sha256=test_sha,speech_regions=test_regions)
    test_duration=round(len(test_audio)*1000/16000)
    baseline=[__import__("voxpipe.speech.diarization",fromlist=["SpeakerTurn"]).SpeakerTurn(r.start_ms,r.end_ms,"speaker_1",1.0) for r in test_regions]
    metrics={"single_speaker_baseline":evaluate_diarization(test_refs,baseline,duration_ms=test_duration,collar_ms=0,frame_ms=100),
             "ecapa_validation_tuned":evaluate_diarization(test_refs,result.turns,duration_ms=test_duration,collar_ms=0,frame_ms=100)}
    output_dir.mkdir(parents=True,exist_ok=True)
    summary={"dataset":{"id":DATASET_ID,"release":"V6.0","revision":dataset_revision,
        "validation_split":{"name":"validation","samples":dev_items,"speakers":len(dev_aliases),"synthetic_recording_sha256":dev_sha},
        "test_split":{"name":"test","samples":test_items,"speakers":len(test_aliases),"synthetic_recording_sha256":test_sha},
        "zenodo_doi":"10.5281/zenodo.18073632","license":"CC0-1.0",
        "construction":"six read-speech clips alternating three anonymous speakers, with 250 ms inserted silence; no overlap"},
        "model":{"id":MODEL_ID,"revision":model_revision,"license":"Apache-2.0",
            "weights_sha256":{p.name:sha256_file(p) for p in sorted(model_dir.iterdir()) if p.is_file() and p.suffix in (".ckpt",".txt",".yaml")},
            "window_ms":WINDOW_MS,"threshold_sweep_on_validation":sweep,
            "selected_threshold":selected_threshold,"device":"cpu"},
        "test_metrics":metrics,
        "predicted_turns":[{"start_ms":x.start_ms,"end_ms":x.end_ms,"speaker_id":x.speaker_id,
            "cosine_similarity":x.confidence} for x in result.turns],
        "limitations":["controlled concatenation, not natural conversation","no overlapping speech",
            "ECAPA was trained on VoxCeleb, not Persian","speaker IDs are anonymous dataset labels; no identity inference"],
        "environment":{"python":sys.version,"platform":platform.platform(),"processor":platform.processor()},
        "generated_at":datetime.now(timezone.utc).isoformat()}
    (output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-scan-rows",type=int,default=1000)
    parser.add_argument("--output-dir",type=Path,default=Path("benchmark-results/neyshekar-diarization"))
    args=parser.parse_args()
    print(json.dumps(run(args.output_dir,args.max_scan_rows),ensure_ascii=False,indent=2))

if __name__=="__main__": main()
