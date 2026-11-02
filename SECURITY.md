# Security Policy

## Status

LLMForge is a **portfolio-grade MVP**. It applies sensible security controls
(listed below) but has **not** undergone a professional security review or
penetration test. Do not expose it to the public internet without additional
hardening (TLS termination, a WAF / reverse proxy, central secret management,
and a review of the threat model for your environment).

## Reporting a vulnerability

Please report vulnerabilities privately via GitHub's
"Report a vulnerability" (Security Advisories) feature on the repository,
rather than in a public issue. Include reproduction steps and affected
versions. You should receive an acknowledgement within a few days.

## Controls implemented

| Area | Control |
|------|---------|
| Authentication | API keys from environment (`LLMFORGE_API_KEYS`), constant-time comparison, min length 16, startup fails if auth is enabled with no keys |
| Input validation | Pydantic v2 schemas (`extra="forbid"`), prompt length limit, model-name pattern, model allowlist, `max_tokens`/`temperature` bounds, request body size limit (413) |
| Abuse protection | Per-client token-bucket rate limiting (429 + `Retry-After`), bounded concurrent generations with queue timeout (503) |
| Timeouts | Connect timeout, per-request inference timeout, overall generation deadline |
| Error handling | Uniform error envelope; stack traces and runtime error bodies stay in server logs |
| Logging | Structured JSON; prompts, completions and API keys are never logged (prompt *length* only) |
| Metrics | Bounded label sets only; no prompts / identities as labels |
| Containers | API runs as UID 10001, read-only root FS, all capabilities dropped, `no-new-privileges`; images pinned by digest |
| Network | Ollama is not published to the host; API / Prometheus / Grafana bind to `127.0.0.1` only; dedicated bridge network |
| Secrets | No secrets in Git; `.env` is git-ignored and generated with mode 600; the API container receives only its own variables |
| Supply chain | Pinned Python dependencies, Trivy image + config scans, Syft SBOM (SPDX & CycloneDX) in CI |

## Known limitations

- `/health`, `/ready` and `/metrics` are unauthenticated by design (probes and
  Prometheus). They are only bound to localhost; protect them at the network
  layer if you expose the API.
- The Ollama upstream image runs as root inside its container (capabilities
  dropped, read-only root FS, not published).
- Rate limiting is in-memory per process; multiple replicas need a shared store.
- API keys are static shared secrets; there is no key rotation workflow, per-key
  quota, or RBAC.
- No TLS: terminate TLS in a reverse proxy before any non-local exposure.
- Prompt-injection and output-safety filtering are out of scope for this MVP.

## Scanning locally

```bash
docker build -t llmforge:0.1.0 .
# Vulnerabilities (fail on fixable criticals, like CI):
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.75.0 \
  image --ignore-unfixed --severity CRITICAL --exit-code 1 llmforge:0.1.0
# Misconfiguration (Dockerfile / compose):
docker run --rm -v "$PWD:/src:ro" aquasec/trivy:0.75.0 config /src
# SBOM:
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock anchore/syft:v1.54.1 \
  docker:llmforge:0.1.0 -o spdx-json > sbom.spdx.json
```
