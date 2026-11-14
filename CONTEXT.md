# DNS Investigation CLI

A command-line tool that collects DNS records for the hostnames found in a list of URLs, stores them as a graph, and answers investigation questions about that graph.

## Language

### Names

**Hostname**:
A fully qualified DNS name, normalised to lowercase ASCII (punycode) without a trailing dot. Every DNS name in the graph is a Hostname, whatever role it plays.
_Avoid_: domain, host, FQDN, site

**Input Hostname**:
A Hostname extracted from a URL in the input file.
_Avoid_: target, seed

**Apex Domain**:
The registrable domain of a Hostname according to the Public Suffix List (`blog.example.co.uk` → `example.co.uk`). An Apex Domain is itself a Hostname.
_Avoid_: root domain, base domain, TLD, zone

**Rejected Line**:
A line of the input file from which no Hostname could be extracted, kept with the reason for rejection.

### Collection

**Lookup**:
One `dig` invocation for one Hostname and one record type.
_Avoid_: query (reserved for Investigation Query), request

**Lookup Status**:
The outcome of a Lookup: `NOERROR`, `NXDOMAIN`, `SERVFAIL`, `REFUSED`, `TIMEOUT` or `ERROR`. A `NOERROR` Lookup with no answers is still `NOERROR`.

**DNS Record**:
One answer returned by a Lookup: a record type (A, AAAA, MX, TXT, CNAME) and its value.

**CNAME Chain**:
The ordered sequence of Hostnames reached by following CNAME records from a Hostname until a name with no CNAME is reached.

**Scan**:
One run of the `ingest` command: every Lookup performed for every Hostname in one input file.
_Avoid_: run, crawl, job

### Investigation

**Investigation Query**:
A named, registered question asked of the graph, such as `shared-infra` or `takeover`.
_Avoid_: report, check, search

**Shared Infrastructure**:
An IP address, mail exchange, CNAME target or Provider that two or more Input Hostnames depend on.

**Provider**:
A third-party service a Hostname depends on, identified from DNS evidence.
_Avoid_: vendor, service, SaaS

**Evidence**:
The DNS Record that justifies linking a Hostname to a Provider: an SPF include, a TXT verification token, an MX exchange or a CNAME target.

**Dangling CNAME**:
A CNAME record whose chain ends at a Hostname that no longer resolves or at a Provider resource that no longer exists.

**Fingerprint**:
A catalogue entry describing how to recognise an unclaimed resource at one Provider: the CNAME suffixes that identify the Provider and the signal (a Lookup Status or an HTTP response marker) that shows the resource is gone.

**Takeover Candidate**:
A Hostname with a Dangling CNAME that matches a Fingerprint. Its confidence is `high` (DNS signal observed), `needs-verification` (HTTP signal required but not checked), `confirmed` or `not-vulnerable` (after HTTP verification).
_Avoid_: vulnerable domain, finding
