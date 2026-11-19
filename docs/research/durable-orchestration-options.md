# Durable Workflow and Agent Orchestration Options

**Research checked:** 2026-11-19  
**Status:** Comparative research; no runtime selected yet.

## Decision context

VoxPipe needs durable audio-processing jobs and, separately, durable multi-agent engineering/operations workflows. These may have different requirements. Do not assume one engine must own both planes.

## Shortlist

| Dimension | Temporal | DBOS | Restate |
|---|---|---|---|
| Core model | External durable workflow service with language SDKs | Library-backed durable execution using PostgreSQL | Distributed durable execution runtime and log |
| Python | Official SDK | Official SDK | Official SDK |
| Go | Official SDK | Official SDK | Official SDK |
| TypeScript | Official SDK | Official SDK | Official SDK |
| Durable jobs | Workflows and, as of Sep 2026, GA Standalone Activities | Workflows, steps, queues, scheduling | Durable functions, workflows, async tasks |
| Operational footprint | Separate Temporal service/Cloud plus workers | PostgreSQL plus application processes; optional Conductor | Restate runtime/server plus services |
| Agent relevance | Durable long-running agent workflows and tool activities | Official agent integrations and durable agent examples | Explicit durable-agent and stateful-service positioning |
| Key evaluation risk | Workflow determinism, service operations, history/versioning and cost | Postgres coupling, transaction boundaries, concurrency and operational scaling | Runtime adoption, SDK maturity for our exact use cases, state/version evolution |

## Evidence from official sources

### Temporal

- Python client supports starting workflows, signaling, querying, and retrieving results: https://docs.temporal.io/develop/python/client/temporal-client
- Standalone Activities became generally available across Python, Go, TypeScript, and other SDKs on 2026-09-15. The documented feature set includes durable jobs, retries, timeouts, heartbeats, priority/fairness, start delay, and lifecycle controls: https://temporal.io/changelog/standalone-activities-durable-job-processing-ga
- Temporal's standalone activity model may cover a durable job queue without forcing every task into a multi-step workflow.

**Interpretation:** Strong candidate when workflow history, long-running orchestration, cross-service coordination, and independent orchestration operations justify a dedicated system.

### DBOS

- Official documentation describes durable workflows, steps, queues, scheduling, messaging, and timers backed by PostgreSQL: https://docs.dbos.dev/
- Python workflow documentation describes recovery from the last completed step and explains the distinction between retried steps and committed transactions: https://docs.dbos.dev/python/tutorials/workflow-tutorial
- DBOS supports multiple languages and documents cross-language interaction between separately deployed applications through clients/shared system databases: https://docs.dbos.dev/architecture
- Official material includes AI-agent integrations and examples: https://www.dbos.dev/

**Interpretation:** Strong candidate for an initial Python-centric modular system if PostgreSQL is already a foundational dependency and we want to avoid operating a separate workflow cluster. Exactly-once guarantees must be understood at the transaction boundary; external side effects still require idempotency and careful step design.

### Restate

- Official documentation positions Restate for durable agents, workflows, microservice orchestration, and event processing: https://restate.dev/
- The official project lists SDKs for Python, Go, TypeScript, Java/Kotlin, and Rust: https://github.com/restatedev/restate

**Interpretation:** Strong candidate for low-latency durable service interactions and stateful orchestration. Validate SDK behavior, deployment topology, operational requirements, and versioning semantics in a small proof of concept before adoption.

## Provisional recommendation

Do not select a production engine yet.

1. Keep the workflow boundary provider-neutral.
2. Build a small spike for DBOS and Temporal using the same representative workload: ingest job, multi-step transcription/extraction, transient provider failure, worker restart, cancellation, and idempotent result persistence.
3. Include Restate if low-latency durable interactions or stateful agents become a concrete requirement.
4. Compare recovery correctness, duplicate side effects, latency, throughput, operational burden, observability, deployment footprint, and agent implementation/debuggability.
5. Choose one engine per responsibility only if measurements show that separating the audio job plane from the engineering-agent plane is worthwhile.

For the first VoxPipe runtime, DBOS is the leading low-ops hypothesis—not an approved dependency—because PostgreSQL is already a likely metadata and job-state store. Temporal is the leading alternative if durable workflow operations and independent scaling outweigh the extra service. Restate remains a candidate for a measured fast-path/stateful-service need.

## Proof-of-concept acceptance criteria

- A workflow survives worker/process termination and resumes without repeating completed durable steps.
- External calls use idempotency keys or an equivalent deduplication strategy.
- Retryable and non-retryable errors are classified explicitly.
- Cancellation and timeout behavior are observable and tested.
- A second worker can safely claim and execute queued work.
- Workflow history/state can be inspected and correlated with application traces.
- The same workload is run with documented versions, hardware, concurrency, and repetitions.
- License, self-hosting, backup/restore, upgrade, and operational costs are reviewed.

## Sources and freshness

All sources above are official project documentation or project-maintained repositories, checked on 2026-11-19. Marketing performance claims are not used as benchmark evidence.
