FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HEALTH_HOST=0.0.0.0 \
    HEALTH_PORT=8080

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

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD python -c "import os,urllib.request as u;p=os.getenv('HEALTH_PORT','8080');r=u.urlopen(f'http://127.0.0.1:{p}/health',timeout=3);c=r.getcode();r.close();raise SystemExit(0 if c==200 else 1)"

CMD ["python", "-m", "squire_core.runtime"]
