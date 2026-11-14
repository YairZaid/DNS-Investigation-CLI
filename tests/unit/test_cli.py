import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dnsinv import cli
from dnsinv.collection.runner import CommandNotFoundError, CommandResult
from dnsinv.doctor import Check, CheckStatus, DoctorReport
from dnsinv.enrichment.catalog import CatalogError
from dnsinv.graph.connection import Neo4jUnavailableError
from dnsinv.graph.store import GraphCounts
from tests.fakes import NOERROR, UNREACHABLE, FakeRunner, ok

# Wide enough that rich never wraps a table cell, whatever terminal the tests run in.
runner = CliRunner(env={"COLUMNS": "250"})


@pytest.fixture
def fake_doctor(monkeypatch):
    """Replace the real checks; returns a setter for the report the CLI will get."""
    captured: dict[str, object] = {}

    def install(report: DoctorReport) -> dict[str, object]:
        def fake_run_doctor(**kwargs):
            captured.update(kwargs)
            return report

        monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)
        return captured

    return install


def report(*statuses: CheckStatus) -> DoctorReport:
    names = ["dig", "resolver", "neo4j"]
    return DoctorReport(
        checks=tuple(Check(n, s, f"{n} detail") for n, s in zip(names, statuses, strict=False))
    )


def test_no_arguments_shows_help():
    result = runner.invoke(cli.app, [])

    assert "doctor" in result.output


def test_doctor_all_ok_exits_zero(fake_doctor):
    fake_doctor(report(CheckStatus.OK, CheckStatus.OK, CheckStatus.OK))

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "OK" in result.output
    assert "neo4j detail" in result.output


def test_doctor_warning_still_exits_zero(fake_doctor):
    fake_doctor(report(CheckStatus.OK, CheckStatus.WARN, CheckStatus.OK))

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "WARN" in result.output


def test_doctor_failure_exits_two(fake_doctor):
    fake_doctor(report(CheckStatus.FAIL, CheckStatus.FAIL, CheckStatus.OK))

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 2
    assert "FAIL" in result.output


FIXTURE = "tests/fixtures/messy_urls.txt"


def test_parse_lists_hostnames_apex_and_rejected_lines():
    result = runner.invoke(cli.app, ["parse", FIXTURE])

    assert result.exit_code == 0
    out = result.output
    assert "www.example.com" in out
    assert "example.co.uk" in out
    assert "xn--bcher-kva.de" in out
    assert "192.168.1.1" in out
    assert "IP address" in out
    assert "7 hostnames" in out
    assert "7 rejected" in out
    assert "2 duplicates" in out


def test_parse_with_only_rejected_lines_exits_one(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("localhost\n10.0.0.1\n")

    result = runner.invoke(cli.app, ["parse", str(path)])

    assert result.exit_code == 1
    assert "no valid hostnames" in result.output


def test_parse_missing_file_exits_one_with_message(tmp_path):
    result = runner.invoke(cli.app, ["parse", str(tmp_path / "nope.txt")])

    assert result.exit_code == 1
    assert "not found" in result.output
    assert "Traceback" not in result.output


DIG_FIXTURES = Path(__file__).parent.parent / "fixtures" / "dig"
DIG_ANSWERS = {
    "alias.test.lab": "alias",
    "xdangling.test.lab": "unbound-xdangling",
    "example.com": "public-example-com",
}


@pytest.fixture
def fake_dig(monkeypatch):
    """Replace dig: the resolver probe answers, and known names get recorded output."""

    def respond(args):
        if args[-2:] == [".", "NS"]:
            return NOERROR
        fixture = DIG_ANSWERS.get(args[-2])
        if fixture is None:
            return CommandResult(9, ";; communications error: timed out\n", "")
        return ok((DIG_FIXTURES / f"{fixture}.txt").read_text(encoding="utf-8"))

    fake = FakeRunner(respond)
    monkeypatch.setattr(cli, "run_subprocess", fake)
    return fake


def write_urls(tmp_path, *lines):
    path = tmp_path / "urls.txt"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_ingest_dry_run_prints_the_scan_as_json(tmp_path, fake_dig):
    path = write_urls(tmp_path, "https://alias.test.lab/x", "xdangling.test.lab", "localhost")

    result = runner.invoke(cli.app, ["ingest", path, "--dry-run"])

    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["resolver"] == "1.1.1.1 (default)"
    alias, dangling = report["hostnames"]
    assert alias["name"] == "alias.test.lab"
    assert alias["status"] == "NOERROR"
    assert alias["dangling"] is False
    assert dangling["cname_chain"] == [
        {"name": "xdangling.test.lab", "status": "NOERROR"},
        {"name": "gone.other.lab", "status": "NXDOMAIN"},
    ]
    assert dangling["dangling"] is True
    assert dangling["lookups"]["A"]["status"] == "NXDOMAIN"
    assert report["rejected_lines"][0]["text"] == "localhost"
    assert "Using resolver: 1.1.1.1" in result.stderr
    assert "Skipping 1 rejected" in result.stderr


def test_ingest_scans_the_apex_domain_unless_told_not_to(tmp_path, fake_dig):
    path = write_urls(tmp_path, "https://www.example.com")

    with_apex = json.loads(runner.invoke(cli.app, ["ingest", path, "--dry-run"]).stdout)
    without = json.loads(runner.invoke(cli.app, ["ingest", path, "--dry-run", "--no-apex"]).stdout)

    assert [(h["name"], h["is_input"]) for h in with_apex["hostnames"]] == [
        ("www.example.com", True),
        ("example.com", False),
    ]
    assert with_apex["hostnames"][0]["status"] == "TIMEOUT"
    assert with_apex["hostnames"][1]["lookups"]["MX"]["records"] == [
        {"owner": "example.com", "type": "MX", "value": ".", "priority": 0}
    ]
    assert [h["name"] for h in without["hostnames"]] == ["www.example.com"]


def test_ingest_with_no_valid_hostname_exits_one_before_any_dns(tmp_path, fake_dig):
    path = write_urls(tmp_path, "localhost", "10.0.0.1")

    result = runner.invoke(cli.app, ["ingest", path, "--dry-run"])

    assert result.exit_code == 1
    assert "no valid hostnames" in result.stderr
    assert fake_dig.calls == []


def test_ingest_invalid_resolver_is_a_usage_error(tmp_path, fake_dig):
    path = write_urls(tmp_path, "example.com")

    result = runner.invoke(cli.app, ["ingest", path, "--dry-run", "--resolver", "dns.google"])

    assert result.exit_code == 1
    assert "IP address" in result.stderr


def test_ingest_unreachable_explicit_resolver_exits_two(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_subprocess", FakeRunner(lambda args: UNREACHABLE))
    path = write_urls(tmp_path, "example.com")

    result = runner.invoke(cli.app, ["ingest", path, "--dry-run", "--resolver", "192.0.2.1"])

    assert result.exit_code == 2
    assert "did not answer" in result.stderr


def test_ingest_without_dig_exits_two(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_subprocess", FakeRunner(lambda a: CommandNotFoundError("dig")))
    path = write_urls(tmp_path, "example.com")

    result = runner.invoke(cli.app, ["ingest", path, "--dry-run"])

    assert result.exit_code == 2
    assert "dig" in result.stderr


def test_ingest_fallback_resolver_prints_a_warning(tmp_path, monkeypatch):
    def respond(args):
        if args[-2:] == [".", "NS"]:
            return UNREACHABLE
        return ok((DIG_FIXTURES / "public-example-com.txt").read_text(encoding="utf-8"))

    monkeypatch.setattr(cli, "run_subprocess", FakeRunner(respond))
    path = write_urls(tmp_path, "example.com")

    result = runner.invoke(cli.app, ["ingest", path, "--dry-run"])

    assert result.exit_code == 0
    assert "Warning: 1.1.1.1 is unreachable" in result.stderr
    assert json.loads(result.stdout)["resolver"] == "system resolver (fallback)"


class FakeStore:
    def __init__(self):
        self.saved = []
        self.schema_created = False
        self.opened = []

    def ensure_schema(self):
        self.schema_created = True

    def save_scan(self, info, scans, *, replace, enrichment=None):
        self.saved.append((info, list(scans), replace))
        self.enrichment = enrichment

    def counts(self):
        return GraphCounts(nodes={"Hostname": 2, "IP": 3, "Scan": 1}, relationships={"CNAME_TO": 1})


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()

    @contextmanager
    def fake_open_store(settings, timeout, on_wait=None):
        store.opened.append(timeout)
        yield store

    monkeypatch.setattr(cli, "open_store", fake_open_store)
    return store


def test_ingest_stores_the_scan_and_replaces_by_default(tmp_path, fake_dig, fake_store):
    path = write_urls(tmp_path, "alias.test.lab", "xdangling.test.lab")

    result = runner.invoke(cli.app, ["ingest", path])

    assert result.exit_code == 0
    ((info, scans, replace),) = fake_store.saved
    assert replace is True
    assert fake_store.schema_created
    assert info.source == path.replace("\\", "/")
    assert info.resolver == "1.1.1.1 (default)"
    assert info.hostnames == 2
    assert [s.name for s in scans] == ["alias.test.lab", "xdangling.test.lab"]
    assert "Stored scan" in result.stdout
    assert "Hostname 2" in result.stdout
    # Enrichment ran on the same scans and was handed to the store with them.
    assert fake_store.enrichment is not None


def test_ingest_with_a_broken_catalog_stops_before_any_dns(tmp_path, fake_dig, monkeypatch):
    def broken():
        raise CatalogError("provider 'X' has category 'magic'")

    monkeypatch.setattr(cli, "load_catalog", broken)
    path = write_urls(tmp_path, "alias.test.lab")

    result = runner.invoke(cli.app, ["ingest", path, "--dry-run"])

    assert result.exit_code == 2
    assert "magic" in result.stderr
    assert fake_dig.calls == []


def test_ingest_append_keeps_the_existing_graph(tmp_path, fake_dig, fake_store):
    path = write_urls(tmp_path, "alias.test.lab")

    runner.invoke(cli.app, ["ingest", path, "--append"])

    ((_, _, replace),) = fake_store.saved
    assert replace is False


def test_ingest_reports_hostnames_whose_lookups_failed(tmp_path, fake_dig, fake_store):
    path = write_urls(tmp_path, "alias.test.lab", "unknown.example.net")

    result = runner.invoke(cli.app, ["ingest", path, "--no-apex"])

    assert result.exit_code == 0
    assert "1 hostname had failed lookups" in result.stderr


def test_dry_run_never_opens_the_store(tmp_path, fake_dig, fake_store):
    path = write_urls(tmp_path, "alias.test.lab")

    runner.invoke(cli.app, ["ingest", path, "--dry-run"])

    assert fake_store.opened == []


def test_ingest_checks_neo4j_before_spending_time_on_dns(tmp_path, fake_dig, monkeypatch):
    @contextmanager
    def unavailable(settings, timeout, on_wait=None):
        raise Neo4jUnavailableError("Neo4j is not reachable at bolt://x:7687 after 30s")
        yield

    monkeypatch.setattr(cli, "open_store", unavailable)
    path = write_urls(tmp_path, "alias.test.lab")

    result = runner.invoke(cli.app, ["ingest", path])

    assert result.exit_code == 2
    assert "not reachable" in result.stderr
    assert all(call[-2:] == [".", "NS"] for call in fake_dig.calls)  # no hostname scanned


def test_failed_scan_leaves_the_graph_untouched(tmp_path, monkeypatch, fake_store):
    def respond(args):
        if args[-2:] == [".", "NS"]:
            return NOERROR
        raise CommandNotFoundError("dig")

    monkeypatch.setattr(cli, "run_subprocess", FakeRunner(respond))
    path = write_urls(tmp_path, "alias.test.lab")

    result = runner.invoke(cli.app, ["ingest", path])

    assert result.exit_code == 2
    assert fake_store.saved == []


def test_doctor_passes_resolver_and_timeout_through(fake_doctor):
    captured = fake_doctor(report(CheckStatus.OK, CheckStatus.OK, CheckStatus.OK))

    runner.invoke(cli.app, ["doctor", "--resolver", "8.8.8.8", "--neo4j-timeout", "3"])

    assert captured["explicit_resolver"] == "8.8.8.8"
    assert captured["neo4j_timeout"] == 3.0
