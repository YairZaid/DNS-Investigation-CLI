"""Scanning Hostnames: one dig process each, statuses and CNAME Chains derived from real output."""

from pathlib import Path

import pytest

from dnsinv.collection.model import LookupStatus, RecordType, ScanTarget
from dnsinv.collection.resolver import ResolverChoice, ResolverSource
from dnsinv.collection.runner import CommandNotFoundError, CommandResult, CommandTimeoutError
from dnsinv.collection.scanner import plan_targets, scan_all, scan_hostname
from dnsinv.hostnames import InputHostname
from tests.fakes import FakeRunner, ok

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dig"
RESOLVER = ResolverChoice("1.1.1.1", ResolverSource.DEFAULT)
NOERROR, NXDOMAIN, SERVFAIL, TIMEOUT = (
    LookupStatus.NOERROR,
    LookupStatus.NXDOMAIN,
    LookupStatus.SERVFAIL,
    LookupStatus.TIMEOUT,
)


def fixture_text(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


def scan(fixture, hostname):
    runner = FakeRunner(lambda args: ok(fixture_text(fixture)))
    return scan_hostname(ScanTarget(hostname, is_input=True, apex=None), runner, RESOLVER)


def links(result):
    return [(link.name, link.status) for link in result.chain.links]


# ---- one hostname -----------------------------------------------------------------


def test_one_dig_process_per_hostname():
    runner = FakeRunner(lambda args: ok(fixture_text("ok")))

    result = scan_hostname(ScanTarget("ok.test.lab", True, None), runner, RESOLVER)

    assert len(runner.calls) == 1
    assert [lookup.rtype for lookup in result.lookups] == list(RecordType)
    assert result.status is NOERROR


def test_plain_hostname_has_a_chain_of_just_itself():
    result = scan("ok", "ok.test.lab")

    assert links(result) == [("ok.test.lab", NOERROR)]
    assert not result.chain.has_cname


def test_nonexistent_hostname():
    assert scan("missing", "missing.test.lab").status is NXDOMAIN


def test_records_are_collected_once_across_the_five_lookups():
    result = scan("alias", "alias.test.lab")

    cnames = [r for r in result.records if r.rtype is RecordType.CNAME]

    # The same CNAME comes back in all five answers; it is one record.
    assert [(r.owner, r.value) for r in cnames] == [("alias.test.lab", "ok.test.lab")]


# ---- CNAME chains: every link gets its own status ---------------------------------------


def test_chain_to_an_existing_target():
    result = scan("goodchain1", "goodchain1.test.lab")

    assert links(result) == [
        ("goodchain1.test.lab", NOERROR),
        ("goodchain2.test.lab", NOERROR),
        ("ok.test.lab", NOERROR),
    ]
    assert result.chain.has_cname
    assert not result.chain.is_dangling


def test_dangling_cname_the_name_exists_but_its_target_does_not():
    result = scan("unbound-xdangling", "xdangling.test.lab")

    assert result.status is NOERROR
    assert links(result) == [("xdangling.test.lab", NOERROR), ("gone.other.lab", NXDOMAIN)]
    assert result.chain.is_dangling


def test_dangling_at_the_end_of_a_longer_chain():
    result = scan("unbound-xchain", "xchain.test.lab")

    assert links(result) == [
        ("xchain.test.lab", NOERROR),
        ("xdangling.test.lab", NOERROR),
        ("gone.other.lab", NXDOMAIN),
    ]


def test_target_that_exists_without_an_address_is_not_dangling():
    result = scan("unbound-xnodata", "xnodata.test.lab")

    assert links(result) == [("xnodata.test.lab", NOERROR), ("txtonly.other.lab", NOERROR)]
    assert not result.chain.is_dangling


def test_cname_loop_is_reported_and_does_not_hang():
    result = scan("loop1", "loop1.test.lab")

    assert result.chain.loop
    assert [name for name, _ in links(result)] == ["loop1.test.lab", "loop2.test.lab"]
    assert links(result)[-1][1] is SERVFAIL
    assert not result.chain.is_dangling


def test_one_failed_lookup_does_not_hide_what_the_others_found():
    # Real case: the A lookup was SERVFAIL, the other three returned the CNAME with NOERROR.
    result = scan("dangling-out", "dangling-out.test.lab")

    assert result.lookup(RecordType.A).status is SERVFAIL
    assert links(result) == [
        ("dangling-out.test.lab", NOERROR),
        ("this-name-does-not-exist-zz91.example.com", NOERROR),
    ]


def test_chain_longer_than_the_limit_is_cut_and_marked():
    hops = "".join(f"n{i}.example.com.\t60\tIN\tCNAME\tn{i + 1}.example.com.\n" for i in range(15))
    text = (
        ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n"
        ";; QUESTION SECTION:\n;n0.example.com.\t\tIN\tA\n;; ANSWER SECTION:\n" + hops
    )
    runner = FakeRunner(lambda args: ok(text))

    result = scan_hostname(ScanTarget("n0.example.com", True, None), runner, RESOLVER)

    assert len(result.chain.links) == 11  # the name itself + 10 hops
    assert result.chain.truncated


# ---- failures -----------------------------------------------------------------------


def test_unreachable_resolver_marks_every_lookup_timeout():
    runner = FakeRunner(lambda args: CommandResult(9, fixture_text("unreachable"), ""))

    result = scan_hostname(ScanTarget("a.test.lab", True, None), runner, RESOLVER)

    assert {lookup.status for lookup in result.lookups} == {TIMEOUT}
    assert result.status is TIMEOUT


def test_question_without_an_answer_block_is_timeout_and_the_rest_is_kept():
    # dig prints nothing identifiable for a question that timed out, so drop the MX block
    # from a real answer and add the line dig prints instead.
    blocks = fixture_text("ok").split(";; Got answer:")
    without_mx = ";; Got answer:".join(b for b in blocks if "IN\tMX" not in b)
    text = without_mx + ";; communications error to 1.1.1.1#53: timed out\n"
    runner = FakeRunner(lambda args: CommandResult(9, text, ""))

    result = scan_hostname(ScanTarget("ok.test.lab", True, None), runner, RESOLVER)

    assert result.lookup(RecordType.MX).status is TIMEOUT
    assert result.lookup(RecordType.A).status is NOERROR
    assert result.status is NOERROR


def test_dig_process_that_hangs_is_a_timeout_not_a_crash():
    runner = FakeRunner(lambda args: CommandTimeoutError(["dig"], 40.0))

    result = scan_hostname(ScanTarget("slow.example.com", True, None), runner, RESOLVER)

    assert result.status is TIMEOUT


def test_dig_failing_without_output_is_an_error_status():
    runner = FakeRunner(lambda args: CommandResult(1, "", "dig: some usage problem"))

    result = scan_hostname(ScanTarget("x.example.com", True, None), runner, RESOLVER)

    assert result.status is LookupStatus.ERROR


def test_missing_dig_stops_the_scan():
    runner = FakeRunner(lambda args: CommandNotFoundError("dig"))

    with pytest.raises(CommandNotFoundError):
        scan_all([ScanTarget("a.example.com", True, None)], runner, RESOLVER)


# ---- many hostnames -------------------------------------------------------------------


def by_name(args):
    name = args[-2]
    fixture = {"ok.test.lab": "ok", "alias.test.lab": "alias", "missing.test.lab": "missing"}
    return ok(fixture_text(fixture[name]))


def test_scan_all_keeps_input_order_and_reports_progress():
    targets = [ScanTarget(n, True, None) for n in ("ok.test.lab", "missing.test.lab")] + [
        ScanTarget("alias.test.lab", False, None)
    ]
    done: list[str] = []

    results = scan_all(
        targets, FakeRunner(by_name), RESOLVER, workers=3, on_done=lambda s: done.append(s.name)
    )

    assert [r.name for r in results] == ["ok.test.lab", "missing.test.lab", "alias.test.lab"]
    assert [r.status for r in results] == [NOERROR, NXDOMAIN, NOERROR]
    assert sorted(done) == sorted(r.name for r in results)


# ---- which names get scanned ---------------------------------------------------------------


def host(name, apex, line=1):
    return InputHostname(name, apex, line)


def test_apex_domain_is_scanned_too():
    targets = plan_targets([host("blog.example.com", "example.com")], include_apex=True)

    assert targets == [
        ScanTarget("blog.example.com", is_input=True, apex="example.com"),
        ScanTarget("example.com", is_input=False, apex="example.com"),
    ]


def test_no_apex_flag_scans_only_the_input():
    targets = plan_targets([host("blog.example.com", "example.com")], include_apex=False)

    assert [t.name for t in targets] == ["blog.example.com"]


def test_shared_apex_is_scanned_once():
    targets = plan_targets(
        [host("a.example.com", "example.com"), host("b.example.com", "example.com")],
        include_apex=True,
    )

    assert [t.name for t in targets] == ["a.example.com", "example.com", "b.example.com"]


def test_apex_that_is_also_an_input_stays_marked_as_input():
    targets = plan_targets(
        [host("www.example.com", "example.com"), host("example.com", "example.com")],
        include_apex=True,
    )

    assert targets == [
        ScanTarget("www.example.com", True, "example.com"),
        ScanTarget("example.com", True, "example.com"),
    ]


def test_hostname_without_an_apex_is_scanned_alone():
    assert plan_targets([host("co.il", None)], include_apex=True) == [
        ScanTarget("co.il", True, None)
    ]
