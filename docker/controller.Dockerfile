# syntax=docker/dockerfile:1

ARG UV_VERSION=0.11.28

# 专门用于提供 uv 二进制的构建阶段
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# AgentGuard 的实际运行镜像
FROM debian:bookworm-slim AS controller

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        python3 \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# 从上面的 uv 阶段复制二进制
COPY --from=uv /uv /uvx /usr/local/bin/

RUN useradd \
    --create-home \
    --uid 10001 \
    --shell /bin/bash \
    agentguard

WORKDIR /app

COPY --chown=agentguard:agentguard \
    pyproject.toml uv.lock README.md ./

RUN mkdir -p \
        /app/src \
        /app/tests \
        /app/policies \
        /app/data \
        /app/workspaces \
    && chown -R agentguard:agentguard /app

USER agentguard

RUN uv sync --locked --no-install-project

COPY --chown=agentguard:agentguard src ./src
COPY --chown=agentguard:agentguard tests ./tests

RUN uv sync --locked

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "agentguard.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]