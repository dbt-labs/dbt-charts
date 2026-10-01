"""Compile-time validation of authored format-string slots.

Authored format strings are checked against three layers, in order: the
predefined engine vocabulary, the theme/board alias table, then
``d3_format.parse``. A spec that survives none of the three — a typo like
``percent_1`` — raises ``ERR-FORMAT-INVALID`` here, at compile, instead of
surfacing as ``ERR-INTERNAL`` deep inside rasterization (see
``core/compile/format.py``'s ``resolve_format`` docstring for the three-way
contract this validator runs ahead of).

Coverage is the authored surface: board-level ``style:`` and every chart,
recursively, plus the alias table's own targets (validated via
``_validate_board`` with ``allow_predefined=False``). Slots are recognized by
field name (``_FORMAT_FIELDS``) rather than by type annotation; every current
format field is spelled one of those three names, so the coverage is complete.

Time-format slots (axis ticks, table columns — anywhere a date-typed column
value might render) additionally accept a strftime-style spec (``%b %Y``):
an author may write the strftime form directly rather than through a predefined
alias (``date_short``).

A slot that knows its own kind — ``number_format`` feeds a quantitative axis,
``time_format`` a temporal one — accepts only that half of the predefined
vocabulary; the other half raises ``ERR-FORMAT-KIND-MISMATCH``. The kind is read
off the field's ``Format`` facet rather than its name, so it travels with the
field. User ``style.formats`` aliases are exempt: the engine cannot know which
kind a user's alias targets, so they stay legal in every slot.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from d3_format import parse as _d3_parse
from d3_format.errors import D3FormatError
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.markers import Format
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_FORMAT_INVALID,
    ERR_FORMAT_KIND_MISMATCH,
    ERR_FORMAT_NATIVE_IN_VEGA_SLOT,
    ERR_FORMAT_PREDEFINED_SHADOW,
    ERR_FORMAT_TIME_DIRECTIVE_UNSUPPORTED,
)
from dbt_charts.core.text.format_d3 import (
    D3_TIME_FORMAT_DIRECTIVES,
    PYTHON_STRFTIME_DIRECTIVES,
    VEGA_SAFE_TIME_DIRECTIVES,
    find_unsupported_directive,
    is_time_format,
    unsupported_time_directives,
)
from dbt_charts.core.text.predefined_formats import (
    ALL_PREDEFINED_NAMES,
    PREDEFINED_NATIVE_NAMES,
    PREDEFINED_NUMBER_NAMES,
    PREDEFINED_TIME_NAMES,
)

# Field names that hold an authored format spec. Every current format field is
# spelled one of these; a future field named otherwise would need a new entry here.
_FORMAT_FIELDS = frozenset({"format", "number_format", "time_format"})

# Field names whose subtree may also carry a strftime spec: axis ticks, table
# columns, and the footer timestamp are the slots that render a date. Matched on
# structural field names during the walk, never on the flattened path -- a
# chart id or column name could otherwise spell one of these and silently
# widen what its siblings accept.
#
# `axis`, `axis_band`, and `axis_quantitative` are chart-local axis overrides
# (`style_cascade.py`'s `_merge_axis_cascade`) that merge into the same
# resolved axis `format` field as `axis_x`/`axis_y` -- a strftime spec there
# is exactly as legitimate.
_TIME_CAPABLE_FIELDS = frozenset(
    {
        "axis",
        "axis_x",
        "axis_y",
        "axis_band",
        "axis_quantitative",
        "mirror",
        "columns",
        "column_defaults",
        "time_format",
        "timestamp",
    }
)

# Per slot kind: the predefined names it accepts, and the sentence naming what
# else is legal there. "any" is absent on purpose -- a missing entry means no
# narrowing, which is what a kind-agnostic `format:` slot wants.
#
# The escape-hatch sentence is kind-specific because the generic one was wrong
# half the time: a raw d3 *number* spec is exactly what must not reach a
# temporal axis, so telling a `time_format` author "a raw d3 spec works too"
# reproduces the defect this error exists to reject.
_KIND_RULES: dict[str, tuple[frozenset[str], str]] = {
    "number": (
        PREDEFINED_NUMBER_NAMES,
        "A raw d3 number spec (e.g. ',.0f') or a `style.formats` alias of your "
        "own works here too.",
    ),
    "time": (
        PREDEFINED_TIME_NAMES,
        "A strftime spec (e.g. '%b %Y') or a `style.formats` alias of your own "
        "works here too.",
    ),
}

# The full set of directive letters this compile-time check accepts: Vega
# paints a time-format-capable slot via d3-time-format, and the Python
# painters (table cells, KPI values) go through portable_strftime — a
# directive is legal here if either engine implements it.
_ACCEPTED_TIME_DIRECTIVES = PYTHON_STRFTIME_DIRECTIVES | D3_TIME_FORMAT_DIRECTIVES

# Field names whose child format slots are rendered by Vega (not Python).
# PREDEFINED_NATIVE members bypass d3 and have no Vega equivalent; they are
# rejected here so the author gets a compile error rather than a Vega crash.
# `number_format` and `time_format` are always Vega-painted regardless of parent;
# _iter_format_slots sets vega_painted=True for those field names directly.
_VEGA_PAINTED_PARENTS = frozenset(
    {
        "axis",
        "axis_x",
        "axis_y",
        "axis_band",
        "axis_quantitative",
        "mirror",
        "labels",  # MarkLabelsStyle / BarLabelsStyle / PointLabelsStyle
        "total_label",  # BarTotalLabelStyle: bar stack total label
        "total",  # TotalStyle: donut center total (pie.py → Vega text-mark encoding)
        "tooltip",  # TooltipStyle: Vega-Lite tooltip format across all chart families
        "support_table",  # ChartSupportTableSource/Aggregate/PerSeries: _vl_format_calc emits into Vega calculate transform
    }
)


def _slot_kind(model: type[BaseModel], name: str) -> str:
    """The declared kind of one format slot: "number", "time", or "any".

    Read from the field's ``Format`` facet, which ``build_patch_model_ext``
    forwards onto every generated patch model — so a chart's authored
    ``BarChartStylePatch.time_format`` answers the same as the theme field it
    was generated from.
    """
    for meta in model.model_fields[name].metadata:
        if isinstance(meta, Format):
            return meta.kind
    return "any"


def _iter_format_slots(
    node: object, path: str, time_capable: bool, vega_painted: bool = False
) -> Iterator[tuple[str, str | FormatConfig, bool, bool, str]]:
    """Yield (field_path, spec, time_capable, vega_painted, kind) per format slot."""
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            if name == "query":  # SQL text and source/cache config, no format slots
                continue
            value = getattr(node, name)
            if value is None:
                continue
            child_time = time_capable or name in _TIME_CAPABLE_FIELDS
            child_vega = vega_painted or name in _VEGA_PAINTED_PARENTS
            child = f"{path}.{name}"
            if name in _FORMAT_FIELDS and isinstance(value, (str, FormatConfig)):
                # number_format and time_format are always Vega-painted: both feed
                # into axis.labels.format via the Layer-10 merge in axis_cascade.py
                # and are rendered by Vega, never by Python renderers.
                slot_vega = child_vega or name in ("number_format", "time_format")
                yield child, value, child_time, slot_vega, _slot_kind(type(node), name)
            else:
                yield from _iter_format_slots(value, child, child_time, child_vega)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_format_slots(
                value, f"{path}.{key}", time_capable, vega_painted
            )
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _iter_format_slots(
                value, f"{path}.{index}", time_capable, vega_painted
            )


def _raise_if_unsupported_time_directives(spec: str, field_path: str) -> None:
    unsupported = unsupported_time_directives(spec)
    if unsupported:
        raise CompilationError.from_code(
            ERR_FORMAT_TIME_DIRECTIVE_UNSUPPORTED,
            spec=spec,
            field_path=field_path,
            directives=", ".join(f"%{letter}" for letter in unsupported),
            available=", ".join(
                f"%{letter}" for letter in sorted(VEGA_SAFE_TIME_DIRECTIVES)
            ),
        )


def _validate_spec(
    value: str | FormatConfig,
    formats: dict[str, str],
    field_path: str,
    time_format: bool,
    allow_predefined: bool = True,
    vega_painted: bool = False,
    kind: str = "any",
) -> None:
    """Raise if ``value`` resolves to an unusable spec.

    Also called from ``_validate_board`` on the four global theme-resolved
    axis slots (``charts.axis``/``axis_x``/``axis_y``/``axis_quantitative``)
    to catch a theme-baked default (e.g.
    ``axis_quantitative.labels.format == "number"``) whose alias no
    longer resolves -- a board that clears ``style.formats`` to ``null`` is
    valid, compile()-accepted authoring (``formats`` merges key-wise;
    explicitly nulling it is the one way to drop an inherited key), but it
    is never an *authored* format string itself, so this module's own
    per-chart walk (``_iter_format_slots``) never reaches it.

    ``allow_predefined=False`` is passed when validating alias targets: a
    predefined name is not a valid d3-format spec, so ``formats: {mine: currency}``
    must raise at compile rather than silently passing validation and dying at
    render with ERR-INTERNAL.

    ``vega_painted=True`` is passed for slots rendered by Vega (axis labels,
    mark value labels, ``number_format``, ``time_format``, ``support_table``).
    PREDEFINED_NATIVE members bypass d3 entirely and have no Vega equivalent;
    they are rejected here so the author gets ERR-FORMAT-NATIVE-IN-VEGA-SLOT
    rather than a Vega runtime crash.

    ``kind`` is the slot's own half of the vocabulary (``"number"`` for
    ``number_format``, ``"time"`` for ``time_format``, ``"any"`` everywhere
    else). A predefined name from the other half raises
    ERR-FORMAT-KIND-MISMATCH: ``time_format: currency`` resolves to ``$,.2f``,
    which Vega bakes onto a temporal axis as garbage tick labels rather than
    failing.
    """
    spec = value.spec if isinstance(value, FormatConfig) else value
    if not spec:
        return
    rule = _KIND_RULES.get(kind)
    if allow_predefined and spec in ALL_PREDEFINED_NAMES:
        if vega_painted and spec in PREDEFINED_NATIVE_NAMES:
            raise CompilationError.from_code(
                ERR_FORMAT_NATIVE_IN_VEGA_SLOT,
                spec=spec,
                field_path=field_path,
                available=sorted(ALL_PREDEFINED_NAMES - PREDEFINED_NATIVE_NAMES),
            )
        if rule is not None and spec not in rule[0]:
            raise CompilationError.from_code(
                ERR_FORMAT_KIND_MISMATCH,
                spec=spec,
                field_path=field_path,
                kind=kind,
                available=sorted(rule[0] - PREDEFINED_NATIVE_NAMES),
                escape_hatch=rule[1],
            )
        return
    if spec in formats:
        # An alias is just another spelling of whatever slot it resolves
        # in -- a Vega-painted `time_format: myalias` risks the identical
        # measure-vs-paint divergence as authoring the directive inline, so
        # the gate must run against the *resolved* target, not the alias
        # name (which never matches `is_time_format` itself and would
        # short-circuit past the check entirely). The alias's *definition*
        # site (`style.formats.myalias: ...`) is validated separately with
        # `vega_painted` at its default `False`, since the same alias is
        # legitimately fine in a Python-painted slot (a table column).
        target = formats[spec]
        if time_format and vega_painted and is_time_format(target):
            _raise_if_unsupported_time_directives(target, field_path)
        return
    if time_format and is_time_format(spec):
        # A directive neither d3-time-format nor portable_strftime implements
        # must fail here, at compile -- not reach render, where it's either
        # an uncoded ValueError (ERR-INTERNAL, Python-painted slots) or a
        # literal, unrendered letter (Vega-painted slots).
        bad_directive = find_unsupported_directive(spec, _ACCEPTED_TIME_DIRECTIVES)
        if bad_directive is not None:
            raise CompilationError.from_code(
                ERR_FORMAT_INVALID,
                spec=spec,
                field_path=field_path,
                kind_noun="date/time",
                explanation=(
                    f"%{bad_directive} is not a strftime directive the engine "
                    "implements. Accepted directives: "
                    f"{', '.join('%' + d for d in sorted(_ACCEPTED_TIME_DIRECTIVES))}."
                ),
            )
        # Of the accepted directives, only a Vega-painted slot risks a
        # measure-vs-paint divergence -- a table column or the footer
        # timestamp renders through Python directly, so any directive
        # portable_strftime itself accepts is fine there.
        if vega_painted:
            _raise_if_unsupported_time_directives(spec, field_path)
        return
    try:
        _d3_parse(spec)
    except D3FormatError as e:
        # Alias targets must be valid d3 specs; predefined names are not valid
        # d3 specs and are already excluded. Including them in available would
        # produce "Did you mean 'currency'?" when rejecting "currency" as a target.
        # Scoped to the slot's own half: suggesting `currency` for `currencyy`
        # in a `time_format` would hand the author the very value the kind check
        # rejects one compile later.
        available = (
            sorted(formats)
            if not allow_predefined
            else sorted({*formats, *(rule[0] if rule else ALL_PREDEFINED_NAMES)})
        )
        if kind == "time":
            # No `%` directive was found, so this isn't a d3-format parse
            # failure at all -- pointing the author at number-spec parsing
            # would mislead them; the slot's own kind already settles what
            # it should have written instead.
            explanation = (
                "it is not an engine-predefined format name and not a key "
                f"in `style.formats`. {_KIND_RULES['time'][1]}"
            )
        else:
            explanation = (
                "it is not an engine-predefined format name, not a key in "
                f"`style.formats`, and is not a valid d3-format spec ({e.reason} "
                f"at position {e.position})."
            )
        raise CompilationError.from_code(
            ERR_FORMAT_INVALID,
            spec=spec,
            field_path=field_path,
            kind_noun="date/time" if kind == "time" else "number",
            explanation=explanation,
            available=available,
        ) from e


def validate_board_format_specs(board: Board) -> None:
    """Walk a normalized Board tree, validating every authored format string.

    Recurses into nested boards -- each carries its own resolved theme, and
    therefore its own alias table (``board.chart_style_context.formats``).
    """
    _validate_board(board, set())


def _validate_board(board: Board, validated: set[int]) -> None:
    """Validate one board's format slots, skipping charts a descendant owns."""
    # Nested boards first. Normalization hoists a nested board's charts into
    # every ancestor's `charts` registry (`_collect_charts_from_layout`), by
    # reference -- so the identical chart object is reachable from a board whose
    # alias table never defined the alias that chart uses. Validating the owner
    # first and skipping the object here keeps every chart judged against the
    # table that actually resolves it, and walks each chart exactly once.
    for item in board.layout.items:
        if item.type == "board" and item.board is not None:
            _validate_board(item.board, validated)

    formats = board.chart_style_context.formats
    if formats is None:
        formats = {}
    # Shadowing a predefined enum member name in style.formats is always a bug:
    # the engine owns those names and resolve_format checks them first.
    for alias in formats:
        if alias in ALL_PREDEFINED_NAMES:
            raise CompilationError.from_code(
                ERR_FORMAT_PREDEFINED_SHADOW,
                spec=alias,
                field_path=f"style.formats.{alias}",
            )
    # An alias is only useful if its target resolves; checking the keys alone
    # would let `formats: {mine: bogus}` reach the render-time raise.
    # A predefined name (e.g. "currency") is not a valid d3 spec — reject it.
    for alias, target in formats.items():
        _validate_spec(
            target, {}, f"style.formats.{alias}", True, allow_predefined=False
        )
    # Theme-baked axis format defaults (e.g. axis_quantitative.labels.format
    # == "number") are never *authored* format strings, so the
    # per-chart walk below never reaches them -- but `style.formats: null`
    # is valid, compile()-accepted authoring that drops an inherited alias,
    # and it leaves that default unresolvable. Both facts
    # (chart_style_context.<slot>.labels.format and the alias table) are
    # already in hand here, so the guarantee belongs at this boundary, not
    # as a defensive re-check downstream in render (core/AGENTS.md's
    # normalizer-trusts-downstream rule).
    charts_style = board.chart_style_context
    for slot_name, axis_style in (
        ("axis", charts_style.axis),
        ("axis_x", charts_style.axis_x),
        ("axis_y", charts_style.axis_y),
        ("axis_quantitative", charts_style.axis_quantitative),
    ):
        label_format = axis_style.labels.format
        if label_format is not None:
            # Axis label format slots are Vega-painted — native formatters invalid.
            _validate_spec(
                label_format,
                formats,
                f"style.charts.{slot_name}.labels.format",
                True,
                vega_painted=True,
            )
    # Board-level `style:` is the same authored surface as chart-local `style:`
    # and reaches the same consumer, so it needs the same check. Walk the
    # authored patch, not resolved_style -- the latter carries theme content
    # this pass has no business rejecting.
    if board.authored_style is not None:
        for field_path, spec, timed, is_vega, kind in _iter_format_slots(
            board.authored_style, "style", False
        ):
            _validate_spec(
                spec, formats, field_path, timed, vega_painted=is_vega, kind=kind
            )
    for chart_id, chart in board.charts.items():
        if id(chart) in validated:
            continue
        validated.add(id(chart))
        for field_path, spec, timed, is_vega, kind in _iter_format_slots(
            chart, f"charts.{chart_id}", False
        ):
            _validate_spec(
                spec, formats, field_path, timed, vega_painted=is_vega, kind=kind
            )
