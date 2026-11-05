FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    uv

RUN uv tool install git+https://github.com/benthomasson/reasonsforge \
    && uv tool install git+https://github.com/benthomasson/ftl-reasons

ENV PATH="/root/.local/bin:${PATH}"

RUN mkdir -p /workspace
WORKDIR /workspace

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
