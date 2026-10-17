"""Shared utility functions for the render module.

This module contains common utilities used across multiple render modules
to avoid code duplication.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from dbt_charts.core.utils import Rows

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import FontStyle, ToneLiteral
    from dbt_charts.core.compile.models.style.theme import KpiTonesStyle


def font_style_to_mark(font: FontStyle) -> dict[str, str | float]:
    """Copy non-None ``FontStyle`` fields into a VL text-mark dict fragment.

    The one family/size/weight/style/color → VL-mark copier shared by every
    emitter that paints a ``FontStyle`` onto a text mark (support-table
    row/label text). ``font.color`` lands on the mark's ``fill``.
    """
    mark: dict[str, str | float] = {}
    if font.family is not None:
        mark["font"] = font.family
    if font.size is not None:
        mark["fontSize"] = font.size
    if font.weight is not None:
        mark["fontWeight"] = font.weight
    if font.style is not None:
        mark["fontStyle"] = font.style
    if font.color is not None:
        mark["fill"] = font.color
    return mark


def resolve_tone_color(tone: ToneLiteral, tones: KpiTonesStyle) -> str:
    """Resolve a semantic tone name to its theme-provided color.

    The single tone->color resolver shared by KPI value/glyph rendering and
    table conditional-formatting glyphs, so both surfaces read tone hexes
    from the same theme-provided ``KpiTonesStyle`` rather than duplicating
    the lookup. Both callers guard ``None`` before delegating, so ``tone``
    is always an authored tone name here.
    """
    return getattr(tones, tone)


def is_integer_key_value(value: Any) -> bool:
    """Return True when a runtime value can join an integer-valued lookup key."""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return False
        if stripped.isdigit():
            return True
        try:
            parsed = float(stripped)
        except ValueError:
            return False
        return parsed.is_integer()
    return False


from dbt_charts.core.text.humanize import slug_to_text as slug_to_text  # re-exported


def normalize_scalar_for_json(value: Any) -> Any:
    """Normalize a single value for JSON serialization and rendering.

    Converts Decimal to float, preserves lists/tuples, and stringifies
    other non-JSON-serializable types (e.g. ``datetime.date``/``datetime``
    values from a SQL adapter, which are not JSON-native).
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float, str, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    return str(value)


def normalize_data_types(data: Rows) -> Rows:
    """Normalize data types for JSON serialization and rendering.

    Converts Decimal to float, preserves lists/tuples, and stringifies
    other non-JSON-serializable types.
    """
    return [
        {key: normalize_scalar_for_json(value) for key, value in row.items()}
        for row in data
    ]


# What a categorical domain value can be once ``normalize_scalar_for_json``
# has run: the JSON scalars it emits, minus the null
# ``ordered_distinct_values`` drops. Narrower than ``object`` on purpose —
# these values are compared, hashed, and serialized into a scale domain or a
# Vega filter literal, and nothing else is ever done with them.
DomainValue = str | int | float | bool


def ordered_distinct_values(rows: Rows, field: str) -> list[DomainValue]:
    """Ordered distinct non-null values of ``field`` across ``rows`` (first-seen).

    Normalized the same way as any other data reaching the VL spec
    (``normalize_data_types``) — these values land directly in ``scale.domain``
    and in Vega filter expressions, never through that normalizer, so a raw
    ``datetime.date``/``Decimal`` value here would otherwise reach vl_convert's
    JSON serialization unconverted.
    """
    ordered: dict[DomainValue, None] = {}
    for row in rows:
        value = row.get(field)
        if value is not None:
            ordered.setdefault(normalize_scalar_for_json(value), None)
    return list(ordered)
