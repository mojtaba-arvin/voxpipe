# Persian annotation agreement and adjudication

Compare two independently produced JSONL annotation exports against the same immutable transcript segments:

```bash
python scripts/compare_persian_annotations.py \
  --annotator-a annotations/a.jsonl \
  --annotator-b annotations/b.jsonl \
  --disagreements-output review/adjudication-queue.jsonl
```

The stdout report contains per-category and micro exact-value agreement plus exact evidence-span agreement. It is a pairwise diagnostic, not Cohen's kappa, not a semantic correctness score, and not a gold label.

When `--disagreements-output` is supplied, the tool writes a deterministic JSONL review queue containing:
- sample ID and extraction category;
- disagreement type: `annotator_a_only`, `annotator_b_only`, or `evidence_mismatch`;
- the disputed claim value;
- each annotator's presence flag and exact evidence spans.

The queue deliberately omits transcript segment text. An adjudicator can use the sample/segment IDs and offsets to open the immutable source in the controlled review environment. The writer uses a temporary file and atomic replacement and refuses to overwrite either annotator input.

The queue is a human-review aid only. Adjudicated decisions must be made by a qualified reviewer, recorded with reviewer identity/role, timestamp and rationale, and exported as a separate versioned artifact. Neither annotator output nor this disagreement queue is automatically promoted to gold.
