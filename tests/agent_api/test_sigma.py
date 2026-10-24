"""Tests for ``import_sigma`` — HTTP is mocked at ``urllib.request.urlopen``."""

from __future__ import annotations

import hashlib
import io
import json
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from dbt_charts.agent_api import sigma as module
from dbt_charts.agent_api.sigma import (
    SigmaWorkbook,
    import_sigma,
    list_sigma_workbooks,
)
from dbt_charts.agent_api.source_bundle import BundleError

SECRET = "sekrit-client-secret"
TOKEN = "tok-abc-123"
BASE = "https://aws-api.sigma.test"
WID = "wb-uuid-1"
URL_ID = "AbC123xyz"
LAYOUT = """<?xml version="1.0" encoding="utf-8"?>
<Page type="grid" id="p1">
  <Element elementId="c1" gridColumn="13 / 25" gridRow="5 / 9"/>
  <Element elementId="k1" gridColumn="1 / 13" gridRow="5 / 9"/>
  <Element elementId="ctl" gridColumn="1 / 5" gridRow="1 / 3"/>
  <Element elementId="t1" gridColumn="1 / 25" gridRow="9 / 10"/>
</Page>
<Page type="grid" id="p2">
  <Element elementId="h1" gridColumn="1 / 25" gridRow="1 / 5"/>
  <Element elementId="hk" gridColumn="1 / 5" gridRow="5 / 7"/>
</Page>
"""
ELEMENTS: list[dict[str, Any]] = [
    {
        "id": "c1",
        "kind": "bar-chart",
        "name": "Revenue by month",
        "source": {"kind": "table", "elementId": "h1"},
        "columns": [{"id": "m", "formula": 'DateTrunc("month", [Day])'}],
        "xAxis": {"columnIds": ["m"]},
    },
    {"id": "k1", "kind": "kpi-chart", "name": {"text": "Total revenue"}},
    {
        "id": "ctl",
        "kind": "control",
        "controlId": "Region",
        "controlType": "list",
        "values": ["EMEA"],
        "filters": [{"source": {"kind": "table", "elementId": "c1"}, "columnId": "m"}],
        "source": {
            "kind": "source",
            "source": {"kind": "table", "elementId": "h1"},
            "columnId": "m",
        },
    },
    {"id": "t1", "kind": "text", "body": "Notes\n  here"},
    {"id": "h1", "kind": "table", "name": "Prep", "columns": []},
    {"id": "hk", "kind": "kpi-chart", "name": "Hidden kpi"},
]
CONTENTS = {
    "workbookId": WID,
    "workbookUrlId": URL_ID,
    "name": "Revenue",
    "url": f"https://app.sigma.test/acme/workbook/{URL_ID}",
    "path": "Team/Revenue",
    "contents": {"elements": ELEMENTS, "layout": LAYOUT},
}
PAGES = [
    {"pageId": "p1", "name": "Summary & Co"},
    {"pageId": "p2", "name": "Data prep", "hidden": True},
]
QUERIES = [
    {"elementId": "c1", "name": "Revenue by month", "sql": "select 1 as c1"},
    {"elementId": "k1", "name": "Total revenue", "sql": "select 2 as k1"},
    {"elementId": "h1", "name": "Prep", "sql": "select 3 as h1"},
]
WORKBOOKS = [
    {
        "workbookId": WID,
        "workbookUrlId": URL_ID,
        "name": "Revenue",
        "path": "Team/Revenue",
        "url": CONTENTS["url"],
        "updatedAt": "2026-10-01T00:00:00Z",
    },
    {
        "workbookId": "wb-2",
        "workbookUrlId": "Other999",
        "name": "Ops",
        "path": "Team/Ops",
        "url": "https://app.sigma.test/acme/workbook/Other999",
        "updatedAt": "2026-10-20T00:00:00Z",
    },
]

Route = Callable[[urllib.request.Request], bytes]


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _json(data: Any) -> Route:
    return lambda request: json.dumps(data).encode()


def _still_pending(request: urllib.request.Request) -> bytes:
    return b""


def _routes() -> dict[tuple[str, str], Route]:
    polls: dict[str, int] = {}

    def download(request: urllib.request.Request) -> bytes:
        query_id = urlparse(request.full_url).path.split("/")[-2]
        polls[query_id] = polls.get(query_id, 0) + 1
        return b"" if polls[query_id] == 1 else f"bytes-of-{query_id}".encode()

    def export(request: urllib.request.Request) -> bytes:
        assert isinstance(request.data, bytes)
        body = json.loads(request.data)
        return json.dumps({"queryId": body.get("pageId") or body["elementId"]}).encode()

    routes: dict[tuple[str, str], Route] = {
        ("POST", "/v2/auth/token"): _json({"access_token": TOKEN}),
        ("GET", "/v2/workbooks"): lambda request: json.dumps(
            {"entries": WORKBOOKS[:1], "nextPage": "n2"}
            if "page=n2" not in request.full_url
            else {"entries": WORKBOOKS[1:]}
        ).encode(),
        ("GET", f"/v2/workbooks/{WID}"): lambda request: json.dumps(
            CONTENTS, indent=1
        ).encode(),
        ("GET", f"/v2/workbooks/{WID}/pages"): _json({"entries": PAGES}),
        ("GET", f"/v2/workbooks/{WID}/queries"): _json({"entries": QUERIES}),
        ("POST", f"/v2/workbooks/{WID}/export"): export,
    }
    for query_id in ("p1", "k1"):
        routes[("GET", f"/v2/query/{query_id}/download")] = download
    return routes


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], Route]:
    routes = _routes()
    monkeypatch.setattr(module, "POLL_SECONDS", 0)
    monkeypatch.setenv("SIGMA_CLIENT_ID", "client-id")
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", SECRET)

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        handler = routes.get((request.get_method(), urlparse(request.full_url).path))
        if handler is None:
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                f"Not Found token {TOKEN}",
                Message(),
                io.BytesIO(),
            )
        return _Response(handler(request))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return routes


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _run(tmp_path: Path, ref: str = URL_ID, **kwargs: Any) -> Path:
    options: dict[str, Any] = {"out": tmp_path / "b", "base_url": BASE, "force": False}
    return import_sigma(ref, **{**options, **kwargs})


def test_writes_complete_bundle(http: Any, tmp_path: Path) -> None:
    out = _run(tmp_path)

    assert _files(out) == {
        "outline.md",
        "source.json",
        "contents.json",
        "pages.json",
        "png/00-summary-co.png",
        "kpi/total-revenue-k1.csv",
        "queries/c1.sql",
        "queries/k1.sql",
        "queries/h1.sql",
    }
    assert (out / "queries/c1.sql").read_text() == "select 1 as c1"
    assert (out / "png/00-summary-co.png").read_bytes() == b"bytes-of-p1"
    assert (out / "kpi/total-revenue-k1.csv").read_bytes() == b"bytes-of-k1"


def test_source_json_records_tool_url_sha_and_no_timezone(
    http: Any, tmp_path: Path
) -> None:
    out = _run(tmp_path, ref=f"https://u:pw@app.sigma.test/acme/workbook/{URL_ID}?t=zz")

    source = json.loads((out / "source.json").read_text())
    assert source["tool"] == "sigma"
    assert source["url"] == f"https://app.sigma.test/acme/workbook/{URL_ID}"
    assert (
        source["raw_sha256"]
        == hashlib.sha256((out / "contents.json").read_bytes()).hexdigest()
    )
    assert source["fetched_at"]
    assert "query_timezone" not in source


def test_outline_has_hidden_pages_control_targets_and_sql_paths(
    http: Any, tmp_path: Path
) -> None:
    outline = (_run(tmp_path) / "outline.md").read_text()

    assert "## Page: Summary & Co" in outline
    assert "## Page: Data prep (hidden — data prep)" in outline
    assert (
        "control Region: list" in outline and "targets=['Revenue by month']" in outline
    )
    assert "filtered by controls: Region" in outline
    assert "sql: queries/c1.sql" in outline
    assert "sql: queries/k1.sql" in outline
    order = [outline.index(t) for t in ("`k1`", "`c1`")]
    assert order == sorted(order)  # k1 sits at row 5 col 1, before c1 at col 13


def test_every_query_gets_its_file_and_an_outline_reference(
    http: Any, tmp_path: Path
) -> None:
    out = _run(tmp_path)
    outline = (out / "outline.md").read_text()

    for query in QUERIES:
        assert f"queries/{query['elementId']}.sql" in outline
        assert (out / "queries" / f"{query['elementId']}.sql").is_file()


def test_no_credential_or_token_is_written(http: Any, tmp_path: Path) -> None:
    out = _run(tmp_path)

    for path in out.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert SECRET.encode() not in data
            assert TOKEN.encode() not in data


def test_failed_export_names_it_leaves_nothing_and_hides_the_token(
    http: Any, tmp_path: Path
) -> None:
    def server_error(request: urllib.request.Request) -> bytes:
        raise urllib.error.HTTPError(
            request.full_url, 500, f"boom {TOKEN}", Message(), io.BytesIO()
        )

    http[("GET", "/v2/query/k1/download")] = server_error

    with pytest.raises(BundleError) as exc:
        _run(tmp_path)

    assert "kpi/total-revenue-k1.csv" in str(exc.value) and "500" in str(exc.value)
    assert TOKEN not in "".join(traceback.format_exception(exc.value))
    assert not (tmp_path / "b").exists()
    assert list(tmp_path.iterdir()) == []


def test_unfinished_export_times_out_naming_it(
    http: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "POLL_LIMIT", 3)
    http[("GET", "/v2/query/p1/download")] = _still_pending

    with pytest.raises(BundleError, match="png/00-summary-co.png.*in time"):
        _run(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_not_ready_statuses_are_polled_again(
    http: Any, tmp_path: Path
) -> None:
    statuses = iter([409, 404])

    def not_ready_then_ready(request: urllib.request.Request) -> bytes:
        code = next(statuses, None)
        if code is None:
            return b"bytes-of-k1"
        raise urllib.error.HTTPError(
            request.full_url, code, "not ready", Message(), io.BytesIO()
        )

    http[("GET", "/v2/query/k1/download")] = not_ready_then_ready

    out = _run(tmp_path)

    assert (out / "kpi/total-revenue-k1.csv").read_bytes() == b"bytes-of-k1"


def test_query_for_an_element_missing_from_the_layout_is_an_error(
    http: Any, tmp_path: Path
) -> None:
    http[("GET", f"/v2/workbooks/{WID}/queries")] = _json(
        {"entries": [*QUERIES, {"elementId": "ghost", "name": "n", "sql": "select 1"}]}
    )

    with pytest.raises(BundleError, match="ghost"):
        _run(tmp_path)


def test_failed_export_submission_is_an_error(http: Any, tmp_path: Path) -> None:
    del http[("POST", f"/v2/workbooks/{WID}/export")]

    with pytest.raises(BundleError, match="POST workbooks/.*/export failed: HTTP 404"):
        _run(tmp_path)


def test_workbook_without_compiled_sql_is_not_a_bundle(
    http: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http[("GET", f"/v2/workbooks/{WID}/queries")] = _json({"entries": []})

    with pytest.raises(BundleError, match=r"queries/\*\.sql"):
        _run(tmp_path)


def test_unsafe_element_id_is_refused(http: Any, tmp_path: Path) -> None:
    http[("GET", f"/v2/workbooks/{WID}/queries")] = _json(
        {"entries": [{"elementId": "../x", "name": "n", "sql": "select 1"}]}
    )

    with pytest.raises(BundleError, match="element id"):
        _run(tmp_path)


@pytest.mark.parametrize("missing", ["SIGMA_CLIENT_ID", "SIGMA_CLIENT_SECRET"])
def test_missing_credentials_name_the_variable(
    http: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)

    with pytest.raises(BundleError, match=missing):
        _run(tmp_path)


def test_missing_base_url_names_the_variable(http: Any, tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="SIGMA_API_BASE"):
        _run(tmp_path, base_url=None)


@pytest.mark.parametrize(
    "ref",
    [
        URL_ID,
        WID,
        f"https://app.sigma.test/acme/workbook/{URL_ID}/?:tab=x",
        f"https://app.sigma.test/acme/workbook/Revenue-Dashboard-{URL_ID}",
    ],
)
def test_ref_matches_url_id_workbook_id_or_url(
    http: Any, tmp_path: Path, ref: str
) -> None:
    assert (_run(tmp_path, ref=ref) / "source.json").is_file()


def test_unknown_workbook_is_an_error(http: Any, tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="nope"):
        _run(tmp_path, ref="nope")


def test_reused_out_dir_is_refused_without_force(http: Any, tmp_path: Path) -> None:
    out = _run(tmp_path)

    with pytest.raises(BundleError, match="--force"):
        _run(tmp_path)
    assert _run(tmp_path, force=True) == out


def test_list_sigma_workbooks_is_newest_first_filterable_and_secret_free(
    http: Any,
) -> None:
    found = list_sigma_workbooks(None, base_url=BASE)

    assert [w.name for w in found] == ["Ops", "Revenue"]
    assert list_sigma_workbooks("rev", base_url=BASE) == [found[1]]
    assert found[1] == SigmaWorkbook(
        URL_ID, "Revenue", "Team/Revenue", CONTENTS["url"], "2026-10-01T00:00:00Z"
    )
    assert SECRET not in repr(found) and TOKEN not in repr(found)


def test_list_entry_missing_a_field_names_it(http: Any) -> None:
    http[("GET", "/v2/workbooks")] = _json({"entries": [{"workbookUrlId": "x"}]})

    with pytest.raises(BundleError, match="'name'"):
        list_sigma_workbooks(None, base_url=BASE)
