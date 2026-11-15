# Setup and usage

Everything runs in Docker: `dig`, Python and Neo4j are in containers, so Docker (with Docker Compose) is the only thing to install.

## Start

Extract the ZIP and enter the folder (or `git clone https://github.com/YairZaid/DNS-Investigation-CLI.git` and `cd` into it):

```bash
cd DNS-Investigation-CLI
```

```bash
docker compose run --rm cli doctor
```

The first run builds the image and starts Neo4j, waiting until it is ready (about 30 seconds). `doctor` checks that `dig`, a DNS resolver and Neo4j are all available.

## Scan and look at the records

```bash
docker compose run --rm cli ingest examples/urls.txt
```

What DNS returned for each hostname, one line per hostname:

```bash
docker compose run --rm cli records
```

```
┆ HOSTNAME          ┆ STATUS  ┆ CNAME                       ┆ A                  ┆ AAAA                ┆ MX              ┆ TXT ┆
├───────────────────┼─────────┼─────────────────────────────┼────────────────────┼─────────────────────┼─────────────────┼─────┤
┆ python.org (apex) ┆ NOERROR ┆ -                           ┆ 151.101.0.223 (+3) ┆ 2a04:4e42::223 (+3) ┆ mail.python.org ┆ 13  ┆
┆ www.python.org    ┆ NOERROR ┆ → dualstack.python.map...   ┆ 151.101.0.223 (+3) ┆ 2a04:4e42::223 (+3) ┆ -               ┆ -   ┆
```

Every record of one hostname, or one record type for all of them, in full:

```bash
docker compose run --rm cli records --hostname python.org
```

```bash
docker compose run --rm cli records --type MX
```

## Run the investigation queries

```bash
docker compose run --rm cli query run shared-infra --cross-apex
```

```bash
docker compose run --rm cli query run providers
```

```bash
docker compose run --rm cli query run outage-impact
```

To delete the stored graph: `docker compose down -v`.

## Viewing the graph

Add `--cypher` to any query. It prints a link to `http://localhost:7474/...` that opens Neo4j Browser with the query already in the editor; sign in as `neo4j` with password `dnsinv-local` and press **Run**.

```bash
docker compose run --rm cli query run shared-infra --cross-apex --cypher
```

## Troubleshooting

**`dependency failed to start: container …-neo4j-1 exited`**

Neo4j could not start. The usual cause is that ports 7474 or 7687 are already taken, by Neo4j Desktop, another Neo4j or an old container (`docker ps` shows the containers). To use other ports, copy `.env.example` to `.env` and change them:

```
NEO4J_HTTP_PORT=7475
NEO4J_BOLT_PORT=7688
```

The `--cypher` link follows the new HTTP port. In Neo4j Browser's login form, set the connection URL to `localhost:7688`.

If Neo4j still exits after the ports are free, the container may have been left in a broken state by the failed start. Remove the container (the stored graph is kept in its volume) and start again:

```bash
docker compose rm -f -s neo4j
```

```bash
docker compose run --rm cli doctor
```

`docker compose down -v` starts from nothing: it also deletes the stored graph.

**`doctor` warns that 1.1.1.1 is unreachable.** The network blocks outbound DNS to public resolvers. The tool uses the system resolver instead, so results may differ from the Findings in the README.

**`The graph is empty`.** Run `ingest` first; each `docker compose down -v` deletes the graph.

**A code change has no effect.** Docker builds the image once and reuses it. Rebuild it with `docker compose build`. A graph stored by an older version is not migrated: run `ingest` again.

## Running without Docker

Needs Python 3.12+, `dig` (bind9 utilities) and a Neo4j 5 server.

```bash
pip install .
```

```bash
NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=your-password dnsinv doctor
```

For development, [uv](https://docs.astral.sh/uv/) installs the exact locked versions: `uv sync`.
