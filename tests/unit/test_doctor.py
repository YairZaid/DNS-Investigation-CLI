from dnsinv.collection.runner import CommandNotFoundError
from dnsinv.config import Settings
from dnsinv.doctor import CheckStatus, run_doctor
from dnsinv.graph.connection import Neo4jUnavailableError
from tests.fakes import NOERROR, UNREACHABLE, FakeRunner, ok

SETTINGS = Settings.from_env({})
DIG_VERSION = ok(stderr="DiG 9.20.30-1~deb13u1-Debian\n")


def dig_answers(probe_result):
    def respond(args):
        return DIG_VERSION if "-v" in args else probe_result

    return respond


def neo4j_ok(settings, timeout, on_wait):
    return "Neo4j 5.26.0 community"


def neo4j_down(settings, timeout, on_wait):
    raise Neo4jUnavailableError("Neo4j is not reachable at bolt://localhost:7687")


def doctor(runner, check_neo4j=neo4j_ok, resolver=None):
    return run_doctor(
        runner=runner,
        settings=SETTINGS,
        explicit_resolver=resolver,
        check_neo4j=check_neo4j,
        neo4j_timeout=1.0,
    )


def statuses(report):
    return {check.name: check.status for check in report.checks}


def test_everything_available():
    report = doctor(FakeRunner(dig_answers(NOERROR)))

    assert statuses(report) == {
        "dig": CheckStatus.OK,
        "resolver": CheckStatus.OK,
        "neo4j": CheckStatus.OK,
    }
    assert report.ok
    details = {check.name: check.detail for check in report.checks}
    assert "9.20.30" in details["dig"]
    assert details["resolver"] == "1.1.1.1 (default)"
    assert "5.26" in details["neo4j"]


def test_resolver_fallback_is_a_warning_not_a_failure():
    report = doctor(FakeRunner(dig_answers(UNREACHABLE)))

    assert statuses(report)["resolver"] is CheckStatus.WARN
    assert report.ok


def test_missing_dig_fails_and_skips_the_resolver_check():
    runner = FakeRunner(lambda args: CommandNotFoundError("dig"))

    report = doctor(runner)

    assert statuses(report)["dig"] is CheckStatus.FAIL
    assert statuses(report)["resolver"] is CheckStatus.FAIL
    assert "dig" in report.checks[1].detail
    assert len(runner.calls) == 1
    assert not report.ok


def test_unreachable_explicit_resolver_fails():
    report = doctor(FakeRunner(dig_answers(UNREACHABLE)), resolver="8.8.8.8")

    assert statuses(report)["resolver"] is CheckStatus.FAIL
    assert not report.ok


def test_invalid_explicit_resolver_fails():
    report = doctor(FakeRunner(dig_answers(NOERROR)), resolver="not-an-ip")

    assert statuses(report)["resolver"] is CheckStatus.FAIL


def test_neo4j_down_fails_but_other_checks_still_run():
    report = doctor(FakeRunner(dig_answers(NOERROR)), check_neo4j=neo4j_down)

    assert statuses(report) == {
        "dig": CheckStatus.OK,
        "resolver": CheckStatus.OK,
        "neo4j": CheckStatus.FAIL,
    }
    assert "bolt://localhost:7687" in report.checks[2].detail
    assert not report.ok
