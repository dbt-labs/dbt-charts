"""Ctrl-C stops `dct serve` while a board's query is still running."""

from __future__ import annotations

import contextlib
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

_SLOW_BOARD = """\
title: Slow
source: local
queries:
  main:
    sql: |-
      SELECT count(*) AS n, 1 AS g FROM range(100000000000) t(i)
      WHERE md5(i::varchar) LIKE 'abcdef%'
charts:
  c:
    type: bar
    query: main
    x: g
    y: n
rows:
  - c
"""
_CONFIG = """\
sources:
  local:
    type: duckdb
    path: ":memory:"
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str, timeout: float) -> int:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — localhost
        return int(resp.status)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT delivery")
@pytest.mark.timeout(60)
def test_sigint_stops_serve_during_a_long_query(tmp_path: Path) -> None:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "slow.yml").write_text(_SLOW_BOARD)
    (tmp_path / "dbt_charts.yml").write_text(_CONFIG)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen(  # noqa: S603 — our own CLI
        [
            sys.executable,
            "-c",
            "from dbt_charts.cli.main import app; app()",
            "serve",
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                _get(f"{base}/health", timeout=1)
                break
            except OSError:
                assert time.monotonic() < deadline, "serve never came up"
                time.sleep(0.2)

        def _render() -> None:
            with contextlib.suppress(OSError):  # the server goes away mid-render
                _get(f"{base}/slow/", timeout=60)

        threading.Thread(target=_render, daemon=True).start()
        time.sleep(1)  # let the query reach DuckDB

        assert _get(f"{base}/health", timeout=3) == 200

        started = time.monotonic()
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=15)
        assert time.monotonic() - started < 10
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
