"""Render VL-JSON regression suite — full spec equality against a frozen golden.

For each MVP family, the render path (resolve → emit → features →
translate_to_vl) must produce VL JSON byte-equal to a golden file frozen
from a prior, reviewed render of the same fixture.

Comparison level: FULL spec — result == golden with NO stripping. The gate
includes config, background, mark visual-props, axis/tooltip, and every layer.

Fixture loading: fixture YAML → normalize_chart → the render pipeline, so
this proves end-to-end YAML-sourced coverage against the real normalizer.

All families are plain params that must go green; no permanent xfails are permitted.

To regenerate goldens after an intentional, reviewed render output change, run:
DCT_WRITE_PARITY_GOLDENS=1 uv run pytest dbt-charts/tests/core/render/chart/test_render_parity.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.core.render.chart.spec import RenderBox

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "parity"
_GOLDEN_DIR = _FIXTURE_DIR / "goldens"
_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _board_style() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    return resolve_style_and_context(get_theme_style(get_default_theme_name()))


# ---------------------------------------------------------------------------
# render path
# ---------------------------------------------------------------------------


def _v2_vl_from_dict(
    chart_id: str,
    chart_def: dict[str, Any],
    query_registry: dict[str, Any],
    data: list[dict[str, Any]],
    board_style: Any,
) -> dict[str, Any]:
    """render path: authored dict → normalize_chart → CompiledChart → finalize_vl."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.session import BoardRenderSession

    board_rs, board_ctx = board_style
    compiled = normalize_chart(chart_id, chart_def, query_registry, sources={})
    resolved = resolve(compiled, data, chart_style_context=board_ctx)
    session = BoardRenderSession.create(board_rs)
    spec = session.emit_chart(resolved, _DEFAULT_BOX, {resolved.query_name: data})
    return session.finalize_vl(spec)


# ---------------------------------------------------------------------------
# Tier B fixture loader
# ---------------------------------------------------------------------------


def _load_fixture(
    path: Path,
) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Load a parity fixture YAML.

    Returns (chart_id, chart_def, query_registry, data).
    """
    from dbt_charts.core.compile.models.query.normalized import ValuesQuery

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    chart_id: str = raw["chart_id"]
    chart_def: dict[str, Any] = raw["chart"]

    query_registry: dict[str, Any] = {}
    for qname, qdef in (raw.get("queries") or {}).items():
        query_registry[qname] = ValuesQuery(
            columns=qdef.get("columns"),
            values=qdef.get("values"),
            rows=qdef.get("rows") or [],
        )

    # Data: rows from the query referenced by the chart (first query if unspecified).
    query_name: str = chart_def.get("query") or next(iter(query_registry))
    data: list[dict[str, Any]] = query_registry[query_name].rows
    return chart_id, chart_def, query_registry, data


# ---------------------------------------------------------------------------
# Test parameters
# ---------------------------------------------------------------------------

_VL_FAMILY_IDS = [
    "bar",
    "histogram",
    "line",
    "area",
    "scatter",
    "heatmap",
    "layered",
    "point_map",
    "bubble_map",
    "geoshape",
]


# ---------------------------------------------------------------------------
# Full YAML normalize path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Golden regression gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", _VL_FAMILY_IDS)
def test_v2_matches_golden(family: str) -> None:
    """Regression gate: current render output must match the frozen golden."""
    fixture_path = _FIXTURE_DIR / f"{family}.yml"
    chart_id, chart_def, query_registry, data = _load_fixture(fixture_path)
    board_style = _board_style()
    result = _v2_vl_from_dict(chart_id, chart_def, query_registry, data, board_style)

    golden = json.loads((_GOLDEN_DIR / f"{family}.json").read_text())
    assert result == golden, (
        f"V2 output drifted from golden for family={family!r}\n"
        f"GOLDEN={json.dumps(golden, indent=2, default=str)}\n"
        f"V2    ={json.dumps(result, indent=2, default=str)}"
    )


@pytest.mark.parametrize("family", ["kpi", "table"])
def test_non_vl_family_not_in_emitter_registry(family: str) -> None:
    """Non-VL families (kpi, table) must not have an emitter; get_emitter raises."""
    from dbt_charts.core.render.chart.emitters import get_emitter

    fixture_path = _FIXTURE_DIR / f"{family}.yml"
    chart_id, chart_def, query_registry, data = _load_fixture(fixture_path)
    _, board_ctx = _board_style()

    compiled = _v2_compiled_from_dict(chart_id, chart_def, query_registry)
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.errors import RenderError

    resolved = resolve(compiled, data, chart_style_context=board_ctx)
    with pytest.raises(RenderError, match=type(resolved).__name__):
        get_emitter(resolved)


def _v2_compiled_from_dict(
    chart_id: str,
    chart_def: dict[str, Any],
    query_registry: dict[str, Any],
) -> Any:
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    return normalize_chart(chart_id, chart_def, query_registry, sources={})


# ---------------------------------------------------------------------------
# Golden regeneration path — not run in CI
# ---------------------------------------------------------------------------


def _write_goldens() -> None:
    """Regenerate the frozen VL-JSON goldens from current render output.

    Regeneration path only — never runs in CI. Use after an intentional,
    reviewed change to rendering output:

        DCT_WRITE_PARITY_GOLDENS=1 uv run pytest dbt-charts/tests/core/render/chart/test_render_parity.py -q
    """
    board_style = _board_style()
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for family in _VL_FAMILY_IDS:
        fixture_path = _FIXTURE_DIR / f"{family}.yml"
        chart_id, chart_def, query_registry, data = _load_fixture(fixture_path)
        vl = _v2_vl_from_dict(chart_id, chart_def, query_registry, data, board_style)
        golden_path = _GOLDEN_DIR / f"{family}.json"
        golden_path.write_text(json.dumps(vl, indent=2, sort_keys=True) + "\n")


if os.environ.get("DCT_WRITE_PARITY_GOLDENS") == "1":
    _write_goldens()
