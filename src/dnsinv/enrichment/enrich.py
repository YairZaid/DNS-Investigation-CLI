"""Which providers a Scan reveals, and from what evidence.

Two kinds of fact come out of it, because they mean different things:

- OperatedAsset: an IP address or a name (CNAME target, mail exchange) that a provider runs.
  This is infrastructure; several domains can share it.
- ProviderClaim: a domain's own statement that it uses a provider - an SPF include or a
  TXT verification token. It belongs to the name that owns the TXT record.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from dnsinv.collection.model import HostnameScan, RecordType
from dnsinv.enrichment.catalog import Catalog, Provider

MAX_DETAIL_LENGTH = 80
_SPF_TARGET = re.compile(r"^[+?~-]?(include):(\S+)$|^(redirect)=(\S+)$", re.IGNORECASE)
_GENERIC_VERIFICATION = re.compile(r"[-_]verification[=:]|^[a-z0-9-]+-verification-", re.I)


class TxtKind(StrEnum):
    SPF = "spf"
    DKIM = "dkim"
    VERIFICATION = "verification"
    OTHER = "other"


@dataclass(frozen=True)
class OperatedAsset:
    kind: Literal["ip", "hostname"]
    asset: str
    provider: str


@dataclass(frozen=True)
class ProviderClaim:
    owner: str
    provider: str
    evidence: Literal["spf", "txt"]
    detail: str


@dataclass(frozen=True)
class Enrichment:
    operated: list[OperatedAsset]
    claims: list[ProviderClaim]
    txt_kinds: dict[tuple[str, str], TxtKind]
    providers: list[Provider]
    """Every provider referenced above, in catalog order."""


def enrich(scans: Sequence[HostnameScan], catalog: Catalog) -> Enrichment:
    operated: dict[OperatedAsset, None] = {}
    claims: dict[ProviderClaim, None] = {}
    txt_kinds: dict[tuple[str, str], TxtKind] = {}

    for scan in scans:
        for link in scan.chain.links[1:]:
            for provider in catalog.match_cname(link.name):
                operated[OperatedAsset("hostname", link.name, provider.name)] = None

        for record in scan.records:
            if record.rtype in (RecordType.A, RecordType.AAAA):
                for provider in catalog.match_ip(record.value):
                    operated[OperatedAsset("ip", record.value, provider.name)] = None
            elif record.rtype is RecordType.MX and not record.is_null_mx:
                for provider in catalog.match_mx(record.value):
                    operated[OperatedAsset("hostname", record.value, provider.name)] = None
            elif record.rtype is RecordType.TXT:
                txt_kinds[record.owner, record.value] = classify_txt(record.value, catalog)
                for claim in _txt_claims(record.owner, record.value, catalog):
                    claims[claim] = None

    used = {item.provider for item in operated} | {claim.provider for claim in claims}
    return Enrichment(
        operated=list(operated),
        claims=list(claims),
        txt_kinds=txt_kinds,
        providers=[p for p in catalog.providers if p.name in used],
    )


def spf_targets(value: str) -> list[tuple[str, str]]:
    """The include: and redirect= domains of an SPF record, lowercased, in order."""
    terms = value.split()
    if not terms or terms[0].lower() != "v=spf1":
        return []
    targets = []
    for term in terms[1:]:
        match = _SPF_TARGET.match(term)
        if match:
            mechanism = (match.group(1) or match.group(3)).lower()
            domain = (match.group(2) or match.group(4)).lower().rstrip(".")
            if domain:
                targets.append((mechanism, domain))
    return targets


def classify_txt(value: str, catalog: Catalog) -> TxtKind:
    text = value.strip()
    lowered = text.lower()
    if lowered == "v=spf1" or lowered.startswith("v=spf1 "):
        return TxtKind.SPF
    if lowered.startswith("v=dkim1"):
        return TxtKind.DKIM
    if catalog.match_txt(text) or _GENERIC_VERIFICATION.search(text):
        return TxtKind.VERIFICATION
    return TxtKind.OTHER


def _txt_claims(owner: str, value: str, catalog: Catalog) -> list[ProviderClaim]:
    claims = []
    for mechanism, domain in spf_targets(value.strip()):
        separator = ":" if mechanism == "include" else "="
        for provider in catalog.match_spf(domain):
            claims.append(
                ProviderClaim(owner, provider.name, "spf", f"{mechanism}{separator}{domain}")
            )
    for provider in catalog.match_txt(value.strip()):
        claims.append(ProviderClaim(owner, provider.name, "txt", _shorten(value.strip())))
    return claims


def _shorten(text: str) -> str:
    if len(text) <= MAX_DETAIL_LENGTH:
        return text
    return text[: MAX_DETAIL_LENGTH - 3] + "..."
