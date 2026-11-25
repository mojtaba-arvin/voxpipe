#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${ISSUE_NUMBER:?ISSUE_NUMBER is required}"
: "${COMMENT_BODY:?COMMENT_BODY is required}"

if [[ ! "$ISSUE_NUMBER" =~ ^[1-9][0-9]*$ ]]; then
  echo "ISSUE_NUMBER must be a positive integer." >&2
  exit 2
fi

payload="$(python -c 'import json, os; print(json.dumps({"body": os.environ["COMMENT_BODY"]}))')"
response="$(
  curl --fail-with-body --silent --show-error \
    --request POST \
    --header "Accept: application/vnd.github+json" \
    --header "Authorization: Bearer ${GITHUB_TOKEN}" \
    --header "X-GitHub-Api-Version: 2022-11-28" \
    --data-binary "$payload" \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/issues/${ISSUE_NUMBER}/comments"
)"

python -c 'import json, sys; data = json.load(sys.stdin); print(data.get("html_url", "Comment created."))' <<< "$response"
