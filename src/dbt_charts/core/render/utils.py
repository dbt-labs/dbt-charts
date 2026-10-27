"""Shared utility functions for the render module.

This module contains common utilities used across multiple render modules
to avoid code duplication.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from dbt_charts.core.utils import Rows

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import ToneLiteral
    from dbt_charts.core.compile.models.style.theme import KpiTonesStyle


def resolve_tone_color(tone: ToneLiteral, tones: KpiTonesStyle) -> str:
    """Resolve a semantic tone name to its theme-provided color.

    The single tone->color resolver shared by KPI value/glyph rendering and
    table conditional-formatting glyphs, so both surfaces read tone hexes
    from the same theme-provided ``KpiTonesStyle`` rather than duplicating
    the lookup. Both callers guard ``None`` before delegating, so ``tone``
    is always an authored tone name here.
    """
    return getattr(tones, tone)


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
