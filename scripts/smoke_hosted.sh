#!/usr/bin/env bash
# Smoke test for a running stack (local or hosted): health, dashboard, one fault, one detected incident, one diagnosis.
#
#   COPILOT_URL=https://demo.example.com SHOP_URL=https://demo.example.com:8443 CHAOS_KEY=... scripts/smoke_hosted.sh
#
# Defaults target a local stack (http://localhost:9911 and http://localhost:9922). Exits non-zero on the first failed check, and
# always switches the fault off again. It makes ONE chat call (a real model call), so run it sparingly on a budget-capped demo.
# Set SKIP_CHAT=1 to skip the model call. Needs curl and python3 (or python).
set -uo pipefail

COPILOT_URL="${COPILOT_URL:-http://localhost:9911}"; COPILOT_URL="${COPILOT_URL%/}"
SHOP_URL="${SHOP_URL:-http://localhost:9922}";       SHOP_URL="${SHOP_URL%/}"
FAULT="${FAULT:-payment_failure}"
DETECT_TIMEOUT="${DETECT_TIMEOUT:-240}"
chaos_hdr=(); [ -n "${CHAOS_KEY:-}" ] && chaos_hdr=(-H "X-Chaos-Key: ${CHAOS_KEY}")
fails=0

ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fails=$((fails + 1)); }
code() { curl -s -o /dev/null -m 30 -w '%{http_code}' "$@"; }
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys' >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "python3 (or python) is required" >&2; exit 2; }
json() { "$PY" -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null | tr -d '\r'; }
cleanup() { curl -s -m 15 -o /dev/null -X POST "${chaos_hdr[@]+"${chaos_hdr[@]}"}" "$SHOP_URL/api/faults/reset"; }
trap cleanup EXIT

echo "Smoke test: copilot=$COPILOT_URL shop=$SHOP_URL fault=$FAULT"

echo "1. Reachability"
[ "$(code "$COPILOT_URL/dashboard")" = 200 ] && ok "dashboard 200" || bad "dashboard did not return 200"
[ "$(code "$COPILOT_URL/api/incidents")" = 200 ] && ok "incident API 200" || bad "incident API did not return 200"
[ "$(code "$SHOP_URL/api/faults")" = 200 ] && ok "shop fault API 200" || bad "shop fault API did not return 200"
mem=$(curl -s -m 30 "$COPILOT_URL/api/memory/past-incidents" | json "d.get('enabled')")
[ "$mem" = "True" ] && ok "memory enabled" || bad "memory is not enabled (MEMORY_MODE=on and Hindsight up?)"
[ "$(code "$COPILOT_URL/ingest/events" -X POST -H 'Content-Type: application/json' -d '{}')" = 404 ] && ok "ingest API not public (404)" \
  || echo "  note: /ingest/events answered something other than 404 (expected only on a local stack, not behind the proxy)"

echo "2. Fault injection"
if [ -n "${CHAOS_KEY:-}" ]; then
  [ "$(code -X POST "$SHOP_URL/api/faults" -H 'Content-Type: application/json' -d "{\"fault_type\":\"$FAULT\",\"enabled\":true,\"params\":{}}")" = 401 ] \
    && ok "fault change without the key is refused (401)" || bad "fault change without the key was NOT refused"
fi
before=$(curl -s -m 30 "$COPILOT_URL/api/incidents?limit=200" | json "len(d)")
[ "$(code -X POST "${chaos_hdr[@]+"${chaos_hdr[@]}"}" "$SHOP_URL/api/faults" -H 'Content-Type: application/json' -d "{\"fault_type\":\"$FAULT\",\"enabled\":true,\"params\":{}}")" = 200 ] \
  && ok "fault $FAULT switched on" || { bad "could not switch the fault on (CHAOS_KEY wrong or missing?)"; exit 1; }

echo "3. Detection (up to ${DETECT_TIMEOUT}s)"
inc=""; end=$((SECONDS + DETECT_TIMEOUT))
while [ $SECONDS -lt $end ]; do
  inc=$(curl -s -m 30 "$COPILOT_URL/api/incidents?limit=200" | json "next((i['id'] for i in sorted(d, key=lambda x: x['opened_at'], reverse=True) if i['status'] != 'RESOLVED'), '')")
  [ -n "$inc" ] && break; sleep 5
done
[ -n "$inc" ] && ok "incident $inc opened" || { bad "no incident opened within ${DETECT_TIMEOUT}s"; exit 1; }

echo "4. Diagnosis"
rca=""; end=$((SECONDS + 240))
while [ $SECONDS -lt $end ]; do
  rca=$(curl -s -m 30 "$COPILOT_URL/api/incidents/$inc" | json "d.get('rca_status') if d.get('rca_status') != 'pending' else ''")
  [ -n "$rca" ] && break; sleep 5
done
[ "$rca" = ok ] && ok "automatic diagnosis stored (rca_status=ok)" || bad "automatic diagnosis ended as '${rca:-pending}'"

if [ "${SKIP_CHAT:-0}" != 1 ]; then
  cid=$(curl -s -m 30 -X POST "$COPILOT_URL/chat/new" | json "d['id']")
  reply=$(curl -s -m 300 -X POST "$COPILOT_URL/ask/$cid" -H 'Content-Type: application/json' -d '{"message":"why is checkout failing?"}')
  tier=$(printf '%s' "$reply" | json "d.get('tier')"); joined=$(printf '%s' "$reply" | json "d.get('incident_id')")
  budget=$(printf '%s' "$reply" | json "d.get('budget_exhausted')")
  if [ "$budget" = True ]; then
    ok "chat answered from the demo-budget replay (budget_exhausted)"
  elif printf '%s' "$reply" | grep -q 'Proposed action'; then
    ok "chat RCA returned a proposed action (tier=$tier, incident=$joined)"
  else
    bad "chat RCA did not return a proposed action"
  fi
fi

echo
if [ "$fails" -eq 0 ]; then echo "SMOKE TEST PASSED"; else echo "SMOKE TEST FAILED: $fails check(s)"; exit 1; fi
