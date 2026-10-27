#!/usr/bin/env bash
# Reset the demo to a clean take (Linux/macOS/VM twin of scripts/reset_demo.ps1).
#
# Removes ONLY this project's two data volumes, by exact name:
#   <project>_copilot-data     incidents, chats, events
#   <project>_hindsight-data   Hindsight memory (skip with --keep-memory)
# and recreates loki/tempo/prometheus (telemetry only) unless --keep-telemetry. The shop DB, images, .env and every other
# Docker volume are left alone. MEMORY_MODE is not touched.
#
#   scripts/reset_demo.sh                 # fresh take: first fault = ultra tier, repeat = nano
#   scripts/reset_demo.sh --keep-memory   # keep what the copilot has learned, clear incidents and chats
set -euo pipefail
cd "$(dirname "$0")/.."

keep_memory=0; keep_telemetry=0
for a in "$@"; do
  case "$a" in
    --keep-memory) keep_memory=1 ;;
    --keep-telemetry) keep_telemetry=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

compose=(docker compose)
[ -n "${COMPOSE_FILES:-}" ] && compose=(docker compose $COMPOSE_FILES)   # e.g. COMPOSE_FILES="-f docker-compose.yml -f deploy/docker-compose.prod.yml"

project=$("${compose[@]}" config --format json | python3 -c 'import sys,json; print(json.load(sys.stdin)["name"])')
[ -n "$project" ] || { echo "Could not determine the compose project name (is Docker running?)" >&2; exit 1; }

chaos_hdr=()
[ -n "${CHAOS_KEY:-}" ] && chaos_hdr=(-H "X-Chaos-Key: $CHAOS_KEY")
curl -fsS -X POST "${SHOP_URL:-http://localhost:9922}/api/faults/reset" "${chaos_hdr[@]+"${chaos_hdr[@]}"}" >/dev/null \
  || echo "(faults not reset: frontend not reachable)"

services=(copilot adapter hindsight)
[ "$keep_telemetry" -eq 0 ] && services+=(loki tempo prometheus)
"${compose[@]}" stop "${services[@]}"
"${compose[@]}" rm -f "${services[@]}"

volumes=("${project}_copilot-data")
[ "$keep_memory" -eq 0 ] && volumes+=("${project}_hindsight-data")
for v in "${volumes[@]}"; do
  if docker volume ls --format '{{.Name}}' | grep -Fxq "$v"; then
    docker volume rm "$v" && echo "removed volume $v"
  else
    echo "volume $v not found (already clean)"
  fi
done

"${compose[@]}" up -d "${services[@]}"
echo "Done. Hindsight needs ~30 s to 5 min to be ready on a fresh volume."
