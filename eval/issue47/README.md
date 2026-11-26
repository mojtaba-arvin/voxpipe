# Persian speech evaluation boundaries

## Common Voice read-speech

The automated Common Voice path is limited to the publisher-provided sample archive (maximum 50 clips). Audio, source reference sentences, Qwen hypotheses, and per-utterance alignment timings remain inside the CI job's temporary directory. Artifacts contain aggregate metrics, pinned model provenance, and a non-identifying diarization boundary record only.

Qwen3-ASR performs transcription. WhisperX is used only for forced alignment of the Qwen hypotheses with the Persian Wav2Vec2 CTC alignment model. The model revision and license are pinned in `whisperx_alignment.py`.

## CPU configuration

For WhisperX's Faster-Whisper ASR CLI path, a memory-conscious CPU invocation is `--compute_type int8 --batch_size 1`. This project uses Qwen3-ASR for transcription instead, then calls WhisperX's Hugging Face CTC forced-aligner API directly. That aligner runs in PyTorch `float32`, one clip at a time (`batch_size=1`); recording it as int8 would be inaccurate.

## Evidence boundary

Common Voice is scripted read-speech, not natural multi-speaker conversation. The pipeline emits no diarization, speaker separation, overlap, or participant-identity evidence. `speaker_segments.json` explicitly records `not_evaluated_read_speech` and an empty segment list. Natural conversational diarization and overlap evaluation require separately consented M2 data.

## Reproducible runtime pins

- Qwen3-ASR: `Qwen/Qwen3-ASR-0.6B-hf`, revision `40946090cac63fc01d723a83174e16515073d7c0`, native Transformers `5.20.1`.
- CPU runtime: PyTorch/torchaudio `2.15.0+cpu`; Pillow `13.0.0`; Hugging Face Hub `1.31.0`.
- WhisperX alignment code: `3.8.6`; Persian CTC model revision is pinned in `whisperx_alignment.py`.
- WhisperX is installed with `--no-deps` because its package metadata still constrains Torch to the older `2.8.x` line. The project installs only the dependencies used by WhisperX's alignment module and explicitly pins the patched Torch/torchaudio/Transformers stack. The reusable workflow performs an import/API compatibility check before requesting the protected dataset sample.
- The WhisperX source package and its runtime dependencies are audited separately; no known vulnerabilities are suppressed.
