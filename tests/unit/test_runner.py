import sys

import pytest

from dnsinv.collection.runner import CommandNotFoundError, CommandTimeoutError, run_subprocess


def test_returns_exit_code_and_both_streams():
    result = run_subprocess(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
        ],
        timeout=10,
    )

    assert result.returncode == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_missing_binary_raises_not_found():
    with pytest.raises(CommandNotFoundError) as excinfo:
        run_subprocess(["dnsinv-no-such-binary-xyz"], timeout=5)

    assert excinfo.value.command == "dnsinv-no-such-binary-xyz"


def test_slow_command_raises_timeout():
    with pytest.raises(CommandTimeoutError):
        run_subprocess([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)


def test_arguments_are_not_interpreted_by_a_shell():
    result = run_subprocess(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", "a; echo injected"], timeout=10
    )

    assert result.stdout.strip() == "a; echo injected"
