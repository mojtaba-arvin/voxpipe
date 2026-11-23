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

    def __post_init__(self) -> None:
        if not isinstance(self.language, str) or not self.language.strip():
            raise ValueError("language must be a non-empty label")
        if (not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool)
                or not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0):
            raise ValueError("language confidence must be finite and between 0 and 1")
        if not isinstance(self.start_ms, int) or isinstance(self.start_ms, bool) or self.start_ms < 0:
            raise ValueError("start_ms must be a non-negative integer")
        if self.end_ms is not None and (
            not isinstance(self.end_ms, int) or isinstance(self.end_ms, bool) or self.end_ms <= self.start_ms
        ):
            raise ValueError("end_ms must be an integer greater than start_ms")
        for name, value in (("provider", self.provider), ("model_version", self.model_version)):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{name} must be a non-empty bounded string")

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

    @staticmethod
    def _class_labels(model, count: int):
        encoder = getattr(getattr(model, "hparams", None), "label_encoder", None)
        inventory = getattr(encoder, "ind2lab", None)
        if inventory is None:
            raise ValueError("SpeechBrain model does not expose its complete label inventory")
        try:
            return [inventory[index] for index in range(count)]
        except (KeyError, IndexError, TypeError):
            raise ValueError("SpeechBrain label inventory does not match model output dimensions") from None

    def identify(self, samples, *, sample_rate: int, start_ms: int=0) -> LanguagePrediction:
        if sample_rate!=16000: raise ValueError("language identification requires mono 16 kHz audio")
        raw_values = list(samples)
        if any(isinstance(x, bool) for x in raw_values):
            raise ValueError("audio samples must be numeric normalized values")
        try:
            values = [float(x) for x in raw_values]
        except (TypeError, ValueError, OverflowError):
            raise ValueError("audio samples must be numeric normalized values") from None
        if len(values) < 16000 or any(
            not math.isfinite(x) or not -1.0 <= x <= 1.0 for x in values
        ):
            raise ValueError("language identification requires at least one second of finite normalized audio")
        if not isinstance(start_ms, int) or isinstance(start_ms, bool) or start_ms < 0:
            raise ValueError("start_ms must be a non-negative integer")
        if hasattr(self.model,"classify"):
            probabilities, labels=self.model.classify(values,sample_rate)
            probabilities=[float(x) for x in probabilities]
        else:
            import torch
            with torch.inference_mode():
                out_prob, _, _, _ = self.model.classify_batch(
                    torch.tensor(values,dtype=torch.float32).unsqueeze(0)
                )
                logits = out_prob.detach().cpu().reshape(-1).tolist()
            labels = self._class_labels(self.model, len(logits))
            peak = max(logits)
            exponentials = [math.exp(value-peak) for value in logits]
            total = sum(exponentials)
            probabilities = [value/total for value in exponentials]
        if not probabilities or len(probabilities)!=len(labels):
            raise ValueError("language model returned inconsistent scores")
        if any(not math.isfinite(score) or not 0.0 <= score <= 1.0 for score in probabilities):
            raise ValueError("language model returned invalid probability scores")
        index=max(range(len(probabilities)),key=probabilities.__getitem__)
        confidence=probabilities[index]
        label=self._label(labels[index])
        if not label:
            raise ValueError("language model returned an empty language label")
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
    for reference, prediction in zip(references, predictions):
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("reference labels must be non-empty strings")
        if not isinstance(prediction.language, str) or not prediction.language.strip():
            raise ValueError("predicted language labels must be non-empty strings")
        if (not isinstance(prediction.confidence, (int, float)) or isinstance(prediction.confidence, bool)
                or not math.isfinite(prediction.confidence) or not 0 <= prediction.confidence <= 1):
            raise ValueError("prediction confidence must be finite and between 0 and 1")
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
    accepted=[(reference,prediction) for reference,prediction in zip(references,predictions)
              if prediction.language != "und"]
    coverage=len(accepted)/len(references)
    selective_accuracy=(sum(reference==prediction.language for reference,prediction in accepted)/len(accepted)
                        if accepted else None)
    return {"accuracy":sum(r==p.language for r,p in zip(references,predictions))/len(references),
            "macro_f1":sum(x["f1"] for x in per_class.values())/len(per_class),
            "per_class":per_class,"confusion_matrix":confusion,"expected_calibration_error":ece,
            "coverage":coverage,"selective_accuracy":selective_accuracy,"abstention_rate":1.0-coverage}


def calibrate_abstention_threshold(references, predictions, *, min_selective_accuracy: float=0.90,
                                   min_coverage: float=0.50) -> dict:
    """Choose the lowest confidence threshold meeting validation-set accuracy and coverage floors.

    Run only on a calibration split disjoint from the final test split. Predictions must
    contain the model top label (not a thresholded ``und`` label), so the candidate
    thresholds can be evaluated without rerunning inference.
    """
    if len(references)!=len(predictions) or not references:
        raise ValueError("equal non-empty reference/prediction arrays required")
    for value, name in ((min_selective_accuracy, "min_selective_accuracy"), (min_coverage, "min_coverage")):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be finite and between 0 and 1")
    for reference, prediction in zip(references, predictions):
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("reference labels must be non-empty strings")
        if not isinstance(prediction.language, str) or not prediction.language.strip() or prediction.language == "und":
            raise ValueError("calibration predictions must contain an unthresholded top language label")
        if (not isinstance(prediction.confidence, (int, float)) or isinstance(prediction.confidence, bool)
                or not math.isfinite(prediction.confidence) or not 0 <= prediction.confidence <= 1):
            raise ValueError("prediction confidence must be finite and between 0 and 1")
    candidates=sorted({0.0, *(float(prediction.confidence) for prediction in predictions)})
    feasible=[]
    for threshold in candidates:
        accepted=[(reference,prediction) for reference,prediction in zip(references,predictions)
                  if prediction.confidence >= threshold]
        coverage=len(accepted)/len(references)
        accuracy=(sum(reference==prediction.language for reference,prediction in accepted)/len(accepted)
                  if accepted else None)
        if coverage >= min_coverage and accuracy is not None and accuracy >= min_selective_accuracy:
            feasible.append((coverage, threshold, accuracy))
    if not feasible:
        return {"feasible":False,"threshold":None,"coverage":0.0,"selective_accuracy":None,
                "min_coverage":float(min_coverage),"min_selective_accuracy":float(min_selective_accuracy)}
    coverage,threshold,accuracy=max(feasible,key=lambda item:(item[0],-item[1]))
    return {"feasible":True,"threshold":threshold,"coverage":coverage,"selective_accuracy":accuracy,
            "min_coverage":float(min_coverage),"min_selective_accuracy":float(min_selective_accuracy)}
