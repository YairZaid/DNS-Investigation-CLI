# DNS Investigation CLI

[![CI](https://github.com/YairZaid/DNS-Investigation-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/YairZaid/DNS-Investigation-CLI/actions/workflows/ci.yml)

A command-line tool that reads a file of URLs, collects the A, AAAA, MX, TXT and CNAME records of their hostnames with `dig`, stores them as a graph in Neo4j, and answers investigation questions about that graph:

- **`shared-infra`**: which domains share infrastructure (addresses, CNAME targets, mail servers), and whether that sharing means anything.
- **`providers`**: which third-party providers each domain depends on, with the DNS evidence for each. This is the additional query of my choice.
- **Subdomain takeover**: see [docs/SUBDOMAIN-TAKEOVER.md](docs/SUBDOMAIN-TAKEOVER.md), which explains how it happens, especially with S3 buckets, and shows how the collected data supports detection.

## Quick start

**Requirement: Docker** (with Docker Compose). Nothing else: `dig`, Python and Neo4j all run in containers.

```bash
git clone https://github.com/YairZaid/DNS-Investigation-CLI.git
cd DNS-Investigation-CLI
```

```bash
docker compose run --rm cli doctor
```

The first run builds the image and starts Neo4j, waiting until it is ready (about 30 seconds). `doctor` checks that `dig`, a DNS resolver and Neo4j are all available.

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

Then the investigation queries:

```bash
docker compose run --rm cli query run shared-infra --cross-apex
```

```bash
docker compose run --rm cli query run providers
```

To see a result as a graph, add `--cypher`: it prints a link that opens Neo4j Browser (`http://localhost:7474`, user `neo4j`, password `dnsinv-local`) with the query already in the editor.

```bash
docker compose run --rm cli query run shared-infra --cross-apex --cypher
```

To delete the stored graph: `docker compose down -v`.

## Commands

| Command | What it does |
|---|---|
| `doctor` | Checks `dig`, the resolver and Neo4j. |
| `parse FILE` | Shows how a file is read: hostnames, apex domains, and rejected lines with the reason. No DNS, no Neo4j. |
| `ingest FILE` | Scans every hostname and stores the result. **Replaces the graph**, so queries always describe one file at one time. `--append` combines files; `--dry-run` prints JSON instead of storing; `--resolver IP`, `--workers N`, `--no-apex`. |
| `records` | The stored A, AAAA, MX, TXT and CNAME records: one line per hostname, the first value of each type and how many more. `--hostname NAME` shows every record of one hostname with the name that owns it; `--type MX` shows one type in full; `--inputs-only` leaves out apex domains; `--format json`. |
| `query list` | Lists the investigation queries. |
| `query run NAME` | Runs one. Each query has its own options (`--help`), plus `--format table\|json` and `--cypher`. |

Exit codes: `0` success, `1` bad input (unreadable file, no valid hostnames, invalid option value), `2` something the tool needs is unavailable (`dig`, the resolver, Neo4j). The command-line parser also uses `2` for malformed command lines.

## Findings

From one run of `examples/urls.txt` (20 hostnames of well-known open-source and developer sites, belonging to 13 organisations by apex domain), on 2026-11-14 through 1.1.1.1. DNS changes; a new run can differ slightly.

**1. Most sharing between organisations goes through platforms, not through relationships.** `shared-infra --cross-apex` finds 15 assets shared between different organisations. All 15 are on shared platforms: Google's mail servers (Google Workspace handles mail for 8 of the 13 organisations) and Fastly's CDN. The `PLATFORM` and `SHARED_PLATFORM` columns exist for this reason: without them, "eight organisations share a mail server" reads like a finding.

```
┆ KIND        ┆ ASSET                   ┆ HOSTNAME_COUNT ┆ APEX_COUNT ┆ ... ┆ PLATFORM         ┆ SHARED_PLATFORM ┆
├─────────────┼─────────────────────────┼────────────────┼────────────┼─────┼──────────────────┼─────────────────┤
┆ mail_server ┆ alt1.aspmx.l.google.com ┆ 10             ┆ 8          ┆ ... ┆ Google Workspace ┆ yes             ┆
┆ ip          ┆ 151.101.0.223           ┆ 4              ┆ 2          ┆ ... ┆ Fastly           ┆ yes             ┆
```

**2. One real relationship: `python.org` and `pypi.org`.** The two organisations share the same four Fastly IPv4 and four IPv6 addresses, and `docs.python.org` and `www.python.org` share the CNAME target `dualstack.python.map.fastly.net`. Fastly serves many customers, so the addresses alone prove nothing. But the identical address set on both domains is consistent with what is publicly known: the Python Software Foundation runs both.

**3. Sharing inside one organisation is what you would expect.** Without `--cross-apex`, the only assets not on a shared platform are each organisation's own: GitHub's and Microsoft's per-tenant Microsoft 365 mail servers, `mail.python.org`, and one address shared by `github.com` and `gist.github.com`.

**4. DNS publishes an organisation's supplier list.** `providers` found 22 providers across the 20 hostnames. `github.com` alone reveals 13, among them Salesforce, Zendesk, Stripe, DocuSign and Atlassian, all from SPF includes and domain verification tokens. OpenAI verification tokens appear on 5 of the 13 organisations (12 of the 20 hostnames).

**5. Verification tokens outlive what they verified, or reveal more than mail.** `mozilla.org`, `docker.com` and `npmjs.com` hold Microsoft 365 verification tokens (`MS=ms…`), but their mail goes to Google. The tokens may be left over from a migration, or used for other Microsoft services. Either way, DNS alone shows a relationship the organisation's mail setup does not.

**6. No dangling records in this sample.** Every CNAME chain ends at a name that exists, and no lookup failed. `www.rust-lang.org → rust-lang.github.io` (GitHub Pages) and `learn.microsoft.com → learn-public.trafficmanager.net` (Azure) are the shape a takeover-prone record has; both are healthy. See [docs/SUBDOMAIN-TAKEOVER.md](docs/SUBDOMAIN-TAKEOVER.md).

## Design

```
URLs file ─▶ hostnames ─▶ collection ─▶ enrichment ─▶ graph ─▶ queries ─▶ table / JSON / Cypher
             (parse)      (dig)          (providers)   (Neo4j)
```

| Module | Responsibility |
|---|---|
| `hostnames` | URL → normalised hostname (punycode, no trailing dot, validated), apex domain from the bundled Public Suffix List, rejected lines with reasons. |
| `collection` | One `dig` process per hostname asks all five record types. The parser matches each answer block to its question, follows CNAME chains and gives every link its own status. The resolver is chosen once per run: 1.1.1.1, or the system resolver with a warning if 1.1.1.1 is unreachable. |
| `enrichment` | Matches records against [`providers.yaml`](src/dnsinv/enrichment/providers.yaml): infrastructure a provider operates (CNAME targets, MX hosts, published address ranges) and a domain's own claims (SPF includes, TXT verification tokens). |
| `graph` | Writes each hostname in its own transaction; replaces or appends; removes nodes no longer reachable from an input hostname or apex domain. |
| `queries` | Each query is one file: a Cypher body shared by a table ending and a graph ending, plus its options. The CLI builds a subcommand from it. |
| `cli`, `output` | Commands, exit codes and output: every table goes through one function, so they all look alike. DNS text is printed as-is: rich's markup and emoji codes are switched off, because a TXT record is arbitrary text from the internet. |

**Graph model**

```
(:Hostname)-[:RESOLVES_TO]->(:IP)
(:Hostname)-[:CNAME_TO]->(:Hostname)
(:Hostname)-[:MAIL_HANDLED_BY {priority}]->(:Hostname)
(:Hostname)-[:HAS_TXT]->(:TxtRecord {kind})
(:Hostname)-[:SUBDOMAIN_OF]->(:Hostname)                     apex domain
(:IP|:Hostname)-[:OPERATED_BY]->(:Provider)                  infrastructure
(:Hostname)-[:USES_PROVIDER {evidence, detail}]->(:Provider) the domain's own claims
(:Scan {source, scanned_at, resolver, hostnames})
```

A record belongs to the name that owns it. With CNAMEs that is often not the name that was scanned: the addresses of `www.microsoft.com` belong to the Akamai name at the end of its chain.

**Extending it**

| To add | Change |
|---|---|
| An investigation query | One file in `src/dnsinv/queries/definitions/`. It appears in `query list` and gets its own subcommand. |
| A provider | One entry in `providers.yaml`. No code. |
| A record type (NS, CAA) | The parser in `collection/dig.py` and one write in `graph/store.py`. |

The main decisions are recorded as ADRs in [docs/adr](docs/adr/). The reasoning behind them, the reviews the plan went through, and the experiments that changed it are in the [design journal](docs/DESIGN-JOURNAL.md). Domain terms are defined in [CONTEXT.md](CONTEXT.md).

## Assumptions

- The person running the tool has Docker and internet access, and outbound DNS (port 53) is allowed. If 1.1.1.1 is blocked, the tool falls back to the system resolver and says so.
- The input is a text file with one URL per line. Bare hostnames, missing schemes, ports, paths and credentials are accepted; IP addresses, single-label names and lines that could be read as `dig` options are rejected with a reason.
- "Hostnames" also means their apex domain: MX and TXT records usually live on `example.com`, not on `blog.example.com`, so the apex is scanned too ([ADR 0004](docs/adr/0004-scan-apex-domain.md)). `--no-apex` restores the literal reading.
- A snapshot is enough: the graph holds the latest scan, not history ([ADR 0002](docs/adr/0002-latest-state-graph.md)).
- Input files are in the thousands of lines at most.
- The tool is used to investigate domains one is allowed to look at. It only performs DNS lookups.

## Limitations

- **One point of view.** Answers depend on the resolver and the location. CDNs answer differently from query to query: the same file scanned twice returned different IPv6 addresses for one Akamai name. Findings are a sample, not a census.
- **The provider catalog is partial.** About 35 providers. In the example scan, `www.docker.com` goes through Pantheon and `www.mozilla.org` through `fastly-edge.com`, and neither is recognised. Address ranges were checked against the providers' published lists on 2026-11-14 and will age.
- **Shared IP is not proof of a relationship.** The tool marks known shared platforms, but cannot mark the ones it does not know.
- **NXDOMAIN is not always NXDOMAIN.** Some DNSSEC-signed zones report a non-existent name as NOERROR with no records. This does not affect the two queries, but it matters for takeover detection; the fix is known and documented.
- **No subdomain discovery.** Only names in the file, their apex domains, and names reached through CNAMEs are scanned. No NS, SOA, CAA, DMARC or DKIM records.
- **Wildcard DNS** makes every subdomain look like it exists.
- **No history.** "What changed since last week" is not supported.
- **A graph stored by an older version** of the tool is not migrated; run `ingest` again after upgrading.

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

**`doctor` warns that 1.1.1.1 is unreachable.** The network blocks outbound DNS to public resolvers. The tool uses the system resolver instead, so results may differ from the Findings above.

**`The graph is empty`.** Run `ingest` first; each `docker compose down -v` deletes the graph.

**A code change has no effect.** Docker builds the image once and reuses it. Rebuild it with `docker compose build`.

## Testing

```bash
docker compose --profile dev run --rm test
```

About 360 tests, 97% line coverage. Unit tests never touch the network: `dig` is replaced by recorded output from BIND, Unbound, 1.1.1.1 and Docker's resolver ([tests/fixtures/dig](tests/fixtures/dig/README.md)). Integration tests run against a real Neo4j that has its own throwaway container, so running them never deletes your graph. One test per query checks that the `--cypher` graph shows exactly what the table shows. GitHub Actions runs ruff, mypy, all tests, and builds the image and runs `doctor` on every push.

## Running without Docker

Needs Python 3.12+, `dig` (bind9 utilities) and a Neo4j 5 server.

```bash
pip install .
```

```bash
NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=your-password dnsinv doctor
```

For development, [uv](https://docs.astral.sh/uv/) installs the exact locked versions: `uv sync`. Docker builds the image once and reuses it; after changing the code, rebuild it with `docker compose build`.
