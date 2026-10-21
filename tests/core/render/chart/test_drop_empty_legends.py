"""A spec over no rows drops the legends Vega cannot lay out."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.render.chart.vega_lite import drop_empty_legends


def test_a_top_legend_is_dropped_wherever_the_spec_nests_it() -> None:
    spec: dict[str, Any] = {
        "layer": [
            {"encoding": {"color": {"field": "g", "legend": {"orient": "top-left"}}}},
            {"encoding": {"color": {"field": "g", "legend": {"orient": "top"}}}},
        ],
        "hconcat": [{"encoding": {"color": {"legend": {"orient": "top-right"}}}}],
    }
    drop_empty_legends(spec)
    assert spec["layer"][0]["encoding"]["color"]["legend"] is None
    assert spec["layer"][1]["encoding"]["color"]["legend"] is None
    assert spec["hconcat"][0]["encoding"]["color"]["legend"] is None


def test_a_titled_legend_off_the_top_edge_is_kept() -> None:
    kept = {"orient": "bottom"}
    spec: dict[str, Any] = {
        "encoding": {
            "color": {"field": "g", "legend": kept},
            "shape": {"field": "g", "title": "G", "legend": {"orient": "right"}},
        }
    }
    drop_empty_legends(spec)
    assert spec["encoding"]["color"]["legend"] is kept
    assert spec["encoding"]["shape"]["legend"] == {"orient": "right"}


def test_a_titleless_legend_with_no_entries_is_dropped() -> None:
    spec: dict[str, Any] = {
        "encoding": {
            "color": {"field": "w", "title": None, "legend": {"orient": "right"}}
        }
    }
    drop_empty_legends(spec)
    assert spec["encoding"]["color"]["legend"] is None


def test_a_titleless_legend_over_a_fixed_domain_or_values_is_kept() -> None:
    spec: dict[str, Any] = {
        "encoding": {
            "color": {
                "field": "w",
                "title": None,
                "scale": {"domain": ["a", "b"]},
                "legend": {"orient": "right"},
            },
            "shape": {
                "field": "w",
                "title": None,
                "legend": {"orient": "right", "values": ["a"]},
            },
        }
    }
    drop_empty_legends(spec)
    assert spec["encoding"]["color"]["legend"] == {"orient": "right"}
    assert spec["encoding"]["shape"]["legend"] == {"orient": "right", "values": ["a"]}
