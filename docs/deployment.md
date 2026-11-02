# Deployment Guide (Docker Compose)

## Requirements

| | Minimum | Tested on |
|-|---------|-----------|
| OS | Linux, macOS or Windows with Docker Desktop | Ubuntu 26.04, x86_64 |
| Docker | Engine 24+ with Compose v2 | Docker 28.3.3, Compose 2.39.1 |
| RAM | 4 GiB free (the model uses ~1.6 GiB when loaded) | 14 GiB |
| Disk | ~10 GiB (Ollama image 5.1 GB, Grafana 1.4 GB, model 1.3 GB, Prometheus + API ~0.4 GB) | 29 GiB free |
| GPU | Not required | CPU only |

## 1. Configure

```bash
./scripts/setup.sh
```

This checks the prerequisites and creates `.env` (mode 600) from `.env.example`
with a random API key and Grafana admin password. It never overwrites an existing
`.env`. To configure by hand instead:

```bash
cp .env.example .env && chmod 600 .env
# set LLMFORGE_API_KEYS (e.g. `openssl rand -hex 32`) and GRAFANA_ADMIN_PASSWORD
```

Compose refuses to start if either secret is empty.

## 2. Start the stack

```bash
docker compose up -d --build
docker compose ps
```

Start-up order is enforced with health checks: `ollama` (healthy) → `llmforge-api`
→ `prometheus` (healthy) → `grafana`. The API **does not download a model**, so it
starts in seconds. At this point `/health` returns 200 and `/ready` returns 503
(`model_available: false`).

## 3. Pull the model (one-time)

```bash
./scripts/download-model.sh               # LLMFORGE_DEFAULT_MODEL, default llama3.2:1b
./scripts/download-model.sh qwen2.5:0.5b  # any other model
```

Models are stored in the `llmforge-ollama-models` volume and survive restarts. A model
must also be listed in `LLMFORGE_ALLOWED_MODELS` (or be the default) before clients can use it.

## 4. Verify

```bash
./scripts/smoke-test.sh
.venv/bin/pytest -m integration   # optional, needs the dev venv
```

## URLs

| Service | URL | Auth |
|---------|-----|------|
| API | http://127.0.0.1:8000 | `X-API-Key` for `/v1/*` |
| Swagger UI | http://127.0.0.1:8000/docs | - |
| Prometheus | http://127.0.0.1:9090 | none (localhost only) |
| Alerts | http://127.0.0.1:9090/alerts | |
| Grafana | http://127.0.0.1:3000 | `admin` / `GRAFANA_ADMIN_PASSWORD` |

Ollama (11434) is reachable only inside the `llmforge-net` network.

## Configuration reference

All gateway settings use the `LLMFORGE_` prefix (see `app/config.py`).

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLMFORGE_API_KEYS` | *(required)* | Comma-separated API keys |
| `LLMFORGE_AUTH_ENABLED` | `true` | Disable only for local experiments |
| `LLMFORGE_DEFAULT_MODEL` | `llama3.2:1b` | Model used when a request names none |
| `LLMFORGE_ALLOWED_MODELS` | *(empty)* | Extra allowlisted models |
| `LLMFORGE_PROVIDER` | `ollama` | Provider from the registry |
| `LLMFORGE_OLLAMA_BASE_URL` | `http://ollama:11434` | Runtime URL |
| `LLMFORGE_OLLAMA_KEEP_ALIVE` | `10m` | How long Ollama keeps the model in memory |
| `LLMFORGE_GENERATION_TIMEOUT_SECONDS` | `120` | Overall generation deadline |
| `LLMFORGE_CONNECT_TIMEOUT_SECONDS` | `5` | TCP connect timeout to the runtime |
| `LLMFORGE_MAX_PROMPT_CHARS` | `8000` | Prompt length limit |
| `LLMFORGE_MAX_REQUEST_BODY_BYTES` | `65536` | Body size limit |
| `LLMFORGE_DEFAULT_MAX_TOKENS` / `LLMFORGE_MAX_OUTPUT_TOKENS` | `256` / `1024` | Output length |
| `LLMFORGE_RATE_LIMIT_ENABLED` | `true` | Token-bucket rate limiting |
| `LLMFORGE_RATE_LIMIT_REQUESTS_PER_MINUTE` / `_BURST` | `30` / `10` | Per-client rate and burst |
| `LLMFORGE_MAX_CONCURRENT_GENERATIONS` | `2` | Generation slots (match `OLLAMA_NUM_PARALLEL`) |
| `LLMFORGE_QUEUE_TIMEOUT_SECONDS` | `10` | Max wait for a slot before 503 |
| `LLMFORGE_MODEL_PROBE_INTERVAL_SECONDS` | `30` | Background availability probe (0 disables it) |
| `LLMFORGE_LOG_LEVEL` / `LLMFORGE_LOG_FORMAT` | `INFO` / `json` | Logging |
| `LLMFORGE_DOCS_ENABLED` | `true` | Swagger/ReDoc/OpenAPI |
| `LLMFORGE_API_PORT`, `PROMETHEUS_PORT`, `GRAFANA_PORT` | `8000`, `9090`, `3000` | Host ports (bound to 127.0.0.1) |
| `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_MEMORY_LIMIT` | `2`, `1`, `6g` | Runtime sizing |

After changing `.env`, apply the change with `docker compose up -d`, which recreates only the affected services.

## Operations

```bash
docker compose ps                          # status and health
docker compose logs -f llmforge-api        # JSON logs
docker compose logs llmforge-api | grep '"request_id": "<id>"'
docker compose restart llmforge-api        # restart one service
docker compose stop                        # stop, keep containers and volumes
docker compose start
docker compose down                        # remove containers + network, KEEP volumes
docker compose up -d                       # back up; model and dashboards still there
docker compose down -v                     # DESTROY volumes (model, metrics, Grafana DB)
docker compose exec ollama ollama list     # models in the runtime
docker compose exec ollama ollama rm <m>   # delete a model
```

### Upgrading images

Images are pinned as `name:tag@sha256:digest`. To upgrade, pull the new tag,
read its digest (`docker inspect --format '{{index .RepoDigests 0}}' <image:tag>`),
update both tag and digest in `docker-compose.yml` / `Dockerfile`, then run
`docker compose up -d --build` and `./scripts/smoke-test.sh`.

### Running the API outside Docker (development)

```bash
.venv/bin/pip install -r requirements-dev.txt
LLMFORGE_API_KEYS=dev-key-0123456789abcdef \
LLMFORGE_OLLAMA_BASE_URL=http://127.0.0.1:11434 \
LLMFORGE_LOG_FORMAT=text \
.venv/bin/python -m app
```

## Hardening before any non-local exposure

- Put a TLS-terminating reverse proxy in front of the API, and do not publish `/metrics` through it.
- Keep Prometheus and Grafana on a private network or behind SSO.
- Move secrets into a secret manager (Docker secrets, Vault, cloud KMS).
- Add Alertmanager with real receivers.
- Re-run Trivy on a schedule, because base-image CVEs appear over time.
