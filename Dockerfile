FROM python:3.13-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml ./
RUN uv sync --no-install-project --no-dev

COPY . .
RUN uv sync --no-dev
ENV PATH="/app/.venv/bin:$PATH"

ENV LORA_BRIDGE_CONFIG=/config/config.yaml \
    LORA_BRIDGE_DB=/data/lora_bridge.sqlite \
    LORA_BRIDGE_LOG=INFO

ENTRYPOINT ["tini", "--"]
CMD ["lora-bridge"]
