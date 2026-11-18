"""The smoke-install retry helper retries outages and nothing else."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ._install_retry import is_network_failure, run_install


def _script(tmp_path: Path, body: str) -> list[str]:
    """A command that appends to a counter file, then runs `body`."""
    counter = tmp_path / "count"
    script = tmp_path / "cmd.py"
    script.write_text(f"import sys\nopen({str(counter)!r}, 'a').write('x')\n{body}\n")
    return [sys.executable, str(script)]


def _calls(tmp_path: Path) -> int:
    return len((tmp_path / "count").read_text())


@pytest.mark.parametrize(
    "text",
    [
        "HTTP Error 504: Gateway Timeout",
        "HTTP Error 502: Bad Gateway",
        "Connection reset by peer",
        "operation timed out",
    ],
)
def test_network_signatures_classified(text: str) -> None:
    assert is_network_failure(text)


@pytest.mark.parametrize(
    "text",
    [
        "No solution found when resolving dependencies",
        "HTTP Error 404: Not Found",
        "error: Failed to build `foo`",
    ],
)
def test_resolution_and_build_errors_not_network(text: str) -> None:
    assert not is_network_failure(text)


def test_network_error_retries_then_succeeds(tmp_path: Path) -> None:
    count_file = str(tmp_path / "count")
    cmd = _script(
        tmp_path,
        f"if len(open({count_file!r}).read()) < 3:\n"
        "    sys.stderr.write('HTTP Error 504')\n"
        "    sys.exit(1)",
    )
    run_install(cmd, attempts=4, backoff_seconds=0)
    assert _calls(tmp_path) == 3


def test_resolution_error_fails_immediately(tmp_path: Path) -> None:
    cmd = _script(
        tmp_path, "sys.stderr.write('No solution found when resolving')\nsys.exit(1)"
    )
    with pytest.raises(subprocess.CalledProcessError):
        run_install(cmd, attempts=4, backoff_seconds=0)
    assert _calls(tmp_path) == 1


def test_persistent_outage_fails_after_all_attempts(tmp_path: Path) -> None:
    cmd = _script(tmp_path, "sys.stderr.write('HTTP Error 504')\nsys.exit(1)")
    with pytest.raises(subprocess.CalledProcessError):
        run_install(cmd, attempts=3, backoff_seconds=0)
    assert _calls(tmp_path) == 3
