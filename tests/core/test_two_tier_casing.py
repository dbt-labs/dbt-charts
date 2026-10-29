"""Two-tier casing: headings are Title Case, every other label is sentence case.

Rendered end to end through the production paint path.
"""

from __future__ import annotations

import html
import re

import pytest

from dbt_charts.core.text.case import apply_case
from dbt_charts.core.text.humanize import slug_to_text

from ._svg_render import render_board_to_svg


def _texts(svg: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", m)).strip()
        for m in re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)
    ]


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("gdp_per_capita", "GDP per capita"),
        ("ios_users", "iOS users"),
        ("total_kpi_count", "Total KPI count"),
        ("cpu_hrs", "CPU (h)"),
        ("sku_count", "SKU count"),
        ("ebitda_usd", "EBITDA ($)"),
        ("p95_latency_ms", "P95 latency (ms)"),
        ("us_revenue", "Us revenue"),
    ],
)
def test_derived_labels_spell_known_acronyms(slug: str, expected: str) -> None:
    assert slug_to_text(slug) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("weight in lbs", "Weight in Lbs"),
        ("cpu hrs", "Cpu Hrs"),
        ("Mass (kg)", "Mass (kg)"),
        ("Weight (lb)", "Weight (lb)"),
        ("Latency (ms)", "Latency (ms)"),
        ("SpaceX since 2006", "SpaceX Since 2006"),
    ],
)
def test_title_case_does_not_invent_acronyms(text: str, expected: str) -> None:
    assert apply_case(text, "title") == expected


PROBE = """
title: probe board
queries:
  q:
    columns: [gdp_per_capita, ios_users, total_kpi_count, p95_latency_ms, grp]
    values:
      - [5, 10, 3, 120, a]
      - [8, 14, 5, 150, b]
  one:
    columns: [total_kpi_count]
    values:
      - [3]
charts:
  sc:
    type: scatter
    query: q
    x: gdp_per_capita
    y: ios_users
    color: grp
    title: SpaceX since 2006
  auth:
    type: scatter
    query: q
    x: gdp_per_capita
    y: ios_users
    x_label: Revenue By Region
    y_label: net revenue per user
  tbl:
    type: table
    query: q
    style:
      columns:
        grp:
          label: authored lowercase label
        total_kpi_count:
          label: Revenue By Region
  k:
    type: kpi
    query: one
    label: Total KPI count
    value: total_kpi_count
rows:
  - cols: [sc, auth]
  - tbl
  - k
  - tabs:
      items:
        - title: a tab label of the region
          rows: [sc]
        - title: second tab
          rows: [auth]
"""


def test_derived_labels_are_identical_on_every_surface() -> None:
    texts = _texts(render_board_to_svg(PROBE))
    assert texts.count("GDP per capita") >= 2  # both axis titles + table header
    assert texts.count("iOS users") >= 2
    assert "P95 latency (ms)" in texts
    assert "Total KPI count" in texts


def test_authored_labels_render_as_typed() -> None:
    texts = _texts(render_board_to_svg(PROBE))
    assert "authored lowercase label" in texts
    assert "net revenue per user" in texts
    assert texts.count("Revenue By Region") >= 2  # axis title + table header


def test_heading_tier_title_cases_chart_titles() -> None:
    assert "SpaceX Since 2006" in _texts(render_board_to_svg(PROBE))


def test_tab_labels_are_title_cased_and_the_pane_repeats_no_heading() -> None:
    texts = _texts(render_board_to_svg(PROBE))
    assert texts.count("A Tab Label of the Region") == 1  # the tab bar alone
    assert "a tab label of the region" not in texts
    assert "Second Tab" in texts


def test_derived_support_table_label_is_sentence_case() -> None:
    board = """
title: strip
queries:
  q:
    columns: [week, avg_order_revenue]
    values:
      - [a, 10]
      - [b, 20]
charts:
  c:
    type: bar
    query: q
    x: week
    y: avg_order_revenue
    support_table:
      - source: avg_order_revenue
rows:
  - c
"""
    texts = _texts(render_board_to_svg(board))
    assert any(t.startswith("Avg order rev") for t in texts), texts
    assert not any(t.startswith("Avg Order") for t in texts)
