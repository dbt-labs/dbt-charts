"""The geometry loader: bundled maps from package data, other URLs through an
injectable fetcher."""

from __future__ import annotations

import http.client
import io
import json
import urllib.request
from collections.abc import Iterator
from email.message import Message
from typing import Any

import pytest

from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.geo import loader
from dbt_charts.core.render.geo.loader import (
    FETCH_TIMEOUT_SECONDS,
    MAX_GEOMETRY_BYTES,
    geometry_fetcher_scope,
    load_geometry_text,
)


def _forbid(url: str) -> bytes:
    raise AssertionError(f"unexpected network fetch of {url}")


@pytest.mark.parametrize(
    ("name", "objects"),
    [
        ("us-10m.json", {"states", "counties"}),
        ("world-110m.json", {"countries", "land"}),
    ],
)
def test_bundled_source_loads_without_network(name: str, objects: set[str]) -> None:
    with geometry_fetcher_scope(_forbid):
        document = json.loads(load_geometry_text(f"bundled:{name}"))

    assert document["type"] == "Topology"
    assert objects <= set(document["objects"])


def test_unknown_bundled_name_is_an_error_card() -> None:
    with pytest.raises(ChartDataError, match="bundled:nope.json"):
        load_geometry_text("bundled:nope.json")


def test_bundled_name_cannot_escape_the_package_directory() -> None:
    with pytest.raises(ChartDataError, match="bundled:"):
        load_geometry_text("bundled:../loader.py")


def test_url_goes_through_the_scoped_fetcher() -> None:
    seen: list[str] = []

    def fetcher(url: str) -> bytes:
        seen.append(url)
        return b'{"type": "FeatureCollection", "features": []}'

    with geometry_fetcher_scope(fetcher):
        text = load_geometry_text("https://example.com/geo.json")

    assert seen == ["https://example.com/geo.json"]
    assert json.loads(text) == {"type": "FeatureCollection", "features": []}


def test_failed_fetch_is_an_error_card() -> None:
    def fetcher(url: str) -> bytes:
        raise TimeoutError("timed out")

    with (
        geometry_fetcher_scope(fetcher),
        pytest.raises(ChartDataError, match="timed out"),
    ):
        load_geometry_text("https://example.com/geo.json")


def test_scope_restores_the_previous_fetcher() -> None:
    with geometry_fetcher_scope(lambda url: b"{}"):
        with geometry_fetcher_scope(lambda url: b'{"inner": 1}'):
            assert load_geometry_text("https://example.com/a.json") == '{"inner": 1}'
        assert load_geometry_text("https://example.com/a.json") == "{}"


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _Opener:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[dict[str, Any]] = []

    def open(self, url: str, *, timeout: float) -> _Response:
        self.calls.append({"url": url, "timeout": timeout})
        return _Response(self.body)


@pytest.fixture
def opener(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Opener]:
    fake = _Opener(json.dumps({"type": "FeatureCollection", "features": []}).encode())
    monkeypatch.setattr(loader, "_opener", fake)
    with geometry_fetcher_scope(loader.fetch_url):
        yield fake


def test_default_fetcher_does_not_cache(opener: _Opener) -> None:
    load_geometry_text("https://example.com/a.json")
    load_geometry_text("https://example.com/a.json")

    assert len(opener.calls) == 2
    assert opener.calls[0]["timeout"] == FETCH_TIMEOUT_SECONDS


def test_default_fetcher_only_speaks_http(opener: _Opener) -> None:
    with pytest.raises(ChartDataError, match="file:///etc/passwd"):
        load_geometry_text("file:///etc/passwd")

    assert opener.calls == []


def test_default_fetcher_enforces_the_size_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader, "_opener", _Opener(b" " * (MAX_GEOMETRY_BYTES + 1)))

    with (
        geometry_fetcher_scope(loader.fetch_url),
        pytest.raises(ChartDataError, match="larger than"),
    ):
        load_geometry_text("https://example.com/huge.json")


def test_default_fetcher_enforces_an_overall_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter(range(0, 1000, 20))
    monkeypatch.setattr(loader.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(loader, "_opener", _Opener(b" " * 200_000))

    with (
        geometry_fetcher_scope(loader.fetch_url),
        pytest.raises(ChartDataError, match="not finished"),
    ):
        load_geometry_text("https://example.com/slow.json")


def test_redirect_to_a_non_http_scheme_is_refused() -> None:
    handler = loader._HttpOnlyRedirects()
    request = urllib.request.Request("https://example.com/a.json")

    with pytest.raises(ChartDataError, match="non-http"):
        handler.redirect_request(
            request, io.BytesIO(), 302, "Found", Message(), "file:///etc/passwd"
        )


def test_malformed_http_response_is_an_error_card() -> None:
    def fetcher(url: str) -> bytes:
        raise http.client.IncompleteRead(b"")

    with (
        geometry_fetcher_scope(fetcher),
        pytest.raises(ChartDataError, match="example.com/geo.json"),
    ):
        load_geometry_text("https://example.com/geo.json")


def test_text_is_the_documents_bytes_unparsed() -> None:
    body = b'{"type":  "FeatureCollection",\n "features": []}'
    with geometry_fetcher_scope(lambda url: body):
        text = load_geometry_text("https://example.com/geo.json")

    assert text.encode() == body


def test_bundled_text_is_read_once_per_process() -> None:
    assert load_geometry_text("bundled:us-10m.json") is load_geometry_text(
        "bundled:us-10m.json"
    )


def test_fetched_text_is_never_kept() -> None:
    bodies = iter([b'{"a": 1}', b'{"a": 2}'])
    with geometry_fetcher_scope(lambda url: next(bodies)):
        first = load_geometry_text("https://example.com/geo.json")
        second = load_geometry_text("https://example.com/geo.json")

    assert (first, second) == ('{"a": 1}', '{"a": 2}')


def test_text_that_is_not_utf8_is_an_error_card() -> None:
    with (
        geometry_fetcher_scope(lambda url: b"\xff\xfe"),
        pytest.raises(ChartDataError, match="example.com/geo.json"),
    ):
        load_geometry_text("https://example.com/geo.json")


def test_a_leading_byte_order_mark_is_dropped() -> None:
    with geometry_fetcher_scope(lambda url: b"\xef\xbb\xbf" + b'{"a": 1}'):
        text = load_geometry_text("https://example.com/geo.json")

    assert text == '{"a": 1}'
