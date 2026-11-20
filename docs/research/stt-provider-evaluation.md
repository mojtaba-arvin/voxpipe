# Speech-to-Text Provider Evaluation

**Research checked:** 2026-11-20  
**Work item:** VP-0022  
**Scope:** first batch transcription adapter for Persian-first multilingual audio.  
**Evidence boundary:** capability, cost and deployment comparison—not a measured Persian accuracy leaderboard. No customer audio was uploaded and no paid inference was run.

## 1. Evaluation dimensions and method

The decision considers Persian (fa/fa-IR), mixed-language/code-switching, word-level timing, diarization, batch formats, deployment, privacy, licensing, cost and operations.

A valid quality benchmark must use the same consented, representative corpus and normalized references for every candidate. Split by speaker and recording session. Report raw and normalized Persian WER/CER, code-switch span accuracy, language-ID accuracy, word timestamp boundary error (median/p95 ms), DER/JER where diarization exists, real-time factor (inference seconds/audio seconds), peak RSS/VRAM, failures and effective cost per audio hour. Stratify by dialect, noise, phone/meeting microphones, overlap and sample rate. Publish corpus version, consent/provenance, model/API version, hardware, region, options and raw per-file outcomes.

No accuracy scores are fabricated. VoxPipe currently has no approved consented Persian evaluation corpus or fixed benchmark host. The benchmark harness added with the adapter scores WER/CER/RTF and optional API cost from a JSONL result set; a real provider comparison remains pending that corpus.

## 2. Candidate comparison

| Candidate | Persian and multilingual evidence | Timestamps | Diarization | Deployment and privacy | Cost / license |
|---|---|---|---|---|---|
| **faster-whisper (Whisper via CTranslate2)** | Whisper is multilingual and includes Persian; quality varies by model size, dialect and recording. Upstream publishes speed/memory results for a 13-minute non-Persian workload; those are not VoxPipe accuracy or performance guarantees. | Segment and optional word timestamps. | Not provided by this ASR adapter. A separate local diarization model adds runtime and evaluation complexity. | Runs locally; audio need not leave deployment. Model weights must be provisioned and cached before offline use. CPU/GPU, memory and storage are operator responsibilities. | No per-minute API fee; compute, power, storage and operations are not free. faster-whisper is MIT; review pinned model weights and transitive notices. |
| **Google Cloud Speech-to-Text V2 / Chirp 3** | Official language table lists Persian (fa-IR) as Preview, not GA. Other multilingual locales have differing GA/Preview status. | Word timestamps can be enabled for Recognize/BatchRecognize, with documented trade-offs; utterance timestamps for streaming. | Chirp 3 diarization is limited to a documented language subset; Persian is not in that subset. | Hosted service; EU regional availability, data residency, audit logging and customer-managed encryption are documented. Audio is transmitted to Google; review region, DPA, retention and project policy. | Published V2 price $0.016/minute; storage/related services may add cost. Proprietary service. |
| **Deepgram Nova-3 Multilingual** | Official model/language table explicitly lists Persian (fa). This is support evidence, not an independent Persian WER result. | Word-level timestamps in transcription response. | Diarization supported for Nova batch models; verify exact model/version and Persian behavior on a test set. | Hosted API; audio leaves deployment. Review plan, region, DPA, retention and training-use terms. Self-hosted offerings have separate operational requirements. | Public pay-as-you-go Nova-3 Multilingual list price $0.0092/minute; optional features may cost extra. Proprietary service. |
| **OpenAI Audio Transcriptions (gpt-transcribe)** | Current docs recommend gpt-transcribe for recorded audio and support language hints. Public docs do not establish a Persian-specific WER guarantee. | Current recommended workflow does not document the legacy word-timestamp contract. Timestamp examples use deprecated whisper-1. | Existing diarization example uses deprecated gpt-4o-transcribe-diarize; deprecated models are scheduled to shut down 2027-02-26. | Hosted API; audio is sent to OpenAI. Review current API data controls, retention, region eligibility and contractual terms. | Published estimate $0.0045/minute. Proprietary API; do not start a new integration on deprecated models. |

## 2.1 Audio format and input constraints

- **faster-whisper:** accepts a local path or file-like input through PyAV decoding; practical containers/codecs depend on the installed PyAV/FFmpeg build. VoxPipe stages common WAV, MP3, M4A/MP4, FLAC, Ogg/Opus and WebM MIME types, but deployments must validate actual decoder support.
- **Google Chirp 3:** Cloud Speech-to-Text documents WAV/LINEAR16, FLAC, Ogg/Opus and WebM/Opus among its encoding/container combinations; supported encodings and sample-rate constraints are explicit and must match the media header.
- **Deepgram:** documents common MP3, MP4, MP2, AAC, WAV, FLAC, PCM, M4A, Ogg, Opus and WebM inputs; pre-recorded API limits include a 2 GB file size ceiling and request-duration limits.
- **OpenAI:** the current file transcription guide documents FLAC, MP3, MP4, MPEG, MPGA, M4A, OGG, WAV and WEBM, with a 25 MB upload limit.

These lists are provider input constraints, not guarantees that every codec/build combination is suitable. VoxPipe should normalize only after media inspection and should benchmark with the same source audio.

## 3. Selection

**Select faster-whisper as the first adapter**, with model identifier small and CPU int8 as conservative defaults. This is a privacy/operability-first selection, not a claim that it has the best Persian accuracy.

Reasons:
- Raw audio stays inside the operator-controlled worker boundary during inference.
- The multilingual Whisper model family includes Persian.
- Optional word timestamps map to the existing canonical contract.
- No API credentials, network access or per-minute inference spend are required at runtime.
- It is replaceable behind SpeechToTextPort.

Trade-offs:
- No diarization in this adapter. A diarization request fails explicitly; it never invents speaker identities.
- Model weights must be provisioned separately before offline operation.
- Quality and throughput depend on model size, quantization, hardware, audio domain and dialect. small is a starter default, not a production accuracy choice.
- Operators must pin package/model revisions, scan dependencies and account for compute, energy, storage and model notices.

## 4. Cost snapshot

Hosted list prices observed on 2026-11-20 (USD, before taxes, discounts, storage and optional features):

| Provider | Rate | 1,000 audio minutes |
|---|---:|---:|
| OpenAI gpt-transcribe | $0.0045/min | $4.50 |
| Deepgram Nova-3 Multilingual | $0.0092/min | $9.20 |
| Google Chirp 3 V2 | $0.016/min | $16.00 |
| faster-whisper | No per-minute API fee | Local compute/energy/storage/operator cost; measure locally |

These are list-price snapshots, not quotes or total-cost guarantees. Confirm current rates and plan-specific terms before procurement. No API was called and no cost was incurred in this evaluation.

## 5. Privacy and operational gates

- The local adapter defaults to local_files_only=True and refuses runtime model downloads.
- Provision model weights through a controlled build/cache process and verify origin and digest.
- Run inference in a low-privilege worker with no network access, bounded audio bytes, private temporary files, CPU/RAM/VRAM limits and cleanup.
- Never log audio bytes, transcript text, provider payloads, local paths or credentials.
- Treat transcripts and speaker labels as sensitive derived data.
- A hosted provider requires explicit deployment allowlisting, data-processing review, retention/training-use review, region selection, customer authorization and a budget limit.

## 6. Reproducible benchmark protocol

1. Create a versioned, consented Persian/multilingual corpus with speaker/session-separated partitions and human-reviewed verbatim references.
2. Include standard and colloquial Persian, dialects, English/Persian code-switching, names/numbers, overlap, phone/meeting codecs, noise and quiet speech.
3. Pin package, model repository revision, runtime, device, compute type and decoding options.
4. Warm up once; run each file at least three times; record median/p95 latency, RTF, peak RSS/VRAM and failures. Keep first-download time separate.
5. Score raw/normalized WER and CER, language-ID accuracy, word timestamp error, and DER/JER only for diarization-capable systems.
6. Record list price snapshot and effective compute cost. Never compare one vendor's advertised benchmark to another vendor's measured result.
7. Store per-file JSONL with corpus/version, reference, hypothesis, duration, inference time, provider/model, hardware and optional cost. scripts/benchmark_stt.py reports WER, CER, RTF and estimated API cost.

**Status:** comparison, benchmark protocol and scoring harness are implemented. A true model quality/performance run is not claimed until an approved corpus and benchmark host are available.

## 7. Official sources

- faster-whisper implementation, model loading, word timestamps and upstream benchmark: https://github.com/SYSTRAN/faster-whisper
- OpenAI Whisper multilingual model card: https://huggingface.co/openai/whisper-large-v3
- Google Chirp 3 features, languages, timestamps and diarization limits: https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3
- Google V2 language availability: https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages
- Google audio encodings: https://docs.cloud.google.com/speech-to-text/docs/encoding\n- Google Speech-to-Text pricing: https://cloud.google.com/speech-to-text/pricing
- Deepgram Nova-3 model/language matrix: https://developers.deepgram.com/docs/models-languages-overview
- Deepgram supported formats: https://developers.deepgram.com/docs/supported-audio-formats\n- Deepgram diarization: https://developers.deepgram.com/docs/diarization
- Deepgram pricing: https://deepgram.com/pricing
- OpenAI current speech-to-text workflow/deprecations and 25 MB file limit: https://developers.openai.com/api/docs/guides/speech-to-text
- OpenAI transcription model pricing: https://developers.openai.com/api/docs/pricing
