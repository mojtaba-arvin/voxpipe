# Common Voice Persian full-archive ASR evaluation

## Pinned source

- Dataset: Common Voice Scripted Speech 27.0 - Persian
- Dataset ID: `cmu5xazgy00finq074vaqo1b3`
- Release datasheet: `cv-corpus-27.0-2026-09-11`
- Locale / task: `fa` / ASR
- License: CC0-1.0
- Dataset page: https://mozilladatacollective.com/datasets/cmu5xazgy00finq074vaqo1b3
- Verified archive: 11,236,660,956 bytes; SHA-256 `f71fd362846433376b529d3f2ce4258a4075d9da8fe2b0f4b12465c12672a65d`
- Archive inventory: 395,173 audio files and 10 TSV metadata files.

## Download and selection policy

Use the official `datacollective==0.8.1` SDK and publisher `download_dataset()` API. The repository secret `MDC_API_KEY` is required, and the dataset terms/access must be authorized for the key's organization. Do not use third-party mirrors or external inference.

The complete archive is downloaded to job-local temporary storage, validated for publisher identity/license, byte size, SHA-256, tar member/path safety, uncompressed-size limits, and audio/TSV presence. The evaluator reads only the first 50 matching rows from `validated.tsv`, extracts only those clips to a temporary directory, and removes the archive and temporary files after the job. The archive is never committed or uploaded.

## Persian/Arabic language validation

The previous automatic Qwen3-ASR path classified the first Persian Common Voice clip as Arabic and correctly stopped. Do not accept Arabic as Persian or weaken that check.

The evaluator now:
1. Runs independent local SpeechBrain VoxLingua107 ECAPA language identification on every selected audio clip. Model revision `0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9` is pinned; only `fa` and `ar` are in the accepted inventory.
2. Requires a Persian (`fa`) prediction at confidence >= 0.65 before ASR. Arabic, low-confidence, and other/undetermined outcomes are counted separately and rejected.
3. Calls Transformers 5.20.1 `apply_transcription_request(audio=..., language="Persian")` for clips that pass the independent audio-language gate. Forced language is a decoding instruction, not proof of the audio language.
4. Publishes WER/CER only if all 50 requested clips pass the independent language gate and produce non-empty Persian hypotheses. Otherwise the aggregate report has `status=failed` and null WER/CER; it does not calculate metrics on a reduced or mixed-language set.

Qwen model: `Qwen/Qwen3-ASR-0.6B-hf`, revision `40946090cac63fc01d723a83174e16515073d7c0`, CPU float32, batch size 1. Language-ID model: `speechbrain/lang-id-voxlingua107-ecapa`, Apache-2.0, pinned revision above. The language-ID confidence threshold is recorded in the aggregate report; it is a conservative gate, not a Common Voice-calibrated probability guarantee.

## Privacy and interpretation

Raw audio, references, hypotheses, client IDs, speaker IDs and per-clip language predictions remain temporary and are never uploaded, committed or logged. Only aggregate WER/CER, aggregate language-gate failure counts, model/runtime configuration and dataset provenance may be published.

Common Voice is scripted read-speech, not natural conversation or human-gold annotation. CC0 licensing does not establish individual consent for natural-conversation use. These results cannot establish conversational ASR, diarization, speaker identity, production accuracy or medical readiness.
