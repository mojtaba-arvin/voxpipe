#!/usr/bin/env python3
"""Validate VoxPipe's machine-readable engineering work ledger."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "planning" / "WORK_ITEMS.json"
VALID_STATES = {
    "planned",
    "ready",
    "in_progress",
    "blocked",
    "review",
    "done",
    "cancelled",
}
REQUIRED_FILES = [
    "AGENTS.md",
    "README.md",
    "docs/ROADMAP.md",
    "docs/architecture/overview.md",
    "docs/operations/agent-execution-protocol.md",
    "planning/WORK_ITEMS.json",
]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            fail(f"required file is missing: {relative}")

    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read work ledger: {exc}")

    if data.get("schema_version") != 1:
        fail("unsupported or missing schema_version")
    if data.get("project") != "voxpipe":
        fail("ledger project must be 'voxpipe'")

    items = data.get("items")
    if not isinstance(items, list) or not items:
        fail("items must be a non-empty array")

    by_id: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            fail("every work item must be an object")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            fail("every work item needs a non-empty id")
        if item_id in by_id:
            fail(f"duplicate work item id: {item_id}")
        by_id[item_id] = item

        if item.get("status") not in VALID_STATES:
            fail(f"{item_id}: invalid status {item.get('status')!r}")
        if not isinstance(item.get("acceptance_criteria"), list) or not item["acceptance_criteria"]:
            fail(f"{item_id}: acceptance_criteria must be a non-empty array")
        if not isinstance(item.get("depends_on", []), list):
            fail(f"{item_id}: depends_on must be an array")
        if item.get("status") == "blocked" and not item.get("blocked_reason"):
            fail(f"{item_id}: blocked items must explain the blocker")
        if item.get("status") == "done" and not item.get("evidence"):
            fail(f"{item_id}: done items must include evidence")

    for item_id, item in by_id.items():
        for dependency in item.get("depends_on", []):
            if dependency not in by_id:
                fail(f"{item_id}: unknown dependency {dependency}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visiting:
            fail(f"dependency cycle detected at {item_id}")
        if item_id in visited:
            return
        visiting.add(item_id)
        for dependency in by_id[item_id].get("depends_on", []):
            visit(dependency)
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in by_id:
        visit(item_id)

    for item_id, item in by_id.items():
        if item.get("status") in {"ready", "in_progress", "review", "done"}:
            incomplete = [
                dep for dep in item.get("depends_on", [])
                if by_id[dep].get("status") != "done"
            ]
            if incomplete:
                fail(f"{item_id}: status {item['status']} has incomplete dependencies: {incomplete}")

    print(f"VoxPipe repository validation passed: {len(by_id)} work items, no missing dependencies or cycles.")


if __name__ == "__main__":
    main()
