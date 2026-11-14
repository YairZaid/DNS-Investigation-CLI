import pytest
from typer.testing import CliRunner

from dnsinv import cli
from dnsinv.doctor import Check, CheckStatus, DoctorReport

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


def test_doctor_passes_resolver_and_timeout_through(fake_doctor):
    captured = fake_doctor(report(CheckStatus.OK, CheckStatus.OK, CheckStatus.OK))

    runner.invoke(cli.app, ["doctor", "--resolver", "8.8.8.8", "--neo4j-timeout", "3"])

    assert captured["explicit_resolver"] == "8.8.8.8"
    assert captured["neo4j_timeout"] == 3.0
