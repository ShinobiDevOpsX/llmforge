# LLMForge v0.1.0 Implementation Report

**Date:** 2026-11-01. **Scope:** design, implementation and local verification of the
Docker-based LLMOps MVP. Every result below comes from commands that were actually run
on the host described next. Nothing is simulated.

## 1. Environment

| Item | Value |
|------|-------|
| OS | Ubuntu 26.04.1 LTS, kernel 7.0.0-42-generic, x86_64 |
| CPU / RAM / disk | 8 vCPU, 14 GiB RAM (+4 GiB swap), 29 GiB free at start |
| GPU | None (no `nvidia-smi`). **All inference ran on CPU.** |
| Python | 3.12.3 (system) for the project venv; the system default is 3.14.4 |
| Docker | Engine 28.3.3, Compose v2.39.1 |
| Pre-existing services | A host Ollama on 127.0.0.1:11434. No conflict, because the compose Ollama publishes no port |

Environment note: `python3.12-venv` (ensurepip) is not installed. The system was left
unchanged. The venv was created with `python3.12 -m venv --without-pip` and seeded with
`python3.12 -m pip --python .venv/bin/python install pip`. CONTRIBUTING.md and the
Jenkinsfile handle this case.

## 2. What was built

| Area | Delivered |
|------|-----------|
| Gateway | FastAPI app factory. Endpoints `/health`, `/ready`, `/metrics`, `/v1/models`, `/v1/generate`, `/docs`, `/redoc`, `/openapi.json` |
| Provider layer | `LLMProvider` ABC, `OllamaProvider` (HTTPX), registry with `register_provider()` |
| Controls | API-key auth (header or bearer, constant-time comparison, fail-fast config), token-bucket rate limiting, generation slots with queue timeout, connect/inference/deadline timeouts, model allowlist, prompt and `max_tokens` limits, body size limit, uniform sanitized errors, correlation and trace IDs, JSON logs without prompts |
| Metrics | 11 metrics: the 7 required plus inference latency, active generations, rate-limited count and build info. Bounded labels and zero-initialized series |
| Monitoring | Prometheus config, 4 alert rules, provisioned Grafana datasource and 15-panel dashboard |
| Deployment | Multi-stage non-root Dockerfile and a 4-service compose stack: health-checked start-up order, named volumes, dedicated bridge network, localhost-only ports, read-only root filesystems, `cap_drop: ALL`, `no-new-privileges`, resource limits, rotated logs, digest-pinned images |
| Scripts | `setup.sh` (prereqs + secret generation), `download-model.sh`, `smoke-test.sh` (11 checks) |
| Tests | 87 unit tests and 6 integration tests |
| CI | Jenkinsfile (10 functional stages + an optional quality gate), `sonar-project.properties` |
| Docs | README, architecture, API, deployment, troubleshooting, SECURITY, CONTRIBUTING, this report |

Not created on purpose: `deploy/kubernetes/` (the brief says not to add Kubernetes
to the first MVP) and `deploy/docker/` (the Dockerfile and compose file live at the root,
where tooling expects them). Both are on the roadmap rather than present as empty placeholders.

## 3. Verification results

### 3.1 Static checks and unit tests

```
ruff check .            -> All checks passed!
ruff format --check .   -> 48 files already formatted
pytest --cov            -> 87 passed, 6 deselected in 3.23s
coverage                -> TOTAL 771 stmts, 98% (branch coverage enabled)
```

| File | Tests | Covers |
|------|------:|--------|
| `tests/unit/test_generate.py` | 24 | generate success/defaults/options, null tokens, empty and whitespace prompts, oversized prompt, prompt not echoed, disallowed and malformed model, unknown fields, max_tokens limit, 4 provider-error mappings, deadline timeout, unexpected-error sanitization, 413 body limit, models listing, runtime down |
| `tests/unit/test_security.py` | 21 | missing/invalid key, bearer, multiple keys, auth disabled, auth before validation, config fail-fast (no keys, short keys, token bounds), secret not in repr, unknown provider, 429 + Retry-After, health not limited, limiter disabled, bucket refill, isolation and memory bound, overload 503, queueing, zero-queue mode |
| `tests/unit/test_operations.py` | 14 | health, readiness (ready, implicit `:latest`, model missing, runtime down), request-ID generation/propagation/sanitization, security headers, 404 envelope, docs on/off, provider closed on shutdown |
| `tests/unit/test_metrics.py` | 10 | required metric names exposed, unauthenticated scrape, token/request/inference counters increase, null tokens leave counters unchanged, errors by type, availability gauge, `unmatched` label, active gauge returns to 0, zero-initialized series |
| `tests/unit/test_ollama_provider.py` | 10 | tag parsing, `:latest` normalization, payload mapping, missing token counts, 404 → ModelNotFound, 5xx details hidden, connect error, timeout, invalid JSON, registry |
| `tests/unit/test_logging.py` | 5 | JSON access log with request ID, traceparent trace ID, prompts never logged, API keys never logged, text format |
| `tests/unit/test_model_monitor.py` | 3 | probe loop updates all allowed models, survives unexpected errors, started and stopped by the lifespan |

### 3.2 Container build

- `docker build` succeeded. The image `llmforge:0.1.0` is **157 MB** and runs as `uid=10001(llmforge)`.
- The image contains exactly the 22 pinned runtime packages, with no pip, setuptools or wheel.
- The API was started standalone with `--read-only --cap-drop ALL --security-opt no-new-privileges`, and `/health` returned 200.

### 3.3 Stack bring-up (`docker compose up -d --build`)

`docker compose config --quiet` passed. Final `docker compose ps`:

```
SERVICE        IMAGE                                   STATUS                   PORTS
grafana        grafana/grafana:13.2.5@sha256:9f7a…     Up 8 minutes (healthy)   127.0.0.1:3000->3000/tcp
llmforge-api   llmforge:0.1.0                          Up 5 minutes (healthy)   127.0.0.1:8000->8000/tcp
ollama         ollama/ollama:0.40.12@sha256:20d1…      Up 8 minutes (healthy)   11434/tcp   (not published)
prometheus     prom/prometheus:v3.13.5@sha256:aafa…    Up 8 minutes (healthy)   127.0.0.1:9090->9090/tcp
```

Effective runtime security, from `docker inspect`:

| Container | User | Read-only root FS | Capabilities | no-new-privileges | Host ports |
|-----------|------|-------------------|--------------|-------------------|-----------|
| llmforge-api | 10001:10001 | yes | all dropped | yes | 127.0.0.1:8000 |
| ollama | root (upstream image) | yes | all dropped | yes | none |
| prometheus | nobody | yes | all dropped | yes | 127.0.0.1:9090 |
| grafana | 472 | yes | all dropped | yes | 127.0.0.1:3000 |

Idle memory with the model loaded: API 46 MiB, Prometheus 30 MiB, Grafana 215 MiB, Ollama 1.56 GiB.

### 3.4 Model download and readiness

- Before the pull: `/health` returned 200 and `/ready` returned **503**, with
  `"model_available": false, "detail": "Model 'llama3.2:1b' has not been pulled into the runtime."`
- `./scripts/download-model.sh` pulled `llama3.2:1b` (ID `baf6a787fdff`, 1.3 GB).
- After the pull, `/ready` returned **200** with `{"status":"ready", ... "model_available":true}`.

### 3.5 End-to-end smoke test (`./scripts/smoke-test.sh`)

```
PASS GET /health returns 200
PASS GET /ready returns 200
PASS GET /v1/models without key 401
PASS GET /v1/models with key 200
PASS Empty prompt rejected (422)
PASS Disallowed model rejected (400)
response: {"model":"llama3.2:1b","response":"Kubernetes is an open-source container orchestration
system for automating the deployment, scaling, and management of containerized applications and
services.","prompt_tokens":33,"completion_tokens":28,"duration_seconds":2.726}
PASS POST /v1/generate returns text
PASS Output token metric increased (0 -> 28)
PASS Prometheus target llmforge-api is up
PASS Prometheus alert rules loaded
PASS Grafana healthy
11 passed, 0 failed
```

The smoke test passed again (11/11) after the final image rebuild.

### 3.6 Integration tests (`pytest -m integration`)

```
test_ready_reports_model_available                      PASSED
test_models_lists_default_model                         PASSED
test_generation_returns_completion_and_updates_metrics  PASSED
test_live_validation_and_auth                           PASSED
test_runtime_unavailable_is_reported                    PASSED
test_gateway_reports_not_ready_when_runtime_down        PASSED
6 passed
```

### 3.7 Prometheus

- Targets: `llmforge-api` **up**, `prometheus` **up**.
- All required metrics had data (for example, after early traffic: 59 requests, 220 input tokens, 68 output tokens, availability 1).
- Rules: `LLMForgeAPIUnavailable`, `LLMForgeModelUnavailable`, `LLMForgeHighErrorRate` and `LLMForgeHighInferenceLatency` were all loaded with health `ok` and state `inactive` while the system was healthy.

### 3.8 Grafana

- Version 13.2.5, database `ok`. The `Prometheus` datasource (uid `llmforge-prometheus`) and the **LLMForge Overview** dashboard (folder *LLMForge*) were provisioned automatically.
- Every dashboard query (21 targets across 15 panels) was sent through Grafana's `/api/ds/query` to Prometheus and returned data. The latency quantiles are `NaN` when no generation happened inside the query window, which is the correct result.
- Visual check in a browser: the stat row showed API UP, model UP, 0% errors and 0 active generations. The latency and token-throughput time series drew lines (P50 about 2.5 s, P99 spike about 4 s on CPU).

### 3.9 Failure drill: runtime outage

With the stack running, `docker compose stop ollama`:

| Check | Result |
|-------|--------|
| `POST /v1/generate` | `503 runtime_unavailable` (4 of 4) |
| `/ready` | 503 with `runtime_reachable: false` |
| `llmforge_model_availability` | dropped from 1 to **0** within one probe interval |
| `llmforge_generation_errors_total{error_type="runtime_unavailable"}` | 5 |
| 5xx ratio (after the fix in §4) | 0.56 |
| `LLMForgeModelUnavailable` | **pending** (would fire after its 2-minute `for:`) |
| After `docker compose start ollama` | `/ready` returned 200 and generate returned 200, with no API restart |

### 3.10 Restart persistence (`docker compose down` then `up -d`)

| Data | Before | After |
|------|--------|-------|
| Ollama models | `llama3.2:1b` | `llama3.2:1b` (no re-download), `/ready` 200 |
| Prometheus TSDB | 45 `up` samples, oldest 668 s old | 47 samples, oldest 708 s old (history kept) |
| Grafana DB | user preference set as a marker | marker still present (reset afterwards) |

The three named volumes `llmforge-ollama-models`, `llmforge-prometheus-data` and
`llmforge-grafana-data` survived `down`.

### 3.11 Live rate limiting

13 rapid `GET /v1/models` calls returned `200 ×9, 429 ×4` with `retry-after: 1`.
The burst is 10; one token had already been used by the preceding smoke run.

### 3.12 Logs

After the final restart, a scan of all four containers found **no** warning, error or
critical lines from the API, Prometheus or Ollama, and no error or critical lines from Grafana.

### 3.13 Security scans

**Trivy 0.75.0, image `llmforge:0.1.0`:**

| Severity | Count | Fix available |
|----------|------:|---------------|
| CRITICAL | 0 | - |
| HIGH | 44 | 0 |
| MEDIUM | 60 | 0 |
| LOW | 61 | 0 |
| UNKNOWN | 1 | 0 |

- Every finding is in Debian 13.7 base-image OS packages (util-linux family, ncurses, systemd libs, perl-base, libacl), and **none has an upstream fix** yet.
- There are **0** vulnerabilities in Python packages.
- The CI policy gate (`--ignore-unfixed --severity CRITICAL --exit-code 1`) **passes**.
- Reducing the OS-package count further needs a distroless or minimal base image (roadmap).

**Trivy config:** Dockerfile has **0** misconfigurations.

**Syft 1.54.1 SBOM:** `reports/sbom.spdx.json` and `reports/sbom.cyclonedx.json` were
generated. The final image's SBOM lists 2,812 components, including the 22 pinned Python
distributions (the first scan, before the dependencies were trimmed, had 2,823 components
and 25 Python distributions).

### 3.14 CI pipeline replay

Jenkins was not run. Each Jenkinsfile stage was replayed locally with the same commands,
starting from a fresh venv built from `requirements-dev.txt`:

| Stage | Result |
|-------|--------|
| Python env | `pip check`: no broken requirements |
| Lint | pass |
| Unit tests | 87 passed |
| Coverage gate | 98% (threshold 85%) |
| Docker build | ok, with VERSION and VCS_REF build args |
| Trivy | gate passed, reports written |
| Syft | SBOMs written |
| SonarQube | **not executed**: no SonarQube server is available |

## 4. Problems found during verification and how they were fixed

| # | Symptom | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | `python3.12 -m venv` failed | `python3.12-venv` is not installed on the host | Seeded the venv with system pip (no system changes), and documented it |
| 2 | Grafana `/api/ds/query` returned "Plugin not registered", and the Prometheus datasource was missing from the plugin list | Grafana 13's background plugin installer auto-updates bundled plugins from the internet at start-up. On the read-only root FS it removed the bundled `prometheus` plugin registration and then failed to write the update (`unlinkat … read-only file system`) | Set `GF_PLUGINS_PREINSTALL_DISABLED=true` and `GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false`. Plugins are now fixed by the pinned image, which is also more reproducible |
| 3 | Grafana logged errors about missing `provisioning/plugins` and `alerting` directories | Mounting the whole provisioning folder hid the image's empty sub-directories | Mount only `datasources/` and `dashboards/` |
| 4 | "Error rate" panel was empty | Labeled counters have no series until the first increment, so `rate()` returned nothing | Zero-initialize all bounded label combinations at start-up |
| 5 | During the outage drill the 5xx ratio read **0** although 5 of 8 requests failed | The `status="503"` series first appeared with value 5, and Prometheus cannot see an increase on a series that starts at N. The error-rate alert would miss the first burst of a new error class | Pre-create request series for `/v1` routes × the API's status codes. The drill was re-run and the ratio read 0.56 |
| 6 | Pre-created series were missing in a unit test | FastAPI 0.162 keeps included routers as nested `_IncludedRouter` objects in `app.routes` | Enumerate the public `v1.router.routes` instead of private internals |
| 7 | Trivy command failed with "pull access denied for cv" | The project path contains spaces, and an unquoted `-v $PWD/...` split the argument | Used a quoted shell function, and fixed the same pattern in the Jenkinsfile |
| 8 | Scanner reports were owned by root | Trivy and Syft containers write as root | Added a `chown` step in the Jenkinsfile archive stage and documented it |
| 9 | Unpinned transitive deps, plus dev-only `watchfiles`, `websockets` and `pyyaml` in the image | `uvicorn[standard]` extras, and only direct deps were pinned | Pinned the full runtime tree and replaced the extras with explicit `uvloop` and `httptools`. Image went from 163 to 157 MB |
| 10 | Uvicorn `color_message` (ANSI codes) appeared in JSON logs | Extra attribute from uvicorn's records | Excluded it in the formatter |
| 11 | "Total requests" stat showed 49.5 | `increase()` extrapolates | Wrapped the expression in `round()` |
| 12 | Grafana legacy `/api/datasources/uid/:uid/health` and `/api/dashboards/uid/:uid` returned 404 | Those legacy endpoints are not served in Grafana 13 | Verification uses `/api/search` and `/api/ds/query` |

## 5. Architecture trade-offs

The full discussion is in [architecture.md](architecture.md#key-trade-offs). In summary:

- generation is non-streaming;
- the rate limiter and generation slots live in process memory, with a single uvicorn worker;
- models are pulled explicitly and never at start-up;
- the container health check uses `/health`, and readiness gating uses `/ready`;
- Ollama runs as root because of the upstream image, mitigated by dropped capabilities, a read-only FS and no published port;
- `/metrics` is unauthenticated but bound to localhost;
- there is no Kubernetes or Alertmanager in v0.1.

## 6. Known limitations

1. **CPU-only verification.** No GPU path was tested. Latency figures (about 2.5–4 s for short completions) apply to this host only.
2. **The Jenkinsfile has not run on a Jenkins server.** Its stages were replayed locally. **SonarQube analysis was not executed** because no server is available. Some plugins are needed (Timestamper, Workspace Cleanup, JUnit, SonarQube Scanner).
3. **Alerts are not routed** because no Alertmanager is deployed. `LLMForgeModelUnavailable` was observed in the *pending* state. Firing after its 2-minute `for:` was not waited for. API-down, error-rate and latency alerts were validated as rules (health `ok`) but were not triggered.
4. **Rate limiting and concurrency are per process.** Multiple replicas need a shared store.
5. **Token metrics depend on runtime reporting.** Prompt counts can be missing for cached prompts. There are no container or host resource metrics (Ollama has no metrics endpoint, and cAdvisor and node-exporter are not included).
6. **Base-image CVEs:** 44 HIGH with no fix available today. Re-scan regularly.
7. **Not production-secure as-is.** There is no TLS, no key rotation or RBAC, and no prompt-injection or output filtering. See SECURITY.md.
8. `shellcheck` is not installed on the host. The scripts were syntax-checked (`bash -n`) and exercised end to end, but not linted.
9. Starlette 1.10 shows a deprecation warning for its TestClient using `httpx`. It is harmless today, but a future Starlette release may require `httpx2` for tests.

## 7. Operating commands

```bash
# Start / verify
./scripts/setup.sh
docker compose up -d --build
./scripts/download-model.sh
./scripts/smoke-test.sh

# Stop / restart
docker compose stop
docker compose start
docker compose restart llmforge-api
docker compose down              # keeps volumes (model, metrics, dashboards)
docker compose up -d

# Full cleanup (irreversible: deletes the model and all metrics and Grafana data)
docker compose down -v --rmi local
docker volume rm llmforge-trivy-cache   # created by local Trivy scans, if present
```

## 8. First push to GitHub

The repository was initialized and staged locally, but nothing was committed or pushed.
`.env`, `.venv/`, `reports/` and the caches are git-ignored. After creating an empty
`llmforge` repository on GitHub:

```bash
git commit -m "feat: LLMForge v0.1.0 - LLMOps gateway, observability stack and CI pipeline"
git remote add origin git@github.com:<your-user>/llmforge.git
git push -u origin main
```
