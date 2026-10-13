"""The live send: wire format, anonymity, and bounded exit latency.

No test reaches p.vx.dbt.com: the producer is pointed at a loopback server,
a silent loopback socket, or its file-writing dev mode.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from dbtlabs.proto.public.v1.events.charts_pb2 import CliInvocation as CliInvocationPb
from dbtlabs.proto.public.v1.events.vortex_pb2 import VortexMessageBatch

from dbt_charts.telemetry.emitter import (
    FIRST_RUN_NOTICE,
    CliInvocation,
    begin_invocation,
    send,
)


class _Ingest(BaseHTTPRequestHandler):
    requests: list[tuple[str, dict[str, str], bytes]]

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.requests.append((self.path, dict(self.headers), body))
        self.send_response(202)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(autouse=True)
def tracking_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DO_NOT_TRACK")
    monkeypatch.delenv("DBT_SEND_ANONYMOUS_USAGE_STATS", raising=False)
    monkeypatch.setenv("DBT_PROFILES_DIR", str(tmp_path / "dbt"))


@pytest.fixture
def ingest(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[list[tuple[str, dict[str, str], bytes]], None, None]:
    received: list[tuple[str, dict[str, str], bytes]] = []
    handler = type("Handler", (_Ingest,), {"requests": received})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("VORTEX_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    yield received
    server.shutdown()


START = CliInvocation(
    event_id="e",
    invocation_id="i",
    progress="start",
    command="render",
    version="1.2.3",
    python_version="3.13.0",
    os_name="Darwin",
    project_id=None,
    dbt_local_cookie_user_id="u",
    result_type=None,
    duration_ms=None,
)


def test_send_maps_none_to_proto3_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "vortex.jsonl"
    monkeypatch.setenv("VORTEX_DEV_MODE", "true")
    monkeypatch.setenv("VORTEX_DEV_MODE_OUTPUT_PATH", str(out))
    send(START)
    send(
        replace(START, progress="end", result_type="ok", duration_ms=7, project_id="p")
    )
    start, end = (json.loads(line) for line in out.read_text().splitlines())
    assert start["type_url"] == "/v1.public.events.charts.CliInvocation"
    assert start["value"] == {
        "commonContext": {},
        "enrichment": {},
        "eventId": "e",
        "invocationId": "i",
        "progress": "start",
        "command": "render",
        "version": "1.2.3",
        "pythonVersion": "3.13.0",
        "osName": "Darwin",
        "dbtLocalCookieUserId": "u",
    }
    assert (end["value"]["resultType"], end["value"]["durationMs"]) == ("ok", "7")
    assert end["value"]["projectId"] == "p"


def test_run_posts_one_unauthenticated_batch_on_exit(
    ingest: list[tuple[str, dict[str, str], bytes]], tmp_path: Path
) -> None:
    invocation = begin_invocation("render", no_send_flag=False, project_dir=tmp_path)
    assert invocation is not None
    invocation.end(None)

    [(path, headers, body)] = ingest
    assert path == "/v1/ingest/protobuf"
    assert {name.lower() for name in headers}.isdisjoint(
        {"authorization", "x-api-key", "cookie"}
    )
    batch = VortexMessageBatch.FromString(body)
    events = [CliInvocationPb.FromString(m.any.value) for m in batch.payload]
    assert [e.progress for e in events] == ["start", "end"]
    assert events[0].invocation_id == events[1].invocation_id


def test_consent_off_sends_nothing(
    ingest: list[tuple[str, dict[str, str], bytes]], tmp_path: Path
) -> None:
    assert begin_invocation("render", no_send_flag=True, project_dir=tmp_path) is None
    assert ingest == []


@pytest.fixture
def silent_endpoint() -> Generator[str, None, None]:
    """A loopback server that accepts connections and never answers."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    accepted: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                accepted.append(server.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    server.close()
    for connection in accepted:
        connection.close()


def test_unresponsive_endpoint_is_bounded_and_prints_nothing(
    silent_endpoint: str, tmp_path: Path
) -> None:
    env = {
        **os.environ,
        "VORTEX_BASE_URL": silent_endpoint,
        "DCT_NO_WORKSPACE_GUARD": "1",
    }
    env.pop("DO_NOT_TRACK", None)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", "from dbt_charts.cli.main import app; app()", "docs"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        env=env,
        check=True,
    )
    elapsed = time.monotonic() - started
    # Startup plus the 1s shutdown bound; the producer's own default is 10s.
    assert elapsed < 6
    assert result.stderr.splitlines() == [FIRST_RUN_NOTICE]
