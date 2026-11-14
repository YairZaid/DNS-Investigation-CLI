"""`dnsinv records`: the graph is replaced by a fake that returns known rows."""

import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from dnsinv import cli
from dnsinv.graph.records import HostnameRecords, Record
from dnsinv.graph.store import ScanInfo

# Wide enough that rich never wraps a cell in these tests.
runner = CliRunner(env={"COLUMNS": "250"})
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
SCAN = ScanInfo("urls.txt", datetime(2026, 11, 14, 10, 30, tzinfo=UTC), "1.1.1.1 (default)", 2)

WWW = HostnameRecords(
    name="www.example.com",
    is_input=True,
    is_apex=False,
    status="NOERROR",
    failed_lookups=[],
    chain=["www.example.com", "edge.cdn.net"],
    records=[
        Record("www.example.com", "CNAME", "edge.cdn.net"),
        Record("edge.cdn.net", "A", "192.0.2.1"),
        Record("edge.cdn.net", "A", "192.0.2.2"),
        Record("edge.cdn.net", "A", "192.0.2.3"),
        Record("edge.cdn.net", "A", "192.0.2.4"),
        Record("edge.cdn.net", "AAAA", "2001:db8::1"),
    ],
)
APEX = HostnameRecords(
    name="example.com",
    is_input=False,
    is_apex=True,
    status="NOERROR",
    failed_lookups=["AAAA:TIMEOUT"],
    chain=["example.com"],
    records=[
        Record("example.com", "MX", "10 mx.mail.example"),
        Record("example.com", "TXT", "v=spf1 include:_spf.google.com -all"),
        Record("example.com", "TXT", "google-site-verification=" + "x" * 60),
    ],
)


def plain(text):
    return _ANSI.sub("", text)


@pytest.fixture
def graph(monkeypatch):
    calls: dict[str, object] = {}

    def install(rows=(APEX, WWW), scans=(SCAN,)):
        @contextmanager
        def fake_open_store(settings, timeout, on_wait=None):
            yield type("FakeStore", (), {"scans": lambda self: list(scans)})()

        def fake_load_records(store, hostname=None, include_apex=True):
            calls.update(hostname=hostname, include_apex=include_apex)
            return [r for r in rows if hostname is None or r.name == hostname]

        monkeypatch.setattr(cli, "open_store", fake_open_store)
        monkeypatch.setattr(cli, "load_records", fake_load_records)
        return calls

    return install


def test_overview_has_one_row_per_hostname_with_all_five_types(graph):
    graph()

    out = plain(runner.invoke(cli.app, ["records"]).stdout)

    for header in ("HOSTNAME", "STATUS", "CNAME", "A", "AAAA", "MX", "TXT"):
        assert header in out
    assert "www.example.com" in out
    assert "example.com (apex)" in out
    assert "→ edge.cdn.net" in out
    assert "mx.mail.example" in out
    assert out.splitlines()[0].startswith("Scan of urls.txt")


def test_overview_shows_how_many_txt_records_not_their_text(graph):
    graph()

    out = plain(runner.invoke(cli.app, ["records"]).stdout)

    assert "v=spf1" not in out
    assert re.search(r"┆ 2 +┆\s*$", out, re.MULTILINE)  # the TXT column of the apex row


def test_overview_is_one_line_per_hostname_first_value_and_count(graph):
    graph()

    out = plain(runner.invoke(cli.app, ["records"]).stdout)

    assert "192.0.2.1 (+3)" in out
    assert "192.0.2.2" not in out


def test_failed_lookups_are_visible(graph):
    graph()

    out = plain(runner.invoke(cli.app, ["records"]).stdout)

    assert "AAAA:TIMEOUT" in out


def test_one_hostname_lists_every_record_in_full(graph):
    calls = graph()

    out = plain(runner.invoke(cli.app, ["records", "--hostname", "example.com"]).stdout)

    assert calls["hostname"] == "example.com"
    assert "google-site-verification=" + "x" * 60 in out
    assert "v=spf1 include:_spf.google.com -all" in out
    for header in ("TYPE", "VALUE", "OWNER"):
        assert header in out


def test_hostname_is_normalised(graph):
    calls = graph()

    runner.invoke(cli.app, ["records", "--hostname", "https://Example.COM/x"])

    assert calls["hostname"] == "example.com"


def test_unknown_hostname(graph):
    graph()

    result = runner.invoke(cli.app, ["records", "--hostname", "nope.example"])

    assert result.exit_code == 1
    assert "nope.example" in result.stderr


def test_one_record_type_in_full(graph):
    graph()

    out = plain(runner.invoke(cli.app, ["records", "--type", "A"]).stdout)

    assert "192.0.2.4" in out
    assert "MX" not in out


def test_record_type_is_case_insensitive_and_checked(graph):
    graph()

    assert runner.invoke(cli.app, ["records", "--type", "mx"]).exit_code == 0
    assert runner.invoke(cli.app, ["records", "--type", "NS"]).exit_code != 0


def test_inputs_only(graph):
    calls = graph()

    runner.invoke(cli.app, ["records", "--inputs-only"])

    assert calls["include_apex"] is False


def test_json_has_everything(graph):
    graph()

    data = json.loads(runner.invoke(cli.app, ["records", "--format", "json"]).stdout)

    (apex, www) = data["hostnames"]
    assert www["name"] == "www.example.com"
    assert www["records"]["A"] == ["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4"]
    assert apex["failed_lookups"] == ["AAAA:TIMEOUT"]
    assert data["scans"][0]["source"] == "urls.txt"


def test_empty_graph(graph):
    graph(rows=(), scans=())

    result = runner.invoke(cli.app, ["records"])

    assert result.exit_code == 0
    assert "dnsinv ingest" in result.stderr


def test_dns_text_is_printed_as_is_not_as_styling_or_emoji(graph):
    # A TXT record is arbitrary text from the internet; rich must not interpret it.
    hostile = HostnameRecords(
        name="odd.example",
        is_input=True,
        is_apex=False,
        status="NOERROR",
        failed_lookups=[],
        chain=["odd.example"],
        records=[Record("odd.example", "TXT", "[bold red]not bold[/bold red] :smile:")],
    )
    graph(rows=(hostile,))

    out = plain(runner.invoke(cli.app, ["records", "--hostname", "odd.example"]).stdout)

    assert "[bold red]not bold[/bold red] :smile:" in out


def test_tables_have_dashed_column_separators(graph):
    graph()

    out = runner.invoke(cli.app, ["records"]).stdout

    assert "┆" in out
