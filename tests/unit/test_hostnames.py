import pytest

from dnsinv.hostnames import (
    InputFileError,
    InvalidHostnameError,
    ParsedInput,
    apex_domain,
    extract_hostname,
    parse_input,
    read_input_file,
)

# ---- extract_hostname: one URL in, one normalised Hostname out ------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com", "example.com"),
        ("https://Example.COM/path?q=1#frag", "example.com"),
        ("HTTPS://WWW.EXAMPLE.COM", "www.example.com"),
        ("example.com", "example.com"),
        ("example.com/some/path", "example.com"),
        ("example.com:8080/path", "example.com"),
        ("http://user:pass@example.com:8080/x", "example.com"),
        ("//example.com/x", "example.com"),
        ("https://example.com.", "example.com"),
        ("example.com.", "example.com"),
        ("ftp://files.example.com/pub", "files.example.com"),
        ("https://bücher.de/", "xn--bcher-kva.de"),
        ("https://xn--bcher-kva.de/", "xn--bcher-kva.de"),
        ("https://sub_domain.example.com", "sub_domain.example.com"),
        ("https://a-b.example.com", "a-b.example.com"),
        ("https://foo.internal", "foo.internal"),
        ("https://example.com#section", "example.com"),
    ],
)
def test_extracts_and_normalises_the_hostname(raw, expected):
    assert extract_hostname(raw) == expected


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("http://192.168.1.1/admin", "IP address"),
        ("1.2.3.4", "IP address"),
        ("http://[2001:db8::1]:80/", "IP address"),
        ("999.1.1.1", "numeric top-level"),
        ("http://example.123/", "numeric top-level"),
        ("localhost", "single label"),
        ("http://intranet/", "single label"),
        ("mailto:someone@example.com", "no hostname"),
        ("javascript:alert(1)", "no hostname"),
        ("http://", "no hostname"),
        ("http://./", "no hostname"),
        ("http://[not-closed/", "malformed URL"),
        ("https://ex☃mple.com", "internationalised"),
        ("http://exa mple.com", "invalid character"),
        ("http://*.example.com", "invalid character"),
        ("http://exa$mple.com", "invalid character"),
        ("http://-example.com", "hyphen"),
        ("http://example-.com", "hyphen"),
        ("http://a..b.com", "empty label"),
        ("http://" + "a" * 64 + ".com", "longer than 63"),
        ("http://" + ".".join(["a" * 60] * 5) + ".com", "longer than 253"),
        ("-x", "option"),
        ("+short", "option"),
        ("@8.8.8.8", "option"),
        ("--help.example.com", "option"),
    ],
)
def test_rejects_with_a_reason(raw, reason):
    with pytest.raises(InvalidHostnameError) as excinfo:
        extract_hostname(raw)

    assert reason in str(excinfo.value)


def test_label_of_exactly_63_characters_is_allowed():
    name = "a" * 63 + ".com"

    assert extract_hostname(name) == name


# ---- apex_domain: the registrable domain, from the bundled Public Suffix List --


@pytest.mark.parametrize(
    ("hostname", "apex"),
    [
        ("blog.example.com", "example.com"),
        ("example.com", "example.com"),
        ("a.b.example.co.uk", "example.co.uk"),
        ("www.example.co.il", "example.co.il"),
        ("xn--bcher-kva.de", "xn--bcher-kva.de"),
        # Private suffixes: a GitHub Pages site or an S3 bucket is its own apex,
        # so we never scan github.io or s3.amazonaws.com as if the user owned them.
        ("user.github.io", "user.github.io"),
        ("bucket.s3.amazonaws.com", "bucket.s3.amazonaws.com"),
        # No registrable domain at all.
        ("co.il", None),
        ("foo.internal", None),
    ],
)
def test_apex_domain(hostname, apex):
    assert apex_domain(hostname) == apex


# ---- parse_input: a whole file ----------------------------------------------


def parse(text: str) -> ParsedInput:
    return parse_input(text.encode("utf-8"))


def test_one_url_per_line_with_line_numbers():
    result = parse("https://a.example.com\nhttps://b.example.org/x\n")

    assert [(h.name, h.apex, h.line_number) for h in result.hostnames] == [
        ("a.example.com", "example.com", 1),
        ("b.example.org", "example.org", 2),
    ]
    assert result.rejected == ()


def test_blank_lines_and_comments_are_skipped_not_rejected():
    result = parse("\n   \n# a comment\n  # indented comment\nexample.com\n")

    assert [h.name for h in result.hostnames] == ["example.com"]
    assert result.rejected == ()
    assert result.skipped_lines == 4


def test_surrounding_whitespace_is_ignored():
    assert [h.name for h in parse("   https://example.com  \t\n").hostnames] == ["example.com"]


def test_duplicates_after_normalisation_are_kept_once_at_first_line():
    result = parse("https://Example.com/a\nexample.com\nhttp://EXAMPLE.COM.:443/\nother.com\n")

    assert [(h.name, h.line_number) for h in result.hostnames] == [
        ("example.com", 1),
        ("other.com", 4),
    ]
    assert result.duplicate_lines == 2


def test_rejected_lines_keep_number_text_and_reason():
    result = parse("example.com\nhttp://10.0.0.1/\nlocalhost\n")

    assert [(r.line_number, r.text) for r in result.rejected] == [
        (2, "http://10.0.0.1/"),
        (3, "localhost"),
    ]
    assert "IP address" in result.rejected[0].reason


def test_windows_line_endings_and_bom():
    result = parse_input("﻿https://a.example.com\r\nhttps://b.example.com\r\n".encode())

    assert [h.name for h in result.hostnames] == ["a.example.com", "b.example.com"]


def test_line_that_is_not_utf8_is_rejected_alone():
    data = b"https://good.example.com\nhttps://bad\xff\xfe.example.com\nhttps://also-good.example.com\n"

    result = parse_input(data)

    assert [h.name for h in result.hostnames] == ["good.example.com", "also-good.example.com"]
    assert len(result.rejected) == 1
    assert result.rejected[0].line_number == 2
    assert "UTF-8" in result.rejected[0].reason


def test_last_line_without_newline():
    assert [h.name for h in parse("a.example.com\nb.example.com").hostnames] == [
        "a.example.com",
        "b.example.com",
    ]


def test_apex_is_none_for_a_public_suffix():
    (hostname,) = parse("co.il\n").hostnames

    assert hostname.apex is None


# ---- read_input_file: problems with the file itself ------------------------


def test_reads_a_file(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_bytes(b"https://example.com\n")

    assert [h.name for h in read_input_file(path).hostnames] == ["example.com"]


def test_missing_file(tmp_path):
    with pytest.raises(InputFileError) as excinfo:
        read_input_file(tmp_path / "nope.txt")

    assert "not found" in str(excinfo.value)
    assert excinfo.value.exit_code == 1


def test_directory_instead_of_file(tmp_path):
    with pytest.raises(InputFileError) as excinfo:
        read_input_file(tmp_path)

    assert "not a file" in str(excinfo.value)


@pytest.mark.parametrize("content", [b"", b"\n\n", b"# only comments\n   \n"])
def test_file_without_any_url(tmp_path, content):
    path = tmp_path / "urls.txt"
    path.write_bytes(content)

    with pytest.raises(InputFileError) as excinfo:
        read_input_file(path)

    assert "no URLs" in str(excinfo.value)


def test_file_where_every_line_is_rejected_is_not_a_file_error(tmp_path):
    # The caller decides what to do; the rejected lines must still be reportable.
    path = tmp_path / "urls.txt"
    path.write_bytes(b"localhost\n10.0.0.1\n")

    result = read_input_file(path)

    assert result.hostnames == ()
    assert len(result.rejected) == 2
