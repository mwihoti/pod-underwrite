FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY podx ./podx
COPY templates ./templates
COPY static ./static
COPY scripts ./scripts

RUN pip install --quiet --upgrade pip && pip install --quiet .

RUN mkdir -p /data/cache/vectors /data/cache/tasks
ENV DB_PATH=/data/podx.sqlite \
    CACHE_DIR=/data/cache

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "podx.web:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
