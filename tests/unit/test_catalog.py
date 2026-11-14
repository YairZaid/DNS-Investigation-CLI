"""The provider catalog: loading, validation and matching."""

import pytest

from dnsinv.enrichment.catalog import Catalog, CatalogError, load_catalog, parse_catalog


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


# ---- the bundled catalog ------------------------------------------------------------


def test_bundled_catalog_loads_with_unique_names(catalog):
    names = [p.name for p in catalog.providers]

    assert len(names) >= 25
    assert len(names) == len(set(names))


def test_every_provider_has_at_least_one_pattern(catalog):
    for provider in catalog.providers:
        assert provider.has_patterns, provider.name


def test_cdn_and_cloud_platforms_are_marked_as_shared(catalog):
    for name in ("Cloudflare", "Akamai", "Fastly", "Amazon CloudFront"):
        assert catalog.get(name).shared_platform, name
    assert not catalog.get("Microsoft 365").shared_platform


def test_takeover_fingerprints_cover_the_s3_case(catalog):
    s3 = catalog.get("Amazon S3")

    assert s3.takeover is not None
    assert s3.takeover.signal == "http"
    assert s3.takeover.marker == "NoSuchBucket"


# ---- matching ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "provider"),
    [
        ("d111111abcdef8.cloudfront.net", "Amazon CloudFront"),
        ("www.microsoft.com-c-3.edgekey.net", "Akamai"),
        ("e13678.dscb.akamaiedge.net", "Akamai"),
        ("dualstack.python.map.fastly.net", "Fastly"),
        ("octocat.github.io", "GitHub Pages"),
        ("myapp.herokuapp.com", "Heroku"),
        ("myapp.azurewebsites.net", "Microsoft Azure"),
        ("bucket.s3.amazonaws.com", "Amazon S3"),
        ("bucket.s3-website-us-east-1.amazonaws.com", "Amazon S3"),
        ("bucket.s3-website.eu-west-1.amazonaws.com", "Amazon S3"),
        ("bucket.s3.us-east-2.amazonaws.com", "Amazon S3"),
    ],
)
def test_cname_targets(catalog, name, provider):
    assert [p.name for p in catalog.match_cname(name)] == [provider]


@pytest.mark.parametrize(
    "name",
    [
        "evilcloudfront.net",  # a suffix match must start at a label boundary
        "cloudfront.net.attacker.com",
        "amazonaws.com",
        "example.com",
    ],
)
def test_cname_non_matches(catalog, name):
    assert catalog.match_cname(name) == []


@pytest.mark.parametrize(
    ("exchange", "provider"),
    [
        ("github-com.mail.protection.outlook.com", "Microsoft 365"),
        ("aspmx.l.google.com", "Google Workspace"),
        ("smtp.google.com", "Google Workspace"),
        ("mx1.example.pphosted.com", "Proofpoint"),
    ],
)
def test_mail_exchanges(catalog, exchange, provider):
    assert [p.name for p in catalog.match_mx(exchange)] == [provider]


@pytest.mark.parametrize(
    ("include", "provider"),
    [
        ("_spf.google.com", "Google Workspace"),
        ("spf.protection.outlook.com", "Microsoft 365"),
        ("sendgrid.net", "SendGrid"),
        ("amazonses.com", "Amazon SES"),
        ("servers.mcsv.net", "Mailchimp"),
    ],
)
def test_spf_includes(catalog, include, provider):
    assert [p.name for p in catalog.match_spf(include)] == [provider]


@pytest.mark.parametrize(
    ("value", "provider"),
    [
        ("google-site-verification=abc123", "Google Search Console"),
        ("MS=ms12345678", "Microsoft 365"),
        ("facebook-domain-verification=xyz", "Meta"),
        ("atlassian-domain-verification=xyz", "Atlassian"),
        ("docusign=05958488-4752-4ef2-95eb-aa7ba8a3bd0e", "DocuSign"),
        ("apple-domain-verification=xyz", "Apple"),
    ],
)
def test_txt_verification_tokens(catalog, value, provider):
    assert [p.name for p in catalog.match_txt(value)] == [provider]


def test_txt_without_a_known_token(catalog):
    assert catalog.match_txt("v=spf1 -all") == []
    assert catalog.match_txt("some random text") == []


@pytest.mark.parametrize(
    ("address", "provider"),
    [
        ("104.16.1.1", "Cloudflare"),
        ("172.66.147.243", "Cloudflare"),
        ("2606:4700:10::6814:179a", "Cloudflare"),
        ("151.101.0.223", "Fastly"),
        ("185.199.108.153", "GitHub Pages"),
    ],
)
def test_addresses(catalog, address, provider):
    assert [p.name for p in catalog.match_ip(address)] == [provider]


def test_address_outside_every_range(catalog):
    assert catalog.match_ip("192.0.2.1") == []


# ---- validation -----------------------------------------------------------------------

MINIMAL = """
providers:
  - name: Example CDN
    category: cdn
    shared_platform: true
    patterns:
      cname: [example-cdn.net]
"""


def test_minimal_catalog():
    (provider,) = parse_catalog(MINIMAL).providers

    assert provider.name == "Example CDN"
    assert provider.takeover is None


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("providers: []", "no providers"),
        ("nope: 1", "providers"),
        (MINIMAL.replace("category: cdn", "category: magic"), "category"),
        (MINIMAL.replace("cname: [example-cdn.net]", "cname: []"), "no patterns"),
        (MINIMAL.replace("cname:", "dns:"), "unknown pattern"),
        (
            MINIMAL.replace("shared_platform: true", "shared_platform: true\n    colour: red"),
            "unknown key",
        ),
        (MINIMAL.replace("cname: [example-cdn.net]", "ip: [not-a-network]"), "not-a-network"),
        (MINIMAL.replace("cname: [example-cdn.net]", "txt: ['(unclosed']"), "regular expression"),
        (MINIMAL.replace("cname: [example-cdn.net]", "cname: [Example-CDN.NET.]"), "lowercase"),
        (MINIMAL + MINIMAL.replace("providers:", ""), "twice"),
        (MINIMAL + "    takeover: {signal: smoke}\n", "signal"),
        (MINIMAL + "    takeover: {signal: http}\n", "marker"),
        ("providers:\n  - name: X\n", "category"),
        ("{{{", "YAML"),
    ],
)
def test_invalid_catalogs_name_the_problem(yaml_text, message):
    with pytest.raises(CatalogError) as excinfo:
        parse_catalog(yaml_text)

    assert message in str(excinfo.value)
