"""`dnsinv query ...`: the store and the query runner are replaced by fakes."""

import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import unquote

import pytest
from typer.testing import CliRunner

from dnsinv import cli
from dnsinv.graph.connection import Neo4jUnavailableError
from dnsinv.graph.store import ScanInfo
from dnsinv.queries.runner import QueryResult

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Help text without colour codes (Typer forces colour on GitHub Actions)."""
    return _ANSI.sub("", text)


SCAN = ScanInfo("urls.txt", datetime(2026, 11, 14, 10, 30, tzinfo=UTC), "1.1.1.1 (default)", 9)
ROWS = [
    {
        "kind": "mail_server",
        "asset": "mx.mailhost.com",
        "hostname_count": 3,
        "apex_count": 2,
        "hostnames": ["shop.alpha.com", "www.alpha.com", "www.beta.org"],
        "apex_domains": ["alpha.com", "beta.org"],
    }
]


class FakeStore:
    def __init__(self, scans):
        self._scans = scans

    def scans(self):
        return self._scans


@pytest.fixture
def graph(monkeypatch):
    """Install a fake store and runner; returns what the runner was called with."""
    calls = {}

    def install(scans=(SCAN,), rows=ROWS):
        @contextmanager
        def fake_open_store(settings, timeout, on_wait=None):
            yield FakeStore(list(scans))

        def fake_run_query(store, query, values):
            calls["query"] = query.name
            calls["values"] = dict(values)
            query.params(values)  # validation happens in the real runner too
            return QueryResult(tuple(rows[0]) if rows else query.columns, list(rows))

        monkeypatch.setattr(cli, "open_store", fake_open_store)
        monkeypatch.setattr(cli, "run_query", fake_run_query)
        return calls

    return install


def test_query_list_shows_every_query():
    result = runner.invoke(cli.app, ["query", "list"])

    assert result.exit_code == 0
    assert "shared-infra" in result.stdout
    assert "two or more hostnames" in result.stdout


def test_query_run_without_a_name_lists_the_queries():
    result = runner.invoke(cli.app, ["query", "run"])

    assert "shared-infra" in plain(result.output)


def test_each_query_has_its_own_help_with_its_options():
    result = runner.invoke(cli.app, ["query", "run", "shared-infra", "--help"])

    assert result.exit_code == 0
    help_text = plain(result.stdout)
    assert "--min-shared" in help_text
    assert "--cross-apex" in help_text
    assert "--cypher" in help_text


def test_table_output_starts_with_the_scan_it_describes(graph):
    graph()

    result = runner.invoke(cli.app, ["query", "run", "shared-infra"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0] == "Scan of urls.txt at 2026-11-14 10:30 UTC (resolver 1.1.1.1 (default))"
    assert "mx.mailhost.com" in result.stdout
    assert "shop.alpha.com, www.alpha.com, www.beta.org" in result.stdout
    assert "1 result" in result.stdout


def test_long_lists_are_shortened_in_the_table_but_not_in_json(graph):
    long_row = dict(ROWS[0], hostnames=[f"h{i}.example.com" for i in range(10)])
    graph(rows=[long_row])

    table = runner.invoke(cli.app, ["query", "run", "shared-infra"]).stdout
    data = json.loads(
        runner.invoke(cli.app, ["query", "run", "shared-infra", "--format", "json"]).stdout
    )

    assert "h0.example.com, h1.example.com, h2.example.com (+7 more)" in table
    assert "h9.example.com" not in table
    assert len(data["rows"][0]["hostnames"]) == 10


def test_long_text_is_cut_in_the_table(graph):
    graph(rows=[dict(ROWS[0], asset="x" * 200)])

    table = runner.invoke(cli.app, ["query", "run", "shared-infra"]).stdout

    assert "x" * 200 not in table
    assert "x" * 37 + "..." in table


def test_options_reach_the_query(graph):
    calls = graph()

    runner.invoke(cli.app, ["query", "run", "shared-infra", "--min-shared", "3", "--cross-apex"])

    assert calls == {"query": "shared-infra", "values": {"min_shared": 3, "cross_apex": True}}


def test_json_output(graph):
    graph()

    result = runner.invoke(cli.app, ["query", "run", "shared-infra", "--format", "json"])

    data = json.loads(result.stdout)
    assert data["query"] == "shared-infra"
    assert data["options"] == {"min_shared": 2, "cross_apex": False}
    assert data["scans"] == [
        {
            "source": "urls.txt",
            "scanned_at": "2026-11-14T10:30:00+00:00",
            "resolver": "1.1.1.1 (default)",
            "hostnames": 9,
        }
    ]
    assert data["rows"] == ROWS


def test_no_results_is_not_an_error(graph):
    graph(rows=[])

    result = runner.invoke(cli.app, ["query", "run", "shared-infra"])

    assert result.exit_code == 0
    assert "No results" in result.stdout


def test_empty_graph_says_how_to_fill_it(graph):
    graph(scans=())

    result = runner.invoke(cli.app, ["query", "run", "shared-infra"])

    assert result.exit_code == 0
    assert "dnsinv ingest" in result.stderr


def test_option_below_its_minimum_is_a_usage_error(graph):
    graph()

    result = runner.invoke(cli.app, ["query", "run", "shared-infra", "--min-shared", "1"])

    assert result.exit_code == 1
    assert "at least 2" in result.stderr


def test_cypher_prints_the_graph_query_with_values_filled_in_and_needs_no_database(monkeypatch):
    @contextmanager
    def no_database(*args, **kwargs):
        raise AssertionError("--cypher must not connect to Neo4j")
        yield

    monkeypatch.setattr(cli, "open_store", no_database)

    result = runner.invoke(
        cli.app, ["query", "run", "shared-infra", "--cypher", "--min-shared", "3"]
    )

    assert result.exit_code == 0
    assert "RETURN path" in result.stdout
    assert "size(hosts) >= 3" in result.stdout
    assert "$" not in result.stdout
    # The link opens Neo4j Browser with exactly the printed query in its editor.
    link = next(line for line in result.stderr.splitlines() if line.startswith("http"))
    prefix = "http://localhost:7474/browser/?cmd=edit&arg="
    assert link.startswith(prefix)
    assert unquote(link.removeprefix(prefix)) == result.stdout.strip()


def test_neo4j_down_exits_two(monkeypatch):
    @contextmanager
    def unavailable(settings, timeout, on_wait=None):
        raise Neo4jUnavailableError("Neo4j is not reachable at bolt://x:7687 after 30s")
        yield

    monkeypatch.setattr(cli, "open_store", unavailable)

    result = runner.invoke(cli.app, ["query", "run", "shared-infra"])

    assert result.exit_code == 2
    assert "not reachable" in result.stderr
