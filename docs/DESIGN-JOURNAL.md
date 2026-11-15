# Design journal

The questions I worked through while planning and building this tool: what I was unsure about, the options I weighed, what I checked before deciding, and what I changed my mind about. Final decisions are recorded as ADRs in [`docs/adr/`](adr/); this file is the reasoning that led to them. Terms are defined in [`CONTEXT.md`](../CONTEXT.md).

## How I worked

- **Plan before code.** I wrote a full plan covering architecture, graph model, edge cases, tests, assumptions and limitations, and only started coding after reviewing it.
- **Outside review.** I had the plan reviewed independently twice and talked it through with a software engineer. Every point raised is below, including the ones I rejected and why.
- **Claims tested before they were accepted.** When a review made a technical claim, I ran an experiment in a container before changing the plan. Some claims held up, one did not.
- **One stage at a time.** Each stage ends with lint (ruff), type checks (mypy), tests and a stage-specific command whose real output I read before moving on.
- **AI pair programmer.** I used an AI coding assistant to write code and run experiments. Decisions, scope and every review step were mine.

---

## 1. Where must this run?

**Question.** The tool has to work on the interviewer's machine, not only on mine. I develop on Windows, where `dig` does not exist, and I cannot know the interviewer's OS or what is installed.

**Options.** Install instructions per OS; a pure-Python DNS library instead of `dig`; Docker.

**Decision.** Docker Compose runs both Neo4j and the CLI, with `dig` inside the image. The only requirement is Docker. The assignment requires `dig`, so a DNS library was ruled out ([ADR 0001](adr/0001-shell-out-to-dig.md)).

## 2. Which names get scanned?

**Question.** The assignment says to collect records for "their hostnames". But MX and TXT (SPF, verification tokens) live on the registrable domain: `blog.example.com` almost never has them, `example.com` does.

**Options.** Literal reading (hostname only); hostname plus its apex domain; apex behind a flag.

**Decision.** Scan both and link them with `SUBDOMAIN_OF`. The apex comes from the Public Suffix List, so `example.co.il` is handled correctly. `--no-apex` gives the literal reading ([ADR 0004](adr/0004-scan-apex-domain.md)).

## 3. How deep should takeover detection go?

**Question.** With DNS alone you can see a CNAME whose target returns NXDOMAIN. But for S3, GitHub Pages and Heroku the DNS keeps resolving after the resource is deleted; only the HTTP response (`NoSuchBucket`) shows it is gone.

**Options.** DNS only; always verify over HTTP; DNS by default with opt-in HTTP verification.

**Decision.** DNS by default, `--verify-http` opt-in. Always verifying would send requests to third-party servers the user never asked to contact. DNS only cannot say anything useful about S3 ([ADR 0003](adr/0003-passive-takeover-detection.md)).

## 4. The interviewer changed the takeover requirement

**What happened.** After the assignment, the interviewer wrote that instead of building the takeover query I should learn what subdomain takeover is and how it happens, especially with S3 buckets.

**What I weighed.** Implementing the query is not hard: the CNAME chain and lookup statuses are collected anyway for the shared-infrastructure query. The risk is the quality of the results: without HTTP verification S3 produces noise, there is no real vulnerable domain I am allowed to test against, and a query that returns a wrong result in a demo hurts more than a missing one.

**Decision.** The required deliverable is an explanation chapter in the README on how takeover happens and why S3 is the hard case. The query and HTTP verification are optional stages after the submission point, built only if time remains.

## 5. Which additional query?

**Options.** Mail security (MX without SPF, permissive SPF); third-party providers; infrastructure concentration; other dangling records.

**Decision.** Third-party providers: which external services each domain depends on, from TXT verification tokens, SPF includes, MX hosts and CNAME targets. It maps the organisation's supply chain and reuses the provider catalogue that takeover detection also needs.

## 6. What happens when I scan again?

**First decision.** Keep only the latest state, no history: history adds a time dimension to every node and query, beyond the assignment's scope.

**Challenged by** a software engineer I consulted: DNS changes from day to day. Should the database persist at all, or be flushed when containers stop?

**What I found.** Re-scanning a domain already replaced its old records, so changed DNS was handled. The real problem was different: domains from an earlier file stayed in the graph, so a query after scanning `list2.txt` could mix in `list1.txt` from another day. Flushing on shutdown would not help either: `ingest` and `query` are separate commands that must share data, and a Docker restart would silently empty the graph.

**Decision.** Keep the volume. Each `ingest` replaces the whole graph by default; `--append` builds one graph from several files. The graph records which file was scanned and when, and queries show it ([ADR 0002](adr/0002-latest-state-graph.md)).

## 7. First review: what held up when tested

An independent review raised eight points. I ran `dig 9.20` in the same base image the tool uses before accepting any of them.

| Claim | Verdict | Evidence |
|---|---|---|
| Five `dig` processes per name will choke the CPU | Fix adopted, warning overstated | One process for all five types took 28 ms against 118 ms for five. A process spawn costs about 7 ms, so 5,000 spawns is tens of seconds, not "long minutes". |
| Use fixed `dig` flags, not the default human output | Adopted, with a correction | `+noall +comments +answer` works. `+yaml` also works, but not with the older `dig` on macOS, which would break running without Docker. |
| CNAME targets outside the input file have no status | Valid, different fix | One A query already returns the whole chain. Every link becomes a node with its own status; no extra lookups needed. |
| SPF handling is split between two modules | Valid | Collection keeps raw TXT; enrichment classifies SPF. |
| `docker compose up -d` returns before Neo4j is ready, so the CLI races it | **Rejected** | Tested with a service that takes 15 s to become healthy: `docker compose run` waited 18 s before starting. `depends_on: service_healthy` already prevents the race. |
| Delete only outgoing relationships, then clean orphan nodes | Valid | Added to the graph model. |
| Fixed structure for the provider catalogue | Adopted | Includes a `shared_platform` flag so a shared CDN IP is not reported as a real link between domains. |
| Progress bar | Adopted | Written to stderr so JSON output stays clean. |

**Found by the experiments, missed by the review:**

1. In a batched `dig` call each answer block has no question, so an empty AAAA block looks identical to an empty CNAME block. Adding `+question` labels every block.
2. A timeout inside a batched call prints a line with no name or type. Any question with no answer block is now marked `TIMEOUT`.
3. Docker Desktop's resolver returned every record twice. Records are deduplicated.

## 8. Which DNS resolver?

**Question.** Results depend on the resolver. Some ISPs answer NXDOMAIN with an ad page, which would hide exactly the dangling records the takeover analysis looks for. Results should also be reproducible on the interviewer's machine.

**Decision.** Default to `1.1.1.1`. A second review added an important detail: check reachability **once** at the start of the run, not per domain. Otherwise, on a network that blocks outbound DNS, every lookup waits for a timeout twice. If `1.1.1.1` is unreachable the tool falls back to the system resolver and says so. A resolver given explicitly with `--resolver` is never silently replaced: if it does not answer, the tool stops with an error.

## 9. Two days: what to cut

**Constraint.** Two full working days.

**What I expected to take the time.** Not writing code. Reading and understanding each stage well enough to defend it; real-world DNS surprises (several already appeared in the experiments above); and the README findings.

**Decision.** A submission point after the core stages: tool, shared infrastructure, providers, README. Takeover and HTTP verification come after it. Test coverage is measured and reported, but does not fail CI.

## 10. pip or uv?

**Question.** The same engineer pointed out that uv has largely replaced pip for Python projects.

**What it exposed.** The plan said "pinned versions", but `pyproject.toml` only had ranges such as `typer>=0.15,<1`. An image built next month could have installed different versions from mine.

**Decision.** uv with `uv.lock`, which pins every dependency, including transitive ones. Docker and CI install with `--frozen`, so a mismatch fails the build instead of silently installing other versions. The interviewer still needs only Docker. `pyproject.toml` stays standard, so `pip install .` also works.

## 11. Reading the input file

Smaller decisions made while building the parser, each covered by a test:

- **Lines are read as bytes**, so one line that is not valid UTF-8 is rejected on its own instead of failing the whole file.
- **Private public suffixes count as suffixes.** `user.github.io` and `bucket.s3.amazonaws.com` are their own Apex Domains. Otherwise the tool would scan `github.io` and `s3.amazonaws.com` as if the user owned them, and every GitHub Pages site in a file would look like shared infrastructure.
- **The suffix list never comes from the network.** The snapshot bundled with `tldextract` is used, so results do not depend on a download at runtime.
- **Underscores are allowed in labels.** They are invalid in strict hostnames but common in real DNS names, and querying them is harmless.
- **Lines starting with `-`, `+` or `@` are rejected**, even when a valid name could be extracted from them, because `dig` reads such arguments as options or as the server to query.
- **A numeric last label is rejected** (`999.1.1.1`): it is a malformed IP address, not a hostname.
- **A `parse` command was added.** It was not in the original plan. It shows how a file is read, which lines were rejected and why, without touching DNS or Neo4j. I built it to verify this stage, and kept it because a user preparing an input file needs the same view.

## 12. Showing the graph, not only tables

**Suggestion I received.** Neo4j ships a browser UI, so the interviewer should see the graph itself, not only terminal tables: add a flag that prints a ready-to-paste Cypher query returning nodes and relationships, add `--open-browser`, and put a screenshot in the README.

**What I changed.** Adopted the flag (`--cypher`) and the screenshot. Dropped `--open-browser`: the tool runs in a container and cannot open a browser on the host. And one risk the suggestion did not mention: two versions of each query can drift apart, so the table says one thing and the picture another. Both versions are therefore built from the same `MATCH` clause, and an integration test checks that they return the same domains.

## 13. What dig really prints: two labs before writing the parser

**Question.** The whole graph depends on parsing `dig` correctly, and one assumption was still unverified: that a dangling CNAME comes back as the CNAME together with NXDOMAIN. I had no real dangling record I was allowed to test against.

**What I did.** Built two throwaway labs in a container and recorded the output of the exact command the tool runs. The recordings are now the parser's test fixtures ([`tests/fixtures/dig`](../tests/fixtures/dig/README.md)).

1. BIND 9.20, authoritative for a test zone with a plain name, CNAME chains, a CNAME loop, a null MX and awkward TXT records.
2. Unbound, a real recursive resolver, in front of two separate authoritative servers, so that a CNAME crossing from one zone to the other has to be chased by the resolver, as on the internet.

**What it confirmed.** A dangling CNAME returns the CNAME and NXDOMAIN for A, AAAA, MX and TXT, while the CNAME lookup itself returns NOERROR. So the name exists and the response code describes the end of the chain. A CNAME to a name that exists but has no address returns NOERROR, so the two cases are distinguishable.

**What it changed.**

- **The parser I had planned was wrong.** BIND prints `; EDNS:` and `; COOKIE:` lines that start with the same character as a question line. Earlier experiments ran through Docker's resolver, which prints none. The parser now tracks which section it is in.
- **Statuses can disagree between the five lookups of one name.** In one recording the A lookup was SERVFAIL while the others answered. The rule: NXDOMAIN wins, because it is about the name and not the record type; otherwise any successful lookup outweighs a failed one.
- **A CNAME loop** comes back as SERVFAIL with both links in the answer. The chain is cut and marked as a loop instead of being reported as dangling.

**What the first live run found: NXDOMAIN is not always NXDOMAIN.** A made-up name under `example.org` came back NOERROR with no records, from 1.1.1.1, 8.8.8.8 and 9.9.9.9 alike. The zone is hosted on Cloudflare with DNSSEC, which answers for nonexistent names with a signed "no data" response (compact denial of existence, RFC 9824) instead of NXDOMAIN. The only trace is an NSEC record carrying the marker type `TYPE128`, visible with `+dnssec +authority`. A made-up name under `python.org`, `azurewebsites.net` or `cloudapp.net` returns a normal NXDOMAIN.

**Decision.** Not handled in this version. It is recorded as a limitation, with the fix known: add `+dnssec +authority` and treat that NSEC marker as NXDOMAIN. It does not affect the shared-infrastructure and provider queries, which do not depend on whether a name exists.

---

## 14. Storing the graph

- **Records hang off the name that owns them.** `www.microsoft.com` has no addresses of its own: they belong to the Akamai name at the end of its CNAME chain. Attaching them to the scanned name would hide the chain, which is exactly what shared-infrastructure and takeover analysis need to see.
- **Scan first, write second.** Neo4j is contacted before scanning, so an unreachable database fails in seconds. But the graph is only replaced after the scan has finished, so a scan that fails half-way leaves the previous graph intact.
- **A chain link keeps its own status if it was scanned itself**, whichever is written first. Otherwise the order of writes would decide what the graph says.
- **Tests get their own Neo4j.** Integration tests empty the database they use. Pointing them at the same Neo4j as the CLI would silently delete the user's graph every time the tests ran, so the test service has a throwaway instance with no volume.

**Checked: the same file twice.** The first real double ingest did not give identical counts: 23 addresses, then 25. The store tests already showed that identical input gives an identical graph, so I scanned the same file three times and compared every name. All were stable except one Akamai CDN name, which returned a different set of IPv6 addresses on every query. The graph was right; the internet changed. Two consequences: re-running a scan is not guaranteed to reproduce the README's numbers exactly, and shared-infrastructure results for CDN-hosted names are a sample of what the CDN hands out, not the full set.

## 15. The query framework and shared-infra

- **A query is data, not code.** Each query is one file with a Cypher body, a table ending and a graph ending, plus its options. The CLI builds a subcommand with its own `--help` and flags from that definition, so adding a query touches no CLI code.
- **What "depends on" means.** An input hostname depends on the addresses it resolves to (also at the end of its CNAME chain), on every name in its chain, and on the mail servers of its apex domain. Apex domains the tool added are not subjects themselves: they are scanned only to read MX and TXT on behalf of the input.
- **Sharing inside one organisation is shown by default.** `www.example.com` and `shop.example.com` on one address is real shared infrastructure, if an obvious one. `--cross-apex` keeps only sharing between different apex domains, which is usually the interesting finding.
- **The table and the picture cannot disagree.** An integration test runs both endings on a known graph and checks that they return the same (asset, hostname) pairs.
- **`--cypher` prints a link, not only a query.** I checked in a browser that Neo4j Browser 5.26 opens `/browser/?cmd=edit&arg=...` with the query already in its editor. After signing in, the user presses Run. A test checks that the link decodes to exactly the printed query.

**Found while checking the link.** The first graph in the browser showed `example.com` and `www.example.com` as different organisations, although `--cross-apex` should have hidden them. Neo4j warned that the `apex` property did not exist: the data had been stored by the previous stage, before that property was added. A fresh `ingest` fixed it. Because `ingest` replaces the graph, an upgrade only needs one re-run, but a graph stored by an older version is not migrated.

## 16. Providers: two kinds of evidence

- **Infrastructure and claims are different facts.** An address in Cloudflare's published ranges, a CNAME into `cloudfront.net` or an MX at `mail.protection.outlook.com` is infrastructure a provider *runs*, and several domains can share it: `(:IP|:Hostname)-[:OPERATED_BY]->(:Provider)`. An SPF include or a verification token is the domain's own *statement* that it uses a provider: `(:Hostname)-[:USES_PROVIDER {evidence, detail}]->(:Provider)`. Keeping them apart lets `shared-infra` name the platform behind a shared asset, and lets `providers` say where each piece of evidence was found.
- **The catalog is data.** `providers.yaml` lists about 35 providers with their CNAME suffixes, MX suffixes, SPF includes, TXT token patterns and published address ranges. It is validated on load: a typo in a category, an invalid regular expression or address range stops the tool with a message naming the entry, before any DNS is queried. I only included patterns I am confident of; a provider missing from the list is simply not reported.
- **Suffixes match on label boundaries.** `evilcloudfront.net` is not `cloudfront.net`. Without this rule, anyone could register a look-alike domain and be reported as a CDN.
- **The scanned name itself is never "operated" by a CNAME provider.** Scanning `octocat.github.io` asks about that site; only names reached through a CNAME are attributed to the provider.

**Found by a test: "orphan" was the wrong rule.** In append mode, when a CNAME to CloudFront disappeared, the CloudFront name stayed in the graph: it still had address records of its own, so it was not an orphan by the old definition (no relationships). The correct rule is reachability: input hostnames and apex domains are the roots, and every other node must be reachable from one of them. Nodes that are not, and the addresses only they led to, are removed.

**Two smaller lessons from this stage.**

- CI failed on a test that passed locally: on GitHub Actions, Typer forces coloured output, and the colour codes split `--min-shared` in the help text. I reproduced it locally by setting the same environment variable, and the test now strips colour codes. The full suite is now also run locally with `GITHUB_ACTIONS=1`.
- A new dependency's type stubs had been released the day before. I pinned an older release: there was no specific risk, but a package that is a day old is where supply-chain attacks start.

**The first live run** reported 13 providers for `github.com` from DNS alone, among them Salesforce, Zendesk, Stripe and Atlassian. All of it is public information, and it shows how much an organisation's supplier list leaks through DNS. The evidence column also showed that long values make a terminal table unreadable, so tables now shorten long values and lists; `--format json` keeps them whole.

## 17. Writing the findings

- **The first scan of the example file found two catalog mistakes that would have made the README wrong.** Fastly's IPv6 addresses were not recognised, so the same servers appeared as "Fastly, shared" over IPv4 and as an unknown platform over IPv6. And Google's MX hosts were not marked as a shared platform, so "eight organisations share a mail server" looked like a finding. I replaced the address ranges with the providers' own published lists (Fastly's API, Cloudflare's lists, GitHub's `meta` endpoint) instead of what I remembered. That also showed my GitHub Pages range was too broad: it covered `raw.githubusercontent.com`, which is not GitHub Pages.
- **Every number in the README was recounted from the graph before it was written down.** Two were wrong in the first draft: the example covers 13 organisations, not 9, and OpenAI tokens appear on 5 of them, not 6.
- **No takeover explanation in the submission.** The interviewer asked me to learn how subdomain takeover happens, not to send a summary of it, so the explanation is not part of the repository.
- **A clean clone found one more problem.** Running the README's commands on a fresh copy worked, but the first command printed `pull access denied for dnsinv` before building: Compose tries Docker Hub first. Harmless, but it looks like an error to someone running the project for the first time. `pull_policy: build` removes it.
- **No graph screenshot from me.** The browser built into my tools is about 800 pixels wide, too small for readable labels. A screenshot taken in a normal browser is better than a poor one.

## 18. Seeing the records, and tables people can read

**What I noticed when I ran the tool myself.** The assignment is about collecting A, AAAA, MX, TXT and CNAME records, yet no command showed them: `ingest` printed a summary, and the records were only in the `--dry-run` JSON. The tables also had no column separators.

**Decision.** A `records` command, separate from the investigation queries, reads the stored graph: an overview with one line per hostname (first value of each type and how many more), `--hostname` for every record of one name with its owner, and `--type` for one type in full. Tables are drawn by rich, which was already installed with Typer, with dashed separators.

**What the first attempts got wrong.**

- Seven columns of real DNS data do not fit a normal terminal. Letting every cell wrap broke hostnames in the middle; cutting every cell with an ellipsis shrank whole columns to nothing. The overview now shows one line per hostname, and full values are one command away.
- DNS text is untrusted. Rich reads `[bold]` as styling and `:smile:` as an emoji, so a TXT record could be silently altered on screen. Both are switched off, and a test checks that such a record prints unchanged.
- My earlier fix for the first-run warning, `pull_policy: build`, made Compose rebuild the image before *every* command. The clean-clone check missed it because I had filtered build lines out of the output. `pull_policy: never` builds once when the image is missing and never asks Docker Hub; after a code change the image is rebuilt with `docker compose build`.

## 19. An extra feature: outage impact

**The idea.** The two required queries describe what a set of domains *is*: what it shares, whom it uses. I wanted one that says what that *means*: if a provider has an outage, what stops working, and for whom. In June 2021 a single Fastly configuration error took a large part of the web offline for about an hour; this query answers ahead of time which of your domains would be part of that.

**What needed thinking, not just code.**

- **Not every link to a provider is a dependency.** A CNAME into a CDN or an address in its ranges means the website goes down with it. An MX at the provider means incoming mail stops. An SPF include means mail sent through it fails. A domain verification token means nothing at all at run time: it only proves the domain was once verified. `providers` lists all four; `outage-impact` deliberately leaves the last one out.
- **Fully or partially.** A hostname with addresses or mail servers at two providers survives an outage of one of them, degraded. The query compares how many of the hostname's addresses and mail servers sit at the provider with how many it has in total, and marks the hostname `(partial)` when it is not all of them.
- **Whose mail servers.** A hostname's mail is handled by the nearest name with MX records: itself, a name it aliases, or its apex domain. Taking the MX records of all of them together would mix two different mail setups.
- **Ranking.** Providers are sorted by how many hostnames would lose their website or incoming mail entirely, so single points of failure come first.

**It also tested the architecture.** The README claims a new query is one file. This one was: the query definition and its tests, with no change to the CLI, the graph or the collection code. `--cypher`, `--format json` and `--help` came with it.

**What the first run found.** Fastly is a single point of failure for 7 of the 20 example hostnames in 4 organisations, Python's website, documentation and package index among them. And no hostname in the sample spreads its website or mail servers over two providers: every dependency found is total.

## Open questions

- **Compact denial of existence.** Nonexistent names in some DNSSEC-signed zones are reported as NOERROR (section 13). The fix is known but not implemented.
