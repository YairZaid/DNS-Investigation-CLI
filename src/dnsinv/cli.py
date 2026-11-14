"""Command-line entry point. The only module that knows about every other module."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from dnsinv.collection.runner import run_subprocess
from dnsinv.config import Settings
from dnsinv.doctor import CheckStatus, run_doctor
from dnsinv.errors import EXIT_ENVIRONMENT, EXIT_USAGE, DnsInvError
from dnsinv.graph.connection import check_neo4j
from dnsinv.hostnames import ParsedInput, read_input_file

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
