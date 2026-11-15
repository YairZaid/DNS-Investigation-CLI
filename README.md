# DNS Investigation CLI

[![CI](https://github.com/YairZaid/DNS-Investigation-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/YairZaid/DNS-Investigation-CLI/actions/workflows/ci.yml)

A command-line tool that reads a file of URLs, collects the A, AAAA, MX, TXT and CNAME records of their hostnames with `dig`, stores them as a graph in Neo4j, and answers investigation questions about that graph:

- **`shared-infra`**: which domains share infrastructure (addresses, CNAME targets, mail servers), and whether that sharing means anything.
- **`providers`**: which third-party providers each domain depends on, with the DNS evidence for each. This is the additional query of my choice.
- **`outage-impact`**: what stops working, and for whom, if one provider goes down: websites, incoming mail and outgoing mail, fully or partially. An extra feature built on the same data.

## Quick start

Needs Docker (with Docker Compose). Extract the ZIP, then:

```bash
cd DNS-Investigation-CLI
docker compose run --rm cli doctor
docker compose run --rm cli ingest examples/urls.txt
docker compose run --rm cli query run shared-infra --cross-apex
docker compose run --rm cli query run providers
docker compose run --rm cli query run outage-impact
```

Add `--cypher` to any query to see the result as a graph in Neo4j Browser (user `neo4j`, password `dnsinv-local`).

Setup in full, every command with example output, viewing the graph, troubleshooting and running without Docker: [SETUP.md](SETUP.md).

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

**1. Most sharing between organisations goes through platforms, not through relationships.** `shared-infra --cross-apex` finds 15 assets shared between different organisations. All 15 are on shared platforms: Google's mail servers (Google Workspace handles mail for 8 of the 13 organisations) and Fastly's CDN. The `PLATFORM` and `SHARED_PLATFORM` columns exist for this reason: without them, "eight organisations share a mail server" reads like a finding. Inside one organisation (without `--cross-apex`) the sharing is what you would expect: per-tenant Microsoft 365 mail servers, `mail.python.org`, and one address shared by `github.com` and `gist.github.com`.

```
┆ KIND        ┆ ASSET                   ┆ HOSTNAME_COUNT ┆ APEX_COUNT ┆ ... ┆ PLATFORM         ┆ SHARED_PLATFORM ┆
├─────────────┼─────────────────────────┼────────────────┼────────────┼─────┼──────────────────┼─────────────────┤
┆ mail_server ┆ alt1.aspmx.l.google.com ┆ 10             ┆ 8          ┆ ... ┆ Google Workspace ┆ yes             ┆
┆ ip          ┆ 151.101.0.223           ┆ 4              ┆ 2          ┆ ... ┆ Fastly           ┆ yes             ┆
```

**2. One real relationship: `python.org` and `pypi.org`.** The two organisations share the same four Fastly IPv4 and four IPv6 addresses, and `docs.python.org` and `www.python.org` share the CNAME target `dualstack.python.map.fastly.net`. Fastly serves many customers, so the addresses alone prove nothing. But the identical address set on both domains is consistent with what is publicly known: the Python Software Foundation runs both.

**3. DNS publishes an organisation's supplier list.** `providers` found 22 providers across the 20 hostnames. `github.com` alone reveals 13, among them Salesforce, Zendesk, Stripe, DocuSign and Atlassian, all from SPF includes and domain verification tokens. OpenAI verification tokens appear on 5 of the 13 organisations (12 of the 20 hostnames). Tokens also outlive what they verified: `mozilla.org`, `docker.com` and `npmjs.com` hold Microsoft 365 tokens, but their mail goes to Google.

**4. One provider outage would take down a whole ecosystem.** `outage-impact` ranks providers by how many hostnames would lose their website or incoming mail entirely. If Fastly went down, 7 hostnames in 4 organisations would lose their website, among them `www.python.org`, `docs.python.org` and `pypi.org` at the same moment: Python's documentation and its package index. A Google Workspace outage would stop incoming mail for 10 of the 20 hostnames. And not one hostname spreads its website or mail servers over two providers: every dependency the tool found is a single point of failure.

```bash
docker compose run --rm cli query run outage-impact --provider fastly
```

No dangling CNAME was found in this sample.

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
| An investigation query | One file in `src/dnsinv/queries/definitions/`. It appears in `query list` and gets its own subcommand. `outage-impact` was added this way, after the rest was finished: one new file and its tests, no other code changed. |
| A provider | One entry in `providers.yaml`. No code. |
| A record type (NS, CAA) | The parser in `collection/dig.py` and one write in `graph/store.py`. |

The main decisions are recorded as ADRs in [docs/adr](docs/adr/). The reasoning behind them, the reviews the plan went through, and the experiments that changed it are in the [design journal](docs/DESIGN-JOURNAL.md). Domain terms are defined in [CONTEXT.md](CONTEXT.md).

## Assumptions

- The person running the tool has Docker and internet access, and outbound DNS (port 53) is allowed. If 1.1.1.1 is blocked, the tool falls back to the system resolver and says so.
- The input is a text file with one URL per line. Bare hostnames, missing schemes, ports, paths and credentials are accepted; IP addresses, single-label names and lines that could be read as `dig` options are rejected with a reason.
- "Hostnames" also means their apex domain: MX and TXT records usually live on `example.com`, not on `blog.example.com`, so the apex is scanned too ([ADR 0004](docs/adr/0004-scan-apex-domain.md)). `--no-apex` restores the literal reading.
- A snapshot is enough: the graph holds the latest scan, not history ([ADR 0002](docs/adr/0002-latest-state-graph.md)).
- Input files are in the thousands of lines at most, and list domains one is allowed to investigate. The tool only performs DNS lookups.

## Limitations

- **One point of view.** Answers depend on the resolver and the location. CDNs answer differently from query to query: the same file scanned twice returned different IPv6 addresses for one Akamai name. Findings are a sample, not a census.
- **The provider catalog is partial.** About 35 providers. In the example scan, `www.docker.com` goes through Pantheon and `www.mozilla.org` through `fastly-edge.com`, and neither is recognised. Address ranges were checked against the providers' published lists on 2026-11-14 and will age.
- **Shared IP is not proof of a relationship.** The tool marks known shared platforms, but cannot mark the ones it does not know.
- **Outage impact sees only what DNS shows.** A provider missing from the catalog does not appear. DNS hosting itself (NS records) is not collected, although a DNS provider outage takes everything down. "Partial" counts addresses and mail servers; it does not know how traffic is actually split between them.
- **NXDOMAIN is not always NXDOMAIN.** Some DNSSEC-signed zones report a non-existent name as NOERROR with no records. This does not affect the two queries, but it matters for takeover detection; the fix is known and documented.
- **No subdomain discovery, and only five record types.** Only names in the file, their apex domains, and names reached through CNAMEs are scanned; no NS, SOA, CAA, DMARC or DKIM. Wildcard DNS makes every subdomain look like it exists.

## Testing

```bash
docker compose --profile dev run --rm test
```

About 380 tests, 97% line coverage. Unit tests never touch the network: `dig` is replaced by recorded output from BIND, Unbound, 1.1.1.1 and Docker's resolver ([tests/fixtures/dig](tests/fixtures/dig/README.md)). Integration tests run against a real Neo4j that has its own throwaway container, so running them never deletes your graph. One test per query checks that the `--cypher` graph shows exactly what the table shows. GitHub Actions runs ruff, mypy, all tests, and builds the image and runs `doctor` on every push.
