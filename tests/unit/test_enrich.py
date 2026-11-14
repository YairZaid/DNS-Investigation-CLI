"""Enrichment: which providers a Scan reveals, and from what evidence."""

import pytest

from dnsinv.enrichment.catalog import load_catalog
from dnsinv.enrichment.enrich import (
    OperatedAsset,
    ProviderClaim,
    TxtKind,
    classify_txt,
    enrich,
    spf_targets,
)
from tests.builders import make_scan


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_cname_chain_links_are_operated_by_their_provider(catalog):
    scan = make_scan(
        "www.example.com",
        cname=["www.example.com-c-3.edgekey.net", "e1.dscb.akamaiedge.net"],
        a=["192.0.2.1"],
    )

    result = enrich([scan], catalog)

    assert set(result.operated) == {
        OperatedAsset("hostname", "www.example.com-c-3.edgekey.net", "Akamai"),
        OperatedAsset("hostname", "e1.dscb.akamaiedge.net", "Akamai"),
    }


def test_the_scanned_name_itself_is_never_operated_by_a_cname_provider(catalog):
    # Someone scanning octocat.github.io directly is asking about it, not aliasing to it.
    result = enrich([make_scan("octocat.github.io", a=["185.199.108.153"])], catalog)

    assert OperatedAsset("hostname", "octocat.github.io", "GitHub Pages") not in result.operated
    assert OperatedAsset("ip", "185.199.108.153", "GitHub Pages") in result.operated


def test_addresses_inside_a_provider_range(catalog):
    result = enrich([make_scan("example.com", a=["104.16.1.1", "192.0.2.1"])], catalog)

    assert result.operated == [OperatedAsset("ip", "104.16.1.1", "Cloudflare")]


def test_mail_exchanges(catalog):
    scan = make_scan("example.com", mx=["example-com.mail.protection.outlook.com"])

    result = enrich([scan], catalog)

    assert result.operated == [
        OperatedAsset("hostname", "example-com.mail.protection.outlook.com", "Microsoft 365")
    ]


def test_spf_includes_and_redirect_become_claims_by_the_record_owner(catalog):
    scan = make_scan(
        "example.com",
        txt=[
            "v=spf1 include:_spf.google.com include:sendgrid.net redirect=_spf.salesforce.com -all"
        ],
    )

    claims = enrich([scan], catalog).claims

    assert set(claims) == {
        ProviderClaim("example.com", "Google Workspace", "spf", "include:_spf.google.com"),
        ProviderClaim("example.com", "SendGrid", "spf", "include:sendgrid.net"),
        ProviderClaim("example.com", "Salesforce", "spf", "redirect=_spf.salesforce.com"),
    }


def test_verification_tokens_become_claims(catalog):
    scan = make_scan("example.com", txt=["google-site-verification=abc", "MS=ms123456"])

    claims = enrich([scan], catalog).claims

    assert set(claims) == {
        ProviderClaim(
            "example.com", "Google Search Console", "txt", "google-site-verification=abc"
        ),
        ProviderClaim("example.com", "Microsoft 365", "txt", "MS=ms123456"),
    }


def test_unknown_spf_include_is_not_a_claim(catalog):
    scan = make_scan("example.com", txt=["v=spf1 include:_spf.unknown-vendor.example -all"])

    assert enrich([scan], catalog).claims == []


def test_long_tokens_are_shortened_in_the_detail(catalog):
    token = "google-site-verification=" + "x" * 200
    (claim,) = enrich([make_scan("example.com", txt=[token])], catalog).claims

    assert len(claim.detail) <= 80
    assert claim.detail.endswith("...")


def test_same_evidence_from_two_scans_is_reported_once(catalog):
    scans = [
        make_scan("www.example.com", cname=["x.cloudfront.net"]),
        make_scan("img.example.com", cname=["x.cloudfront.net"]),
    ]

    result = enrich(scans, catalog)

    assert result.operated == [OperatedAsset("hostname", "x.cloudfront.net", "Amazon CloudFront")]


def test_txt_kinds_are_reported_for_every_txt_record(catalog):
    scan = make_scan("example.com", txt=["v=spf1 -all", "google-site-verification=a", "hello"])

    kinds = enrich([scan], catalog).txt_kinds

    assert kinds == {
        ("example.com", "v=spf1 -all"): TxtKind.SPF,
        ("example.com", "google-site-verification=a"): TxtKind.VERIFICATION,
        ("example.com", "hello"): TxtKind.OTHER,
    }


def test_providers_used_are_listed_once(catalog):
    scan = make_scan("example.com", a=["104.16.1.1"], txt=["v=spf1 include:_spf.google.com -all"])

    names = [p.name for p in enrich([scan], catalog).providers]

    assert names == ["Cloudflare", "Google Workspace"]


# ---- SPF and TXT parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "targets"),
    [
        ("v=spf1 include:_spf.google.com ~all", [("include", "_spf.google.com")]),
        ("V=SPF1 INCLUDE:_SPF.Google.COM -ALL", [("include", "_spf.google.com")]),
        (
            "v=spf1 +include:a.example ?include:b.example -all",
            [("include", "a.example"), ("include", "b.example")],
        ),
        ("v=spf1 redirect=_spf.example.net", [("redirect", "_spf.example.net")]),
        ("v=spf1 ip4:192.0.2.0/24 mx a -all", []),
        ("v=spf1 include: -all", []),
        ("not spf include:x.example", []),
    ],
)
def test_spf_targets(value, targets):
    assert spf_targets(value) == targets


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("v=spf1 -all", TxtKind.SPF),
        ("  v=spf1 include:x -all", TxtKind.SPF),
        ("v=spf10", TxtKind.OTHER),
        ("v=DKIM1; k=rsa; p=abc", TxtKind.DKIM),
        ("some-vendor-domain-verification=abc", TxtKind.VERIFICATION),
        ("random", TxtKind.OTHER),
    ],
)
def test_classify_txt_without_catalog_matches(catalog, value, kind):
    assert classify_txt(value, catalog) is kind
