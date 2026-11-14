FROM postgres:17-bookworm AS catalog

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY scripts/catalog_build.py ./scripts/catalog_build.py

FROM python:3.13-slim-bookworm AS bot

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid bot --no-create-home bot \
    && mkdir -p /app/catalog /app/runtime \
    && chown bot:bot /app/runtime

COPY assistant_bot ./assistant_bot

USER bot
ENTRYPOINT ["python", "-m", "assistant_bot"]
