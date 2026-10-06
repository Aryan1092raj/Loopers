#!/usr/bin/env bash
# Phase 2 exit gate (PLAN §5): drives the running API end to end on the real corpus.
# A's half covers documents, grounded and refused chat, citations and the graph.
# B extends the marked section at the bottom with video, intake, quiz and mastery.
#
#   cd RAG_DB/src && LOOPERS_NO_REEXEC=1 uvicorn api.main:app --port 8000   # in one shell
#   bash scripts/e2e_smoke.sh                                               # in another
#
# Settings (environment): SMOKE_BASE, SMOKE_PDF, SMOKE_QUESTION, SMOKE_OFF_TOPIC, SMOKE_TIMEOUT (seconds).
set -euo pipefail

BASE="${SMOKE_BASE:-http://localhost:8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PDF="${SMOKE_PDF:-$ROOT/RAG_DB/raw/openstax_calculus-vol1.pdf}"
QUESTION="${SMOKE_QUESTION:-What is the definition of the limit of a function?}"
OFF_TOPIC="${SMOKE_OFF_TOPIC:-Who won the 1998 football World Cup final?}"
TIMEOUT="${SMOKE_TIMEOUT:-3600}"
SESSION="smoke-$$-$(date +%s)"

# Environment proxies would send localhost calls elsewhere.
export NO_PROXY="localhost,127.0.0.1" no_proxy="localhost,127.0.0.1"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

# py '<expression over d>' < json  prints the expression's value.
py() { python3 -c 'import sys, json; d = json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }

post_json() { curl -sf -H 'content-type: application/json' -d "$2" "$BASE$1"; }

[ -f "$PDF" ] || fail "no document at $PDF (set SMOKE_PDF)"

# 1. API is up
curl -sf "$BASE/api/status" > /dev/null || fail "$BASE/api/status is not answering"
ok "api status"

# 2. Upload a document and wait for its job
upload="$(curl -sf -F "file=@$PDF" -F "title=Smoke Calculus" -F "subject=calculus" -F "lecture_no=1" "$BASE/api/sources")" \
  || fail "upload rejected"
source_id="$(echo "$upload" | py "d['source_id']")"
job_id="$(echo "$upload" | py "d['job_id']")"
ok "upload accepted: source $source_id, job $job_id"

deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  status="$(curl -sf "$BASE/api/jobs/$job_id" | py "d['status']")"
  case "$status" in
    done) break ;;
    failed) curl -sf "$BASE/api/jobs/$job_id/log?tail=30" | py "chr(10).join(d['lines'])" >&2; fail "job $job_id failed" ;;
  esac
  [ "$(date +%s)" -lt "$deadline" ] || fail "job $job_id still $status after ${TIMEOUT}s"
  sleep 10
done
ok "job reached done"

units="$(curl -sf "$BASE/api/sources" | py "next(s['unit_count'] for s in d if s['source_id'] == '$source_id')")"
[ "$units" -gt 0 ] || fail "source $source_id has no units"
ok "source has $units units"

# 3. Grounded chat: a mode of grounded, a page citation, and its page image
chat="$(post_json /api/chat "{\"session_id\":\"$SESSION\",\"student_id\":\"smoke\",\"message\":\"$QUESTION\"}")" || fail "chat call failed"
mode="$(echo "$chat" | py "d['mode']")"
case "$mode" in grounded|partially_grounded) ;; *) fail "expected a grounded answer, got mode $mode" ;; esac
page="$(echo "$chat" | py "d['citations'][0]['locator']['page_no']")"
[ "$page" != "None" ] || fail "first citation has no page_no"
image="$(echo "$chat" | py "d['citations'][0].get('page_image_url') or ''")"
[ -n "$image" ] || fail "first citation has no page_image_url"
curl -sf -o /dev/null "$BASE$image" || fail "page image $image does not load"
ok "grounded chat: page $page, image $image"

# 4. Refused chat
refused="$(post_json /api/chat "{\"session_id\":\"$SESSION-2\",\"student_id\":\"smoke\",\"message\":\"$OFF_TOPIC\"}" | py "d['mode']")"
[ "$refused" = "refused" ] || fail "off-topic question got mode $refused, expected refused"
ok "off-topic question refused"

# 5. Concept graph (the fixture graph counts until the concept build has run)
nodes="$(curl -sf "$BASE/api/graph" | py "len(d['nodes'])")"
[ "$nodes" -gt 0 ] || fail "graph has no nodes"
ok "graph has $nodes nodes"

# --- B's part: extend below this line -------------------------------------------------------
# - upload a video through POST /api/sources, wait for its job, expect units with t_start and t_end
# - grounded chat whose first citation has seek_url (a video citation)
# - intake and diagnostic through the students endpoints; expect non-default mastery afterwards
# - practice quiz: create, answer, report; expect a mastery change
# ---------------------------------------------------------------------------------------------

echo "smoke passed"
