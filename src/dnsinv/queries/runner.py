"""Running a Query against the graph and turning the records into plain rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from dnsinv.queries.base import Query


class Queryable(Protocol):
    def query(self, cypher: str, **params: Any) -> Sequence[Any]: ...


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: list[dict[str, Any]]


def run_query(store: Queryable, query: Query, values: Mapping[str, Any]) -> QueryResult:
    params = query.params(values)
    records = store.query(query.cypher(graph=False), **params)
    columns = tuple(records[0].keys()) if records else query.columns
    # A neo4j Record iterates over its values, not its keys, so .keys() is needed here.
    rows = [{key: _plain(record[key]) for key in record.keys()} for record in records]  # noqa: SIM118
    return QueryResult(columns, rows)


def _plain(value: Any) -> Any:
    if hasattr(value, "to_native"):
        return value.to_native()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value
