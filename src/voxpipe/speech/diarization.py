"""Local ECAPA speaker-turn clustering and DER/JER evaluation."""
from __future__ import annotations
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path
import math
import re
from typing import Protocol

from voxpipe.speech.vad import SpeechRegion

_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class DiarizationRequest:
    """Provider-neutral mono audio and VAD regions for anonymous speaker clustering."""
    samples: tuple[float, ...]
    audio_sha256: str
    speech_regions: tuple[SpeechRegion, ...]
    sample_rate: int = 16000

    def __post_init__(self) -> None:
        if not isinstance(self.samples, (tuple, list)) or not self.samples:
            raise ValueError("samples must be a non-empty mono audio sequence")
        normalized = []
        for sample in self.samples:
            if (not isinstance(sample, (int, float)) or isinstance(sample, bool)
                    or not math.isfinite(sample) or not -1.0 <= sample <= 1.0):
                raise ValueError("samples must be finite normalized values between -1 and 1")
            normalized.append(float(sample))
        object.__setattr__(self, "samples", tuple(normalized))
        if (not isinstance(self.sample_rate, int) or isinstance(self.sample_rate, bool)
                or self.sample_rate != 16000):
            raise ValueError("the current ECAPA adapter requires integer mono 16 kHz audio")
        if not isinstance(self.audio_sha256, str) or not _SHA256.fullmatch(self.audio_sha256):
            raise ValueError("audio_sha256 must be lowercase SHA-256")
        if not isinstance(self.speech_regions, (tuple, list)):
            raise ValueError("speech_regions must be a sequence")
        duration_ms = round(len(normalized) * 1000 / self.sample_rate)
        previous_end = -1
        for region in self.speech_regions:
            if not isinstance(region, SpeechRegion):
                raise ValueError("speech_regions must contain SpeechRegion values")
            if region.start_ms < previous_end or region.end_ms > duration_ms:
                raise ValueError("speech regions must be ordered and within the audio duration")
            previous_end = region.end_ms
        object.__setattr__(self, "speech_regions", tuple(self.speech_regions))


class DiarizationPort(Protocol):
    """Provider-neutral anonymous speaker diarization boundary."""
    provider_name: str

    def diarize_request(self, request: DiarizationRequest) -> "DiarizationResult": ...


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    start_ms:int
    end_ms:int
    speaker_id:str
    confidence:float

    def __post_init__(self) -> None:
        if (not isinstance(self.start_ms, int) or isinstance(self.start_ms, bool) or self.start_ms < 0
                or not isinstance(self.end_ms, int) or isinstance(self.end_ms, bool) or self.end_ms <= self.start_ms):
            raise ValueError("speaker turn boundaries are invalid")
        if not isinstance(self.speaker_id, str) or not self.speaker_id.strip() or len(self.speaker_id) > 128:
            raise ValueError("speaker_id must be a non-empty anonymous label")
        if (not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool)
                or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1):
            raise ValueError("speaker confidence must be finite and between 0 and 1")

@dataclass(frozen=True, slots=True)
class DiarizationResult:
    provider:str
    model:str
    audio_sha256:str
    turns:tuple[SpeakerTurn,...]
    overlap_supported:bool=False

    def __post_init__(self) -> None:
        for field in ("provider", "model"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{field} must be a non-empty bounded string")
        if not isinstance(self.audio_sha256, str) or not _SHA256.fullmatch(self.audio_sha256):
            raise ValueError("audio_sha256 must be lowercase SHA-256")
        if not isinstance(self.turns, tuple) or any(not isinstance(turn, SpeakerTurn) for turn in self.turns):
            raise ValueError("turns must be a tuple of SpeakerTurn values")
        if not isinstance(self.overlap_supported, bool):
            raise ValueError("overlap_supported must be boolean")
        if any(right.start_ms < left.start_ms for left, right in zip(self.turns, self.turns[1:])):
            raise ValueError("speaker turns must be ordered by start time")
        if not self.overlap_supported and any(
            right.start_ms < left.end_ms for left, right in zip(self.turns, self.turns[1:])
        ):
            raise ValueError("overlapping speaker turns require overlap_supported=True")

class SpeechBrainECAPAEmbedder:
    """Thin local-only SpeechBrain ECAPA embedding wrapper."""
    def __init__(self,model_dir: str|Path):
        path=Path(model_dir)
        if not path.is_dir(): raise FileNotFoundError("ECAPA weights must be provisioned locally")
        from speechbrain.inference.speaker import EncoderClassifier
        self.model=EncoderClassifier.from_hparams(source=str(path),savedir=str(path),run_opts={"device":"cpu"})
    def embed(self,samples,sample_rate):
        if sample_rate!=16000: raise ValueError("ECAPA expects 16 kHz mono audio")
        import torch
        with torch.inference_mode():
            value=self.model.encode_batch(torch.tensor(list(samples),dtype=torch.float32).unsqueeze(0))
        return value.detach().cpu().reshape(-1).tolist()

class ECAPADiarizationAdapter:
    """Sliding-window ECAPA embeddings with deterministic online cosine clustering.

    Speaker labels are anonymous and scoped to this one audio asset. Overlapping
    speech is not supported by this baseline and is never silently fabricated.
    """
    provider_name="speechbrain-ecapa-diarization"
    def __init__(self,*,embedder=None,model_dir=None,window_ms=1500,similarity_threshold: float):
        if (not isinstance(window_ms, int) or isinstance(window_ms, bool) or window_ms < 1000
                or not isinstance(similarity_threshold, (int, float)) or isinstance(similarity_threshold, bool)
                or not math.isfinite(similarity_threshold) or not 0 < similarity_threshold < 1):
            raise ValueError("invalid diarization options")
        if embedder is None:
            if model_dir is None: raise ValueError("provide local ECAPA model_dir or an embedder")
            embedder=SpeechBrainECAPAEmbedder(model_dir)
        self.embedder,self.window_ms,self.threshold=embedder,window_ms,similarity_threshold
    @staticmethod
    def _normalize(vector):
        if not isinstance(vector, (tuple, list)) or not vector:
            raise ValueError("invalid speaker embedding")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool)
               or not math.isfinite(value) for value in vector):
            raise ValueError("invalid speaker embedding")
        values=[float(x) for x in vector]
        norm=math.sqrt(sum(x*x for x in values))
        if norm==0 or not math.isfinite(norm): raise ValueError("invalid speaker embedding")
        return [x/norm for x in values]
    def diarize_request(self, request: DiarizationRequest) -> DiarizationResult:
        if not isinstance(request, DiarizationRequest):
            raise TypeError("request must be a DiarizationRequest")
        return self.diarize(request.samples, sample_rate=request.sample_rate,
            audio_sha256=request.audio_sha256, speech_regions=request.speech_regions)

    def diarize(self,samples,*,sample_rate=16000,audio_sha256,speech_regions):
        request = DiarizationRequest(
            tuple(samples), audio_sha256, tuple(speech_regions), sample_rate=sample_rate,
        )
        samples = request.samples
        sample_rate = request.sample_rate
        speech_regions = request.speech_regions
        window=sample_rate*self.window_ms//1000
        vectors=[]; windows=[]
        embedding_dimensions = None
        for region in sorted(speech_regions, key=lambda x: x.start_ms):
            start=max(0,region.start_ms*sample_rate//1000)
            end=min(len(samples),region.end_ms*sample_rate//1000)
            if end-start<sample_rate: continue
            step=window
            starts=list(range(start,max(start+1,end-window+1),step))
            if not starts: starts=[start]
            for left in starts:
                right=min(end,left+window)
                if right-left<sample_rate: continue
                emb=self._normalize(self.embedder.embed(samples[left:right],sample_rate))
                if embedding_dimensions is None:
                    embedding_dimensions = len(emb)
                elif len(emb) != embedding_dimensions:
                    raise ValueError("speaker embedder returned inconsistent vector dimensions")
                windows.append((left,right)); vectors.append(emb)
        centroids=[]; counts=[]; assigned=[]
        for vector in vectors:
            scores=[sum(a*b for a,b in zip(vector,center)) for center in centroids]
            if scores and max(scores)>=self.threshold:
                index=max(range(len(scores)),key=scores.__getitem__)
                count=counts[index]
                centroids[index]=self._normalize([(centroids[index][i]*count+vector[i])/(count+1) for i in range(len(vector))])
                counts[index]+=1; confidence=max(0.0,min(1.0,scores[index]))
            else:
                index=len(centroids); centroids.append(vector); counts.append(1); confidence=1.0
            assigned.append((index,confidence))
        turns=[]
        for (start,end),(speaker,confidence) in zip(windows,assigned):
            turn=SpeakerTurn(round(start*1000/sample_rate),round(end*1000/sample_rate),f"speaker_{speaker+1}",confidence)
            if turns and turns[-1].speaker_id==turn.speaker_id and turn.start_ms<=turns[-1].end_ms+100:
                prior=turns[-1]; turns[-1]=SpeakerTurn(prior.start_ms,max(prior.end_ms,turn.end_ms),prior.speaker_id,min(prior.confidence,turn.confidence))
            else: turns.append(turn)
        return DiarizationResult(self.provider_name,"ecapa-voxceleb-local",audio_sha256,tuple(turns),False)

def evaluate_diarization(reference,hypothesis,*,duration_ms:int,collar_ms:int=250,frame_ms:int=10,skip_overlap:bool=False):
    """Frame-based DER/JER with one-to-one speaker mapping and explicit collar policy."""
    if (not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 1
            or not isinstance(frame_ms, int) or isinstance(frame_ms, bool) or frame_ms < 1
            or not isinstance(collar_ms, int) or isinstance(collar_ms, bool) or collar_ms < 0
            or not isinstance(skip_overlap, bool)):
        raise ValueError("invalid diarization evaluation options")
    for name, turns in (("reference", reference), ("hypothesis", hypothesis)):
        if not isinstance(turns, (tuple, list)) or any(
            not isinstance(turn, SpeakerTurn) or turn.end_ms > duration_ms for turn in turns
        ):
            raise ValueError(f"{name} turns must be SpeakerTurn values within duration_ms")
    refs=sorted({x.speaker_id for x in reference}); hyps=sorted({x.speaker_id for x in hypothesis})
    overlap={(h,r):0 for h in hyps for r in refs}
    def active(turns,t): return {x.speaker_id for x in turns if x.start_ms<=t<x.end_ms}
    for t in range(0,duration_ms,frame_ms):
        rs,hs=active(reference,t),active(hypothesis,t)
        for h in hs:
            for r in rs: overlap[(h,r)]+=1
    mapping={}
    if hyps and refs:
        if len(hyps)<=8 and len(refs)<=8:
            best=None; best_score=-1
            if len(hyps)<=len(refs):
                candidates=(dict(zip(hyps,chosen)) for chosen in permutations(refs,len(hyps)))
            else:
                candidates=(dict(zip(chosen,refs)) for chosen in permutations(hyps,len(refs)))
            for candidate in candidates:
                score=sum(overlap[(h,r)] for h,r in candidate.items())
                if score>best_score: best,best_score=candidate,score
            mapping=best or {}
        else:
            candidates=sorted(((v,h,r) for (h,r),v in overlap.items()),reverse=True)
            used_h=set();used_r=set()
            for _,h,r in candidates:
                if h not in used_h and r not in used_r: mapping[h]=r;used_h.add(h);used_r.add(r)
    boundaries=[b for turn in reference for b in (turn.start_ms,turn.end_ms)]
    miss=fa=conf=denom=0
    ref_frames={r:set() for r in refs}; hyp_frames={r:set() for r in refs}
    for t in range(0,duration_ms,frame_ms):
        if any(abs(t-b)<collar_ms for b in boundaries): continue
        rs,hs=active(reference,t),active(hypothesis,t)
        if skip_overlap and len(rs)>1: continue
        denom+=len(rs)
        mapped={mapping[h] for h in hs if h in mapping}
        miss+=max(0,len(rs)-len(hs)); fa+=max(0,len(hs)-len(rs))
        conf+=max(0,min(len(rs),len(hs))-len(rs & mapped))
        for r in rs: ref_frames[r].add(t)
        for h,r in mapping.items():
            if h in hs: hyp_frames[r].add(t)
    jer=[]
    for r in refs:
        union=ref_frames[r]|hyp_frames[r]
        jer.append(1-len(ref_frames[r]&hyp_frames[r])/len(union) if union else 0.0)
    return {"der":(miss+fa+conf)/denom if denom else None,
            "jer":sum(jer)/len(jer) if jer else None,
            "missed_speaker_frames":miss,"false_alarm_speaker_frames":fa,
            "speaker_confusion_frames":conf,"reference_speaker_frames":denom,
            "speaker_count_error":abs(len(refs)-len(hyps)),
            "collar_ms":collar_ms,"frame_ms":frame_ms,"skip_overlap":skip_overlap,
            "overlap_supported":False}
