"""What a Scan produces. Plain data: this is all the rest of the tool knows about dig."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecordType(StrEnum):
    A = "A"
    AAAA = "AAAA"
    MX = "MX"
    TXT = "TXT"
    CNAME = "CNAME"


class LookupStatus(StrEnum):
    NOERROR = "NOERROR"
    NXDOMAIN = "NXDOMAIN"
    SERVFAIL = "SERVFAIL"
    REFUSED = "REFUSED"
    ERROR = "ERROR"
    """Any other response code, or dig failed without an answer."""
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class DnsRecord:
    owner: str
    """The name the record belongs to. With CNAMEs this is often not the name that was asked."""
    rtype: RecordType
    value: str
    priority: int | None = None
    """MX preference; None for other types."""

    @property
    def is_null_mx(self) -> bool:
        """RFC 7505 "this domain accepts no mail": an MX whose exchange is the root."""
        return self.rtype is RecordType.MX and self.value == "."


@dataclass(frozen=True)
class Answer:
    """One answer block of dig output: the response code and the records it carried."""

    status: LookupStatus
    records: tuple[DnsRecord, ...]


@dataclass(frozen=True)
class Lookup:
    name: str
    rtype: RecordType
    status: LookupStatus
    records: tuple[DnsRecord, ...]


@dataclass(frozen=True)
class ChainLink:
    name: str
    status: LookupStatus


@dataclass(frozen=True)
class CnameChain:
    links: tuple[ChainLink, ...]
    """The scanned name first, then every name reached by following CNAMEs."""
    loop: bool = False
    truncated: bool = False
    """Cut at the hop limit."""

    @property
    def has_cname(self) -> bool:
        return len(self.links) > 1

    @property
    def is_dangling(self) -> bool:
        """The chain ends at a name that does not exist."""
        return (
            self.has_cname
            and not self.loop
            and not self.truncated
            and self.links[-1].status is LookupStatus.NXDOMAIN
        )
