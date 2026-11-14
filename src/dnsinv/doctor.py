"""The `doctor` command: is everything the tool needs in place?"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from dnsinv.collection.resolver import select_resolver
from dnsinv.collection.runner import CommandRunner
from dnsinv.config import Settings
from dnsinv.errors import DnsInvError
from dnsinv.graph.connection import WaitCallback


class CheckStatus(Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Check:
    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return all(check.status is not CheckStatus.FAIL for check in self.checks)


Neo4jCheck = Callable[[Settings, float, WaitCallback | None], str]


def run_doctor(
    *,
    runner: CommandRunner,
    settings: Settings,
    explicit_resolver: str | None,
    check_neo4j: Neo4jCheck,
    neo4j_timeout: float,
    on_wait: WaitCallback | None = None,
) -> DoctorReport:
    dig = _check_dig(runner)
    if dig.status is CheckStatus.FAIL:
        resolver = Check("resolver", CheckStatus.FAIL, "skipped: dig is not available")
    else:
        resolver = _check_resolver(runner, explicit_resolver)
    neo4j = _check_neo4j(check_neo4j, settings, neo4j_timeout, on_wait)
    return DoctorReport((dig, resolver, neo4j))


def _check_dig(runner: CommandRunner) -> Check:
    try:
        result = runner(["dig", "-v"], timeout=5)
    except DnsInvError as exc:
        return Check("dig", CheckStatus.FAIL, str(exc))
    # dig prints its version to stderr.
    version = (result.stderr or result.stdout).strip().splitlines()
    return Check("dig", CheckStatus.OK, version[0] if version else "installed")


def _check_resolver(runner: CommandRunner, explicit: str | None) -> Check:
    try:
        choice = select_resolver(runner, explicit)
    except DnsInvError as exc:
        return Check("resolver", CheckStatus.FAIL, str(exc))
    if choice.warning:
        return Check("resolver", CheckStatus.WARN, f"{choice.describe()}: {choice.warning}")
    return Check("resolver", CheckStatus.OK, choice.describe())


def _check_neo4j(
    check: Neo4jCheck, settings: Settings, timeout: float, on_wait: WaitCallback | None
) -> Check:
    try:
        return Check(
            "neo4j", CheckStatus.OK, f"{check(settings, timeout, on_wait)} at {settings.neo4j_uri}"
        )
    except DnsInvError as exc:
        return Check("neo4j", CheckStatus.FAIL, str(exc))
