# Speech corpus governance

VoxPipe evaluation data is separated from customer data. Public benchmark inputs are fetched only from the exact dataset revision and split recorded in the manifest. The manifest stores source-file SHA-256, per-audio SHA-256, reference-text SHA-256, language, duration, speaker/session IDs when supplied, license, attribution, consent basis and generation timestamp.

## Initial public benchmark

The Persian ASR workflow uses the FLEURS `google/fleurs` `fa_ir` test split at immutable revision `6989e5bd2dc37546a93ae5d857bc04325e6a3738`. The split is licensed CC BY 4.0 and the workflow includes attribution. Its pinned test Parquet SHA-256 is `e8982e2e5bb7c505c90aed9c5dde5c533076f6b74d1104d250cfc1ae7cb02a36`. The workflow records the actual raw audio and reference checksums for every sample processed.

This FLEURS split does not expose speaker/session identity or speaker-level consent metadata. The manifest records those fields as null and uses `public-dataset-license` as its legal basis; it is not used as diarization ground truth and no real-world speaker identity is inferred.

Neyshekar v6.0 (Zenodo DOI `10.5281/zenodo.22223171`) is a separate CC0 Persian ASR source with anonymous speaker IDs and predefined splits. Its speaker IDs may be used only as anonymous dataset labels. Do not attempt to identify speakers. A sample is eligible only after the exact release revision and item checksum have been recorded.

## Customer data

Customer audio and transcripts never enter public manifests, public CI artifacts or benchmark caches by default. Customer evaluation requires documented customer consent, a tenant-isolated private artifact store, a retention deadline, and explicit opt-in. Deletion requests must propagate to derived manifests and benchmark artifacts.

## Validation

`contracts/schemas/speech-corpus-manifest.v1.schema.json` defines the manifest contract. `scripts/validate_corpus_manifest.py` validates schema, unique sample IDs, checksums and task-specific speaker metadata. A diarization manifest is rejected if any sample lacks a speaker ID.
