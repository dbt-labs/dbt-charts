"""Version-migration module for the 0.9.0 -> 0.10.0 boundary.

THIS FILE IS SCHEMA CHANGES ONLY. Do not add an entry here for anything that
is not a key rename, a key removal, or an authored value's meaning changing
in place. See ``migrations/AGENTS.md``'s "If you catch yourself thinking..."
table.

Changes in this release:

- **`legend.position` became a mapping** of `edge`, `align` and `overlay`: the
  key survives and its value's shape changed, so this is identity-path `Move`s
  carrying an `expansion` (a string rewrites to its mapping; a mapping is never
  touched; `sub_board_prefixes` reaches nested boards). A cardinal pins `overlay: false`
  and leaves `align` to the engine (start; a pie table's own default); a corner pins its edge, alignment and `overlay: true`.
  A pie's key never overlays, so `overlay` is dropped there.
- **The heading ramp moves to the font registry**: ``style.title.sizes``
  becomes ``style.fonts.heading.sizes``. One ``Move`` per board-owned
  ``style:`` block, resolved mechanically from ``AuthoredBoard`` down to
  ``BOARD_NESTING_DEPTH`` levels of nested boards.
- **Separator lines are named `divider`**: `text.column.rule`, `text.rule`,
  `footer.rule`, `table.header.rule`, `table.row.rule` and
  `support_table.row.rule` become `divider` (`Move`s). A `rewrite` fills the
  new `visible` switch: a key whose presence meant "on" gains `visible: true`,
  a table or support-table `width` of 0 becomes `visible: false`, and a footer
  `rule: null`, which merged as "inherit", is dropped. `footer.rule.stroke_width`
  becomes `width`, and the column rule's `style` becomes a `dasharray`.
- **Table dividers own their color and width**: `row.rule.color`, else
  `table.rule.color`, is written to the header, row, summary and total dividers
  (identity-path `Move`; `table.rule` and `header.rule.color` are dropped).
  `roles.summary.rule_width` becomes a `divider` on both roles, and
  `roles.total.rule_width` is dropped.
- **`support_table.divider` keeps its key** and loses `continuous`; a `width`
  above 0 gains `visible: true` (identity-path `Move`). Written alone,
  `{width: 1}` is valid in both grammars and cannot be told apart.
- **KPI content at root, styling under `style:`** (`Move`s): `variant`,
  `support.format` and `support.tone` move to `style.variant`,
  `style.support.format` and `style.support.tone`; `style.glyph.character`
  becomes root `glyph`; `style.glyph.font` and `style.affix.font` become
  `style.value.glyph.font` and `style.value.affix.font`; `style.title.overflow`
  becomes `style.label.overflow`. The theme paths under `style.charts.kpi`
  move alike. `Deletion`s: theme `glyph.character` (glyph text is per chart),
  the remaining KPI `title` blocks, `character` on the label and affix slots,
  and `line_height` on every KPI font (each slot paints one line; a moved
  affix or glyph font drops it in the move's `rewrite`).
- **Chart-level heading ramps are removed**: ``style.charts.title.sizes``
  (and ``style.charts.<family>.title.sizes``) and a chart-local
  ``style.title.sizes`` have no successor, because the ramp is a board-wide
  font. Each is a ``Deletion`` that can only reach a chart-level style.
- **Chart-wide format keys retire onto the axis slots**: a chart's
  ``style.number_format`` becomes ``style.axis_y.labels.format`` (the measure
  axis, whatever the orientation) and ``style.time_format`` becomes
  ``style.axis_x.labels.format`` (``Move``s scoped to the chart family). A
  ``Move`` raises when the chart's own ``axis`` or ``axis_quantitative`` labels
  already carry a format. ``time_format`` and a scatter's ``number_format``
  raise: the old keys applied only on a date or numeric channel, which the YAML
  does not say. A heatmap's
  keys, and ``style.charts.<family>.number_format`` / ``time_format`` (never
  read), are ``Deletion``s with a reason.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable

from dbt_charts.core.compile.migrations.migrations import (
    DROP,
    Deletion,
    Dropped,
    MappedScalar,
    MigrationError,
    Move,
    YamlKeyPath,
    _child_positions,
    _declared_names,
    _declares_tail,
    _relative_field_paths,
    _schema_path_exists,
    _without_composition,
    board_nesting_prefixes,
    suffix_rename_moves,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    JsonValue,
    YamlSchemaCatalog,
)

THEME_RENAMES: dict[MappedScalar, MappedScalar] = {}

LEGEND_POSITION_TAIL: YamlKeyPath = ("legend", "position")


LEGEND_POSITION_EXPANSION: dict[str, dict[str, MappedScalar]] = {
    "left": {"edge": "left", "overlay": False},
    "right": {"edge": "right", "overlay": False},
    "top": {"edge": "top", "overlay": False},
    "bottom": {"edge": "bottom", "overlay": False},
    "top-left": {"edge": "top", "align": "start", "overlay": True},
    "top-right": {"edge": "top", "align": "end", "overlay": True},
    "bottom-left": {"edge": "bottom", "align": "start", "overlay": True},
    "bottom-right": {"edge": "bottom", "align": "end", "overlay": True},
}

LEGEND_POSITION_DROP_NOTES: dict[str, str] = {
    "overlay": (
        "A pie legend corner (`top-left`, `top-right`, `bottom-left`, "
        "`bottom-right`) floated over the wedges. A pie key never overlays, so "
        "the corner became its edge and alignment, and the overlay was dropped."
    )
}

# `_relative_field_paths` treats a board nested in a board as opaque, so deeper
# boards are reached by composing the one-level steps. Past this depth a board
# ramp is left in place and the board fails to parse loudly.
BOARD_NESTING_DEPTH = 3

_BOARD_STEP_SEGMENTS = frozenset({"rows", "cols", "grid", "items", "item", "tabs", "*"})

_RAMP_TAIL: YamlKeyPath = ("style", "title", "sizes")

_RAMP_DELETION_REASON = (
    "The H1-H6 heading ramp is one board-wide font, so a chart-level "
    "title.sizes was dropped. Set style.fonts.heading.sizes to resize headings."
)


def _board_style_paths() -> list[tuple[str, ...]]:
    """Paths of every board-owned ``style`` block, root included."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    try:
        one_level = dict.fromkeys(
            _relative_field_paths(AuthoredBoard, frozenset(), frozenset({("style",)}))
        )
    finally:
        # Not persisted across calls -- see suffix_rename_moves's docstring.
        _relative_field_paths.cache_clear()
    prefixes = {
        path[:-1]
        for path in one_level
        if path[-1:] == ("style",)
        and path[:-1]
        and set(path[:-1]) <= _BOARD_STEP_SEGMENTS
    }
    # A step that is two others end to end adds nothing once steps compose.
    steps = [
        p
        for p in prefixes
        if not any(p[: len(q)] == q and p[len(q) :] in prefixes for q in prefixes)
    ]
    levels: list[tuple[str, ...]] = [()]
    frontier: list[tuple[str, ...]] = [()]
    for _ in range(BOARD_NESTING_DEPTH):
        frontier = [(*base, *step) for base in frontier for step in steps]
        levels.extend(frontier)
    return [(*prefix, "style") for prefix in dict.fromkeys(levels)]


def moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    """Return Move objects for the 0.9.0 -> 0.10.0 boundary."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    nesting = board_nesting_prefixes(AuthoredBoard)
    return (
        _heading_ramp_moves(source_schema, target_schema, catalog)
        + _divider_moves(source_schema, target_schema, catalog)
        + _kpi_moves(source_schema, target_schema, catalog)
        + _chart_format_moves(source_schema, target_schema, catalog)
        + tuple(
            dataclasses.replace(
                move,
                expansion=LEGEND_POSITION_EXPANSION,
                drop_notes=LEGEND_POSITION_DROP_NOTES,
                sub_board_prefixes=nesting,
            )
            for move in suffix_rename_moves(
                AuthoredBoard,
                source_schema,
                target_schema,
                [(LEGEND_POSITION_TAIL, LEGEND_POSITION_TAIL)],
                catalog=catalog,
            )
        )
    )


_CHART_FORMAT_MEASURE_FAMILIES = ("bar", "line", "area", "histogram")
_CHART_FORMAT_THEME_FAMILIES = (*_CHART_FORMAT_MEASURE_FAMILIES, "scatter", "heatmap")

# Chart-local axis layers that sat below the old key and now would sit above it.
_MEASURE_FORMAT_COMPETITORS: tuple[YamlKeyPath, ...] = (
    ("axis", "labels", "format"),
    ("axis_quantitative", "labels", "format"),
)

_FORMAT_KEY_DELETION_REASON = (
    "{key} was never read here, so it formatted nothing. Set a slot's "
    "labels.format (style.axis_y.labels.format) or formats: on the query."
)
_HEATMAP_FORMAT_REASON = (
    "A heatmap's axes are both grid dimensions, so {key} never applied. "
    "Set formats: on the query."
)


def _ambiguous_chart_format(message: str) -> Callable[[JsonValue], JsonValue | Dropped]:
    def refuse(value: JsonValue) -> JsonValue | Dropped:
        raise MigrationError(f"Cannot migrate {message} Migrate this field manually.")

    return refuse


# The old keys formatted an axis only when its channel was numeric (number_format)
# or temporal (time_format), and the YAML cannot say which channel an axis is.
_TIME_FORMAT_REFUSAL = _ambiguous_chart_format(
    "style.time_format: it formatted x only when x is a date, and the YAML does "
    "not say. If x is a date, move it to style.axis_x.labels.format (or "
    "style.axis_y.labels.format on a scatter's date y); otherwise delete it."
)
_SCATTER_NUMBER_FORMAT_REFUSAL = _ambiguous_chart_format(
    "style.number_format on a scatter chart: it formatted y only when y is "
    "numeric, and a dot plot puts the measure on x. Move it to "
    "style.axis_y.labels.format or style.axis_x.labels.format."
)


def _chart_format_moves(
    source_schema: str, target_schema: str, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    def scoped(
        old: str,
        new_axis: str,
        families: tuple[str, ...],
        *,
        rewrite: Callable[[JsonValue], JsonValue | Dropped] | None = None,
        conflicts_with: tuple[YamlKeyPath, ...] = (),
    ) -> tuple[Move, ...]:
        found = suffix_rename_moves(
            AuthoredBoard,
            source_schema,
            target_schema,
            [(("style", new_axis, "labels", "format"), ("style", old))],
            catalog=catalog,
        )
        return tuple(
            dataclasses.replace(
                move,
                chart_type=family,
                rewrite=rewrite,
                conflicts_with=conflicts_with,
            )
            for move in found
            for family in families
        )

    return (
        scoped(
            "number_format",
            "axis_y",
            _CHART_FORMAT_MEASURE_FAMILIES,
            conflicts_with=_MEASURE_FORMAT_COMPETITORS,
        )
        + scoped(
            "number_format",
            "axis_y",
            ("scatter",),
            rewrite=_SCATTER_NUMBER_FORMAT_REFUSAL,
        )
        + scoped(
            "time_format",
            "axis_x",
            (*_CHART_FORMAT_MEASURE_FAMILIES, "scatter"),
            rewrite=_TIME_FORMAT_REFUSAL,
        )
    )


def _line(value: JsonValue, key: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise MigrationError(
            f"Cannot migrate {key!r}: expected a mapping, got {value!r}. "
            "Migrate this field manually."
        )
    return dict(value)


_LINE_STYLE_DASHARRAYS: dict[JsonValue, str | None] = {
    "solid": None,
    "dashed": "6 3",
    "dotted": "2 2",
}


def _present_means_on(value: JsonValue) -> JsonValue | Dropped:
    """A `rule` whose presence drew the line: it gains `visible: true`.

    Null merged as "inherit", so it said nothing and is dropped.
    """
    if value is None:
        return DROP
    line = _line(value, "rule")
    if "stroke_width" in line:
        line["width"] = line.pop("stroke_width")
    if "style" in line:
        dashes = _LINE_STYLE_DASHARRAYS[line.pop("style")]
        if dashes is not None:
            line["dasharray"] = dashes
    return {"visible": True, **line}


def _width_means_visibility(value: JsonValue) -> JsonValue | Dropped:
    """A `rule` that drew whenever its width was above 0: the width becomes
    `visible`, and a 0 is no width at all. A `rule` without a width keeps
    whatever visibility the theme gave it."""
    if value is None:
        return DROP
    line = _line(value, "rule")
    width = line.get("width")
    if width is None or "visible" in line:
        return line
    if isinstance(width, bool) or not isinstance(width, (int, float)):
        raise MigrationError(
            f"Cannot migrate a rule width of {width!r}. Migrate this field manually."
        )
    if width > 0:
        return {**line, "visible": True}
    line.pop("width")
    return {**line, "visible": False}


def _support_width_means_visibility(value: JsonValue) -> JsonValue | Dropped:
    """`_width_means_visibility` for a support table, whose `continuous` was
    never read."""
    if isinstance(value, dict):
        value = {k: v for k, v in value.items() if k != "continuous"}
    return _width_means_visibility(value)


def _block(parent: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    """The mapping at *key*, created when absent."""
    child = parent.setdefault(key, {})
    if not isinstance(child, dict):
        raise MigrationError(
            f"Cannot migrate {key!r}: expected a mapping, got {child!r}. "
            "Migrate this field manually."
        )
    return child


def _table_dividers(value: JsonValue) -> JsonValue | Dropped:
    """Write the color and width the old table lines shared to each divider.

    Resolved per style block: values authored at two levels (board and chart)
    are not merged first, so they can resolve differently.
    """
    if not isinstance(value, dict):
        return value
    out = copy.deepcopy(value)
    row = out.get("row")
    row_rule = row.get("rule") if isinstance(row, dict) else None
    color = None
    table_rule = out.pop("rule", None)
    if isinstance(table_rule, dict):
        color = table_rule.get("color")
    if isinstance(row_rule, dict) and row_rule.get("color") is not None:
        color = row_rule["color"]
    roles = row.get("roles") if isinstance(row, dict) else None
    if isinstance(roles, dict):
        _role_widths(roles, row_rule)
    header = out.get("header")
    if color is None:
        header_rule = header.get("rule") if isinstance(header, dict) else None
        if isinstance(header_rule, dict):
            header_rule.pop("color", None)
        return out
    row_block = _block(out, "row")
    _block(row_block, "rule")["color"] = color
    _block(_block(out, "header"), "rule")["color"] = color
    roles_block = _block(row_block, "roles")
    for role in ("summary", "total"):
        _block(_block(roles_block, role), "divider").setdefault("color", color)
    return out


def _width(value: JsonValue) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MigrationError(
            f"Cannot migrate rule width {value!r}. Migrate this field manually."
        )
    return value


def _role_widths(roles: dict[str, JsonValue], row_rule: JsonValue) -> None:
    summary, total = roles.get("summary"), roles.get("total")
    if isinstance(total, dict):
        total.pop("rule_width", None)
    if not isinstance(summary, dict) or "rule_width" not in summary:
        return
    width = summary.pop("rule_width")
    if width is None:
        return
    # The shipped themes give body rows no line, so an unauthored width is 0.
    row_width = (
        _width(row_rule["width"])
        if isinstance(row_rule, dict) and "width" in row_rule
        else 0
    )
    effective = _width(width) or row_width
    divider: dict[str, JsonValue] = (
        {"visible": True, "width": effective} if effective > 0 else {"visible": False}
    )
    summary["divider"] = divider
    if total is None:
        roles["total"] = {"divider": dict(divider)}
    elif isinstance(total, dict):
        total.setdefault("divider", dict(divider))


def _divider_moves(
    source_schema: str, target_schema: str, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    def moves_for(
        renames: list[tuple[YamlKeyPath, YamlKeyPath]],
        rewrite: Callable[[JsonValue], JsonValue | Dropped] | None,
    ) -> tuple[Move, ...]:
        return tuple(
            dataclasses.replace(move, rewrite=rewrite)
            for move in suffix_rename_moves(
                AuthoredBoard, source_schema, target_schema, renames, catalog=catalog
            )
        )

    return (
        # First: it reads the old `rule` keys the renames below move.
        moves_for(
            [(("charts", "table"), ("charts", "table")), (("style",), ("style",))],
            _table_dividers,
        )
        + moves_for(
            [
                (("text", "column", "divider"), ("text", "column", "rule")),
                (("footer", "divider"), ("footer", "rule")),
            ],
            _present_means_on,
        )
        + moves_for([(("text", "divider"), ("text", "rule"))], None)
        + moves_for(
            [
                (("table", "header", "divider"), ("table", "header", "rule")),
                (("table", "row", "divider"), ("table", "row", "rule")),
                (("style", "header", "divider"), ("style", "header", "rule")),
                (("style", "row", "divider"), ("style", "row", "rule")),
            ],
            _width_means_visibility,
        )
        + moves_for(
            [
                (
                    ("support_table", "row", "divider"),
                    ("support_table", "row", "rule"),
                ),
                (("support_table", "divider"), ("support_table", "divider")),
            ],
            _support_width_means_visibility,
        )
    )


KPI_LINE_HEIGHT_NOTE = (
    "line_height has no effect on KPI text slots (each renders a single line); removed."
)

KPI_RENAMES: list[tuple[YamlKeyPath, YamlKeyPath]] = [
    (("style", "variant"), ("variant",)),
    (("style", "support", "format"), ("support", "format")),
    (("style", "support", "tone"), ("support", "tone")),
    (("glyph",), ("style", "glyph", "character")),
    (("charts", "kpi", "label", "overflow"), ("charts", "kpi", "title", "overflow")),
]

# Every chart family keeps `style.title.overflow`; only a KPI's moves.
KPI_LABEL_OVERFLOW_RENAME: list[tuple[YamlKeyPath, YamlKeyPath]] = [
    (("style", "label", "overflow"), ("style", "title", "overflow")),
]

KPI_FONT_RENAMES: list[tuple[YamlKeyPath, YamlKeyPath]] = [
    (("style", "value", "glyph", "font"), ("style", "glyph", "font")),
    (("style", "value", "affix", "font"), ("style", "affix", "font")),
    (
        ("charts", "kpi", "value", "glyph", "font"),
        ("charts", "kpi", "glyph", "font"),
    ),
    (
        ("charts", "kpi", "value", "affix", "font"),
        ("charts", "kpi", "affix", "font"),
    ),
]


def _without_line_height(value: JsonValue) -> JsonValue | Dropped:
    if isinstance(value, dict) and "line_height" in value:
        return {k: v for k, v in value.items() if k != "line_height"}
    return value


def _kpi_moves(
    source_schema: str, target_schema: str, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    return (
        suffix_rename_moves(
            AuthoredBoard, source_schema, target_schema, KPI_RENAMES, catalog=catalog
        )
        + suffix_rename_moves(
            AuthoredBoard,
            source_schema,
            target_schema,
            KPI_LABEL_OVERFLOW_RENAME,
            catalog=catalog,
            chart_type="kpi",
        )
        + tuple(
            dataclasses.replace(
                move, rewrite=_without_line_height, rewrite_note=KPI_LINE_HEIGHT_NOTE
            )
            for move in suffix_rename_moves(
                AuthoredBoard,
                source_schema,
                target_schema,
                KPI_FONT_RENAMES,
                catalog=catalog,
            )
        )
    )


_KPI_NO_TITLE = (
    "A KPI has no title: its overflow moved to label.overflow, and any other "
    "title setting was dropped. Style the label under label."
)
_KPI_CHARACTER = "character only drew on the KPI glyph slot; it was ignored here."

# (tail, reason, chart_type)
_KPI_DELETIONS: list[tuple[YamlKeyPath, str | None, str | None]] = [
    (
        ("charts", "kpi", "glyph", "character"),
        "A glyph is per-chart content: set glyph on each KPI.",
        None,
    ),
    (("charts", "kpi", "title"), _KPI_NO_TITLE, None),
    (("charts", "kpi", "label", "character"), _KPI_CHARACTER, None),
    (("charts", "kpi", "affix", "character"), _KPI_CHARACTER, None),
    (("charts", "kpi", "font", "line_height"), KPI_LINE_HEIGHT_NOTE, None),
    (("charts", "kpi", "value", "font", "line_height"), KPI_LINE_HEIGHT_NOTE, None),
    (("charts", "kpi", "label", "font", "line_height"), KPI_LINE_HEIGHT_NOTE, None),
    (("style", "title"), _KPI_NO_TITLE, "kpi"),
    (("style", "label", "character"), _KPI_CHARACTER, "kpi"),
    (("style", "affix", "character"), _KPI_CHARACTER, "kpi"),
    (("style", "font", "line_height"), KPI_LINE_HEIGHT_NOTE, "kpi"),
    (("style", "value", "font", "line_height"), KPI_LINE_HEIGHT_NOTE, "kpi"),
    (("style", "label", "font", "line_height"), KPI_LINE_HEIGHT_NOTE, "kpi"),
    # What the font and character moves leave behind.
    (("charts", "kpi", "glyph"), None, None),
    (("charts", "kpi", "affix"), None, None),
    (("style", "glyph"), None, "kpi"),
    (("style", "affix"), None, "kpi"),
]


def _heading_ramp_moves(
    source_schema: str, target_schema: str, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:

    source_schema_obj = catalog.grammar_for(source_schema)
    target_schema_obj = catalog.grammar_for(target_schema)
    result: list[Move] = []
    for path in _board_style_paths():
        old = (*path, "title", "sizes")
        new = (*path, "fonts", "heading", "sizes")
        if _schema_path_exists(source_schema_obj, old) and _schema_path_exists(
            target_schema_obj, new, open_map_keys=True
        ):
            result.append(Move(source_schema, target_schema, old, new))
    return tuple(result)


def _chart_types_with_title_ramp(schema: JsonObject) -> tuple[str, ...]:
    """Chart ``type:`` literals whose own ``style.title`` declares ``sizes``."""
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    types: set[str] = set()
    for variant in definitions.values():
        assert isinstance(variant, dict)
        properties = variant.get("properties")
        type_schema = properties.get("type") if isinstance(properties, dict) else None
        enum = type_schema.get("enum") if isinstance(type_schema, dict) else None
        own = _without_composition(variant)
        if (
            isinstance(enum, list)
            and own is not None
            and _declares_tail(schema, [own], _RAMP_TAIL)
        ):
            types.update(t for t in enum if isinstance(t, str))
    return tuple(sorted(types))


def _chart_families_with_title_ramp(schema: JsonObject) -> tuple[str, ...]:
    """``style.charts`` families whose ``title`` declares ``sizes``."""
    charts = [schema]
    for key in ("style", "charts"):
        charts = _child_positions(schema, charts, key)
    return tuple(
        sorted(
            family
            for family in _declared_names(schema, charts)
            if _declares_tail(
                schema, _child_positions(schema, charts, family), ("title", "sizes")
            )
        )
    )


def deletions(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Deletion, ...]:
    """Return the Deletions of chart-level ``title.sizes``, retired KPI keys, and
    the format keys no slot ever read."""
    source_schema_obj = catalog.grammar_for(source_schema)
    tails = [
        (*prefix, "title", "sizes")
        for prefix in [
            ("style", "charts"),
            *(
                ("style", "charts", family)
                for family in _chart_families_with_title_ramp(source_schema_obj)
            ),
        ]
        if _schema_path_exists(source_schema_obj, (*prefix, "title", "sizes"))
    ]
    deletions = [
        Deletion(source_schema, target_schema, tail, reason=_RAMP_DELETION_REASON)
        for tail in tails
    ]
    deletions.extend(
        Deletion(
            source_schema,
            target_schema,
            _RAMP_TAIL,
            reason=_RAMP_DELETION_REASON,
            chart_type=chart_type,
        )
        for chart_type in _chart_types_with_title_ramp(source_schema_obj)
    )
    deletions.extend(
        Deletion(source_schema, target_schema, tail, reason=reason, chart_type=family)
        for tail, reason, family in _KPI_DELETIONS
    )
    for key in ("number_format", "time_format"):
        deletions.extend(
            Deletion(
                source_schema,
                target_schema,
                ("style", "charts", family, key),
                reason=_FORMAT_KEY_DELETION_REASON.format(key=key),
            )
            for family in _CHART_FORMAT_THEME_FAMILIES
            if _schema_path_exists(source_schema_obj, ("style", "charts", family, key))
        )
        deletions.append(
            Deletion(
                source_schema,
                target_schema,
                ("style", key),
                reason=_HEATMAP_FORMAT_REASON.format(key=key),
                chart_type="heatmap",
            )
        )
    return tuple(deletions)
