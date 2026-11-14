"""A Scan as plain JSON-ready data, for `ingest --dry-run`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from dnsinv.collection.model import DnsRecord, Lookup
from dnsinv.collection.resolver import ResolverChoice
from dnsinv.collection.scanner import HostnameScan
from dnsinv.hostnames import RejectedLine


def scan_report(
    *,
    source: str,
    scanned_at: datetime,
    resolver: ResolverChoice,
    scans: Sequence[HostnameScan],
    rejected: Sequence[RejectedLine],
) -> dict[str, Any]:
    return {
        "source": source,
        "scanned_at": scanned_at.isoformat(timespec="seconds"),
        "resolver": resolver.describe(),
        "hostnames": [_hostname(scan) for scan in scans],
        "rejected_lines": [
            {"line": r.line_number, "text": r.text, "reason": r.reason} for r in rejected
        ],
    }


def _hostname(scan: HostnameScan) -> dict[str, Any]:
    chain = scan.chain
    data: dict[str, Any] = {
        "name": scan.name,
        "is_input": scan.target.is_input,
        "apex": scan.target.apex,
        "status": scan.status.value,
    }
    if chain.has_cname or chain.loop:
        data["cname_chain"] = [
            {"name": link.name, "status": link.status.value} for link in chain.links
        ]
        data["dangling"] = chain.is_dangling
        if chain.loop:
            data["loop"] = True
        if chain.truncated:
            data["truncated"] = True
    data["lookups"] = {lookup.rtype.value: _lookup(lookup) for lookup in scan.lookups}
    return data


def _lookup(lookup: Lookup) -> dict[str, Any]:
    return {
        "status": lookup.status.value,
        "records": [_record(record) for record in lookup.records],
    }


def _record(record: DnsRecord) -> dict[str, Any]:
    data: dict[str, Any] = {
        "owner": record.owner,
        "type": record.rtype.value,
        "value": record.value,
    }
    if record.priority is not None:
        data["priority"] = record.priority
    return data
