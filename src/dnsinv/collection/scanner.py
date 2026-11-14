"""Scanning Hostnames: which names to ask about, one dig process each, and what it means."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor

from dnsinv.collection.dig import PROCESS_TIMEOUT_SECONDS, build_dig_args, parse_dig_output
from dnsinv.collection.model import (
    ChainLink,
    CnameChain,
    HostnameScan,
    Lookup,
    LookupStatus,
    RecordType,
    ScanTarget,
    distinct_records,
)
from dnsinv.collection.resolver import ResolverChoice
from dnsinv.collection.runner import CommandRunner, CommandTimeoutError
from dnsinv.hostnames import InputHostname

DEFAULT_WORKERS = 10
MAX_CHAIN_HOPS = 10
DIG_EXIT_NO_REPLY = 9

# Which response code describes a name when its lookups disagree. NXDOMAIN is about the
# name, not the record type, so one lookup reporting it settles the question. After that,
# any successful lookup outweighs a failed one.
_STATUS_PRIORITY = (
    LookupStatus.NXDOMAIN,
    LookupStatus.NOERROR,
    LookupStatus.SERVFAIL,
    LookupStatus.REFUSED,
    LookupStatus.ERROR,
    LookupStatus.TIMEOUT,
)


def plan_targets(hostnames: Iterable[InputHostname], *, include_apex: bool) -> list[ScanTarget]:
    """Input Hostnames in file order, each followed by its Apex Domain the first time it appears."""
    targets: dict[str, ScanTarget] = {}
    for hostname in hostnames:
        targets[hostname.name] = ScanTarget(hostname.name, True, hostname.apex)
        if include_apex and hostname.apex and hostname.apex not in targets:
            targets[hostname.apex] = ScanTarget(hostname.apex, False, hostname.apex)
    return list(targets.values())


def scan_all(
    targets: Sequence[ScanTarget],
    runner: CommandRunner,
    resolver: ResolverChoice,
    *,
    workers: int = DEFAULT_WORKERS,
    on_done: Callable[[HostnameScan], None] | None = None,
) -> list[HostnameScan]:
    """Scan every target, `workers` at a time. Results come back in the order given."""

    def scan_one(target: ScanTarget) -> HostnameScan:
        result = scan_hostname(target, runner, resolver)
        if on_done is not None:
            on_done(result)
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(scan_one, targets))


def scan_hostname(
    target: ScanTarget, runner: CommandRunner, resolver: ResolverChoice
) -> HostnameScan:
    name = target.name
    try:
        result = runner(build_dig_args(name, resolver), timeout=PROCESS_TIMEOUT_SECONDS)
    except CommandTimeoutError:
        lookups = tuple(Lookup(name, rtype, LookupStatus.TIMEOUT, ()) for rtype in RecordType)
        return HostnameScan(target, lookups, _build_chain(name, lookups))

    answers = parse_dig_output(result.stdout)
    # dig says nothing identifiable about a question that got no reply: no block at all.
    missing = LookupStatus.TIMEOUT if result.returncode == DIG_EXIT_NO_REPLY else LookupStatus.ERROR
    lookups = tuple(
        Lookup(name, rtype, answer.status, answer.records)
        if (answer := answers.get((name, rtype)))
        else Lookup(name, rtype, missing, ())
        for rtype in RecordType
    )
    return HostnameScan(target, lookups, _build_chain(name, lookups))


def _build_chain(name: str, lookups: Sequence[Lookup]) -> CnameChain:
    """Follow CNAMEs from `name` and give every link its own status.

    A resolver answers `A dangling.example.com` with the CNAME *and* NXDOMAIN: the name
    exists (it has a CNAME), and the response code describes the end of the chain. So a
    link that has a CNAME is NOERROR, and the response code goes to the last link.
    """
    targets = {
        record.owner: record.value
        for record in distinct_records(lookups)
        if record.rtype is RecordType.CNAME
    }

    names = [name]
    loop = truncated = False
    while (target := targets.get(names[-1])) is not None:
        if target in names:
            loop = True
            break
        if len(names) > MAX_CHAIN_HOPS:
            truncated = True
            break
        names.append(target)

    if len(names) == 1:
        end_status = _combined_status(lookups)
    else:
        # The CNAME lookup stops at the first link, so it says nothing about the end.
        end_status = _combined_status([x for x in lookups if x.rtype is not RecordType.CNAME])
    links = [ChainLink(link, LookupStatus.NOERROR) for link in names[:-1]]
    links.append(ChainLink(names[-1], end_status))
    return CnameChain(tuple(links), loop=loop, truncated=truncated)


def _combined_status(lookups: Sequence[Lookup]) -> LookupStatus:
    seen = {lookup.status for lookup in lookups}
    return next(status for status in _STATUS_PRIORITY if status in seen)
