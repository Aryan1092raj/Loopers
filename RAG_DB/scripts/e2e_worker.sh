#!/usr/bin/env bash
# Upload a document through Render, wait for the GPU job, then ask one question and require a cited answer.
# Usage: RENDER_API_URL=https://loopers-kuda.onrender.com bash scripts/e2e_worker.sh path/to/doc.pdf "question about it"
set -euo pipefail
api="${RENDER_API_URL:?set RENDER_API_URL}"
resp=$(curl -fsS -m 600 -F "file=@$1" -F "subject=e2e" "$api/api/sources")
job=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$resp")
for _ in $(seq 1 120); do
  st=$(curl -fsS "$api/api/jobs/$job" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d.get("stage"), d.get("error") or "")')
  echo "job $job: $st"
  case "$st" in done*) break ;; failed*) exit 1 ;; esac
  sleep 10
done
curl -fsS -m 120 -H 'content-type: application/json' \
  -d "{\"session_id\":\"e2e\",\"student_id\":\"e2e\",\"message\":\"$2\"}" "$api/api/chat" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d.get("citations") or []; print(d.get("answer","")[:300]); sys.exit(0 if c else "no citations")'
