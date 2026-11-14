"""The query framework: registry, options and the Cypher it produces. No database needed."""

import pytest

from dnsinv.queries import all_queries, get_query
from dnsinv.queries.base import Option, Query, QueryOptionError, inline_params


def test_shared_infra_is_registered():
    names = [q.name for q in all_queries()]

    assert "shared-infra" in names
    assert names == sorted(names)


def test_query_names_are_unique_and_documented():
    queries = all_queries()

    assert len({q.name for q in queries}) == len(queries)
    for query in queries:
        assert query.summary
        assert query.description


def test_unknown_query():
    with pytest.raises(KeyError):
        get_query("no-such-query")


def toy_query():
    return Query(
        name="toy",
        summary="A toy query.",
        description="For tests.",
        options=(
            Option("min_shared", int, 2, "Minimum.", minimum=2),
            Option("cross_apex", bool, False, "Across apex domains."),
        ),
        body="MATCH (h:Hostname) WHERE size(h.name) >= $min_shared AND $cross_apex = false\nWITH h",
        table_return="RETURN h.name AS name",
        graph_return="RETURN h",
    )


def test_table_and_graph_cypher_share_the_same_body():
    query = toy_query()

    table, graph = query.cypher(graph=False), query.cypher(graph=True)

    assert table.startswith(query.body)
    assert graph.startswith(query.body)
    assert table.endswith("RETURN h.name AS name")
    assert graph.endswith("RETURN h")


def test_params_fill_in_defaults():
    assert toy_query().params({}) == {"min_shared": 2, "cross_apex": False}
    assert toy_query().params({"min_shared": 5}) == {"min_shared": 5, "cross_apex": False}


def test_params_enforce_the_minimum():
    with pytest.raises(QueryOptionError, match="at least 2"):
        toy_query().params({"min_shared": 1})


def test_params_reject_unknown_options():
    with pytest.raises(QueryOptionError, match="unknown"):
        toy_query().params({"nope": 1})


@pytest.mark.parametrize(
    ("value", "literal"),
    [
        (3, "3"),
        (True, "true"),
        (False, "false"),
        ("it's", "'it\\'s'"),
        ("back\\slash", "'back\\\\slash'"),
        (None, "null"),
        (["a", 1], "['a', 1]"),
    ],
)
def test_inline_params_writes_cypher_literals(value, literal):
    assert inline_params("RETURN $x", {"x": value}) == f"RETURN {literal}"


def test_inline_params_replaces_whole_names_only():
    cypher = "WHERE a = $min AND b = $min_shared"

    assert inline_params(cypher, {"min": 1, "min_shared": 2}) == "WHERE a = 1 AND b = 2"


def test_inline_params_leaves_unknown_names():
    assert inline_params("RETURN $other", {"x": 1}) == "RETURN $other"
