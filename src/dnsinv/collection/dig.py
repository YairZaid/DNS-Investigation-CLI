"""Building the dig command and parsing its output.

The format below was checked against real output from BIND, Unbound, 1.1.1.1 and Docker's
resolver (tests/fixtures/dig). The command asks all five record types in one process:

    dig [@server] +noall +comments +question +answer +time=3 +tries=2 NAME A NAME AAAA ...

and prints one block per question:

    ;; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 60735     <- response code
    ;; QUESTION SECTION:
    ;xdangling.test.lab.        IN  A                               <- which question this is
    ;; ANSWER SECTION:
    xdangling.test.lab. 60  IN  CNAME   gone.other.lab.             <- owner ttl class type rdata

`+question` matters: without it an empty AAAA block and an empty CNAME block are identical.
A question that timed out prints no block at all, only an anonymous "timed out" line.
"""

from __future__ import annotations

import ipaddress
import re

from dnsinv.collection.model import Answer, DnsRecord, LookupStatus, RecordType
from dnsinv.collection.resolver import ResolverChoice

QUERY_SECONDS = 3
QUERY_TRIES = 2
_FLAGS = (
    "+noall",
    "+comments",
    "+question",
    "+answer",
    f"+time={QUERY_SECONDS}",
    f"+tries={QUERY_TRIES}",
)
# Worst case: every question uses all its tries, plus a margin for process start-up.
PROCESS_TIMEOUT_SECONDS = len(RecordType) * QUERY_SECONDS * QUERY_TRIES + 10

_SAFE_NAME = re.compile(r"^[a-z0-9_]([a-z0-9_.-]*[a-z0-9_])?$")
_STATUS = re.compile(r"status: (\w+)")
_KNOWN_TYPES = {t.value for t in RecordType}

AnswerKey = tuple[str, RecordType]


def build_dig_args(name: str, resolver: ResolverChoice) -> list[str]:
    # Hostnames are validated when the input file is read. This second check is here
    # because this is the line where a name becomes a command-line argument.
    if not _SAFE_NAME.match(name):
        raise ValueError(f"unsafe hostname for dig: {name!r}")
    questions = [part for rtype in RecordType for part in (name, rtype.value)]
    return ["dig", *resolver.dig_args(), *_FLAGS, *questions]


def parse_dig_output(text: str) -> dict[AnswerKey, Answer]:
    """Answers keyed by (question name, record type). Questions that timed out are absent."""
    answers: dict[AnswerKey, Answer] = {}
    status: LookupStatus | None = None
    question: AnswerKey | None = None
    records: list[DnsRecord] = []
    section = ""

    def close_block() -> None:
        if status is not None and question is not None:
            answers[question] = Answer(status, tuple(dict.fromkeys(records)))

    for line in text.splitlines():
        if line.startswith(";; ->>HEADER<<-"):
            close_block()
            status, question, records, section = _parse_status(line), None, [], ""
        elif line.startswith(";; "):
            # Section titles, flags and "communications error" lines.
            section = line[3:].split(" SECTION:")[0] if line.endswith("SECTION:") else ""
        elif section == "QUESTION" and line.startswith(";"):
            question = _parse_question(line)
        elif section == "ANSWER" and line and not line.startswith(";"):
            record = _parse_record(line)
            if record is not None:
                records.append(record)
    close_block()
    return answers


def decode_txt(rdata: str) -> str:
    """Join the quoted strings of a TXT record and undo dig's escaping.

    dig prints `"part one " "part two"`, with `\\"`, `\\\\` and `\\DDD` (a decimal byte)
    inside the quotes.
    """
    data = bytearray()
    in_quotes = False
    i = 0
    while i < len(rdata):
        char = rdata[i]
        if char == "\\" and i + 1 < len(rdata):
            digits = rdata[i + 1 : i + 4]
            if len(digits) == 3 and digits.isdigit() and int(digits) < 256:
                data.append(int(digits))
                i += 4
            else:
                data.extend(rdata[i + 1].encode())
                i += 2
            continue
        if char == '"':
            in_quotes = not in_quotes
        elif in_quotes or not char.isspace():
            data.extend(char.encode())
        i += 1
    return data.decode("utf-8", errors="replace")


def _parse_status(header: str) -> LookupStatus:
    match = _STATUS.search(header)
    code = match.group(1) if match else ""
    try:
        return LookupStatus(code)
    except ValueError:
        return LookupStatus.ERROR  # FORMERR, NOTIMP, ...


def _parse_question(line: str) -> AnswerKey | None:
    parts = line[1:].split()
    if len(parts) != 3 or parts[1] != "IN" or parts[2] not in _KNOWN_TYPES:
        return None
    return _normalize(parts[0]), RecordType(parts[2])


def _parse_record(line: str) -> DnsRecord | None:
    parts = line.split(None, 4)
    if len(parts) != 5 or parts[2] != "IN" or parts[3] not in _KNOWN_TYPES:
        return None
    owner, rtype, rdata = _normalize(parts[0]), RecordType(parts[3]), parts[4].strip()

    if rtype in (RecordType.A, RecordType.AAAA):
        try:
            return DnsRecord(owner, rtype, str(ipaddress.ip_address(rdata)))
        except ValueError:
            return None
    if rtype is RecordType.CNAME:
        return DnsRecord(owner, rtype, _normalize(rdata))
    if rtype is RecordType.MX:
        fields = rdata.split()
        if len(fields) != 2 or not fields[0].isdigit():
            return None
        return DnsRecord(owner, rtype, _normalize(fields[1]) or ".", priority=int(fields[0]))
    return DnsRecord(owner, rtype, decode_txt(rdata))


def _normalize(name: str) -> str:
    return name.rstrip(".").lower()
