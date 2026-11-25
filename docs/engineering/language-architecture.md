# Multilingual Architecture and Automatic Language Identification

## Design Principle

Deepgram pattern: **one multilingual model, \`language_hint\` as
prior not lock**. Three API parameters:

- \`language\`: explicit code (\`fa\`) or \`multi\`.
- \`language_hint\`: BCP-47 array, only with \`language=multi\`.
- Response fields: \`detected_language\`, \`language_confidence\`,
  \`languages_in_turn\`, \`asr_provider_used\`, \`lid_strategy_used\`.

## VoxPipe Integration

Connect to existing VoxPipe adapter/registry structure via
\`LIDStrategy\` (Strategy Pattern) and \`Adapter Registry\`. Adding
a new language = confirm ASR model coverage + register normalizer,
**no core pipeline changes**.

    class LIDStrategy:
        def detect(self, audio_chunk, hints: list[str] | None) -> LIDResult:
            # LIDResult: {language, confidence, alternatives}
            ...

    # Implementations:
    # - ECAPALIDStrategy      (fast, streaming, CPU-friendly)
    # - Qwen3NativeLIDStrategy (with ASR, no separate pre-stage)
    # - NoOpLIDStrategy      (when language is explicit)

    registry.register_normalizer("fa", PersianNormalizationHandler())

## Routing Logic

Not "LID first, then model selection". The model is multilingual
itself; LID only observes and parses hints.

## Confidence Threshold

When confidence is below 0.7, re-run LID on a wider window or
report \`alternatives\` in the response.

## Developer-Facing Endpoints (minimum)

| Endpoint | Method | Use |
|----------|--------|-----|
| /v1/listen | POST | sync, short files |
| /v1/async/transcripts | POST | async queue, long files |
| /v1/stream | WebSocket | real-time streaming |
| /v1/languages | GET | supported languages per tenant |

\`POST /v1/listen\` accepts \`language\`, \`language_hint\`, and an
ordered \`speech_models\` fallback list (AssemblyAI-style). Async
tasks accept \`webhook_url\` to replace polling.
