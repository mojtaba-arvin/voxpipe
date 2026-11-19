"""Black-box tests for the repository work-ledger validator."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = SOURCE_ROOT / "scripts" / "validate_repo.py"


def valid_ledger() -> dict:
    return {
        "schema_version": 1,
        "project": "voxpipe",
        "updated_at": "2026-11-19",
        "items": [
            {
                "id": "VP-TEST-1",
                "title": "A valid task",
                "phase": 0,
                "priority": 1,
                "status": "ready",
                "depends_on": [],
                "acceptance_criteria": ["The validator accepts this task."],
                "evidence": [],
                "blocked_reason": None,
            }
        ],
    }


class ValidateRepoTests(unittest.TestCase):
    def run_validator(
        self, ledger: dict | str, *, skill_content: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "planning").mkdir()
            (root / "scripts").mkdir()
            for relative in (
                "AGENTS.md",
                "README.md",
                "docs/ROADMAP.md",
                "docs/architecture/overview.md",
                "docs/operations/agent-execution-protocol.md",
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("test fixture\n", encoding="utf-8")
            shutil.copy2(VALIDATOR, root / "scripts" / "validate_repo.py")
            content = ledger if isinstance(ledger, str) else json.dumps(ledger)
            skills = root / ".agents" / "skills" / "fixture-skill"
            skills.mkdir(parents=True)
            (skills / "SKILL.md").write_text(
                skill_content
                or "---\nname: fixture-skill\ndescription: Use this skill for fixture validation and test coverage.\n---\n\nInstructions.\n",
                encoding="utf-8",
            )
            agents = root / ".codex" / "agents"
            agents.mkdir(parents=True)
            (agents / "reviewer.toml").write_text(
                'name = "reviewer"\ndescription = "Read-only fixture reviewer."\ndeveloper_instructions = "Do not edit files."\n',
                encoding="utf-8",
            )
            (root / ".codex" / "config.toml").write_text(
                "[agents]\nmax_concurrent_threads_per_session = 2\n",
                encoding="utf-8",
            )
            (root / "planning" / "WORK_ITEMS.json").write_text(content, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(root / "scripts" / "validate_repo.py")],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_accepts_valid_ledger(self) -> None:
        result = self.run_validator(valid_ledger())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validation passed", result.stdout)

    def test_rejects_skill_without_discovery_description(self) -> None:
        result = self.run_validator(
            valid_ledger(),
            skill_content="---\nname: fixture-skill\n---\n\nInstructions.\n",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires name and description", result.stderr)

    def test_rejects_invalid_json(self) -> None:
        result = self.run_validator("{not-json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot read work ledger", result.stderr)

    def test_rejects_unknown_dependency(self) -> None:
        ledger = valid_ledger()
        ledger["items"][0]["depends_on"] = ["VP-MISSING"]
        result = self.run_validator(ledger)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown dependency", result.stderr)

    def test_rejects_dependency_cycle(self) -> None:
        ledger = valid_ledger()
        ledger["items"].append(
            {
                **ledger["items"][0],
                "id": "VP-TEST-2",
                "status": "planned",
                "depends_on": ["VP-TEST-1"],
            }
        )
        ledger["items"][0]["depends_on"] = ["VP-TEST-2"]
        result = self.run_validator(ledger)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dependency cycle", result.stderr)

    def test_done_task_requires_evidence(self) -> None:
        ledger = valid_ledger()
        ledger["items"][0]["status"] = "done"
        result = self.run_validator(ledger)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must include evidence", result.stderr)

    def test_blocked_task_requires_reason(self) -> None:
        ledger = valid_ledger()
        ledger["items"][0]["status"] = "blocked"
        result = self.run_validator(ledger)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must explain the blocker", result.stderr)

    def test_active_task_requires_completed_dependencies(self) -> None:
        ledger = valid_ledger()
        ledger["items"].append(
            {
                **ledger["items"][0],
                "id": "VP-TEST-2",
                "status": "planned",
                "depends_on": [],
            }
        )
        ledger["items"][0]["depends_on"] = ["VP-TEST-2"]
        result = self.run_validator(ledger)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete dependencies", result.stderr)


if __name__ == "__main__":
    unittest.main()
