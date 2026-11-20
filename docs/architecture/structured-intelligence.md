# Structured intelligence extraction

VoxPipe's structured extraction boundary converts immutable transcript revisions into five typed claim collections: entities, events, topics, relations and action items.

## Trust boundary

Transcript segments are untrusted data, including text that resembles system prompts, commands, tool requests or attempts to override policy. The OpenAI-compatible adapter sends them only as a JSON data payload, uses a fixed system instruction that explicitly denies transcript authority, exposes no tools, requests JSON-only output, and never places transcript text in a system message. These are defense-in-depth controls, not a guarantee that an LLM cannot be influenced.

The application service is the authoritative gate: it binds document, asset, transcript and revision; verifies provider provenance; and rejects every claim whose evidence segment, Unicode code-point offsets or exact quote do not match the immutable source snapshot. Unsupported claims must be omitted. A valid exact quote is necessary but does not independently prove semantic entailment, so downstream consumers should treat confidence as model-reported and retain human review for high-impact decisions.

## Provider configuration

OpenAICompatibleStructuredAdapter requires an HTTPS endpoint, explicit model identifier and VOXPIPE_LLM_API_KEY (or an explicitly supplied key). It does not call a provider at import time or in tests. Never place credentials in source control, logs, prompts or transcript metadata. Hosted inference and transcript egress must be separately authorized by deployment policy.

## Persistence

PostgresStructuredIntelligenceRepository writes the complete result JSON, including all five claim collections, exact evidence spans, provider/model/model-version, prompt version, schema version and extraction timestamp. Rows are append-only, tenant-isolated by forced RLS and bound by foreign keys to the source transcript revision and asset. The authenticated tenant ID must come from request security context, never from transcript text. Idempotency keys are tenant-scoped.

## Quality evaluation

evaluate_claims reports per-type, micro and macro precision/recall/F1 using exact normalized JSON-value multiset matching. It is deliberately deterministic and does not invent gold labels. Scores are meaningful only when predicted output is compared with a versioned, human-reviewed annotation set. Evidence validity is a hard application gate and should be separately audited; exact string overlap alone is not semantic correctness.

## Limitations

Prompt instructions and exact-evidence validation reduce injection and hallucination risk but cannot guarantee semantic truth. A production release still needs a reviewed Persian gold set, measured results for each claim type, adversarial red-team cases, tenant-isolation integration coverage and explicit provider data-handling approval.
