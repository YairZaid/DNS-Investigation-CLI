"""Real dig against real resolvers. Needs internet; excluded from CI (`pytest -m live`)."""

import pytest

from dnsinv.collection.resolver import ResolverSource, select_resolver
from dnsinv.collection.runner import run_subprocess

pytestmark = pytest.mark.live


def test_default_resolver_answers_from_this_network():
    choice = select_resolver(run_subprocess)

    assert choice.source is ResolverSource.DEFAULT


def test_unroutable_explicit_resolver_is_reported():
    from dnsinv.collection.resolver import ResolverUnreachableError

    with pytest.raises(ResolverUnreachableError):
        select_resolver(run_subprocess, explicit="192.0.2.1")
