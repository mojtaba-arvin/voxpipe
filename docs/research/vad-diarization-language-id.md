# VAD, Diarization and Language Identification

**Checked:** 2026-11-21. **Work items:** VP-0023, VP-0024, VP-0025.

## VAD candidates

- **Silero VAD** (MIT): local PyTorch/ONNX inference, supports 8 and 16 kHz, and exposes speech boundaries. The adapter uses the package's locally provisioned model and never downloads weights at request time.
- **WebRTC VAD** (BSD-3-Clause): lightweight frame classifier with 10/20/30 ms frames and 8/16/32/48 kHz support. It is a useful independent baseline, but aggressiveness trades missed speech for false activation.

VoxPipe exposes both behind the same VAD result shape. A CPU comparison ran on 60 pinned FLEURS test utterances (20 each fa_ir, en_us, de_de; revision `6989e5bd2dc37546a93ae5d857bc04325e6a3738`). On the synthetic utterance-envelope proxy, Silero VAD 6.2.3 (threshold 0.5) scored precision 1.000, recall 0.726, F1 0.837, envelope-boundary error 1,553 ms, zero false-activation seconds and 220.75 missed-speech seconds; runtime was 0.168 s/sample. WebRTC VAD 2.0.14.post1 (aggressiveness 2, 30 ms frames) scored precision 0.991, recall 0.813, F1 0.888, envelope-boundary error 481 ms, 5.77 false-activation seconds and 153.3 missed-speech seconds; runtime was 0.00141 s/sample. Combined process peak RSS was 6.93 GB (not per-model memory). These are explicitly envelope-proxy scores: FLEURS has utterance-level audio/text but no frame-level speech/non-speech labels, and internal pauses are counted as missed speech. They are not gold frame-level VAD precision/recall. WebRTC performed better on this proxy and was much faster; Silero was more conservative with no false activations. A production acceptance benchmark still requires a licensed Persian corpus with frame-aligned annotations.

## Diarization

The first local baseline uses SpeechBrain ECAPA-TDNN speaker embeddings (Apache-2.0 model card) with deterministic cosine-centroid clustering over VAD speech windows. Speaker labels are anonymous and scoped to one audio asset. This baseline does not model overlap, turn-taking priors, or conversational channel separation. It is not claimed to be a production Persian diarization system.

DER/JER evaluation supports a configurable reference-boundary collar, optional overlap exclusion, one-to-one speaker mapping, and speaker-count error. ECAPA turn confidence is an uncalibrated cosine similarity, not a probability. A controlled Persian speaker-clustering benchmark now uses Neyshekar V6.0 (CC0-1.0, DOI 10.5281/zenodo.18073632), pinned dataset revision `30696ada746a66bb3047c984744b5ecfd60fe4e9`, and three anonymous speakers with two utterances each in both validation and test splits. The threshold is selected on validation (0.20) and evaluated on disjoint test speakers. Test DER was 23.81% and JER 23.60%, versus 57.14% DER and 85.71% JER for a single-speaker baseline; speaker-count error remained 2. This is a small synthetic concatenation with 250 ms inserted silence, no overlap, and no natural turn-taking. It is not production-quality conversational diarization.

## Language identification

SpeechBrain VoxLingua107 ECAPA is Apache-2.0 and includes Persian among its 107 language classes. The model card reports 6.7% error on its development set (1,609 manually checked clips from 33 languages); that result is not a VoxPipe result and does not establish Persian F1. VoxPipe loads a pre-provisioned local model, returns an explicit `und` outcome below a configurable threshold, and supports sliding windows for code-switch investigation.

A first CPU benchmark ran on 60 pinned FLEURS test samples (20 each from fa_ir, en_us and de_de; dataset revision `6989e5bd2dc37546a93ae5d857bc04325e6a3738`). SpeechBrain VoxLingua107 model revision `0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9` achieved 95.0% accuracy and 0.7297 strict macro-F1 across the union of reference and predicted classes; expected calibration error was 0.0445. Persian precision/recall/F1 were all 1.0 (20/20), English scores were all 1.0 (20/20), and German recall was 0.85 (17/20); three German clips were incorrectly labelled Romanian, introducing a prediction-only class and lowering strict macro-F1. This was run with threshold 0.0 (no abstention), no code-switch samples, and only 20 examples per language. These figures are a small benchmark, not production calibration.

The evaluator reports accuracy, macro-F1, per-class precision/recall/F1, a confusion matrix, and expected calibration error. Thresholds still require calibration on larger representative Persian, English, code-switched, short, noisy and out-of-inventory samples before production use.

## Reproducibility and privacy

Optional runtimes are listed in `requirements-speech.txt`. Model files must be staged and pinned by deployment tooling; no adapter enables network downloads. No customer audio is used in tests. Offline tests inject deterministic models and exercise boundary validation, abstention, clustering, and metric calculations.

## Remaining evidence gates

- VAD: a pinned FLEURS utterance-envelope proxy compares Silero and WebRTC; true frame-labelled Persian precision/recall and production boundary accuracy remain blocked by the lack of frame-level gold annotations.
- Diarization: controlled read-speech speaker-clustering metrics are available; natural conversational DER/JER, overlap handling and production attribution remain unverified.
- Language ID: a pinned 3-language FLEURS smoke benchmark exists; larger per-language samples, code-switch evaluation and calibrated abstention remain outstanding.


## Confidence calibration and abstention

The local VoxLingua107 adapter supports an explicit confidence threshold and returns `und` below that threshold. Callers may also provide an explicit `supported_languages` frozenset; top labels outside that inventory become `und` with the original `raw_language` and `abstention_reason=out_of_inventory` preserved. Low-confidence abstentions are separately marked `low_confidence`. The validation calibrator excludes pre-abstained out-of-inventory labels from accepted predictions and coverage, so its accuracy/coverage constraints match the final inventory-aware policy. The benchmark runner calibrates its threshold on the pinned FLEURS `validation` split (default 100 samples per language) and evaluates only on the disjoint `test` split. Both the dataset revision and SpeechBrain model revision are pinned. Calibration maximizes accepted coverage subject to a 0.95 selective-accuracy floor and 0.50 minimum-coverage floor by default; if no threshold satisfies both, the run fails closed and writes the calibration diagnostic rather than silently selecting a threshold. An explicit operator `--threshold` override is available but is recorded as uncalibrated. Both calibration and test outputs retain per-sample audio hashes, model revision, split and confidence metadata. The dedicated `fleurs-lid-calibration` workflow runs CPU/local inference, uses an isolated non-cancelling concurrency group, and uploads calibration/evaluation artifacts. The current FLEURS test subset is not a code-switch benchmark, so this does not close the code-switch acceptance gap.
