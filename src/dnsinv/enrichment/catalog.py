"""Loading and validating the provider catalog (providers.yaml), and matching against it.

The catalog is data: adding a provider means adding an entry to the YAML file, not code.
A malformed entry stops the tool with a message naming the entry, instead of silently
matching nothing.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from typing import Any, Literal

import yaml

from dnsinv.errors import DnsInvError

CATEGORIES = frozenset({"cdn", "cloud", "hosting", "email", "saas"})
_PATTERN_KEYS = frozenset({"cname", "cname_regex", "mx", "spf", "txt", "ip"})
_PROVIDER_KEYS = frozenset({"name", "category", "shared_platform", "patterns", "takeover"})

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class CatalogError(DnsInvError):
    pass


@dataclass(frozen=True)
class Takeover:
    signal: Literal["nxdomain", "http"]
    marker: str | None = None


@dataclass(frozen=True)
class Provider:
    name: str
    category: str
    shared_platform: bool = False
    takeover: Takeover | None = None
    cname: tuple[str, ...] = ()
    cname_regex: tuple[re.Pattern[str], ...] = ()
    mx: tuple[str, ...] = ()
    spf: tuple[str, ...] = ()
    txt: tuple[re.Pattern[str], ...] = ()
    ip: tuple[IpNetwork, ...] = field(default=())

    @property
    def has_patterns(self) -> bool:
        return any((self.cname, self.cname_regex, self.mx, self.spf, self.txt, self.ip))

    def matches_cname(self, name: str) -> bool:
        return _has_suffix(name, self.cname) or any(r.search(name) for r in self.cname_regex)

    def matches_mx(self, exchange: str) -> bool:
        return _has_suffix(exchange, self.mx)

    def matches_spf(self, domain: str) -> bool:
        return _has_suffix(domain, self.spf)

    def matches_txt(self, value: str) -> bool:
        return any(r.search(value) for r in self.txt)

    def matches_ip(self, address: str) -> bool:
        ip = ipaddress.ip_address(address)
        return any(ip.version == net.version and ip in net for net in self.ip)


@dataclass(frozen=True)
class Catalog:
    providers: tuple[Provider, ...]

    def get(self, name: str) -> Provider:
        return next(p for p in self.providers if p.name == name)

    def match_cname(self, name: str) -> list[Provider]:
        return [p for p in self.providers if p.matches_cname(name)]

    def match_mx(self, exchange: str) -> list[Provider]:
        return [p for p in self.providers if p.matches_mx(exchange)]

    def match_spf(self, domain: str) -> list[Provider]:
        return [p for p in self.providers if p.matches_spf(domain)]

    def match_txt(self, value: str) -> list[Provider]:
        return [p for p in self.providers if p.matches_txt(value)]

    def match_ip(self, address: str) -> list[Provider]:
        return [p for p in self.providers if p.matches_ip(address)]


@cache
def load_catalog() -> Catalog:
    """The catalog bundled with the package."""
    text = files("dnsinv.enrichment").joinpath("providers.yaml").read_text(encoding="utf-8")
    return parse_catalog(text)


def parse_catalog(text: str) -> Catalog:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"provider catalog is not valid YAML: {exc}") from None
    if not isinstance(data, dict) or "providers" not in data:
        raise CatalogError("provider catalog must have a top-level 'providers' list")
    entries = data["providers"]
    if not isinstance(entries, list) or not entries:
        raise CatalogError("provider catalog has no providers")

    providers: list[Provider] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        provider = _parse_provider(entry, index)
        if provider.name in seen:
            raise CatalogError(f"provider {provider.name!r} is listed twice")
        seen.add(provider.name)
        providers.append(provider)
    return Catalog(tuple(providers))


def _parse_provider(entry: Any, index: int) -> Provider:
    where = f"provider #{index}"
    if not isinstance(entry, dict):
        raise CatalogError(f"{where} must be a mapping")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise CatalogError(f"{where} needs a name")
    where = f"provider {name!r}"

    unknown = set(entry) - _PROVIDER_KEYS
    if unknown:
        raise CatalogError(f"{where} has unknown key(s): {', '.join(sorted(unknown))}")
    category = entry.get("category")
    if category not in CATEGORIES:
        raise CatalogError(
            f"{where} has category {category!r}; expected one of {', '.join(sorted(CATEGORIES))}"
        )
    patterns = entry.get("patterns") or {}
    if not isinstance(patterns, dict):
        raise CatalogError(f"{where}: patterns must be a mapping")
    unknown = set(patterns) - _PATTERN_KEYS
    if unknown:
        raise CatalogError(f"{where} has unknown pattern type(s): {', '.join(sorted(unknown))}")

    provider = Provider(
        name=name,
        category=category,
        shared_platform=bool(entry.get("shared_platform", False)),
        takeover=_parse_takeover(entry.get("takeover"), where),
        cname=_suffixes(patterns, "cname", where),
        cname_regex=_regexes(patterns, "cname_regex", where),
        mx=_suffixes(patterns, "mx", where),
        spf=_suffixes(patterns, "spf", where),
        txt=_regexes(patterns, "txt", where),
        ip=_networks(patterns, "ip", where),
    )
    if not provider.has_patterns:
        raise CatalogError(f"{where} has no patterns, so it could never match")
    return provider


def _strings(patterns: dict[str, Any], key: str, where: str) -> list[str]:
    values = patterns.get(key, [])
    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
        raise CatalogError(f"{where}: {key} must be a list of non-empty strings")
    return values


def _suffixes(patterns: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    values = _strings(patterns, key, where)
    for value in values:
        if value != value.lower() or value.endswith(".") or value.startswith("."):
            raise CatalogError(
                f"{where}: {key} suffix {value!r} must be lowercase, without leading or "
                "trailing dots"
            )
    return tuple(values)


def _regexes(patterns: dict[str, Any], key: str, where: str) -> tuple[re.Pattern[str], ...]:
    compiled = []
    for value in _strings(patterns, key, where):
        try:
            compiled.append(re.compile(value, re.IGNORECASE))
        except re.error as exc:
            raise CatalogError(
                f"{where}: {value!r} is not a valid regular expression: {exc}"
            ) from None
    return tuple(compiled)


def _networks(patterns: dict[str, Any], key: str, where: str) -> tuple[IpNetwork, ...]:
    networks = []
    for value in _strings(patterns, key, where):
        try:
            networks.append(ipaddress.ip_network(value))
        except ValueError:
            raise CatalogError(f"{where}: {value!r} is not an address range") from None
    return tuple(networks)


def _parse_takeover(value: Any, where: str) -> Takeover | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CatalogError(f"{where}: takeover must be a mapping")
    signal = value.get("signal")
    if signal == "nxdomain":
        return Takeover("nxdomain")
    if signal == "http":
        marker = value.get("marker")
        if not isinstance(marker, str) or not marker:
            raise CatalogError(f"{where}: an http takeover signal needs a marker")
        return Takeover("http", marker)
    raise CatalogError(f"{where}: takeover signal must be nxdomain or http, got {signal!r}")


def _has_suffix(name: str, suffixes: tuple[str, ...]) -> bool:
    # A match must start at a label boundary: evilcloudfront.net is not cloudfront.net.
    return any(name == s or name.endswith("." + s) for s in suffixes)
