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

The complete archive is downloaded to job-local temporary storage and validated for publisher identity/license, byte size, SHA-256, tar member/path safety, uncompressed-size limits, and audio/TSV presence. The evaluator scans at most 100 validated TSV rows and selects the first 50 clips whose audio duration can be probed and is at least one second. Shorter or unprobeable candidates are counted and skipped before the final sample is fixed; if fewer than 50 eligible clips are found within the scan limit, the run fails closed and WER/CER remain null. After the 50 are fixed, no clip is replaced following a language or transcription failure. Only the selected 50 clips undergo independent language validation and Qwen inference. The workflow does not run forced alignment.

## Persian/Arabic language validation

The previous automatic Qwen3-ASR path classified the first selected Common Voice clip as Arabic and correctly stopped. Do not accept Arabic as Persian or weaken that check.

The evaluator now:
1. Runs independent local SpeechBrain VoxLingua107 ECAPA language identification on every selected audio clip. Model revision `0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9` is pinned; only `fa` and `ar` are in the accepted inventory.
2. Requires the independent model's top language label to be Persian (`fa`) before ASR. The disjoint FLEURS calibration/control benchmark now covers Persian (`fa_ir`), Arabic (`ar_eg`), and English/German negative controls on separate validation and test splits. A selected confidence threshold of `0.0` is explicitly a no-op confidence floor, not a meaningful confidence cutoff; the effective acceptance rule is top-label `fa`, with zero-probability and tied-top predictions abstaining. Arabic (`ar`) and every other/out-of-inventory label are rejected. Arabic and other/undetermined rejections are counted separately. Exact positive-floor FLEURS evidence: run #46145923356 (benchmark code head `7d102ed672a40e90ea29738d45a78cb220335f74`) used 400 validation clips and 400 disjoint held-out test clips (100 each for Persian, Arabic, English and German). Validation calibration selected threshold `0.5` with 94.5% coverage and 100% selective accuracy, satisfying the predeclared 50% coverage / 99% selective-accuracy floors. Held-out test accuracy and coverage were 92.25%, selective accuracy 100%, macro-F1 0.7637; Persian was 100/100 and Arabic 99/100, with zero Persian↔Arabic cross-confusions (one Arabic clip abstained). English/German negative controls produced zero false Persian/Arabic accepts; 30 German clips abstained. The runtime gate uses this 0.5 minimum confidence floor. FLEURS is external control evidence, not a Common Voice accuracy guarantee.
3. Calls Transformers 5.20.1 `apply_transcription_request(audio=..., language="Persian")` for clips that pass the independent audio-language gate. Forced language is a decoding instruction, not proof of the audio language.
4. Publishes WER/CER only if all 50 requested clips pass the independent language gate and produce non-empty Persian hypotheses. Otherwise the aggregate report has `status=failed` and null WER/CER; it does not calculate metrics on a reduced or mixed-language set. The report records the scan limit, candidates scanned, short/unprobeable counts, selected count and any duration shortfall.

Qwen model: `Qwen/Qwen3-ASR-0.6B-hf`, revision `40946090cac63fc01d723a83174e16515073d7c0`, CPU float32, batch size 1. Runtime CPU wheels are pinned to the matching `torch==2.13.0+cpu` and `torchaudio==2.11.0+cpu` from the official PyTorch CPU index. TorchAudio 2.11.0 uses the stable ABI and supports PyTorch 2.13+. This moves the runtime above the Torch 2.11.0 vulnerability fixed in 2.13.0 (GHSA-rrmf-rvhw-rf47). Setuptools is pinned to 84.2.1 to avoid the known advisories affecting 81.0.0. Language-ID model: `speechbrain/lang-id-voxlingua107-ecapa`, Apache-2.0, pinned revision above. The language-ID confidence threshold and FLEURS calibration provenance are recorded in the aggregate report. The accepted-language gate is the model's top class being exactly `fa`; the FLEURS threshold calibration is not a Common Voice accuracy guarantee.

## Privacy and interpretation

Raw audio, references, hypotheses, client IDs, speaker IDs and per-clip language predictions remain job-local temporary data and are never uploaded, committed or logged. Only aggregate WER/CER (when all 50 pass), aggregate duration/language/transcription failure counts, pinned model/runtime configuration and dataset provenance may be published. No alignment, speaker-segment or per-clip artifacts are generated or uploaded. Reported inference time/RTF includes both language identification and Qwen decoding, but excludes model initialization, duration screening and archive download.

Common Voice is scripted read-speech, not natural conversation or human-gold annotation. CC0 licensing does not establish individual consent for natural-conversation use. These results cannot establish conversational ASR, diarization, speaker identity, production accuracy or medical readiness.
