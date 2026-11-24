# Durable workflow orchestration

## Initial PostgreSQL-native proof of concept

VoxPipe now has a tenant-scoped workflow run/step journal built on PostgreSQL 16. It is deliberately a bounded sequential workflow primitive that complements the existing ingestion queue; it does not replace the ingestion queue or introduce a third-party workflow dependency.

The journal supports ordered versioned steps, idempotent workflow creation, bounded retries, lease expiry/reclaim, stale-worker fencing, cancellation and human approval pause/resume. A tenant-scoped ready-workflow scan and scheduler-friendly claim loop allow independent workflows to progress without a shared in-memory queue. Both run and step tables use forced RLS. Step inputs/results are reference-only JSON objects with a 64 KiB limit and reject raw audio, transcript text, credentials and other sensitive payload fields. Workflows can be linked to an asset; deleting that asset cascades to workflow state.

## Execution guarantees and limits

- One step per workflow is claimed at a time; concurrent workflows can progress independently.
- An expired lease may cause the current step to execute again. External side effects must therefore use idempotency keys or provider-side deduplication.
- A stale worker cannot complete or fail a step after lease expiry or reassignment.
- Retry count is bounded per step. Permanent failure moves the workflow to failed.
- Approval waits are durable and require an authenticated actor UUID to resume.
- The journal does not store raw audio/transcript content; workers load source data through tenant-scoped repositories.

## Verification

The PostgreSQL 16 integration suite exercises ordered execution, idempotent create, retry exhaustion, approval, cancellation, tenant isolation, sensitive payload rejection and expired-lease recovery.

## Not yet production-ready

The current PoC still lacks deployment-specific service-supervisor configuration, automatic/background heartbeat scheduling (handlers renew cooperatively), parallel branches/compensation, distributed tracing, operational load benchmarks, backup/restore validation and an external workflow engine comparison under representative workload. Re-evaluate Temporal/DBOS/Restate if measured scale or cross-service orchestration exceeds this bounded design.


The scheduler-facing claim loop is deliberately small: it lists ready workflow IDs under the authenticated tenant, then claims the first available step using the same row locks and lease-fencing transaction as direct claims. `PostgresWorkflowWorker` now binds that claim loop to an explicit `WorkflowHandlerRegistry`: no dynamic imports or code execution are used, unknown handlers fail closed, retry policy must be explicit, handlers can pause for durable human approval, and result references pass the repository's sensitive-payload validator. `run_forever` supports graceful shutdown through a stop event; deploy multiple independently supervised worker processes to process different workflows concurrently. Each worker executes one step at a time. Handlers receive a cooperative `context.heartbeat()` callback that extends only a still-valid lease; an expired or reassigned lease cannot be revived. Long-running handlers must call it before expiry and abort if it raises `WorkflowLeaseLost`. External side effects must remain idempotent.


## Speech transcription handler

The `speech.transcribe` handler is registered explicitly with `WorkflowHandlerRegistry`. It reads an immutable audio asset by tenant-scoped reference, verifies object-store size and SHA-256, runs the configured local or hosted `SpeechToTextPort`, and appends an immutable transcript revision. The workflow journal stores references and result counts only, never audio or transcript text. A deterministic request idempotency key is stable across attempts. Before reopening audio or invoking a provider, the handler checks whether the exact tenant/transcript/revision already exists; it reuses the immutable result only when tenant, transcript, asset, revision and source SHA-256 match. This closes the persist-then-lease-loss replay window without paying for another inference or creating a timestamp-only revision conflict. A lease heartbeat runs during inference; stale workers discard results. Provider errors map to bounded retry policy, and ambiguous hosted timeouts are retryable only when the adapter can safely reconcile/idempotently repeat the vendor request.
