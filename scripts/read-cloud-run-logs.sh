#!/usr/bin/env bash
set -euo pipefail

PROJECT="${PROJECT:-activists-edinet}"
JOB="${JOB:-edinet-watcher-hourly}"
LIMIT="${LIMIT:-80}"
FRESHNESS="${FRESHNESS:-24h}"

FILTER="resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\""

gcloud logging read "$FILTER" \
  --project "$PROJECT" \
  --freshness "$FRESHNESS" \
  --limit "$LIMIT" \
  --format json \
  | jq -r '
      reverse[]
      | {
          time: .timestamp,
          severity: (.severity // "DEFAULT"),
          event: (.jsonPayload.event // .jsonPayload.message // .textPayload // ""),
          payload: (.jsonPayload // {})
        }
      | @json
    '
