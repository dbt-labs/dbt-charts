"""Resolve a geo source to its parsed TopoJSON/GeoJSON document.

A source is either `bundled:<file>` (a document shipped in this package) or an
http(s) URL. Python loads the geometry and hands the parsed document to whoever
draws the map, so the draw step does no I/O. A host with its own egress policy
installs a fetcher with `geometry_fetcher_scope`.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.request
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from importlib.resources import files
from pathlib import PurePosixPath
from typing import IO, Any, Final

from dbt_charts.core.compile.config import get_config
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_GEO_SOURCE_UNAVAILABLE

GeoDocument = dict[str, Any]  # type-state: explicit_any — parsed TopoJSON/GeoJSON

# Returns the raw document bytes, or raises OSError (unreachable, timed out,
# refused by policy), http.client.HTTPException (a malformed response) or
# ChartDataError for a failure it words itself.
GeometryFetcher = Callable[[str], bytes]

BUNDLED_PREFIX: Final = "bundled:"
FETCH_TIMEOUT_SECONDS: Final = 15.0
# The socket timeout bounds each read, not the transfer: a server dripping bytes
# would hold a render for as long as it likes, so the whole fetch has a deadline.
FETCH_DEADLINE_SECONDS: Final = 30.0
_READ_CHUNK_BYTES: Final = 64 * 1024
# The largest bundled map is ~640 KB; an authored city or country source is
# well under this.
MAX_GEOMETRY_BYTES: Final = 20 * 1024 * 1024


def _author_name(source: str) -> str:
    """The registry names the author may have written for *source*, else *source*.

    A bundled file can back several names (`us-states` and `us-counties`), and the
    resolved chart no longer says which one the author wrote, so all are listed.
    """
    names = [
        str(name)
        for name, info in get_config().geo_sources.items()
        if info["url"] == source
    ]
    return " / ".join(names) or source


def _unavailable(source: str, reason: str) -> ChartDataError:
    return ChartDataError.from_code(
        ERR_GEO_SOURCE_UNAVAILABLE, source=_author_name(source), reason=reason
    )


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to http(s): urllib would follow one to `file:`."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not newurl.startswith(("http://", "https://")):
            raise _unavailable(req.full_url, f"redirects to a non-http(s) URL {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpOnlyRedirects())


def fetch_url(url: str) -> bytes:
    """The default fetcher: a plain http(s) GET, uncached.

    Caching belongs to a host that can say whose data it holds.
    """
    if not url.startswith(("http://", "https://")):
        raise _unavailable(url, "only http(s) URLs can be fetched")
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    chunks: list[bytes] = []
    size = 0
    with _opener.open(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
        while chunk := response.read(_READ_CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_GEOMETRY_BYTES:
                raise _unavailable(url, f"larger than {MAX_GEOMETRY_BYTES} bytes")
            if time.monotonic() > deadline:
                raise _unavailable(url, f"not finished in {FETCH_DEADLINE_SECONDS}s")
            chunks.append(chunk)
    return b"".join(chunks)


_fetcher: ContextVar[GeometryFetcher] = ContextVar(
    "_geometry_fetcher", default=fetch_url
)


@contextmanager
def geometry_fetcher_scope(fetcher: GeometryFetcher) -> Generator[None]:
    """Fetch non-bundled geo sources through *fetcher* for the duration of a render."""
    token = _fetcher.set(fetcher)
    try:
        yield
    finally:
        _fetcher.reset(token)


def _parse(source: str, body: bytes) -> GeoDocument:
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise _unavailable(source, f"not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _unavailable(source, "not a JSON object")
    return document


@cache
def _bundled_bytes(name: str) -> bytes:
    source = f"{BUNDLED_PREFIX}{name}"
    if PurePosixPath(name).name != name or not name.endswith(".json"):
        raise _unavailable(source, "no bundled map has that name")
    resource = files("dbt_charts.core.render.geo").joinpath(name)
    if not resource.is_file():
        raise _unavailable(source, "no bundled map has that name")
    return resource.read_bytes()


def load_geometry(source: str) -> GeoDocument:
    """The parsed document for *source*, a fresh object on every call.

    Raises:
        ChartDataError: ERR-GEO-SOURCE-UNAVAILABLE when the source cannot be
            read, is over the size cap, or is not a JSON object.
    """
    if source.startswith(BUNDLED_PREFIX):
        return _parse(source, _bundled_bytes(source.removeprefix(BUNDLED_PREFIX)))
    try:
        body = _fetcher.get()(source)
    except (OSError, http.client.HTTPException) as exc:
        raise _unavailable(source, str(exc) or repr(exc)) from exc
    return _parse(source, body)
