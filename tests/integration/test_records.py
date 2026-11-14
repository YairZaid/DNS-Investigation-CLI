"""Reading back what DNS returned for each scanned hostname, as `records` shows it."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from dnsinv.collection.model import LookupStatus
from dnsinv.config import Settings
from dnsinv.graph.records import load_records
from dnsinv.graph.store import GraphStore, ScanInfo, open_store
from tests.builders import make_scan

pytestmark = pytest.mark.integration

SCANS = [
    make_scan(
        "www.example.com",
        apex="example.com",
        cname=["edge.example-cdn.net", "e1.cdn.net"],
        a=["192.0.2.1", "192.0.2.2"],
    ),
    make_scan(
        "example.com",
        apex="example.com",
        is_input=False,
        a=["192.0.2.9"],
        mx=["mx1.mail.example", "mx2.mail.example"],
        txt=["v=spf1 -all", "hello"],
    ),
    make_scan("gone.example.org", apex="example.org", end_status=LookupStatus.NXDOMAIN),
    make_scan("nullmx.example.net", apex="example.net", mx=["."]),
]


@pytest.fixture(scope="module")
def store() -> Iterator[GraphStore]:
    with open_store(Settings.from_env(os.environ), timeout=30.0) as graph:
        graph.ensure_schema()
        info = ScanInfo("known.txt", datetime(2026, 11, 14, tzinfo=UTC), "test", len(SCANS))
        graph.save_scan(info, SCANS, replace=True)
        yield graph


def by_name(store, **kwargs):
    return {r.name: r for r in load_records(store, **kwargs)}


def test_every_scanned_hostname_is_listed_apex_first_within_its_group(store):
    names = [r.name for r in load_records(store)]

    assert names == [
        "example.com",
        "www.example.com",
        "nullmx.example.net",
        "gone.example.org",
    ]


def test_chain_links_that_were_not_scanned_are_not_rows(store):
    assert "e1.cdn.net" not in by_name(store)


def test_input_and_apex_are_marked(store):
    rows = by_name(store)

    assert rows["www.example.com"].is_input
    assert not rows["example.com"].is_input
    assert rows["example.com"].is_apex


def test_addresses_are_followed_through_the_cname_chain(store):
    row = by_name(store)["www.example.com"]

    assert row.chain == ["www.example.com", "edge.example-cdn.net", "e1.cdn.net"]
    assert row.values("A") == ["192.0.2.1", "192.0.2.2"]
    assert {r.owner for r in row.records if r.rtype == "A"} == {"e1.cdn.net"}


def test_mail_and_txt_with_priority_and_order(store):
    row = by_name(store)["example.com"]

    assert row.values("MX") == ["10 mx1.mail.example", "20 mx2.mail.example"]
    assert row.values("TXT") == ["hello", "v=spf1 -all"]
    assert row.values("A") == ["192.0.2.9"]


def test_cname_column_shows_the_chain_after_the_name(store):
    assert by_name(store)["www.example.com"].values("CNAME") == [
        "edge.example-cdn.net",
        "e1.cdn.net",
    ]
    assert by_name(store)["example.com"].values("CNAME") == []


def test_null_mx_is_shown_as_such(store):
    assert by_name(store)["nullmx.example.net"].values("MX") == ["0 . (accepts no mail)"]


def test_status_of_a_name_that_does_not_exist(store):
    row = by_name(store)["gone.example.org"]

    assert row.status == "NXDOMAIN"
    assert row.records == []


def test_one_hostname(store):
    rows = load_records(store, hostname="example.com")

    assert [r.name for r in rows] == ["example.com"]


def test_unknown_hostname_gives_nothing(store):
    assert load_records(store, hostname="nope.example") == []


def test_only_input_hostnames(store):
    names = {r.name for r in load_records(store, include_apex=False)}

    assert names == {"www.example.com", "gone.example.org", "nullmx.example.net"}
