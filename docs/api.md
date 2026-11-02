# API Reference

Base URL (default): `http://127.0.0.1:8000`. Interactive docs: `/docs` (Swagger UI),
`/redoc`, and the raw schema at `/openapi.json` (disable with `LLMFORGE_DOCS_ENABLED=false`).

## Authentication

All `/v1/*` endpoints require an API key, sent either as

```
X-API-Key: <key>
```

or

```
Authorization: Bearer <key>
```

Keys come from `LLMFORGE_API_KEYS` (comma-separated, ≥16 characters each).
`/health`, `/ready` and `/metrics` are unauthenticated (for probes and Prometheus).

```bash
export LLMFORGE_KEY="$(grep '^LLMFORGE_API_KEYS=' .env | cut -d= -f2 | cut -d, -f1)"
```

## Common behaviour

- Every response has an `X-Request-ID` header. Send your own (`[A-Za-z0-9._-]{1,64}`)
  to correlate client and server logs; anything else is replaced.
- A W3C `traceparent` header is accepted, and its trace ID is written to the logs.
- `/v1/*` responses set `Cache-Control: no-store`.
- Errors always use this shape:

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Rate limit exceeded.",
    "request_id": "3e07c86a10b8481f8186dfd24b62ac19"
  }
}
```

Validation errors also include `details` (`loc`, `msg`, `type`). The rejected
input is never echoed back.

| HTTP | `code` | Meaning |
|------|--------|---------|
| 400 | `model_not_allowed` | Requested model is not in the allowlist |
| 401 | `unauthorized` | Missing or invalid API key |
| 404 | `not_found` | Unknown route |
| 413 | `payload_too_large` | Body exceeds `LLMFORGE_MAX_REQUEST_BODY_BYTES` |
| 422 | `validation_error` | Schema validation failed (empty prompt, unknown field, bad type…) |
| 422 | `prompt_too_long` | Prompt exceeds `LLMFORGE_MAX_PROMPT_CHARS` |
| 422 | `invalid_parameter` | `max_tokens` exceeds `LLMFORGE_MAX_OUTPUT_TOKENS` |
| 429 | `rate_limited` | Token bucket empty; see `Retry-After` |
| 500 | `internal_error` | Unexpected server error (details only in server logs) |
| 502 | `runtime_error` | The runtime returned an error |
| 503 | `runtime_unavailable` | The runtime is unreachable |
| 503 | `model_not_available` | Model allowed but not pulled into the runtime |
| 503 | `overloaded` | All generation slots busy past the queue timeout; see `Retry-After` |
| 504 | `runtime_timeout` | Generation exceeded `LLMFORGE_GENERATION_TIMEOUT_SECONDS` |

---

## `GET /health`

Liveness. Does not contact the runtime.

```bash
curl -s http://127.0.0.1:8000/health
```
```json
{"status":"ok","version":"0.1.0"}
```

## `GET /ready`

Readiness: returns 200 only if the runtime is reachable **and** the default model is pulled.
Otherwise it returns 503 with the same body shape.

```bash
curl -s http://127.0.0.1:8000/ready
```
```json
{"status":"ready","provider":"ollama","model":"llama3.2:1b","runtime_reachable":true,"model_available":true,"detail":null}
```

Before the model is pulled:

```json
{"status":"not_ready","provider":"ollama","model":"llama3.2:1b","runtime_reachable":true,"model_available":false,"detail":"Model 'llama3.2:1b' has not been pulled into the runtime."}
```

## `GET /v1/models`

Models present in the runtime. `allowed` tells you whether the gateway accepts each one.

```bash
curl -s -H "X-API-Key: $LLMFORGE_KEY" http://127.0.0.1:8000/v1/models
```
```json
{
  "provider": "ollama",
  "default_model": "llama3.2:1b",
  "models": [
    {"name": "llama3.2:1b", "allowed": true, "size_bytes": 1321098329, "family": "llama",
     "parameter_size": "1.2B", "quantization": "Q8_0"}
  ]
}
```

## `POST /v1/generate`

Request body:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `prompt` | string | yes | 1 to `LLMFORGE_MAX_PROMPT_CHARS` characters after trimming whitespace |
| `model` | string | no | Defaults to `LLMFORGE_DEFAULT_MODEL`; must be allowlisted; `[A-Za-z0-9._:/-]{1,128}` |
| `max_tokens` | int ≥ 1 | no | Defaults to `LLMFORGE_DEFAULT_MAX_TOKENS`; ≤ `LLMFORGE_MAX_OUTPUT_TOKENS` |
| `temperature` | float 0–2 | no | Runtime default if omitted |

Unknown fields are rejected (422).

```bash
curl -s http://127.0.0.1:8000/v1/generate \
  -H "X-API-Key: $LLMFORGE_KEY" -H 'Content-Type: application/json' \
  -d '{"prompt": "Explain Kubernetes", "model": "llama3.2:1b", "max_tokens": 64}'
```

Response (real output from a CPU-only run):

```json
{
  "model": "llama3.2:1b",
  "response": "Kubernetes is an open-source container orchestration system for automating the deployment, scaling, and management of containerized applications and services.",
  "prompt_tokens": 33,
  "completion_tokens": 28,
  "duration_seconds": 2.726
}
```

`prompt_tokens` and `completion_tokens` are `null` if the runtime does not report them.
For example, Ollama may omit the prompt count when the prompt is served from its cache.
`duration_seconds` is the time spent waiting on the runtime.

Python example:

```python
import httpx

resp = httpx.post(
    "http://127.0.0.1:8000/v1/generate",
    headers={"X-API-Key": "<key>"},
    json={"prompt": "Write a haiku about SRE", "max_tokens": 48},
    timeout=180,
)
resp.raise_for_status()
print(resp.json()["response"])
```

## `GET /metrics`

Prometheus text format. See the metrics table in the [README](../README.md#observability).
