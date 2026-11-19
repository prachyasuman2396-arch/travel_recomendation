# syntax=docker/dockerfile:1.7
# Multi-stage: build wheels with compilers in a throwaway stage, ship only the venv on a slim base.

# ---------------------------------------------------------------- build
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt \
    && find "$VIRTUAL_ENV" -name "__pycache__" -type d -prune -exec rm -rf {} + \
    && find "$VIRTUAL_ENV" -name "tests" -path "*/site-packages/*" -type d -prune -exec rm -rf {} +

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    WEB_CONCURRENCY=2

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts

USER app:app
EXPOSE 8000

# Liveness only (no DB dependency) so a database blip never gets the container killed.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

# Behind a proxy set FORWARDED_ALLOW_IPS to its address/CIDR (uvicorn reads it); never "*" (spoofable client IPs).
# NOTE: the rate limiter and caches are per process. With WEB_CONCURRENCY>1 each worker has its own budget;
# move them to Redis (see the deployment checklist) before scaling out.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --workers ${WEB_CONCURRENCY} --timeout-graceful-shutdown 20"]
