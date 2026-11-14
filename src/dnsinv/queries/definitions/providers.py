"""Third-party providers each input hostname depends on, and the evidence for each."""

from dnsinv.queries.base import Option, Query

# Evidence, from strongest to most circumstantial:
#   cname  a name in the hostname's CNAME chain is run by the provider
#   ip     an address it resolves to is in the provider's published ranges
#   mx     its (or its apex domain's) mail is handled by the provider
#   spf    its (or its apex's) SPF record authorises the provider to send mail
#   txt    its (or its apex's) TXT records hold the provider's verification token
_BODY = """
MATCH (h:Hostname {is_input: true})
CALL (h) {
  MATCH path = (h)-[:CNAME_TO*1..10]->(n:Hostname)-[:OPERATED_BY]->(p:Provider)
  RETURN p, 'cname: ' + n.name AS evidence, h AS found_on, path
  UNION
  MATCH path = (h)-[:CNAME_TO*0..10]->(:Hostname)
               -[:RESOLVES_TO]->(ip:IP)-[:OPERATED_BY]->(p:Provider)
  RETURN p, 'ip: ' + ip.address AS evidence, h AS found_on, path
  UNION
  MATCH path = (h)-[:CNAME_TO|SUBDOMAIN_OF*0..2]->(owner:Hostname)
               -[:MAIL_HANDLED_BY]->(mx:Hostname)-[:OPERATED_BY]->(p:Provider)
  RETURN p, 'mx: ' + mx.name AS evidence, owner AS found_on, path
  UNION
  MATCH path = (h)-[:CNAME_TO|SUBDOMAIN_OF*0..2]->(owner:Hostname)-[u:USES_PROVIDER]->(p:Provider)
  RETURN p, u.evidence + ': ' + u.detail AS evidence, owner AS found_on, path
}
WITH h, p, evidence, found_on, path
WHERE ($provider = '' OR toLower(p.name) CONTAINS toLower($provider))
  AND ($category = '' OR p.category = toLower($category))
WITH h, p, evidence, found_on, path
ORDER BY evidence, found_on.name
WITH h, p,
     collect(DISTINCT evidence) AS evidence,
     collect(DISTINCT found_on.name) AS found_on,
     collect(path) AS paths
"""

QUERY = Query(
    name="providers",
    summary="Third-party providers each hostname depends on, with the evidence.",
    description=(
        "For every input hostname, lists the third-party providers it depends on and why: "
        "a CNAME into the provider's domain, an address in its published ranges, mail "
        "handled by it, an SPF include, or a domain verification token in TXT. Mail, SPF "
        "and TXT evidence usually sits on the apex domain; FOUND_ON shows where. Use "
        "--provider to answer 'who uses X?'. Only providers in the bundled catalog "
        "(src/dnsinv/enrichment/providers.yaml) are recognised."
    ),
    body=_BODY,
    table_return="""
RETURN h.name AS hostname,
       p.name AS provider,
       p.category AS category,
       evidence,
       found_on
ORDER BY hostname, category, provider
""",
    graph_return="""
UNWIND paths AS path
RETURN path
""",
    columns=("hostname", "provider", "category", "evidence", "found_on"),
    options=(
        Option(
            "provider",
            str,
            "",
            "Only this provider (case-insensitive substring of its name, e.g. microsoft).",
        ),
        Option(
            "category",
            str,
            "",
            "Only this category: cdn, cloud, hosting, email or saas.",
        ),
    ),
)
