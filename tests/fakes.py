"""Test doubles shared by the unit tests."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from dnsinv.collection.runner import CommandResult, CommandTimeoutError

Responder = Callable[[Sequence[str]], CommandResult | Exception]


@dataclass
class FakeRunner:
    """Stands in for running `dig`; answers each call with `responder` and records the args."""

    responder: Responder
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, args: Sequence[str], timeout: float) -> CommandResult:
        self.calls.append(list(args))
        outcome = self.responder(args)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def ok(stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr=stderr)


NOERROR = ok(";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n")
UNREACHABLE = CommandResult(
    returncode=9,
    stdout=";; communications error to 1.1.1.1#53: timed out\n;; no servers could be reached\n",
    stderr="",
)
TIMEOUT = CommandTimeoutError(["dig"], 5.0)
