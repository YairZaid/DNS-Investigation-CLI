"""Providers in the graph: OPERATED_BY for infrastructure, USES_PROVIDER for a domain's claims."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from dnsinv.config import Settings
from dnsinv.enrichment.catalog import load_catalog
from dnsinv.enrichment.enrich import enrich
from dnsinv.graph.store import GraphStore, ScanInfo, open_store
from tests.builders import make_scan

pytestmark = pytest.mark.integration

DAY_1 = datetime(2026, 11, 14, tzinfo=UTC)
DAY_2 = datetime(2026, 11, 15, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[GraphStore]:
    with open_store(Settings.from_env(os.environ), timeout=30.0) as graph:
        graph.ensure_schema()
        graph.clear()
        yield graph


def save(store, scans, *, replace=True, at=DAY_1):
    info = ScanInfo("urls.txt", at, "test", len(scans))
    store.save_scan(info, scans, replace=replace, enrichment=enrich(scans, load_catalog()))


def rows(store, cypher):
    return [record.data() for record in store.query(cypher)]


def test_infrastructure_is_operated_by_its_provider(store):
    save(
        store,
        [
            make_scan("www.example.com", cname=["x.cloudfront.net"], a=["192.0.2.1"]),
            make_scan("example.com", a=["104.16.1.1"], mx=["ex.mail.protection.outlook.com"]),
        ],
    )

    operated = rows(
        store,
        "MATCH (a)-[:OPERATED_BY]->(p:Provider) "
        "RETURN coalesce(a.name, a.address) AS asset, p.name AS provider ORDER BY asset",
    )

    assert operated == [
        {"asset": "104.16.1.1", "provider": "Cloudflare"},
        {"asset": "ex.mail.protection.outlook.com", "provider": "Microsoft 365"},
        {"asset": "x.cloudfront.net", "provider": "Amazon CloudFront"},
    ]


def test_provider_nodes_carry_category_and_shared_platform(store):
    save(store, [make_scan("example.com", a=["104.16.1.1"])])

    provider = rows(
        store, "MATCH (p:Provider) RETURN p.name AS name, p.category AS c, p.shared_platform AS s"
    )

    assert provider == [{"name": "Cloudflare", "c": "cdn", "s": True}]


def test_claims_hang_off_the_txt_owner_with_their_evidence(store):
    save(
        store,
        [make_scan("example.com", txt=["v=spf1 include:_spf.google.com -all", "MS=ms42"])],
    )

    claims = rows(
        store,
        "MATCH (h:Hostname)-[u:USES_PROVIDER]->(p:Provider) "
        "RETURN h.name AS owner, p.name AS provider, u.evidence AS evidence, u.detail AS detail "
        "ORDER BY provider",
    )

    assert claims == [
        {
            "owner": "example.com",
            "provider": "Google Workspace",
            "evidence": "spf",
            "detail": "include:_spf.google.com",
        },
        {
            "owner": "example.com",
            "provider": "Microsoft 365",
            "evidence": "txt",
            "detail": "MS=ms42",
        },
    ]


def test_txt_records_get_their_kind(store):
    save(store, [make_scan("example.com", txt=["v=spf1 -all", "google-site-verification=a", "x"])])

    kinds = rows(store, "MATCH (t:TxtRecord) RETURN t.value AS v, t.kind AS kind ORDER BY v")

    assert kinds == [
        {"v": "google-site-verification=a", "kind": "verification"},
        {"v": "v=spf1 -all", "kind": "spf"},
        {"v": "x", "kind": "other"},
    ]


def test_rescan_drops_claims_that_disappeared_and_unused_providers(store):
    save(store, [make_scan("example.com", txt=["MS=ms42"])])
    save(store, [make_scan("example.com", txt=["hello"])], replace=False, at=DAY_2)

    assert rows(store, "MATCH ()-[u:USES_PROVIDER]->() RETURN count(u) AS n") == [{"n": 0}]
    assert rows(store, "MATCH (p:Provider) RETURN count(p) AS n") == [{"n": 0}]


def test_assets_kept_alive_only_by_their_provider_are_removed(store):
    # Day 1: CNAME to CloudFront. Day 2: the CNAME is gone. The CloudFront name is now only
    # linked to its Provider, which must not keep it in the graph.
    save(store, [make_scan("www.example.com", cname=["x.cloudfront.net"], a=["192.0.2.1"])])
    save(store, [make_scan("www.example.com", a=["192.0.2.1"])], replace=False, at=DAY_2)

    names = rows(store, "MATCH (h:Hostname) RETURN h.name AS name ORDER BY name")

    assert names == [{"name": "www.example.com"}]
    assert rows(store, "MATCH (p:Provider) RETURN count(p) AS n") == [{"n": 0}]


def test_addresses_only_an_unreachable_name_led_to_are_removed_too(store):
    save(store, [make_scan("www.example.com", cname=["x.cloudfront.net"], a=["192.0.2.1"])])
    save(store, [make_scan("www.example.com", a=["192.0.2.9"])], replace=False, at=DAY_2)

    addresses = rows(store, "MATCH (ip:IP) RETURN ip.address AS a")

    assert addresses == [{"a": "192.0.2.9"}]


def test_save_without_enrichment_still_works(store):
    store.save_scan(ScanInfo("u", DAY_1, "t", 1), [make_scan("example.com", a=["104.16.1.1"])],
                    replace=True)  # fmt: skip

    assert rows(store, "MATCH (p:Provider) RETURN count(p) AS n") == [{"n": 0}]
