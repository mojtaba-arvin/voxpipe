from __future__ import annotations
import hashlib
import stat
import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from scripts.dev_quickstart import create_sample_audio, ensure_environment_file

class LocalQuickstartTests(unittest.TestCase):
    def test_environment_is_generated_with_least_privilege_digest_and_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            values = ensure_environment_file(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(values["TENANT_ID"], str(uuid.UUID(values["TENANT_ID"])))
            self.assertEqual(values["API_TOKEN_HASHES"].split(":", 1)[0], hashlib.sha256(values["VOXPIPE_API_TOKEN"].encode()).hexdigest())
            self.assertIn(":ingestion:read,ingestion:write,transcript:read", values["API_TOKEN_HASHES"])

    def test_existing_environment_is_not_rotated_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            first = ensure_environment_file(path)
            second = ensure_environment_file(path)
            self.assertEqual(first, second)

    def test_sample_is_valid_mono_pcm_and_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            self.assertEqual(create_sample_audio(path), path)
            first = path.read_bytes()
            create_sample_audio(path)
            self.assertEqual(path.read_bytes(), first)
            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertEqual(audio.getframerate(), 16_000)
                self.assertEqual(audio.getnframes(), 32_000)

if __name__ == "__main__":
    unittest.main()
