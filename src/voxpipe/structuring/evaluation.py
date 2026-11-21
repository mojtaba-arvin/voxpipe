"""Exact-value precision/recall/F1 over human-reviewed gold annotations."""
from collections import Counter
from dataclasses import dataclass
import json
COLLECTIONS=("entities","events","topics","relations","action_items")
@dataclass(frozen=True,slots=True)
class Score:
    true_positive:int
    false_positive:int
    false_negative:int
    precision:float
    recall:float
    f1:float
def _score(tp,fp,fn):
    p=tp/(tp+fp) if tp+fp else (1.0 if fn==0 else 0.0)
    r=tp/(tp+fn) if tp+fn else (1.0 if fp==0 else 0.0)
    return Score(tp,fp,fn,p,r,2*p*r/(p+r) if p+r else 0.0)
def evaluate_claims(predicted,gold):
    per_type={}; totals=[0,0,0]
    for name in COLLECTIONS:
        p=Counter(json.dumps(x.get("value"),ensure_ascii=False,sort_keys=True,separators=(",",":")) for x in predicted.get(name,()))
        g=Counter(json.dumps(x.get("value"),ensure_ascii=False,sort_keys=True,separators=(",",":")) for x in gold.get(name,()))
        tp=sum((p&g).values()); fp=sum(p.values())-tp; fn=sum(g.values())-tp
        per_type[name]=_score(tp,fp,fn)
        totals=[a+b for a,b in zip(totals,(tp,fp,fn))]
    micro=_score(*totals)
    return {"per_type":per_type,"micro":micro,
      "macro":{"precision":sum(s.precision for s in per_type.values())/5,
               "recall":sum(s.recall for s in per_type.values())/5,
               "f1":sum(s.f1 for s in per_type.values())/5},
      "matching":"exact-json-value-multiset-v1",
      "warning":"Meaningful only on human-reviewed, versioned gold annotations."}
