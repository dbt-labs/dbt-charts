"""Tests for ``import_omni`` / ``list_omni_dashboards`` — HTTP mocked at ``urlopen``."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

import pytest

from dbt_charts.agent_api import omni as module
from dbt_charts.agent_api.omni import OmniDashboard, import_omni, list_omni_dashboards
from dbt_charts.agent_api.source_bundle import BundleError

from .._outline import missing_options, tile_block

FIXTURES = Path(__file__).parent / "omni_fixtures"
KEY = "sekrit-omni-key"
BASE = "https://acme.omniapp.test"
DOC = "abc12345"

Handler = Callable[[urllib.request.Request], bytes]


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _body(request: urllib.request.Request) -> dict[str, Any]:
    assert isinstance(request.data, bytes)
    return json.loads(request.data)


def _plan(request: urllib.request.Request) -> bytes:
    body = _body(request)
    if "planOnly" not in body:
        assert body["resultType"] == "csv"
        return _fixture("kpi_1.csv")
    first_field = body["query"]["fields"][0]
    return _fixture(
        "plan_1.ndjson" if first_field == "orders.total_revenue" else "plan_2.ndjson"
    )


def _routes(export: bytes) -> dict[tuple[str, str], Handler]:
    polls = iter([b'{"status": "in_progress"}', b'{"status": "complete"}'])
    return {
        ("GET", f"/unstable/documents/{DOC}/export"): lambda r: export,
        ("GET", f"/v1/documents/{DOC}/queries"): lambda r: _fixture("queries.json"),
        ("POST", "/v1/query/run"): _plan,
        ("POST", f"/v1/dashboards/{DOC}/download"): lambda r: b'{"job_id": "j1"}',
        ("GET", f"/v1/dashboards/{DOC}/download/j1/status"): lambda r: next(polls),
        ("GET", f"/v1/dashboards/{DOC}/download/j1"): lambda r: b"\x89PNG-bytes",
    }


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], Handler]:
    return _install(monkeypatch, _routes(_fixture("export.json")))


def _install(
    monkeypatch: pytest.MonkeyPatch, routes: dict[tuple[str, str], Handler]
) -> dict[tuple[str, str], Handler]:
    monkeypatch.setattr(module, "POLL_SECONDS", 0)
    monkeypatch.setenv("OMNI_API_KEY", KEY)
    monkeypatch.delenv("OMNI_BASE_URL", raising=False)

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        assert request.get_header("Authorization") == f"Bearer {KEY}"
        path = urlparse(request.full_url).path.removeprefix("/api")
        handler = routes.get((request.get_method(), path))
        if handler is None:
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                Message(),
                io.BytesIO(b'{"detail": "No document"}'),
            )
        return _Response(handler(request))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return routes


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_writes_complete_bundle(http: Any, tmp_path: Path) -> None:
    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    assert _files(tmp_path) == {
        "outline.md",
        "source.json",
        "dashboard.json",
        "queries/1.json",
        "queries/1.sql",
        "queries/2.json",
        "queries/2.sql",
        "kpi/closed-won-1.csv",
        "png/dashboard.png",
    }
    assert "SUM" in (tmp_path / "queries/1.sql").read_text()
    assert (
        tmp_path / "kpi/closed-won-1.csv"
    ).read_text() == 'Total Revenue\n"11,110"\n'


def test_source_json_records_url_hash_and_query_timezone(
    http: Any, tmp_path: Path
) -> None:
    import_omni(f"{BASE}/dashboards/{DOC}", out=tmp_path, base_url=None, force=False)

    source = json.loads((tmp_path / "source.json").read_text())
    assert source["tool"] == "omni"
    assert source["url"] == f"{BASE}/dashboards/{DOC}"
    assert source["query_timezones"] == ["America/Los_Angeles"]
    raw = (tmp_path / "dashboard.json").read_bytes()
    assert source["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert raw == _fixture("export.json")


def test_outline_lists_tiles_in_reading_order_with_their_query_shape(
    http: Any, tmp_path: Path
) -> None:
    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    outline = (tmp_path / "outline.md").read_text()
    assert outline.index("Closed Won") < outline.index("Revenue by month")
    for expected in (
        "# Sales overview",
        "**kpi** `1` 'Closed Won'",
        "values: kpi/closed-won-1.csv",
        "**lineColor** `2` 'Revenue by month'",
        "pivots: orders.region",
        "table calc: calc_1 'Running total'",
        "sort: orders.created_at[month] asc",
        "filter: orders.status EQUALS values=['Closed Won']",
        "listens: f1 -> orders.created_at[date]",
        "- Order date `f1` orders.created_at: TIME_FOR_INTERVAL_DURATION "
        "ui_type=PAST left_side=90 days ago right_side=90 days",
        "- Region `f2` orders.region: EQUALS values=['EMEA']",
        "- Segment `f3`: EQUALS",
        "**text** 'Sales overview'",
        "**hidden** `3` 'Retired tile'",
    ):
        assert expected in outline, expected


def test_pages_layout_renders_one_png_per_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    export = json.loads(_fixture("export.json"))
    export["dashboard"]["metadata"]["layouts"] = {"lg": []}
    export["dashboard"]["metadata"]["containers"] = [
        {
            "name": "Overview",
            "containerType": "page",
            "instanceKey": "page-a",
            "container": {
                "instanceKey": "page-a-root",
                "children": [
                    {
                        "name": "Row",
                        "containerType": "stack",
                        "children": [{"id": "1", "type": "query", "as": "chart"}],
                    }
                ],
            },
        },
        {
            "name": "Trends",
            "containerType": "page",
            "instanceKey": "page-b",
            "container": {
                "instanceKey": "page-b-root",
                "children": [
                    {"id": "2", "type": "query", "as": "metadata", "format": "name"},
                    {"id": "2", "type": "query", "as": "chart"},
                ],
            },
        },
        {
            "children": [],
            "direction": "row",
            "instanceKey": "filter-bar",
            "containerType": "stack",
        },
    ]
    sent: list[dict[str, Any]] = []
    routes = _routes(json.dumps(export).encode())
    ids = iter(["j1", "j2"])
    routes[("POST", f"/v1/dashboards/{DOC}/download")] = lambda r: (
        sent.append(_body(r)) or json.dumps({"job_id": next(ids)}).encode()
    )
    for job in ("j1", "j2"):
        routes[("GET", f"/v1/dashboards/{DOC}/download/{job}/status")] = lambda r: (
            b'{"status": "complete"}'
        )
        routes[("GET", f"/v1/dashboards/{DOC}/download/{job}")] = lambda r, job=job: (
            f"png-{job}".encode()
        )
    _install(monkeypatch, routes)

    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    assert [s["containerPages"] for s in sent] == [["page-a"], ["page-b"]]
    assert (tmp_path / "png/overview.png").read_bytes() == b"png-j1"
    assert (tmp_path / "png/trends.png").read_bytes() == b"png-j2"
    outline = (tmp_path / "outline.md").read_text()
    assert "## Page: Overview" in outline
    assert outline.count("`2` 'Revenue by month'") == 1


def test_missing_api_key_names_the_variable(
    http: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OMNI_API_KEY")

    with pytest.raises(BundleError, match="OMNI_API_KEY") as exc:
        import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    assert "API Keys" in str(exc.value)
    assert ".env" in str(exc.value)


def test_bare_id_without_base_url_names_the_setting(http: Any, tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="OMNI_BASE_URL"):
        import_omni(DOC, out=tmp_path, base_url=None, force=False)


def test_non_dashboard_url_is_rejected(http: Any, tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="/dashboards/<id>"):
        import_omni(f"{BASE}/w/{DOC}", out=tmp_path, base_url=None, force=False)


def test_failed_request_names_the_endpoint_and_omnis_reason_never_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    del routes[("GET", f"/v1/documents/{DOC}/queries")]
    _install(monkeypatch, routes)

    with pytest.raises(
        BundleError, match=r"GET /v1/documents/abc12345/queries.*404.*No document"
    ) as exc:
        import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    assert KEY not in str(exc.value)


def test_download_error_status_fails_with_the_job_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    routes[("GET", f"/v1/dashboards/{DOC}/download/j1/status")] = lambda r: (
        b'{"status": "error", "error": "render crashed"}'
    )
    _install(monkeypatch, routes)

    with pytest.raises(BundleError, match="render crashed"):
        import_omni(DOC, out=tmp_path, base_url=BASE, force=False)


def test_plan_without_query_timezone_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    routes[("POST", "/v1/query/run")] = lambda r: (
        b'{"jobs_submitted": {}}\n{"status": "PLANNED", "summary": {"display_sql": "SELECT 1"}}\n'
    )
    _install(monkeypatch, routes)

    with pytest.raises(BundleError, match="query_timezone"):
        import_omni(DOC, out=tmp_path, base_url=BASE, force=False)


def test_layout_tile_without_a_saved_query_is_named_and_the_rest_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    saved = json.loads(_fixture("queries.json"))
    saved["queries"] = [
        q for q in saved["queries"] if q["queryIdentifierMapKey"] != "1"
    ]
    routes = _routes(_fixture("export.json"))
    routes[("GET", f"/v1/documents/{DOC}/queries")] = lambda r: json.dumps(
        saved
    ).encode()
    _install(monkeypatch, routes)

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert "- **missing** `1`" in outline
    assert (tmp_path / "b" / "queries/2.sql").exists()


def test_dashboard_whose_every_tile_lacks_a_saved_query_fails_naming_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    export = json.loads(_fixture("export.json"))
    export["dashboard"]["metadata"]["layouts"]["lg"] = [
        t for t in export["dashboard"]["metadata"]["layouts"]["lg"] if t["i"] != "txt-1"
    ]
    routes = _routes(json.dumps(export).encode())
    routes[("GET", f"/v1/documents/{DOC}/queries")] = lambda r: b'{"queries": []}'
    _install(monkeypatch, routes)

    with pytest.raises(
        BundleError, match=r"1 \(no saved query\).*2 \(no saved query\)"
    ):
        import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)


def test_a_failed_job_with_no_error_type_aborts_the_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    plan = routes[("POST", "/v1/query/run")]
    failed = b'{"job_id": "j", "status": "FAILED", "error_type": null, "error_message": "x"}\n'
    routes[("POST", "/v1/query/run")] = lambda r: (
        failed if _body(r)["query"]["fields"][0] == "orders.total_revenue" else plan(r)
    )
    _install(monkeypatch, routes)

    with pytest.raises(BundleError, match="did not plan"):
        import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)


def test_every_plan_error_type_leaves_the_tile_unplanned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    plan = routes[("POST", "/v1/query/run")]
    failed = (
        b'{"job_id": "j", "status": "FAILED", "error_type": "PLAN_MISSING_MODEL_OBJECT", '
        b'"error_message": "No such field orders.gone"}\n'
    )
    routes[("POST", "/v1/query/run")] = lambda r: (
        failed if _body(r)["query"]["fields"][0] == "orders.total_revenue" else plan(r)
    )
    _install(monkeypatch, routes)

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert (
        "    - sql: none, Omni could not plan it: No such field orders.gone" in outline
    )
    assert (tmp_path / "b" / "queries/2.sql").exists()


def test_a_kpi_whose_value_run_fails_is_kept_without_a_values_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    plan = routes[("POST", "/v1/query/run")]
    routes[("POST", "/v1/query/run")] = lambda r: (
        plan(r) if "planOnly" in _body(r) else _fail_with(408)(r)
    )
    _install(monkeypatch, routes)

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert "    - values: none, Omni could not run it: HTTP 408" in outline
    assert (tmp_path / "b" / "queries/1.sql").exists()
    assert not list((tmp_path / "b").glob("kpi/*"))
    assert (tmp_path / "b" / "queries/2.sql").exists()


def test_a_render_that_does_not_finish_is_named_and_the_bundle_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    routes[("GET", f"/v1/dashboards/{DOC}/download/j1/status")] = lambda r: (
        b'{"status": "in_progress"}'
    )
    _install(monkeypatch, routes)
    monkeypatch.setattr(module, "POLL_LIMIT", 2)

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert "## Not fetched: Omni failed to export these" in outline
    assert "- `png/dashboard.png`: Omni render j1 did not finish in time" in outline
    assert not list((tmp_path / "b").glob("png/*"))
    assert (tmp_path / "b" / "queries/1.sql").exists()


def test_a_render_already_in_progress_is_named_and_the_bundle_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))

    def busy(request: urllib.request.Request) -> bytes:
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            Message(),
            io.BytesIO(
                b'{"detail": "A download is already in progress", '
                b'"existing_job_id": "old-7", "status": "EXECUTING"}'
            ),
        )

    routes[("POST", f"/v1/dashboards/{DOC}/download")] = busy
    _install(monkeypatch, routes)

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert (
        "- `png/dashboard.png`: Omni render already in progress (job old-7)" in outline
    )
    assert not list((tmp_path / "b").glob("png/*"))
    assert (tmp_path / "b" / "queries/1.sql").exists()


def test_a_failed_job_that_is_not_a_plan_error_aborts_the_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    routes[("POST", "/v1/query/run")] = lambda r: (
        b'{"job_id": "j", "status": "FAILED", "error_type": "TIMEOUT", '
        b'"error_message": "warehouse busy"}\n'
    )
    _install(monkeypatch, routes)

    with pytest.raises(BundleError, match="did not plan"):
        import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)


def test_list_returns_only_dashboards_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursors: list[str | None] = []

    def page(request: urllib.request.Request) -> bytes:
        cursor = parse_qs(urlparse(request.full_url).query).get("cursor")
        cursors.append(cursor[0] if cursor else None)
        return _fixture("documents_page2.json" if cursor else "documents_page1.json")

    _install(monkeypatch, {("GET", "/v1/documents"): page})

    documents = list_omni_dashboards(base_url=BASE)

    assert documents == [
        OmniDashboard(
            "abc12345",
            "organization",
            "Sales overview",
            "https://acme.omniapp.test/dashboards/abc12345",
        ),
        OmniDashboard(
            "def67890",
            "restricted",
            "Pipeline",
            "https://acme.omniapp.test/dashboards/def67890",
        ),
    ]
    assert cursors == [None, "c2"]


def test_rate_limited_request_is_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    export = routes[("GET", f"/unstable/documents/{DOC}/export")]
    limited = iter([True, True, False])

    def flaky(request: urllib.request.Request) -> bytes:
        if next(limited):
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests", Message(), io.BytesIO()
            )
        return export(request)

    routes[("GET", f"/unstable/documents/{DOC}/export")] = flaky
    _install(monkeypatch, routes)
    monkeypatch.setattr(module, "RATE_LIMIT_SECONDS", 0)

    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    assert (tmp_path / "source.json").is_file()


def test_persistent_rate_limit_fails_naming_the_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))

    def always(request: urllib.request.Request) -> bytes:
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many Requests", Message(), io.BytesIO()
        )

    routes[("GET", f"/unstable/documents/{DOC}/export")] = always
    _install(monkeypatch, routes)
    monkeypatch.setattr(module, "RATE_LIMIT_SECONDS", 0)

    with pytest.raises(BundleError, match="HTTP 429"):
        import_omni(DOC, out=tmp_path, base_url=BASE, force=False)


def test_non_empty_out_is_refused_without_force_and_replaced_with_it(
    http: Any, tmp_path: Path
) -> None:
    (tmp_path / "stale.txt").write_text("old")

    with pytest.raises(BundleError, match="--force"):
        import_omni(DOC, out=tmp_path, base_url=BASE, force=False)
    assert (tmp_path / "stale.txt").read_text() == "old"

    import_omni(DOC, out=tmp_path, base_url=BASE, force=True)

    assert not (tmp_path / "stale.txt").exists()
    assert (tmp_path / "source.json").is_file()


def _fail_with(code: int) -> Handler:
    def handler(request: urllib.request.Request) -> bytes:
        raise urllib.error.HTTPError(
            request.full_url, code, "no", Message(), io.BytesIO()
        )

    return handler


@pytest.mark.parametrize(
    ("route", "code"),
    [
        (("POST", f"/v1/dashboards/{DOC}/download"), 500),
        (("POST", "/v1/query/run"), 429),
    ],
)
def test_failed_build_leaves_old_bundle_and_no_temp_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: tuple[str, str],
    code: int,
) -> None:
    routes = _routes(_fixture("export.json"))
    routes[route] = _fail_with(code)
    _install(monkeypatch, routes)
    monkeypatch.setattr(module, "RATE_LIMIT_SECONDS", 0)
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "keep.txt").write_text("previous")

    with pytest.raises(BundleError, match=f"HTTP {code}"):
        import_omni(DOC, out=out, base_url=BASE, force=True)

    assert (out / "keep.txt").read_text() == "previous"
    assert [p.name for p in tmp_path.iterdir()] == ["bundle"]


def test_url_in_bundle_drops_userinfo_query_and_fragment(
    http: Any, tmp_path: Path
) -> None:
    ref = f"https://me:pw@acme.omniapp.test/dashboards/{DOC}?token=zzz#frag"

    import_omni(ref, out=tmp_path / "b", base_url=None, force=False)

    for name in ("source.json", "outline.md"):
        text = (tmp_path / "b" / name).read_text()
        assert "pw" not in text and "zzz" not in text and "frag" not in text
        assert f"{BASE}/dashboards/{DOC}" in text


def test_source_json_flags_kpi_values_as_display_formatted(
    http: Any, tmp_path: Path
) -> None:
    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    source = json.loads((tmp_path / "b" / "source.json").read_text())
    assert source["kpi_values"] == "display_formatted"


def _page_export(children: list[dict[str, Any]]) -> bytes:
    export = json.loads(_fixture("export.json"))
    export["dashboard"]["metadata"]["layouts"] = {"lg": []}
    export["dashboard"]["metadata"]["containers"] = [
        {
            "name": "Page",
            "containerType": "page",
            "instanceKey": "page-a",
            "container": {"instanceKey": "page-a-root", "children": children},
        }
    ]
    return json.dumps(export).encode()


def test_divider_tile_is_listed_without_a_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    children = [
        {"id": "1", "type": "query", "as": "chart"},
        {"type": "inline-divider", "id": "d1"},
    ]
    _install(monkeypatch, _routes(_page_export(children)))

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert "- **divider** `d1` — page layout" in outline
    assert (tmp_path / "b" / "queries/1.sql").exists()
    assert not (tmp_path / "b" / "queries/d1.sql").exists()


def test_unknown_tile_type_is_named_and_the_rest_of_the_bundle_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    children = [
        {"type": "inline-map", "id": "m1", "title": "Stores"},
        {"id": "1", "type": "query", "as": "chart"},
    ]
    _install(monkeypatch, _routes(_page_export(children)))

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert "- **unsupported** `m1` 'Stores' — page layout" in outline
    assert "    - unsupported tile type 'inline-map': left out" in outline
    assert (tmp_path / "b" / "queries/1.sql").exists()


def test_dashboard_with_no_representable_tile_fails_naming_the_types(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    children = [
        {"type": "inline-map", "instanceKey": "k1"},
        {"type": "inline-divider", "id": "d1"},
    ]
    _install(monkeypatch, _routes(_page_export(children)))

    with pytest.raises(BundleError, match="inline-map"):
        import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)


@pytest.mark.parametrize("container_type", ["grid", "stack"])
def test_top_level_container_outside_a_page_is_listed_as_a_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, container_type: str
) -> None:
    export = json.loads(_page_export([]))
    export["dashboard"]["metadata"]["containers"] = [
        {
            "instanceKey": "g1",
            "containerType": container_type,
            "children": [{"id": "1", "type": "query", "as": "chart"}],
        },
        {"instanceKey": "empty", "containerType": "stack", "children": []},
    ]
    _install(monkeypatch, _routes(json.dumps(export).encode()))

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert outline.count("## Page: Dashboard") == 1
    assert "`1` 'Closed Won'" in outline
    assert (tmp_path / "b" / "png/dashboard.png").exists()


def test_a_tile_omni_cannot_plan_is_named_and_the_others_are_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _routes(_fixture("export.json"))
    plan = routes[("POST", "/v1/query/run")]
    failed = (
        b'{"job_id": "j", "status": "FAILED", "error_type": "PLAN", '
        b'"error_message": "Filter on field gone.field not found"}\n'
    )
    routes[("POST", "/v1/query/run")] = lambda r: (
        failed if _body(r)["query"]["fields"][0] == "orders.total_revenue" else plan(r)
    )
    _install(monkeypatch, routes)

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert (
        "    - sql: none, Omni could not plan it: Filter on field gone.field not found"
        in outline
    )
    assert (tmp_path / "b" / "queries/2.sql").exists()
    assert not (tmp_path / "b" / "queries/1.sql").exists()
    assert not list((tmp_path / "b").glob("kpi/*"))
    assert (tmp_path / "b" / "source.json").exists()


def test_table_calc_without_a_label_is_listed_by_its_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    saved = json.loads(_fixture("queries.json"))
    for query in saved["queries"]:
        for calc in query["query"]["calculations"]:
            del calc["label"]
    routes = _routes(_fixture("export.json"))
    routes[("GET", f"/v1/documents/{DOC}/queries")] = lambda r: json.dumps(
        saved
    ).encode()
    _install(monkeypatch, routes)

    import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b" / "outline.md").read_text()
    assert "table calc: calc_1" in outline
    assert "table calc: calc_1 '" not in outline


def test_list_names_the_key_omni_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    page = json.loads(_fixture("documents_page2.json"))
    del page["records"][0]["scope"]
    _install(
        monkeypatch, {("GET", "/v1/documents"): lambda r: json.dumps(page).encode()}
    )

    with pytest.raises(BundleError, match="scope"):
        list_omni_dashboards(base_url=BASE)


def test_pages_whose_names_collide_as_file_names_fail_before_any_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    export = json.loads(_fixture("export.json"))
    export["dashboard"]["metadata"]["layouts"] = {"lg": []}
    export["dashboard"]["metadata"]["containers"] = [
        {
            "name": name,
            "containerType": "page",
            "instanceKey": key,
            "container": {"instanceKey": f"{key}-root", "children": []},
        }
        for name, key in (("A B", "page-a"), ("A-B", "page-b"))
    ]
    routes = _routes(json.dumps(export).encode())

    def unreachable(request: urllib.request.Request) -> bytes:
        pytest.fail("no request may follow a page-name collision")

    routes[("POST", "/v1/query/run")] = unreachable
    routes[("POST", f"/v1/dashboards/{DOC}/download")] = unreachable
    _install(monkeypatch, routes)

    with pytest.raises(BundleError, match="collide as file names"):
        import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("###", "(empty)"),
        ("\n\n# Hello\nbody", "Hello"),
        ("   ", "(empty)"),
        ("# Sales overview\nowner", "Sales overview"),
    ],
)
def test_title_is_the_first_non_blank_line_without_heading_marks(
    text: str, title: str
) -> None:
    assert module._title(text) == title


def test_source_json_marks_the_outline_version(http: Any, tmp_path: Path) -> None:
    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    assert json.loads((tmp_path / "source.json").read_text())["outline_version"] == 2


def test_every_tile_names_where_its_full_config_lives(
    http: Any, tmp_path: Path
) -> None:
    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)
    block = tile_block((tmp_path / "outline.md").read_text(), "2")

    assert "config: queries/2.json, dashboard.json#queryPresentation[p2]" in block


def test_outline_carries_query_and_chart_options_without_colors(
    http: Any, tmp_path: Path
) -> None:
    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)
    block = tile_block((tmp_path / "outline.md").read_text(), "2")

    assert "limit: 1000" in block and "column_limit: 50" in block
    assert '"label":"Revenue","format":"CURRENCY_0"' in block
    assert "transposed_measures:" in block and "controls:" in block
    assert "table calc: calc_1 'Running total'" in block and '"name":"SUM"' in block
    assert "chartType: lineColor" in block and "visType: basic" in block
    assert '"rule":{"type":"greater-than","value":0.5' in block
    assert "columnDisplay:" in block and "frozenColumn: orders.region" in block
    assert "#1DB9B9" not in block and "export-palette-1" not in block
    assert "bandSize" not in block


def test_every_raw_tile_option_is_in_the_outline_or_the_styling_set(
    http: Any, tmp_path: Path
) -> None:
    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)
    outline = (tmp_path / "outline.md").read_text()
    saved = json.loads(_fixture("queries.json"))["queries"]
    export = json.loads(_fixture("export.json"))["dashboard"]
    presentations = {
        m["queryPresentation"]["id"]: m["queryPresentation"]
        for m in export["queryPresentationCollection"][
            "queryPresentationCollectionMemberships"
        ]
    }
    for query in (q for q in saved if q["id"] != "p3"):  # p3 is hidden, not fetched
        block = tile_block(outline, query["queryIdentifierMapKey"])
        presentation = presentations[query["id"]]
        vis = presentation["visConfig"]
        assert (
            missing_options(
                query["query"], block, module.QUERY_NON_OPTION_KEYS.__contains__
            )
            == []
        ), query["id"]
        assert (
            missing_options(
                presentation, block, module.PRESENTATION_NON_OPTION_KEYS.__contains__
            )
            == []
        ), query["id"]
        assert (
            missing_options(vis, block, module.VIS_NON_OPTION_KEYS.__contains__) == []
        ), query["id"]
        assert (
            missing_options(
                vis.get("spec", {}), block, module.SPEC_STYLING_KEYS.__contains__
            )
            == []
        ), query["id"]


def test_unlisted_chart_option_still_reaches_the_outline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    export = json.loads(_fixture("export.json"))
    memberships = export["dashboard"]["queryPresentationCollection"][
        "queryPresentationCollectionMemberships"
    ]
    memberships[1]["queryPresentation"]["visConfig"]["spec"]["brandNew"] = "on"
    _install(monkeypatch, _routes(json.dumps(export).encode()))
    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    assert "brandNew: on" in tile_block((tmp_path / "outline.md").read_text(), "2")


TWO_STEP = (
    "-- step 1, run on Snowflake\n"
    'SELECT "A", SUM("B") AS "S" FROM "T" GROUP BY 1\n'
    "\n"
    "-- step 2, run on DuckDB\n"
    "-- use result of step 1 as step_ref__1\n"
    'SELECT "A", "S" * 2 AS "calc_1"\nFROM "step_ref__1"\nORDER BY "A"\n'
)


def _import_with_sql(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sql: str) -> Path:
    routes = _routes(_fixture("export.json"))
    first, second = _fixture("plan_2.ndjson").splitlines()
    plan = json.loads(second)
    plan["summary"]["display_sql"] = sql
    reply = first + b"\n" + json.dumps(plan).encode() + b"\n"
    routes[("POST", "/v1/query/run")] = lambda r: reply
    _install(monkeypatch, routes)
    return import_omni(DOC, out=tmp_path / "b", base_url=BASE, force=False)


def test_two_step_sql_splits_into_warehouse_file_and_duckdb_post_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _import_with_sql(monkeypatch, tmp_path, TWO_STEP)

    warehouse = (out / "queries/1.sql").read_text()
    assert warehouse.strip() == 'SELECT "A", SUM("B") AS "S" FROM "T" GROUP BY 1'
    post = (out / "queries/1.post.sql").read_text()
    assert post.startswith("SELECT")
    assert 'FROM "step_ref__1"' in post
    assert "step 1" not in post and "step 2" not in post
    assert (
        "    - post-step: queries/1.post.sql (DuckDB over step 1's result as "
        "`step_ref__1`: SELECT, FROM, ORDER BY)"
    ) in (out / "outline.md").read_text()


def test_two_step_sql_with_crlf_line_endings_still_splits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _import_with_sql(monkeypatch, tmp_path, TWO_STEP.replace("\n", "\r\n"))
    assert (out / "queries/1.post.sql").exists()
    assert "step 2" not in (out / "queries/1.sql").read_text()


def test_single_step_sql_is_written_unchanged_with_no_post_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _import_with_sql(monkeypatch, tmp_path, "SELECT 1")

    assert (out / "queries/1.sql").read_text() == "SELECT 1"
    assert not list(out.glob("queries/*.post.sql"))
    assert "post-step" not in (out / "outline.md").read_text()


@pytest.mark.parametrize(
    "sql",
    [
        TWO_STEP.replace("run on DuckDB", "run on Spark"),
        TWO_STEP.replace("-- use result of step 1 as step_ref__1\n", ""),
        TWO_STEP + "\n-- step 3, run on DuckDB\nSELECT 1\n",
        TWO_STEP.replace("-- step 1, run on Snowflake\n", ""),
    ],
)
def test_unrecognized_step_shape_fails_naming_the_tile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sql: str
) -> None:
    with pytest.raises(BundleError, match=r"tile `\d+`.*two-step"):
        _import_with_sql(monkeypatch, tmp_path, sql)


def _kpi_routes(
    markdown_config: list[dict[str, Any]],
    csv_body: bytes,
    query_extra: dict[str, Any] | None = None,
) -> dict[tuple[str, str], Handler]:
    export = json.loads(_fixture("export.json"))
    memberships = export["dashboard"]["queryPresentationCollection"][
        "queryPresentationCollectionMemberships"
    ]
    memberships[0]["queryPresentation"]["visConfig"]["spec"] = {
        "markdownConfig": markdown_config
    }
    queries = json.loads(_fixture("queries.json"))
    queries["queries"][0]["query"]["fields"] = ["orders.total_revenue", "orders.count"]
    queries["queries"][0]["query"].update(query_extra or {})
    routes = _routes(json.dumps(export).encode())
    routes[("GET", f"/v1/documents/{DOC}/queries")] = lambda r: json.dumps(
        queries
    ).encode()
    routes[("POST", "/v1/query/run")] = lambda r: (
        _plan(r) if "planOnly" in _body(r) else csv_body
    )
    return routes


def _number(field: str, row: str = "_first") -> dict[str, Any]:
    return {
        "type": "number",
        "config": {"field": {"row": row, "field": {"name": field}}},
    }


def _kpi_csv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    markdown_config: list[dict[str, Any]],
    body: bytes,
    query_extra: dict[str, Any] | None = None,
) -> list[str]:
    _install(monkeypatch, _kpi_routes(markdown_config, body, query_extra))
    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)
    return (tmp_path / "kpi/closed-won-1.csv").read_text().splitlines()


_THREE_ROWS = b'Revenue,Orders\n"$1,110",17\n$20,"4,000"\n$7,9\n'


def test_a_kpi_file_leads_with_the_headline_column_and_the_first_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _kpi_csv(
        monkeypatch,
        tmp_path,
        [{"type": "text"}, _number("orders.count")],
        _THREE_ROWS,
    )

    assert lines == ["Orders,Revenue", '17,"$1,110"']


@pytest.mark.parametrize(
    ("row", "expected"),
    [("_second", '"4,000",$20'), ("_last", "9,$7")],
)
def test_a_kpi_keeps_the_row_its_tile_displays(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, row: str, expected: str
) -> None:
    lines = _kpi_csv(monkeypatch, tmp_path, [_number("orders.count", row)], _THREE_ROWS)

    assert lines == ["Orders,Revenue", expected]


def test_a_totals_kpi_is_the_total_row_and_last_skips_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = b"Region,Revenue,Orders\nEMEA,$20,3\nAPAC,$7,9\n,$27,12\n"
    extra = {
        "fields": ["orders.region", "orders.total_revenue", "orders.count"],
        "column_totals": {"::total::": {"type": "aggregation"}},
    }
    totals = _kpi_csv(
        monkeypatch,
        tmp_path / "t",
        [_number("orders.count", "_totals.0")],
        body,
        extra,
    )
    last = _kpi_csv(
        monkeypatch, tmp_path / "l", [_number("orders.count", "_last")], body, extra
    )

    assert totals == ["Orders,Region,Revenue", "12,,$27"]
    assert last == ["Orders,Region,Revenue", "9,APAC,$7"]


def test_a_totals_kpi_without_a_total_row_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        _kpi_routes([_number("orders.count", "_totals.0")], _THREE_ROWS),
    )

    with pytest.raises(BundleError, match=r"Closed Won.*_totals\.0"):
        import_omni(DOC, out=tmp_path, base_url=BASE, force=False)


def test_a_kpi_with_an_unknown_row_spec_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        _kpi_routes([_number("orders.count", "_median")], _THREE_ROWS),
    )

    with pytest.raises(BundleError, match=r"Closed Won.*'_median'"):
        import_omni(DOC, out=tmp_path, base_url=BASE, force=False)


def test_a_kpi_without_a_number_entry_keeps_the_first_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _kpi_csv(monkeypatch, tmp_path, [{"type": "text"}], _THREE_ROWS)

    assert lines == ["Revenue,Orders", '"$1,110",17']


def test_a_kpi_headline_that_is_not_a_query_field_keeps_its_columns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _kpi_csv(
        monkeypatch, tmp_path, [_number("measure_value", "_last")], _THREE_ROWS
    )

    assert lines == ["Revenue,Orders", "$7,9"]


def test_a_pivoted_kpi_keeps_the_csv_as_omni_returned_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = b"Period,Now,Before\nWeek,Orders,Orders\n1,5,6\nTotal,5,6\n"
    _install(
        monkeypatch,
        _kpi_routes(
            [_number("orders.count", "_totals.0")], body, {"pivots": ["orders.region"]}
        ),
    )

    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    assert (tmp_path / "kpi/closed-won-1.csv").read_bytes() == body


def test_hidden_lines_skip_tiles_also_placed_on_a_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    export = json.loads(_fixture("export.json"))
    export["dashboard"]["metadata"]["hiddenTiles"] = ["1", "3"]
    _install(monkeypatch, _routes(json.dumps(export).encode()))

    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    outline = (tmp_path / "outline.md").read_text()
    assert "**hidden** `3`" in outline
    assert "**hidden** `1`" not in outline


def _filter_routes(
    tile_filter_map: dict[str, dict[str, str | Literal[False]]],
) -> tuple[dict[tuple[str, str], Handler], list[dict[str, Any]]]:
    export = json.loads(_fixture("export.json"))
    export["dashboard"]["metadata"]["tileFilterMap"] = tile_filter_map
    routes = _routes(json.dumps(export).encode())
    bodies: list[dict[str, Any]] = []
    inner = routes[("POST", "/v1/query/run")]

    def run(request: urllib.request.Request) -> bytes:
        bodies.append(_body(request))
        return inner(request)

    routes[("POST", "/v1/query/run")] = run
    return routes, bodies


def test_dashboard_filter_defaults_replace_the_tiles_filters_before_plan_and_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes, bodies = _filter_routes(
        {"1": {"f2": "orders.status", "f1": "orders.created_at[date]"}}
    )
    _install(monkeypatch, routes)

    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    tile_1 = [
        b["query"] for b in bodies if b["query"]["fields"] == ["orders.total_revenue"]
    ]
    assert len(tile_1) == 2
    for sent in tile_1:
        assert sent["filters"]["orders.status"] == {
            "kind": "EQUALS",
            "type": "string",
            "values": ["EMEA"],
            "is_negative": False,
        }
        assert sent["filters"]["orders.created_at"]["left_side"] == "90 days ago"
        assert "label" not in sent["filters"]["orders.created_at"]
    assert [b.get("planOnly") for b in bodies if b["query"] in tile_1] == [True, None]
    outline = (tmp_path / "outline.md").read_text()
    assert "filter: orders.status EQUALS values=['EMEA']" in outline
    assert "filter: orders.status EQUALS values=['Closed Won']" not in outline
    assert "dashboard filter applied: Region = EQUALS values=['EMEA']" in outline
    assert "dashboard filter applied: Order date = " in outline


def test_a_dashboard_filter_without_a_default_leaves_the_tiles_filter_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes, bodies = _filter_routes({"1": {"f3": "orders.status"}})
    _install(monkeypatch, routes)

    import_omni(DOC, out=tmp_path, base_url=BASE, force=False)

    sent = next(
        b["query"] for b in bodies if b["query"]["fields"] == ["orders.total_revenue"]
    )
    assert sent["filters"]["orders.status"]["values"] == ["Closed Won"]
    assert "dashboard filter applied" not in (tmp_path / "outline.md").read_text()
