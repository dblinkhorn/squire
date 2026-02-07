FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 squire

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config /app/config
COPY config.yaml.example /app/config.yaml.example

RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN mkdir -p /data/archive \
    && chown -R squire:squire /app /data

USER squire

CMD ["python", "-m", "squire_core.discord_bot"]
