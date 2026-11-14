"""What an Investigation Query is.

A query is data: a Cypher body shared by two endings. The table ending returns rows for the
terminal, the graph ending returns paths for Neo4j Browser. Because both start from the same
body, the picture can never show something the table does not, or the reverse.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dnsinv.errors import UsageError

OptionValue = int | bool | str


class QueryOptionError(UsageError):
    pass


@dataclass(frozen=True)
class Option:
    name: str
    """Cypher parameter name; the CLI flag is the same with dashes (min_shared -> --min-shared)."""
    type: type[int] | type[bool] | type[str]
    default: OptionValue
    help: str
    minimum: int | None = None

    @property
    def flag(self) -> str:
        return "--" + self.name.replace("_", "-")


@dataclass(frozen=True)
class Query:
    name: str
    summary: str
    """One line, for `query list`."""
    description: str
    """What the query answers and how to read the result, for `query run NAME --help`."""
    body: str
    """MATCH ... WITH: everything both endings share, including every filter."""
    table_return: str
    graph_return: str
    """Must return paths in a column named `path`."""
    columns: tuple[str, ...] = ()
    options: tuple[Option, ...] = ()

    def cypher(self, *, graph: bool) -> str:
        ending = self.graph_return if graph else self.table_return
        return f"{self.body.rstrip()}\n{ending.strip()}"

    def params(self, values: Mapping[str, Any]) -> dict[str, OptionValue]:
        known = {option.name: option for option in self.options}
        unknown = set(values) - set(known)
        if unknown:
            raise QueryOptionError(f"unknown option for {self.name}: {', '.join(sorted(unknown))}")
        params: dict[str, OptionValue] = {}
        for option in self.options:
            value = values.get(option.name, option.default)
            if option.minimum is not None and isinstance(value, int) and value < option.minimum:
                raise QueryOptionError(f"{option.flag} must be at least {option.minimum}")
            params[option.name] = value
        return params


_PARAM = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def inline_params(cypher: str, params: Mapping[str, Any]) -> str:
    """Replace $name with a Cypher literal, so the text runs as-is in Neo4j Browser."""

    def literal(match: re.Match[str]) -> str:
        name = match.group(1)
        return _literal(params[name]) if name in params else match.group(0)

    return _PARAM.sub(literal, cypher)


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"
