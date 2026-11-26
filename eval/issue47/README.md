# Persian speech evaluation boundaries

## Common Voice read-speech

The automated Common Voice path is limited to the publisher-provided sample archive (maximum 50 clips). Audio, source reference sentences, Qwen hypotheses, and per-utterance alignment timings remain inside the CI job's temporary directory. Artifacts contain aggregate metrics, pinned model provenance, and a non-identifying diarization boundary record only.

Qwen3-ASR performs transcription. WhisperX is used only for forced alignment of the Qwen hypotheses with the Persian Wav2Vec2 CTC alignment model. The model revision and license are pinned in `whisperx_alignment.py`.

## CPU configuration

For WhisperX's Faster-Whisper ASR CLI path, a memory-conscious CPU invocation is `--compute_type int8 --batch_size 1`. This project uses Qwen3-ASR for transcription instead, then calls WhisperX's Hugging Face CTC forced-aligner API directly. That aligner runs in PyTorch `float32`, one clip at a time (`batch_size=1`); recording it as int8 would be inaccurate.

## Evidence boundary

Common Voice is scripted read-speech, not natural multi-speaker conversation. The pipeline emits no diarization, speaker separation, overlap, or participant-identity evidence. `speaker_segments.json` explicitly records `not_evaluated_read_speech` and an empty segment list. Natural conversational diarization and overlap evaluation require separately consented M2 data.
