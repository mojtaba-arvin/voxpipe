"""Provider-neutral VAD contracts, local adapters and frame-level evaluation."""
from __future__ import annotations
from array import array
from dataclasses import dataclass
import math
import re

_SHA256 = re.compile(r"^[a-f0-9]{64}$")

@dataclass(frozen=True, slots=True)
class SpeechRegion:
    start_ms: int
    end_ms: int
    confidence: float | None = None

@dataclass(frozen=True, slots=True)
class VADResult:
    provider: str
    model: str
    audio_sha256: str
    sample_rate: int
    duration_ms: int
    regions: tuple[SpeechRegion, ...]

class SileroVADAdapter:
    """Silero VAD with bundled/local model loading; no runtime model download."""
    provider_name = "silero-vad"
    def __init__(self, *, model=None, timestamp_fn=None, threshold=0.5,
                 min_speech_duration_ms=100, min_silence_duration_ms=100):
        if not 0.0 < threshold < 1.0: raise ValueError("threshold must be between 0 and 1")
        if min_speech_duration_ms < 1 or min_silence_duration_ms < 0:
            raise ValueError("invalid VAD duration options")
        injected_timestamp_fn = timestamp_fn is not None
        if model is None and timestamp_fn is None:
            try:
                from silero_vad import load_silero_vad, get_speech_timestamps
                model, timestamp_fn = load_silero_vad(), get_speech_timestamps
            except Exception as exc:
                raise RuntimeError("Silero VAD package/model is unavailable locally") from exc
        elif timestamp_fn is None:
            from silero_vad import get_speech_timestamps
            timestamp_fn = get_speech_timestamps
        self.model, self.timestamp_fn = model, timestamp_fn
        self._timestamp_injected = injected_timestamp_fn
        self.threshold = threshold
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms

    def detect(self, samples, *, sample_rate: int, audio_sha256: str) -> VADResult:
        if sample_rate not in (8000, 16000): raise ValueError("Silero VAD accepts 8 kHz or 16 kHz audio")
        if not _SHA256.fullmatch(audio_sha256): raise ValueError("audio_sha256 must be lowercase SHA-256")
        values = array("f", samples)
        if not values or any(not math.isfinite(x) for x in values): raise ValueError("audio must be non-empty finite mono samples")
        if self._timestamp_injected:
            waveform = values
        else:
            import torch
            waveform = torch.tensor(values, dtype=torch.float32)
        raw = self.timestamp_fn(
            waveform, self.model, sampling_rate=sample_rate, threshold=self.threshold,
            min_speech_duration_ms=self.min_speech_duration_ms,
            min_silence_duration_ms=self.min_silence_duration_ms,
            return_seconds=False,
        )
        regions = []
        for item in raw:
            start, end = int(item["start"]), int(item["end"])
            if start < 0 or end <= start or end > len(values):
                raise ValueError("VAD model returned invalid sample boundaries")
            regions.append(SpeechRegion(round(start * 1000 / sample_rate), round(end * 1000 / sample_rate)))
        regions.sort(key=lambda x: (x.start_ms, x.end_ms))
        merged = []
        for region in regions:
            if merged and region.start_ms <= merged[-1].end_ms:
                prior = merged[-1]
                merged[-1] = SpeechRegion(prior.start_ms, max(prior.end_ms, region.end_ms))
            else: merged.append(region)
        return VADResult(self.provider_name, "silero-vad-local", audio_sha256, sample_rate,
                         round(len(values) * 1000 / sample_rate), tuple(merged))

class WebRTCVADAdapter:
    """Frame-based WebRTC VAD baseline for mono PCM16 at supported sample rates."""
    provider_name = "webrtc-vad"
    def __init__(self, *, vad=None, aggressiveness=2, frame_duration_ms=30):
        if aggressiveness not in (0,1,2,3) or frame_duration_ms not in (10,20,30):
            raise ValueError("invalid WebRTC VAD configuration")
        if vad is None:
            try:
                import webrtcvad
                vad = webrtcvad.Vad(aggressiveness)
            except ImportError as exc:
                raise RuntimeError("install webrtcvad-wheels to use WebRTC VAD") from exc
        self.vad, self.frame_duration_ms = vad, frame_duration_ms
    def detect(self, pcm16: bytes, *, sample_rate: int, audio_sha256: str) -> VADResult:
        if sample_rate not in (8000,16000,32000,48000): raise ValueError("unsupported WebRTC sample rate")
        if not isinstance(pcm16,bytes) or not pcm16 or len(pcm16)%2: raise ValueError("audio must be non-empty mono PCM16")
        if not _SHA256.fullmatch(audio_sha256): raise ValueError("audio_sha256 must be lowercase SHA-256")
        frame_samples = sample_rate*self.frame_duration_ms//1000
        frame_bytes = frame_samples*2
        total_samples = len(pcm16)//2
        frame_count = (total_samples+frame_samples-1)//frame_samples
        flags=[]
        for index in range(frame_count):
            frame=pcm16[index*frame_bytes:(index+1)*frame_bytes]
            if len(frame)<frame_bytes: frame=frame + bytes(frame_bytes-len(frame))
            flags.append(bool(self.vad.is_speech(frame,sample_rate)))
        regions=[]; start=None
        for i, voiced in enumerate(flags+[False]):
            if voiced and start is None: start=i
            elif not voiced and start is not None:
                regions.append(SpeechRegion(start*self.frame_duration_ms,
                    min(round(total_samples*1000/sample_rate),i*self.frame_duration_ms)))
                start=None
        return VADResult(self.provider_name,"webrtc-vad",audio_sha256,sample_rate,
                         round(total_samples*1000/sample_rate),tuple(regions))

def evaluate_vad(reference, hypothesis, *, duration_ms: int, frame_ms: int=10) -> dict:
    """Frame-level speech precision/recall plus false/missed duration and boundary error."""
    if duration_ms < 1 or frame_ms < 1: raise ValueError("duration_ms and frame_ms must be positive")
    def active(regions, t):
        return any(r.start_ms <= t and r.end_ms > t for r in regions)
    tp=fp=fn=0
    for t in range(0,duration_ms,frame_ms):
        ref, hyp=active(reference,t), active(hypothesis,t)
        tp += ref and hyp; fp += (not ref) and hyp; fn += ref and (not hyp)
    precision=tp/(tp+fp) if tp+fp else 0.0
    recall=tp/(tp+fn) if tp+fn else 0.0
    f1=2*precision*recall/(precision+recall) if precision+recall else 0.0
    refs=sorted(reference,key=lambda x:x.start_ms)
    hyps=sorted(hypothesis,key=lambda x:x.start_ms)
    remaining=set(range(len(hyps))); pairs=[]
    for ref in refs:
        if not remaining: break
        best=max(remaining,key=lambda index:max(0,min(ref.end_ms,hyps[index].end_ms)-max(ref.start_ms,hyps[index].start_ms)))
        overlap=max(0,min(ref.end_ms,hyps[best].end_ms)-max(ref.start_ms,hyps[best].start_ms))
        if overlap>0:
            pairs.append((ref,hyps[best]));remaining.remove(best)
    errors=[abs(a.start_ms-b.start_ms) for a,b in pairs]+[abs(a.end_ms-b.end_ms) for a,b in pairs]
    return {"precision":precision,"recall":recall,"f1":f1,
            "false_activation_seconds":fp*frame_ms/1000,
            "missed_speech_seconds":fn*frame_ms/1000,
            "mean_boundary_error_ms":sum(errors)/len(errors) if errors else None,
            "matched_regions":len(pairs),"reference_regions":len(refs),"hypothesis_regions":len(hyps),
            "unmatched_reference_regions":len(refs)-len(pairs),"unmatched_hypothesis_regions":len(hyps)-len(pairs)}
