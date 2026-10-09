# Discito app — FastAPI + Jinja2 on uv. Build context: repo root.

# Stage 1: build the Tailwind bundle
FROM node:22-alpine AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY app/static ./app/static
COPY app/templates ./app/templates
RUN npx tailwindcss -i app/static/css/input.css -o /build/styles.css --minify

# Stage 2: runtime
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    APP_ENV=prod \
    DATABASE_URL=sqlite:////data/discito.db

# uv binary from the official image. Pinned minor for reproducible builds.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

WORKDIR /app

# Dependency layer — cached until the lockfile changes. Prod deps only.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code + migrations.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY --from=assets /build/styles.css ./app/static/css/styles.css

# /data holds the SQLite file — mount a persistent volume here.
RUN useradd --create-home appuser && mkdir -p /data && chown appuser /data
USER appuser
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

# Run migrations, then serve. One worker: SQLite has a single writer anyway.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
