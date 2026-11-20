# ADR-0022: Select faster-whisper for the first STT adapter

- **Status:** Accepted for the first local adapter; Persian quality benchmark remains pending.
- **Date:** 2026-11-20
- **Work item:** VP-0022

## Context

VoxPipe has a provider-neutral SpeechToTextPort and canonical transcript contract. The first adapter must support multilingual batch audio, Persian as a priority, word timestamps, provenance binding, offline tests, and deployment without mandatory paid inference or external audio transfer. Diarization is represented by the contract but need not be fabricated by every provider.

## Decision

Implement a local-only faster-whisper adapter backed by CTranslate2. Default to model identifier small, CPU execution and int8; permit explicit model/device/compute configuration. Default local_files_only=True, so model weights must be provisioned before the worker runs offline. The adapter supports segment and optional word timestamps. It does not support diarization; a diarization request returns STT_FEATURE_UNSUPPORTED.

No cloud provider is enabled by this ADR. Hosted alternatives remain candidates for a later measured comparison after consent, privacy, region and budget gates are approved.

## Alternatives considered

- **Google Chirp 3:** managed multilingual option and EU regional availability. Persian is Preview in the official language table; Persian is not in the documented diarization subset. V2 price is listed at $0.016/minute.
- **Deepgram Nova-3 Multilingual:** Persian appears in the official language list; diarization is available for Nova batch models. Hosted processing and a $0.0092/minute published multilingual list rate create data-transfer and spend gates.
- **OpenAI gpt-transcribe:** recommended current file-transcription model and listed at $0.0045/minute. Existing word-timestamp and diarization examples refer to deprecated models scheduled for shutdown on 2027-02-26; no current equivalent timestamp guarantee is assumed.
- **faster-whisper:** local multilingual Whisper implementation with optional word timestamps, no per-minute API bill, and no need to transfer raw audio. This adapter does not provide diarization and requires model storage/compute.

See [STT provider evaluation](../research/stt-provider-evaluation.md) for the source-backed comparison and benchmark plan.

## Consequences

Positive:
- Audio stays inside the deployment boundary during inference.
- Runtime requires no API key or external network.
- Adapter implements the existing port and canonical transcript contract.
- Tests inject a deterministic fake model and run without model downloads, audio uploads, GPU, or inference charges.

Costs and limitations:
- Local CPU/GPU, memory, power, disk and model operations are paid by the operator.
- Model weights must be staged and pinned before offline startup.
- Persian WER/CER and throughput have not been measured on a VoxPipe corpus; model size is provisional.
- Diarization remains unsupported until a separately evaluated local component or approved provider is introduced.
- Optional package and model artifacts require dependency/license review for each pinned release.

## Rollout and rollback

Install the optional dependency from requirements-stt.txt, provision the selected model cache in a controlled environment, and instantiate FasterWhisperAdapter. Do not enable production workloads until resource limits and a consented Persian benchmark exist. Rollback is to the previous adapter or a no-STT deployment; the provider-neutral port and transcript schema remain unchanged.

## Evidence status

Offline adapter tests and canonical service validation are included in the PR. No live model inference, customer audio transfer, paid API call, or accuracy/performance benchmark is claimed.
