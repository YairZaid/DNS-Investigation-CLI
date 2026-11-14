"""Turning an input file of URLs into Input Hostnames, with a reason for every rejected line.

Pure logic: no DNS, no Neo4j, no network. The Apex Domain comes from the Public Suffix List
snapshot bundled with tldextract, so the result never depends on a download at runtime.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

import idna
import tldextract

from dnsinv.errors import UsageError

MAX_LABEL_LENGTH = 63
MAX_NAME_LENGTH = 253

# Letters, digits, hyphen, plus underscore: not valid in hostnames by RFC 952, but common
# in real DNS names and harmless to query.
_LABEL = re.compile(r"^[a-z0-9_-]+$")
# "mailto:x@y", "javascript:..." - a scheme with no "//" and no host. "example.com:8080"
# also looks like "scheme:" but is followed by a digit, so it is excluded.
_SCHEME_WITHOUT_HOST = re.compile(r"^[a-z][a-z0-9+.-]*:(?!//|\d)", re.IGNORECASE)
# dig reads arguments starting with these as options or as the server to query.
_OPTION_PREFIXES = ("-", "+", "@")


class InvalidHostnameError(ValueError):
    """One line could not be turned into a Hostname. The message is the reason."""


class InputFileError(UsageError):
    """The input file as a whole is unusable."""


@dataclass(frozen=True)
class InputHostname:
    name: str
    apex: str | None
    """Registrable domain, or None when the name has none (a public suffix, unknown TLD)."""
    line_number: int
    """First line the name appeared on (1-based)."""


@dataclass(frozen=True)
class RejectedLine:
    line_number: int
    text: str
    reason: str


@dataclass(frozen=True)
class ParsedInput:
    hostnames: tuple[InputHostname, ...]
    rejected: tuple[RejectedLine, ...]
    skipped_lines: int
    """Blank lines and comments."""
    duplicate_lines: int
    """Lines whose Hostname already appeared earlier in the file."""


def read_input_file(path: Path) -> ParsedInput:
    if not path.exists():
        raise InputFileError(f"input file not found: {path}")
    if not path.is_file():
        raise InputFileError(f"input path is not a file: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputFileError(f"cannot read {path}: {exc.strerror}") from exc

    result = parse_input(data)
    if not result.hostnames and not result.rejected:
        raise InputFileError(f"{path} contains no URLs (only blank lines or comments)")
    return result


def parse_input(data: bytes) -> ParsedInput:
    """Parse raw file contents. Bytes, so one undecodable line cannot fail the whole file."""
    hostnames: list[InputHostname] = []
    rejected: list[RejectedLine] = []
    seen: set[str] = set()
    skipped = duplicates = 0

    for number, raw in enumerate(data.splitlines(), start=1):
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace").strip()
            rejected.append(RejectedLine(number, text, "line is not valid UTF-8"))
            continue
        if number == 1:
            line = line.removeprefix("﻿")

        text = line.strip()
        if not text or text.startswith("#"):
            skipped += 1
            continue

        try:
            name = extract_hostname(text)
        except InvalidHostnameError as exc:
            rejected.append(RejectedLine(number, text, str(exc)))
            continue

        if name in seen:
            duplicates += 1
            continue
        seen.add(name)
        hostnames.append(InputHostname(name, apex_domain(name), number))

    return ParsedInput(tuple(hostnames), tuple(rejected), skipped, duplicates)


def extract_hostname(raw: str) -> str:
    """The normalised Hostname in one URL (or bare name). Raises InvalidHostnameError."""
    text = raw.strip()
    if text.startswith(_OPTION_PREFIXES):
        raise InvalidHostnameError(
            f"starts with {text[0]!r} and could be read as a dig option or server"
        )

    if "://" in text:
        url = text
    elif text.startswith("//"):
        url = "http:" + text
    elif _SCHEME_WITHOUT_HOST.match(text):
        raise InvalidHostnameError("no hostname in a URL with this scheme")
    else:
        url = "http://" + text

    try:
        host = urlsplit(url).hostname
    except ValueError as exc:
        raise InvalidHostnameError(f"malformed URL: {exc}") from None
    if not host:
        raise InvalidHostnameError("no hostname in the URL")
    return normalize_hostname(host)


def normalize_hostname(host: str) -> str:
    """Lowercase ASCII (punycode), no trailing dot, validated label by label."""
    name = host[:-1] if host.endswith(".") else host
    if not name:
        raise InvalidHostnameError("no hostname in the URL")

    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        raise InvalidHostnameError("is an IP address, not a hostname")

    if not name.isascii():
        try:
            name = idna.encode(name, uts46=True).decode("ascii")
        except idna.IDNAError as exc:
            raise InvalidHostnameError(f"not a valid internationalised name: {exc}") from None
    name = name.lower()

    if len(name) > MAX_NAME_LENGTH:
        raise InvalidHostnameError(f"name is longer than {MAX_NAME_LENGTH} characters")
    labels = name.split(".")
    if len(labels) == 1:
        raise InvalidHostnameError("single label name (no dot), not a public hostname")
    for label in labels:
        _check_label(label)
    if labels[-1].isdigit():
        raise InvalidHostnameError("numeric top-level label (a malformed IP address?)")
    return name


def _check_label(label: str) -> None:
    if not label:
        raise InvalidHostnameError("empty label (two dots in a row)")
    if len(label) > MAX_LABEL_LENGTH:
        raise InvalidHostnameError(f"label is longer than {MAX_LABEL_LENGTH} characters")
    if not _LABEL.match(label):
        raise InvalidHostnameError(f"invalid character in label {label!r}")
    if label.startswith("-") or label.endswith("-"):
        raise InvalidHostnameError(f"label {label!r} starts or ends with a hyphen")


def apex_domain(hostname: str) -> str | None:
    return _extractor()(hostname).top_domain_under_public_suffix or None


@cache
def _extractor() -> tldextract.TLDExtract:
    # No suffix_list_urls: use the snapshot shipped with the package, never the network.
    # Private suffixes (github.io, s3.amazonaws.com) count as public suffixes, so a GitHub
    # Pages site or an S3 bucket is its own Apex Domain rather than the provider's domain.
    return tldextract.TLDExtract(
        suffix_list_urls=(), include_psl_private_domains=True, cache_dir=None
    )
