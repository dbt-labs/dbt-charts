"""Tests for ``import_looker`` — HTTP is mocked at ``urllib.request.urlopen``."""

from __future__ import annotations

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

from dbt_charts.agent_api import looker as module
from dbt_charts.agent_api.looker import import_looker
from dbt_charts.agent_api.source_bundle import BundleError

from .._outline import missing_options, tile_block

FIXTURES = Path(__file__).parent / "looker_fixtures"
SECRET = "sekrit-client-secret"
TOKEN = "tok-abc-123"
BASE = "https://acme.looker.test:19999"


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _routes() -> dict[tuple[str, str], Callable[[], bytes]]:
    polls = iter([b'{"status": "render"}'])
    routes: dict[tuple[str, str], Callable[[], bytes]] = {
        ("POST", "/login"): lambda: json.dumps({"access_token": TOKEN}).encode(),
        ("GET", "/dashboards/3539"): lambda: _fixture("dashboard.json"),
        ("GET", "/merge_queries/m7"): lambda: _fixture("merge_query_m7.json"),
        ("POST", "/render_tasks/dashboards/3539/png"): lambda: b'{"id": "rt1"}',
        ("GET", "/render_tasks/rt1"): lambda: next(polls, b'{"status": "success"}'),
        ("GET", "/render_tasks/rt1/results"): lambda: b"\x89PNG-bytes",
        ("GET", "/queries/102/run/json"): lambda: _fixture("query_102_values.json"),
    }
    for qid in ("101", "102", "103", "104"):
        routes[("GET", f"/queries/{qid}")] = lambda qid=qid: _fixture(
            f"query_{qid}.json"
        )
        routes[("GET", f"/queries/{qid}/run/sql")] = lambda qid=qid: _fixture(
            f"query_{qid}.sql"
        )
    return routes


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], Callable[[], bytes]]:
    routes = _routes()
    monkeypatch.setattr(module, "POLL_SECONDS", 0)
    monkeypatch.setenv("LOOKER_CLIENT_ID", "client-id")
    monkeypatch.setenv("LOOKER_CLIENT_SECRET", SECRET)

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        path = urlparse(request.full_url).path.removeprefix("/api/4.0")
        handler = routes.get((request.get_method(), path))
        if handler is None:
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                f"Not Found token {TOKEN}",
                Message(),
                io.BytesIO(),
            )
        return _Response(handler())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return routes


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_writes_complete_bundle(http: Any, tmp_path: Path) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)

    assert _files(tmp_path / "b") == {
        "outline.md",
        "source.json",
        "dashboard.json",
        "png/dashboard.png",
        "kpi/total-revenue-11.csv",
        "queries/101.sql",
        "queries/101.totals.sql",
        "queries/101.json",
        "queries/102.sql",
        "queries/102.json",
        "queries/103.sql",
        "queries/103.json",
        "queries/104.sql",
        "queries/104.json",
        "queries/merge-m7.json",
    }
    assert (tmp_path / "b" / "kpi/total-revenue-11.csv").read_text().splitlines() == [
        "orders.revenue",
        "12345.5",
    ]
    source = json.loads((tmp_path / "b" / "source.json").read_text())
    assert source["query_timezones"] == ["America/Los_Angeles"]
    assert source["tool"] == "looker"


def test_outline_lists_tiles_in_reading_order_with_details(
    http: Any, tmp_path: Path
) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)
    outline = (tmp_path / "b" / "outline.md").read_text()

    order = [
        outline.index(title)
        for title in ("Revenue by month", "Total revenue", "Joined results", "Notes")
    ]
    assert order == sorted(order)
    assert "pivots: orders.region" in outline
    assert "growth" in outline and "offset(" in outline
    assert "listens: date -> orders.created_date" in outline
    assert "orders.status = complete" in outline
    assert "date (date_filter) default: 30 days" in outline
    assert "region (field_filter) default: EMEA" in outline
    assert "row 0, col 12" in outline


def test_merged_results_tile_appears_once_with_its_sources(
    http: Any, tmp_path: Path
) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)
    outline = (tmp_path / "b" / "outline.md").read_text()

    assert outline.count("Joined results") == 1
    assert "merged results m7" in outline
    assert "queries/103.sql" in outline and "queries/104.sql" in outline


def test_no_credential_or_token_is_written(http: Any, tmp_path: Path) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)

    for path in (tmp_path / "b").rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert SECRET.encode() not in data
            assert TOKEN.encode() not in data


def test_failed_endpoint_exits_naming_it_without_the_token(
    http: Any, tmp_path: Path
) -> None:
    del http[("GET", "/queries/102/run/json")]

    with pytest.raises(BundleError) as exc:
        import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)

    assert "/queries/102/run/json" in str(exc.value)
    assert "404" in str(exc.value)
    assert TOKEN not in "".join(traceback.format_exception(exc.value))


def test_failed_render_names_the_render(http: Any, tmp_path: Path) -> None:
    http[("GET", "/render_tasks/rt1")] = lambda: b'{"status": "failure"}'

    with pytest.raises(BundleError, match="render"):
        import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)


def test_missing_credentials_name_the_variable(
    http: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LOOKER_CLIENT_SECRET")

    with pytest.raises(BundleError, match="LOOKER_CLIENT_SECRET"):
        import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)


def test_bare_id_without_base_url_is_an_error(http: Any, tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="LOOKER_BASE_URL"):
        import_looker("3539", out=tmp_path / "b", base_url=None, force=False)


@pytest.mark.parametrize(
    ("ref", "base_url", "expected"),
    [
        ("3539", BASE, ("3539", f"{BASE}/api/4.0")),
        (
            "https://acme.looker.com/dashboards/3539?Date=7+days",
            None,
            ("3539", "https://acme.looker.com/api/4.0"),
        ),
        (
            "https://acme.looker.com/dashboards-next/sales::overview",
            BASE,
            ("sales::overview", f"{BASE}/api/4.0"),
        ),
        ("3539", f"{BASE}/api/4.0/", ("3539", f"{BASE}/api/4.0")),
    ],
)
def test_parse_ref(ref: str, base_url: str | None, expected: tuple[str, str]) -> None:
    assert module.parse_ref(ref, base_url=base_url) == expected


def test_parse_ref_rejects_a_url_without_a_dashboard() -> None:
    with pytest.raises(BundleError, match="dashboard"):
        module.parse_ref("https://acme.looker.com/looks/5", base_url=None)


def _replace_fixture(http: Any, route: tuple[str, str], name: str, change: Any) -> None:
    data = json.loads(_fixture(name))
    http[route] = lambda: json.dumps(change(data) or data).encode()


def test_looker_error_rows_raise_naming_the_tile(http: Any, tmp_path: Path) -> None:
    http[("GET", "/queries/102/run/json")] = lambda: b'[{"looker_error": "bad sql"}]'

    with pytest.raises(BundleError, match="Total revenue.*bad sql"):
        import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)


def test_look_backed_tile_raises_naming_the_tile(http: Any, tmp_path: Path) -> None:
    def make_look(dashboard: dict[str, Any]) -> None:
        dashboard["dashboard_elements"][0].update(query_id=None, look_id="9")

    _replace_fixture(http, ("GET", "/dashboards/3539"), "dashboard.json", make_look)

    with pytest.raises(BundleError, match="Revenue by month.*Look 9"):
        import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)


def test_requires_an_active_layout(http: Any, tmp_path: Path) -> None:
    def deactivate(dashboard: dict[str, Any]) -> None:
        for layout in dashboard["dashboard_layouts"]:
            layout["active"] = False

    _replace_fixture(http, ("GET", "/dashboards/3539"), "dashboard.json", deactivate)

    with pytest.raises(BundleError, match="active layout"):
        import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)


def test_merged_single_value_tile_raises_naming_the_tile(
    http: Any, tmp_path: Path
) -> None:
    def single_value(merge: dict[str, Any]) -> None:
        merge["vis_config"] = {"type": "single_value"}

    _replace_fixture(
        http, ("GET", "/merge_queries/m7"), "merge_query_m7.json", single_value
    )

    with pytest.raises(BundleError, match="Joined results.*merged"):
        import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)


def test_pivoted_kpi_values_are_flattened_to_columns(http: Any, tmp_path: Path) -> None:
    http[("GET", "/queries/102/run/json")] = lambda: (
        b'[{"orders.revenue": {"EMEA": 1, "APAC": 2}}]'
    )

    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)

    assert (tmp_path / "b/kpi/total-revenue-11.csv").read_text().splitlines() == [
        "orders.revenue|EMEA,orders.revenue|APAC",
        "1,2",
    ]


def test_dynamic_fields_are_labeled_by_kind(http: Any, tmp_path: Path) -> None:
    def add_measure(query: dict[str, Any]) -> None:
        fields = json.loads(query["dynamic_fields"])
        fields.append({"measure": "total", "based_on": "orders.revenue"})
        query["dynamic_fields"] = json.dumps(fields)

    _replace_fixture(http, ("GET", "/queries/101"), "query_101.json", add_measure)

    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)

    outline = (tmp_path / "b/outline.md").read_text()
    assert "custom measure: total = orders.revenue" in outline
    assert "None" not in outline


def test_url_is_stripped_of_userinfo_and_query_before_writing(
    http: Any, tmp_path: Path
) -> None:
    ref = "https://user:pw@acme.looker.test:19999/dashboards/3539?embed_token=zzz#x"

    import_looker(ref, out=tmp_path / "b", base_url=None, force=False)

    for path in (tmp_path / "b").rglob("*"):
        if path.is_file() and path.name != "dashboard.json":
            data = path.read_text(errors="ignore")
            assert "embed_token" not in data and "user:" not in data
    source = json.loads((tmp_path / "b/source.json").read_text())
    assert source["url"] == "https://acme.looker.test:19999/dashboards/3539"


def test_reused_out_dir_is_refused_without_force_and_replaced_with_it(
    http: Any, tmp_path: Path
) -> None:
    out = tmp_path / "b"
    import_looker("3539", out=out, base_url=BASE, force=False)
    (out / "kpi/stale-9.csv").write_text("old")

    with pytest.raises(BundleError, match="--force"):
        import_looker("3539", out=out, base_url=BASE, force=False)
    assert (out / "kpi/stale-9.csv").exists()

    import_looker("3539", out=out, base_url=BASE, force=True)
    assert not (out / "kpi/stale-9.csv").exists()
    assert (out / "source.json").is_file()


def test_pivot_totals_statements_are_split_from_the_main_query(
    http: Any, tmp_path: Path
) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)
    queries = tmp_path / "b" / "queries"

    assert (queries / "101.sql").read_text() == "SELECT 1 AS m FROM orders\nLIMIT 500\n"
    totals = (queries / "101.totals.sql").read_text()
    assert totals.startswith("-- sql for creating the total and/or determining pivot")
    assert "SELECT 3 AS g" in totals
    assert not (queries / "102.totals.sql").exists()
    outline = (tmp_path / "b" / "outline.md").read_text()
    assert "queries/101.sql" in outline and "queries/101.totals.sql" in outline


def test_source_json_marks_the_outline_version(http: Any, tmp_path: Path) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)

    source = json.loads((tmp_path / "b" / "source.json").read_text())
    assert source["outline_version"] == 2


def test_every_tile_names_the_file_holding_its_full_config(
    http: Any, tmp_path: Path
) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)
    outline = (tmp_path / "b" / "outline.md").read_text()

    assert "config: queries/101.json" in tile_block(outline, "10")
    assert "config: queries/102.json" in tile_block(outline, "11")


def test_outline_carries_reader_visible_options_without_hex_colors(
    http: Any, tmp_path: Path
) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)
    block = tile_block((tmp_path / "b" / "outline.md").read_text(), "10")

    assert "row_total: right" in block and "total: true" in block
    assert "stacking: normal" in block
    assert "growth" in block and "0.0%" in block
    assert "Average order" in block
    assert '"type":"greater than","value":1000' in block
    assert '"bold":true' in block
    assert '"is_active":true' in block
    assert "#FF9D99" not in block and "#bfdfff" not in block
    assert "palette" not in block


def test_every_raw_option_is_in_the_outline_or_the_styling_set(
    http: Any, tmp_path: Path
) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)
    block = tile_block((tmp_path / "b" / "outline.md").read_text(), "10")
    query = json.loads(_fixture("query_101.json"))

    assert missing_options(query, block, lambda k: k in module.NON_OPTION_KEYS) == []
    assert missing_options(query["vis_config"], block, module.is_styling_key) == []


def test_unlisted_vis_option_still_reaches_the_outline(
    http: Any, tmp_path: Path
) -> None:
    query = json.loads(_fixture("query_101.json"))
    query["vis_config"]["brand_new_vendor_option"] = "on"
    http[("GET", "/queries/101")] = lambda: json.dumps(query).encode()
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)

    block = tile_block((tmp_path / "b" / "outline.md").read_text(), "10")
    assert "brand_new_vendor_option: on" in block


def test_every_raw_element_and_merge_option_is_in_the_outline_or_dropped(
    http: Any, tmp_path: Path
) -> None:
    import_looker("3539", out=tmp_path / "b", base_url=BASE, force=False)
    outline = (tmp_path / "b" / "outline.md").read_text()
    dashboard = json.loads(_fixture("dashboard.json"))

    for element in dashboard["dashboard_elements"]:
        block = tile_block(outline, element["id"])
        assert (
            missing_options(element, block, module.ELEMENT_NON_OPTION_KEYS.__contains__)
            == []
        ), element["id"]
    merge = json.loads(_fixture("merge_query_m7.json"))
    block = tile_block(outline, "12")
    assert missing_options(merge, block, module.NON_OPTION_KEYS.__contains__) == []
    assert "merge_fields=" in block
