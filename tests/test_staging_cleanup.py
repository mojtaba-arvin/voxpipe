from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from voxpipe.storage.staging import remove_staged_source


class StagedSourceCleanupTests(unittest.TestCase):
    def test_removes_scoped_source_and_temporary_parts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tenant, session = str(uuid4()), str(uuid4())
            directory = root / tenant / session
            directory.mkdir(parents=True)
            source = directory / "source"
            partial = directory / ".upload-interrupted.part"
            source.write_bytes(b"audio")
            partial.write_bytes(b"partial")
            removed = remove_staged_source(root, tenant, session, f"{tenant}/{session}/source", include_temporary=True)
            self.assertTrue(removed)
            self.assertFalse(source.exists())
            self.assertFalse(partial.exists())
            self.assertFalse(directory.exists())

    def test_rejects_path_traversal_and_cross_tenant_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tenant, session = str(uuid4()), str(uuid4())
            with self.assertRaises(ValueError):
                remove_staged_source(root, tenant, session, "../outside")
            with self.assertRaises(ValueError):
                remove_staged_source(root, tenant, session, f"{uuid4()}/{session}/source")

    def test_rejects_symlinked_source(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            tenant, session = str(uuid4()), str(uuid4())
            directory = root / tenant / session
            directory.mkdir(parents=True)
            external = Path(outside) / "secret"
            external.write_text("must remain")
            (directory / "source").symlink_to(external)
            with self.assertRaises(ValueError):
                remove_staged_source(root, tenant, session, f"{tenant}/{session}/source")
            self.assertTrue(external.exists())

    def test_supports_single_file_legacy_ingestion_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tenant, session = str(uuid4()), str(uuid4())
            source = root / "fixture.wav"
            source.write_bytes(b"audio")
            self.assertTrue(remove_staged_source(root, tenant, session, "fixture.wav"))
            self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
