"""The playground server only serves files under the directory it is rooted at."""

from __future__ import annotations

import http.client
import importlib.util
import threading
from collections.abc import Iterator
from http.server import HTTPServer
from pathlib import Path

import pytest

_SERVER_PATH = Path(__file__).parents[1] / "playground" / "server.py"


@pytest.fixture
def playground_port() -> Iterator[int]:
    spec = importlib.util.spec_from_file_location("playground_server", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    httpd = HTTPServer(("127.0.0.1", 0), module.PlaygroundHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _get(port: int, path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.putrequest("GET", path, skip_host=False)
    conn.endheaders()
    return conn.getresponse()


def test_examples_route_serves_an_example(playground_port: int) -> None:
    first = next((_SERVER_PATH.parents[1] / "examples").glob("*.md"))
    assert _get(playground_port, f"/examples/{first.name}").status == 200


@pytest.mark.parametrize(
    "path",
    [
        "/examples/../playground/server.py",
        "/examples/../../../../../../../../etc/passwd",
        "/examples/..%2f..%2fpyproject.toml",
    ],
)
def test_examples_route_rejects_traversal(playground_port: int, path: str) -> None:
    assert _get(playground_port, path).status == 404
