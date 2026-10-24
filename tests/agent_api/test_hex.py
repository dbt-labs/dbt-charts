"""Tests for ``import_hex`` — HTTP is mocked at ``urllib.request.urlopen``."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from dbt_charts.agent_api import hex_project as module
from dbt_charts.agent_api.hex_project import (
    HexProject,
    import_hex,
    list_hex_projects,
)
from dbt_charts.agent_api.source_bundle import BundleError

TOKEN = "tok-abc-123"
PID = "11111111-2222-3333-4444-555555555555"
BASE = "https://hex.test/api/v1"
RUN = "run-1"
PNG = b"\x89PNG-bytes"


def _cell(kind: str, cell_id: str, label: str | None, **config: Any) -> dict[str, Any]:
    return {"cellType": kind, "cellId": cell_id, "cellLabel": label, "config": config}


def _export_yaml(**overrides: Any) -> str:
    cells = [
        _cell(
            "COLLAPSIBLE",
            "sec",
            "Base",
            cells=[
                _cell(
                    "SQL",
                    "sql1",
                    "Revenue",
                    source="select 1 as revenue",
                    resultVariableName="rev",
                ),
                _cell(
                    "SQL", "sql2", None, source="select 2", resultVariableName="rev2"
                ),
            ],
        ),
        _cell(
            "INPUT",
            "in1",
            None,
            inputType="DROPDOWN",
            name="period",
            defaultValue="Monthly",
            options={"valueOptions": ["Monthly", "Yearly"]},
        ),
        _cell(
            "CHARTV2",
            "ch1",
            "Revenue chart",
            chartSpec={
                "type": "layered",
                "layers": [
                    {
                        "xAxis": {"dataFrameColumn": "month"},
                        "series": [
                            {
                                "type": "bar",
                                "dataFrameColumns": ["revenue"],
                                "axis": {"aggregate": "sum"},
                            }
                        ],
                    }
                ],
            },
        ),
        _cell(
            "EXPLORE",
            "ex1",
            "By month",
            dataframe="rev",
            spec={
                "fields": [
                    {"channel": "row", "value": "month", "truncUnit": "month"},
                    {"channel": "value", "value": "revenue", "aggregation": "Sum"},
                ]
            },
        ),
        _cell(
            "METRIC",
            "m1",
            None,
            title="Total",
            valueVariableName="rev",
            valueColumn="revenue",
            valueAggregate="Sum",
            showComparison=False,
            comparisonColumn=None,
        ),
        _cell("CODE", "py1", "Cleanup", source="import pandas as pd\nx = 1\n"),
        _cell("CODE", "py2", None, source="print('unplaced')"),
    ]
    doc = {
        "schemaVersion": 3,
        "meta": {"title": "Sales app"},
        "cells": cells,
        "appLayout": {
            "tabs": [
                {
                    "name": "Overview",
                    "rows": [
                        {
                            "columns": [
                                {
                                    "start": 0,
                                    "end": 60,
                                    "elements": [{"type": "CELL", "cellId": "m1"}],
                                },
                                {
                                    "start": 60,
                                    "end": 120,
                                    "elements": [{"type": "CELL", "cellId": "ch1"}],
                                },
                            ]
                        },
                        {
                            "columns": [
                                {
                                    "start": 0,
                                    "end": 120,
                                    "elements": [
                                        {"type": "CELL", "cellId": "ex1"},
                                        {"type": "CELL", "cellId": "in1"},
                                        {"type": "CELL", "cellId": "py1"},
                                    ],
                                }
                            ]
                        },
                    ],
                }
            ]
        },
        **overrides,
    }
    return yaml.safe_dump(doc, sort_keys=False)


class _Response(io.BytesIO):
    def __init__(self, data: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _json(value: Any) -> Callable[[dict[str, str]], bytes]:
    return lambda query: json.dumps(value).encode()


def _bad_request(message: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"message": message}).encode())
    return urllib.error.HTTPError("u", 400, "Bad", Message(), body)


def _routes() -> dict[tuple[str, str], Callable[[dict[str, str]], bytes]]:
    export = {"filename": "sales.hex.yaml", "content": _export_yaml()}
    run = {
        "runs": [
            {
                "runId": RUN,
                "runUrl": f"https://hex.test/acme/app/{PID}/3/{RUN}",
                "projectVersion": "3",
            }
        ]
    }
    image = {"imageBase64": base64.b64encode(PNG).decode(), "mimeType": "image/png"}

    def not_a_chart(query: dict[str, str]) -> bytes:
        raise _bad_request('Cell "By month" is not a chart visualization.')

    return {
        ("GET", f"/projects/{PID}/runs/{RUN}/cells/ex1/image"): not_a_chart,
        ("POST", "/projects/export"): _json(export),
        ("GET", f"/projects/{PID}/runs"): _json(run),
        ("GET", f"/projects/{PID}/runs/{RUN}/cells/ch1/image"): _json(image),
    }


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> dict[Any, Any]:
    routes: dict[Any, Any] = _routes()
    seen: list[urllib.request.Request] = []
    monkeypatch.setenv("HEX_API_TOKEN", TOKEN)
    monkeypatch.setattr(module, "RETRY_SECONDS", 0)

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        seen.append(request)
        parts = urlparse(request.full_url)
        handler = routes.get((request.get_method(), parts.path.removeprefix("/api/v1")))
        if handler is None:
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", Message(), io.BytesIO()
            )
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        return _Response(handler(query))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    routes["seen"] = seen
    return routes


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_writes_complete_bundle(http: Any, tmp_path: Path) -> None:
    import_hex(PID, out=tmp_path, base_url=BASE, force=False)

    assert _files(tmp_path) == {
        "outline.md",
        "source.json",
        "project.hex.yaml",
        "queries/sql1.sql",
        "queries/sql2.sql",
        "png/ch1.png",
    }
    assert (tmp_path / "queries/sql1.sql").read_text() == "select 1 as revenue"
    assert (tmp_path / "png/ch1.png").read_bytes() == PNG
    assert (tmp_path / "project.hex.yaml").read_text() == _export_yaml()
    source = json.loads((tmp_path / "source.json").read_text())
    assert source["tool"] == "hex"
    assert source["workspace"] == "acme"
    assert source["project_id"] == PID


def test_export_requests_the_published_version(http: Any, tmp_path: Path) -> None:
    import_hex(PID, out=tmp_path, base_url=BASE, force=False)

    export = next(r for r in http["seen"] if r.full_url.endswith("/projects/export"))
    assert json.loads(export.data) == {"projectId": PID, "version": "latest"}
    assert export.get_header("User-agent") == module.USER_AGENT


def test_outline_lists_app_layout_controls_and_cells(http: Any, tmp_path: Path) -> None:
    import_hex(PID, out=tmp_path, base_url=BASE, force=False)
    outline = (tmp_path / "outline.md").read_text()

    assert outline.startswith("# Sales app")
    assert "period (DROPDOWN) default: Monthly; options: Monthly, Yearly" in outline
    assert (
        outline.index("Overview")
        < outline.index("Total")
        < outline.index("Revenue chart")
    )
    assert "cols 0-60" in outline and "cols 60-120" in outline
    assert "x=month" in outline and "bar" in outline and "sum(revenue)" in outline
    assert "queries/sql1.sql" in outline
    assert "png/ch1.png" in outline
    assert "Sum(revenue)" in outline


def test_python_cells_are_flagged_never_dropped(http: Any, tmp_path: Path) -> None:
    import_hex(PID, out=tmp_path, base_url=BASE, force=False)
    outline = (tmp_path / "outline.md").read_text()

    assert outline.count("UNMIGRATED") == 2
    assert "Cleanup" in outline
    assert "not in the app" in outline


def test_explore_without_a_render_is_noted(http: Any, tmp_path: Path) -> None:
    import_hex(PID, out=tmp_path, base_url=BASE, force=False)

    assert "no render" in (tmp_path / "outline.md").read_text()
    assert not (tmp_path / "png/ex1.png").exists()


def test_no_chart_render_means_no_bundle(http: Any, tmp_path: Path) -> None:
    http[("POST", "/projects/export")] = _json(
        {
            "filename": "t.hex.yaml",
            "content": yaml.safe_dump(
                {
                    "meta": {"title": "T"},
                    "cells": [
                        _cell(
                            "SQL", "s", None, source="select 1", resultVariableName="d"
                        )
                    ],
                    "appLayout": {
                        "tabs": [
                            {
                                "name": "A",
                                "rows": [
                                    {
                                        "columns": [
                                            {
                                                "start": 0,
                                                "end": 120,
                                                "elements": [
                                                    {"type": "CELL", "cellId": "s"}
                                                ],
                                            }
                                        ]
                                    }
                                ],
                            }
                        ]
                    },
                }
            ),
        }
    )

    with pytest.raises(BundleError, match=r"png/\*\.png"):
        import_hex(PID, out=tmp_path, base_url=BASE, force=False)


def test_failed_chart_image_names_the_cell(http: Any, tmp_path: Path) -> None:
    del http[("GET", f"/projects/{PID}/runs/{RUN}/cells/ch1/image")]

    with pytest.raises(BundleError, match="ch1"):
        import_hex(PID, out=tmp_path, base_url=BASE, force=False)


def test_unsupported_chart_spec_is_an_error(http: Any, tmp_path: Path) -> None:
    http[("POST", "/projects/export")] = _json(
        {
            "filename": "t.hex.yaml",
            "content": yaml.safe_dump(
                {
                    "meta": {"title": "T"},
                    "cells": [_cell("CHARTV2", "ch1", None, chartSpec={"type": "pie"})],
                    "appLayout": {"tabs": []},
                }
            ),
        }
    )

    with pytest.raises(BundleError, match="pie"):
        import_hex(PID, out=tmp_path, base_url=BASE, force=False)


def test_no_completed_run_is_an_error(http: Any, tmp_path: Path) -> None:
    http[("GET", f"/projects/{PID}/runs")] = _json({"runs": []})

    with pytest.raises(BundleError, match="completed run"):
        import_hex(PID, out=tmp_path, base_url=BASE, force=False)


def test_token_is_sent_but_never_written_or_reported(http: Any, tmp_path: Path) -> None:
    import_hex(PID, out=tmp_path, base_url=BASE, force=False)

    assert http["seen"][0].get_header("Authorization") == f"Bearer {TOKEN}"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes()


def test_http_failure_names_the_endpoint_without_the_token(
    http: Any, tmp_path: Path
) -> None:
    del http[("GET", f"/projects/{PID}/runs")]

    with pytest.raises(BundleError) as exc:
        import_hex(PID, out=tmp_path, base_url=BASE, force=False)

    assert f"/projects/{PID}/runs" in str(exc.value)
    assert "404" in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_missing_token_names_the_variable(
    http: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HEX_API_TOKEN")

    with pytest.raises(BundleError, match="HEX_API_TOKEN"):
        import_hex(PID, out=tmp_path, base_url=BASE, force=False)


def test_rate_limit_is_retried(http: Any, tmp_path: Path) -> None:
    real = http[("GET", f"/projects/{PID}/runs/{RUN}/cells/ch1/image")]
    calls = iter([429])

    def flaky(query: dict[str, str]) -> bytes:
        if next(calls, None):
            raise urllib.error.HTTPError("u", 429, "Slow", Message(), io.BytesIO())
        return real(query)

    http[("GET", f"/projects/{PID}/runs/{RUN}/cells/ch1/image")] = flaky
    import_hex(PID, out=tmp_path, base_url=BASE, force=False)

    assert (tmp_path / "png/ch1.png").read_bytes() == PNG


@pytest.mark.parametrize(
    ("ref", "base_url", "expected"),
    [
        (PID, None, (PID, module.DEFAULT_API_BASE)),
        (PID, BASE, (PID, BASE)),
        (PID, f"{BASE}/", (PID, BASE)),
        (
            f"https://app.hex.tech/acme/hex/{PID}/draft/logic",
            None,
            (PID, "https://app.hex.tech/api/v1"),
        ),
        (
            f"https://eu.hex.tech/acme/app/hex-{PID}",
            None,
            (PID, "https://eu.hex.tech/api/v1"),
        ),
    ],
)
def test_parse_ref(ref: str, base_url: str | None, expected: tuple[str, str]) -> None:
    assert module.parse_ref(ref, base_url=base_url) == expected


def test_parse_ref_rejects_a_non_project_ref() -> None:
    with pytest.raises(BundleError, match="project"):
        module.parse_ref("not-an-id", base_url=None)


def test_list_projects_pages_through_everything(
    http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = {
        None: {
            "values": [{"id": "a", "title": "A", "lastPublishedAt": "2026-01-01"}],
            "pagination": {"after": "c1"},
        },
        "c1": {
            "values": [{"id": "b", "title": "B", "lastPublishedAt": None}],
            "pagination": {"after": None},
        },
    }
    monkeypatch.setattr(module, "PAGE_SIZE", 1)

    def page(query: dict[str, str]) -> bytes:
        return json.dumps(pages[query.get("after")]).encode()

    http[("GET", "/projects")] = page

    projects = list_hex_projects(BASE)

    assert projects == [
        HexProject(id="a", title="A", last_published_at="2026-01-01"),
        HexProject(id="b", title="B", last_published_at=None),
    ]


def test_reused_out_dir_is_refused_without_force_and_replaced_with_it(
    http: Any, tmp_path: Path
) -> None:
    out = tmp_path / "b"
    import_hex(PID, out=out, base_url=BASE, force=False)
    (out / "kpi/stale.csv").write_text("old")

    with pytest.raises(BundleError, match="--force"):
        import_hex(PID, out=out, base_url=BASE, force=False)
    assert (out / "kpi/stale.csv").exists()

    import_hex(PID, out=out, base_url=BASE, force=True)
    assert not (out / "kpi/stale.csv").exists()
    assert (out / "source.json").is_file()


def test_failed_build_leaves_the_existing_bundle_untouched(
    http: Any, tmp_path: Path
) -> None:
    out = tmp_path / "b"
    import_hex(PID, out=out, base_url=BASE, force=False)
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    del http[("GET", f"/projects/{PID}/runs/{RUN}/cells/ch1/image")]

    with pytest.raises(BundleError, match="ch1"):
        import_hex(PID, out=out, base_url=BASE, force=True)

    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    assert [p.name for p in tmp_path.iterdir()] == ["b"]


def test_url_credentials_query_and_fragment_are_not_written(
    http: Any, tmp_path: Path
) -> None:
    ref = f"https://user:pw@hex.test/acme/hex/{PID}?token=sekrit#frag"
    import_hex(ref, out=tmp_path / "b", base_url=BASE, force=False)

    for path in (tmp_path / "b").rglob("*"):
        if path.is_file() and path.name != "project.hex.yaml":
            data = path.read_bytes()
            assert b"sekrit" not in data and b"pw@" not in data
            assert b"frag" not in data
    source = json.loads((tmp_path / "b/source.json").read_text())
    assert source["url"] == f"https://hex.test/acme/hex/{PID}"


def test_unsupported_cell_type_raises_naming_the_cell(
    http: Any, tmp_path: Path
) -> None:
    http[("POST", "/projects/export")] = _json(
        {
            "filename": "t.hex.yaml",
            "content": yaml.safe_dump(
                {
                    "meta": {"title": "T"},
                    "cells": [_cell("MAP", "map1", "Stores")],
                    "appLayout": {"tabs": []},
                }
            ),
        }
    )

    with pytest.raises(BundleError, match=r"map1.*Stores.*MAP"):
        import_hex(PID, out=tmp_path / "b", base_url=BASE, force=False)


def test_list_normalizes_titles_and_names_a_missing_key(http: Any) -> None:
    def page(query: dict[str, str]) -> bytes:
        return json.dumps(
            {
                "values": [
                    {"id": "a", "title": "Two\nlines  here", "lastPublishedAt": None}
                ],
                "pagination": {"after": None},
            }
        ).encode()

    http[("GET", "/projects")] = page
    assert list_hex_projects(BASE)[0].title == "Two lines here"

    def broken(query: dict[str, str]) -> bytes:
        return json.dumps(
            {"values": [{"id": "a", "title": "A"}], "pagination": {"after": None}}
        ).encode()

    http[("GET", "/projects")] = broken
    with pytest.raises(BundleError, match="lastPublishedAt"):
        list_hex_projects(BASE)


def test_pagination_cursor_is_url_quoted(
    http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cursor = "a+b/c="
    pages = {
        None: {
            "values": [{"id": "a", "title": "A", "lastPublishedAt": None}],
            "pagination": {"after": cursor},
        },
        cursor: {"values": [], "pagination": {"after": None}},
    }
    monkeypatch.setattr(module, "PAGE_SIZE", 1)

    def page(query: dict[str, str]) -> bytes:
        return json.dumps(pages[query.get("after")]).encode()

    http[("GET", "/projects")] = page

    assert [p.id for p in list_hex_projects(BASE)] == ["a"]


def test_explore_400_with_another_message_is_an_error(
    http: Any, tmp_path: Path
) -> None:
    def refuse(query: dict[str, str]) -> bytes:
        raise _bad_request("Invalid width")

    http[("GET", f"/projects/{PID}/runs/{RUN}/cells/ex1/image")] = refuse

    with pytest.raises(BundleError, match="ex1.*Invalid width"):
        import_hex(PID, out=tmp_path / "b", base_url=BASE, force=False)


def _export_with(
    cells: list[dict[str, Any]], tabs: list[dict[str, Any]]
) -> Callable[[dict[str, str]], bytes]:
    return _json(
        {
            "filename": "t.hex.yaml",
            "content": yaml.safe_dump(
                {"meta": {"title": "T"}, "cells": cells, "appLayout": {"tabs": tabs}}
            ),
        }
    )


def _tab(*elements: dict[str, Any]) -> dict[str, Any]:
    columns = [{"start": 0, "end": 120, "elements": list(elements)}]
    return {"name": "Main", "rows": [{"columns": columns}]}


def test_non_cell_app_element_raises_naming_the_tab(http: Any, tmp_path: Path) -> None:
    http[("POST", "/projects/export")] = _export_with(
        [], [_tab({"type": "FILTER_BAR", "cellId": "x"})]
    )

    with pytest.raises(BundleError, match="FILTER_BAR.*Main"):
        import_hex(PID, out=tmp_path / "b", base_url=BASE, force=False)


def test_dangling_cell_id_raises_naming_tab_and_cell(http: Any, tmp_path: Path) -> None:
    http[("POST", "/projects/export")] = _export_with(
        [], [_tab({"type": "CELL", "cellId": "ghost"})]
    )

    with pytest.raises(BundleError, match="Main.*ghost"):
        import_hex(PID, out=tmp_path / "b", base_url=BASE, force=False)


def test_metric_without_a_label_or_title_raises_naming_the_cell(
    http: Any, tmp_path: Path
) -> None:
    metric = _cell(
        "METRIC",
        "m9",
        None,
        title=None,
        valueVariableName="d",
        valueColumn="c",
        valueAggregate="Sum",
        showComparison=False,
        comparisonColumn=None,
    )
    http[("POST", "/projects/export")] = _export_with(
        [metric], [_tab({"type": "CELL", "cellId": "m9"})]
    )

    with pytest.raises(BundleError, match="m9"):
        import_hex(PID, out=tmp_path / "b", base_url=BASE, force=False)


def test_project_version_is_recorded(http: Any, tmp_path: Path) -> None:
    import_hex(PID, out=tmp_path / "b", base_url=BASE, force=False)

    assert (
        json.loads((tmp_path / "b/source.json").read_text())["project_version"] == "3"
    )


_VALUE = {
    "valueVariableName": "df",
    "valueColumn": "revenue",
    "valueAggregate": "Sum",
    "comparisonColumn": "prior",
}


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        (
            _cell(
                "PIVOT",
                "c",
                "P",
                dataframeName="df",
                pivotConfig={
                    "values": [
                        {"field": "arr", "aggregation": "SUM"},
                        {"field": "n", "aggregation": "COUNT"},
                    ]
                },
            ),
            ["dataframe: df", "values: SUM(arr), COUNT(n)"],
        ),
        (
            _cell("TABLE_DISPLAY", "c", "T", dataFrameVariableName="grouping"),
            ["dataframe: grouping"],
        ),
        (
            _cell(
                "FILTER",
                "c",
                "F",
                dataframeName="base",
                filters={"filters": [{"column": "region"}, {"column": "segment"}]},
            ),
            ["dataframe: base", "filters: region, segment"],
        ),
        (
            _cell("METRIC", "c", "M", title="M", showComparison=True, **_VALUE),
            ["value: Sum(revenue) of df; comparison prior"],
        ),
        (
            _cell("METRIC", "c", "M", title="M", showComparison=False, **_VALUE),
            ["value: Sum(revenue) of df"],
        ),
        (_cell("MARKDOWN", "c", "N", source="\nFirst line\nsecond"), ["First line"]),
        (_cell("TEXT", "c", "X", richText=[{"type": "p"}]), []),
    ],
)
def test_outline_describes_each_cell_type(
    http: Any, tmp_path: Path, cell: dict[str, Any], expected: list[str]
) -> None:
    chart = _cell(
        "CHARTV2",
        "ch1",
        "Chart",
        chartSpec={
            "type": "layered",
            "layers": [
                {
                    "xAxis": {"dataFrameColumn": "x"},
                    "series": [{"type": "bar", "dataFrameColumns": ["y"], "axis": {}}],
                }
            ],
        },
    )
    http[("POST", "/projects/export")] = _export_with(
        [
            chart,
            cell,
            _cell("SQL", "q", None, source="select 1", resultVariableName="df"),
        ],
        [_tab({"type": "CELL", "cellId": "ch1"}, {"type": "CELL", "cellId": "c"})],
    )

    import_hex(PID, out=tmp_path / "b", base_url=BASE, force=False)
    outline = (tmp_path / "b/outline.md").read_text()

    head = outline.index(f"`{cell['cellId']}`")
    described = outline[head:].splitlines()[1:]
    assert [
        line.removeprefix("    - ") for line in described[: len(expected)]
    ] == expected
