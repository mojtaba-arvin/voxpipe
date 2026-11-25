# Parallel Execution Plan

## Design Principle

**Groups do not wait for each other; within a group, tasks run in
parallel.** Three groups with one real dependency: group B's corpus
ideally comes from group A. But group B does not wait for A's CI —
it develops the full pipeline against a placeholder corpus, then
injects the real corpus via \`CORPUS_PATH\` and re-runs only
\`eval-48\`. Group C is fully independent and can start first.

    time →
    Group A (#32)  ──[template gen]──▶ [rule label]──▶ [eval #32]──┐
                                                                   │ CORPUS_PATH inject
    Group B (#48)  ──[dev with placeholder corpus]───────────────▶ [real eval #48]
    Group C (#47)  ──[Common Voice + Qwen3-ASR]──▶ [TTS+overlap]──▶ [eval #47]
                   (fully independent)

## Group A — Issue #32

| Task | Depends on | Parallelization |
|------|-----------|-----------------|
| A1. Conversation templates + slots | none | with A2/A3 |
| A2. Deterministic labeler | none | separate file eval/issue32/rules.py |
| A3. P/R/F1 script | none | separate file eval/issue32/metrics.py |
| A4. Assemble job eval-32 | A1+A2+A3 | seconds |

## Group B — Issue #48

| Task | Depends on | Parallelization |
|------|-----------|-----------------|
| B1. Query generator with placeholder corpus | none | does not wait for A |
| B2. Heuristic qrels | none | separate file eval/issue48/qrels.py |
| B3. Retrieval metrics | none | separate file eval/issue48/metrics.py |
| B4. Inject real corpus and re-run | A4 (optional) | triggers eval-48 only |

## Group C — Issue #47 (fully independent)

| Task | Depends on | Parallelization |
|------|-----------|-----------------|
| C1. Common Voice download | none | job download-cv |
| C2. ASR transcription | none | matrix: [qwen3-asr]; [shenava] optional if budget allows |
| C3. WhisperX alignment + diarization | C1 | job align-cv, parallel with C2 |
| C4. TTS + ffmpeg overlap synthesis | none | job synth-audio, does NOT wait for C1 |
| C5. Merge labels | C2+C3+C4 | small final job |

Key parallelization: C2 (transcription), C3 (alignment), C4
(synthesis) start simultaneously — no data dependency among them.

## Cross-Group Aggregation

    jobs:
      eval-32:   # group A
      eval-48:   # group B, CORPUS_PATH via env
      eval-47:   # group C, 4 parallel sub-jobs
      post-results:
        needs: [eval-32, eval-48, eval-47]
        if: always()

\`post-results\` only runs \`curl\` for issue comments and
\`upload-artifact\`; under 10 seconds, not a bottleneck.
