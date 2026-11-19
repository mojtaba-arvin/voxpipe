"""Tests for the ffprobe adapter; no ffprobe binary is required."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voxpipe.media.probe import FFprobeMediaInspector, MediaInspectionError, ProbePolicy


def probe_payload(
    *,
    format_name: str = "wav",
    duration: str = "2.5",
    streams: list[dict] | None = None,
) -> str:
    if streams is None:
        streams = [{
            "index": 0,
            "codec_type": "audio",
            "codec_name": "pcm_s16le",
            "sample_rate": "16000",
            "channels": 1,
            "channel_layout": "mono",
            "bits_per_sample": 16,
            "duration": duration,
        }]
    return json.dumps({
        "format": {"format_name": format_name, "duration": duration, "size": "999"},
        "streams": streams,
    })


def video_payload(*, format_name: str = "wav", has_video: bool = False) -> str:
    streams = [{"index": 1, "codec_type": "video"}] if has_video else []
    return json.dumps({"format": {"format_name": format_name}, "streams": streams})


def completed(payload: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ffprobe"], returncode=returncode, stdout=payload, stderr=stderr
    )


class FFprobeMediaInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "sample.wav"
        self.path.write_bytes(b"staged-test-media")
        self.inspector = FFprobeMediaInspector()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_returns_normalized_metadata(self, run, _which) -> None:
        run.side_effect = [completed(probe_payload()), completed(video_payload())]
        metadata = self.inspector.inspect(self.path)

        self.assertEqual(metadata.mime_type, "audio/wav")
        self.assertEqual(metadata.container_format, "wav")
        self.assertEqual(metadata.codec, "pcm_s16le")
        self.assertEqual(metadata.duration_ms, 2500)
        self.assertEqual(metadata.size_bytes, len(b"staged-test-media"))
        self.assertEqual(metadata.sample_rate_hz, 16000)
        self.assertEqual(metadata.channels, 1)
        self.assertEqual(metadata.audio_track_index, 0)
        self.assertEqual(metadata.bit_depth, 16)
        self.assertEqual(metadata.channel_layout, "mono")
        self.assertEqual(run.call_count, 2)

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_invocation_is_shell_free_local_only_and_stream_limited(self, run, _which) -> None:
        run.side_effect = [completed(probe_payload()), completed(video_payload())]
        self.inspector.inspect(self.path)

        audio_command = run.call_args_list[0].args[0]
        video_command = run.call_args_list[1].args[0]
        self.assertEqual(audio_command[audio_command.index("-select_streams") + 1], "a:0")
        self.assertEqual(video_command[video_command.index("-select_streams") + 1], "V:0")
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file")
            self.assertFalse(call.kwargs["shell"])
            self.assertEqual(call.kwargs["timeout"], 10.0)
            self.assertNotIn("http", command)

    @patch("voxpipe.media.probe.subprocess.run")
    def test_rejects_file_over_size_limit_before_launch(self, run) -> None:
        inspector = FFprobeMediaInspector(policy=ProbePolicy(max_file_size_bytes=4))
        with self.assertRaises(MediaInspectionError) as context:
            inspector.inspect(self.path)
        self.assertEqual(context.exception.code, "MEDIA_FILE_TOO_LARGE")
        run.assert_not_called()

    @patch("voxpipe.media.probe.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 1))
    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    def test_maps_timeout_to_stable_error(self, _which, _run) -> None:
        with self.assertRaises(MediaInspectionError) as context:
            self.inspector.inspect(self.path)
        self.assertEqual(context.exception.code, "MEDIA_PROBE_TIMEOUT")

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_maps_nonzero_exit_to_stable_error(self, run, _which) -> None:
        run.return_value = completed("", returncode=1, stderr="sensitive parser detail")
        with self.assertRaises(MediaInspectionError) as context:
            self.inspector.inspect(self.path)
        self.assertEqual(context.exception.code, "MEDIA_PROBE_FAILED")
        self.assertNotIn("sensitive parser detail", str(context.exception))

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_rejects_invalid_json(self, run, _which) -> None:
        run.return_value = completed("{invalid")
        with self.assertRaises(MediaInspectionError) as context:
            self.inspector.inspect(self.path)
        self.assertEqual(context.exception.code, "MEDIA_PROBE_INVALID_OUTPUT")

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_rejects_probe_output_over_limit(self, run, _which) -> None:
        run.return_value = completed(" " * 2048)
        inspector = FFprobeMediaInspector(policy=ProbePolicy(max_json_bytes=1024))
        with self.assertRaises(MediaInspectionError) as context:
            inspector.inspect(self.path)
        self.assertEqual(context.exception.code, "MEDIA_PROBE_OUTPUT_TOO_LARGE")

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_rejects_video_without_audio(self, run, _which) -> None:
        run.return_value = completed(video_payload(format_name="mp4", has_video=True))
        with self.assertRaises(MediaInspectionError) as context:
            self.inspector.inspect(self.path)
        self.assertEqual(context.exception.code, "MEDIA_NO_AUDIO_STREAM")
        run.assert_called_once()

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_rejects_unsupported_container(self, run, _which) -> None:
        run.side_effect = [
            completed(probe_payload(format_name="unknown_format")),
            completed(video_payload(format_name="unknown_format")),
        ]
        with self.assertRaises(MediaInspectionError) as context:
            self.inspector.inspect(self.path)
        self.assertEqual(context.exception.code, "MEDIA_UNSUPPORTED_CONTAINER")

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_rejects_duration_over_policy(self, run, _which) -> None:
        run.side_effect = [
            completed(probe_payload(duration="60")),
            completed(video_payload()),
        ]
        inspector = FFprobeMediaInspector(policy=ProbePolicy(max_duration_ms=30_000))
        with self.assertRaises(MediaInspectionError) as context:
            inspector.inspect(self.path)
        self.assertEqual(context.exception.code, "MEDIA_DURATION_LIMIT_EXCEEDED")

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_rejects_sample_rate_out_of_policy(self, run, _which) -> None:
        payload = json.loads(probe_payload())
        payload["streams"][0]["sample_rate"] = "4000"
        run.return_value = completed(json.dumps(payload))
        with self.assertRaises(MediaInspectionError) as context:
            self.inspector.inspect(self.path)
        self.assertEqual(context.exception.code, "MEDIA_SAMPLE_RATE_OUT_OF_RANGE")

    @patch("voxpipe.media.probe.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("voxpipe.media.probe.subprocess.run")
    def test_maps_mp4_video_and_audio_only_mime_types(self, run, _which) -> None:
        audio_format = "mov,mp4,m4a,3gp,3g2,mj2"
        run.side_effect = [
            completed(probe_payload(format_name=audio_format)),
            completed(video_payload(format_name=audio_format, has_video=True)),
        ]
        video_metadata = self.inspector.inspect(self.path)
        self.assertEqual(video_metadata.container_format, "mp4")
        self.assertEqual(video_metadata.mime_type, "video/mp4")

        run.side_effect = [
            completed(probe_payload(format_name=audio_format)),
            completed(video_payload(format_name=audio_format, has_video=False)),
        ]
        audio_metadata = self.inspector.inspect(self.path)
        self.assertEqual(audio_metadata.container_format, "m4a")
        self.assertEqual(audio_metadata.mime_type, "audio/mp4")

    def test_rejects_symbolic_link(self) -> None:
        link = Path(self.temporary.name) / "link.wav"
        try:
            link.symlink_to(self.path)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links are unavailable in this environment")
        with self.assertRaises(MediaInspectionError) as context:
            self.inspector.inspect(link)
        self.assertEqual(context.exception.code, "MEDIA_INVALID_PATH")

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe is not installed")
    def test_real_ffprobe_can_inspect_synthetic_wave(self) -> None:
        wave_path = Path(self.temporary.name) / "generated.wav"
        with wave.open(str(wave_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 16000)
        metadata = FFprobeMediaInspector().inspect(wave_path)
        self.assertEqual(metadata.container_format, "wav")
        self.assertEqual(metadata.sample_rate_hz, 16000)
        self.assertEqual(metadata.channels, 1)
        self.assertEqual(metadata.duration_ms, 1000)


if __name__ == "__main__":
    unittest.main()
