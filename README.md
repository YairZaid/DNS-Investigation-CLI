# DNS Investigation CLI

Collects A, AAAA, MX, TXT and CNAME records with `dig` for the hostnames in a list of URLs, stores them as a graph in Neo4j, and answers investigation queries about that graph.

> Work in progress. The full README (design, findings, assumptions, limitations) comes with the finished version.

## Requirements

Docker with Docker Compose. Nothing else: `dig`, Python and Neo4j all run in containers.

## Quick start

Check that everything is in place:

```bash
docker compose run --rm cli doctor
```

The first run builds the image and starts Neo4j; the command waits until Neo4j is ready. The Neo4j browser is then available at http://localhost:7474 (user `neo4j`, password `dnsinv-local`).

## Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/) and pinned in `uv.lock`, so every build installs exactly the same versions. Inside Docker uv is already installed; you only need it on the host for an IDE virtualenv (`uv sync`).

```bash
docker compose --profile dev run --rm test
```

Runs the unit and integration tests against a real Neo4j. Lint and type checks:

```bash
docker compose --profile dev run --rm test ruff check
```

```bash
docker compose --profile dev run --rm test mypy
```
