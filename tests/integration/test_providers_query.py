"""providers (and the platform column of shared-infra) on a known graph.

www.alpha.com  ──CNAME──▶ a.cloudfront.net            (Amazon CloudFront)
alpha.com      ──MX─────▶ alpha-com.mail.protection.outlook.com (Microsoft 365)
               ──TXT────  v=spf1 include:_spf.google.com -all    (Google Workspace, spf)
               ──TXT────  google-site-verification=abc           (Google Search Console)
shop.beta.org  ──A──────▶ 104.16.1.1                  (Cloudflare)
beta.org       ──TXT────  MS=ms99                     (Microsoft 365, txt)
plain.gamma.io ──A──────▶ 192.0.2.7                   (nothing known)
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
    make_scan("www.alpha.com", apex="alpha.com", cname=["a.cloudfront.net"], a=["192.0.2.1"]),
    make_scan(
        "alpha.com",
        apex="alpha.com",
        is_input=False,
        mx=["alpha-com.mail.protection.outlook.com"],
        txt=["v=spf1 include:_spf.google.com -all", "google-site-verification=abc"],
    ),
    make_scan("shop.beta.org", apex="beta.org", a=["104.16.1.1"]),
    make_scan("beta.org", apex="beta.org", is_input=False, txt=["MS=ms99"]),
    make_scan("plain.gamma.io", apex="gamma.io", a=["192.0.2.7"]),
    make_scan("cdn-a.delta.net", apex="delta.net", a=["104.16.1.1"]),
]


@pytest.fixture(scope="module")
def store() -> Iterator[GraphStore]:
    with open_store(Settings.from_env(os.environ), timeout=30.0) as graph:
        graph.ensure_schema()
        info = ScanInfo("known.txt", datetime(2026, 11, 14, tzinfo=UTC), "test", len(SCANS))
        graph.save_scan(info, SCANS, replace=True, enrichment=enrich(SCANS, load_catalog()))
        yield graph


def providers(store, **options):
    result = run_query(store, get_query("providers"), options)
    return {(row["hostname"], row["provider"]): row for row in result.rows}


def test_every_provider_of_every_input_hostname_with_its_evidence(store):
    rows = providers(store)

    assert set(rows) == {
        ("www.alpha.com", "Amazon CloudFront"),
        ("www.alpha.com", "Microsoft 365"),
        ("www.alpha.com", "Google Workspace"),
        ("www.alpha.com", "Google Search Console"),
        ("shop.beta.org", "Cloudflare"),
        ("shop.beta.org", "Microsoft 365"),
        ("cdn-a.delta.net", "Cloudflare"),
    }
    assert rows["www.alpha.com", "Amazon CloudFront"]["evidence"] == ["cname: a.cloudfront.net"]
    assert rows["www.alpha.com", "Microsoft 365"]["evidence"] == [
        "mx: alpha-com.mail.protection.outlook.com"
    ]
    assert rows["www.alpha.com", "Google Workspace"]["evidence"] == ["spf: include:_spf.google.com"]
    assert rows["shop.beta.org", "Microsoft 365"]["evidence"] == ["txt: MS=ms99"]
    assert rows["shop.beta.org", "Cloudflare"]["evidence"] == ["ip: 104.16.1.1"]


def test_rows_carry_category_and_where_the_evidence_was_found(store):
    rows = providers(store)

    assert rows["www.alpha.com", "Amazon CloudFront"]["category"] == "cdn"
    assert rows["www.alpha.com", "Amazon CloudFront"]["found_on"] == ["www.alpha.com"]
    assert rows["www.alpha.com", "Microsoft 365"]["found_on"] == ["alpha.com"]


def test_hostname_without_known_providers_is_absent(store):
    assert not any(host == "plain.gamma.io" for host, _ in providers(store))


def test_filter_by_provider_answers_who_uses_it(store):
    rows = providers(store, provider="microsoft")

    assert set(rows) == {("www.alpha.com", "Microsoft 365"), ("shop.beta.org", "Microsoft 365")}


def test_filter_by_category(store):
    rows = providers(store, category="cdn")

    assert {provider for _, provider in rows} == {"Amazon CloudFront", "Cloudflare"}


def test_graph_version_shows_exactly_the_table_results(store):
    query = get_query("providers")
    table_pairs = set(providers(store))

    graph_pairs = set()
    for record in store.query(query.cypher(graph=True), **query.params({})):
        path = record["path"]
        graph_pairs.add((path.start_node["name"], path.end_node["name"]))

    assert graph_pairs == table_pairs


def test_shared_infra_names_the_platform_behind_a_shared_asset(store):
    rows = {row["asset"]: row for row in run_query(store, get_query("shared-infra"), {}).rows}

    assert rows["104.16.1.1"]["platform"] == ["Cloudflare"]
    assert rows["104.16.1.1"]["shared_platform"] is True
