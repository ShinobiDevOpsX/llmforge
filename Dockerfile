# syntax=docker/dockerfile:1.7
#
# LLMForge API image.
# - multi-stage: build tooling never reaches the runtime image
# - runs as an unprivileged, fixed UID (10001)
# - compatible with a read-only root filesystem
#
# Base image is pinned by tag *and* digest; bump both together.
ARG PYTHON_IMAGE=python:3.12.16-slim-trixie@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN pip install --prefix=/install --only-binary=:all: -r requirements.txt

FROM ${PYTHON_IMAGE} AS runtime
ARG VERSION=0.1.0
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="llmforge" \
      org.opencontainers.image.description="LLMOps gateway for locally hosted language models" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

RUN groupadd --system --gid 10001 llmforge \
 && useradd --system --uid 10001 --gid llmforge --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin llmforge \
 # pip/setuptools are not needed at runtime; removing them shrinks the attack surface
 && pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true

COPY --from=builder /install /usr/local
WORKDIR /srv
COPY --chown=root:root app ./app

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "app"]
