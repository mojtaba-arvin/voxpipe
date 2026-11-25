# Structured extraction silver evaluation

## Reproducible input

Run `python -m eval.issue32.templates --count 120 --seed 20261125`. The generator accepts 100–200 conversations and uses a fixed seed. It creates four synthetic sales/support scenarios: order confirmation, address change, refund, and callback request. All names, order IDs, and phone values are placeholders; phone strings use the non-dialable `PHONE-PLACEHOLDER-NNN` form. No real PII or medical content is used.

The conversation schema keeps source conversations and utterances distinct from retrieval passages. Each generated record includes template-derived `reference_slots`, a document-level train/dev/test split, and its seed. The sidecar metadata records counts and SHA-256 of the JSONL output.

## Deterministic silver labeling

Run `python -m eval.issue32.rules`. Rules normalize Persian and Arabic-Indic digits, then extract only:

- `date`
- `amount`
- `order_id`
- `customer_name`

A field with no match is `null`. Multiple distinct matches are `ambiguous` and return no selected value. No LLM or external inference service is used. The rules are deliberately narrow; their performance is not a measure of general Persian extraction quality.

## Metrics

Run `python -m eval.issue32.metrics`. Exact-match counts are calculated per field. A wrong non-null value contributes one false positive and one false negative. A missing or ambiguous prediction for a non-null reference contributes a false negative. A prediction for a null reference contributes a false positive. Undefined precision, recall, or F1 denominators produce zero.

The label output is validated against `eval/issue32/labels.schema.json`. The report includes per-field precision/recall/F1, overall micro metrics, overall macro averages, split counts/ratios, seed, and dataset count. Output is validated against `eval/issue32/metrics.schema.json`.

## Interpretation boundary

These reference slots are generated from the same synthetic templates, not human annotations. Rule-based metrics are an automated **silver** pipeline check only. They do not establish human-reviewed Persian quality, production accuracy, real conversational coverage, or medical readiness. The human gold upgrade remains tracked separately in issue #32 and M2.
