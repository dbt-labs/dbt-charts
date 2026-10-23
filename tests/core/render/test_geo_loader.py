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
    load_geometry,
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
        document = load_geometry(f"bundled:{name}")

    assert document["type"] == "Topology"
    assert objects <= set(document["objects"])


def test_unknown_bundled_name_is_an_error_card() -> None:
    with pytest.raises(ChartDataError, match="bundled:nope.json"):
        load_geometry("bundled:nope.json")


def test_bundled_name_cannot_escape_the_package_directory() -> None:
    with pytest.raises(ChartDataError, match="bundled:"):
        load_geometry("bundled:../loader.py")


def test_url_goes_through_the_scoped_fetcher() -> None:
    seen: list[str] = []

    def fetcher(url: str) -> bytes:
        seen.append(url)
        return b'{"type": "FeatureCollection", "features": []}'

    with geometry_fetcher_scope(fetcher):
        document = load_geometry("https://example.com/geo.json")

    assert seen == ["https://example.com/geo.json"]
    assert document == {"type": "FeatureCollection", "features": []}


@pytest.mark.parametrize(
    "body", [b"not json", b"[1, 2]", b"\xff\xfe"], ids=["syntax", "not-object", "utf8"]
)
def test_unparseable_document_is_an_error_card(body: bytes) -> None:
    with (
        geometry_fetcher_scope(lambda url: body),
        pytest.raises(ChartDataError, match="example.com/geo.json"),
    ):
        load_geometry("https://example.com/geo.json")


def test_failed_fetch_is_an_error_card() -> None:
    def fetcher(url: str) -> bytes:
        raise TimeoutError("timed out")

    with (
        geometry_fetcher_scope(fetcher),
        pytest.raises(ChartDataError, match="timed out"),
    ):
        load_geometry("https://example.com/geo.json")


def test_scope_restores_the_previous_fetcher() -> None:
    with geometry_fetcher_scope(lambda url: b"{}"):
        with geometry_fetcher_scope(lambda url: b'{"inner": 1}'):
            assert load_geometry("https://example.com/a.json") == {"inner": 1}
        assert load_geometry("https://example.com/a.json") == {}


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
    load_geometry("https://example.com/a.json")
    load_geometry("https://example.com/a.json")

    assert len(opener.calls) == 2
    assert opener.calls[0]["timeout"] == FETCH_TIMEOUT_SECONDS


def test_default_fetcher_only_speaks_http(opener: _Opener) -> None:
    with pytest.raises(ChartDataError, match="file:///etc/passwd"):
        load_geometry("file:///etc/passwd")

    assert opener.calls == []


def test_default_fetcher_enforces_the_size_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader, "_opener", _Opener(b" " * (MAX_GEOMETRY_BYTES + 1)))

    with (
        geometry_fetcher_scope(loader.fetch_url),
        pytest.raises(ChartDataError, match="larger than"),
    ):
        load_geometry("https://example.com/huge.json")


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
        load_geometry("https://example.com/slow.json")


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
        load_geometry("https://example.com/geo.json")


def test_bundled_documents_are_not_shared() -> None:
    first = load_geometry("bundled:world-110m.json")
    first["objects"].clear()

    assert load_geometry("bundled:world-110m.json")["objects"]


def test_bundled_failure_names_the_authored_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader, "_bundled_bytes", lambda name: b"not json")

    with pytest.raises(ChartDataError) as raised:
        load_geometry("bundled:us-10m.json")

    assert "us-states" in str(raised.value)
    assert "bundled:" not in str(raised.value)
