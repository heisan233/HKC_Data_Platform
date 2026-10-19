FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HKC_DATA_ROOT=/data

WORKDIR /opt/hkc-platform

RUN useradd --create-home --uid 1000 appuser

COPY pyproject.toml ./
COPY app ./app

RUN python -m pip install --no-cache-dir . \
    && mkdir -p /data \
    && chown appuser:appuser /data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/dashboard/', timeout=3)"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
