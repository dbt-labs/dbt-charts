"""Render every shipped data/inspector view against a profiled source.

The listing templates project their schema queries with ``fields:`` because a
profiled source returns the full super-schema contract per row — dozens of
metadata keys that must stay out of the browsable listings. A bare live
source exercises only the minimal key set, which is how two shipped views
once broke without any test noticing. This suite bakes a metadata-rich
``target/super_schema.json`` and pins that each view renders AND stays
curated.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.registered_views.render_pipeline import (
    RenderSuccess,
    render_registered_view,
)

pytest.importorskip("dbt_charts_super_schema")

_RICH_COLUMN = {
    "name": "id",
    "order": 1,
    "database_type": "BIGINT",
    "role": "dimension",
    "semantic_type": "identifier",
    "semantic_type_confidence": 0.9,
    "distribution": "unique",
    "completeness": "complete",
    "key_role": "primary",
    "distinct_count": 3,
    "uniqueness_ratio": 1.0,
    "null_percentage": 0.0,
    "min_value": 1,
    "max_value": 3,
    "mean_value": 2.0,
    "median_value": 2.0,
    "stddev_value": 1.0,
    "min_length": None,
    "max_length": None,
    "avg_length": None,
    "top_values": [{"value": "1", "count": 1}],
    "enum_values": None,
    "histogram_bins": None,
    "date_distribution": None,
    "approximate": False,
    "declared_unique": True,
    "is_non_negative": True,
    "is_normalized": False,
    "is_zero_inflated": False,
    "has_outliers": False,
    "is_skewed": False,
    "is_incremental": True,
    "is_sequential": True,
    "is_fixed_length": False,
    "is_free_text": False,
    "is_ordinal": False,
    "is_currency": False,
    "is_geo": False,
    "is_pii": False,
}

_RICH_TABLE = {
    "contract_version": "1.2",
    "table_name": "orders",
    "schema_name": "main",
    "database_name": None,
    "kind": "table",
    "profiled_at": "2026-08-25T00:00:00+00:00",
    "inspector_version": "2.0.0",
    "row_count": 3,
    "column_count": 2,
    "primary_date_column": None,
    "empty_columns": [],
    "high_null_columns": [],
    "stats": {"query_count": 1},
    "stats_approximate": False,
    "sample_percent": None,
    "grain": {
        "columns": ["id"],
        "label": "one row per id",
        "confidence": 1.0,
        "source": "primary_key",
        "is_composite": False,
    },
    "relationships": [],
    "columns": [
        _RICH_COLUMN,
        {**_RICH_COLUMN, "name": "status", "order": 2, "database_type": "VARCHAR"},
    ],
}


@pytest.fixture
def profiled_project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    db_path = tmp_path / "wh.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE orders (id INTEGER, status VARCHAR)")
    conn.execute("INSERT INTO orders VALUES (1, 'open'), (2, 'won'), (3, 'lost')")
    conn.close()
    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  wh:\n    type: duckdb\n    path: '{db_path}'\n"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "super_schema.json").write_text(
        json.dumps(
            {
                "version": "1.2",
                "generated_at": "2026-08-25T00:00:00+00:00",
                "tables": {"main.orders": _RICH_TABLE},
            }
        )
    )
    return local_project(tmp_path)


def _render(project: FilesystemProject, path: str) -> str:
    from dbt_charts.core.execute.env import build_exec_env

    registry = build_exec_env(
        project,
        read_only=False,
        allow_external_access_in_readonly=False,
        duckdb_config=None,
        profile_type="duckdb",
        target="dev",
    )
    result = render_registered_view(
        request_path=path,
        project=project,
        env=registry,
        result_cache=None,
    )
    assert result is not None, path
    assert isinstance(result, RenderSuccess), f"{path}: {result}"
    return result.output


@pytest.mark.parametrize(
    ("path", "must_show", "must_not_show"),
    [
        ("/data/", ["wh"], ["In Memory", "Path"]),
        ("/data/wh/", ["main"], ["Table Count"]),
        ("/data/wh/main/", ["orders"], ["Row Count", "Size Bytes", "Grain"]),
        ("/data/wh/main/orders/", ["id", "status"], []),
        ("/inspector/wh/", ["main"], ["Table Count"]),
        ("/inspector/wh/main/", ["orders"], ["Row Count", "Grain"]),
        ("/inspector/wh/main/orders/", ["status"], []),
        # profile_detail sits in a collapsed details: block, so its body is
        # not in the initial HTML — its curation is pinned separately below.
        ("/inspector/wh/main/orders/id/", ["Distribution"], ["is_pii"]),
    ],
)
def test_shipped_view_renders_curated(
    profiled_project: FilesystemProject,
    path: str,
    must_show: list[str],
    must_not_show: list[str],
) -> None:
    # _render already asserts RenderSuccess — a transition error would have
    # surfaced there as a RenderError, so no marker scan is needed here.
    html = _render(profiled_project, path)
    for text in must_show:
        assert text in html, f"{path}: expected {text!r} in the render"
    for text in must_not_show:
        assert text not in html, (
            f"{path}: profile metadata {text!r} leaked into the listing"
        )


from ..._paths import DBT_CHARTS_PKG_DIR

_INSPECT_TEMPLATE_DIR = DBT_CHARTS_PKG_DIR / "core" / "inspect" / "templates"


@pytest.mark.parametrize(
    "template_name",
    sorted(p.name for p in _INSPECT_TEMPLATE_DIR.glob("*.yml")),
)
@pytest.mark.parametrize("column", ["status", "id"])
def test_shipped_inspect_template_renders_curated(
    profiled_project: FilesystemProject, template_name: str, column: str
) -> None:
    """`dct inspect` templates render against the profiled source with no
    transition panel — this surface embeds chart errors in the HTML, so the
    marker scan is load-bearing here (unlike registered views, which return
    RenderError)."""
    from dbt_charts.core.execute.env import build_exec_env
    from dbt_charts.core.inspect.renderer import render_inspect_dashboard

    registry = build_exec_env(
        profiled_project,
        read_only=False,
        allow_external_access_in_readonly=False,
        duckdb_config=None,
        profile_type="duckdb",
        target="dev",
    )
    template_yaml = (_INSPECT_TEMPLATE_DIR / template_name).read_text()
    html = render_inspect_dashboard(
        template_yaml,
        {"model": "orders", "column": column},
        project=profiled_project,
        env=registry,
    )
    assert html, template_name
    lowered = html.lower()
    assert "table-columns-transition" not in lowered, template_name
    assert "will now render" not in lowered, template_name
    assert "err-internal" not in lowered, template_name


@pytest.mark.parametrize(
    ("query_name", "chart_name"),
    [
        ("column_overview", "column_inventory"),
        ("column_classification_profile", "column_classification"),
        ("column_stats_profile", "column_stats"),
    ],
)
def test_model_template_queries_project_their_panel_columns(
    query_name: str, chart_name: str
) -> None:
    """model.yml's classification and stats panels sit in collapsed details:
    blocks, so a broken projection is byte-invisible to the HTML smoke test —
    pin the template shape: each schema query projects exactly the keys its
    panel styles."""
    import yaml

    d = yaml.safe_load((_INSPECT_TEMPLATE_DIR / "model.yml").read_text())
    query = d["queries"][query_name]
    chart = d["charts"][chart_name]
    assert list(query["fields"]) == list(chart["style"]["columns"])


def test_column_view_profile_query_projects_the_panel_columns() -> None:
    """The Full-profile table lives in a collapsed details: block, so a broken
    projection there is invisible to the HTML smoke test above — pin the
    template shape instead: the `profile` schema query projects exactly the
    keys its table styles."""
    from dbt_charts.core.registered_views.expander import expand_registered_view
    from dbt_charts.core.registered_views.loader import load_builtin_registry
    from dbt_charts.core.registered_views.router import RouteRouter

    match = RouteRouter(load_builtin_registry()).match("/inspector/wh/main/orders/id/")
    assert match is not None
    board = expand_registered_view(match, query_results={})
    assert board.queries is not None and board.charts is not None
    profile = board.queries["profile"]
    detail = board.charts["profile_detail"]
    assert detail.style is not None and detail.style.columns is not None
    assert list(profile.fields or []) == list(detail.style.columns)
