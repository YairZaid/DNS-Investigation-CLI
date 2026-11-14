"""Writing Scans to Neo4j and reading them back.

Graph model (see docs/PLAN.md section 4):

    (:Hostname)-[:RESOLVES_TO]->(:IP)
    (:Hostname)-[:CNAME_TO]->(:Hostname)
    (:Hostname)-[:MAIL_HANDLED_BY {priority}]->(:Hostname)
    (:Hostname)-[:HAS_TXT]->(:TxtRecord)
    (:Hostname)-[:SUBDOMAIN_OF]->(:Hostname)       apex domain
    (:Scan {source, scanned_at, resolver, hostnames})

Every record hangs off the name that owns it, which with CNAMEs is often not the name
that was scanned: the addresses of www.example.com belong to the end of its chain.
"""

from __future__ import annotations

import hashlib
import ipaddress
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from neo4j import Driver, ManagedTransaction, Record

from dnsinv.collection.model import HostnameScan, LookupStatus, RecordType
from dnsinv.config import Settings
from dnsinv.graph.connection import WaitCallback, wait_for_neo4j

_SCHEMA = (
    "CREATE CONSTRAINT hostname_name IF NOT EXISTS FOR (h:Hostname) REQUIRE h.name IS UNIQUE",
    "CREATE CONSTRAINT ip_address IF NOT EXISTS FOR (i:IP) REQUIRE i.address IS UNIQUE",
    "CREATE CONSTRAINT txt_key IF NOT EXISTS FOR (t:TxtRecord) REQUIRE t.key IS UNIQUE",
)

# Relationships a scan of a Hostname rewrites. Replaced as a whole on every scan, so a
# record that disappeared from DNS disappears from the graph too.
_SCANNED_RELATIONSHIPS = "RESOLVES_TO|CNAME_TO|MAIL_HANDLED_BY|HAS_TXT|SUBDOMAIN_OF"

_WRITE_HOSTNAME = f"""
MERGE (h:Hostname {{name: $name}})
SET h.is_input = coalesce(h.is_input, false) OR $is_input,
    h.is_apex = coalesce(h.is_apex, false) OR $is_apex,
    h.apex = $apex,
    h.status = $status,
    h.scanned_at = $scanned_at,
    h.failed_lookups = $failed_lookups,
    h.dangling = $dangling,
    h.cname_loop = $cname_loop,
    h.cname_truncated = $cname_truncated
WITH h
OPTIONAL MATCH (h)-[old:{_SCANNED_RELATIONSHIPS}]->()
DELETE old
"""

# A chain link that was never scanned itself takes the status the chain gives it.
# One that was scanned keeps its own status, whichever is written first.
_WRITE_CHAIN_STATUS = """
UNWIND $links AS link
MERGE (n:Hostname {name: link.name})
SET n.status = CASE WHEN n.scanned_at IS NULL THEN link.status ELSE n.status END
"""

_WRITE_ADDRESSES = """
UNWIND $records AS rec
MERGE (owner:Hostname {name: rec.owner})
MERGE (ip:IP {address: rec.value})
  ON CREATE SET ip.version = rec.version
MERGE (owner)-[:RESOLVES_TO]->(ip)
"""

_WRITE_CNAMES = """
UNWIND $records AS rec
MERGE (owner:Hostname {name: rec.owner})
MERGE (target:Hostname {name: rec.value})
MERGE (owner)-[:CNAME_TO]->(target)
"""

_WRITE_MAIL = """
UNWIND $records AS rec
MERGE (owner:Hostname {name: rec.owner})
MERGE (mx:Hostname {name: rec.value})
MERGE (owner)-[r:MAIL_HANDLED_BY]->(mx)
SET r.priority = rec.priority
"""

_WRITE_NULL_MX = """
UNWIND $owners AS owner
MERGE (h:Hostname {name: owner})
SET h.null_mx = true
"""

_WRITE_TXT = """
UNWIND $records AS rec
MERGE (owner:Hostname {name: rec.owner})
MERGE (t:TxtRecord {key: rec.key})
  ON CREATE SET t.value = rec.value
MERGE (owner)-[:HAS_TXT]->(t)
"""

_WRITE_APEX = """
MATCH (h:Hostname {name: $name})
MERGE (apex:Hostname {name: $apex})
SET apex.is_apex = true
MERGE (h)-[:SUBDOMAIN_OF]->(apex)
"""

# Nodes nothing points to any more. Input Hostnames and Apex Domains stay even with no
# records: "this name has nothing" is a result.
_DELETE_ORPHANS = """
MATCH (n)
WHERE (n:IP OR n:TxtRecord
       OR (n:Hostname AND NOT coalesce(n.is_input, false) AND NOT coalesce(n.is_apex, false)))
  AND NOT (n)--()
DELETE n
"""


@dataclass(frozen=True)
class ScanInfo:
    source: str
    scanned_at: datetime
    resolver: str
    hostnames: int


@dataclass(frozen=True)
class GraphCounts:
    nodes: dict[str, int]
    relationships: dict[str, int]


class GraphStore:
    def __init__(self, driver: Driver) -> None:
        self._driver = driver

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self._driver.execute_query(statement)

    def clear(self) -> None:
        self._driver.execute_query("MATCH (n) DETACH DELETE n")

    def save_scan(self, info: ScanInfo, scans: Sequence[HostnameScan], *, replace: bool) -> None:
        """Store a Scan. `replace` (the default for ingest) first empties the graph."""
        if replace:
            self.clear()
        with self._driver.session() as session:
            for scan in scans:
                session.execute_write(_write_hostname, scan, info.scanned_at)
            session.execute_write(_write_scan_node, info)
            session.execute_write(lambda tx: tx.run(_DELETE_ORPHANS).consume())

    def scans(self) -> list[ScanInfo]:
        records, _, _ = self._driver.execute_query(
            "MATCH (s:Scan) RETURN s ORDER BY s.scanned_at, s.source"
        )
        return [
            ScanInfo(
                source=r["s"]["source"],
                scanned_at=r["s"]["scanned_at"].to_native(),
                resolver=r["s"]["resolver"],
                hostnames=r["s"]["hostnames"],
            )
            for r in records
        ]

    def counts(self) -> GraphCounts:
        nodes, _, _ = self._driver.execute_query(
            "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY label"
        )
        rels, _, _ = self._driver.execute_query(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY type"
        )
        return GraphCounts(
            nodes={r["label"]: r["n"] for r in nodes},
            relationships={r["type"]: r["n"] for r in rels},
        )

    def query(self, cypher: str, **params: Any) -> list[Record]:
        records, _, _ = self._driver.execute_query(cypher, params)
        return records


@contextmanager
def open_store(
    settings: Settings, timeout: float, on_wait: WaitCallback | None = None
) -> Iterator[GraphStore]:
    driver: Driver = wait_for_neo4j(settings, timeout, on_wait)
    try:
        yield GraphStore(driver)
    finally:
        driver.close()


def _write_hostname(tx: ManagedTransaction, scan: HostnameScan, scanned_at: datetime) -> None:
    target, chain = scan.target, scan.chain
    tx.run(
        _WRITE_HOSTNAME,
        name=scan.name,
        is_input=target.is_input,
        is_apex=target.apex == scan.name,
        apex=target.apex,
        status=scan.status.value,
        scanned_at=scanned_at,
        failed_lookups=[
            f"{lookup.rtype.value}:{lookup.status.value}"
            for lookup in scan.lookups
            if lookup.status not in (LookupStatus.NOERROR, LookupStatus.NXDOMAIN)
        ],
        dangling=chain.is_dangling,
        cname_loop=chain.loop,
        cname_truncated=chain.truncated,
    ).consume()

    links = [{"name": link.name, "status": link.status.value} for link in chain.links[1:]]
    if links:
        tx.run(_WRITE_CHAIN_STATUS, links=links).consume()

    records = scan.records
    by_type = {rtype: [r for r in records if r.rtype is rtype] for rtype in RecordType}
    addresses = [
        {"owner": r.owner, "value": r.value, "version": ipaddress.ip_address(r.value).version}
        for r in by_type[RecordType.A] + by_type[RecordType.AAAA]
    ]
    mail = [r for r in by_type[RecordType.MX] if not r.is_null_mx]
    null_mx = sorted({r.owner for r in by_type[RecordType.MX] if r.is_null_mx})

    _run_if(tx, _WRITE_ADDRESSES, records=addresses)
    _run_if(tx, _WRITE_CNAMES, records=[_plain(r) for r in by_type[RecordType.CNAME]])
    _run_if(tx, _WRITE_MAIL, records=[_plain(r) | {"priority": r.priority} for r in mail])
    _run_if(tx, _WRITE_NULL_MX, owners=null_mx)
    _run_if(
        tx,
        _WRITE_TXT,
        records=[_plain(r) | {"key": _txt_key(r.owner, r.value)} for r in by_type[RecordType.TXT]],
    )
    if target.apex and target.apex != scan.name:
        tx.run(_WRITE_APEX, name=scan.name, apex=target.apex).consume()


def _write_scan_node(tx: ManagedTransaction, info: ScanInfo) -> None:
    tx.run(
        "CREATE (:Scan {source: $source, scanned_at: $scanned_at, resolver: $resolver, "
        "hostnames: $hostnames})",
        source=info.source,
        scanned_at=info.scanned_at,
        resolver=info.resolver,
        hostnames=info.hostnames,
    ).consume()


def _run_if(tx: ManagedTransaction, cypher: str, **params: Any) -> None:
    """Run `cypher` only when it has something to write."""
    if any(params.values()):
        tx.run(cypher, dict(params)).consume()


def _plain(record: Any) -> dict[str, Any]:
    return {"owner": record.owner, "value": record.value}


def _txt_key(owner: str, value: str) -> str:
    # TXT values can be long; index a fixed-size key instead of the value itself.
    return hashlib.sha256(f"{owner}\0{value}".encode()).hexdigest()
