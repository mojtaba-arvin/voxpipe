# Common Voice Persian sample evaluation

## Pinned source

- Dataset: Common Voice Scripted Speech 27.0 - Persian
- Dataset ID: `cmu5xazgy00finq074vaqo1b3`
- Release datasheet: `cv-corpus-27.0-2026-09-11`
- Locale / task: `fa` / ASR
- License: CC0-1.0
- Dataset page: https://mozilladatacollective.com/datasets/cmu5xazgy00finq074vaqo1b3
- Publisher metadata: 431.44 recorded hours, 373.15 validated hours, 395,173 clips and 4,668 speakers.

## Download policy

Use the official `datacollective==0.8.1` SDK and its `download_sample()` API only. The repository secret `MDC_API_KEY` is required, and the dataset terms must have been accepted by the key's organization in Mozilla Data Collective. Do not call `download_dataset()`, fall back to the full 10.46 GB archive, or use a direct storage URL.

The SDK sample archive is temporary. The workflow inspects its tar headers without extracting audio or parsing transcript rows, enforces archive/member/uncompressed-size limits, counts only audio and metadata files, and removes the archive when the process exits. Only aggregate public dataset provenance and sample file counts are uploaded.

If the publisher has no sample, the command writes a `sample_unavailable` report and exits non-zero. This is an explicit blocked result, not a passing download. Authentication/terms failures are not bypassed.

## Interpretation and privacy

Common Voice is scripted read-speech, not natural conversation. The speaker count is a release-wide aggregate from the public datasheet; speaker IDs, client IDs, transcript rows, audio bytes and per-speaker statistics must never be read into reports, uploaded, committed or re-hosted. Do not attempt speaker identification. This workflow establishes only source access and bounded sample availability; it does not establish ASR quality or natural-conversation performance.

The public dataset page reports the release-wide totals and license: https://mozilladatacollective.com/datasets/cmu5xazgy00finq074vaqo1b3
