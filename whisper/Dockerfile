# Memoria whisper sidecar — faster-whisper on CPU. Build context: ./whisper
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/models \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Bake the model into the image so cold starts don't hit Hugging Face.
ARG WHISPER_MODEL=small.en
ENV WHISPER_MODEL=${WHISPER_MODEL}
RUN python -c "import os; from faster_whisper import download_model; download_model(os.environ['WHISPER_MODEL'])"

COPY app.py ./

RUN useradd --create-home appuser && chown -R appuser /models
USER appuser

EXPOSE 9000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/healthz', timeout=4)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
