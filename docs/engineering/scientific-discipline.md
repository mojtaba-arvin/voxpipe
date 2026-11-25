# Scientific Discipline Requirements

Six elements required in every output so that results are
**citable and reproducible**. This is for **methodological
quality**, not as a publication goal — the document does not
define publication as success criterion, to avoid goal-directed
bias.

## 1. Data source and license

Exact version (e.g. Common Voice 27.0), license (CC0), speaker
count / hours, and whether data is **read-speech** or
conversational.

## 2. Execution parameters

Model name + \`commit/revision\`, \`compute_type\`, \`batch_size\`,
\`seed\`, thresholds.

## 3. Exact metric definitions

Formula for Recall@5, MRR, nDCG@5; tie-breaking rules.

## 4. Data split and fixed seed

Prevent leakage; record proportions.

## 5. Limitations section

State explicitly: output is **silver**, no human annotator, not
sufficient for absolute accuracy inference; only for
**regression detection**.

## 6. Ethics statement

Synthetic/read-speech data, no real PII, no medical content; no
production-readiness claim for medical domain.
