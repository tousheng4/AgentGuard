FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 sandbox \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --create-home \
        --shell /bin/sh \
        sandbox \
    && mkdir -p /workspace \
    && chown sandbox:sandbox /workspace

USER 10001:10001

WORKDIR /workspace

CMD ["python3", "-c", "print('sandbox ready')"]