"""The dig parser, tested against real recorded output (see tests/fixtures/dig/README.md)."""

from pathlib import Path

import pytest

from dnsinv.collection.dig import build_dig_args, decode_txt, parse_dig_output
from dnsinv.collection.model import DnsRecord, LookupStatus, RecordType
from dnsinv.collection.resolver import ResolverChoice, ResolverSource

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dig"
A, AAAA, MX, TXT, CNAME = (
    RecordType.A,
    RecordType.AAAA,
    RecordType.MX,
    RecordType.TXT,
    RecordType.CNAME,
)


def parsed(fixture):
    return parse_dig_output((FIXTURES / f"{fixture}.txt").read_text(encoding="utf-8"))


def values(answer):
    return [r.value for r in answer.records]


# ---- the command line -------------------------------------------------------


def test_one_command_asks_all_five_types_through_the_chosen_resolver():
    args = build_dig_args("example.com", ResolverChoice("1.1.1.1", ResolverSource.DEFAULT))

    assert args[:2] == ["dig", "@1.1.1.1"]
    assert {"+noall", "+comments", "+question", "+answer"} <= set(args)
    assert args[-10:] == [
        "example.com", "A", "example.com", "AAAA", "example.com", "MX",
        "example.com", "TXT", "example.com", "CNAME",
    ]  # fmt: skip


def test_system_resolver_passes_no_server_argument():
    args = build_dig_args("example.com", ResolverChoice(None, ResolverSource.SYSTEM))

    assert not any(a.startswith("@") for a in args)


@pytest.mark.parametrize("name", ["-x", "+short", "@8.8.8.8", "a b.com", ""])
def test_refuses_names_dig_could_read_as_options(name):
    with pytest.raises(ValueError, match="unsafe"):
        build_dig_args(name, ResolverChoice(None, ResolverSource.SYSTEM))


# ---- plain answers ------------------------------------------------------------


def test_every_block_is_matched_to_its_question():
    answers = parsed("ok")

    assert set(answers) == {("ok.test.lab", t) for t in RecordType}
    assert all(a.status is LookupStatus.NOERROR for a in answers.values())
    assert sorted(values(answers["ok.test.lab", A])) == ["192.0.2.10", "192.0.2.11"]
    assert values(answers["ok.test.lab", AAAA]) == ["2001:db8::10"]
    assert answers["ok.test.lab", MX].records == ()


def test_edns_comment_lines_are_not_mistaken_for_questions():
    # BIND's output has "; EDNS: ..." and "; COOKIE: ..." lines that also start with ';'.
    assert len(parsed("ok")) == 5


def test_nonexistent_name_is_nxdomain_for_every_type():
    answers = parsed("missing")

    assert {a.status for a in answers.values()} == {LookupStatus.NXDOMAIN}


def test_existing_name_without_the_record_type_is_noerror_and_empty():
    answers = parsed("nodata")

    assert answers["nodata.test.lab", A].status is LookupStatus.NOERROR
    assert answers["nodata.test.lab", A].records == ()
    assert values(answers["nodata.test.lab", TXT]) == ["only txt here"]


def test_public_resolver_output_without_cookie_lines():
    answers = parsed("public-example-com")

    assert len(answers["example.com", A].records) == 2
    assert len(answers["example.com", AAAA].records) == 2
    assert "v=spf1 -all" in values(answers["example.com", TXT])


# ---- record types ---------------------------------------------------------------


def test_mx_records_keep_priority_and_exchange():
    records = parsed("mail")["mail.test.lab", MX].records

    assert records == (
        DnsRecord("mail.test.lab", MX, "mx1.test.lab", priority=10),
        DnsRecord("mail.test.lab", MX, "mx2.test.lab", priority=20),
    )


def test_null_mx_is_kept_as_a_dot():
    (record,) = parsed("nullmx")["nullmx.test.lab", MX].records

    assert (record.value, record.priority) == (".", 0)
    assert record.is_null_mx


def test_txt_strings_are_joined_and_unescaped():
    txt = set(values(parsed("txt")["txt.test.lab", TXT]))

    assert txt == {
        "v=spf1 include:_spf.example.com ~all",
        "part one part two",
        'semi;colon and "quotes" and back\\slash',
        "v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC",
        "non-ascii: é tab:\tend",
    }


@pytest.mark.parametrize(
    ("rdata", "expected"),
    [
        ('"simple"', "simple"),
        ('"a" "b" "c"', "abc"),
        ('"trailing space " "kept"', "trailing space kept"),
        (r'"say \"hi\""', 'say "hi"'),
        (r'"back\\slash"', "back\\slash"),
        (r'"\195\169"', "é"),
        (r'"bad utf8 \255 here"', "bad utf8 � here"),
        ('""', ""),
        ("unquoted", "unquoted"),
    ],
)
def test_decode_txt(rdata, expected):
    assert decode_txt(rdata) == expected


# ---- CNAME ------------------------------------------------------------------------


def test_records_belong_to_the_name_on_their_own_line_not_the_question():
    records = parsed("alias")["alias.test.lab", A].records

    assert DnsRecord("alias.test.lab", CNAME, "ok.test.lab") in records
    assert {r.owner for r in records if r.rtype is A} == {"ok.test.lab"}


def test_dangling_cname_has_the_cname_and_nxdomain_together():
    answers = parsed("unbound-xdangling")
    name = "xdangling.test.lab"

    for rtype in (A, AAAA, MX, TXT):
        assert answers[name, rtype].status is LookupStatus.NXDOMAIN
        assert values(answers[name, rtype]) == ["gone.other.lab"]
    # Asking for the CNAME itself succeeds: the name exists, its target does not.
    assert answers[name, CNAME].status is LookupStatus.NOERROR


def test_cname_loop_is_servfail_with_both_links():
    answer = parsed("loop1")["loop1.test.lab", A]

    assert answer.status is LookupStatus.SERVFAIL
    assert [(r.owner, r.value) for r in answer.records] == [
        ("loop1.test.lab", "loop2.test.lab"),
        ("loop2.test.lab", "loop1.test.lab"),
    ]


# ---- real-world mess -----------------------------------------------------------------


def test_duplicate_records_are_removed_keeping_first_order():
    records = parsed("docker-resolver-duplicates")["www.microsoft.com", A].records

    assert [(r.rtype, r.owner) for r in records] == [
        (CNAME, "www.microsoft.com"),
        (CNAME, "www.microsoft.com-c-3.edgekey.net"),
        (A, "e13678.dscb.akamaiedge.net"),
    ]


def test_unreachable_resolver_yields_no_blocks_at_all():
    assert parsed("unreachable") == {}


def test_names_are_lowercased_and_lose_the_trailing_dot():
    text = (
        ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n"
        ";; QUESTION SECTION:\n;WWW.Example.COM.\t\tIN\tA\n"
        ";; ANSWER SECTION:\nWWW.Example.COM.\t60\tIN\tCNAME\tTarget.Example.NET.\n"
    )

    (record,) = parse_dig_output(text)["www.example.com", A].records

    assert (record.owner, record.value) == ("www.example.com", "target.example.net")


def test_unknown_status_and_unknown_record_types_do_not_crash():
    text = (
        ";; ->>HEADER<<- opcode: QUERY, status: FORMERR, id: 1\n"
        ";; QUESTION SECTION:\n;example.com.\t\tIN\tA\n"
        ";; ANSWER SECTION:\n"
        "example.com.\t60\tIN\tRRSIG\tA 13 2 300 20260101000000 20250101000000 1 example.com. x=\n"
        "example.com.\t60\tIN\tA\tnot-an-ip\n"
        "garbage line\n"
        "example.com.\t60\tIN\tA\t192.0.2.1\n"
    )

    answer = parse_dig_output(text)["example.com", A]

    assert answer.status is LookupStatus.ERROR
    assert values(answer) == ["192.0.2.1"]


def test_empty_output():
    assert parse_dig_output("") == {}
