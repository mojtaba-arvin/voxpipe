# VAD, Diarization and Language Identification

**Checked:** 2026-11-21. **Work items:** VP-0023, VP-0024, VP-0025.

## VAD candidates

- **Silero VAD** (MIT): local PyTorch/ONNX inference, supports 8 and 16 kHz, and exposes speech boundaries. The adapter uses the package's locally provisioned model and never downloads weights at request time.
- **WebRTC VAD** (BSD-3-Clause): lightweight frame classifier with 10/20/30 ms frames and 8/16/32/48 kHz support. It is a useful independent baseline, but aggressiveness trades missed speech for false activation.

VoxPipe exposes both behind the same VAD result shape. The evaluator reports frame-level precision/recall/F1, false-activation and missed-speech seconds, and mean boundary error. No Persian production score is claimed until an approved corpus with frame-level speech/non-speech annotations is available.

## Diarization

The first local baseline uses SpeechBrain ECAPA-TDNN speaker embeddings (Apache-2.0 model card) with deterministic cosine-centroid clustering over VAD speech windows. Speaker labels are anonymous and scoped to one audio asset. This baseline does not model overlap, turn-taking priors, or conversational channel separation. It is not claimed to be a production Persian diarization system.

DER/JER evaluation supports a configurable reference-boundary collar, optional overlap exclusion, speaker mapping, and speaker-count error. A genuine Persian DER/JER result requires consented multi-speaker recordings with time-aligned speaker-turn annotations. Read-speech corpora and per-utterance speaker IDs are not substitutes for within-recording turn labels.

## Language identification

SpeechBrain VoxLingua107 ECAPA is Apache-2.0 and includes Persian among its 107 language classes. The model card reports 6.7% error on its development set (1,609 manually checked clips from 33 languages); that result is not a VoxPipe result and does not establish Persian F1. VoxPipe loads a pre-provisioned local model, returns an explicit `und` outcome below a configurable threshold, and supports sliding windows for code-switch investigation.

The evaluator reports accuracy, macro-F1, per-class precision/recall/F1, a confusion matrix, and expected calibration error. Thresholds must be calibrated on representative Persian, English, code-switched, short, noisy and out-of-inventory samples before production use.

## Reproducibility and privacy

Optional runtimes are listed in `requirements-speech.txt`. Model files must be staged and pinned by deployment tooling; no adapter enables network downloads. No customer audio is used in tests. Offline tests inject deterministic models and exercise boundary validation, abstention, clustering, and metric calculations.

## Remaining evidence gates

- VAD: no approved frame-labelled Persian corpus; report synthetic/fixture tests separately from real speech/non-speech metrics.
- Diarization: no approved Persian corpus with within-recording speaker-turn ground truth; no real DER/JER claim.
- Language ID: adapter and metrics exist; a pinned multilingual gold benchmark is still required for measured macro-F1 and calibration.
