# Qwen3-ASR CPU smoke

Run `python -m eval.issue47.cpu_smoke` in the isolated `requirements-eval-asr.txt` environment.

- Model: `Qwen/Qwen3-ASR-0.6B`, Apache-2.0, pinned revision `5eb144179a02acc5e5ba31e748d22b0cf3e303b0`.
- Runtime: `qwen-asr==0.0.6`, CPU-only, float32, batch size 1, no forced aligner.
- Input: the publisher's public English sample audio. This measures model load time, inference time and real-time factor without requiring Common Voice credentials.
- Output: schema-validated timing/hardware metadata and transcript character count only. Transcript text and sample audio are not uploaded as artifacts.

**Interpretation:** this is a CPU feasibility smoke, not a Persian WER benchmark. Qwen3-ASR supports Persian, but the model card's Qwen3-ForcedAligner language list excludes Persian; this script intentionally returns no timestamps. Persian word alignment is tracked separately in issue #178 using WhisperX's Persian alignment model. Do not start full Common Voice transcription until this CPU result and the dataset-access gate in issue #176 are resolved.
