FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RUFF_CACHE_DIR=/tmp/ruff-cache

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY dev/seed_users.py ./dev/seed_users.py
COPY alembic.ini ./
COPY tests ./tests

RUN python -m pip install --no-cache-dir ".[dev,netup]" \
    && groupadd --gid 10001 ossapp \
    && useradd --uid 10001 --gid ossapp --no-create-home --shell /usr/sbin/nologin ossapp

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
