# Persian STT Benchmark: corpus and execution protocol

**Status:** protocol prepared; no VoxPipe model scores are claimed yet.  
**Normalization:** `fa-norm-v1` in `scripts/benchmark_stt.py`.  
**Primary implementation:** local faster-whisper adapter (VP-0022).

## 1. Corpus policy

Use two public, independently maintained test sets. Keep corpus licensing and attribution attached to every run. Do not commit audio or full transcripts to this repository; store a manifest and checksums, and provision the files into an ignored local benchmark directory.

| Corpus | Intended use | Split | License / constraints |
|---|---|---|---|
| Neyshekar v6.0 | Persian speech with speaker diversity; primary Persian set | Test only | CC0 as declared by the dataset release. Pin the exact dataset revision and verify the v6.0 release metadata before download. |
| Google FLEURS Persian | Cross-corpus read-speech comparison | `fa_ir`, Test | CC BY 4.0; preserve attribution and license notices in benchmark reports. |

Sources:
- Neyshekar dataset: https://huggingface.co/datasets/shekar-ai/neyshekar
- Neyshekar v6.0 record: https://zenodo.org/records/22223171
- Google FLEURS: https://huggingface.co/datasets/google/fleurs
- FLEURS paper: https://arxiv.org/abs/2205.12446

The manifest in `benchmarks/corpora/persian.yaml` is intentionally not populated with guessed commit hashes or local paths. Before a run, resolve and record immutable dataset revisions, exact split/configuration, file count, archive checksum, license metadata, and local file checksums. A moving `main` or `latest` dataset reference is not reproducible.

## 2. Ground-truth validation

For every selected item:
1. Require a paired audio file and human-provided dataset reference text from the same dataset record.
2. Verify audio decodes completely and has non-zero duration.
3. Verify reference text is non-empty UTF-8 and preserve the exact original.
4. Record the dataset row ID, speaker/session ID if provided, split, audio SHA-256, reference SHA-256, duration, sample rate, channel count and license identifier.
5. Reject duplicate audio hashes across train/dev/test and reject speaker leakage where speaker IDs are available.
6. Flag suspicious audio/text pairs (empty transcript, implausible duration/text ratio, duplicate transcript, corrupted decode) for review; never silently repair the reference.
7. Keep rejected IDs and reasons in the run manifest so the effective test population is auditable.

Do not use ASR-generated transcripts as ground truth. Forced alignment may assist human review but does not independently establish transcript correctness.

## 3. Persian normalization

The scorer implements `fa-norm-v1`:
- Unicode NFC.
- Arabic Yeh/Alef Maksura/Kaf variants mapped to Persian forms.
- Arabic and Persian digits mapped to ASCII digits.
- Arabic diacritics and tatweel removed.
- ZWNJ treated as a token boundary.
- Unicode punctuation removed and whitespace collapsed.
- Case-folding for mixed Latin/Persian text.

Both conservative raw scores and normalized scores are reported. Normalization can change tokenization and is not lossless; preserve raw references/hypotheses and never compare scores produced with different normalization versions. The scorer's character metric uses whitespace-joined normalized tokens; state this definition when publishing CER.

## 4. Inference protocol

- Run local faster-whisper only; do not upload these public benchmark files to hosted APIs by default.
- Pin faster-whisper, CTranslate2, model repository revision, model file digests, compute type, device, beam size, VAD options, language hint and word timestamp option.
- Provision model weights before an offline run; disable runtime downloads.
- Warm up once per model/device, then execute every item in a fixed deterministic order. Run at least three timed repetitions for performance measurements and retain per-run raw records.
- Report CPU model, RAM, OS, Python, FFmpeg/PyAV, GPU/driver/VRAM if applicable, thread count and power mode.
- Do not mix first-time model download or cold-start time into steady-state RTF. Report cold-start separately.
- Keep inference concurrency and resource limits fixed between compared models.

## 5. Metrics and reporting

Report per corpus and aggregate:
- Raw WER/CER and `fa-norm-v1` WER/CER.
- Micro and macro scores; do not hide corpus imbalance in a single aggregate.
- RTF, p50/p95 per-file latency, peak RSS/VRAM, decode/inference failures and empty outputs.
- Confidence intervals (speaker/session-clustered bootstrap where IDs exist).
- Stratified error review for dialect/register, code-switching, names, numbers, noisy/telephone audio and short utterances where labels support those strata.
- Word timestamp boundary error only when a trusted word-aligned reference exists.
- Diarization DER/JER only on a corpus with speaker/time annotations and a declared overlap/collar policy.

Do not report diarization or VAD quality from unlabelled speech corpora. Do not infer that read-speech performance predicts conversational performance.

## 6. Result record

One JSONL row per audio file must retain at least:
`corpus`, `corpus_revision`, `split`, `item_id`, `audio_sha256`, `reference_sha256`, `reference`, `hypothesis`, `audio_duration_seconds`, `inference_seconds`, `provider`, `model`, `model_revision`, `device`, `compute_type`, `normalization`, and optional `cost_per_audio_minute`.

Do not commit private audio, raw customer transcripts, credentials, or unreviewed personal data. Public benchmark results may be committed only with attribution and licensing notices.

## 7. Completion gate

VP-0022 can be marked Done only after:
- Immutable corpus and model revisions are pinned and checksummed.
- The selected Test split is downloaded and validated with a rejection report.
- Real faster-whisper inference has completed on both corpora on identified hardware.
- Raw per-file results and aggregate reports are reproducible from the committed manifest.
- No quality/performance figures are copied from vendor claims or invented.
- CI runs the offline scorer and contract tests; live inference remains an explicit, separately recorded benchmark operation.
