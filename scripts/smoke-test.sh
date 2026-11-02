#!/usr/bin/env bash
# End-to-end smoke test against a running stack.
# Usage: ./scripts/smoke-test.sh            (reads the API key from .env)
#        API_URL=... API_KEY=... ./scripts/smoke-test.sh
set -euo pipefail

cd "$(dirname "$0")/.."

API_URL="${API_URL:-http://127.0.0.1:${LLMFORGE_API_PORT:-8000}}"
PROM_URL="${PROM_URL:-http://127.0.0.1:${PROMETHEUS_PORT:-9090}}"
GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:${GRAFANA_PORT:-3000}}"
if [ -z "${API_KEY:-}" ] && [ -f .env ]; then
  API_KEY="$(grep -E '^LLMFORGE_API_KEYS=' .env | cut -d= -f2- | cut -d, -f1)"
fi
: "${API_KEY:?API_KEY not set and not found in .env}"

pass=0; failures=0
check() {
  local name="$1"; shift
  if "$@"; then printf '  \033[32mPASS\033[0m %s\n' "$name"; pass=$((pass+1))
  else printf '  \033[31mFAIL\033[0m %s\n' "$name"; failures=$((failures+1)); fi
}
status_is() { [ "$(curl -s -o /dev/null -w '%{http_code}' "${@:2}")" = "$1" ]; }
gen_tokens() {
  curl -s "$API_URL/metrics" | awk '/^llmforge_output_tokens_total\{/ {s+=$2} END {print s+0}'
}

echo "LLMForge smoke test -> $API_URL"
check "GET /health returns 200"        status_is 200 "$API_URL/health"
check "GET /ready returns 200"         status_is 200 "$API_URL/ready"
check "GET /v1/models without key 401" status_is 401 "$API_URL/v1/models"
check "GET /v1/models with key 200"    status_is 200 -H "X-API-Key: $API_KEY" "$API_URL/v1/models"
check "Empty prompt rejected (422)"    status_is 422 -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
                                          -d '{"prompt":""}' "$API_URL/v1/generate"
check "Disallowed model rejected (400)" status_is 400 -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
                                          -d '{"prompt":"hi","model":"not-allowed:1b"}' "$API_URL/v1/generate"

before="$(gen_tokens)"
echo "  ... generating (CPU inference may take a while)"
body="$(curl -s --max-time 180 -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"prompt":"In one sentence, what is Kubernetes?","max_tokens":48}' "$API_URL/v1/generate")"
echo "  response: $body"
check "POST /v1/generate returns text" python3 -c \
  'import json,sys; d=json.loads(sys.argv[1]); assert d["response"].strip() and d["completion_tokens"]>0' "$body"
after="$(gen_tokens)"
check "Output token metric increased ($before -> $after)" python3 -c 'import sys; assert float(sys.argv[2])>float(sys.argv[1])' "$before" "$after"

check "Prometheus target llmforge-api is up" python3 -c '
import json,sys,urllib.request
d=json.load(urllib.request.urlopen(sys.argv[1]+"/api/v1/query?query=up%7Bjob%3D%22llmforge-api%22%7D"))
assert d["data"]["result"] and d["data"]["result"][0]["value"][1]=="1"' "$PROM_URL"
check "Prometheus alert rules loaded" python3 -c '
import json,sys,urllib.request
d=json.load(urllib.request.urlopen(sys.argv[1]+"/api/v1/rules"))
assert sum(len(g["rules"]) for g in d["data"]["groups"])>=4' "$PROM_URL"
check "Grafana healthy"                status_is 200 "$GRAFANA_URL/api/health"

echo
echo "$pass passed, $failures failed"
[ "$failures" -eq 0 ]
