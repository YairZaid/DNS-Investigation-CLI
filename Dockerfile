# syntax=docker/dockerfile:1

# ---- base: Python + dig + uv, shared by every stage -----------------------
FROM python:3.12-slim-trixie AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends bind9-dnsutils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv

# The virtualenv lives outside /app so mounting the source in dev does not hide it.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home --uid 1000 app
WORKDIR /app

# ---- runtime: what the interviewer runs -----------------------------------
FROM base AS runtime

# Dependencies first, from the lockfile, so source edits do not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

USER app
# The project folder is mounted here, so relative paths such as
# examples/urls.txt work exactly as they do on the host.
WORKDIR /work
ENTRYPOINT ["dnsinv"]
CMD ["--help"]

# ---- dev: tests, ruff, mypy; source is mounted over /app ------------------
FROM base AS dev

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project
COPY src ./src
RUN uv sync --frozen

# Keep tool caches out of the mounted source folder.
ENV MYPY_CACHE_DIR=/tmp/.mypy_cache \
    RUFF_CACHE_DIR=/tmp/.ruff_cache \
    COVERAGE_FILE=/tmp/.coverage \
    PYTEST_ADDOPTS="-o cache_dir=/tmp/.pytest_cache"

USER app
CMD ["pytest"]
