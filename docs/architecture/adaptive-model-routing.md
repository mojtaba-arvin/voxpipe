# Adaptive model routing

## Policy boundary

`AdaptiveModelRouter` selects only provider/model/version tuples explicitly present in a `RoutingPolicy.allowed_models` allowlist. It combines that allowlist with the provider capability registry and fresh task-specific `ModelQualityEvidence`. A candidate is eligible only when it is available, its evidence is not future-dated or stale, its quality meets the floor, its latency meets the ceiling, its locality is allowed, and its declared cost is known and within budget.

The default policy shape is local-only and zero-cost. Remote execution requires a separate policy with `require_local=false`, an explicit remote model allowlist, and a positive cost ceiling. Unknown costs fail closed when a ceiling is active.

## Provider circuit breaker

`ProviderCircuitBreaker` opens after a configurable number of consecutive provider failures, excludes the provider during a monotonic cooldown, allows exactly one half-open probe, closes on probe success, and restarts the cooldown on probe failure. A cancelled pre-dispatch probe can be released. The breaker is process-local and exposes only provider-level operational counters/timestamps; it is not a distributed circuit state store. Multi-process deployments must use a shared coordination backend or accept per-process breaker behavior. Callers should invoke `try_acquire()` before routing and pass `open_providers()` to the policy router for ordinary requests; only the request that acquired the half-open probe may bypass that provider exclusion.

## Input validation

Capability declarations require explicit immutable language/feature sets; discovery filters reject string-as-iterable and malformed feature/language values. Route requests require a typed policy, typed capability registry, and a validated set of open-circuit provider identifiers. Malformed control inputs fail before candidate selection rather than being interpreted as partial policy.

## Selection and fallback

Eligible models are ordered deterministically by descending measured quality, ascending latency, ascending estimated cost, then provider/model/version. The decision contains one selected model and at most the configured number of fallback candidates. Open-circuit providers are excluded. This component does not itself execute requests, retry side effects, or maintain circuit-breaker state; the caller owns those runtime concerns and must record each attempt idempotently.

## Audit and privacy

The decision's safe metadata includes task and policy versions, model identities, evidence IDs, quality, latency, cost, and decision time. It deliberately excludes transcript text, audio, query text, tenant identifiers, and credentials.

## Remaining production requirements

The current registry and quality evidence are in-memory inputs. Production activation requires an operator-controlled persistent registry, measured per-task evaluation records, a defined evidence freshness policy, verified cost data, and an auditable allowlist update/rollback process. The unit tests do not constitute measured model quality or authorize hosted inference.
