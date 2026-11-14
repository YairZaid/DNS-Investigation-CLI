"""Command-line entry point. The only module that knows about every other module."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, NoReturn
from urllib.parse import quote

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
from dnsinv.enrichment.catalog import load_catalog
from dnsinv.enrichment.enrich import enrich
from dnsinv.errors import EXIT_ENVIRONMENT, EXIT_USAGE, DnsInvError, UsageError
from dnsinv.graph.connection import check_neo4j
from dnsinv.graph.store import GraphCounts, ScanInfo, open_store
from dnsinv.hostnames import ParsedInput, read_input_file
from dnsinv.queries import all_queries
from dnsinv.queries.base import Query, inline_params
from dnsinv.queries.runner import QueryResult, run_query

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
        catalog = load_catalog()  # a broken catalog fails here, before any DNS
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
            enrichment = enrich(scans, catalog)
            store.ensure_schema()
            store.save_scan(info, scans, replace=not append, enrichment=enrichment)
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


# ---- query ------------------------------------------------------------------------

query_app = typer.Typer(help="Investigation queries over the stored graph.", no_args_is_help=True)
run_app = typer.Typer(
    help="Run an investigation query. Each query is a subcommand with its own options.",
    no_args_is_help=True,
)
app.add_typer(query_app, name="query")
query_app.add_typer(run_app, name="run")

NEO4J_BROWSER_URL = "http://localhost:7474"


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"


@query_app.command("list")
def query_list() -> None:
    """List the available investigation queries."""
    queries = all_queries()
    width = max(len(q.name) for q in queries)
    for query in queries:
        typer.echo(f"{query.name:<{width}}  {query.summary}")


def _query_command(query: Query) -> Callable[..., None]:
    """A Typer command for one query: its own options plus --format and --cypher.

    Built from the query definition, so a new query file needs no CLI code.
    """

    def command(**values: Any) -> None:
        output_format = values.pop("output_format")
        show_cypher = values.pop("cypher")
        _run_query_command(query, values, output_format, show_cypher)

    keyword = inspect.Parameter.KEYWORD_ONLY
    parameters = [
        inspect.Parameter(
            option.name,
            keyword,
            default=option.default,
            annotation=Annotated[option.type, typer.Option(option.flag, help=option.help)],
        )
        for option in query.options
    ]
    parameters += [
        inspect.Parameter(
            "output_format",
            keyword,
            default=OutputFormat.TABLE,
            annotation=Annotated[OutputFormat, typer.Option("--format", help="Output format.")],
        ),
        inspect.Parameter(
            "cypher",
            keyword,
            default=False,
            annotation=Annotated[
                bool,
                typer.Option(
                    "--cypher",
                    help="Print a Cypher query that draws the result as a graph in Neo4j "
                    "Browser, and exit without running it.",
                ),
            ],
        ),
    ]
    command.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    command.__name__ = query.name.replace("-", "_")
    command.__doc__ = query.description
    return command


def _run_query_command(
    query: Query, values: dict[str, Any], output_format: OutputFormat, show_cypher: bool
) -> None:
    try:
        params = query.params(values)
        if show_cypher:
            cypher = inline_params(query.cypher(graph=True), params)
            typer.echo(cypher)
            # Neo4j Browser opens with the query already in its editor (checked on 5.26).
            link = f"{NEO4J_BROWSER_URL}/browser/?cmd=edit&arg={quote(cypher.strip())}"
            _status(
                "Open this link to see the result as a graph in Neo4j Browser "
                f"(user neo4j), then press Run:\n{link}"
            )
            return
        with open_store(Settings.from_env(), NEO4J_WAIT_SECONDS, _status) as store:
            scans = store.scans()
            if scans:
                result = run_query(store, query, values)
            else:
                _status("The graph is empty: run `dnsinv ingest FILE` first.")
                result = QueryResult(query.columns, [])
    except DnsInvError as exc:
        _fail(exc)

    if output_format is OutputFormat.JSON:
        _print_query_json(query, params, scans, result)
    elif scans:
        _print_query_table(scans, result)


def _print_query_json(
    query: Query, params: dict[str, Any], scans: list[ScanInfo], result: QueryResult
) -> None:
    data = {
        "query": query.name,
        "options": params,
        "scans": [
            {
                "source": scan.source,
                "scanned_at": scan.scanned_at.isoformat(timespec="seconds"),
                "resolver": scan.resolver,
                "hostnames": scan.hostnames,
            }
            for scan in scans
        ],
        "rows": result.rows,
    }
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _print_query_table(scans: list[ScanInfo], result: QueryResult) -> None:
    for scan in scans:
        when = scan.scanned_at.strftime("%Y-%m-%d %H:%M UTC")
        typer.echo(f"Scan of {scan.source} at {when} (resolver {scan.resolver})")
    typer.echo("")
    if not result.rows:
        typer.echo("No results.")
        return

    cells = [[_cell(row.get(column)) for column in result.columns] for row in result.rows]
    headers = [column.upper() for column in result.columns]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    typer.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip())
    for row in cells:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())
    count = len(result.rows)
    typer.echo("")
    typer.echo(f"{count} result{'s' if count != 1 else ''}")


MAX_LIST_ITEMS = 3
MAX_ITEM_LENGTH = 40


def _cell(value: Any) -> str:
    """One table cell. Long values are shortened here only; --format json keeps them whole."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        shown = ", ".join(_shorten(str(v)) for v in value[:MAX_LIST_ITEMS])
        hidden = len(value) - MAX_LIST_ITEMS
        return f"{shown} (+{hidden} more)" if hidden > 0 else shown
    return _shorten(str(value))


def _shorten(text: str) -> str:
    if len(text) <= MAX_ITEM_LENGTH:
        return text
    return text[: MAX_ITEM_LENGTH - 3] + "..."


for _query in all_queries():
    run_app.command(_query.name, help=_query.description)(_query_command(_query))
