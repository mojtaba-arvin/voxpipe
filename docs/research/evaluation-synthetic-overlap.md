# Synthetic Persian overlap fixtures

Run `python -m eval.issue47.synthetic_overlap` in the isolated `requirements-eval-tts.txt` environment.

## Pinned local TTS

- Runtime: `sherpa-onnx==2.0.3` (Apache-2.0).
- Customer role voice variant: `fa_IR-amir-medium-int8`.
- Agent role voice variant: `fa_IR-ganji-medium-int8`.
- Both model cards identify Persian (Iran), one speaker, 22,050 Hz and CC0 training data. Ganji is fine-tuned from Amir; these are role-specific voice variants, not evidence of two independent speaker identities.
- The sherpa-onnx release assets are pinned by SHA-256 and verified before extraction. No Piper GPL runtime or hosted TTS API is used.

## Fixture generation

The generator reuses 50 records from the existing deterministic Group A conversation generator. It synthesizes the customer and agent utterances locally, offsets the second PCM stream, adds a 250 ms leading pause, deterministic Gaussian background noise at 30 dB SNR, mixes them with peak limiting, and records the exact sample-aligned overlap interval. Every annotation contains only the conversation ID, scenario, role labels, voice-model names and timing; it does not copy transcript text.

## Persistence and interpretation

The mixed WAVs exist only in a temporary directory and are deleted after validation. The workflow persists only `overlap_annotations.jsonl` and `overlap_manifest.json`; it uploads no audio or transcript artifact.

These are synthetic pipeline fixtures, not natural conversation, speaker diarization, voice identity or production speech-quality evidence. Common Voice read-speech remains a separate evaluation source.
