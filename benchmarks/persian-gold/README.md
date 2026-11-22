# Persian structured-extraction gold set

**Status: protocol ready; annotation data not yet collected.** This directory intentionally contains no fabricated or model-generated gold labels.

## Purpose

Measure semantic extraction quality for `fa-IR` independently of the Ollama integration smoke test. The benchmark covers the five contract collections: `entities`, `events`, `topics`, `relations`, and `action_items`.

## Required annotation workflow

1. Collect consented Persian transcripts representative of the intended deployment domain. Remove or consistently pseudonymize personal, financial, credential, and other sensitive content before annotation. Record provenance and consent outside the public dataset; never commit raw identifying transcripts.
2. Split by conversation/document before annotation into train/dev/test. Keep near-duplicate conversations and revisions in one split. Freeze the test split and do not use it for prompt tuning.
3. Two Persian-proficient annotators independently annotate every item. They must not see model predictions or each other's labels.
4. A third qualified reviewer adjudicates every disagreement. Only adjudicated records count toward benchmark scores.
5. Annotators label all five collections, including explicitly empty collections. An empty collection means the transcript was reviewed and no supported claim of that type exists; it must not mean "not annotated".
6. Every claim must cite one or more exact source spans. Offsets are zero-based Unicode code-point offsets into the exact segment string; verify `text[start_char:end_char] == quote`. Do not paraphrase evidence.
7. Record ambiguity and unsupported/inferential candidates in the annotation discussion log, not as positive claims. Do not infer unstated assignees, dates, identities, relations, or intentions.

## Annotation policy

- **Entities:** concrete named people, organizations, locations, and documents explicitly referenced. Apply the project ontology consistently; do not label generic pronouns as entities.
- **Events:** explicitly stated occurrences, including scheduled meetings and completed actions. Preserve literal temporal expressions; do not normalize an expression such as «جمعه» into a calendar date.
- **Topics:** substantive subjects discussed, not every noun, attribute, event paraphrase, or generic conversation label.
- **Relations:** only directly asserted relationships. Do not infer a relation from co-occurrence or from speech attribution alone.
- **Action items:** explicit actionable obligations or requests. A scheduled meeting or completed action is not itself an action item. Preserve stated assignee and due-date wording; leave absent fields absent.

A claim is positive only when its category and value are directly supported by the transcript. Exact evidence validity and semantic entailment are separate checks: a matching quote does not automatically make the claim correct.

## Record format

Use one UTF-8 JSON object per line. The machine-readable shape is in `record.schema.json`; `annotations.template.jsonl` is an intentionally empty starter file. Keep source segments exactly as annotated; do not normalize punctuation, whitespace, Arabic/Persian code points, or digits after offsets are assigned.

Each record contains:
- a stable opaque `sample_id`, split, language, and exact `transcript_segments`;
- all five annotation arrays under `annotations`;
- review status and opaque annotator/reviewer IDs;
- a dataset version and provenance reference that does not disclose private source identity.

The benchmark evaluator must reject records that are not adjudicated, omit any collection, contain invalid offsets, duplicate claim annotations, or have missing review metadata. Never silently drop invalid rows.

## Reporting

Report precision, recall, and F1 separately for each of the five collections, plus micro and macro scores. Also publish counts (documents, claims, annotator disagreements, adjudicated records), split sizes, and exact-evidence validity. Include false-positive/false-negative examples after privacy review. Do not report a single aggregate score without per-category counts.

The current `scripts/run_ollama_structured_smoke.py` fixtures remain integration smoke tests only. Their exact-value scores are not gold-set benchmark results. Do not relabel them as human-reviewed.

## Versioning and release gate

- Increment the dataset version whenever annotations, ontology rules, or split membership change.
- Publish a SHA-256 checksum for each frozen JSONL split and record the annotation protocol revision.
- Keep test annotations access-controlled where practical; never tune against test errors.
- A quality claim requires a non-empty, fully adjudicated test split and published per-category metrics. Until then, benchmark status remains **not evaluated**.

## Local validation and evaluation

Run `python scripts/evaluate_persian_gold.py validate` (also included in `make check`) to validate every populated split against the JSON Schema, manifest record counts, SHA-256 checksums, adjudicator independence, unique sample IDs/claims, and exact Unicode code-point evidence offsets. Empty, not-yet-collected splits are valid while the manifest remains `not_started`; this does **not** mean the benchmark is ready.

After all records are double-annotated and adjudicated, freeze each `{train,dev,test}.jsonl` file, update the corresponding record counts and SHA-256 values in `manifest.json`, and set `human_reviewed: true` and `status: ready`. The evaluator refuses to score before these gates pass.

Predictions must be UTF-8 JSONL with one object per test sample, shaped as `{"sample_id":"…","annotations":{"entities":[],"events":[],"topics":[],"relations":[],"action_items":[]}}`; each claim contains a `value` object. Run:

```sh
python scripts/evaluate_persian_gold.py evaluate --predictions path/to/predictions.jsonl
```

Prediction coverage must match the frozen test split exactly. The report includes per-category TP/FP/FN, precision/recall/F1, micro and macro scores, and test-set checksum. Matching is exact canonical JSON-value multiset matching; it does not award semantic equivalence and must be supplemented by reviewed error analysis. Never tune against the test split.
