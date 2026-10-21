# TruLens backend image.
#
# Deliberately no `# syntax=docker/dockerfile:1` line: the built-in frontend is
# enough for this file, and skipping it avoids a Docker Hub round-trip for the
# external frontend image (which fails on restricted networks).
#
# BUILD CONTEXT — this Dockerfile needs BOTH sibling folders, so the context is
# the parent directory (the one containing `backend/` and
# `content-trust-engine-v10/`):
#
#     cd <repository-root>
#     docker build -f backend/Dockerfile -t trulens-api .
#     docker compose -f backend/docker-compose.yml up --build
#
# Two targets:
#   engine (default) — backend + the Applied-AI trust engine baked in
#   lean             — backend only; builds even when the engine folder is absent
#                      (docker build --target lean -f backend/Dockerfile .)


# ---------------------------------------------------------------------------
# lean: backend runtime only (built-in heuristics)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS lean

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# --- backend runtime dependencies (cached layer) ---
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# --- application source (data/caches/pycache are pruned by .dockerignore) ---
COPY backend/app ./app
COPY backend/run.py ./

# Non-root runtime user.
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/storage/uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ---------------------------------------------------------------------------
# engine (default): adds the Applied-AI trust engine
# ---------------------------------------------------------------------------
FROM lean AS engine

USER root

# Engine runtime deps the backend itself does not need (openai, python-dotenv).
# The engine's pydantic / httpx requirements are already satisfied by the lean stage.
COPY backend/requirements-engine.txt ./requirements-engine.txt
RUN pip install --no-cache-dir -r requirements-engine.txt

# The engine is a pip-installable package; its versioned prompts are declared as
# package data, so they land in the installed wheel. --no-deps keeps this stage
# from re-resolving (and possibly downgrading) the lean dependencies.
COPY content-trust-engine-v10/ /opt/content-trust-engine/
RUN pip install --no-cache-dir --no-deps /opt/content-trust-engine/ \
    && python -c "import content_trust; print('content_trust import OK')"

# Keep the source tree readable for the non-root runtime user.
RUN chown -R appuser:appuser /opt/content-trust-engine

USER appuser
