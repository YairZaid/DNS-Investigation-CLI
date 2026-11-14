"""Command-line entry point. The only module that knows about every other module."""

from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

from dnsinv.collection.export import scan_report
from dnsinv.collection.model import HostnameScan, LookupStatus, ScanTarget
from dnsinv.collection.resolver import ResolverChoice, select_resolver
from dnsinv.collection.runner import run_subprocess
from dnsinv.collection.scanner import DEFAULT_WORKERS, plan_targets, scan_all
from dnsinv.config import Settings
from dnsinv.doctor import CheckStatus, run_doctor
from dnsinv.errors import EXIT_ENVIRONMENT, EXIT_USAGE, DnsInvError, UsageError
from dnsinv.graph.connection import check_neo4j
from dnsinv.graph.store import GraphCounts, ScanInfo, open_store
from dnsinv.hostnames import ParsedInput, read_input_file

NEO4J_WAIT_SECONDS = 30.0
_FAILED = {LookupStatus.TIMEOUT, LookupStatus.SERVFAIL, LookupStatus.REFUSED, LookupStatus.ERROR}

app = typer.Typer(
    help="Collect DNS records for the hostnames in a list of URLs, store them in Neo4j, "
    "and investigate the graph.",
    no_args_is_help=True,
    add_completion=False,
)

_MARKS = {CheckStatus.OK: "OK  ", CheckStatus.WARN: "WARN", CheckStatus.FAIL: "FAIL"}


@app.callback()
def main() -> None:
    """DNS Investigation CLI."""


def _status(message: str) -> None:
    typer.echo(message, err=True)


def _fail(exc: DnsInvError) -> NoReturn:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(exc.exit_code)


@app.command()
def parse(
    file: Annotated[Path, typer.Argument(help="Text file with one URL per line.")],
) -> None:
    """Show how an input file is read: hostnames, apex domains and rejected lines."""
    try:
        result = read_input_file(file)
    except DnsInvError as exc:
        _fail(exc)
    _print_parsed(result)
    if not result.hostnames:
        typer.echo("Error: the file has no valid hostnames", err=True)
        raise typer.Exit(EXIT_USAGE)


@app.command()
def ingest(
    file: Annotated[Path, typer.Argument(help="Text file with one URL per line.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the scan as JSON instead of storing it.")
    ] = False,
    resolver: Annotated[
        str | None, typer.Option(help="Resolver IP to use instead of the default 1.1.1.1.")
    ] = None,
    workers: Annotated[
        int, typer.Option(min=1, max=64, help="Hostnames scanned at the same time.")
    ] = DEFAULT_WORKERS,
    no_apex: Annotated[
        bool, typer.Option("--no-apex", help="Scan only the hostnames in the file.")
    ] = False,
    append: Annotated[
        bool,
        typer.Option("--append", help="Add to the graph instead of replacing it with this scan."),
    ] = False,
) -> None:
    """Scan the hostnames in FILE with dig (A, AAAA, MX, TXT, CNAME) and store them in Neo4j.

    Each ingest replaces the whole graph, so queries always describe one file scanned at
    one time. Use --append to combine several files in one graph.
    """
    try:
        parsed = read_input_file(file)
        if not parsed.hostnames:
            raise UsageError(f"{file} has no valid hostnames (run `dnsinv parse {file}`)")
        with ExitStack() as stack:
            # Connect before scanning: fail in seconds, not after the whole scan.
            store = (
                None
                if dry_run
                else stack.enter_context(
                    open_store(Settings.from_env(), NEO4J_WAIT_SECONDS, _status)
                )
            )
            choice = select_resolver(run_subprocess, resolver)
            if choice.warning:
                _status(f"Warning: {choice.warning}")
            _status(f"Using resolver: {choice.describe()}")
            if parsed.rejected:
                _status(
                    f"Skipping {len(parsed.rejected)} rejected lines (run `dnsinv parse {file}`)"
                )

            targets = plan_targets(parsed.hostnames, include_apex=not no_apex)
            scanned_at = datetime.now(UTC)
            scans = _scan_with_progress(targets, choice, workers)
            _warn_failed_lookups(scans)

            if store is None:
                report = scan_report(
                    source=file.as_posix(),
                    scanned_at=scanned_at,
                    resolver=choice,
                    scans=scans,
                    rejected=parsed.rejected,
                )
                typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
                return

            info = ScanInfo(
                source=file.as_posix(),
                scanned_at=scanned_at,
                resolver=choice.describe(),
                hostnames=len(scans),
            )
            store.ensure_schema()
            store.save_scan(info, scans, replace=not append)
            counts = store.counts()
    except DnsInvError as exc:
        _fail(exc)

    _print_stored(info, scans, len(parsed.rejected), counts, appended=append)


def _warn_failed_lookups(scans: list[HostnameScan]) -> None:
    failed = [s for s in scans if any(lk.status in _FAILED for lk in s.lookups)]
    if failed:
        noun = "hostname" if len(failed) == 1 else "hostnames"
        _status(
            f"Warning: {len(failed)} {noun} had failed lookups (timeout or server failure); "
            "their records may be incomplete"
        )


def _print_stored(
    info: ScanInfo,
    scans: list[HostnameScan],
    rejected: int,
    counts: GraphCounts,
    *,
    appended: bool,
) -> None:
    inputs = sum(1 for s in scans if s.target.is_input)
    when = info.scanned_at.strftime("%Y-%m-%d %H:%M UTC")
    typer.echo(f"Stored scan of {info.source} at {when} (resolver {info.resolver})")
    typer.echo(
        f"  {inputs} hostnames from the file, {len(scans) - inputs} apex domains added, "
        f"{rejected} rejected lines"
    )
    nodes = ", ".join(f"{label} {n}" for label, n in counts.nodes.items())
    rels = ", ".join(f"{rtype} {n}" for rtype, n in counts.relationships.items())
    typer.echo(f"  Graph now: {nodes} | {rels or 'no relationships'}")
    typer.echo(
        "  Added to the existing graph (--append)" if appended else "  Replaced the previous graph"
    )


def _scan_with_progress(
    targets: list[ScanTarget], resolver: ResolverChoice, workers: int
) -> list[HostnameScan]:
    # Progress goes to stderr so stdout stays clean JSON.
    with Progress(
        TextColumn("Scanning"),
        BarColumn(),
        MofNCompleteColumn(),
        console=Console(stderr=True),
        transient=True,
    ) as progress:
        task = progress.add_task("scan", total=len(targets))
        return scan_all(
            targets,
            run_subprocess,
            resolver,
            workers=workers,
            on_done=lambda _: progress.advance(task),
        )


def _print_parsed(result: ParsedInput) -> None:
    if result.hostnames:
        width = max(len(h.name) for h in result.hostnames)
        typer.echo(f"Hostnames ({len(result.hostnames)})")
        typer.echo(f"  {'LINE':>4}  {'HOSTNAME':<{width}}  APEX")
        for h in result.hostnames:
            typer.echo(f"  {h.line_number:>4}  {h.name:<{width}}  {h.apex or '-'}")
    if result.rejected:
        width = max(len(r.text) for r in result.rejected)
        typer.echo(f"\nRejected lines ({len(result.rejected)})")
        typer.echo(f"  {'LINE':>4}  {'TEXT':<{width}}  REASON")
        for r in result.rejected:
            typer.echo(f"  {r.line_number:>4}  {r.text:<{width}}  {r.reason}")
    typer.echo(
        f"\n{len(result.hostnames)} hostnames, {len(result.rejected)} rejected, "
        f"{result.duplicate_lines} duplicates, {result.skipped_lines} blank or comment lines"
    )


@app.command()
def doctor(
    resolver: Annotated[
        str | None, typer.Option(help="Check this resolver IP instead of the default.")
    ] = None,
    neo4j_timeout: Annotated[
        float, typer.Option(help="Seconds to wait for Neo4j to become ready.")
    ] = 30.0,
) -> None:
    """Check that dig, a DNS resolver and Neo4j are all available."""
    report = run_doctor(
        runner=run_subprocess,
        settings=Settings.from_env(),
        explicit_resolver=resolver,
        check_neo4j=check_neo4j,
        neo4j_timeout=neo4j_timeout,
        on_wait=_status,
    )
    for check in report.checks:
        typer.echo(f"[{_MARKS[check.status]}] {check.name:<9} {check.detail}")
    if not report.ok:
        raise typer.Exit(EXIT_ENVIRONMENT)
