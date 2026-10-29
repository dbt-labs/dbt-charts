"""Hint generators for error codes using difflib close-match suggestions.

`available` is `Sequence[str]` — a raise site writes `available=sorted(...)`,
the expression it naturally reaches for. `DbtChartsError.from_code` joins any
`Sequence[str]` for display at message-format time; `_suggest_close_match`
consumes the sequence directly.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence

# Names this release retired, each pointing at the successor that means what it
# meant. Diagnostic data, not vocabulary: nothing in the format system reads
# this, and a board using one of these still fails with ERR-FORMAT-INVALID.
#
# It exists because a value rename cannot be expressed as a schema migration — a
# `value_map` is declared total over its field's domain and `format:` is an open
# string, so every literal d3 spec an author may write would hard-fail a
# migration that has no business touching it. This hint is the whole migration
# path instead.
#
# Fuzzy matching is actively wrong for a rename: the nearest string to
# `number_default` is `number_full`, which compiles clean and silently drops
# compaction, so an author who followed that hint would get a changed render
# and no error at all.
#
# Drop an entry once boards predating its rename are no longer in the wild.
RETIRED_FORMAT_SUCCESSORS: dict[str, str] = {
    "number_default": "number",
}

# Every retired `chart_rendering.<key>` -> where its value
# lives once it leaves config: a theme path (`style.…`, authored in
# `charts/meta.yml`) for a design value, a config path (`rendering.…`,
# `dbt_charts.yml`) for a runtime knob, or None for an engine constant
# (`dbt_charts.core.constants`), which is not configurable. `load_config`
# raises ERR-CONFIG-KEY-MOVED for a project key listed here. Drop an entry once
# projects predating its move are no longer in the wild.
CHART_RENDERING_SUCCESSORS: dict[str, str | None] = {
    "axis.label_gap_spaces": None,
    "axis.label_gap_spaces_numeric": None,
    "axis.sparse_ceiling_px": None,
    "kpi.compact_spec": "style.charts.kpi.value.compact.spec",
    "kpi.compact_threshold": "style.charts.kpi.value.compact.threshold",
    "spark_bar.title_height": None,
    "spark_bar.title_baseline_y": None,
    "step_band.disconnected_width": "style.charts.marks.line.disconnected_width",
    "endpoint_labels.max_width_fraction": None,
    "endpoint_labels.line_height_multiplier": None,
    "facet.chrome_px": None,
    "facet.min_panel_px": None,
    "facet.mirror_axis_px": None,
    "stroke.min_width": None,
    "stroke.max_width": None,
    "stroke.fallback_width": None,
    "stroke.stacked_fallback_width": "style.charts.marks.area.stacked.stroke.width",
    "point.diameter_ratio": None,
    "point.min_px_per_point": None,
    "pie.wedge_label_min_share": None,
    "pie.label_reach_coefficient": None,
    "pie.invisible_slice_share": None,
    "pie.outer_fraction": None,
    "pie.wheel_dominance_min_ratio": None,
    "pie.attached_table_gap_px": None,
    "pie.hybrid_heading_gap_px": None,
    "pie.right_placement_min_width_fraction": None,
    "pie.right_placement_min_width_px": None,
    "pie.right_placement_max_wheel_px": None,
    "bar.grouped_bar_padding_inner": "style.charts.bar.grouped.padding_inner",
    "bar.grouped_bar_padding_outer": "style.charts.bar.grouped.padding_outer",
    "bar.plot_height_floor_row_legend_total_px": None,
    "bar.hover_band_trigger_fraction": None,
    "bar.hover_band_extend_fraction": None,
    "bar.label_fit_line_height_multiplier": None,
    "bar.top_rail_label_gap_spaces": None,
    "plot_height_floor.ratio": None,
    "plot_height_floor.irreducible_height_px": None,
    "plot_height_floor.axis_titles_height_px": None,
    "plot_height_floor.subtitle_height_px": None,
    "plot_height_floor.compact_legend_row_px": None,
    "type_inference.max_ordinal_buckets": None,
    "frame.footer_rule_gap_px": "style.footer.divider.gap",
    "frame.footer_timestamp_gap_px": "style.timestamp.gap",
    "frame.footer_brand_weight": "style.footer.brand_weight",
    "support_table.divider_gap": "style.charts.support_table.divider.gap",
    "support_table.chart_support_table_max_x_ticks": None,
    "support_table.plot_width_floor_ratio": None,
    "support_table.column_block_share_warn_ratio": None,
    "gradient.nice_tick_count": None,
    "legend.row_swatch_width_px": "style.charts.legend.row.swatch_width",
    "legend.row_entry_gap_px": "style.charts.legend.row.entry_gap",
    "legend.min_reserve_px": None,
    "legend.dimension_axis_reserve_chrome_px": None,
    "legend.row_height_px": None,
    "legend.chrome_height_px": None,
    "hover_emphasis.dimmed_opacity": None,
    "color_variants.label_ink_min_contrast": None,
    "text_contrast.min_ratio": None,
    "text_contrast.large_text_min_ratio": None,
    "color_variants.light_k": None,
    "color_variants.light_chroma": None,
    "color_variants.light_min_gap": None,
    "color_variants.light_pale_gap": None,
    "color_variants.dark_pole": None,
    "color_variants.dark_k": None,
    "color_variants.pale_l": None,
    "color_variants.pale_chroma": None,
    "color_variants.deep_pole": None,
    "color_variants.deep_k": None,
    **{
        f"thumbnail.{name}": f"rendering.thumbnail.{name}"
        for name in (
            "placeholder_ink",
            "bar_color_weight",
            "status_color_weight",
            "chromatic_saturation",
            "text_height",
            "large_text_height",
            "large_text_size",
            "min_text_height",
            "bar_height",
            "bar_max_corner_radius",
            "min_bar_width",
            "bar_gap",
            "bar_room_share",
            "block_share",
            "min_block_width",
            "variable_pill_height",
            "glyph_dot_share",
            "min_glyph_dot_radius",
            "same_line_share",
        )
    },
}

# The categorical-variant companion spellings one release retired (a shipped
# `<family>-<suffix>.yml` file, or a `style.palettes.category_<suffix>` role)
# -> the literal `Variant` word (core/colors.py) that replaced them: `dark`
# and `light` keep their name, `ghost` becomes `pale`, `ink` becomes `deep`.
# Shared by palette.py's live "was retired" hint and
# migrations/versions/v0_9_0.py's migration value-map, so the two can't
# drift out of step with each other -- each import this rather than keep
# its own copy.
RETIRED_VARIANT_SUFFIX_SUCCESSORS: dict[str, str] = {
    "dark": "dark",
    "light": "light",
    "ghost": "pale",
    "ink": "deep",
}

# The two families that shipped literal `<family>-<suffix>.yml` companion
# files, and the one role prefix (`category_dark`, ... in `style.palettes`)
# that named them by role -- both retired in the same release. Also shared
# by palette.py and v0_9_0.py; see `RETIRED_VARIANT_SUFFIX_SUCCESSORS`.
RETIRED_VARIANT_FAMILIES: tuple[str, ...] = ("vivid-10", "editorial-10")
RETIRED_VARIANT_ROLE_PREFIX = "category"

_RETIRED_SUFFIXES = "|".join(RETIRED_VARIANT_SUFFIX_SUCCESSORS)
# One pattern per retired *whole-token* shape, each capturing (suffix, tail)
# with `tail` being the segment that carries across unchanged. The alias and
# slot tails are a single segment on purpose: a retired role followed by two
# dotted segments (`category_dark.blue.dark`) was never a spelling the old
# grammar accepted, and respelling it would name a token that cannot resolve.
_RETIRED_TOKEN_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    # `category_dark[2]` -> `category[2].dark`
    (
        re.compile(rf"^{RETIRED_VARIANT_ROLE_PREFIX}_({_RETIRED_SUFFIXES})\[(\d+)\]$"),
        RETIRED_VARIANT_ROLE_PREFIX + "[{tail}].{variant}",
    ),
    # `category_dark.blue` -> `category.blue.dark`
    (
        re.compile(
            rf"^{RETIRED_VARIANT_ROLE_PREFIX}_({_RETIRED_SUFFIXES})"
            r"\.([A-Za-z][A-Za-z0-9_-]*)$"
        ),
        RETIRED_VARIANT_ROLE_PREFIX + ".{tail}.{variant}",
    ),
    # `category_dark` -> `category.dark` (a whole-list role reference)
    (
        re.compile(rf"^{RETIRED_VARIANT_ROLE_PREFIX}_({_RETIRED_SUFFIXES})()$"),
        RETIRED_VARIANT_ROLE_PREFIX + ".{variant}",
    ),
)
_RETIRED_FAMILY_NAMES = "|".join(re.escape(f) for f in RETIRED_VARIANT_FAMILIES)
_RETIRED_FAMILY_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    # `vivid-10-dark.3` -> `vivid-10.3.dark`
    (
        re.compile(rf"^({_RETIRED_FAMILY_NAMES})-({_RETIRED_SUFFIXES})\.(\d+)$"),
        "{family}.{tail}.{variant}",
    ),
    # `vivid-10-dark:4` / `vivid-10-dark:4_r` -> `vivid-10:4.dark` -- the
    # whole-list `:N`/`_r` shorthand rides along, after the family and
    # before the variant, the order `palette()` parses.
    (
        re.compile(
            rf"^({_RETIRED_FAMILY_NAMES})-({_RETIRED_SUFFIXES})(:\d+(?:_r)?|_r)$"
        ),
        "{family}{tail}.{variant}",
    ),
    # `vivid-10-dark` -> `vivid-10.dark` (a whole-list palette name)
    (
        re.compile(rf"^({_RETIRED_FAMILY_NAMES})-({_RETIRED_SUFFIXES})()$"),
        "{family}.{variant}",
    ),
)


def respell_retired_variant_token(token: str) -> str | None:
    """The third-segment spelling that replaced retired *token*, or ``None``.

    The single transformation both consumers of the retired companion
    spellings share: ``versions/v0_9_0.py`` builds its migration value-map
    from it, and ``palette.py``'s live "was retired" hint names its result --
    so the replacement the migration writes and the one the error message
    suggests can never differ. ``None`` means *token* is not one of the
    closed set of retired shapes (a current spelling, a continuous palette's
    own live ``-dark`` fork, or a mixed old/new spelling the old grammar never
    accepted) and gets no successor.
    """
    for pattern, template in _RETIRED_TOKEN_SHAPES:
        match = pattern.match(token)
        if match:
            suffix, tail = match.groups()
            return template.format(
                tail=tail, variant=RETIRED_VARIANT_SUFFIX_SUCCESSORS[suffix]
            )
    for pattern, template in _RETIRED_FAMILY_SHAPES:
        match = pattern.match(token)
        if match:
            family, suffix, tail = match.groups()
            return template.format(
                family=family,
                tail=tail,
                variant=RETIRED_VARIANT_SUFFIX_SUCCESSORS[suffix],
            )
    return None


def _suggest_close_match(value: str, available: Sequence[str]) -> str | None:
    if isinstance(available, str):
        raise TypeError(
            "available must be a Sequence[str] of candidate names, not a bare "
            "str (which is itself a Sequence[str] of characters and would "
            "silently score character-by-character) — pass "
            "available=['a', 'b'] instead of a joined string."
        )
    candidates = [c.strip() for c in available if c.strip()]
    if not candidates:
        return None
    matches = difflib.get_close_matches(value, candidates, n=1, cutoff=0.5)
    if matches:
        return f"Did you mean {matches[0]!r}?"
    return None


def suggest_close_source(
    source: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for unknown source names."""
    return _suggest_close_match(source, available)


def suggest_close_variable(
    variable: str,
    available: Sequence[str] = (),
    **_kwargs: object,  # type-state: object_annotation — hint_generator is called with every field of the code; the rest are heterogeneous and unread
) -> str | None:
    """Return a 'Did you mean X?' hint for a variable name the board does not declare."""
    return _suggest_close_match(variable, available)


def suggest_close_theme(
    theme: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for an unknown theme name."""
    return _suggest_close_match(theme, available)


def suggest_close_chart(
    chart: str,
    available: Sequence[str] = (),
    **_kwargs: object,  # type-state: object_annotation — hint_generator is called with every field of the code; the rest are heterogeneous and unread
) -> str | None:
    """Return a 'Did you mean X?' hint for an unknown chart id."""
    return _suggest_close_match(chart, available)


def suggest_layout_chart(
    ref: str,
    available: Sequence[str] = (),
    **_kwargs: object,  # type-state: object_annotation — hint_generator is called with every field of the code; the rest are heterogeneous and unread
) -> str | None:
    """The edit for a layout name that is not a chart key: the closest key, then all.

    ``available`` is empty for a reference outside a layout, which has no hint.
    """
    if not available:
        return None
    keys = ", ".join(f"`{name}`" for name in available)
    rule = f"Every name in `rows`, `cols` or `grid` is a key under `charts:` ({keys})."
    matches = difflib.get_close_matches(ref, available, n=1, cutoff=0.5)
    if matches:
        return f"Did you mean `{matches[0]}`? Replace `{ref}` with it. {rule}"
    return (
        f"{rule} Replace `{ref}` with one of them, or define `{ref}` under `charts:`."
    )


def suggest_close_extends(
    entry: str,
    available: Sequence[str] = (),
    **_kwargs: object,  # type-state: object_annotation — hint_generator is called with every field of the code; the rest are heterogeneous and unread
) -> str | None:
    """Return a 'Did you mean X?' hint for an unresolvable `extends:` entry."""
    return _suggest_close_match(entry, available)


def suggest_close_format(
    spec: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for an unresolvable format spec.

    A retired name gets its recorded successor rather than a fuzzy match — see
    ``RETIRED_FORMAT_SUCCESSORS`` for why the nearest string is the wrong answer
    for a rename.
    """
    successor = RETIRED_FORMAT_SUCCESSORS.get(spec)
    # Only when the successor is legal *here*: `available` is the pool the slot
    # takes, so a successor outside it would trade a fuzzy wrong answer for a
    # confident one. An empty pool means no name is legal — the `style.formats` alias-target
    # check passes one, and a predefined successor there re-raises on the very
    # next compile.
    if successor is not None and successor in available:
        return f"{spec!r} was renamed to {successor!r}."
    return _suggest_close_match(spec, available)


def suggest_close_palette(
    name: str,
    available: Sequence[str] = (),
    # ERR-PALETTE-UNKNOWN's remaining field is `field_path`, a str.
    **_kwargs: str,
) -> str | None:
    """Return a hint for an unknown palette or palette role.

    A retired companion spelling gets its recorded successor rather than a
    fuzzy match -- the same refusal ``suggest_close_format`` makes, for the
    same reason: the nearest shipped name to ``vivid-10-dark`` is
    ``vivid-10``, which compiles clean and silently drops the darkening the
    author wrote it for. This is the compile-time diagnostic's only route to
    that successor for a slot no migration reaches (a KPI ``background``
    scale palette), so silence here would be the failure mode.
    """
    successor = respell_retired_variant_token(name)
    if successor is not None:
        return f"{name!r} was retired; use {successor!r} instead."
    return _suggest_close_match(name, available)


def suggest_close_column(
    column_name: str,
    available: Sequence[str] = (),
    **_kwargs: object,  # type-state: object_annotation — hint_generator is called with every field of the code; the rest are heterogeneous and unread
) -> str | None:
    """Return a 'Did you mean X?' hint for a column a model no longer produces."""
    return _suggest_close_match(column_name, available)


def suggest_close_ref(
    ref_name: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for an unknown dbt ref() target."""
    return _suggest_close_match(ref_name, available)


def suggest_close_source_table(
    source_name: str,
    table_name: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for an unknown dbt source() reference."""
    return _suggest_close_match(f"{source_name}.{table_name}", available)
