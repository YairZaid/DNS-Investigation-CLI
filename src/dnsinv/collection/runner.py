"""Running external commands. Injected everywhere, so tests never start a real `dig`."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from dnsinv.errors import DnsInvError


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(self, args: Sequence[str], timeout: float) -> CommandResult: ...


class CommandNotFoundError(DnsInvError):
    def __init__(self, command: str) -> None:
        super().__init__(f"'{command}' is not installed or not on PATH")
        self.command = command


class CommandTimeoutError(DnsInvError):
    def __init__(self, args: Sequence[str], timeout: float) -> None:
        super().__init__(f"'{' '.join(args)}' did not finish within {timeout:g}s")
        self.args_list = list(args)
        self.timeout = timeout


def run_subprocess(args: Sequence[str], timeout: float) -> CommandResult:
    """Run `args` directly (never through a shell) and capture its output."""
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandNotFoundError(args[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandTimeoutError(args, timeout) from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)
