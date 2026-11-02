# Troubleshooting

Start every investigation with:

```bash
docker compose ps
docker compose logs --tail=100 <service>
curl -s http://127.0.0.1:8000/ready
```

Each API error response includes a `request_id`. Find the matching server log line with
`docker compose logs llmforge-api | grep <request_id>`.

## `docker compose up` fails with "set LLMFORGE_API_KEYS in .env"

`.env` is missing or a required secret is empty. Run `./scripts/setup.sh`.

## API container exits immediately with a validation error

Settings are validated at start-up. Common causes:

- `LLMFORGE_AUTH_ENABLED=true` with an empty `LLMFORGE_API_KEYS`
- an API key shorter than 16 characters
- `LLMFORGE_DEFAULT_MAX_TOKENS` larger than `LLMFORGE_MAX_OUTPUT_TOKENS`
- an unknown `LLMFORGE_PROVIDER`

The log line names the field that failed.

## `/ready` returns 503 with `model_available: false`

The model has not been pulled. Run `./scripts/download-model.sh`. This is expected
right after the first `docker compose up`, and it is why the container health check
uses `/health` and not `/ready`.

## `/ready` returns 503 with `runtime_reachable: false`

Ollama is down or unreachable. Check `docker compose ps ollama` and
`docker compose logs ollama`. Out-of-memory kills show up as restarts. Raise
`OLLAMA_MEMORY_LIMIT` or choose a smaller model.

## Generation returns 504 `runtime_timeout`

CPU inference is slow for long outputs. Options:

- lower `max_tokens` in the request or `LLMFORGE_DEFAULT_MAX_TOKENS`
- raise `LLMFORGE_GENERATION_TIMEOUT_SECONDS`
- the first request after an idle period also loads the model into memory. Keep it loaded
  longer with `LLMFORGE_OLLAMA_KEEP_ALIVE`

## Generation returns 503 `overloaded`

All `LLMFORGE_MAX_CONCURRENT_GENERATIONS` slots stayed busy for longer than
`LLMFORGE_QUEUE_TIMEOUT_SECONDS`. Retry after the `Retry-After` delay, raise the
queue timeout, or add capacity. Raising `OLLAMA_NUM_PARALLEL` together with the slot
count helps only if the host has spare CPU or GPU.

## 429 `rate_limited`

The per-client token bucket is empty. Tune `LLMFORGE_RATE_LIMIT_REQUESTS_PER_MINUTE`
and `LLMFORGE_RATE_LIMIT_BURST`. Clients that share an API key share a bucket.

## 400 `model_not_allowed`

Add the model to `LLMFORGE_ALLOWED_MODELS` in `.env`, then run `docker compose up -d`
and pull the model.

## Grafana dashboard panels show "No data"

1. Check that the target is up: http://127.0.0.1:9090/targets should list `llmforge-api` as UP.
2. Latency and throughput panels use `rate()` over short windows. They stay empty
   until generations happen in the selected time range.
3. If the Prometheus datasource is broken, check
   `docker compose logs grafana | grep -i plugin`. Grafana 13 tries to download and
   update plugins at start-up, which fails on a read-only filesystem and unregisters
   the bundled Prometheus plugin. The compose file sets
   `GF_PLUGINS_PREINSTALL_DISABLED=true` to prevent this. Keep that setting if you
   change the Grafana configuration.

## Port already in use

Change `LLMFORGE_API_PORT`, `PROMETHEUS_PORT` or `GRAFANA_PORT` in `.env`. Ollama does
not publish a port, so it does not conflict with an Ollama already running on the host.

## Permission errors on `reports/` after running scanners

Trivy and Syft containers write as root. Give the files back to your user with
`docker run --rm --entrypoint chown -v "$PWD/reports:/reports" aquasec/trivy:0.75.0 -R "$(id -u):$(id -g)" /reports`.

## `python3.12 -m venv` fails with "ensurepip is not available"

On Debian/Ubuntu without `python3.12-venv`:

```bash
python3.12 -m venv --without-pip .venv
python3.12 -m pip --python .venv/bin/python install pip
```

## Start over completely

```bash
docker compose down -v      # deletes the model, metrics and Grafana volumes
docker compose up -d --build && ./scripts/download-model.sh
```
