import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dnsinv import cli
from dnsinv.collection.runner import CommandNotFoundError, CommandResult
from dnsinv.doctor import Check, CheckStatus, DoctorReport
from tests.fakes import NOERROR, UNREACHABLE, FakeRunner, ok

runner = CliRunner()


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


def test_doctor_passes_resolver_and_timeout_through(fake_doctor):
    captured = fake_doctor(report(CheckStatus.OK, CheckStatus.OK, CheckStatus.OK))

    runner.invoke(cli.app, ["doctor", "--resolver", "8.8.8.8", "--neo4j-timeout", "3"])

    assert captured["explicit_resolver"] == "8.8.8.8"
    assert captured["neo4j_timeout"] == 3.0
