"""outage-impact on a graph whose answer is known in advance.

cdn-only.alpha.com    ──CNAME──▶ x.cloudfront.net ──A──▶ 192.0.2.1   CloudFront: website down
alpha.com (apex)      ──MX─────▶ aspmx.l.google.com                  Google: mail in down
                      ──TXT────  v=spf1 include:sendgrid.net -all     SendGrid: mail out
cf.gamma.io           ──A──────▶ 104.16.1.2                          Cloudflare only: down
multi.beta.org        ──A──────▶ 104.16.1.1 (Cloudflare), 192.0.2.5  Cloudflare: partial
beta.org (apex)       ──MX─────▶ x.mail.protection.outlook.com,      Microsoft 365: mail in
                                 mx.other.example                    partial
verify-only.delta.net ──A──────▶ 192.0.2.9
delta.net (apex)      ──TXT────  google-site-verification=abc, MS=ms2  tokens only: no impact
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from dnsinv.config import Settings
from dnsinv.enrichment.catalog import load_catalog
from dnsinv.enrichment.enrich import enrich
from dnsinv.graph.store import GraphStore, ScanInfo, open_store
from dnsinv.queries import get_query
from dnsinv.queries.runner import run_query
from tests.builders import make_scan

pytestmark = pytest.mark.integration

SCANS = [
    make_scan("cdn-only.alpha.com", apex="alpha.com", cname=["x.cloudfront.net"], a=["192.0.2.1"]),
    make_scan(
        "alpha.com",
        apex="alpha.com",
        is_input=False,
        mx=["aspmx.l.google.com"],
        txt=["v=spf1 include:sendgrid.net -all"],
    ),
    make_scan("cf.gamma.io", apex="gamma.io", a=["104.16.1.2"]),
    make_scan("multi.beta.org", apex="beta.org", a=["104.16.1.1", "192.0.2.5"]),
    make_scan(
        "beta.org",
        apex="beta.org",
        is_input=False,
        mx=["x.mail.protection.outlook.com", "mx.other.example"],
    ),
    make_scan("verify-only.delta.net", apex="delta.net", a=["192.0.2.9"]),
    make_scan(
        "delta.net",
        apex="delta.net",
        is_input=False,
        txt=["google-site-verification=abc", "MS=ms2"],
    ),
]


@pytest.fixture(scope="module")
def store() -> Iterator[GraphStore]:
    with open_store(Settings.from_env(os.environ), timeout=30.0) as graph:
        graph.ensure_schema()
        info = ScanInfo("known.txt", datetime(2026, 11, 15, tzinfo=UTC), "test", len(SCANS))
        graph.save_scan(info, SCANS, replace=True, enrichment=enrich(SCANS, load_catalog()))
        yield graph


def impact(store, **options):
    result = run_query(store, get_query("outage-impact"), options)
    return {row["provider"]: row for row in result.rows}


def test_a_cname_into_the_provider_takes_the_website_down(store):
    row = impact(store)["Amazon CloudFront"]

    assert row["website"] == ["cdn-only.alpha.com"]
    assert row["fully_down"] == 1


def test_all_addresses_at_one_provider_is_down_some_is_partial(store):
    row = impact(store)["Cloudflare"]

    assert row["website"] == ["cf.gamma.io", "multi.beta.org (partial)"]
    assert row["hostnames"] == 2
    assert row["fully_down"] == 1


def test_mail_in_follows_the_mail_servers_of_the_apex_domain(store):
    rows = impact(store)

    assert rows["Google Workspace"]["mail_in"] == ["cdn-only.alpha.com"]
    assert rows["Microsoft 365"]["mail_in"] == ["multi.beta.org (partial)"]
    assert rows["Microsoft 365"]["fully_down"] == 0


def test_an_spf_include_means_outgoing_mail_only(store):
    row = impact(store)["SendGrid"]

    assert row["mail_out"] == ["cdn-only.alpha.com"]
    assert row["website"] == []
    assert row["mail_in"] == []
    assert row["fully_down"] == 0


def test_verification_tokens_are_not_a_dependency(store):
    rows = impact(store)

    assert "Google Search Console" not in rows
    # Microsoft 365 is listed because of beta.org's mail servers, not delta.net's MS= token.
    assert rows["Microsoft 365"]["hostnames"] == 1
    assert all("verify-only.delta.net" not in str(row) for row in rows.values())


def test_counts_organisations(store):
    rows = impact(store)

    assert rows["Cloudflare"]["organisations"] == 2
    assert rows["Google Workspace"]["organisations"] == 1


def test_providers_whose_outage_takes_most_down_come_first(store):
    result = run_query(store, get_query("outage-impact"), {})

    assert [row["provider"] for row in result.rows] == [
        "Cloudflare",
        "Amazon CloudFront",
        "Google Workspace",
        "Microsoft 365",
        "SendGrid",
    ]


def test_provider_filter_is_a_case_insensitive_substring(store):
    assert set(impact(store, provider="FLARE")) == {"Cloudflare"}
    assert set(impact(store, provider="cloud")) == {"Cloudflare", "Amazon CloudFront"}


def test_graph_version_shows_exactly_the_table_results(store):
    query = get_query("outage-impact")
    table_pairs = set()
    for row in run_query(store, query, {}).rows:
        for column in ("website", "mail_in", "mail_out"):
            for hostname in row[column]:
                table_pairs.add((hostname.removesuffix(" (partial)"), row["provider"]))

    graph_pairs = set()
    for record in store.query(query.cypher(graph=True), **query.params({})):
        path = record["path"]
        graph_pairs.add((path.start_node["name"], path.end_node["name"]))

    assert graph_pairs == table_pairs
