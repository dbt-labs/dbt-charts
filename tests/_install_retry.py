"""Retry a clean-venv install only when it died on a network outage.

A smoke install resolves transitive sdists (`dbt-core-experimental-parser`)
whose build backend downloads its real wheel from GitHub releases; an outage
there says nothing about whether dbt-charts installs. A resolution or build
error carries no network signature and fails on the first attempt.

Stdlib only: CI runs this file directly as
`python dbt-charts/tests/_install_retry.py -- <install command...>`.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Sequence

ATTEMPTS = 4
BACKOFF_SECONDS = 15

_NETWORK_SIGNATURE = re.compile(
    r"HTTP Error 50[234]"
    r"|Connection reset"
    r"|Connection aborted"
    r"|timed out"
    r"|Temporary failure in name resolution"
    r"|error sending request",
    re.IGNORECASE,
)


def is_network_failure(output: str) -> bool:
    return _NETWORK_SIGNATURE.search(output) is not None


def run_install(
    cmd: Sequence[str],
    *,
    attempts: int = ATTEMPTS,
    backoff_seconds: float = BACKOFF_SECONDS,
) -> None:
    """Run `cmd`, retrying with linear backoff on network failures only.

    Raises:
        subprocess.CalledProcessError: on a non-network failure, or when the
            network failure outlasts `attempts`.
    """
    for attempt in range(1, attempts + 1):
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode == 0:
            return
        output = result.stdout + result.stderr
        if attempt == attempts or not is_network_failure(output):
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        print(
            f"install attempt {attempt}/{attempts} hit a network failure; "
            f"retrying in {backoff_seconds * attempt:g}s",
            file=sys.stderr,
        )
        time.sleep(backoff_seconds * attempt)


if __name__ == "__main__":
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        sys.exit("usage: _install_retry.py -- <install command...>")
    try:
        run_install(command)
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
