"""Compile-time authoring-style warnings for verbose YAML."""

from __future__ import annotations

import re
from typing import Any, TypeAlias

import yaml

from dbt_charts import _engine
from dbt_charts.core.compile.models.primitives import LanguageCode
from dbt_charts.core.compile.restated import restated_diagnostic
from dbt_charts.core.diagnostics import (
    WARN_FLAT_COLS_UNSIZED_OVERFLOW,
    WARN_HEADING_TRAILING_PERIOD,
    WARN_LABEL_TITLE_CASE,
    WARN_RESTATED_INFERRED,
    Diagnostic,
)
from dbt_charts.core.text.format_d3 import language_scope
from dbt_charts.core.utils import YAML_LOADER

# Beyond this many unsized *growable* cells, a flat `cols:` row loses layout
# predictability: each cell inherits the row's full vertical budget, so a wide flat
# row makes height unpredictable (compounds with any child chart that pulls the row
# taller). KPI cards are height-stable — a row of them is a deliberate scorecard, not
# this anti-pattern — so they are carved out of the count (see _is_kpi_cell).
_MAX_UNSIZED_FLAT_COLS = 4

# Keys whose string value is label-tier text (sentence case, printed as typed).
_LABEL_KEYS = frozenset({"label", "x_label", "y_label"})

# Query data: a values row's ``label`` column is a value, not a label.
_DATA_KEYS = frozenset({"queries", "query"})

# Lists whose mapping items are layout sections or tabs.
_LAYOUT_LISTS = frozenset({"rows", "cols", "items"})

# A plain Title Case word: one capital then lowercase only (`Region`, not `iOS`,
# `SpaceX`, `ARR` or `Q3`).
_TITLE_WORD = re.compile(r"[A-Z][a-z]+")

# Precise recursive type for one `yaml.safe_load()` node — every shape PyYAML can
# produce, with no boundary `Any`. Public (no leading underscore): reused by
# compiler.py's cross-file reference resolution, which also walks raw parsed
# YAML. The rest of this module's raw-YAML walk predates this and stays
# untyped `Any`.
YamlScalar: TypeAlias = str | int | float | bool | None
YamlValue: TypeAlias = "YamlScalar | list[YamlValue] | dict[str, YamlValue]"


def detect_authoring_warnings(
    yaml_content: str, language: LanguageCode, inherited_source: str | None
) -> list[Diagnostic]:
    """Return non-fatal warnings for redundant authored YAML.

    ``language`` is the merged board's (meta.yml cascade included): the label a
    variable would print is humanized in it, and the redundancy check reads it
    there. ``inherited_source`` is the board's ``source:`` after the meta
    cascade: the default a nested ``source:`` or a query's must differ from.
    """
    raw = yaml.load(yaml_content, Loader=YAML_LOADER)
    if not isinstance(raw, dict):
        return []

    chart_types: dict[str, str] = {}
    _collect_chart_types(raw, chart_types)
    warnings: list[Diagnostic] = []
    with language_scope(language):
        _walk_board_like_mapping(raw, chart_types, [], warnings, inherited_source)
    _walk_casing(raw, [], warnings)
    return warnings


def _is_title_case_label(text: str) -> bool:
    """Title-case-shaped: a headline, not prose with proper nouns.

    The second word is a plain capitalized word, every non-initial word that is
    not a small word (``small_words``/``extra_small_words`` in the English
    catalog) is one too, and at least two non-initial words are. ``Avg Order
    Value`` and ``Revenue By Region`` fire; ``Sales in North America`` (lowercase
    second word) and ``Griddle Royale launched in March`` (lowercase content
    word) do not.
    """
    words = [word.strip("()[]{}\"'.,:;!?") for word in text.split()[1:]]
    if not words or not _TITLE_WORD.fullmatch(words[0]):
        return False
    small = _small_words()
    content = [word for word in words if word.lower() not in small]
    return all(_TITLE_WORD.fullmatch(word) for word in content) and (
        sum(1 for word in words if _TITLE_WORD.fullmatch(word)) >= 2
    )


def _small_words() -> frozenset[str]:
    return frozenset(
        f"{_engine.message('en', 'case.small_words')},"
        f"{_engine.message('en', 'case.extra_small_words')}".split(",")
    )


def _is_heading_title(path: list[str]) -> bool:
    """Whether a ``title`` key at mapping *path* is a heading.

    Headings are the board's own title, a chart's, and a layout item's (section
    or tab). Any other ``title`` is a label (axis, legend) or a style block.
    """
    return (
        not path
        or path[-2:-1] == ["charts"]
        or (len(path) > 1 and path[-1].isdigit() and path[-2] in _LAYOUT_LISTS)
    )


def _walk_casing(node: YamlValue, path: list[str], warnings: list[Diagnostic]) -> None:
    """Flag Title Case label-tier text and headings that end with a period.

    Query data is skipped: a row's ``label`` column is a value, not a label.
    """
    if isinstance(node, list):
        for index, item in enumerate(node):
            _walk_casing(item, [*path, str(index)], warnings)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key in _DATA_KEYS:
            continue
        child_path = [*path, str(key)]
        dotted = ".".join(child_path)
        if key == "field_labels" and isinstance(value, dict):
            labeled = {f"{dotted}.{name}": text for name, text in value.items()}
        elif key in _LABEL_KEYS and isinstance(value, str):
            labeled = {dotted: value}
        else:
            labeled = {}
        for label_path, text in labeled.items():
            if isinstance(text, str) and _is_title_case_label(text):
                warnings.append(
                    Diagnostic.from_code(
                        WARN_LABEL_TITLE_CASE,
                        path=label_path,
                        message=WARN_LABEL_TITLE_CASE.message_template.format(
                            text=text
                        ),
                        fix=WARN_LABEL_TITLE_CASE.fix_template,
                        fields={"text": text},
                    )
                )
        if key == "field_labels":
            continue
        if (
            key == "title"
            and isinstance(value, str)
            and _is_heading_title(path)
            and value.endswith(".")
            and not value.endswith("..")
        ):
            warnings.append(
                Diagnostic.from_code(
                    WARN_HEADING_TRAILING_PERIOD,
                    path=dotted,
                    message=WARN_HEADING_TRAILING_PERIOD.message_template.format(
                        text=value
                    ),
                    fix=WARN_HEADING_TRAILING_PERIOD.fix_template,
                    fields={"text": value},
                )
            )
        _walk_casing(value, child_path, warnings)


def _collect_chart_types(node: YamlValue, out: dict[str, str]) -> None:
    """Map every named chart in the document to its `type:`.

    A bare-string `cols:` cell references a chart by name, so classifying it (KPI
    vs growable) needs the chart's type. Names resolve board-globally — cross-board
    references work (see board_warnings `_detect_orphan_charts`) — so one flat
    map over the whole document is the right resolution model.
    """
    if isinstance(node, dict):
        charts = node.get("charts")
        if isinstance(charts, dict):
            for name, defn in charts.items():
                if not isinstance(defn, dict):
                    continue
                chart_type = defn.get("type")
                if isinstance(chart_type, str):
                    out[name] = chart_type
        for value in node.values():
            _collect_chart_types(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_chart_types(item, out)


def _walk_board_like_mapping(
    node: Any,
    chart_types: dict[str, str],
    path: list[str],
    warnings: list[Diagnostic],
    inherited_source: str | None,
) -> None:
    if not isinstance(node, dict):
        return

    source = node.get("source")
    if isinstance(source, str) and source == inherited_source and path:
        warnings.append(
            restated_diagnostic(
                WARN_RESTATED_INFERRED,
                ".".join([*path, "source"]),
                source,
                "the enclosing board's source",
                "inherited",
            )
        )
    in_force = source if isinstance(source, str) else inherited_source
    queries = node.get("queries")
    if isinstance(queries, dict):
        for query_name, query in queries.items():
            query_source = query.get("source") if isinstance(query, dict) else None
            if isinstance(query_source, str) and query_source == in_force:
                warnings.append(
                    restated_diagnostic(
                        WARN_RESTATED_INFERRED,
                        ".".join([*path, "queries", str(query_name), "source"]),
                        query_source,
                        "the enclosing board's source",
                        "inherited",
                    )
                )
    for key in ("rows", "cols"):
        value = node.get(key)
        if isinstance(value, list):
            if key == "cols":
                _detect_flat_cols_warning(value, chart_types, [*path, key], warnings)
            for index, item in enumerate(value):
                _walk_layout_item(
                    item, chart_types, [*path, key, str(index)], warnings, in_force
                )

    grid = node.get("grid")
    if isinstance(grid, dict):
        items = grid.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items):
                _walk_layout_item(
                    item,
                    chart_types,
                    [*path, "grid", "items", str(index)],
                    warnings,
                    in_force,
                )

    tabs = node.get("tabs")
    if isinstance(tabs, dict):
        items = tabs.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items):
                _walk_layout_item(
                    item,
                    chart_types,
                    [*path, "tabs", "items", str(index)],
                    warnings,
                    in_force,
                )


def _walk_layout_item(
    item: Any,
    chart_types: dict[str, str],
    path: list[str],
    warnings: list[Diagnostic],
    inherited_source: str | None,
) -> None:
    if isinstance(item, dict):
        _walk_board_like_mapping(item, chart_types, path, warnings, inherited_source)
    elif isinstance(item, list):
        for index, child in enumerate(item):
            _walk_layout_item(
                child, chart_types, [*path, str(index)], warnings, inherited_source
            )


def _is_sized_col_cell(item: YamlValue) -> bool:
    """Return whether a `cols:` cell claims an explicit share of row width.

    Layout width comes only from a nested-board wrapper's `width:` field (see
    ``LayoutItem.user_width`` in normalize/layout.py) — an inline chart's own
    `width:` is a render hint (label-tilt heuristic) that does not size its
    column, so bare chart dicts never count as sized here.
    """
    if not isinstance(item, dict):
        return False  # bare chart-name string shorthand
    if "type" in item:
        return False  # inline AuthoredChart shorthand
    if len(item) == 1 and isinstance(next(iter(item.values())), dict):
        return False  # named-chart dict[str, AuthoredChart] shorthand
    return item.get("width") is not None


def _is_kpi_cell(item: YamlValue, chart_types: dict[str, str]) -> bool:
    """Return whether a `cols:` cell is a KPI card.

    KPI cards are height-stable, so a row of them is a deliberate scorecard rather
    than the unpredictable-height anti-pattern the warning targets — they are carved
    out of the count. Handles all three cell shapes: bare name ref, inline chart
    dict, and single-key named-chart shorthand.
    """
    if isinstance(item, str):
        return chart_types.get(item) == "kpi"  # bare named-chart ref
    if not isinstance(item, dict):
        return False
    if item.get("type") == "kpi":
        return True  # inline AuthoredChart shorthand
    if len(item) == 1:
        inner = next(iter(item.values()))
        if isinstance(inner, dict):
            return inner.get("type") == "kpi"  # named-chart dict shorthand
    return False


def _detect_flat_cols_warning(
    cols: list[YamlValue],
    chart_types: dict[str, str],
    path: list[str],
    warnings: list[Diagnostic],
) -> None:
    growable_count = sum(
        1
        for item in cols
        if not _is_sized_col_cell(item) and not _is_kpi_cell(item, chart_types)
    )
    if growable_count <= _MAX_UNSIZED_FLAT_COLS:
        return
    warnings.append(
        Diagnostic.from_code(
            WARN_FLAT_COLS_UNSIZED_OVERFLOW,
            path=".".join(path),
            message=WARN_FLAT_COLS_UNSIZED_OVERFLOW.message_template.format(
                growable_count=growable_count, max_unsized=_MAX_UNSIZED_FLAT_COLS
            ),
            fix=WARN_FLAT_COLS_UNSIZED_OVERFLOW.fix_template,
        )
    )
