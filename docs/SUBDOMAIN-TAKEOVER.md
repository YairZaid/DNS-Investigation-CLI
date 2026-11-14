# Subdomain takeover, and the S3 case

The assignment asked for a query that finds domains potentially vulnerable to subdomain takeover. The interviewer later asked instead for an explanation of what it is and how it happens, especially with S3 buckets. This document is that explanation, and it shows how the data this tool already collects supports detection. The query itself is an optional extension (see [the end](#building-the-query)).

## What it is

A subdomain takeover happens when an organisation's DNS record points to a resource at a third-party provider that no longer exists, and anyone can create a resource with that name at the provider.

1. A team creates a resource at a provider: a Heroku app, a GitHub Pages site, an S3 bucket.
2. They point a subdomain at it: `shop.example.com  CNAME  example-shop.herokuapp.com`.
3. Later the resource is deleted, **but the DNS record is not.** It is now a *dangling* record.
4. An attacker creates a resource with the same name at the same provider.
5. `shop.example.com` now serves content the attacker controls.

**Why it matters.** The attacker publishes under a domain the organisation's users trust, and can usually obtain a valid TLS certificate for it, because certificate authorities only check control of the content. Cookies scoped to `.example.com` are sent to the attacker's page. CORS rules, Content-Security-Policy allow-lists and OAuth redirect lists that trust `*.example.com` now trust the attacker.

## Two kinds of signal

How a dangling record shows itself depends on the provider.

| Signal | Providers (examples) | How it is seen |
|---|---|---|
| **DNS** | Azure App Service (`azurewebsites.net`), Azure Cloud Services (`cloudapp.net`), Traffic Manager, Elastic Beanstalk | The CNAME target itself stops existing: resolving the chain ends in **NXDOMAIN**. `dig` alone is enough. |
| **HTTP** | Amazon S3, GitHub Pages, Heroku | The CNAME target is a shared endpoint and keeps resolving. Only the HTTP response shows that nothing is behind the name. |

## The S3 case

A static website in S3 is usually set up like this:

```
assets.example.com.  CNAME  assets.example.com.s3-website-us-east-1.amazonaws.com.
```

Three facts make it a classic takeover target:

1. **The bucket name must equal the hostname.** S3 serves the bucket named in the HTTP `Host` header, so the bucket behind `assets.example.com` is called exactly `assets.example.com`.
2. **Bucket names are global.** When the bucket is deleted, its name becomes free, and any AWS account can create a bucket with that name in the region the CNAME points to.
3. **DNS looks perfectly healthy.** `…s3-website-us-east-1.amazonaws.com` is a shared S3 endpoint that resolves whether or not the bucket exists. A DNS-only tool sees nothing wrong.

What reveals it is the HTTP response: S3 answers `404 Not Found` with the error code **`NoSuchBucket`** and the missing bucket's name. In other words, S3 itself says the name is available.

**Prevention.** Delete the DNS record *before* deleting the bucket, never the other way round. Keep an inventory of records that point to cloud resources. Monitor for CNAMEs whose targets return `NoSuchBucket` or NXDOMAIN.

## What this tool already collects

Everything a detector needs is already in the graph:

- **The full CNAME chain, with a status for every link.** A dangling CNAME is stored as a name that exists (NOERROR) whose chain ends at a name that does not (NXDOMAIN). This was checked against a real recursive resolver in a lab before the parser was written ([design journal, section 13](DESIGN-JOURNAL.md#13-what-dig-really-prints-two-labs-before-writing-the-parser)).
- **The provider behind each link** (`OPERATED_BY`), from the catalog.
- **A takeover fingerprint per provider** in [`providers.yaml`](../src/dnsinv/enrichment/providers.yaml): the signal type and, for HTTP providers, the marker. For example, `Amazon S3 → http, NoSuchBucket` and `Microsoft Azure → nxdomain`.

This Cypher, run in Neo4j Browser against the example scan, lists every input hostname with a CNAME chain, where the chain ends, and which providers it passes through:

```cypher
MATCH path = (h:Hostname {is_input: true})-[:CNAME_TO*1..10]->(target:Hostname)
WHERE NOT (target)-[:CNAME_TO]->()
OPTIONAL MATCH (link)-[:OPERATED_BY]->(p:Provider) WHERE link IN nodes(path)
RETURN h.name AS hostname, [n IN nodes(path) | n.name] AS chain,
       target.status AS end_status, collect(DISTINCT p.name) AS providers
```

Two rows from that result show the shape of a takeover-prone record. Here both are healthy:

| hostname | chain | end | providers |
|---|---|---|---|
| `www.rust-lang.org` | → `rust-lang.github.io` | NOERROR | GitHub Pages (HTTP signal) |
| `learn.microsoft.com` | → `learn-public.trafficmanager.net` → … | NOERROR | Microsoft Azure (DNS signal), Akamai |

If `rust-lang.github.io` stopped being claimed, DNS would not change; only a GitHub response "There isn't a GitHub Pages site here" would show it. If the Traffic Manager profile were deleted, the chain would end in NXDOMAIN, and the graph would show it directly.

## Building the query

The plan for a `takeover` query (optional stages 7 and 8, after the submission point):

1. **High confidence:** the chain ends in NXDOMAIN and passes through a provider whose signal is `nxdomain`.
2. **Needs verification:** the chain passes through a provider whose signal is `http` (S3, GitHub Pages, Heroku). DNS cannot decide these.
3. **`--verify-http`, opt-in:** one GET request per candidate, checking the provider's marker. Off by default, so the tool never contacts third-party servers unless asked ([ADR 0003](adr/0003-passive-takeover-detection.md)). The tool only reports; it never tries to claim a resource.

**One known gap to close first.** Some DNSSEC-signed zones (observed on Cloudflare-hosted zones) answer for a non-existent name with NOERROR and no records instead of NXDOMAIN. A dangling target in such a zone would look alive. The fix is known and noted in the design journal (section 13): ask `dig` for `+dnssec +authority` and treat the NSEC marker type as NXDOMAIN.

**What no DNS-based tool can see.** An A record pointing to a cloud IP address the organisation has released (for example an AWS Elastic IP) is also a takeover risk: whoever is assigned that address next receives the traffic. Nothing in DNS distinguishes it from an address still in use.
