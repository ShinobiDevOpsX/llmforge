#!/usr/bin/env bash
# Pulls a model into the Ollama container's persistent volume.
# Usage: ./scripts/download-model.sh [model]   (default: LLMFORGE_DEFAULT_MODEL or llama3.2:1b)
#
# Model download is deliberately a separate, explicit step: the API never
# downloads models at startup, so container start-up stays fast and offline-safe.
set -euo pipefail

cd "$(dirname "$0")/.."

model="${1:-}"
if [ -z "$model" ] && [ -f .env ]; then
  model="$(grep -E '^LLMFORGE_DEFAULT_MODEL=' .env | tail -n1 | cut -d= -f2- || true)"
fi
model="${model:-llama3.2:1b}"

if ! [[ "$model" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
  echo "Refusing suspicious model name: $model" >&2
  exit 2
fi

echo "Waiting for the ollama service to become healthy..."
for _ in $(seq 1 60); do
  status="$(docker inspect --format '{{.State.Health.Status}}' llmforge-ollama-1 2>/dev/null || echo missing)"
  [ "$status" = "healthy" ] && break
  sleep 2
done
[ "$status" = "healthy" ] || { echo "ollama is not healthy (status: $status). Is the stack up?" >&2; exit 1; }

echo "Pulling $model (this can take a few minutes on first run)..."
docker compose exec -T ollama ollama pull "$model"
echo
docker compose exec -T ollama ollama list
echo
echo "Done. Readiness: curl -s http://127.0.0.1:${LLMFORGE_API_PORT:-8000}/ready"
