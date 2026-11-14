"""Command-line entry point. The only module that knows about every other module."""

from __future__ import annotations

from typing import Annotated

import typer

from dnsinv.collection.runner import run_subprocess
from dnsinv.config import Settings
from dnsinv.doctor import CheckStatus, run_doctor
from dnsinv.errors import EXIT_ENVIRONMENT
from dnsinv.graph.connection import check_neo4j

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
