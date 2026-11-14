"""Errors the CLI turns into a message and an exit code instead of a traceback."""

from __future__ import annotations

EXIT_USAGE = 1
"""Bad arguments or bad input file."""

EXIT_ENVIRONMENT = 2
"""Something the tool depends on is missing: dig, the resolver, or Neo4j."""


class DnsInvError(Exception):
    """Base for expected failures. `exit_code` tells the CLI how to exit."""

    exit_code: int = EXIT_ENVIRONMENT


class UsageError(DnsInvError):
    exit_code = EXIT_USAGE
