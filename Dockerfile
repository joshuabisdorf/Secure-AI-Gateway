FROM python:3.13.15-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml ./
COPY app ./app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip \
    && /opt/venv/bin/python -m pip install .

FROM python:3.13.15-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system --gid 10001 sag \
    && useradd --system --uid 10001 --gid sag --home-dir /home/sag --create-home sag

COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY db ./db
COPY config/security-policies.example.json ./config/security-policies.example.json
COPY docker/entrypoint.sh /usr/local/bin/sag-entrypoint

RUN chmod 0555 /usr/local/bin/sag-entrypoint \
    && chown -R sag:sag /app

USER sag:sag

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()" || exit 1

ENTRYPOINT ["sag-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
