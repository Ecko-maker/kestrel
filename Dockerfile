# syntax=docker/dockerfile:1

# ---- Stage 1: build the web console (React + Vite) ---------------------------------
# Node 24 is the current LTS and matches CI's node-version. Upgrade both together, by hand.
FROM node:24-slim AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build


# ---- Stage 2: the Python app, run by a non-root user -------------------------------
FROM python:3.14-slim AS app

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 kestrel
WORKDIR /app

# Dependencies first (cached until uv.lock changes), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src/ src/
COPY kestrel.mcp.json ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

COPY --from=console /console/dist console/dist
# Only the two sample files; your own workspace is mounted at runtime (docker-compose.yml).
COPY workspace/notes.txt workspace/suspicious_email.txt workspace/
RUN mkdir -p logs && chown -R kestrel:kestrel /app/workspace /app/logs

USER kestrel
ENV PATH="/app/.venv/bin:$PATH" \
    KESTREL_HOST=0.0.0.0 \
    KESTREL_PORT=8000
EXPOSE 8000

# /healthz needs no token and reveals nothing but "up".
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

CMD ["kestrel", "web"]
