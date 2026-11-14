# syntax=docker/dockerfile:1

# ---- base: Python + dig, shared by every stage ----------------------------
FROM python:3.12-slim-trixie AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends bind9-dnsutils \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 app
WORKDIR /app

# ---- runtime: what the interviewer runs -----------------------------------
FROM base AS runtime

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

USER app
# The project folder is mounted here, so relative paths such as
# examples/urls.txt work exactly as they do on the host.
WORKDIR /work
ENTRYPOINT ["dnsinv"]
CMD ["--help"]

# ---- dev: tests, ruff, mypy; source is mounted over /app ------------------
FROM base AS dev

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e ".[dev]"

# Keep tool caches out of the mounted source folder.
ENV MYPY_CACHE_DIR=/tmp/.mypy_cache \
    RUFF_CACHE_DIR=/tmp/.ruff_cache \
    COVERAGE_FILE=/tmp/.coverage \
    PYTEST_ADDOPTS="-o cache_dir=/tmp/.pytest_cache"

USER app
CMD ["pytest"]
