# CI Speedups

## Lever 1: uv instead of pip

Benchmark: ~4.8x faster cold cache (~5.4s vs ~26s); ~236x faster
warm cache (~0.056s vs ~13.2s). Uses content-addressable global
store and hardlinks.

    - uses: astral-sh/setup-uv@v6
      with:
        enable-cache: true
    - run: uv pip install --system -r requirements.txt

## Lever 2: pytest-xdist

6,395 tests on 2-core GitHub Actions runner with \`-n auto\`
(2 workers) → ~40–50% wall-clock reduction.

    - run: pytest -n auto --dist loadfile --timeout=120 -m "not slow"

Coverage note: set \`[tool.coverage.run] parallel = true\` in
\`pyproject.toml\`, then \`coverage combine\`; otherwise each worker
writes only its own \`.coverage.*\` and coverage is incomplete.

## Lever 3: mock model loading

On cold cache, loading HuggingFace models can exceed
\`pytest-timeout\` and cause "fail then pass". Fix: in \`conftest.py\`
or fixtures, skip real model init and inject mocks. Tests that
need real models are marked \`@pytest.mark.slow\` and skipped in
regular CI via \`-m "not slow"\`.

## Lever 4: split jobs + static cache keys

Jobs without \`needs\` start in parallel by default. Split lint,
unit tests, and three eval groups into independent jobs. Caution:
each job has ~27s startup overhead. A 16-way matrix burns >7
minutes billed before any useful work. Keep matrix axes minimal:
Group C only uses one ASR axis (Qwen3-ASR); Shenava only if
budget allows.

HuggingFace cache must use a static key. If
\`hashFiles('pyproject.toml')\` is used, every version bump
invalidates ~500 MB of weights. Model weights do not change with
project version.

    - uses: actions/cache@v4
      with:
        path: ~/.cache/huggingface
        key: hf-\${{ runner.os }}-qwen3-asr-0.6b
        restore-keys: hf-\${{ runner.os }}-
