"""Inform-only lints for the two-tier casing system.

Labels print as authored, so a Title Case label is a style slip the author can
fix; a heading's trailing period is not removed for them. Neither fails a render.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.validate.authoring_warnings import (
    detect_authoring_warnings,
)
from dbt_charts.core.diagnostics import Diagnostic

_BOARD = """
title: {board_title}
queries:
  q:
    type: values
    rows:
      - {{x: 1, y: 2, label: North America Total Sales}}
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
    title: {chart_title}
    x_label: {x_label}
    y_label: {y_label}
rows:
  - c
"""


def _warnings(
    code: str,
    board_title: str = "Board",
    chart_title: str = "Chart",
    x_label: str = "x",
    y_label: str = "y",
) -> list[Diagnostic]:
    result = compile(
        _BOARD.format(
            board_title=board_title,
            chart_title=chart_title,
            x_label=repr(x_label),
            y_label=repr(y_label),
        )
    )
    assert result.success, result.errors
    return [w for w in result.warnings if w.code == code]


def test_title_case_label_warns_at_its_key() -> None:
    (warning,) = _warnings("WARN-LABEL-TITLE-CASE", x_label="Revenue By Region")
    assert warning.path == "charts.c.x_label"
    assert "Revenue By Region" in warning.message


@pytest.mark.parametrize(
    "label",
    [
        "Revenue by region",
        "Sales in North America",
        "Total Tickets",
        "iOS Users",
        "ARR ($)",
    ],
)
def test_sentence_case_or_single_capital_labels_do_not_warn(label: str) -> None:
    assert _warnings("WARN-LABEL-TITLE-CASE", x_label=label) == []


def test_label_lint_ignores_query_data() -> None:
    assert _warnings("WARN-LABEL-TITLE-CASE") == []


@pytest.mark.parametrize(
    ("kwargs", "path"),
    [
        ({"board_title": "Quarterly revenue."}, "title"),
        ({"chart_title": "Revenue grew."}, "charts.c.title"),
    ],
)
def test_heading_ending_in_a_period_warns(kwargs: dict[str, str], path: str) -> None:
    (warning,) = _warnings("WARN-HEADING-TRAILING-PERIOD", **kwargs)
    assert warning.path == path


def test_heading_without_a_period_does_not_warn() -> None:
    assert _warnings("WARN-HEADING-TRAILING-PERIOD") == []
    assert _warnings("WARN-HEADING-TRAILING-PERIOD", chart_title="Wait for it...") == []


def _compile_warnings(extra: str, code: str) -> list[Diagnostic]:
    result = compile(
        """
queries:
  q:
    type: values
    rows:
      - {x: 1, y: 2}
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
"""
        + extra
        + """
rows:
  - c
"""
    )
    assert result.success, result.errors
    return [w for w in result.warnings if w.code == code]


def test_inline_query_rows_are_data_not_labels() -> None:
    result = compile(
        """
charts:
  c:
    type: bar
    x: x
    y: y
    query:
      type: values
      rows:
        - {x: 1, y: 2, label: North America Total Sales}
rows:
  - c
"""
    )
    assert result.success, result.errors
    assert [w for w in result.warnings if w.code == "WARN-LABEL-TITLE-CASE"] == []


def test_subtitles_are_prose_and_never_checked() -> None:
    result = compile(
        """
title: t
queries:
  q:
    type: values
    rows:
      - {x: 1, y: 2}
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
    subtitle: Avg Order Value By Region
rows:
  - c
"""
    )
    assert result.success, result.errors
    assert [w for w in result.warnings if w.code == "WARN-LABEL-TITLE-CASE"] == []


@pytest.mark.parametrize(
    "yaml_text",
    [
        "charts:\n  c:\n    legend:\n      title: Revenue by region.\n",
        "charts:\n  c:\n    axis_y:\n      title: Revenue by region.\n",
        "charts:\n  c:\n    layers:\n      - axis_y:\n          title: Revenue by region.\n",
    ],
)
def test_axis_and_legend_titles_are_labels_not_headings(yaml_text: str) -> None:
    """Checked on the raw YAML: ``title`` here is a string at any depth."""
    warnings = detect_authoring_warnings(yaml_text, "en", None)
    assert [w for w in warnings if w.code == "WARN-HEADING-TRAILING-PERIOD"] == []


def test_section_title_ending_in_a_period_warns() -> None:
    result = compile(
        """
queries:
  q:
    type: values
    rows:
      - {x: 1, y: 2}
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
rows:
  - title: A section.
    rows:
      - c
"""
    )
    assert result.success, result.errors
    (warning,) = [
        w for w in result.warnings if w.code == "WARN-HEADING-TRAILING-PERIOD"
    ]
    assert warning.path.startswith("rows.0")


def test_a_field_labels_entry_warns_once() -> None:
    warnings = _compile_warnings(
        "    field_labels:\n      label: Avg Order Value\n", "WARN-LABEL-TITLE-CASE"
    )
    assert [w.path for w in warnings] == ["charts.c.field_labels.label"]
