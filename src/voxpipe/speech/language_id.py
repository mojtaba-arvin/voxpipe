"""Local spoken-language identification and calibration metrics."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import math

@dataclass(frozen=True, slots=True)
class LanguagePrediction:
    language: str
    confidence: float
    start_ms: int = 0
    end_ms: int | None = None
    provider: str = "speechbrain-voxlingua107"
    model_version: str = "unknown"

class SpeechBrainLanguageIdentifier:
    """SpeechBrain VoxLingua107 adapter; model weights must already exist locally."""
    provider_name="speechbrain-voxlingua107"
    def __init__(self, *, model=None, model_dir: str|Path|None=None, threshold: float=0.65,
                 model_version="voxlingua107-ecapa"):
        if not 0.0 <= threshold <= 1.0: raise ValueError("threshold must be in [0,1]")
        if model is None:
            if model_dir is None or not Path(model_dir).is_dir():
                raise FileNotFoundError("provide a pre-provisioned local SpeechBrain model directory")
            from speechbrain.inference.classifiers import EncoderClassifier
            model=EncoderClassifier.from_hparams(source=str(model_dir),savedir=str(model_dir),run_opts={"device":"cpu"})
        self.model,self.threshold,self.model_version=model,threshold,model_version
    @staticmethod
    def _label(value):
        label=str(value).strip()
        if ":" in label: label = label.split(":",1)[0].strip()
        if label.startswith("__label__"): label=label[len("__label__"):]
        return label.lower()
    def identify(self, samples, *, sample_rate: int, start_ms: int=0) -> LanguagePrediction:
        if sample_rate!=16000: raise ValueError("language identification requires mono 16 kHz audio")
        values=[float(x) for x in samples]
        if len(values)<16000 or any(not math.isfinite(x) for x in values):
            raise ValueError("language identification requires at least one second of finite audio")
        if hasattr(self.model,"classify"):
            probabilities, labels=self.model.classify(values,sample_rate)
            probabilities=[float(x) for x in probabilities]
        else:
            import torch
            with torch.inference_mode():
                out_prob,_,_,labels=self.model.classify_batch(torch.tensor(values,dtype=torch.float32).unsqueeze(0))
                probs=torch.softmax(out_prob,dim=-1).squeeze(0)
                probabilities=[float(x) for x in probs.detach().cpu().tolist()]
        if not probabilities or len(probabilities)!=len(labels): raise ValueError("language model returned inconsistent scores")
        index=max(range(len(probabilities)),key=probabilities.__getitem__)
        confidence=probabilities[index]
        if not math.isfinite(confidence) or not 0<=confidence<=1: raise ValueError("invalid language confidence")
        label=self._label(labels[index])
        return LanguagePrediction(label if confidence>=self.threshold else "und",confidence,start_ms,
                                   start_ms+round(len(values)*1000/sample_rate))
    def identify_windows(self,samples,*,sample_rate=16000,window_ms=4000,hop_ms=2000):
        if window_ms<1000 or hop_ms<1: raise ValueError("invalid language window configuration")
        width=sample_rate*window_ms//1000; hop=sample_rate*hop_ms//1000
        if len(samples) < width:
            return (self.identify(samples, sample_rate=sample_rate),) if len(samples) >= sample_rate else ()
        output=[]
        for start in range(0,max(0,len(samples)-width+1),hop):
            output.append(self.identify(samples[start:start+width],sample_rate=sample_rate,
                                         start_ms=round(start*1000/sample_rate)))
        return tuple(output)

def evaluate_language_id(references, predictions, *, bins: int=10) -> dict:
    if len(references)!=len(predictions) or not references: raise ValueError("equal non-empty reference/prediction arrays required")
    if bins<1: raise ValueError("bins must be positive")
    labels=sorted(set(references)|{p.language for p in predictions})
    confusion={r:{p:0 for p in labels} for r in labels}
    tp=Counter(); fp=Counter(); fn=Counter()
    for ref,pred in zip(references,predictions):
        confusion[ref][pred.language]+=1
        if ref==pred.language: tp[ref]+=1
        else: fp[pred.language]+=1; fn[ref]+=1
    per_class={}
    for label in labels:
        precision=tp[label]/(tp[label]+fp[label]) if tp[label]+fp[label] else 0.0
        recall=tp[label]/(tp[label]+fn[label]) if tp[label]+fn[label] else 0.0
        per_class[label]={"precision":precision,"recall":recall,
                          "f1":2*precision*recall/(precision+recall) if precision+recall else 0.0}
    ece=0.0
    for b in range(bins):
        group=[(ref,pred) for ref,pred in zip(references,predictions)
               if min(bins-1,int(pred.confidence*bins))==b]
        if group:
            accuracy=sum(ref==pred.language for ref,pred in group)/len(group)
            confidence=sum(pred.confidence for _,pred in group)/len(group)
            ece += len(group)/len(references)*abs(accuracy-confidence)
    return {"accuracy":sum(r==p.language for r,p in zip(references,predictions))/len(references),
            "macro_f1":sum(x["f1"] for x in per_class.values())/len(per_class),
            "per_class":per_class,"confusion_matrix":confusion,"expected_calibration_error":ece}
