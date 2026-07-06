#!/usr/bin/env bash
set -euo pipefail

WORKFLOW="${WORKFLOW:-CI}"
RUN_ID="${1:-}"

if [[ -z "$RUN_ID" ]]; then
  RUN_ID="$(
    gh run list \
      --workflow "$WORKFLOW" \
      --branch "${BRANCH:-main}" \
      --limit 1 \
      --json databaseId \
      --jq '.[0].databaseId'
  )"
fi

if [[ -z "$RUN_ID" || "$RUN_ID" == "null" ]]; then
  echo "No GitHub Actions run found for workflow: $WORKFLOW" >&2
  exit 1
fi

echo "Watching $WORKFLOW run $RUN_ID"
gh run watch "$RUN_ID" --exit-status
