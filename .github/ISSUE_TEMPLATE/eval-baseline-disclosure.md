---
name: Evaluation baseline disclosure
about: Disclose limitations of an automated evaluation baseline
title: "[baseline] "
labels: automated-baseline, silver
---

## Baseline disclosure

- [ ] Data source documented (license, version, speaker count)
- [ ] Execution parameters documented (model, compute_type, batch_size, seed)
- [ ] Metric definitions documented (formula, tie-breaking)
- [ ] Data split documented (proportions, seed)
- [ ] Limitations section states output is **silver**, not gold
- [ ] Ethics statement states no real PII, no medical claim

## What this baseline does NOT claim

- [ ] No human-annotator equivalence
- [ ] No production accuracy claim
- [ ] No natural-conversation audio claim (if #47)
- [ ] No medical readiness claim

## Upgrade path to gold

Requires: real consented data + two independent human annotators
+ adjudication.
