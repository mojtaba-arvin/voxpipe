"""Provider-neutral VAD contracts, local adapters and frame-level evaluation."""
from __future__ import annotations
from array import array
from dataclasses import dataclass
import math
import re
import struct
from typing import Protocol

_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class VADRequest:
    """Canonical normalized mono audio request shared by all VAD adapters."""
    samples: tuple[float, ...]
    sample_rate: int
    audio_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.samples, (tuple, list)) or not self.samples:
            raise ValueError("samples must be a non-empty sequence of mono float samples")
        normalized = []
        for sample in self.samples:
            if (not isinstance(sample, (int, float)) or isinstance(sample, bool)
                    or not math.isfinite(sample) or not -1.0 <= sample <= 1.0):
                raise ValueError("samples must be finite normalized values between -1 and 1")
            normalized.append(float(sample))
        object.__setattr__(self, "samples", tuple(normalized))
        if not isinstance(self.sample_rate, int) or isinstance(self.sample_rate, bool) or self.sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError("sample_rate must be one of 8, 16, 32 or 48 kHz")
        if not isinstance(self.audio_sha256, str) or not _SHA256.fullmatch(self.audio_sha256):
            raise ValueError("audio_sha256 must be lowercase SHA-256")


class VADPort(Protocol):
    """Provider-neutral VAD contract; adapters accept the same normalized samples."""
    provider_name: str

    def detect_request(self, request: VADRequest) -> "VADResult": ...


@dataclass(frozen=True, slots=True)
class SpeechRegion:
    start_ms: int
    end_ms: int
    confidence: float | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.start_ms, int) or isinstance(self.start_ms, bool)
                or self.start_ms < 0
                or not isinstance(self.end_ms, int) or isinstance(self.end_ms, bool)
                or self.end_ms <= self.start_ms):
            raise ValueError("speech region boundaries must be non-negative integer milliseconds with end > start")
        if self.confidence is not None and (
            not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("speech region confidence must be finite and between 0 and 1")

@dataclass(frozen=True, slots=True)
class VADResult:
    provider: str
    model: str
    audio_sha256: str
    sample_rate: int
    duration_ms: int
    regions: tuple[SpeechRegion, ...]

    def __post_init__(self) -> None:
        for name, value in (("provider", self.provider), ("model", self.model)):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{name} must be a non-empty bounded string")
        if not isinstance(self.audio_sha256, str) or not _SHA256.fullmatch(self.audio_sha256):
            raise ValueError("audio_sha256 must be lowercase SHA-256")
        if (not isinstance(self.sample_rate, int) or isinstance(self.sample_rate, bool)
                or self.sample_rate not in (8000, 16000, 32000, 48000)):
            raise ValueError("sample_rate must be one of 8, 16, 32 or 48 kHz")
        if not isinstance(self.duration_ms, int) or isinstance(self.duration_ms, bool) or self.duration_ms < 1:
            raise ValueError("duration_ms must be a positive integer")
        if not isinstance(self.regions, (tuple, list)):
            raise ValueError("regions must be a sequence of SpeechRegion values")
        regions = tuple(self.regions)
        prior_end = 0
        for region in regions:
            if not isinstance(region, SpeechRegion):
                raise ValueError("regions must contain only SpeechRegion values")
            if region.start_ms < prior_end or region.end_ms > self.duration_ms:
                raise ValueError("VAD regions must be ordered, non-overlapping and within audio duration")
            prior_end = region.end_ms
        object.__setattr__(self, "regions", regions)

class SileroVADAdapter:
    """Silero VAD with bundled/local model loading; no runtime model download."""
    provider_name = "silero-vad"
    def __init__(self, *, model=None, timestamp_fn=None, threshold=0.5,
                 min_speech_duration_ms=100, min_silence_duration_ms=100):
        if (not isinstance(threshold, (int, float)) or isinstance(threshold, bool)
                or not math.isfinite(threshold) or not 0.0 < threshold < 1.0):
            raise ValueError("threshold must be finite and between 0 and 1")
        if (not isinstance(min_speech_duration_ms, int) or isinstance(min_speech_duration_ms, bool)
                or min_speech_duration_ms < 1
                or not isinstance(min_silence_duration_ms, int) or isinstance(min_silence_duration_ms, bool)
                or min_silence_duration_ms < 0):
            raise ValueError("VAD duration options must be integer milliseconds in range")
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

    def detect_request(self, request: VADRequest) -> VADResult:
        if not isinstance(request, VADRequest):
            raise TypeError("request must be a VADRequest")
        return self.detect(request.samples, sample_rate=request.sample_rate, audio_sha256=request.audio_sha256)

    def detect(self, samples, *, sample_rate: int, audio_sha256: str) -> VADResult:
        if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate not in (8000, 16000):
            raise ValueError("Silero VAD accepts 8 kHz or 16 kHz audio")
        request = VADRequest(tuple(samples), sample_rate, audio_sha256)
        values = array("f", request.samples)
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
            if not isinstance(item, dict):
                raise ValueError("VAD model timestamps must be mappings")
            start, end = item.get("start"), item.get("end")
            if (not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)
                    or start < 0 or end <= start or end > len(values)):
                raise ValueError("VAD model returned invalid integer sample boundaries")
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
        if (not isinstance(aggressiveness, int) or isinstance(aggressiveness, bool)
                or aggressiveness not in (0,1,2,3)
                or not isinstance(frame_duration_ms, int) or isinstance(frame_duration_ms, bool)
                or frame_duration_ms not in (10,20,30)):
            raise ValueError("invalid WebRTC VAD configuration")
        if vad is None:
            try:
                import webrtcvad
                vad = webrtcvad.Vad(aggressiveness)
            except ImportError as exc:
                raise RuntimeError("install webrtcvad-wheels to use WebRTC VAD") from exc
        if not callable(getattr(vad, "is_speech", None)):
            raise TypeError("vad must expose a callable is_speech(frame, sample_rate)")
        self.vad, self.frame_duration_ms = vad, frame_duration_ms
    def detect_request(self, request: VADRequest) -> VADResult:
        if not isinstance(request, VADRequest):
            raise TypeError("request must be a VADRequest")
        pcm16 = b"".join(struct.pack("<h", round(sample * 32767)) for sample in request.samples)
        return self.detect(pcm16, sample_rate=request.sample_rate, audio_sha256=request.audio_sha256)

    def detect(self, pcm16: bytes, *, sample_rate: int, audio_sha256: str) -> VADResult:
        if (not isinstance(sample_rate, int) or isinstance(sample_rate, bool)
                or sample_rate not in (8000,16000,32000,48000)):
            raise ValueError("unsupported WebRTC sample rate")
        if not isinstance(pcm16, bytes) or not pcm16 or len(pcm16) % 2:
            raise ValueError("audio must be non-empty mono PCM16")
        if not isinstance(audio_sha256, str) or not _SHA256.fullmatch(audio_sha256):
            raise ValueError("audio_sha256 must be lowercase SHA-256")
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
    if (not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 1
            or not isinstance(frame_ms, int) or isinstance(frame_ms, bool) or frame_ms < 1):
        raise ValueError("duration_ms and frame_ms must be positive integers")
    for name, regions in (("reference", reference), ("hypothesis", hypothesis)):
        if not isinstance(regions, (tuple, list)):
            raise ValueError(f"{name} regions must be a sequence of SpeechRegion values")
        prior_end = 0
        for region in regions:
            if not isinstance(region, SpeechRegion) or region.end_ms > duration_ms:
                raise ValueError(f"{name} regions must be SpeechRegion values within duration_ms")
            if region.start_ms < prior_end:
                raise ValueError(f"{name} regions must be ordered and non-overlapping")
            prior_end = region.end_ms
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
