"""Domains that share infrastructure."""

from dnsinv.queries.base import Option, Query

# What an Input Hostname depends on:
#   ip            an address it resolves to, directly or at the end of its CNAME chain
#   cname_target  any name in its CNAME chain (a CDN or hosting endpoint)
#   mail_server   an MX host of the name, of its apex domain, or of a name it aliases
# Apex domains the tool added are not subjects themselves: they are scanned only to read
# MX and TXT records on behalf of the input hostnames.
_BODY = """
MATCH (h:Hostname {is_input: true})
CALL (h) {
  MATCH path = (h)-[:CNAME_TO*0..10]->(:Hostname)-[:RESOLVES_TO]->(asset:IP)
  RETURN 'ip' AS kind, asset, path
  UNION
  MATCH path = (h)-[:CNAME_TO*1..10]->(asset:Hostname)
  RETURN 'cname_target' AS kind, asset, path
  UNION
  MATCH path = (h)-[:CNAME_TO|SUBDOMAIN_OF*0..2]->(:Hostname)-[:MAIL_HANDLED_BY]->(asset:Hostname)
  RETURN 'mail_server' AS kind, asset, path
}
WITH kind, asset, h, path
ORDER BY h.name
WITH kind, asset, collect(DISTINCT h) AS hosts, collect(path) AS paths
UNWIND hosts AS host
WITH kind, asset, hosts, paths, coalesce(host.apex, host.name) AS apex
ORDER BY apex
WITH kind, asset, hosts, paths, collect(DISTINCT apex) AS apexes
WHERE size(hosts) >= $min_shared AND (NOT $cross_apex OR size(apexes) >= 2)
"""

QUERY = Query(
    name="shared-infra",
    summary="Assets that two or more hostnames from the input depend on.",
    description=(
        "Lists every IP address, CNAME target and mail server that at least --min-shared "
        "input hostnames depend on, most shared first. A hostname depends on an address it "
        "resolves to (also at the end of its CNAME chain), on every name in its CNAME chain, "
        "and on the mail servers of its apex domain. Sharing inside one organisation "
        "(www.example.com and shop.example.com) is included unless --cross-apex is given. "
        "Note that a shared CDN or cloud address does not prove the domains are related."
    ),
    body=_BODY,
    table_return="""
RETURN kind,
       coalesce(asset.address, asset.name) AS asset,
       size(hosts) AS hostname_count,
       size(apexes) AS apex_count,
       [x IN hosts | x.name] AS hostnames,
       apexes AS apex_domains
ORDER BY hostname_count DESC, apex_count DESC, kind, asset
""",
    graph_return="""
UNWIND paths AS path
RETURN path
""",
    columns=("kind", "asset", "hostname_count", "apex_count", "hostnames", "apex_domains"),
    options=(
        Option(
            "min_shared",
            int,
            2,
            "Report an asset only when at least this many hostnames depend on it.",
            minimum=2,
        ),
        Option(
            "cross_apex",
            bool,
            False,
            "Only report assets shared by hostnames of different apex domains.",
        ),
    ),
)
