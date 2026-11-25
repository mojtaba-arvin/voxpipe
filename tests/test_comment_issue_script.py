from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "comment_issue.sh"


class CommentIssueScriptTests(unittest.TestCase):
    def test_posts_json_comment_to_selected_repository_issue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            capture = temp / "curl-args.json"
            fake_curl = temp / "curl"
            fake_curl.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['CURL_CAPTURE'], 'w', encoding='utf-8') as f:\n"
                "    json.dump(sys.argv[1:], f)\n"
                "print(json.dumps({'html_url': 'https://github.com/acme/voxpipe/issues/7#issuecomment-1'}))\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{temp}{os.pathsep}{env['PATH']}",
                    "CURL_CAPTURE": str(capture),
                    "GITHUB_TOKEN": "test-token",
                    "GITHUB_REPOSITORY": "acme/voxpipe",
                    "ISSUE_NUMBER": "7",
                    "COMMENT_BODY": 'A disclosure with "quotes" and a newline.\nSecond line.',
                }
            )

            result = subprocess.run(
                ["bash", str(SCRIPT)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("https://github.com/acme/voxpipe/issues/7#issuecomment-1", result.stdout)
            args = json.loads(capture.read_text(encoding="utf-8"))
            self.assertIn("https://api.github.com/repos/acme/voxpipe/issues/7/comments", args)
            self.assertIn("Authorization: Bearer test-token", args)
            payload = args[args.index("--data-binary") + 1]
            self.assertEqual(json.loads(payload), {"body": env["COMMENT_BODY"]})

    def test_rejects_invalid_issue_number_before_network_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            fake_curl = temp / "curl"
            fake_curl.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            fake_curl.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{temp}{os.pathsep}{env['PATH']}",
                    "GITHUB_TOKEN": "test-token",
                    "GITHUB_REPOSITORY": "acme/voxpipe",
                    "ISSUE_NUMBER": "7/../../issues/8",
                    "COMMENT_BODY": "must not be sent",
                }
            )

            result = subprocess.run(
                ["bash", str(SCRIPT)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("positive integer", result.stderr)


if __name__ == "__main__":
    unittest.main()
