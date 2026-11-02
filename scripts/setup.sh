#!/usr/bin/env bash
# Checks prerequisites and creates .env with freshly generated secrets.
# Safe to re-run: an existing .env is never overwritten.
set -euo pipefail

cd "$(dirname "$0")/.."

ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✘\033[0m %s\n' "$*"; exit 1; }

echo "Checking prerequisites"
command -v docker >/dev/null || fail "docker is not installed"
docker info >/dev/null 2>&1 || fail "docker daemon is not reachable (is it running? is your user in the docker group?)"
ok "docker $(docker version --format '{{.Server.Version}}')"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 plugin is not installed"
ok "$(docker compose version)"

if command -v free >/dev/null; then
  mem_gb=$(free -g | awk '/^Mem:/ {print $2}')
  if [ "${mem_gb:-0}" -lt 4 ]; then warn "only ${mem_gb}GiB RAM; llama3.2:1b needs ~2GiB free"; else ok "${mem_gb}GiB RAM"; fi
fi
disk_gb=$(df -Pk . | awk 'NR==2 {print int($4/1024/1024)}')
if [ "${disk_gb:-0}" -lt 10 ]; then warn "only ${disk_gb}GiB free disk; images + model need ~10GiB"; else ok "${disk_gb}GiB free disk"; fi

gen_secret() {
  if command -v openssl >/dev/null; then openssl rand -hex "$1"
  else python3 -c "import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))" "$1"; fi
}

echo "Configuring environment"
if [ -f .env ]; then
  ok ".env already exists - leaving it untouched"
else
  cp .env.example .env
  chmod 600 .env
  api_key="$(gen_secret 32)"
  grafana_pw="$(gen_secret 16)"
  sed -i.bak -e "s|^LLMFORGE_API_KEYS=.*|LLMFORGE_API_KEYS=${api_key}|" \
             -e "s|^GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=${grafana_pw}|" .env
  rm -f .env.bak
  ok "created .env (mode 600) with a generated API key and Grafana password"
  echo "     Read them with: grep -E '^(LLMFORGE_API_KEYS|GRAFANA_ADMIN_PASSWORD)=' .env"
fi

cat <<'EOF'

Next steps:
  docker compose up -d --build
  ./scripts/download-model.sh
  ./scripts/smoke-test.sh
EOF
