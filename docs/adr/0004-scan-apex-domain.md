# Scan the Apex Domain of every Input Hostname

The assignment asks for records of the hostnames in the URLs, but MX and TXT records usually live on the Apex Domain, not on a subdomain. For every Input Hostname the tool therefore also scans its Apex Domain and links the two with `SUBDOMAIN_OF`. Without this, the mail and Provider queries return almost nothing for inputs such as `blog.example.com`.

## Consequences

- The Apex Domain is derived from a Public Suffix List snapshot bundled with the dependency, so no network fetch is needed at runtime. A very new public suffix may be misclassified until the dependency is updated.
- The number of Lookups roughly doubles for inputs that are subdomains.
- `--no-apex` restores the literal reading of the assignment.
