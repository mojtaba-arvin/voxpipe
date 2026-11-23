import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_sbom import build_bom, read_concatenated_json


class SBOMBuilderTests(unittest.TestCase):
    def test_builds_deterministic_cyclonedx_bom_with_go_and_python_components(self):
        go = [{"Path": "github.com/jackc/pgx/v5", "Version": "v5.7.2"}]
        python = [{"name": "jsonschema", "version": "4.23.0"}]
        first = build_bom(go, python, "v1.2.3")
        second = build_bom(go, python, "v1.2.3")
        self.assertEqual(first, second)
        self.assertEqual(first["bomFormat"], "CycloneDX")
        self.assertEqual(first["specVersion"], "1.6")
        self.assertEqual(len(first["components"]), 2)
        self.assertTrue(all(item["purl"].startswith("pkg:") for item in first["components"]))

    def test_skips_main_go_module_and_normalizes_python_distribution_names(self):
        bom = build_bom(
            [{"Path": "voxpipe", "Main": True}, {"Path": "example.com/lib", "Version": "v1.0.0"}],
            [{"name": "some_pkg", "version": "2.0"}],
            "0.1.0",
        )
        names = {item["name"] for item in bom["components"]}
        self.assertEqual(names, {"example.com/lib", "some-pkg"})

    def test_rejects_duplicate_components_and_empty_version(self):
        with self.assertRaises(ValueError):
            build_bom([], [{"name": "x", "version": "1"}, {"name": "x", "version": "1"}], "v1")
        with self.assertRaises(ValueError):
            build_bom([], [], "")

    def test_reads_go_list_concatenated_json_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "modules.json"
            path.write_text('{"Path":"a","Version":"v1"}\n{"Path":"b","Version":"v2"}\n', encoding="utf-8")
            self.assertEqual(len(read_concatenated_json(path)), 2)


if __name__ == "__main__":
    unittest.main()
