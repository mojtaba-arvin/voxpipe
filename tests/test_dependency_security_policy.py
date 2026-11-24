import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DependencySecurityPolicyTests(unittest.TestCase):
    def test_go_toolchain_and_direct_dependencies_meet_vulnerability_fixes(self):
        go_mod = (ROOT / "go.mod").read_text(encoding="utf-8")
        self.assertIn("toolchain go1.26.11", go_mod)
        self.assertIn("github.com/jackc/pgx/v5 v5.13.0", go_mod)
        self.assertIn("golang.org/x/text v0.41.0", go_mod)
        self.assertNotIn("github.com/jackc/pgx/v5 v5.7.2", go_mod)
        self.assertNotIn("golang.org/x/text v0.21.0", go_mod)
        go_sum = (ROOT / "go.sum").read_text(encoding="utf-8")
        self.assertIn("github.com/stretchr/testify v1.11.1 h1:", go_sum)
        self.assertNotIn("github.com/stretchr/testify v1.8.1 h1:", go_sum)

    def test_python_benchmark_and_optional_speech_sets_exclude_vulnerable_floors(self):
        for path in ("requirements-benchmark.txt", "requirements-vad.txt", "requirements-lid.txt"):
            requirements = (ROOT / path).read_text(encoding="utf-8")
            self.assertRegex(requirements, r"(?m)^datasets>=5\.0\.1,<6$")
            self.assertRegex(requirements, r"(?m)^fsspec>=2026\.9\.0,<2027$")
            if path in ("requirements-benchmark.txt", "requirements-lid.txt"):
                self.assertRegex(requirements, r"(?m)^huggingface_hub>=2\.4\.0,<3$")

    def test_all_go_ci_jobs_use_the_patched_toolchain(self):
        for path in (
            ".github/workflows/validate.yml",
            ".github/workflows/dependency-audit.yml",
        ):
            workflow = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("go-version: '1.26.11'", workflow)


if __name__ == "__main__":
    unittest.main()
