#!/usr/bin/env bash
set -euo pipefail

WORKFLOW="${WORKFLOW:-deploy-cloud-run-job.yml}"
BRANCH="${BRANCH:-main}"
REGION="${REGION:-asia-northeast1}"
SCAN_DAYS="${SCAN_DAYS:-3}"

BEFORE_IDS="$(
  gh run list \
    --workflow "$WORKFLOW" \
    --limit 10 \
    --json databaseId \
    --jq '.[].databaseId' \
    | tr '\n' ' '
)"

gh workflow run "$WORKFLOW" \
  --ref "$BRANCH" \
  -f "region=$REGION" \
  -f "scan_days=$SCAN_DAYS"

echo "Triggered $WORKFLOW on $BRANCH with region=$REGION scan_days=$SCAN_DAYS"
echo "Waiting for GitHub to create the workflow run..."

RUN_ID=""
for _ in {1..30}; do
  RUN_ID="$(
    gh run list \
      --workflow "$WORKFLOW" \
      --limit 10 \
      --json databaseId,event,status \
      --jq '.[] | select(.event == "workflow_dispatch") | .databaseId' \
      | while read -r candidate; do
          if [[ " $BEFORE_IDS " != *" $candidate "* ]]; then
            echo "$candidate"
            break
          fi
        done
  )"
  if [[ -n "$RUN_ID" ]]; then
    break
  fi
  sleep 2
done

if [[ -z "$RUN_ID" ]]; then
  echo "Workflow was triggered, but the new run was not found yet." >&2
  echo "Use: gh run list --workflow $WORKFLOW --limit 5" >&2
  exit 1
fi

echo "Watching deploy run $RUN_ID"
gh run watch "$RUN_ID" --exit-status
