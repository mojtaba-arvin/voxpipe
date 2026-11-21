"""Controlled Persian speaker-turn benchmark from anonymous Neyshekar test speakers.

This builds a synthetic, non-overlapping concatenation of consented/publicly
licensed read-speech utterances. It measures speaker clustering, not natural
conversation turn detection or overlap handling.
"""
from __future__ import annotations
import argparse, hashlib, io, itertools, json, platform, sys
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

DATASET_ID="shekar-ai/Neyshekar"
MODEL_ID="speechbrain/spkrec-ecapa-voxceleb"
WINDOW_MS=1500

def sha256_file(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()

def run(output_dir:Path,max_scan_rows:int=1000)->dict:
    import numpy as np
    import soundfile as sf
    from datasets import Audio,load_dataset
    from huggingface_hub import dataset_info,model_info,snapshot_download
    from voxpipe.speech.vad import SpeechRegion
    from voxpipe.speech.diarization import ECAPADiarizationAdapter,SpeakerTurn,evaluate_diarization

    dataset_revision=dataset_info(DATASET_ID).sha
    model_revision=model_info(MODEL_ID).sha
    dataset=load_dataset(DATASET_ID,split="test",streaming=True,revision=dataset_revision)
    dataset=dataset.cast_column("audio",Audio(decode=False))
    speakers={}
    for row in itertools.islice(dataset,max_scan_rows):
        speaker=str(row.get("speaker_id","")).strip()
        raw=row.get("audio",{}).get("bytes")
        if not speaker or not raw: continue
        waveform,sample_rate=sf.read(io.BytesIO(raw),dtype="float32",always_2d=False)
        waveform=np.asarray(waveform,dtype=np.float32)
        if waveform.ndim>1: waveform=waveform.mean(axis=1)
        if sample_rate!=16000 or waveform.ndim!=1: continue
        usable=(len(waveform)//(sample_rate*WINDOW_MS//1000))*(sample_rate*WINDOW_MS//1000)
        if usable < sample_rate*3: continue
        waveform=waveform[:usable]
        clips=speakers.setdefault(speaker,[])
        if len(clips)<2:
            clips.append({"audio":waveform,"sha256":hashlib.sha256(raw).hexdigest(),
                          "sample_id":str(row.get("id")),"duration_ms":round(len(waveform)*1000/sample_rate)})
        if sum(len(v)>=2 for v in speakers.values())>=3: break
    selected=[(speaker,clips[:2]) for speaker,clips in speakers.items() if len(clips)>=2][:3]
    if len(selected)!=3: raise RuntimeError(f"could not find 3 speakers with 2 suitable clips in first {max_scan_rows} test rows")
    order=[(selected[0][0],selected[0][1][0]),(selected[1][0],selected[1][1][0]),
           (selected[2][0],selected[2][1][0]),(selected[0][0],selected[0][1][1]),
           (selected[1][0],selected[1][1][1]),(selected[2][0],selected[2][1][1])]
    speaker_alias={speaker:f"speaker_{i+1}" for i,(speaker,_) in enumerate(selected)}
    silence=np.zeros(int(16000*0.25),dtype=np.float32)
    chunks=[]; regions=[]; references=[]; offset=0; manifest_items=[]
    for index,(speaker,clip) in enumerate(order):
        audio=clip["audio"]
        duration_ms=round(len(audio)*1000/16000)
        start_ms=round(offset*1000/16000); end_ms=start_ms+duration_ms
        regions.append(SpeechRegion(start_ms,end_ms))
        references.append(SpeakerTurn(start_ms,end_ms,speaker_alias[speaker],1.0))
        chunks.append(audio); offset+=len(audio)
        if index<len(order)-1: chunks.append(silence); offset+=len(silence)
        manifest_items.append({"sample_id":clip["sample_id"],"speaker_id":speaker_alias[speaker],
            "audio_sha256":clip["sha256"],"duration_ms":duration_ms})
    mixed=np.concatenate(chunks).astype(np.float32)
    mixed_sha=hashlib.sha256(mixed.astype("<f4").tobytes()).hexdigest()
    model_dir=Path(snapshot_download(repo_id=MODEL_ID,revision=model_revision))
    adapter=ECAPADiarizationAdapter(model_dir=model_dir,window_ms=WINDOW_MS,similarity_threshold=0.72)
    result=adapter.diarize(mixed.tolist(),sample_rate=16000,audio_sha256=mixed_sha,speech_regions=regions)
    baseline=[SpeakerTurn(region.start_ms,region.end_ms,"speaker_1",1.0) for region in regions]
    metrics={"single_speaker_baseline":evaluate_diarization(references,baseline,duration_ms=round(len(mixed)*1000/16000),collar_ms=0,frame_ms=100),
             "ecapa_cosine_clustering":evaluate_diarization(references,result.turns,duration_ms=round(len(mixed)*1000/16000),collar_ms=0,frame_ms=100)}
    output_dir.mkdir(parents=True,exist_ok=True)
    summary={"dataset":{"id":DATASET_ID,"release":"V6.0","revision":dataset_revision,"split":"test",
        "zenodo_doi":"10.5281/zenodo.18073632","license":"CC0-1.0","samples":manifest_items,
        "speaker_count":3,"synthetic_recording_sha256":mixed_sha,
        "construction":"six read-speech clips alternating three anonymous speakers, with 250 ms inserted silence; no overlap"},
        "model":{"id":MODEL_ID,"revision":model_revision,"license":"Apache-2.0",
            "weights_sha256":{p.name:sha256_file(p) for p in sorted(model_dir.iterdir()) if p.is_file() and p.suffix in (".ckpt",".txt",".yaml")},
            "window_ms":WINDOW_MS,"cosine_threshold":0.72,"device":"cpu"},
        "metrics":metrics,"predicted_turns":[{"start_ms":x.start_ms,"end_ms":x.end_ms,"speaker_id":x.speaker_id,
            "cosine_similarity":x.confidence} for x in result.turns],
        "limitations":["synthetic concatenation, not natural conversation","no overlapping speech","ECAPA was trained on VoxCeleb, not Persian",
            "speaker_id values are anonymous dataset labels and were not used to identify anyone"],
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
