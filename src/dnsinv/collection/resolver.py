"""Choosing the DNS resolver once per run.

Default is 1.1.1.1: some ISPs answer NXDOMAIN with an ad page, which would hide dangling
records, and a public resolver gives the interviewer the same answers as the README.
If it is unreachable we fall back to the system resolver with a warning. A resolver the
user named explicitly is never silently replaced.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import Enum

from dnsinv.collection.runner import CommandRunner, CommandTimeoutError
from dnsinv.errors import DnsInvError, UsageError

DEFAULT_RESOLVER = "1.1.1.1"
PROBE_TIMEOUT_SECONDS = 2
# Root NS query: answered by every working recursive resolver, independent of any domain.
_PROBE_QUERY = ["+time=2", "+tries=1", "+noall", "+comments", ".", "NS"]


class ResolverSource(Enum):
    DEFAULT = "default"
    EXPLICIT = "--resolver"
    SYSTEM = "fallback"


@dataclass(frozen=True)
class ResolverChoice:
    address: str | None
    """IP to pass to dig as @address, or None for the system resolver."""
    source: ResolverSource
    warning: str | None = None

    def describe(self) -> str:
        name = self.address or "system resolver"
        return f"{name} ({self.source.value})"

    def dig_args(self) -> list[str]:
        return [f"@{self.address}"] if self.address else []


class InvalidResolverError(UsageError):
    pass


class ResolverUnreachableError(DnsInvError):
    pass


def select_resolver(runner: CommandRunner, explicit: str | None = None) -> ResolverChoice:
    if explicit is not None:
        address = _validate(explicit)
        if not _answers(runner, address):
            raise ResolverUnreachableError(f"resolver {address} did not answer a test query")
        return ResolverChoice(address, ResolverSource.EXPLICIT)

    if _answers(runner, DEFAULT_RESOLVER):
        return ResolverChoice(DEFAULT_RESOLVER, ResolverSource.DEFAULT)

    return ResolverChoice(
        None,
        ResolverSource.SYSTEM,
        warning=f"{DEFAULT_RESOLVER} is unreachable from this network; "
        "using the system resolver, whose answers may differ",
    )


def _validate(value: str) -> str:
    # Only IP literals: rules out hostnames that need resolving and anything dig
    # could read as an option (values starting with '-' or '+').
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise InvalidResolverError(f"--resolver must be an IP address, got {value!r}") from None


def _answers(runner: CommandRunner, address: str) -> bool:
    try:
        result = runner(["dig", f"@{address}", *_PROBE_QUERY], timeout=PROBE_TIMEOUT_SECONDS + 3)
    except CommandTimeoutError:
        return False
    return result.returncode == 0 and "status: NOERROR" in result.stdout
