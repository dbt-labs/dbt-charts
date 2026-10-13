"""Compile-time validation of authored format-string slots.

Authored format strings are checked against three layers, in order: the
predefined engine vocabulary, the theme/board alias table, then
``d3_format.parse``. A spec that survives none of the three — a typo like
``percent_1`` — raises ``ERR-FORMAT-INVALID`` here, at compile, instead of
surfacing as ``ERR-INTERNAL`` deep inside rasterization (see
``core/compile/format.py``'s ``resolve_format`` docstring for the three-way
contract this validator runs ahead of).

Coverage is the authored surface: board-level ``style:`` and every chart,
recursively, plus the alias table's own targets (validated in
``_validate_board``). Slots are recognized by the field name ``format`` rather
than by type annotation; every current format field is spelled that way, so the
coverage is complete.

Time-format slots (axis ticks, table columns — anywhere a date-typed column
value might render) additionally accept a strftime-style spec (``%b %Y``):
an author may write the strftime form directly rather than through a predefined
alias (``date_short``).

A redefined engine name (``style.formats.percent``) keeps its kind: a number name
cannot be redefined as a date spec, and vice versa (``ERR-FORMAT-KIND-MISMATCH``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from pydantic import BaseModel

from d3_format import parse as _d3_parse
from d3_format.errors import D3FormatError
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import (
    grain_is_redefined,
    missing_format_columns,
    resolve_format_parts,
)
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.primitives import (
    FormatAliases,
    FormatConfig,
    FormatTable,
)
from dbt_charts.core.compile.models.query.normalized import Query, ValuesQuery
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_FORMAT_AFFIX_TIME_UNSUPPORTED,
    ERR_FORMAT_ALIAS_CYCLE,
    ERR_FORMAT_GRAIN_NAME_NOT_REDEFINED,
    ERR_FORMAT_INVALID,
    ERR_FORMAT_KIND_MISMATCH,
    ERR_FORMAT_NATIVE_IN_VEGA_SLOT,
    ERR_FORMAT_SIGN_BEFORE_ANCHORED_PREFIX,
    ERR_FORMAT_TEXT_IN_VEGA_SLOT,
    ERR_FORMAT_TEXT_REDEFINED,
    ERR_FORMAT_TIME_DIRECTIVE_UNSUPPORTED,
    ERR_QUERY_FORMAT_SPEC,
    ERR_QUERY_FORMAT_UNKNOWN,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_FORMAT_COLUMN_MISSING
from dbt_charts.core.text.format_d3 import (
    PYTHON_STRFTIME_DIRECTIVES,
    VEGA_SAFE_TIME_DIRECTIVES,
    find_unsupported_directive,
    is_time_format,
    unsupported_time_directives,
)
from dbt_charts.core.text.predefined_formats import (
    ALL_PREDEFINED_NAMES,
    GRAIN_NAMES,
    PREDEFINED_NUMBER_NAMES,
    PREDEFINED_TEXT_NAMES,
    PREDEFINED_TIME_NAMES,
    PYTHON_ONLY_NATIVE_NAMES,
)

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
        "timestamp",
    }
)

# Axis-painting field names: the only slots rejecting an affix on a time spec.
# Table columns and KPIs paint dates themselves, so they keep compiling.
_AXIS_FIELDS = frozenset(
    {"axis", "axis_x", "axis_y", "axis_band", "axis_quantitative", "mirror"}
)

# What else is legal when a redefinition has the wrong kind, per kind.
_KIND_ESCAPE_HATCHES: dict[str, str] = {
    "number": (
        "A raw d3 number spec (e.g. ',.0f') or a `style.formats` alias of your "
        "own works here too."
    ),
    "time": (
        "A strftime spec (e.g. '%b %Y') or a `style.formats` alias of your own "
        "works here too."
    ),
}

# Field names whose child format slots are rendered by Vega (not Python).
# PREDEFINED_NATIVE members bypass d3 and have no Vega equivalent; they are
# rejected here so the author gets a compile error rather than a Vega crash.
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


def _iter_format_slots(
    node: object,  # type-state: object_annotation — recursive tree walk over a BaseModel/dict/list/tuple/leaf mix; each isinstance branch below narrows it
    path: str,
    time_capable: bool,
    vega_painted: bool = False,
    axis_painted: bool = False,
) -> Iterator[tuple[str, str | FormatConfig, bool, bool, bool]]:
    """Yield (field_path, spec, time_capable, vega_painted, reject_time_affix)
    per format slot.

    ``vega_painted`` gates ERR-FORMAT-NATIVE-IN-VEGA-SLOT; ``reject_time_affix``
    marks an axis slot, where an affix beside a date spec is rejected.
    """
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            if name == "query":  # SQL text and source/cache config, no format slots
                continue
            value = getattr(node, name)
            if value is None:
                continue
            child_time = time_capable or name in _TIME_CAPABLE_FIELDS
            child_vega = vega_painted or name in _VEGA_PAINTED_PARENTS
            child_axis = axis_painted or name in _AXIS_FIELDS
            child = f"{path}.{name}"
            if name == "format" and isinstance(value, (str, FormatConfig)):
                yield (child, value, child_time, child_vega, child_axis)
            else:
                yield from _iter_format_slots(
                    value, child, child_time, child_vega, child_axis
                )
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_format_slots(
                value, f"{path}.{key}", time_capable, vega_painted, axis_painted
            )
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _iter_format_slots(
                value, f"{path}.{index}", time_capable, vega_painted, axis_painted
            )


def _check_time_affix_conflict(
    value: str | FormatConfig, field_path: str, spec: str
) -> None:
    """Raise when an authored affix/notation rides a time-shaped spec.

    No temporal axis path reads an affix, so it would be dropped silently.
    ``spec`` is the authored key, for the message.
    """
    if isinstance(value, FormatConfig) and (
        value.prefix or value.suffix or value.notation is not None
    ):
        raise CompilationError.from_code(
            ERR_FORMAT_AFFIX_TIME_UNSUPPORTED,
            field_path=field_path,
            spec=spec,
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
    formats: FormatTable | None,
    field_path: str,
    time_capable: bool,
    vega_painted: bool = False,
    *,
    reject_time_affix: bool,
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

    ``vega_painted=True`` is passed for slots rendered by Vega (axis labels,
    mark value labels, ``support_table``).
    PREDEFINED_NATIVE members bypass d3 entirely and have no Vega equivalent;
    they are rejected here so the author gets ERR-FORMAT-NATIVE-IN-VEGA-SLOT
    rather than a Vega runtime crash.
    """
    if (
        isinstance(value, FormatConfig)
        and value.prefix
        and value.repeat == "anchor"
        and value.sign_placement == "before_prefix"
    ):
        raise CompilationError.from_code(
            ERR_FORMAT_SIGN_BEFORE_ANCHORED_PREFIX, field_path=field_path
        )
    spec = value.spec if isinstance(value, FormatConfig) else value
    if not spec:
        return
    aliases = {} if formats is None else formats.aliases
    if spec in ALL_PREDEFINED_NAMES:
        if spec in GRAIN_NAMES and not grain_is_redefined(spec, aliases):
            raise CompilationError.from_code(
                ERR_FORMAT_GRAIN_NAME_NOT_REDEFINED, spec=spec, field_path=field_path
            )
        if vega_painted and spec in PYTHON_ONLY_NATIVE_NAMES:
            raise CompilationError.from_code(
                ERR_FORMAT_NATIVE_IN_VEGA_SLOT,
                spec=spec,
                field_path=field_path,
                available=sorted(
                    ALL_PREDEFINED_NAMES
                    - PYTHON_ONLY_NATIVE_NAMES
                    - GRAIN_NAMES
                    - PREDEFINED_TEXT_NAMES
                ),
            )
        if vega_painted and spec in PREDEFINED_TEXT_NAMES:
            raise CompilationError.from_code(
                ERR_FORMAT_TEXT_IN_VEGA_SLOT, spec=spec, field_path=field_path
            )
        # A predefined TIME name has no PREDEFINED_SPECS entry: route it to the
        # time-shaped check.
        if reject_time_affix and spec in PREDEFINED_TIME_NAMES:
            _check_time_affix_conflict(value, field_path, spec)
        return
    if spec in aliases:
        aliased = aliases[spec]
        aliased_spec = aliased.spec if isinstance(aliased, FormatConfig) else aliased
        if aliased_spec in ALL_PREDEFINED_NAMES:
            parts = resolve_format_parts(value, formats)
            _validate_spec(
                FormatConfig(
                    spec=aliased_spec,
                    prefix=parts.prefix or None,
                    suffix=parts.suffix or None,
                    notation=parts.notation,
                ),
                formats,
                field_path,
                time_capable,
                vega_painted=vega_painted,
                reject_time_affix=reject_time_affix,
            )
            return
        # The affix the painter sees: the authored one, else the alias's
        # (resolve_format_parts encodes that precedence).
        _resolved_parts = resolve_format_parts(value, formats)
        has_resolved_affix = _resolved_parts.has_affix
        effective_value: str | FormatConfig = value
        if has_resolved_affix:
            effective_value = FormatConfig(
                prefix=_resolved_parts.prefix or None,
                suffix=_resolved_parts.suffix or None,
                notation=_resolved_parts.notation,
            )
        if aliased_spec and reject_time_affix and is_time_format(aliased_spec):
            _check_time_affix_conflict(effective_value, field_path, spec)
        # A Vega-painted time slot reached through an alias gets the same
        # directive gate as the directive written inline.
        if (
            time_capable
            and vega_painted
            and aliased_spec
            and is_time_format(aliased_spec)
        ):
            _raise_if_unsupported_time_directives(aliased_spec, field_path)
        return
    if time_capable and is_time_format(spec):
        # A directive portable_strftime doesn't paint must fail here, at compile -- not reach render, where it's either
        # an uncoded ValueError (ERR-INTERNAL, Python-painted slots) or a
        # literal, unrendered letter (Vega-painted slots).
        bad_directive = find_unsupported_directive(spec)
        if bad_directive is not None:
            raise CompilationError.from_code(
                ERR_FORMAT_INVALID,
                spec=spec,
                field_path=field_path,
                kind_noun="date/time",
                explanation=(
                    f"%{bad_directive} is not a strftime directive the engine "
                    "implements. Accepted directives: "
                    f"{', '.join('%' + d for d in sorted(PYTHON_STRFTIME_DIRECTIVES))}."
                ),
            )
        if reject_time_affix:
            _check_time_affix_conflict(value, field_path, spec)
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
        raise CompilationError.from_code(
            ERR_FORMAT_INVALID,
            spec=spec,
            field_path=field_path,
            kind_noun="number",
            explanation=(
                "it is not an engine-predefined format name, not a key in "
                f"`style.formats`, and is not a valid d3-format spec ({e.reason} "
                f"at position {e.position})."
            ),
            available=sorted({*aliases, *ALL_PREDEFINED_NAMES}),
        ) from e


def _check_redefinition_kind(name: str, target: str | FormatConfig) -> None:
    """Raise when ``style.formats.<engine name>`` is a spec of the other kind."""
    spec = target.spec if isinstance(target, FormatConfig) else target
    if name not in ALL_PREDEFINED_NAMES:
        return
    if name in PREDEFINED_TEXT_NAMES:
        raise CompilationError.from_code(
            ERR_FORMAT_TEXT_REDEFINED, field_path=f"style.formats.{name}", name=name
        )
    if not spec:
        raise CompilationError.from_code(
            ERR_FORMAT_INVALID,
            spec=name,
            field_path=f"style.formats.{name}",
            kind_noun="date/time" if name in PREDEFINED_TIME_NAMES else "number",
            explanation=(
                "redefining an engine name needs a `spec`; an affix alone would "
                "leave every slot that reads it with no spec."
            ),
            available=[],
        )
    kind = "time" if name in PREDEFINED_TIME_NAMES else "number"
    names = PREDEFINED_TIME_NAMES if kind == "time" else PREDEFINED_NUMBER_NAMES
    spec_is_time = spec in PREDEFINED_TIME_NAMES or (
        spec not in ALL_PREDEFINED_NAMES and is_time_format(spec)
    )
    if spec_is_time != (kind == "time"):
        raise CompilationError.from_code(
            ERR_FORMAT_KIND_MISMATCH,
            spec=spec,
            field_path=f"style.formats.{name}",
            kind=kind,
            available=sorted(names - PYTHON_ONLY_NATIVE_NAMES - GRAIN_NAMES),
            escape_hatch=_KIND_ESCAPE_HATCHES[kind],
        )


def _check_alias_cycle(name: str, aliases: FormatAliases) -> None:
    """Raise when ``name`` leads into a circle of aliases."""
    chain = [name]
    spec = name
    while spec in aliases:
        target = aliases[spec]
        following = target.spec if isinstance(target, FormatConfig) else target
        if following is None or following == spec:
            return
        if following in chain:
            raise CompilationError.from_code(
                ERR_FORMAT_ALIAS_CYCLE,
                field_path=f"style.formats.{name}",
                name=name,
                chain=" -> ".join([*chain, following]),
            )
        chain.append(following)
        spec = following


def _validate_query_formats(
    query_name: str, query: Query, formats: dict[str, str | FormatConfig]
) -> None:
    """Check a query's ``formats:`` values are names this board has, and, for a
    ``values`` query whose columns are literal, that every key is a column."""
    if not query.formats:
        return
    for column, name in query.formats.items():
        if name in GRAIN_NAMES and not grain_is_redefined(name, formats):
            raise CompilationError.from_code(
                ERR_FORMAT_GRAIN_NAME_NOT_REDEFINED,
                spec=name,
                field_path=f"queries.{query_name}.formats.{column}",
            )
        if name in ALL_PREDEFINED_NAMES or name in formats:
            continue
        try:
            _d3_parse(name)
            is_spec = True
        except D3FormatError:
            is_spec = is_time_format(name)
        if is_spec:
            raise CompilationError.from_code(
                ERR_QUERY_FORMAT_SPEC, query=query_name, column=column, spec=name
            )
        raise CompilationError.from_code(
            ERR_QUERY_FORMAT_UNKNOWN,
            query=query_name,
            column=column,
            spec=name,
            available=sorted({*formats, *ALL_PREDEFINED_NAMES - GRAIN_NAMES}),
        )
    if isinstance(query, ValuesQuery):
        returned = set(query.columns) if query.columns else set().union(*query.rows)
        if returned:
            for column in missing_format_columns(query.formats, returned):
                raise CompilationError.from_code(
                    ERR_QUERY_FORMAT_COLUMN_MISSING,
                    query=query_name,
                    column=column,
                    columns=", ".join(sorted(returned)),
                )


def _boards(board: Board) -> Iterator[Board]:
    yield board
    for item in board.layout.items:
        if item.board is not None:
            yield from _boards(item.board)


def _spec_name(spec: str | FormatConfig) -> str | None:
    return spec.spec if isinstance(spec, FormatConfig) else spec


def format_names_used(board: Board) -> set[str]:
    """Format names read by a slot, a query column, or a text ``format('name')``
    call, on this board or any nested board."""
    used: set[str] = set()
    text_call = re.compile(r"""\bformat\(\s*(['"])(.+?)\1""")
    for each in _boards(board):
        roots = (*each.charts.values(), each.authored_style)
        for root in roots:
            if root is not None:
                names = (
                    _spec_name(spec)
                    for _, spec, *_ in _iter_format_slots(root, "", False)
                )
                used.update(name for name in names if name is not None)
        queries = [*each.queries.values()]
        queries += [
            chart.query
            for chart in each.charts.values()
            if chart.type != "callout" and chart.query_is_inline and chart.query
        ]
        for query in queries:
            if query.formats is not None:
                used.update(query.formats.values())
    texts = (
        text for text in _strings(board.model_dump(mode="python")) if "format(" in text
    )
    used.update(match[1] for text in texts for match in text_call.findall(text))
    return used - {""}


def _strings(
    node: object,  # type-state: object_annotation — recursive walk over a model_dump tree of dict/list/leaf values
) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _strings(value)


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
    aliases = {} if formats is None else formats.aliases
    # An alias is only useful if its target resolves; checking the keys alone
    # would let `formats: {mine: bogus}` reach the render-time raise. A preset
    # target is judged per slot, where the alias is used. An engine name
    # redefined here must keep its kind: every slot reading it expects it.
    for alias in aliases:
        _check_alias_cycle(alias, aliases)
    for query_name, query in board.queries.items():
        _validate_query_formats(query_name, query, aliases)
    for alias, target in aliases.items():
        _check_redefinition_kind(alias, target)
        target_spec = target.spec if isinstance(target, FormatConfig) else target
        _validate_spec(
            target,
            # Only a grain name resolves through the table; any other alias
            # target must itself be an engine name or a d3 spec.
            formats if target_spec in GRAIN_NAMES else None,
            f"style.formats.{alias}",
            True,
            # Axes read an engine name by default, and Vega paints them.
            vega_painted=alias in ALL_PREDEFINED_NAMES,
            reject_time_affix=True,
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
                reject_time_affix=True,
            )
    # Board-level `style:` is the same authored surface as chart-local `style:`
    # and reaches the same consumer, so it needs the same check. Walk the
    # authored patch, not resolved_style -- the latter carries theme content
    # this pass has no business rejecting.
    if board.authored_style is not None:
        for field_path, spec, timed, is_vega, reject_affix in _iter_format_slots(
            board.authored_style, "style", False
        ):
            _validate_spec(
                spec,
                formats,
                field_path,
                timed,
                vega_painted=is_vega,
                reject_time_affix=reject_affix,
            )
    for chart_id, chart in board.charts.items():
        if id(chart) in validated:
            continue
        validated.add(id(chart))
        if (
            chart.type != "callout"
            and chart.query_is_inline
            and chart.query is not None
        ):
            _validate_query_formats(chart_id, chart.query, aliases)
        for field_path, spec, timed, is_vega, reject_affix in _iter_format_slots(
            chart, f"charts.{chart_id}", False
        ):
            _validate_spec(
                spec,
                formats,
                field_path,
                timed,
                vega_painted=is_vega,
                reject_time_affix=reject_affix,
            )
