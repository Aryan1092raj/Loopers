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

# --- B's part -------------------------------------------------------------------------------
# Chat moves mastery: the grounded chat in step 3 ran for student "smoke".
updates="$(echo "$chat" | py "len(d['mastery_updates'])")"
[ "$updates" -gt 0 ] || fail "grounded chat returned no mastery_updates (are the retrieved units tagged to concepts?)"
ok "chat updated mastery for $updates concepts"

# 6. Onboarding and diagnostic: answering one diagnostic item must create learner state
STU="smoke-b-$$"
post_json /api/students/onboard "{\"student_id\":\"$STU\",\"background\":\"beginner\",\"exam_date\":\"$(date -d '+10 days' +%F)\"}" > /dev/null \
  || fail "onboard failed"
diag="$(curl -sf "$BASE/api/students/$STU/diagnostic")" || fail "diagnostic call failed"
if [ "$(echo "$diag" | py "d.get('status', 'item')")" = "complete" ]; then
  echo "skip: no diagnostic items in the bank yet (the quiz step below fills it)"
else
  item="$(echo "$diag" | py "d['item_id']")"
  post_json "/api/students/$STU/diagnostic/respond" "{\"item_id\":\"$item\",\"correct\":true}" > /dev/null || fail "diagnostic respond failed"
  known="$(curl -sf "$BASE/api/students/$STU/mastery" | py "len(d)")"
  [ "$known" -gt 0 ] || fail "diagnostic answer created no mastery rows"
  ok "diagnostic: $known concepts have mastery"
fi

# 7. Practice quiz round trip on the first concept that has source units
# The concept build can leave a node with no units under it, so take the first node that has material.
for concept in $(curl -sf "$BASE/api/graph" | py "' '.join(n['id'] for n in d['nodes'])"); do
  gen="$(post_json /api/quiz/generate "{\"concept_id\":\"$concept\",\"topic_id\":\"smoke\",\"item_type\":\"mcq\"}")" || fail "quiz generate call failed"
  [ "$(echo "$gen" | py "d.get('reason')")" = "no_material" ] || break
done
[ "$(echo "$gen" | py "d['status']")" = "success" ] || fail "quiz generate for $concept: $(echo "$gen" | py "d.get('reason')") (has the concept build run?)"
echo "$gen" | py "'answer_json' not in d['item']" | grep -q True || fail "quiz item leaks answer_json"
quiz_item="$(echo "$gen" | py "d['item']['item_id']")"
pick="$(echo "$gen" | py "__import__('json').loads(d['item']['options_json'])[0]")"
sub="$(curl -sf -X POST -H 'content-type: application/json' "$BASE/api/quiz/submit?student_id=$STU" \
  -d "{\"quiz_id\":\"quiz-$STU\",\"item_id\":\"$quiz_item\",\"student_answer\":[\"$pick\"]}")" || fail "quiz submit failed"
cites="$(echo "$sub" | py "len(d['feedback']['citations'])")"
[ "$cites" -gt 0 ] || fail "quiz feedback has no citations"
report="$(curl -sf "$BASE/api/quiz/report/quiz-$STU?student_id=$STU")" || fail "quiz report failed"
[ "$(echo "$report" | py "d['raw_metrics']['total_items']")" = "1" ] || fail "report does not show the answered item"
delta="$(echo "$report" | py "d['mastery_delta'][0]['after'] != d['mastery_delta'][0]['before']")"
[ "$delta" = "True" ] || fail "quiz answer did not change mastery"
ok "quiz round trip: item $quiz_item, $cites citations, mastery changed"

# 8. Schedule honours the exam date
days="$(curl -sf "$BASE/api/students/$STU/schedule?minutes_per_day=30" | py "len(d)")"
[ "$days" -gt 0 ] || fail "schedule is empty"
ok "schedule has $days days"

# 9. Video (set SMOKE_VIDEO to a lecture recording, SMOKE_VIDEO_QUESTION to something it covers)
if [ -n "${SMOKE_VIDEO:-}" ]; then
  [ -f "$SMOKE_VIDEO" ] || fail "no video at $SMOKE_VIDEO"
  vup="$(curl -sf -F "file=@$SMOKE_VIDEO" -F "title=Smoke Lecture" -F "subject=calculus" -F "lecture_no=2" "$BASE/api/sources")" || fail "video upload rejected"
  vjob="$(echo "$vup" | py "d['job_id']")"
  vsource="$(echo "$vup" | py "d['source_id']")"
  deadline=$(( $(date +%s) + TIMEOUT ))
  while :; do
    vstatus="$(curl -sf "$BASE/api/jobs/$vjob" | py "d['status']")"
    case "$vstatus" in
      done) break ;;
      failed) fail "video job $vjob failed" ;;
    esac
    [ "$(date +%s)" -lt "$deadline" ] || fail "video job $vjob still $vstatus after ${TIMEOUT}s"
    sleep 10
  done
  vchat="$(post_json /api/chat "{\"session_id\":\"$SESSION-v\",\"student_id\":\"smoke\",\"message\":\"${SMOKE_VIDEO_QUESTION:?set SMOKE_VIDEO_QUESTION}\"}")" || fail "video chat failed"
  seek="$(echo "$vchat" | py "next((c['seek_url'] for c in d['citations'] if c.get('seek_url')), '')")"
  [ -n "$seek" ] || fail "no citation has a seek_url for the video question"
  ok "video citation: $seek"
else
  echo "skip: SMOKE_VIDEO not set, video upload and seek_url checks not run"
fi
# ---------------------------------------------------------------------------------------------

echo "smoke passed"
