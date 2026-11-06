FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    uv

ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin
RUN uv tool install "reasonsforge[openai,forge] @ git+https://github.com/benthomasson/reasonsforge" \
    && uv tool install git+https://github.com/benthomasson/ftl-reasons \
    && chmod -R a+rX /opt/uv-tools \
    && chmod a+rx /usr/local/bin/reasonsforge /usr/local/bin/reasonsforge-mcp \
       /usr/local/bin/reasons /usr/local/bin/reasons-mcp

RUN mkdir -p /workspace /home/forge/workspace
WORKDIR /home/forge/workspace

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
