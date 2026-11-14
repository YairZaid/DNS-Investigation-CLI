"""Build HostnameScans directly, to describe a graph topology in a few lines."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from dnsinv.collection.model import (
    ChainLink,
    CnameChain,
    DnsRecord,
    HostnameScan,
    Lookup,
    LookupStatus,
    RecordType,
    ScanTarget,
)

NOERROR = LookupStatus.NOERROR


def make_scan(
    name: str,
    *,
    apex: str | None = None,
    is_input: bool = True,
    a: Sequence[str] = (),
    cname: Sequence[str] = (),
    mx: Sequence[str] = (),
    txt: Sequence[str] = (),
    end_status: LookupStatus = NOERROR,
) -> HostnameScan:
    """A scan of `name`. `cname` is the chain after `name`; `a` belongs to its last link."""
    chain_names = [name, *cname]
    end = chain_names[-1]
    cname_records = tuple(
        DnsRecord(owner, RecordType.CNAME, target) for owner, target in pairwise(chain_names)
    )
    by_type = {
        RecordType.A: tuple(DnsRecord(end, RecordType.A, ip) for ip in a),
        RecordType.AAAA: (),
        RecordType.MX: tuple(
            DnsRecord(end, RecordType.MX, host, priority=10 * (i + 1)) for i, host in enumerate(mx)
        ),
        RecordType.TXT: tuple(DnsRecord(end, RecordType.TXT, value) for value in txt),
        RecordType.CNAME: cname_records[:1],
    }
    lookups = tuple(
        Lookup(
            name,
            rtype,
            end_status if rtype is not RecordType.CNAME else NOERROR,
            records if rtype is RecordType.CNAME else cname_records + records,
        )
        for rtype, records in by_type.items()
    )
    links = [ChainLink(link, NOERROR) for link in chain_names[:-1]]
    links.append(ChainLink(end, end_status))
    return HostnameScan(ScanTarget(name, is_input, apex), lookups, CnameChain(tuple(links)))
