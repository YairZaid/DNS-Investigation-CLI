"""What stops working, and for whom, if one provider goes down."""

from dnsinv.queries.base import Option, Query

# For every input hostname and every provider, how an outage of that provider affects it:
#
#   website   down     a name in its CNAME chain is run by the provider (all traffic goes
#                      through it), or every address it resolves to is in the provider's ranges
#             partial  only some of its addresses are
#   mail_in   down     every mail server of the nearest name that has MX records (the
#                      hostname itself, a name it aliases, or its apex domain) is the provider's
#             partial  only some are
#   mail_out           its SPF record includes the provider: mail sent through it fails
#
# A verification token in TXT is not a dependency: it proves the domain was once verified
# with the provider, and nothing stops working when that provider is down.
_BODY = """
MATCH (h:Hostname {is_input: true})
CALL (h) {
  MATCH (h)-[:CNAME_TO*0..10]->(n:Hostname)
  WITH collect(DISTINCT n) AS chain
  OPTIONAL MATCH (x:Hostname)-[:RESOLVES_TO]->(ip:IP) WHERE x IN chain
  RETURN chain, collect(DISTINCT ip) AS ips
}
CALL (h) {
  OPTIONAL MATCH route = (h)-[:CNAME_TO|SUBDOMAIN_OF*0..2]->(owner:Hostname)
  WHERE EXISTS { (owner)-[:MAIL_HANDLED_BY]->() }
  WITH owner ORDER BY length(route) LIMIT 1
  OPTIONAL MATCH (owner)-[:MAIL_HANDLED_BY]->(mx:Hostname)
  RETURN collect(DISTINCT mx) AS mail_servers
}
MATCH (p:Provider)
WHERE $provider = '' OR toLower(p.name) CONTAINS toLower($provider)
WITH h, p, ips, mail_servers,
     any(n IN chain WHERE n <> h AND EXISTS { (n)-[:OPERATED_BY]->(p) }) AS via_cname,
     size([ip IN ips WHERE EXISTS { (ip)-[:OPERATED_BY]->(p) }]) AS ips_at_p,
     size([mx IN mail_servers WHERE EXISTS { (mx)-[:OPERATED_BY]->(p) }]) AS mx_at_p,
     EXISTS {
       (h)-[:CNAME_TO|SUBDOMAIN_OF*0..2]->(:Hostname)-[:USES_PROVIDER {evidence: 'spf'}]->(p)
     } AS sends_mail
WITH h, p, sends_mail,
     CASE WHEN via_cname OR (size(ips) > 0 AND ips_at_p = size(ips)) THEN 'down'
          WHEN ips_at_p > 0 THEN 'partial' END AS website,
     CASE WHEN size(mail_servers) > 0 AND mx_at_p = size(mail_servers) THEN 'down'
          WHEN mx_at_p > 0 THEN 'partial' END AS mail_in
WHERE website IS NOT NULL OR mail_in IS NOT NULL OR sends_mail
WITH p, h, website, mail_in, sends_mail
ORDER BY h.name
WITH p,
     collect(h) AS hosts,
     collect(DISTINCT coalesce(h.apex, h.name)) AS apexes,
     size([x IN collect({w: website, m: mail_in}) WHERE x.w = 'down' OR x.m = 'down'])
       AS fully_down,
     [x IN collect({name: h.name, state: website}) WHERE x.state IS NOT NULL
       | x.name + CASE x.state WHEN 'partial' THEN ' (partial)' ELSE '' END] AS website,
     [x IN collect({name: h.name, state: mail_in}) WHERE x.state IS NOT NULL
       | x.name + CASE x.state WHEN 'partial' THEN ' (partial)' ELSE '' END] AS mail_in,
     [x IN collect({name: h.name, sends: sends_mail}) WHERE x.sends | x.name] AS mail_out
"""

# The picture shows, for each affected hostname, the paths that make it depend on the
# provider: the same four kinds of evidence the body uses.
_GRAPH = """
UNWIND hosts AS h
CALL (h, p) {
  MATCH path = (h)-[:CNAME_TO*1..10]->(:Hostname)-[:OPERATED_BY]->(p)
  RETURN path
  UNION
  MATCH path = (h)-[:CNAME_TO*0..10]->(:Hostname)-[:RESOLVES_TO]->(:IP)-[:OPERATED_BY]->(p)
  RETURN path
  UNION
  MATCH path = (h)-[:CNAME_TO|SUBDOMAIN_OF*0..2]->(:Hostname)
               -[:MAIL_HANDLED_BY]->(:Hostname)-[:OPERATED_BY]->(p)
  RETURN path
  UNION
  MATCH path = (h)-[:CNAME_TO|SUBDOMAIN_OF*0..2]->(:Hostname)
               -[:USES_PROVIDER {evidence: 'spf'}]->(p)
  RETURN path
}
RETURN path
"""

QUERY = Query(
    name="outage-impact",
    summary="What stops working, and for whom, if one provider goes down.",
    description=(
        "For every provider the input depends on, lists which hostnames lose their website, "
        "their incoming mail or their outgoing mail if that provider has an outage, and "
        "whether fully or partially: a hostname with addresses or mail servers at several "
        "providers is marked (partial). FULLY_DOWN counts hostnames that lose their website "
        "or incoming mail entirely; providers are ranked by it, so single points of failure "
        "come first. Domain verification tokens are not counted: they are not a runtime "
        "dependency. Use --provider to ask about one provider."
    ),
    body=_BODY,
    table_return="""
RETURN p.name AS provider,
       p.category AS category,
       size(hosts) AS hostnames,
       size(apexes) AS organisations,
       fully_down,
       website,
       mail_in,
       mail_out
ORDER BY fully_down DESC, hostnames DESC, provider
""",
    graph_return=_GRAPH,
    columns=(
        "provider",
        "category",
        "hostnames",
        "organisations",
        "fully_down",
        "website",
        "mail_in",
        "mail_out",
    ),
    options=(
        Option(
            "provider",
            str,
            "",
            "Only this provider (case-insensitive substring of its name, e.g. fastly).",
        ),
    ),
)
