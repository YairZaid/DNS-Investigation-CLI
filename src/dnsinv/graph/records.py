"""What DNS returned for each scanned hostname, read back from the graph.

Records are followed through the CNAME chain, the way a resolver answers: the A records of
www.example.com are the ones owned by the end of its chain. Each record keeps its owner, so
the detail view can still say which name in the chain it belongs to.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Protocol

RECORD_TYPES = ("CNAME", "A", "AAAA", "MX", "TXT")

_LOAD = """
MATCH (h:Hostname)
WHERE h.scanned_at IS NOT NULL
  AND ($hostname IS NULL OR h.name = $hostname)
  AND ($include_apex OR coalesce(h.is_input, false))
OPTIONAL MATCH p = (h)-[:CNAME_TO*1..10]->(end:Hostname)
WHERE NOT (end)-[:CNAME_TO]->()
WITH h, CASE WHEN p IS NULL THEN [h] ELSE nodes(p) END AS chain
UNWIND chain AS n
CALL (n) {
  OPTIONAL MATCH (n)-[:RESOLVES_TO]->(ip:IP)
  RETURN collect(DISTINCT {address: ip.address, version: ip.version}) AS ips
}
CALL (n) {
  OPTIONAL MATCH (n)-[m:MAIL_HANDLED_BY]->(mx:Hostname)
  RETURN collect(DISTINCT {priority: m.priority, host: mx.name}) AS mxs
}
CALL (n) {
  OPTIONAL MATCH (n)-[:HAS_TXT]->(t:TxtRecord)
  RETURN collect(DISTINCT t.value) AS txts
}
WITH h, chain, collect({
  owner: n.name, null_mx: coalesce(n.null_mx, false), ips: ips, mxs: mxs, txts: txts
}) AS owners
RETURN h.name AS name,
       coalesce(h.is_input, false) AS is_input,
       coalesce(h.is_apex, false) AS is_apex,
       h.status AS status,
       coalesce(h.failed_lookups, []) AS failed_lookups,
       [x IN chain | x.name] AS chain,
       owners
ORDER BY coalesce(h.apex, h.name), h.name <> coalesce(h.apex, h.name), h.name
"""


class Queryable(Protocol):
    def query(self, cypher: str, **params: Any) -> list[Any]: ...


@dataclass(frozen=True)
class Record:
    owner: str
    rtype: str
    value: str


@dataclass(frozen=True)
class HostnameRecords:
    name: str
    is_input: bool
    is_apex: bool
    status: str
    failed_lookups: list[str]
    chain: list[str]
    """The name itself first, then every name its CNAMEs lead to."""
    records: list[Record]

    def values(self, rtype: str) -> list[str]:
        return [r.value for r in self.records if r.rtype == rtype]


def load_records(
    store: Queryable, hostname: str | None = None, include_apex: bool = True
) -> list[HostnameRecords]:
    rows = store.query(_LOAD, hostname=hostname, include_apex=include_apex)
    return [_to_records(row) for row in rows]


def _to_records(row: Any) -> HostnameRecords:
    chain: list[str] = list(row["chain"])
    records = [Record(owner, "CNAME", target) for owner, target in pairwise(chain)]
    for owner in row["owners"]:
        name = owner["owner"]
        ips = [ip for ip in owner["ips"] if ip["address"] is not None]
        for ip in sorted(ips, key=lambda ip: _ip_sort_key(ip["address"])):
            rtype = "A" if ip["version"] == 4 else "AAAA"
            records.append(Record(name, rtype, ip["address"]))
        if owner["null_mx"]:
            records.append(Record(name, "MX", "0 . (accepts no mail)"))
        mxs = [mx for mx in owner["mxs"] if mx["host"] is not None]
        for mx in sorted(mxs, key=lambda mx: (mx["priority"] or 0, mx["host"])):
            records.append(Record(name, "MX", f"{mx['priority']} {mx['host']}"))
        for value in sorted(owner["txts"]):
            records.append(Record(name, "TXT", value))
    return HostnameRecords(
        name=row["name"],
        is_input=row["is_input"],
        is_apex=row["is_apex"],
        status=row["status"],
        failed_lookups=list(row["failed_lookups"]),
        chain=chain,
        records=records,
    )


def _ip_sort_key(address: str) -> tuple[int, int]:
    ip = ipaddress.ip_address(address)
    return (ip.version, int(ip))
