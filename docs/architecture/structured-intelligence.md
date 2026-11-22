# Structured intelligence extraction

VoxPipe's structured extraction boundary converts immutable transcript revisions into five typed claim collections: entities, events, topics, relations and action items. The v1 contract enforces collection-specific value shapes: entity (name, entity_type), event (name, event_type), topic (name), relation (subject, predicate, object), and action item (description).

## Trust boundary

Transcript segments are untrusted data, including text that resembles system prompts, commands, tool requests or attempts to override policy. The OpenAI-compatible adapter keeps the original transcript immutable for final validation, but builds a separate model-facing copy. A narrow, deterministic detector redacts sentence spans that contain both an explicit model/assistant/tool reference and an instruction-like command. This is a targeted defense-in-depth heuristic, not a general prompt-injection detector; unknown phrasings may pass through. The adapter exposes no tools, uses a fixed system instruction, and never places transcript text in a system message.

The model does not calculate or return evidence offsets or quotes. The adapter constructs a bounded catalog of exact sentence spans from the original transcript and assigns each an opaque span_id. The model may only select catalog IDs. The adapter resolves each selected ID to its original segment ID, exact quote and Unicode code-point offsets; it rejects unknown IDs and any attempt to add model-supplied evidence fields. When contiguous transcript word timings can be matched within a selected span, the adapter preserves the corresponding word indices and millisecond boundaries. The application service remains the authoritative gate: it binds document, asset, transcript and revision; verifies provider provenance; and rejects every claim whose evidence does not match the immutable source snapshot. Unsupported claims must be omitted. A valid exact quote is necessary but does not independently prove semantic entailment, so downstream consumers should treat confidence as model-reported and retain human review for high-impact decisions.

## Provider configuration

OpenAICompatibleStructuredAdapter requires an explicit model identifier and immutable model_version. Remote providers require HTTPS and a key; loopback HTTP endpoints (localhost/127.0.0.1/::1) are allowed without credentials for local Ollama-compatible inference. It bounds transcript size and requests a bounded JSON response. It does not call a provider at import time or in tests. Never place credentials in source control, logs, prompts or transcript metadata. Hosted inference and transcript egress must be separately authorized by deployment policy.

## Persistence

PostgresStructuredIntelligenceRepository writes the complete result JSON, including all five claim collections, exact evidence spans, provider/model/model-version, prompt version, schema version and extraction timestamp. Rows are append-only, tenant-isolated by forced RLS and bound by foreign keys to the source transcript revision and asset. The authenticated tenant ID must come from request security context, never from transcript text. Idempotency keys are tenant-scoped.

## Quality evaluation

evaluate_claims reports per-type, micro and macro precision/recall/F1 using exact normalized JSON-value multiset matching. It is deliberately deterministic and does not invent gold labels. Scores are meaningful only when predicted output is compared with a versioned, human-reviewed annotation set. Evidence validity is a hard application gate and should be separately audited; exact string overlap alone is not semantic correctness.

## Limitations

Prompt instructions and exact-evidence validation reduce injection and hallucination risk but cannot guarantee semantic truth. A production release still needs a reviewed Persian gold set, measured results for each claim type, adversarial red-team cases, tenant-isolation integration coverage and explicit provider data-handling approval.
