# DocHeal — Self-Healing Technical Documentation (GitHub Action image)
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE} AS base

ARG WITH_CHROMA=true
ARG WITH_DEV=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ANONYMIZED_TELEMETRY=False

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/docheal
COPY pyproject.toml README.md ./
COPY docheal ./docheal
RUN pip install . \
 && if [ "$WITH_CHROMA" = "true" ]; then pip install "chromadb>=0.5"; fi \
 && if [ "$WITH_DEV" = "true" ]; then pip install "pytest>=8"; fi

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# GitHub mounts the checked-out repo at /github/workspace.
WORKDIR /github/workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["action"]
