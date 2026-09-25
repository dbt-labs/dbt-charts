"""Enhanced YAML error formatting.

This module provides utilities to format YAML validation errors with:
1. Line numbers where errors occur
2. Context - the actual YAML snippet
3. Helpful suggestions ("Did you mean?")
4. Graceful handling of common mistakes

Refs #94
"""

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import UnionType
from typing import TYPE_CHECKING, Annotated, Any, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, Tag

from dbt_charts.core.utils import YAML_LOADER

if TYPE_CHECKING:
    from dbt_charts.core.diagnostics.diagnostic import Diagnostic


_DOC_TOPIC_BY_TOP_LEVEL_KEY = {
    "variables": "variables",
    "queries": "queries",
    "charts": "charts",
    "rows": "layout",
    "cols": "layout",
    "grid": "layout",
    "tabs": "layout",
    "style": "board",
    "theme": "board",
}


@dataclass(frozen=True)
class _ExtraFieldDiagnostic:
    message: str
    hint: str
    fields: dict[str, Any]


# Union-branch discriminator patterns: strings in Pydantic loc tuples that
# identify which branch of a union was being tried, not a real field name.
# These are NOT real YAML keys — they are Pydantic-internal identifiers.
_UNION_DISCRIMINATOR_PREFIXES = (
    "function-after[",
    "function-before[",
    "function-wrap[",
    "function-plain[",
    "dict[",
    "list[",
    "tagged-union[",
    "union[",
)

# Branch names that are literal Pydantic type names (not real YAML fields)
_PRIMITIVE_TYPE_DISCRIMINATORS = {"str", "int", "float", "bool", "bytes", "None"}


def _is_union_discriminator(part: Any) -> bool:
    """Return True when a loc element is a Pydantic union-branch label, not a YAML key."""
    if not isinstance(part, str):
        return False
    if part in _PRIMITIVE_TYPE_DISCRIMINATORS:
        return True
    # Functional discriminator tags (authored.py) use '@' prefix to prevent collision
    # with user-chosen YAML keys ('ref', 'inline', etc.).
    if part.startswith("@"):
        return True
    return any(part.startswith(pfx) for pfx in _UNION_DISCRIMINATOR_PREFIXES)


def _find_discriminator_index(loc: tuple[Any, ...]) -> int | None:
    """Return the index of the union-branch discriminator in loc, or None."""
    for i, part in enumerate(loc):
        if _is_union_discriminator(part):
            return i
    return None


def _branch_score(err: dict[str, Any]) -> int:
    """Score a branch error — lower is better (more informative).

    extra_forbidden → field we actually authored that's forbidden here → 0 (best)
    missing         → required field for this branch missing → 1
    other           → some other type mismatch → 2 (noisier)
    """
    t = err.get("type", "")
    if t == "extra_forbidden":
        return 0
    if t == "missing":
        return 1
    return 2


def _collapse_union_validation_errors(
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse Pydantic union-branch noise into the most informative errors.

    Pydantic tries every branch of a union type annotation (str | AuthoredBoard
    | AuthoredChart | dict[str, AuthoredChart]) for each
    layout row. A single invalid row can produce 14+ error dicts — one per
    (branch × wrong-field) combination. This function keeps only the errors
    from the best-matching branch per unique loc prefix.

    Algorithm:
    1. Partition errors into "union branch" (loc contains a discriminator) and
       "non-union" (plain field errors with no discriminator in loc).
    2. For each unique prefix (loc up to the discriminator), collect all branches.
    3. Pick the branch whose errors have the lowest aggregate _branch_score.
    4. Keep the errors from that winning branch, mapped to the canonical loc
       (prefix + remaining path after the discriminator).
    5. Return non-union errors + winning-branch errors, capped at MAX_GROUPS.
    """
    MAX_GROUPS = 8

    non_union: list[dict[str, Any]] = []
    # Maps prefix_key → {branch_name: [error_dicts]}
    branch_groups: dict[tuple[Any, ...], dict[str, list[dict[str, Any]]]] = {}

    for err in errors:
        loc = err.get("loc", ())
        disc_idx = _find_discriminator_index(loc)
        if disc_idx is None:
            non_union.append(err)
            continue

        prefix = loc[:disc_idx]
        branch_name = str(loc[disc_idx])
        branch_groups.setdefault(prefix, {}).setdefault(branch_name, []).append(err)

    # Pick the best branch per prefix
    collapsed: list[dict[str, Any]] = list(non_union)

    for _prefix, branches in branch_groups.items():
        # Score each branch: sum of individual error scores
        scored = sorted(
            branches.items(),
            key=lambda kv: (
                sum(_branch_score(e) for e in kv[1]),
                len(kv[1]),  # tie-break: fewer errors
            ),
        )
        _best_branch_name, best_errors = scored[0]

        # Rewrite locs to strip the discriminator so downstream path logic works
        for err in best_errors:
            loc = err.get("loc", ())
            disc_idx = _find_discriminator_index(loc)
            if disc_idx is not None:
                # Canonical loc: prefix + everything after the discriminator
                canonical_loc = loc[:disc_idx] + loc[disc_idx + 1 :]
                collapsed.append({**err, "loc": canonical_loc})
            else:
                collapsed.append(err)

    return collapsed[:MAX_GROUPS]


@lru_cache(maxsize=1)
def _authored_chart_fields_by_type() -> dict[str, frozenset[str]]:
    """Authored chart root fields keyed by the AuthoredChart union tag."""
    from dbt_charts.core.compile.models.chart.authored import (  # noqa: PLC0415
        AUTHORED_CHART_VARIANTS,
        AuthoredChart,
    )

    union = get_args(AuthoredChart)[0]
    classes: dict[str, type[BaseModel]] = {
        get_args(v)[0].__name__: get_args(v)[0] for v in get_args(union)
    }
    return {
        tag: frozenset(classes[cls_name].model_fields)
        for tag, cls_name in AUTHORED_CHART_VARIANTS.items()
    }


def _chart_type_from_error_path(
    field_path: list[str],
    yaml_content: str | None,
) -> str | None:
    fields_by_type = _authored_chart_fields_by_type()
    # Prefer the innermost chart discriminator for nested layer errors.
    for part in reversed(field_path[2:-1]):
        if part in fields_by_type:
            return part

    if not yaml_content or field_path[0:1] != ["charts"] or len(field_path) < 2:
        return None

    try:
        data = yaml.load(yaml_content, Loader=YAML_LOADER)
    except yaml.YAMLError:
        return None

    if not isinstance(data, dict):
        return None
    charts = data.get("charts")
    if not isinstance(charts, dict):
        return None
    chart = charts.get(field_path[1])
    if not isinstance(chart, dict):
        return None
    chart_type = chart.get("type")
    if isinstance(chart_type, str):
        return chart_type
    return None


_CARTESIAN_CHART_TYPES = frozenset(
    {"bar", "line", "area", "scatter", "heatmap", "histogram"}
)

# Families with no per-chart card render surface — style.font/style.border
# validate structurally but have no VL "card" to paint them onto.
_CARD_STYLE_UNSUPPORTED_CHART_TYPES = _CARTESIAN_CHART_TYPES | {"pie", "donut"}


def _card_style_slot_chart_type(
    field_name: str,
    field_path: list[str],
    yaml_content: str | None,
) -> str | None:
    """Chart family for a style.font / style.border error at a card-style slot.

    Two authoring positions reach the same narrowed per-family patch type:
    ``style.charts.<family>.<field>`` (board-level) and
    ``charts.<id>.style.<field>`` (chart-local).  Both put the field directly
    under the slot, so both are matched by position rather than by a membership
    test over the whole path: ``font`` and ``border`` are among the most reused
    key names in the style tree, and a deeper hit like ``style.axis_x.ticks.font``
    or ``style.marks.border`` is a different, still-valid field that merely
    shares the name.  Telling that author to delete the key would be worse than
    the generic unknown-field error.
    """
    if field_name not in ("font", "border"):
        return None
    if field_path[0:2] == ["style", "charts"] and len(field_path) == 4:
        family = field_path[2]
        return family if family in _CARD_STYLE_UNSUPPORTED_CHART_TYPES else None
    if field_path[0:1] == ["charts"] and field_path[-2:-1] == ["style"]:
        chart_type = _chart_type_from_error_path(field_path, yaml_content)
        if chart_type in _CARD_STYLE_UNSUPPORTED_CHART_TYPES:
            return chart_type
    return None


def _unsupported_known_chart_field_hint(
    field_name: str,
    field_path: list[str],
    yaml_content: str | None,
) -> str | None:
    fields_by_type = _authored_chart_fields_by_type()
    chart_type = _chart_type_from_error_path(field_path, yaml_content)
    if chart_type not in fields_by_type:
        return None
    if field_name in fields_by_type[chart_type]:
        return None

    if field_name in ("format", "formatter"):
        if chart_type in _CARTESIAN_CHART_TYPES:
            axis = next(
                (p for p in reversed(field_path[2:]) if p in ("axis_x", "axis_y")),
                None,
            )
            if axis == "axis_x":
                return (
                    f"`{field_name}:` is not supported on `type: {chart_type}`. "
                    "Use `style.axis_x.labels.format:` for x-axis tick labels — "
                    "it is never used for tooltip formatting."
                )
            return (
                f"`{field_name}:` is not supported on `type: {chart_type}`. "
                "Use `style.number_format:` (or `style.axis_y.labels.format:`) for axis and "
                "tooltip formatting."
            )
        if chart_type == "table":
            return (
                f"`{field_name}:` is not supported on `type: table`. "
                "Use `style.columns.<column_name>.format:` per column instead."
            )

    if field_name == "axis_quantitative" and chart_type == "heatmap":
        return (
            "`axis_quantitative:` is not supported on `type: heatmap`. "
            "Heatmap's axes are both nominal, so it has no quantitative axis "
            "to style. Use `axis_band:` instead."
        )

    supporting_types = tuple(
        candidate_type
        for candidate_type, fields in sorted(fields_by_type.items())
        if field_name in fields
    )
    if not supporting_types:
        return None

    supported = ", ".join(supporting_types)
    hint = (
        f"`{field_name}:` is not supported on `type: {chart_type}`. "
        f"Supported chart types for `{field_name}:`: {supported}."
    )
    if field_name in ("height", "width"):
        hint += (
            " Use the chart family's sizing surface or the surrounding layout "
            f"instead of chart-root `{field_name}:`."
        )
    return hint


def _field_key(name: str, field: Any) -> str:
    return str(field.alias or name)


def _model_field_annotation(model: type[BaseModel], key: str) -> Any | None:
    for field_name, field in model.model_fields.items():
        if key in (field_name, field.alias):
            return field.annotation
    return None


def _is_model_type(value: Any) -> bool:
    return isinstance(value, type) and issubclass(value, BaseModel)


def _tagged_annotated_inner(annotation: Any, segment: str) -> tuple[bool, Any]:
    if get_origin(annotation) is not Annotated:
        return False, annotation
    args = get_args(annotation)
    tag = next(
        (metadata.tag for metadata in args[1:] if isinstance(metadata, Tag)), None
    )
    if tag == segment:
        return True, args[0]
    return False, args[0]


def _annotation_tag(annotation: Any) -> str | None:
    if get_origin(annotation) is not Annotated:
        return None
    return next(
        (
            metadata.tag
            for metadata in get_args(annotation)[1:]
            if isinstance(metadata, Tag)
        ),
        None,
    )


def _next_annotations_for_segment(annotation: Any, segment: str) -> list[Any]:
    matched_tag, inner = _tagged_annotated_inner(annotation, segment)
    if matched_tag:
        return [inner]
    annotation = inner

    origin = get_origin(annotation)
    if origin is Annotated:
        return _next_annotations_for_segment(get_args(annotation)[0], segment)
    if origin in (Union, UnionType):
        next_annotations: list[Any] = []
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            next_annotations.extend(_next_annotations_for_segment(arg, segment))
        return next_annotations
    if origin is dict:
        value_annotation = get_args(annotation)[1]
        return [value_annotation]
    if origin is list:
        if segment.isdigit():
            return [get_args(annotation)[0]]
        return []
    if _is_model_type(annotation):
        field_annotation = _model_field_annotation(annotation, segment)
        return [field_annotation] if field_annotation is not None else []
    return []


def _annotations_at_path(path: list[str]) -> list[Any]:
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    annotations: list[Any] = [AuthoredBoard]
    for segment in path:
        next_annotations: list[Any] = []
        for annotation in annotations:
            next_annotations.extend(_next_annotations_for_segment(annotation, segment))
        annotations = next_annotations
        if not annotations:
            return []
    return annotations


def _segment_is_tag(
    annotation: Any,  # type-state: explicit_any — typing-introspection value
    segment: str,
) -> bool:
    """True when *segment* is only reachable as a ``Tag(...)`` discriminator
    somewhere in the union arms of *annotation* -- schema plumbing (e.g.
    ``@inline``, a chart-family literal like ``bar``, a query-type literal
    like ``sql``) rather than a real YAML mapping key or list index."""
    if get_origin(annotation) is Annotated:
        matched, inner = _tagged_annotated_inner(annotation, segment)
        return matched or _segment_is_tag(inner, segment)
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(
            _segment_is_tag(arg, segment)
            for arg in get_args(annotation)
            if arg is not type(None)
        )
    return False


def _display_path(path: list[str]) -> list[str]:
    """*path* with pure schema-plumbing segments (union-tag discriminators)
    dropped, for messages an author reads -- unlike ``_annotations_at_path``,
    which needs those segments to walk the type tree and must see the full
    (tagged) path."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    annotations: list[Any] = [AuthoredBoard]  # type-state: explicit_any — typing value
    display: list[str] = []
    for segment in path:
        if not any(_segment_is_tag(annotation, segment) for annotation in annotations):
            display.append(segment)
        next_annotations: list[Any] = []  # type-state: explicit_any — typing value
        for annotation in annotations:
            next_annotations.extend(_next_annotations_for_segment(annotation, segment))
        annotations = next_annotations
        if not annotations:
            break
    return display


def _parent_is_grid_layout(parent_path: list[str]) -> bool:
    """True when *parent_path* lands on the board-layout ``grid:`` block.

    Distinguishes it from the axis-gridline ``grid:`` blocks, which share the
    name at several depths (chart style, theme style, per-layer y axis).
    """
    from dbt_charts.core.compile.models.board.authored import GridLayout

    return any(
        model is GridLayout
        for annotation in _annotations_at_path(parent_path)
        for model in _model_types_from_annotation(annotation)
    )


# `title:` names four unrelated blocks (board, chart, legend, axis); only the
# axis one has its text authored elsewhere, so the redirect is anchored to the
# model rather than to the key name.
_AXIS_TITLE_TEXT_HINT = (
    "Axis title text is authored on the chart, as `x_label:` / `y_label:` — "
    "`title:` here styles the title's typography only."
)


def _path_is_axis_title(path: list[str]) -> bool:
    """True when *path* lands on the ``title:`` block of any axis slot
    (``axis``, ``axis_x``, ``axis_y``, ``axis_quantitative``, ``axis_band``)."""
    from dbt_charts.core.compile.models.style.authored import AxisTitleStylePatch

    return any(
        model is AxisTitleStylePatch
        for annotation in _annotations_at_path(path)
        for model in _model_types_from_annotation(annotation)
    )


# style.total.label is the paint slot (typography only) for the donut center
# caption; its text is authored at chart-root total.label. TotalSlotStylePatch
# anchors only the label slot -- the value slot has its own type
# (TotalValueSlotStylePatch, which does carry a real `format` field), so no
# value/label path special-case is needed here.
_TOTAL_LABEL_TEXT_HINT = (
    "Donut center label text is authored on the chart, as `total.label:` — "
    "`style.total.label` here styles the label's typography only."
)


def _path_is_total_label(path: list[str]) -> bool:
    """True when *path* lands on the ``label:`` slot of a donut center total
    (``style.total.label``, or the theme-tier ``style.charts.pie.total.label``)."""
    from dbt_charts.core.compile.models.style.authored import TotalSlotStylePatch

    return any(
        model is TotalSlotStylePatch
        for annotation in _annotations_at_path(path)
        for model in _model_types_from_annotation(annotation)
    )


def _model_types_from_annotation(annotation: Any) -> list[type[BaseModel]]:
    _, annotation = _tagged_annotated_inner(annotation, "")

    origin = get_origin(annotation)
    if origin is Annotated:
        return _model_types_from_annotation(get_args(annotation)[0])
    if origin in (Union, UnionType):
        models: list[type[BaseModel]] = []
        union_args = [arg for arg in get_args(annotation) if arg is not type(None)]
        inline_args = [arg for arg in union_args if _annotation_tag(arg) == "@inline"]
        for arg in inline_args or union_args:
            if arg is type(None):
                continue
            models.extend(_model_types_from_annotation(arg))
        return models
    if origin is dict:
        return _model_types_from_annotation(get_args(annotation)[1])
    if origin is list:
        return _model_types_from_annotation(get_args(annotation)[0])
    if _is_model_type(annotation):
        return [annotation]
    return []


def _allowed_keys_at_path(parent_path: list[str]) -> list[str]:
    annotations = _annotations_at_path(parent_path)
    keys: set[str] = set()
    for annotation in annotations:
        for model in _model_types_from_annotation(annotation):
            for name, field in model.model_fields.items():
                if field.exclude is True:
                    continue
                keys.add(_field_key(name, field))
    return sorted(keys)


def _docs_topic_for_field_path(field_path: list[str]) -> str:
    if not field_path:
        return "board"
    return _DOC_TOPIC_BY_TOP_LEVEL_KEY.get(field_path[0], "board")


def _format_parent_path(parent_path: list[str]) -> str:
    return ".".join(parent_path) if parent_path else "root"


def _format_allowed_keys(keys: list[str], limit: int = 40) -> str:
    displayed = keys[:limit]
    suffix = ", ..." if len(keys) > limit else ""
    return ", ".join(displayed) + suffix


def _extra_field_diagnostic(
    field_path: list[str],
    yaml_content: str | None = None,
) -> _ExtraFieldDiagnostic | None:
    if not field_path:
        return None

    field_name = field_path[-1]
    parent_path = field_path[:-1]
    allowed_keys = _allowed_keys_at_path(parent_path)
    docs_topic = _docs_topic_for_field_path(field_path)
    parent = _format_parent_path(parent_path)
    full_path = ".".join(field_path)
    message = f"Unknown field {field_name!r} at {parent}. Full path: {full_path}."

    fields: dict[str, Any] = {
        "unknown_field": field_name,
        "parent_path": parent,
        "field_path": full_path,
        "allowed_keys": allowed_keys,
        "docs_topic": docs_topic,
    }

    # description: was renamed to notes: at every position that authors it
    # (board, chart, query, grid item, tab item, variable) -- the two words
    # share no substring difflib's cutoff would catch, so the generic
    # suggest_similar_value fallback below never fires for this rename. It is
    # the fallback wherever NOTES_RENAMES cannot reach -- a self-nested
    # sub-board, a union arm that never declared the field, a board past the
    # transparent-migration cutoff -- so it must name the replacement
    # explicitly rather than fall through to a plain allowed-keys dump. Gated on `"notes" in allowed_keys` rather than
    # firing unconditionally: a chart family with no notes field (e.g.
    # CalloutChart) correctly falls through to the generic diagnostic
    # instead of naming a replacement that doesn't exist there.
    if field_name == "description" and "notes" in allowed_keys:
        fields["suggestion"] = "notes"
        # `parent_path` can carry union-tag discriminators picked up from the
        # Pydantic error loc (`@inline`, a chart-family literal like `bar`, a
        # query-type literal like `sql`) that were never a YAML key the
        # author wrote -- e.g. a query-level description lands at loc
        # `queries.q.@inline.sql.description`. `_display_path` walks the same
        # schema tree and drops exactly those segments, so the hint names the
        # key's real parent (`queries.q`) instead of a schema-internal path.
        display_parent = _format_parent_path(_display_path(parent_path))
        return _ExtraFieldDiagnostic(
            message=message,
            hint=(
                f"`description:` was renamed to `notes:`. Rename the key at "
                f"{display_parent} to `notes:`. See: dct docs {docs_topic}"
            ),
            fields=fields,
        )

    # Authored grid.gap is inert (resolve reads style.layout.grid.gap instead)
    # and declarable but unconverted: the ("grid", "gap") tail also matches the
    # live style key, so it is legal only root-anchored. Until it is declared,
    # this hint is the whole author-facing story.
    # `grid:` also names axis-gridline blocks, which are a different key
    # entirely -- so ask the schema which model the parent is rather than
    # guessing from the path's names.
    if field_name == "gap" and _parent_is_grid_layout(parent_path):
        return _ExtraFieldDiagnostic(
            message=message,
            hint=(
                "Grid spacing is set by `style.layout.grid.gap` (pixels) — "
                "the `grid.gap` key never had any effect and should be removed."
            ),
            fields=fields,
        )

    _SIZING_FIELDS = frozenset(("aspect_ratio", "min_height", "max_height"))

    # Theme-level style.charts.kpi.* / style.charts.table.* sizing fields.
    # These were never part of the declared KpiChartStyle/TableChartStyle schema;
    # a @cache-divergence bug in the old pipeline accidentally accepted them.
    # The fix closes the hole, so authors now see a clear hint instead of a bare
    # extra-field dump.  There is no migration (no structural schema history to
    # migrate from), per the repo's fail-loud exemption for such cases.
    if (
        field_path[0:2] == ["style", "charts"]
        and len(field_path) >= 4
        and field_path[2] in ("kpi", "table")
        and field_name in _SIZING_FIELDS
    ):
        chart_family = field_path[2]
        return _ExtraFieldDiagnostic(
            message=message,
            hint=(
                f"`style.charts.{chart_family}` uses a fixed sizing contract — "
                f"`{field_name}` has no effect on {chart_family} charts "
                "and should be removed."
            ),
            fields=fields,
        )

    # Theme-level style.charts.heatmap.axis_quantitative. Heatmap's axes are
    # both nominal — there is no quantitative axis for this field to style.
    if (
        field_name == "axis_quantitative"
        and field_path[0:2] == ["style", "charts"]
        and len(field_path) >= 4
        and field_path[2] == "heatmap"
    ):
        return _ExtraFieldDiagnostic(
            message=message,
            hint=(
                "`style.charts.heatmap.axis_quantitative` is not supported — "
                "heatmap's axes are both nominal, so it has no quantitative "
                "axis to style. Use `style.charts.heatmap.axis_band` instead."
            ),
            fields=fields,
        )

    if field_name == "text" and _path_is_axis_title(parent_path):
        return _ExtraFieldDiagnostic(
            message=message,
            hint=_AXIS_TITLE_TEXT_HINT,
            fields=fields,
        )

    if field_name == "text" and _path_is_total_label(parent_path):
        return _ExtraFieldDiagnostic(
            message=message,
            hint=_TOTAL_LABEL_TEXT_HINT,
            fields=fields,
        )

    card_style_chart_type = _card_style_slot_chart_type(
        field_name, field_path, yaml_content
    )
    if card_style_chart_type is not None:
        return _ExtraFieldDiagnostic(
            message=message,
            hint=(
                f"`style.{field_name}` is not supported on "
                f"`type: {card_style_chart_type}` — {card_style_chart_type} has no "
                "per-chart card to paint it onto (it renders via Vega-Lite with no "
                "card surface separate from the board frame). The field has no "
                "effect and should be removed."
            ),
            fields=fields,
        )

    if field_path[0:1] == ["charts"] and len(field_path) >= 3:
        if "style" in field_path and field_name in ("height", "width"):
            return _ExtraFieldDiagnostic(
                message=message,
                hint=(
                    f"chart.{field_name} must be set at the chart root, "
                    f"not under style: — move it up one level."
                ),
                fields=fields,
            )
        if "style" in field_path and field_name == "legend":
            chart_type = _chart_type_from_error_path(field_path, yaml_content)
            if chart_type in ("kpi", "table"):
                return _ExtraFieldDiagnostic(
                    message=message,
                    hint=(
                        f"`type: {chart_type}` charts never paint a legend — "
                        "the field has no effect and should be removed."
                    ),
                    fields=fields,
                )
        unsupported_hint = _unsupported_known_chart_field_hint(
            field_name, field_path, yaml_content
        )
        if unsupported_hint:
            return _ExtraFieldDiagnostic(
                message=message,
                hint=unsupported_hint,
                fields=fields,
            )

    if not allowed_keys:
        return _ExtraFieldDiagnostic(
            message=message,
            hint=(
                f"Unknown field {field_name!r} at {parent}. See: dct docs {docs_topic}"
            ),
            fields=fields,
        )

    suggestion = suggest_similar_value(field_name, allowed_keys)
    if suggestion is not None:
        fields["suggestion"] = suggestion
    hint_parts = [
        f"Unknown field {field_name!r} at {parent}.",
        f"Allowed keys: {_format_allowed_keys(allowed_keys)}.",
    ]
    if suggestion is not None:
        hint_parts.append(f"Did you mean {suggestion!r}?")
    hint_parts.append(f"See: dct docs {docs_topic}")
    return _ExtraFieldDiagnostic(
        message=message,
        hint=" ".join(hint_parts),
        fields=fields,
    )


@lru_cache(maxsize=1)
def get_valid_chart_types() -> tuple[str, ...]:
    """Return chart types accepted by the authored chart union.

    ``ChartType`` is broader than the authored YAML surface: it includes
    internal and future Vega-Lite mark families such as ``boxplot`` and
    ``tick``. Error messages must name what authors can write today.
    """
    from dbt_charts.core.compile.models.chart.authored import (
        AUTHORED_CHART_TYPE_TAGS,
    )

    return AUTHORED_CHART_TYPE_TAGS


def get_yaml_context(
    yaml_content: str,
    line_number: int,
    context_lines: int = 2,
) -> str:
    """Get YAML context around an error line.

    Returns the surrounding lines with the error line highlighted.

    Args:
        yaml_content: Raw YAML string
        line_number: Line number of the error (1-indexed)
        context_lines: Number of lines to show before/after

    Returns:
        Formatted context string with line numbers and error marker
    """
    lines = yaml_content.split("\n")

    # Ensure valid line number
    if line_number < 1 or line_number > len(lines):
        return ""

    # Calculate range
    start = max(0, line_number - context_lines - 1)
    end = min(len(lines), line_number + context_lines)

    # Build context with line numbers
    result_lines: list[str] = []
    for idx in range(start, end):
        actual_line_num = idx + 1
        line = lines[idx]

        # Mark the error line
        if actual_line_num == line_number:
            marker = ">>>"
            suffix = "  # <-- Error here"
        else:
            marker = "   "
            suffix = ""

        result_lines.append(f"{marker} {actual_line_num:4d} | {line}{suffix}")

    return "\n".join(result_lines)


def suggest_similar_value(
    invalid_value: str,
    valid_values: Sequence[str],
    cutoff: float = 0.6,
) -> str | None:
    """Suggest a similar valid value for a typo.

    Uses difflib to find close matches to the invalid value.

    Args:
        invalid_value: The invalid value that was provided
        valid_values: List of valid values to match against
        cutoff: Minimum similarity ratio (0-1) for a match

    Returns:
        The closest valid value, or None if no good match
    """
    if not invalid_value or not valid_values:
        return None

    # Normalize for comparison
    normalized_input = invalid_value.lower().strip()

    # First check for exact match (case insensitive)
    for valid in valid_values:
        if valid.lower() == normalized_input:
            return valid

    # Use difflib to find close matches
    matches = difflib.get_close_matches(
        normalized_input,
        [v.lower() for v in valid_values],
        n=1,
        cutoff=cutoff,
    )

    if matches:
        # Return the original case version
        for valid in valid_values:
            if valid.lower() == matches[0]:
                return valid

    return None


def _newer_schema_version_hint(yaml_content: str | None) -> str | None:
    """A hint when *yaml_content* declares a ``_schema_version`` newer than the
    latest frozen schema this build knows about.

    ``_schema_version`` is informational (``dct migrate``-written, never
    validated) -- this signals "the file may need a newer dbt charts", not a
    guarantee. Does not raise on a malformed value: the value is read from a
    raw re-parsed mapping with no Pydantic coercion, so a YAML float,
    non-numeric text, or a malformed version degrades to no hint rather than
    a crash inside error formatting itself. This does not extend to
    a parse error the earlier re-parse above could itself raise -- that risk
    already exists on every other path through this same re-parse and is not
    new here.

    Only fires across a real release boundary: within one dev cycle, a file
    this build's ``dct migrate`` stamps carries ``catalog.latest_released.version``,
    the same value this build compares against, so ``declared <= latest`` and
    no hint fires. The hint is for a reader *older* than the build that wrote
    the stamp, which by definition can't be this build.
    """
    if not yaml_content:
        return None
    try:
        data = yaml.load(yaml_content, Loader=YAML_LOADER)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    declared = data.get("_schema_version")
    if not isinstance(declared, str):
        return None

    from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
        parse_dotted_version,
    )

    declared_parts = parse_dotted_version(declared)
    if declared_parts is None:
        return None

    # _board_migration_context (not the public load_yaml_schema_catalog) on
    # purpose: it's @cache'd and already warm here -- every call site of this
    # formatter is reached only after prepare_board_mapping already ran (and
    # would itself have raised on a broken migration declaration, well before
    # any diagnostic gets formatted), so this never pays the first-call cost
    # of re-reading and sha256-verifying every frozen schema from disk.
    from dbt_charts.core.compile.migrations.migrations import _board_migration_context

    catalog, _ = _board_migration_context()
    latest_parts = parse_dotted_version(catalog.latest_released.version)
    if latest_parts is None or declared_parts <= latest_parts:
        return None
    return (
        f"This board declares _schema_version {declared}, newer than the "
        f"{catalog.latest_released.version} schema this build of dbt charts "
        "understands. Upgrade dbt charts to parse it correctly."
    )


def format_validation_errors_structured(
    error: Any,
    yaml_content: str | None = None,
) -> "list[Diagnostic]":
    """Format a Pydantic ValidationError into collapsed Diagnostic objects.

    Each returned Diagnostic corresponds to one collapsed error group (not
    one raw Pydantic error). Union-branch phantom failures are suppressed; the
    best-matching branch per loc prefix is kept. Cap: MAX_GROUPS errors.

    Every Diagnostic here carries ``path`` (its dotted field path) but never
    ``range`` — the caller's compile-level source map resolves ranges from
    paths in one central stamping pass (``source_map.stamp_diagnostics``),
    so this formatter has no file-position concern of its own.

    Args:
        error: A pydantic.ValidationError instance.
        yaml_content: Raw YAML string -- used for extra-field key suggestions
            and (when it declares a newer ``_schema_version``) an upgrade hint
            appended to every returned diagnostic; not used for line
            resolution.

    Returns:
        list of Diagnostic (≤ MAX_GROUPS elements).
    """
    from dbt_charts.core.diagnostics.codes_compile import (
        ERR_EXTRA_FIELD,
        ERR_VALIDATION_FIELD,
        ERR_WRONG_SHAPE,
    )
    from dbt_charts.core.diagnostics.codes_unknown import ERR_INTERNAL
    from dbt_charts.core.diagnostics.diagnostic import Diagnostic

    raw_errors = error.errors()
    if not raw_errors:
        return [Diagnostic.from_code(ERR_INTERNAL, message=str(error))]

    collapsed = _collapse_union_validation_errors(raw_errors)
    newer_schema_hint = _newer_schema_version_hint(yaml_content)

    structured: list[Diagnostic] = []
    for err in collapsed:
        loc = err.get("loc", ())
        err_type = err.get("type", "")
        err_msg = err.get("msg", "Validation error")

        field_path_parts = [str(p) for p in loc if not str(p).startswith("function-")]
        field_path_str = ".".join(field_path_parts) if field_path_parts else ""
        chart_type_msg = _format_chart_type_discriminator_error(err, field_path_parts)
        if chart_type_msg is not None:
            err_msg = chart_type_msg

        extra_field_diagnostic: _ExtraFieldDiagnostic | None = None
        if err_type == "extra_forbidden":
            extra_field_diagnostic = _extra_field_diagnostic(
                field_path_parts, yaml_content
            )

        # Collect available keys for model_type (scalar where mapping expected)
        wrong_shape_keys: list[str] = (
            _allowed_keys_at_path(field_path_parts) if err_type == "model_type" else []
        )

        # Pick the most specific error code based on Pydantic error type
        if err_type == "extra_forbidden":
            ec = ERR_EXTRA_FIELD
        elif err_type == "model_type" and wrong_shape_keys:
            ec = ERR_WRONG_SHAPE
        elif field_path_str:
            ec = ERR_VALIDATION_FIELD
        else:
            ec = ERR_INTERNAL

        # Include input value in message when it's a simple string (not a dict/list)
        # so error consumers can see what value was actually provided.
        input_val = err.get("input")
        if extra_field_diagnostic is not None:
            message = extra_field_diagnostic.message
        elif field_path_str:
            if isinstance(input_val, str):
                message = f"Field '{field_path_str}': {err_msg} (got: {input_val!r})"
            else:
                message = f"Field '{field_path_str}': {err_msg}"
        else:
            message = err_msg

        hint: str | None = None
        if extra_field_diagnostic is not None:
            hint = extra_field_diagnostic.hint
        elif wrong_shape_keys:
            field_name = field_path_parts[-1] if field_path_parts else "field"
            docs_topic = _docs_topic_for_field_path(field_path_parts)
            redirect = (
                f" {_AXIS_TITLE_TEXT_HINT}"
                if _path_is_axis_title(field_path_parts)
                else f" {_TOTAL_LABEL_TEXT_HINT}"
                if _path_is_total_label(field_path_parts)
                else ""
            )
            hint = (
                f"'{field_name}' expects a mapping.{redirect} "
                f"Available keys: {_format_allowed_keys(wrong_shape_keys)}. "
                f"See: dct docs {docs_topic}"
            )
        elif (
            "variables" in field_path_parts
            and field_path_parts[-1:] == ["input"]
            and isinstance(input_val, str)
        ):
            # Suggest valid input types when a variable's `input:` value is wrong.
            from typing import get_args

            from dbt_charts.core.compile.models.variable.authored import (
                VariableInputType,
            )

            valid_types = tuple(get_args(VariableInputType))
            suggestion = suggest_similar_value(input_val, valid_types)
            if suggestion:
                hint = f"Did you mean {suggestion!r}? Valid input types: {', '.join(valid_types)}."
            else:
                hint = f"Valid input types: {', '.join(valid_types)}."

        if newer_schema_hint is not None:
            if hint:
                # Not every existing hint ends in sentence punctuation (e.g.
                # "See: dct docs board") -- force a boundary so the two don't
                # run together mid-sentence.
                sep = "" if hint.endswith((".", "!", "?")) else "."
                hint = f"{hint}{sep} {newer_schema_hint}"
            else:
                hint = newer_schema_hint

        fields = extra_field_diagnostic.fields if extra_field_diagnostic else {}

        # No range here — the caller's compile-level source map resolves
        # `.range` from `.path` in one central stamping pass (source_map.py),
        # so this formatter no longer needs its own line-lookup.
        structured.append(
            Diagnostic.from_code(
                ec,
                message=message,
                fields=fields,
                path=field_path_str or None,
                hint=hint,
            )
        )

    return structured


_SHAPE_NOUN_SEPARATORS = re.compile(r"[\s_-]+")

# Trailing words that name the medium rather than the shape. Authors and agents
# reach for "bullet graph" and "bullet chart" as readily as "bullet", and a
# table keyed on the bare noun answers none of them. Stripped only as a FALLBACK
# after the full spelling misses, never as part of the primary normalization:
# "dot plot" and "box plot" are shapes whose own last word is in this set, and
# folding them eagerly would collapse them onto "dot" and "box".
_SHAPE_NOUN_GENERIC_TAILS = ("chart", "graph", "plot", "diagram")


def _shape_noun_keys(value: str) -> tuple[str, ...]:
    """The spellings to try for one authored noun, most specific first.

    The full normalized spelling always wins; the generic-tail-stripped form is
    consulted only when it misses, so a shape that legitimately ends in one of
    those words keeps its own entry.
    """
    normalized = _normalize_shape_noun(value)
    keys = [normalized]
    for tail in _SHAPE_NOUN_GENERIC_TAILS:
        if normalized.endswith(tail) and len(normalized) > len(tail):
            keys.append(normalized[: -len(tail)])
    return tuple(keys)


def _normalize_shape_noun(value: str) -> str:
    """Fold the spellings of one shape noun together.

    Authors reach for `streamgraph`, `stream_graph`, and `Stream Graph` for the same
    chart, so separators are dropped rather than unified — otherwise every entry below
    would need a row per punctuation variant.
    """
    return _SHAPE_NOUN_SEPARATORS.sub("", value.strip().lower())


# Chart shapes the world names that dbt charts draws by composing existing fields
# rather than by a `type:` tag. The value spells the recipe out: an author who
# reached this error already looked for the noun and did not find it, so a bare
# pointer to the docs repeats the failure. Every entry is pinned by a spec
# assertion in tests/core/test_chart_shape_recipes.py.
_CHART_SHAPE_RECIPES = {
    "streamgraph": "type: area with color: and style.stack: center",
    "stacked_area": "type: area with color: and style.stack: zero",
    "stacked_bar": "type: bar with color: and style.stack: zero",
    "grouped_bar": "type: bar with color: and style.stack: none",
    "clustered_bar": "type: bar with color: and style.stack: none",
    "horizontal_bar": "type: bar with style.orientation: horizontal",
    "column": "type: bar with style.orientation: vertical",
    "100% stacked": "type: bar or type: area with color: and style.stack: normalize",
    "percent_stacked_bar": "type: bar with color: and style.stack: normalize",
    "normalized_bar": "type: bar with color: and style.stack: normalize",
    "small_multiples": "multiples.rows: <column> (or multiples.columns:) on a cartesian chart",
    "trellis": "multiples.rows: <column> (or multiples.columns:) on a cartesian chart",
    "faceted": "multiples.rows: <column> (or multiples.columns:) on a cartesian chart",
    "dual_axis": (
        "layers: on a cartesian chart, with axis_y.position: right on the "
        "added layer for its own y-axis"
    ),
    "combo": (
        "layers: on a cartesian chart, with axis_y.position: right on the "
        "added layer for its own y-axis"
    ),
    "bar_and_line": (
        "layers: on a cartesian chart, with axis_y.position: right on the "
        "added layer for its own y-axis"
    ),
    # The _column nouns promise a vertical layout, which orientation inference
    # doesn't guarantee for a categorical x — style.orientation: vertical is
    # explicit here, unlike the plain _bar recipes above.
    "stacked_column": "type: bar with style.orientation: vertical, color:, and style.stack: zero",
    "grouped_column": "type: bar with style.orientation: vertical, color:, and style.stack: none",
    "clustered_column": "type: bar with style.orientation: vertical, color:, and style.stack: none",
    "row_chart": "type: bar with style.orientation: horizontal",
    "vertical_bar": "type: bar with style.orientation: vertical",
    "stream_chart": "type: area with color: and style.stack: center",
    # Shapes composed from a base plus a `layers:` entry, or from one family
    # pointed at an unusual column. Measured by rendering each recipe and
    # looking at it, not by reasoning about whether it ought to work.
    "lollipop": (
        "type: bar with style.marks.bar.band_width thinned to a stem, plus a "
        "layers: scatter on the same y (style.marks.bar.size is a separate "
        "fixed-pixel mode and does not thin the bar)"
    ),
    "bullet": (
        "type: bar with style.stack: zero, style.stack_order: data and color: "
        "on the qualitative range column — one row per (category, band) — plus "
        "a layers: bar on the value and a layers: line on the target, both "
        "reading their own one-row-per-category source via layers[].query "
        "(sharing the ranges' rows multiplies each by the band count). Thin "
        "the value bar with style.marks.bar.band_width so the ranges stay "
        "visible, give the target style.marks.line.curve: step with "
        "connect: false for the goal tick, and set style.orientation: vertical"
    ),
    "slope": (
        "type: line with a two-category x and color: on the series column "
        "(year-shaped x values resolve to a continuous temporal scale, which "
        "fills in the span between the pair)"
    ),
    "bump": (
        "type: line on a rank column with color: on the series column (the data "
        "is entity x period x rank, so without it the rows collide on the "
        "period key) and a descending style.axis_y.scale.continuous.domain "
        "(e.g. [6, 1]) so rank 1 is on top"
    ),
    "dot_plot": (
        "type: scatter with a categorical x and a measure y; for several dots "
        "per category use long-format rows with color: on the series column "
        "rather than layers:, which keeps the shape rotatable"
    ),
    "cleveland_dot_plot": (
        "type: scatter with a categorical x and a measure y; for several dots "
        "per category use long-format rows with color: on the series column "
        "rather than layers:, which keeps the shape rotatable"
    ),
    "dumbbell": (
        "type: bar with y_start for the far end, thinned to a stem with "
        "style.marks.bar.band_width, plus a layers: scatter at each end "
        "(color: from style.color.categorical.palette — a chart with layers: "
        "does not bind board category_colors)"
    ),
    "barbell": (
        "type: bar with y_start for the far end, thinned to a stem with "
        "style.marks.bar.band_width, plus a layers: scatter at each end "
        "(color: from style.color.categorical.palette — a chart with layers: "
        "does not bind board category_colors)"
    ),
    "connected_dot_plot": (
        "type: bar with y_start for the far end, thinned to a stem with "
        "style.marks.bar.band_width, plus a layers: scatter at each end "
        "(color: from style.color.categorical.palette — a chart with layers: "
        "does not bind board category_colors)"
    ),
    "ranged_dot": (
        "type: bar with y_start for the low end, drawn as a pale interval "
        "(color: from style.color.categorical.palette), plus a layers: "
        "scatter on the point estimate"
    ),
    "floating_bar": "type: bar with y_start, so the bar runs from y_start to y instead of from zero",
    "range_bar": "type: bar with y_start, so the bar runs from y_start to y instead of from zero",
    "waterfall": (
        "type: bar with y_start from a running total the query computes, "
        "color: on the query's own direction column pinned with board "
        "category_colors, and style.overlap: full so each step's bar fills "
        "its band instead of splitting into a grouped slot"
    ),
    "bridge_chart": (
        "type: bar with y_start from a running total the query computes, "
        "color: on the query's own direction column pinned with board "
        "category_colors, and style.overlap: full so each step's bar fills "
        "its band instead of splitting into a grouped slot"
    ),
    "candlestick": (
        "type: bar with y_start: low and y: high as a thin wick, plus a "
        "layers: bar with y_start: open and y: close as the body. Same-color "
        "convention: color: on the query's session column on both the base "
        "bar and the body layer, with style.marks.bar.border.width: 0 to drop "
        "the knockout outline and border.radius: 0 for square candles. "
        "Ink-outline convention: no color: on the base "
        "bar (an ink palette slot), color: on the body layer alone, and "
        "style.marks.bar.border in ink"
    ),
    "ohlc": (
        "type: bar with y_start: low and y: high as a thin wick, plus a "
        "layers: bar with y_start: open and y: close as the body. Same-color "
        "convention: color: on the query's session column on both the base "
        "bar and the body layer, with style.marks.bar.border.width: 0 to drop "
        "the knockout outline and border.radius: 0 for square candles. "
        "Ink-outline convention: no color: on the base "
        "bar (an ink palette slot), color: on the body layer alone, and "
        "style.marks.bar.border in ink"
    ),
    "stock_chart": (
        "type: bar with y_start: low and y: high as a thin wick, plus a "
        "layers: bar with y_start: open and y: close as the body. Same-color "
        "convention: color: on the query's session column on both the base "
        "bar and the body layer, with style.marks.bar.border.width: 0 to drop "
        "the knockout outline and border.radius: 0 for square candles. "
        "Ink-outline convention: no color: on the base "
        "bar (an ink palette slot), color: on the body layer alone, and "
        "style.marks.bar.border in ink"
    ),
    "gantt": (
        "type: bar with a date y and a date y_start, style.orientation: "
        "horizontal so time runs left to right, keeping the query's row "
        "order rather than the usual value-descending default"
    ),
}

# Shapes dbt charts cannot draw at all. Naming them is the half a recipe cannot
# cover: without it an author settles for the nearest tag, which validates clean
# and renders the wrong chart.
_UNSUPPORTED_CHART_SHAPES = frozenset(
    {
        "sankey",
        "treemap",
        "violin",
        "funnel",
        "gauge",
        "radar",
        "sunburst",
        "chord",
        "marimekko",
        "spider",
        "alluvial",
        "word cloud",
        "network",
    }
)

# Both tables are keyed for reading; lookups go through the normalized forms so a
# noun is spelled once at the source of truth.
_RECIPE_BY_NORMALIZED_NOUN = {
    _normalize_shape_noun(noun): recipe for noun, recipe in _CHART_SHAPE_RECIPES.items()
}
_NORMALIZED_UNSUPPORTED_SHAPES = frozenset(
    _normalize_shape_noun(noun) for noun in _UNSUPPORTED_CHART_SHAPES
)


def _format_chart_type_discriminator_error(
    err: dict[str, Any],
    field_path: list[str],
) -> str | None:
    """Return clean chart-type diagnostics for chart discriminator failures."""
    if err.get("type") != "union_tag_not_found":
        return None
    if field_path[0:1] != ["charts"] or len(field_path) < 2:
        return None
    input_value = err.get("input")
    if not isinstance(input_value, dict):
        return None
    invalid_type = input_value.get("type")

    valid_chart_types = get_valid_chart_types()
    supported = ", ".join(valid_chart_types)
    if not isinstance(invalid_type, str):
        return f"Missing required field 'type'. Supported chart types: {supported}."

    # A known shape noun beats a fuzzy tag guess: both branches below answer the
    # question the author actually asked, so neither falls through to "did you mean".
    noun_keys = _shape_noun_keys(invalid_type)
    recipe = next(
        (r for k in noun_keys if (r := _RECIPE_BY_NORMALIZED_NOUN.get(k))), None
    )
    if recipe:
        return (
            f"Unknown chart type {invalid_type!r}. dbt charts draws that shape as "
            f"{recipe}. Run `dct docs charts` for the chart reference. "
            f"Supported chart types: {supported}."
        )
    if any(k in _NORMALIZED_UNSUPPORTED_SHAPES for k in noun_keys):
        return (
            f"Unknown chart type {invalid_type!r}. dbt charts cannot draw that shape "
            f"today. Supported chart types: {supported}."
        )

    suggestion = suggest_similar_value(invalid_type, valid_chart_types)
    if suggestion:
        return (
            f"Unknown chart type {invalid_type!r}. Did you mean {suggestion!r}? "
            f"Supported chart types: {supported}."
        )
    return f"Unknown chart type {invalid_type!r}. Supported chart types: {supported}."
