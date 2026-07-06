#!/usr/bin/env bash
set -euo pipefail

PROJECT="${PROJECT:-activists-edinet}"
REGION="${REGION:-asia-northeast1}"
JOB="${JOB:-edinet-watcher-hourly}"
EXECUTION="${1:-}"

if [[ -z "$EXECUTION" ]]; then
  EXECUTION="$(
    gcloud run jobs executions list \
      --project "$PROJECT" \
      --region "$REGION" \
      --job "$JOB" \
      --limit 1 \
      --sort-by '~metadata.creationTimestamp' \
      --format 'value(metadata.name)'
  )"
fi

if [[ -z "$EXECUTION" ]]; then
  echo "No executions found for Cloud Run job: $JOB" >&2
  exit 1
fi

echo "Watching Cloud Run execution $EXECUTION for job $JOB"
while true; do
  gcloud run jobs executions describe "$EXECUTION" \
    --project "$PROJECT" \
    --region "$REGION" \
    --format 'table(metadata.name,status.conditions[-1].type,status.conditions[-1].status,status.conditions[-1].message)'

  STATE="$(
    gcloud run jobs executions describe "$EXECUTION" \
      --project "$PROJECT" \
      --region "$REGION" \
      --format 'value(status.conditions[-1].type,status.conditions[-1].status)'
  )"

  case "$STATE" in
    *"Completed"*"True"*)
      exit 0
      ;;
    *"Completed"*"False"*|*"Failed"*)
      exit 1
      ;;
  esac

  sleep "${INTERVAL_SECONDS:-10}"
done
