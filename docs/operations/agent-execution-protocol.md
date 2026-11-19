# Agent Execution Protocol

## Purpose

Provide a durable, bounded execution loop so work can continue across agents, sessions, and context resets without relying on conversational memory.

## Work item lifecycle

```text
planned -> ready -> in_progress -> review -> done
                    |             |
                    v             v
                  blocked <-------+
                    |
                    +-> ready (after blocker resolution)

planned/ready/in_progress -> cancelled (with rationale)
```

Only dependency-free `planned` items may be promoted to `ready`. Only items whose dependencies are `done` may enter `in_progress`.

## Execution loop

1. **Rehydrate:** read `AGENTS.md`, this protocol, the work ledger, current branch, and recent changes.
2. **Select:** choose the highest-priority ready item; avoid duplicate ownership.
3. **Plan:** state the smallest implementation slice and its acceptance evidence.
4. **Execute:** implement one bounded slice; keep changes reversible.
5. **Verify:** run focused tests, repository checks, and relevant security/compatibility checks.
6. **Review:** inspect the diff as a separate reviewer would; correct issues before completion.
7. **Persist:** update task status, evidence, changed paths, and follow-up work in version control.
8. **Continue:** select the next unblocked task, or stop with a precise blocker and alternative.

## Anti-stall controls

- Each work item must have bounded acceptance criteria.
- Do not repeat the same failed approach.
- After two materially different failures, record evidence and change strategy or delegate.
- Timeouts and retry budgets must be explicit for external calls.
- A blocked item must not prevent unrelated ready work from proceeding.
- Never mark a task done merely because code was generated or a command returned exit code zero; inspect outputs and verify behavior.

## Parallel execution

Parallelize independent research and implementation lanes, not writes to the same files or tightly coupled interfaces.

Recommended lanes:
- research: official documentation, alternatives, lifecycle and license;
- implementation: isolated module or adapter;
- evaluation: test datasets, benchmarks, failure cases;
- security: threat model, data flow, permissions, supply chain;
- integration: contracts, compatibility, end-to-end verification.

The orchestrator integrates results, resolves conflicting recommendations, and records decisions. Research agents must return sources, date checked, findings, uncertainty, and a recommendation—not just a tool list.

## Completion evidence

Record reproducible commands, versions, environment assumptions, pass/fail output, and artifact links/paths. Benchmarks must include workload, hardware, dataset, warm-up, repetitions, and percentile methodology.

## External side effects

Use synthetic or consented test data. Do not deploy, incur paid usage, alter access controls, delete data, or perform irreversible operations without a policy explicitly authorizing that operation.


## Skills and specialist agents

VoxPipe stores reusable procedures in `.agents/skills/<name>/SKILL.md`. Skills use progressive disclosure: metadata (name and trigger description) supports discovery; the full instructions are loaded only when selected. Keep one skill focused on one repeatable workflow. Do not duplicate always-on rules from `AGENTS.md` in every skill.

Project-scoped Codex specialists are defined in `.codex/agents/*.toml`. Use them as read-only reviewers for independent lanes such as CI triage, security, and cross-runtime contracts. They inherit the parent session's permissions and tools. The orchestrator remains responsible for deciding which findings are valid, making changes, and running final checks.

If the active agent client does not support repository skills or subagents, follow the same procedures manually using `AGENTS.md` and this protocol. Do not claim that a configured agent or skill ran unless its execution actually occurred.

## CI feedback loop

- Python and Go validation run as independent jobs. A failure in one language must not skip the other language's checks.
- Pull-request CI is the authoritative gate for feature branches; main-branch push CI protects the integration branch. Avoid duplicate push and pull-request runs for the same feature-branch commit.
- Use workflow concurrency to cancel superseded runs for the same PR/ref.
- A fix is not verified until a new run for the exact resulting head SHA has completed successfully.
- Keep validation commands read-only. Dependency drift and formatting checks report differences; they do not silently rewrite files.
- A CI failure creates a durable learning loop: root cause -> regression test -> process/skill update if the failure pattern can recur -> successful exact-SHA rerun.
