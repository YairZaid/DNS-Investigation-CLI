import pytest

from dnsinv.collection.resolver import (
    DEFAULT_RESOLVER,
    InvalidResolverError,
    ResolverSource,
    ResolverUnreachableError,
    select_resolver,
)
from dnsinv.collection.runner import CommandNotFoundError
from tests.fakes import NOERROR, TIMEOUT, UNREACHABLE, FakeRunner, ok


def server_of(args):
    return next((a[1:] for a in args if a.startswith("@")), None)


def test_default_resolver_is_used_when_it_answers():
    runner = FakeRunner(lambda args: NOERROR)

    choice = select_resolver(runner)

    assert choice.address == DEFAULT_RESOLVER == "1.1.1.1"
    assert choice.source is ResolverSource.DEFAULT
    assert choice.warning is None
    assert len(runner.calls) == 1
    assert server_of(runner.calls[0]) == "1.1.1.1"


def test_falls_back_to_system_resolver_with_warning_when_default_unreachable():
    runner = FakeRunner(lambda args: UNREACHABLE if server_of(args) else NOERROR)

    choice = select_resolver(runner)

    assert choice.address is None
    assert choice.source is ResolverSource.SYSTEM
    assert choice.warning is not None
    assert "1.1.1.1" in choice.warning


def test_falls_back_when_default_probe_times_out():
    runner = FakeRunner(lambda args: TIMEOUT)

    choice = select_resolver(runner)

    assert choice.source is ResolverSource.SYSTEM


def test_fallback_does_not_probe_the_system_resolver():
    # The system resolver is used as-is: probing it too would only add a second timeout
    # on networks where DNS is broken, and the scan reports per-lookup failures anyway.
    runner = FakeRunner(lambda args: UNREACHABLE)

    select_resolver(runner)

    assert len(runner.calls) == 1


def test_probe_needs_a_noerror_answer_not_just_any_reply():
    refused = ok(";; ->>HEADER<<- opcode: QUERY, status: REFUSED, id: 1\n")
    runner = FakeRunner(lambda args: refused)

    assert select_resolver(runner).source is ResolverSource.SYSTEM


def test_explicit_resolver_is_used_without_fallback():
    runner = FakeRunner(lambda args: NOERROR)

    choice = select_resolver(runner, explicit="8.8.8.8")

    assert choice.address == "8.8.8.8"
    assert choice.source is ResolverSource.EXPLICIT
    assert server_of(runner.calls[0]) == "8.8.8.8"


def test_explicit_resolver_that_does_not_answer_is_an_error():
    runner = FakeRunner(lambda args: UNREACHABLE)

    with pytest.raises(ResolverUnreachableError) as excinfo:
        select_resolver(runner, explicit="8.8.8.8")

    assert "8.8.8.8" in str(excinfo.value)
    assert len(runner.calls) == 1


def test_explicit_ipv6_resolver_is_accepted():
    runner = FakeRunner(lambda args: NOERROR)

    assert select_resolver(runner, explicit="2606:4700:4700::1111").address == (
        "2606:4700:4700::1111"
    )


@pytest.mark.parametrize("value", ["dns.google", "-x", "+short", "1.1.1.1; rm -rf /", ""])
def test_explicit_resolver_must_be_an_ip_address(value):
    runner = FakeRunner(lambda args: NOERROR)

    with pytest.raises(InvalidResolverError):
        select_resolver(runner, explicit=value)

    assert runner.calls == []


def test_missing_dig_is_not_mistaken_for_an_unreachable_resolver():
    runner = FakeRunner(lambda args: CommandNotFoundError("dig"))

    with pytest.raises(CommandNotFoundError):
        select_resolver(runner)


def test_choice_describes_itself_for_the_user():
    runner = FakeRunner(lambda args: NOERROR)

    assert select_resolver(runner).describe() == "1.1.1.1 (default)"
    assert select_resolver(runner, explicit="9.9.9.9").describe() == "9.9.9.9 (--resolver)"
    unreachable = FakeRunner(lambda args: UNREACHABLE)
    assert select_resolver(unreachable).describe() == "system resolver (fallback)"
