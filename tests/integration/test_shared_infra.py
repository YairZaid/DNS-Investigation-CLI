"""shared-infra against a real Neo4j, on a graph whose answer is known in advance.

www.alpha.com  ──A──────────────────▶ 192.0.2.1  ◀──A── shop.alpha.com
www.beta.org   ──CNAME─▶ edge.cdn.net ──A──▶ 192.0.2.50
img.gamma.io   ──CNAME─▶ edge.cdn.net
alpha.com, beta.org ──MX──▶ mx.mailhost.com     (reached from subdomains via SUBDOMAIN_OF)
lonely.delta.net ──A──▶ 192.0.2.99               (shares nothing)
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from dnsinv.config import Settings
from dnsinv.graph.store import GraphStore, ScanInfo, open_store
from dnsinv.queries import get_query
from dnsinv.queries.runner import run_query
from tests.builders import make_scan

pytestmark = pytest.mark.integration

SCANS = [
    make_scan("www.alpha.com", apex="alpha.com", a=["192.0.2.1"]),
    make_scan("shop.alpha.com", apex="alpha.com", a=["192.0.2.1"]),
    make_scan("alpha.com", apex="alpha.com", is_input=False, mx=["mx.mailhost.com"]),
    make_scan("www.beta.org", apex="beta.org", cname=["edge.cdn.net"], a=["192.0.2.50"]),
    make_scan("beta.org", apex="beta.org", is_input=False, mx=["mx.mailhost.com"]),
    make_scan("img.gamma.io", apex="gamma.io", cname=["edge.cdn.net"], a=["192.0.2.50"]),
    make_scan("lonely.delta.net", apex="delta.net", a=["192.0.2.99"]),
]


@pytest.fixture(scope="module")
def store() -> Iterator[GraphStore]:
    with open_store(Settings.from_env(os.environ), timeout=30.0) as graph:
        graph.ensure_schema()
        info = ScanInfo("known.txt", datetime(2026, 11, 14, tzinfo=UTC), "test", len(SCANS))
        graph.save_scan(info, SCANS, replace=True)
        yield graph


def table(store, **options):
    result = run_query(store, get_query("shared-infra"), options)
    return {(row["kind"], row["asset"]): row for row in result.rows}


def test_finds_every_shared_asset_with_its_hostnames(store):
    rows = table(store)

    assert set(rows) == {
        ("ip", "192.0.2.1"),
        ("ip", "192.0.2.50"),
        ("cname_target", "edge.cdn.net"),
        ("mail_server", "mx.mailhost.com"),
    }
    assert rows["ip", "192.0.2.1"]["hostnames"] == ["shop.alpha.com", "www.alpha.com"]
    assert rows["cname_target", "edge.cdn.net"]["hostnames"] == ["img.gamma.io", "www.beta.org"]
    assert rows["mail_server", "mx.mailhost.com"]["hostnames"] == [
        "shop.alpha.com",
        "www.alpha.com",
        "www.beta.org",
    ]


def test_counts_hostnames_and_apex_domains(store):
    rows = table(store)

    assert (rows["ip", "192.0.2.1"]["hostname_count"], rows["ip", "192.0.2.1"]["apex_count"]) == (
        2,
        1,
    )
    mail = rows["mail_server", "mx.mailhost.com"]
    assert (mail["hostname_count"], mail["apex_count"]) == (3, 2)
    assert mail["apex_domains"] == ["alpha.com", "beta.org"]


def test_most_shared_assets_come_first(store):
    result = run_query(store, get_query("shared-infra"), {})

    counts = [row["hostname_count"] for row in result.rows]
    assert counts == sorted(counts, reverse=True)
    assert result.rows[0]["asset"] == "mx.mailhost.com"


def test_nothing_shared_is_not_reported(store):
    assert ("ip", "192.0.2.99") not in table(store)


def test_cross_apex_drops_sharing_inside_one_organisation(store):
    rows = table(store, cross_apex=True)

    assert ("ip", "192.0.2.1") not in rows
    assert ("ip", "192.0.2.50") in rows
    assert ("mail_server", "mx.mailhost.com") in rows


def test_min_shared_raises_the_bar(store):
    assert set(table(store, min_shared=3)) == {("mail_server", "mx.mailhost.com")}


def test_apex_domains_added_by_the_tool_are_not_counted_as_sharing(store):
    # alpha.com and beta.org are in the graph only because the tool scanned the apex.
    hostnames = {h for row in table(store).values() for h in row["hostnames"]}

    assert "alpha.com" not in hostnames
    assert "beta.org" not in hostnames


def test_graph_version_shows_exactly_the_table_results(store):
    """The --cypher output must never show something the table does not, or the reverse."""
    query = get_query("shared-infra")
    params = query.params({})
    table_pairs = {
        (row["asset"], hostname)
        for row in run_query(store, query, {}).rows
        for hostname in row["hostnames"]
    }

    paths = store.query(query.cypher(graph=True), **params)
    graph_pairs = set()
    for record in paths:
        path = record["path"]
        end = path.end_node
        graph_pairs.add((end.get("address") or end.get("name"), path.start_node["name"]))

    assert graph_pairs == table_pairs


def test_empty_graph_gives_no_rows():
    with open_store(Settings.from_env(os.environ), timeout=30.0) as graph:
        graph.clear()
        try:
            assert run_query(graph, get_query("shared-infra"), {}).rows == []
        finally:
            # Restore for any other test that might use the module fixture.
            info = ScanInfo("known.txt", datetime(2026, 11, 14, tzinfo=UTC), "test", len(SCANS))
            graph.save_scan(info, SCANS, replace=True)
