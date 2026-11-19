# ADR 0001: Agent-native foundation and modular-monolith default

- **Status:** Accepted for initial development
- **Date:** 2026-11-19
- **Decision owners:** Repository maintainers and delegated engineering agents

## Context

VoxPipe is intended to be developed and operated by autonomous agents with optional human oversight. Human team size, familiarity, and manual coordination should not be treated as fixed architectural constraints. However, agent autonomy does not eliminate runtime complexity, security risk, or the need for verifiable evidence.

The initial technology proposal assigns Go to real-time/API surfaces, Python to AI logic, and Rust to specialized high-performance or sandboxed components. That is a useful candidate map, not sufficient evidence for committing to multiple services and languages before workload and operational requirements are known.

## Decision

1. Treat agents as first-class engineering and operational actors.
2. Keep a machine-readable work ledger and explicit agent execution protocol in version control.
3. Default to a modular monolith with strict internal ports and adapter boundaries.
4. Use Python as the initial candidate for AI/data workflows.
5. Evaluate Go and Rust with representative benchmarks before extracting performance-sensitive components.
6. Require measured evidence and an ADR for consequential technology changes.
7. Make safety limits, auditability, retries, budgets, and rollback part of autonomous operations.

## Consequences

**Positive**
- Lower initial deployment and integration complexity.
- Work can be resumed by a different agent from repository state.
- Language changes remain possible without premature distributed boundaries.
- Decisions can be revisited using benchmark evidence.

**Trade-offs**
- The first implementation may later require extraction of hot paths.
- The work ledger and validation rules require maintenance.
- A modular monolith still needs disciplined module boundaries and dependency checks.

## Revisit when

- A representative benchmark identifies a throughput, tail-latency, or memory bottleneck.
- A provider/runtime requires process or security isolation.
- Independent scaling or availability objectives justify a separate service.
- Agent tooling or deployment constraints materially change.
