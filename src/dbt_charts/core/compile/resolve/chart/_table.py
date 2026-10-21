"""Table resolution shared by authored tables and pie attachments."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from types import MappingProxyType
from typing import Any

from d3_format import format as _d3_fmt
from dbt_charts.core.colors import sanitize_color
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import (
    column_format,
    decimal_pad_table_for,
    declared_format,
    inferred_number_format,
    sub_unit_spec_for_value,
)
from dbt_charts.core.compile.format_value import (
    raise_if_format_cannot_print_number,
    text_format_link,
)
from dbt_charts.core.compile.models.chart.normalized import (
    TableChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedTableChart,
)
from dbt_charts.core.compile.models.primitives import (
    AuthoredFormat,
    FormatTable,
    LinkOpen,
    ResolvedFormat,
    split_link,
)
from dbt_charts.core.compile.models.style.authored import (
    TableColumnConfig,
    TableColumnDefaultsConfig,
    fill_table_column_defaults,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedColumnScaleConfig,
    ResolvedColumnSharedScale,
    ResolvedTableColumnConfig,
    ResolvedTableStyle,
)
from dbt_charts.core.compile.models.style.resolved.table import (
    ResolvedColumnScaleTargetConfig,
    ResolvedNamedPaletteColumnScaleTargetConfig,
)
from dbt_charts.core.compile.resolve.chart._channels import _channels_for
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    _base_kwargs,
    _shared_kwargs,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_requested_alias_palette,
    _with_color_tokens,
)
from dbt_charts.core.compile.resolve.chart.link_keys import plan_link_keys
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.inherit_resolver import merge_unset
from dbt_charts.core.compile.resolve.style.palette import (
    palette as resolve_named_palette,
)
from dbt_charts.core.compile.resolve.style.typography import (
    concrete_title_style,
    resolve_title_font,
)
from dbt_charts.core.compile.template.display_text import ChartDisplayText
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_FORMAT_SIGN_PLACEMENT_TABLE_UNSUPPORTED,
)
from dbt_charts.core.font_measure import compose_decimal_units
from dbt_charts.core.fonts import (
    DBT_SANS_TABULAR_FONT_FAMILY,
    SOURCE_SERIF_4_FONT_FAMILY,
)
from dbt_charts.core.text.format_d3 import d3_format, is_d3_si_spec
from dbt_charts.core.text.numeral_scale import (
    UNCOMPACTED_COLUMN_SPEC,
    build_decimal_pad_table,
    column_digit_format,
    column_shares_one_printed_unit,
    fractional_digit_count,
    shared_scale_for_column,
    tier_distance,
)
from dbt_charts.core.text.predefined_formats import PREDEFINED_TEXT_NAMES
from dbt_charts.core.utils import coerce_numeric_cell

__all__ = [
    "_EMPTY_TABLE_COLUMN_LINKS",
    "_resolve_table",
    "infer_pivot_measure_names",
]


_EMPTY_TABLE_COLUMN_LINKS: Mapping[str, str] = MappingProxyType({})


def infer_pivot_measure_names(
    all_keys: Sequence[str],
    rows: Sequence[str],
    columns: Sequence[str],
    values: Sequence[str] | None,
    row_role_spec: str | None,
) -> list[str]:
    """Resolve the effective pivot measure list: explicit, or every observed
    key not claimed by ``rows``/``columns``/the row-role marker.

    Single owner of this inference rule — render's ``pivot_table_data`` (via
    ``_validate_pivot_fields``) and resolve's table-column materialization
    (``_materialize_table_columns``) both call this, so a pivoting chart's
    effective measure set can never diverge between the two: a measure
    column never gets exposed as an ordinary inferred column at resolve just
    because ``values:`` was omitted from the authored board.
    """
    claimed = set(rows) | set(columns) | ({row_role_spec} if row_role_spec else set())
    return (
        list(values)
        if values is not None
        else [k for k in all_keys if k not in claimed]
    )


def _style_input_columns(
    explicit: Mapping[str, TableColumnConfig] | None,
    defaults: TableColumnDefaultsConfig | None,
    query_keys: Collection[str],
) -> frozenset[str]:
    """Query columns consumed as per-row style inputs.

    ``background``, ``font.color`` and ``font.weight`` resolve
    column-ID-first (a value matching a query column name reads that row's
    value from the named column). A column consumed that way is paint, not
    display data, so it is auto-hidden — the docs' helper-column cell-styling
    pattern relies on this, with no authored ``visible:``.
    """
    sources: list[TableColumnConfig | TableColumnDefaultsConfig] = list(
        explicit.values() if explicit else ()
    )
    if defaults is not None:
        sources.append(defaults)
    specs: list[str | float | None] = []
    for cfg in sources:
        specs.append(cfg.background)
        if cfg.font is not None:
            specs.append(cfg.font.color)
            specs.append(cfg.font.weight)
    return frozenset(s for s in specs if isinstance(s, str) and s in query_keys)


def _materialize_table_columns(
    explicit: Mapping[str, TableColumnConfig] | None,
    defaults: TableColumnDefaultsConfig | None,
    data: Sequence[Mapping[str, Any]],
    row_role: str | None,
    pivot_measure_names: frozenset[str],
    table_column_links: Mapping[str, str],
    table_column_rows: Sequence[Mapping[str, str]],
) -> dict[str, TableColumnConfig] | None:
    """Build the final per-column config mapping once, from trusted inputs.

    Precedence per field: explicit column value > column_defaults > (for
    ``link`` only) runtime FK link / (for ``align`` only) a value-classified
    verdict, via ``fill_table_column_defaults``. A single identity-key column
    (per ``plan_link_keys``) is protected from FK-link assignment.

    Keys: every query-inferred column — excluding the row-role column and,
    for a pivoting chart, the measure fields named in ``pivot_measure_names``
    — plus any explicitly authored keys beyond those (a pivoting chart
    authors columns keyed by measure name or bare pivoted value, and an
    empty result set still needs the authored entries for its empty-state
    headers). ``style.columns`` is styling-only: authoring a subset styles
    those columns and never narrows this mapping. Hiding is the per-column
    ``visible: false`` — such entries stay present here carrying
    ``visible=False`` (render filters at display time), and a column
    consumed as a style input (see ``_style_input_columns``) is materialized
    with ``visible=False`` derived automatically.

    A pivoted measure's real leaf-column key space (leaf keys, or bare
    pivoted values for a single-dim single-measure pivot) only exists after
    render's ``pivot_table_data`` transform runs — it is not a function of
    the pre-pivot rows resolve holds, and reconstructing it here would
    duplicate that transform's inference logic. So measure fields are
    excluded here; render applies ``column_defaults`` (kept on
    ``ResolvedTableChart`` for exactly this) to its own synthesized pivot-leaf
    columns instead. Row/pivot-dimension fields that survive pivoting
    unchanged (e.g. a `rows:` column) are NOT excluded — they get their
    defaults/FK-link here same as any other inferred column.

    Returns ``None`` when there is nothing to materialize: no explicit
    columns, no defaults, no runtime links, AND no data to classify a
    column's alignment from — render then falls back to plain query
    columns. Whenever there IS data, every inferred column still gets
    materialized even with no explicit/default/link facts, purely so
    ``fill_table_column_defaults`` can classify ``align`` from its values
    (see its docstring) — every other field on such a column stays unset,
    identical to the pre-materialization render fallback. Returns ``{}`` in
    the rarer case a real materialization pass still ends up with zero keys
    (e.g. a degenerate pivot where every query column classifies as a
    measure) — render treats both the same way (``columns_promoted or {}``),
    so the ``None``/``{}`` distinction is not load-bearing.
    """
    if not explicit and defaults is None and not table_column_links and not data:
        return None

    keys = [
        key
        for key in (data[0] if data else {})
        if key != row_role and key not in pivot_measure_names
    ]
    if explicit:
        present = set(keys)
        keys += [key for key in explicit if key not in present]

    style_inputs = _style_input_columns(explicit, defaults, data[0] if data else ())

    protected: frozenset[str] = frozenset()
    if table_column_links:
        identity_keys = frozenset(
            str(key["name"]) for key in plan_link_keys(table_column_rows)
        )
        protected = identity_keys if len(identity_keys) == 1 else frozenset()

    result: dict[str, TableColumnConfig] = {}
    for key in keys:
        source = explicit.get(key) if explicit else None
        link_override = table_column_links.get(key) if key not in protected else None
        # Building the values list is an O(rows) allocation; skip it once
        # align is already going to come from source or defaults (the same
        # precedence fill_table_column_defaults applies) — classification
        # would just be computed and discarded.
        align_already_set = (source is not None and source.align is not None) or (
            defaults is not None and defaults.align is not None
        )
        result[key] = fill_table_column_defaults(
            source,
            defaults,
            link_override=link_override,
            values=() if align_already_set else [row.get(key) for row in data],
            # An explicit entry is a display signal that beats derivation:
            # auto-hide applies only to columns the author never mentioned.
            auto_hidden=source is None and key in style_inputs,
        )
    return result


def _per_cell_texts(
    values: list[float],
    si_check_fmt: str,
    raw: str | None,
    formats: FormatTable | None,
) -> list[tuple[float, str]]:
    """Each value paired with the text its own per-cell SI spec prints."""
    return [
        (
            v,
            d3_format(sub_unit_spec_for_value(raw, si_check_fmt, v, formats))(v),
        )
        for v in values
    ]


def _unscaled_decimal_pad_table(
    values: list[float],
    si_check_fmt: str,
    font_family: str,
    raw: str | None,
    formats: FormatTable | None,
) -> tuple[str, ...]:
    """Decimal pad table for a column with no shared SI tier to divide by --
    every cell keeps its own per-cell significant-figures spec. Self-gates on
    ``column_shares_one_printed_unit``: returns ``()`` when the column's
    cells don't all print the same SI suffix (a genuine tier mix, e.g.
    12100/900), since a bare whole number and a "k"-suffixed one share no
    decimal position to pad to.

    Compile-side twin of ``support_table_attachment.py``'s
    ``_unscaled_decimal_pad``: same string-level questions (do these cells
    share a printed unit; how many fractional digits does the deepest one
    print), but formats with ``d3_format`` (the board locale) rather than render's
    ``format_value``, since ``compile`` cannot import ``render``
    (``compile ↛ render``). Each value is measured through
    ``sub_unit_spec_for_value``, not the bare ``si_check_fmt`` -- a sub-1 cell
    prints deeper than the rest of the column.

    Returns ``()`` when every cell already prints the same number of
    fractional digits (nothing to align).
    """
    texts = _per_cell_texts(values, si_check_fmt, raw, formats)
    if not column_shares_one_printed_unit(texts):
        return ()
    fracs = {fractional_digit_count(t) for _, t in texts}
    if len(fracs) <= 1:
        return ()
    digit_unit, dot_unit = compose_decimal_units(font_family)
    return build_decimal_pad_table(max(fracs), digit_unit, dot_unit)


# The engine fills a `{{ column | urlencode }}` template per row, and reads a
# link that names a data column verbatim as that column's value.
_EMAIL_LINK = "mailto:{{{{ {column} | urlencode }}}}"


def _text_link_field(
    name: str,
    col: TableColumnConfig,
    effective: ResolvedFormat,
    rows: list[dict[str, Any]],  # type-state: explicit_any — rows are raw query output
    filter_variables: frozenset[str],
) -> dict[str, str]:
    """The ``link`` an ``email`` / ``url`` column implies, or nothing.

    Below every other cell link: a link the column already has (authored, or the
    FK drill) and a filter link win. A number under a text format is an error
    whichever link wins; a ``url`` value must be absolute only when it will link.
    """
    fmt = effective.raw
    if fmt not in PREDEFINED_TEXT_NAMES:
        return {}
    values = [row.get(name) for row in rows]
    if any(coerce_numeric_cell(value) is not None for value in values):
        raise_if_format_cannot_print_number(fmt)
    if col.link or name in filter_variables:
        return {}
    for value in values:
        text_format_link(fmt, value, f"column {name!r}")
    return {"link": _EMAIL_LINK.format(column=name) if fmt == "email" else name}


def _with_resolved_scale_stops(
    columns: dict[str, TableColumnConfig] | None,
    text_color: str | None,
    formats: FormatTable | None,
    font_family: str,
    rows: list[dict[str, Any]],  # type-state: explicit_any — rows are raw query output
    allow_shared_scale: bool,
    own_formats: Mapping[str, AuthoredFormat | None],
    defaults_format: AuthoredFormat | None,
    declared: Mapping[str, str] | None,
    filter_variables: frozenset[str],
    link_open: LinkOpen,
) -> dict[str, ResolvedTableColumnConfig] | None:
    """Construct ResolvedTableColumnConfig entries with baked scale stops.

    Every string palette on a table column is resolved to stops via the
    WCAG-safe `surface="table"` carve — unlike bake_scale_target_stops, this
    path has no VEGA_SCHEME_NAMES carve-out, so every string palette bakes as
    ResolvedNamedPaletteColumnScaleTargetConfig. This is correct: tables never
    forward a VL scheme name; table palettes are always dbt-charts named ones.

    Inline stop lists leave background/color as plain ResolvedColumnScaleTargetConfig
    (no resolved_stops). `text_color` is only required when a column actually
    authors a named-string scale palette.

    ``font_family`` is the numeric cell font (dbt Sans Tabular, or Source Serif
    for Source Serif table themes) -- not the body font. ``rows`` is used to
    gate the pad-table bake: a uniform-depth column gets ``()`` even when the
    spec is trim-enabled, matching the axis-side gate.

    ``allow_shared_scale`` gates the shared-magnitude bake specifically
    (decimal_pad_table still bakes either way): a chart-attached legend
    "table" (pie/donut) shares this function's column-resolution machinery
    with real authored tables (this module's own docstring), but a legend
    entry is read beside its own slice, not scanned down a column -- an
    ANCHOR-mode magnitude declared once on the top entry and stripped from
    the rest reads as data loss there, not alignment. Callers synthesizing a
    legend/attachment table pass ``False``; real authored tables keep the
    default.
    """
    if columns is None:
        return None

    result: dict[str, ResolvedTableColumnConfig] = {}
    for name, col in columns.items():
        coerced_values = _coerced_column_values(rows, name) if rows else []
        own = own_formats.get(name)
        inferred = inferred_number_format(name, (row.get(name) for row in rows))
        _effective = column_format(
            "number" if coerced_values else "time",
            formats,
            own,
            board=defaults_format,
            query_formats=declared,
            columns=name,
            text=True,
            inferred=inferred,
        )
        text_link = _text_link_field(name, col, _effective, rows, filter_variables)
        has_format = inferred is not None or any(
            f is not None
            for f in (own, defaults_format, declared_format(declared, name))
        )
        _col_parts = _effective if has_format else None
        resolved_fmt = _effective.spec if has_format else ""

        # A column with no explicit format: still falls back to the engine default
        # (an SI spec) at render time (format_kpi_parts's default_number=True)
        # -- mirror that fallback here so an unformatted numeric column is
        # eligible for the same shared-scale bake as an explicitly `~s`
        # column, matching the Problem this task targets ("the engine
        # default, or any inline ~s spec").
        si_check_fmt, raw_format_name = _effective.spec, _effective.raw

        shared_scale: ResolvedColumnSharedScale | None = None
        if allow_shared_scale and coerced_values and is_d3_si_spec(si_check_fmt):
            raw_scale = shared_scale_for_column(coerced_values)
            if raw_scale is not None:
                magnitude = 10.0**raw_scale.exponent
                nonzero = [v for v in coerced_values if v != 0]
                finest = min(nonzero, key=abs)
                # Precision is derived from the finest (smallest, fewest
                # integer digits) value's own decimal exponent -- that value
                # needs the MOST decimals to hit the format's significant-
                # figure count, so pinning precision to it guarantees no row
                # undershoots its own sig figs (a row with more integer
                # digits then shows more precision than it strictly needs,
                # harmless). But that exponent is unbounded below zero: a
                # value with no tier at all (nowhere near the column's
                # majority) would force every other row to inherit far more
                # precision than the format's own sig-fig count ever asked
                # for (three sig figs asked, seven delivered, because one
                # stray row sits three tiers below the rest). A value one
                # tier below the majority (e.g. thousands inside a millions
                # column) legitimately needs a few extra decimals and stays
                # reasonable; anything further -- or a value with no SI tier
                # at all -- refuses the whole bake instead, same as a column
                # with no majority tier in the first place.
                distance = tier_distance(finest, raw_scale.exponent)
                if distance is None or distance > 1:
                    shared_scale = None
                else:
                    finest_scaled = abs(finest) / magnitude
                    _, digit_spec = column_digit_format(si_check_fmt, finest_scaled)
                    # Even within that bound, a low-enough authored
                    # significant-figure count (e.g. an explicit `.0~s`)
                    # can still leave zero decimals to lean on, rendering a
                    # sub-tier row with no nonzero digit -- indistinguishable
                    # from a real zero. Checked on digit content, not a
                    # fixed glyph set: a signed spec (`+.0~s`) still needs
                    # refusing on "+0", and this reads the same regardless
                    # of which sign/symbol/separator flags the author's spec
                    # carries.
                    if any(
                        not any(
                            c.isdigit() and c != "0"
                            for c in _d3_fmt(digit_spec, v / magnitude)
                        )
                        for v in nonzero
                    ):
                        shared_scale = None
                    else:
                        shared_scale = ResolvedColumnSharedScale(
                            exponent=raw_scale.exponent,
                            mode=raw_scale.mode,
                            digit_spec=digit_spec,
                        )

        baked_exact_digits = False
        # The vote declined to compact a column whose spec is the engine's
        # default (no format, or an affix with no spec) AND its rows would
        # print mixed SI units: print every row as exact grouped digits
        # rather than letting each row pick its own tier (5 / 1.2 K / 77). A
        # column whose rows all print one unit (42K / 81K) keeps compacting.
        # A board that redefined `number` as SI keeps it.
        if (
            allow_shared_scale
            and shared_scale is None
            and coerced_values
            and is_d3_si_spec(si_check_fmt)
            and not column_shares_one_printed_unit(
                _per_cell_texts(coerced_values, si_check_fmt, raw_format_name, formats)
            )
        ):
            exact = column_format(
                "number",
                formats,
                own,
                board=defaults_format,
                query_formats=declared,
                columns=name,
                text=True,
                engine_default=lambda: UNCOMPACTED_COLUMN_SPEC,
            )
            if not is_d3_si_spec(exact.spec):
                _col_parts = exact
                resolved_fmt = si_check_fmt = exact.spec
                baked_exact_digits = True

        # A shared-scale column's cells format through digit_spec (fixed-point,
        # trim-enabled), not the original SI spec -- so the mixed-depth gate
        # must also run against digit_spec and the scaled values, mirroring
        # ruler_digit_format's own decimal_pad_table bake for axis rulers.
        if shared_scale is not None:
            pad_fmt = shared_scale.digit_spec
            magnitude = 10.0**shared_scale.exponent
            pad_values = [v / magnitude for v in coerced_values]
        else:
            pad_fmt = resolved_fmt
            pad_values = coerced_values
        spec_table = decimal_pad_table_for(pad_fmt, font_family)
        if spec_table and pad_values:
            # Gate on actual fractional depths (mirror the axis-side gate).
            # decimal_pad_table_for already validated the spec; it is a
            # trim-enabled fixed-point spec whose precision > 0.
            actual_fracs = {
                len(_d3_fmt(pad_fmt, v).partition(".")[2]) for v in pad_values
            }
            pad_table: tuple[str, ...]
            if len(actual_fracs) <= 1:
                pad_table = ()
            elif shared_scale is not None or baked_exact_digits:
                # Rebuild capped at the observed max, not digit_spec's full
                # (possibly sig-figs-default-6) precision: a shared_scale
                # column with no explicit precision on its format can bake a
                # far-wider-than-ever-needed digit_spec (e.g. 5 decimals),
                # and every row's trimmed depth is already known here -- no
                # row can ever need more padding than the deepest one
                # actually observed. Scoped to the columns this bake formats
                # itself (shared_scale, exact digits): it knows its own spec
                # produced every cell it measured, so the observed depths
                # really do bound what the pad must cover. A plain
                # fixed-point column has no such guarantee, so its table
                # stays at the declared precision.
                pad_table = decimal_pad_table_for(
                    pad_fmt, font_family, max_precision=max(actual_fracs)
                )
            else:
                pad_table = spec_table
        else:
            pad_table = spec_table
        if (
            shared_scale is None
            and not pad_table
            and coerced_values
            and is_d3_si_spec(si_check_fmt)
        ):
            # decimal_pad_table_for refuses an SI spec (type "s") -- it only
            # builds a table from a declared fixed-point precision. A column
            # with no shared tier at all still needs decimal alignment, so
            # fall back to measuring each cell's own printed depth, mirroring
            # support_table_attachment.py's _unscaled_decimal_pad.
            # _unscaled_decimal_pad_table self-gates on
            # column_shares_one_printed_unit, refusing a genuine tier mix.
            pad_table = _unscaled_decimal_pad_table(
                coerced_values,
                si_check_fmt,
                font_family,
                raw_format_name,
                formats,
            )
        if col.scale is None:
            result[name] = ResolvedTableColumnConfig.model_validate(
                {
                    **col.model_dump(exclude_none=True),
                    **_split_column_links(col, link_open),
                    **({"format": _col_parts} if _col_parts is not None else {}),
                    **text_link,
                    "decimal_pad_table": pad_table,
                    "shared_scale": shared_scale,
                }
            )
            continue
        resolved_scale_fields: dict[str, Any] = {}
        for attr in ("background", "color"):
            target = getattr(col.scale, attr)
            if target is None:
                continue
            if isinstance(target.palette, str):
                if text_color is None:
                    raise ValueError(
                        "table.font.color unset — theme cascade must populate "
                        "it before a named scale palette can be resolved"
                    )
                stops = tuple(
                    resolve_named_palette(
                        target.palette, surface="table", text_color=text_color
                    )
                )
                resolved_scale_fields[attr] = (
                    ResolvedNamedPaletteColumnScaleTargetConfig.model_validate(
                        {
                            **target.model_dump(exclude_unset=True),
                            "resolved_stops": stops,
                        }
                    )
                )
            else:
                resolved_scale_fields[attr] = (
                    ResolvedColumnScaleTargetConfig.model_validate(
                        target.model_dump(exclude_unset=True)
                    )
                )
        scale_base = col.scale.model_dump(exclude_none=True)
        scale_base.update(
            {
                k: v.model_dump(exclude_unset=True)
                for k, v in resolved_scale_fields.items()
            }
        )
        resolved_scale = ResolvedColumnScaleConfig.model_validate(scale_base)
        col_base = col.model_dump(exclude_none=True)
        col_base.update(_split_column_links(col, link_open))
        col_base["scale"] = resolved_scale.model_dump(exclude_unset=True)
        if _col_parts is not None:
            col_base["format"] = _col_parts
        col_base.update(text_link)
        col_base["decimal_pad_table"] = pad_table
        col_base["shared_scale"] = shared_scale
        result[name] = ResolvedTableColumnConfig.model_validate(col_base)
    return result


def _split_column_links(
    col: TableColumnConfig, board_open: LinkOpen
) -> dict[str, str | None]:
    """A column's authored links as the resolved URL strings plus open modes."""
    link, link_open = split_link(col.link, board_open)
    header_link, header_link_open = split_link(col.header_link, board_open)
    return {
        "link": link,
        "link_open": link_open,
        "header_link": header_link,
        "header_link_open": header_link_open,
    }


def _coerced_column_values(
    rows: list[dict[str, Any]],  # type-state: explicit_any — rows are raw query output
    name: str,
) -> list[float]:
    """Coerce one column's raw row values to float, skipping non-numeric cells.

    Shared by the decimal-pad-table gate and the shared-scale bake so both
    read the exact same coercion. Delegates to ``coerce_numeric_cell``
    (``core/utils.py``) rather than a hand-rolled float() cast: it already
    handles ``decimal.Decimal`` (warehouse drivers return it for
    DECIMAL/NUMERIC columns, not float) and filters non-finite values
    (NaN/Infinity) to None -- a bare ``float(raw)`` cast would instead crash
    ``shared_scale_for_column``'s ``max(abs(v) for v in values)`` on a NaN row
    order-dependently.
    """
    return [v for row in rows if (v := coerce_numeric_cell(row.get(name))) is not None]


def _resolve_table(
    normalized: TableChart,
    data: list[dict[str, Any]],
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    table_column_links: Mapping[str, str],
    table_column_rows: Sequence[Mapping[str, str]],
    display_text: ChartDisplayText = None,
    allow_shared_scale: bool = True,
    *,
    filter_variables: frozenset[str],
    link_open: LinkOpen,
) -> ResolvedTableChart:
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    primary = _with_color_tokens(normalized.style, chart_style_context)
    table = merge_unset(chart_style_context.table, primary)
    channels = _channels_for(normalized, data)
    column_defaults_val = table.column_defaults
    # Table-specific promoted field, lifted to the top level here so the
    # renderer can read a typed, already-final field instead of reaching into
    # style.table or re-merging defaults/FK links itself.
    pivot_measure_names = (
        frozenset(
            infer_pivot_measure_names(
                list(data[0]) if data else [],
                normalized.rows or [],
                normalized.columns,
                normalized.values,
                table.row.role,
            )
        )
        if normalized.columns
        else frozenset()
    )
    # Cell body text sits on the scale fill, so the table carve must keep
    # fills that hold this color — light on dark themes, dark on light.
    # sanitize_color falls back to table.font.color for an invalid/empty
    # static override rather than feeding a non-hex string into the carve.
    _table_color_static = table.color.static if table.color is not None else None
    effective_table_ink = sanitize_color(_table_color_static, table.font.color or "")

    # Numeric table cells paint with dbt Sans Tabular on all non-serif themes;
    # Source Serif themes use the serif face for their numeric cells instead.
    numeric_cell_font = (
        SOURCE_SERIF_4_FONT_FAMILY
        if table.font.family and "Source Serif" in table.font.family
        else DBT_SANS_TABULAR_FONT_FAMILY
    )
    authored_columns = (
        primary.columns if primary is not None and primary.columns else {}
    )
    table_columns = _materialize_table_columns(
        primary.columns if primary is not None else None,
        column_defaults_val,
        data,
        table.row.role,
        pivot_measure_names,
        table_column_links,
        table_column_rows,
    )
    resolved_columns = _with_resolved_scale_stops(
        table_columns,
        effective_table_ink,
        formats=chart_local_style_context.formats,
        font_family=numeric_cell_font,
        rows=data,
        allow_shared_scale=allow_shared_scale,
        own_formats={name: cfg.format for name, cfg in authored_columns.items()},
        defaults_format=(
            column_defaults_val.format if column_defaults_val is not None else None
        ),
        declared=normalized.query.formats if normalized.query else None,
        filter_variables=filter_variables,
        link_open=link_open,
    )
    # The prefix lane paints apart from the signed digits (`€  −40`).
    for name, column in resolved_columns.items() if resolved_columns else ():
        if column.format is not None and column.format.sign_placement is not None:
            raise CompilationError.from_code(
                ERR_FORMAT_SIGN_PLACEMENT_TABLE_UNSUPPORTED,
                field_path=f"charts.{normalized.id}.style.columns.{name}.format",
            )
    header_overflow_val = primary.header_overflow if primary is not None else None
    _tf = resolve_title_font(chart_local_style_context, width)
    return ResolvedTableChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            None,
            requested_alias_palette=_effective_requested_alias_palette(
                chart_style_context, primary
            ),
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=table.padding,
            legendless=True,
            wrap_legend=False,
        ),
        **_shared_kwargs(normalized, display_text, chart_local_style_context),
        chart_type="table",
        rows=normalized.rows,
        pivot_columns=normalized.columns,
        values=normalized.values,
        spanners=normalized.spanners,
        min_height=normalized.min_height,
        max_height=normalized.max_height,
        columns=resolved_columns,
        header_overflow=header_overflow_val,
        column_defaults=column_defaults_val,
        style=ResolvedTableStyle(
            table=table,
            title=concrete_title_style(chart_local_style_context),
            formats=chart_local_style_context.formats,
            title_font=_tf,
            pagination=chart_local_style_context.pagination,
        ),
    )
