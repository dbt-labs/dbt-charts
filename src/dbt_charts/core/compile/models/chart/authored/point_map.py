"""Authored point_map / bubble_map chart."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.constraints import Constraint, Forbids, ValueIn
from dbt_charts.core.compile.models.markers import Channel
from dbt_charts.core.compile.models.style.authored import PointMapChartStylePatch

from ._base import BasemapConfig, _GeoChartFields


class PointMapChart(_GeoChartFields):
    """Authored patch for point_map and bubble_map charts; the two type spellings are synonyms."""

    model_config = ConfigDict(extra="forbid")

    # `collapse` emits VL's native `size: {aggregate: "count"}`: it binds the
    # size channel, and VL groups it by every other non-aggregated encoded
    # field, so a `color` column re-creates the pile `collapse` removes.
    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        Forbids(
            ("size",),
            "{type}: `collapse: true` and `size: <column>` both bind "
            "the mark's size channel — they cannot be combined. Drop `size` "
            "to size collapsed points by count, or drop `collapse` to keep "
            "`size` as a bubble_map measure encoding.",
            when=(ValueIn("collapse", (True,)),),
        ),
        Forbids(
            ("color",),
            "{type}: `collapse: true` and `color: <column>` cannot be "
            "combined — VL's size:{{aggregate: count}} groups by every "
            "other non-aggregated encoded field, so an authored `color` "
            "reintroduces the exact pile `collapse` exists to remove. "
            "Drop `color` to keep collapsed counts, or drop `collapse` "
            "and aggregate per-category counts in SQL (`GROUP BY <lat>, <lon>, "
            "<category>` producing a count column), then use `bubble_map` "
            "with `size: <count column>, color: <category column>`.",
            when=(ValueIn("collapse", (True,)),),
        ),
    )

    type: Annotated[
        Literal["point_map", "bubble_map"],
        Field(description="Selects the chart family."),
    ]
    # None = lat/lon not specified. point_map/bubble_map-only: geoshape has no
    # render support for them, so they live here rather than on the shared
    # _GeoChartFields base — extra="forbid" rejects them on geoshape for free.
    latitude: Annotated[
        str | None,
        Channel(),
        Field(
            default=None,
            description="Column containing latitude values for point/bubble maps.",
        ),
    ]
    longitude: Annotated[
        str | None,
        Channel(),
        Field(
            default=None,
            description="Column containing longitude values for point/bubble maps.",
        ),
    ]
    # None = no size encoding; bubbles use a fixed radius.
    size: Annotated[
        str | None,
        Channel(),
        Field(
            default=None,
            description="Quantitative column that scales point area. Mutually exclusive with `collapse`.",
        ),
    ]
    # Opt-in aggregation: collapses marks sharing an exact latitude/longitude
    # into one mark sized by count (area-proportional). Mutually exclusive
    # with `size` — both bind the mark's size channel. Never a silent default;
    # the render-time co-located-marks warning is the automatic half of this
    # affordance pair.
    collapse: Annotated[
        bool,
        Field(
            default=False,
            description="Collapse marks sharing an exact latitude/longitude into one mark sized by count. Mutually exclusive with `size` and with any color channel.",
        ),
    ]
    basemap: Annotated[
        BasemapConfig | None,
        Field(default=None, description="Styled geographic background layer."),
    ]
    style: Annotated[
        PointMapChartStylePatch | None,
        Field(default=None, description="Appearance overrides for this chart alone."),
    ]
