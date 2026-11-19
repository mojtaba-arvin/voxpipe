# Media Probing and Normalization Options

**Research checked:** 2026-11-19  
**Work item:** VP-0009  
**Status:** Recommendation for a bounded proof of concept; no media parser dependency is approved yet.

## 1. Separate two responsibilities

Do not conflate:

1. **Metadata inspection:** identify the container, audio streams, codec, duration, sample rate, channel count, and track index from the actual bytes.
2. **Normalization/transcoding:** decode/resample/remix/re-encode media into a canonical representation for downstream speech models.

The ingestion boundary should inspect first and only normalize after the asset passes policy checks. A metadata probe is not proof that the entire file can be decoded safely or completely.

## 2. Candidate comparison

| Candidate | Strengths | Limitations / operational risks | Licensing observation | Fit |
|---|---|---|---|---|
| FFmpeg / ffprobe | Broad multimedia container/codec support; ffprobe emits machine-readable JSON and exposes format and stream metadata; same ecosystem can later normalize/resample | Native parser surface; untrusted files must be isolated; builds differ by enabled codecs and external libraries; arbitrary URL/protocol inputs must be blocked | FFmpeg default is LGPL 2.1+; optional GPL components and external libraries can change the effective license | Strongest overall fit for broad inspection plus later normalization |
| GStreamer | Plugin-based media framework, pipeline model, broad platform support, suitable for real-time and streaming pipelines | Larger plugin/runtime surface; plugin discovery and per-plugin licensing; more concepts than a one-shot metadata probe | Core and project plugins are LGPL 2.1; individual plugins/dependencies can have different licenses, including GPL | Strong candidate for a future real-time/media pipeline, not the simplest batch probe |
| MediaInfo CLI / library | Metadata-oriented tool with CLI and library interfaces; supports many media formats; clean separation from transcoding | Metadata field semantics and output normalization need an adapter; library is C++; packaging/build choices require review | Official documentation describes BSD-style licensing for CLI and library distributions; binary redistribution has a notice requirement | Attractive metadata-only alternative where licensing simplicity is prioritized |
| Mutagen | Pure-Python audio metadata; no dependencies outside the standard library; useful format-specific audio tags and metadata | Audio-focused, not a general video/container inspection and normalization stack; does not replace a full media probe | GPL-2.0-or-later | Useful optional audio-specific adapter, not the default platform parser |

## 3. Official documentation and license evidence

### FFmpeg / ffprobe

- ffprobe inspects multimedia streams and emits machine-readable output; it can report container format and individual stream types: https://ffmpeg.org/ffprobe.html
- JSON output and selective stream/field reporting are documented: https://www.ffmpeg.org/ffprobe.html
- FFmpeg's default license is LGPL 2.1+; enabling GPL components changes the effective license, and external libraries may affect it: https://ffmpeg.org/legal.html and https://github.com/FFmpeg/FFmpeg/blob/master/LICENSE.md

### GStreamer

- GStreamer is a plugin-based multimedia framework; the project documents LGPL 2.1 licensing for its core and notes that some plugin dependencies may be GPL: https://gstreamer.freedesktop.org/documentation/frequently-asked-questions/general.html
- Its legal documentation explains that effective licensing must be checked at the plugin level: https://gstreamer.freedesktop.org/documentation/frequently-asked-questions/licensing.html

### MediaInfo

- Official support documentation exposes format lists, field mappings, and developer SDKs: https://mediaarea.net/en/MediaInfo/Support
- Official license text grants BSD-style source/binary redistribution rights with notice requirements and identifies third-party dependencies: https://mediaarea.net/en/MediaInfo/License
- The FAQ describes BSD-style licensing for the CLI and library: https://mediaarea.net/en/MediaInfo/Support/FAQ

### Mutagen

- The official project describes supported audio formats and its pure-Python/no-third-party-dependency approach: https://mutagen.readthedocs.io/en/latest/index.html
- The project declares GPL-2.0-or-later: https://github.com/quodlibet/mutagen/blob/main/pyproject.toml

## 4. Recommendation

**Primary candidate for the PoC: ffprobe as an isolated subprocess adapter.**

Reasons:
- One metadata engine can inspect audio-only and audio-bearing video files.
- JSON output provides a stable adapter input shape.
- The same ecosystem is a likely future candidate for normalization/resampling, avoiding an unnecessary second codec stack.
- The process boundary makes resource limits and sandboxing more explicit than importing native parser libraries into the API process.

This is a PoC recommendation, not approval to ship an arbitrary FFmpeg binary. Before distribution, record the exact binary version, build configuration, enabled codecs, linked libraries, and corresponding license obligations. Prefer an auditable LGPL-compatible build if it covers the required formats; do not enable GPL/nonfree components by default.

**Alternative:** MediaInfo for metadata-only deployments where its BSD-style license and field coverage are a better fit. GStreamer should be revisited when a concrete real-time pipeline requirement exists. Mutagen may be useful for a narrow Python-only audio-tagging adapter but is not the general parser.

## 5. Security requirements for the probe adapter

- Accept only an already-staged local file path from trusted internal code; never pass a client-supplied URL to ffprobe.
- Use an argument array with no shell interpolation.
- Restrict protocols to local file access; explicitly prevent network protocols and remote resource fetching.
- Run in a low-privilege worker/container with read-only input, isolated temporary storage, no credentials, and no network access.
- Enforce wall-clock timeout, memory/CPU/process limits, maximum file size, maximum duration, and bounded stdout/stderr.
- Do not request packet/frame dumps or arbitrary metadata tags unless required.
- Parse JSON defensively; treat all metadata strings as untrusted.
- Map parser failures to stable VoxPipe error codes without returning raw stderr to clients.
- Keep the parser binary patched and record its version/build digest.

## 6. Proof-of-concept plan

Use a deterministic, synthetic fixture corpus—no customer audio:

- WAV PCM mono/stereo at supported sample rates.
- MP3, M4A/AAC, FLAC, Ogg/Opus, WebM/Opus, and MP4 with an audio track.
- Video-only input, empty file, truncated header, corrupt container, mismatched extension/MIME, oversized metadata, and unsupported codec.
- Crafted URL/protocol input to prove network access is blocked.
- Files at and beyond configured size/duration/channel/sample-rate limits.

Measure and record:
- Correctness of container and audio-stream detection against fixture ground truth.
- Duration/sample-rate/channel/codec extraction correctness.
- False accept and false reject rates.
- p50/p95/p99 probe latency by file size and format.
- Peak RSS, CPU time, timeout behavior, and process cleanup.
- Behavior under concurrent probes and malformed-input fuzzing.

Run the same fixtures through MediaInfo as a metadata-only comparison. A tool is selected only after the security and correctness gates pass; performance alone cannot compensate for false acceptance of invalid media.

## 7. Known limitations

- Metadata probing does not validate that every packet/frame can be decoded successfully.
- Duration fields may be estimated or absent for some containers/codecs; the adapter must preserve unknown values rather than fabricate them.
- Codec patent obligations are jurisdiction- and deployment-dependent and are separate from open-source software license obligations.
- Sandboxing reduces parser blast radius but does not eliminate parser vulnerabilities.
