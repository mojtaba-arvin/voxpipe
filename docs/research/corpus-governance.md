# Speech corpus governance

## Default boundary

Public benchmark data and customer recordings are separate data classes. Public benchmark workflows in this repository use only version-pinned public datasets with an explicit dataset license. Customer audio is never copied into a public benchmark, committed to the repository, or sent to a hosted inference provider by default. Benchmark CI runs on public corpora only.

A public reuse license is not the same thing as an individual consent record. The manifest therefore records `consent_basis=public-dataset-license` and an explanatory note where the dataset does not publish per-speaker consent/session metadata. Do not describe such a dataset as individually consented unless the source explicitly documents that basis.

## Versioning and provenance

Every evaluation manifest must include:
- Dataset identifier, immutable repository revision, split/config and source-file SHA-256
- License identifier and required attribution
- Consent basis and a note about missing consent/session metadata
- Per-item stable sample ID, audio SHA-256, reference-text SHA-256, language and duration
- Speaker/session IDs only when the source publishes them; otherwise null
- Manifest schema version and generation timestamp

Validate manifests with `contracts/schemas/speech-corpus-manifest.v1.schema.json` and `scripts/validate_corpus_manifest.py`. Do not accept mutable branch names as dataset revisions or silently substitute a different split.

## Current public benchmark sources

- **Google FLEURS**: CC BY 4.0. Preserve the FLEURS attribution in every published summary. The current benchmark pins revision `6989e5bd2dc37546a93ae5d857bc04325e6a3738` and records individual parquet/audio/reference checksums. Its public test split does not provide the per-recording speaker/session consent metadata needed for speaker-attributed conversational ground truth.
- **Neyshekar V6.0**: CC0-1.0, DOI `10.5281/zenodo.18073632`. The controlled diarization benchmark uses the published anonymous speaker labels only to construct a synthetic read-speech test. It must not be used to infer or reveal any speaker's real-world identity.

## Retention, deletion and redistribution

- Do not commit raw audio or model weight files. Commit only derived metrics, manifests, transcript hypotheses and checksums when their source license permits redistribution.
- Preserve FLEURS attribution in the repository and any exported report.
- Do not repackage upstream model weights; record model revision and weight checksums instead.
- Workflow artifacts are retained for 30 days unless a repository result file is committed. Deleting a benchmark result requires removing the associated manifest, per-item outputs and summary together.
- If a source license changes, a revision is withdrawn, or attribution is incomplete, stop the affected benchmark and remove the affected derived artifacts before further redistribution.
- Customer deletion requests must be handled in the customer-data store and audit process; public benchmark cleanup is not a substitute for customer-data deletion.

## Limitations

The current public datasets support reproducible ASR, multilingual language-ID smoke tests, VAD utterance-envelope proxies and controlled speaker-clustering tests. They do not provide a complete Persian frame-labelled VAD corpus or natural multi-speaker conversational diarization corpus with explicit consent and time-aligned turns. Those remain separate acceptance gates; do not fill the gap with synthetic labels presented as gold ground truth.
