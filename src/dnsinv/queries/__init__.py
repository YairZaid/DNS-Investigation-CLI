"""Investigation Queries. Each one is a module in `definitions/` that defines `QUERY`.

Adding a query means adding one file there: it is picked up by `query list` and
`query run` without touching anything else.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import cache

from dnsinv.queries import definitions
from dnsinv.queries.base import Query


@cache
def _registry() -> dict[str, Query]:
    queries: dict[str, Query] = {}
    for module_info in pkgutil.iter_modules(definitions.__path__):
        module = importlib.import_module(f"{definitions.__name__}.{module_info.name}")
        query = getattr(module, "QUERY", None)
        if not isinstance(query, Query):
            raise TypeError(f"{module.__name__} must define QUERY = Query(...)")
        if query.name in queries:
            raise ValueError(f"two queries are named {query.name!r}")
        queries[query.name] = query
    return dict(sorted(queries.items()))


def all_queries() -> list[Query]:
    return list(_registry().values())


def get_query(name: str) -> Query:
    return _registry()[name]
