"""Render-local intermediate chart representation.

``ChartSpec`` is produced by a family emitter and mutated by the ``ChartFeature``
pipeline before being assembled to Vega-Lite by ``assemble_final_vl()``
(see ``translate.py``).  Emitters write Vega-Lite encoding and mark names
directly — ChartSpec is a VL-shaped builder intermediate, not a dbt charts-native
vocabulary.  The ``"geoshape"`` sentinel is not a VL mark; it drives composition
shape detection in the assembler.

This is deliberately a mutable ``dataclass``, not a frozen Pydantic model — features
accumulate mutations (overlay layers, config overrides) before the spec is sealed for
assembly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
    from dbt_charts.core.compile.models.style.theme import TitleStyle


@dataclass(frozen=True)
class RenderBox:
    """Render-time layout slot geometry for one chart emission.

    Both dimensions are the extent the chart will actually render at. When the
    caller has no explicit height (``height: null`` — "let Vega auto-size
    vertically"), it resolves the height Vega-Lite itself will use from the
    theme before constructing the box, so consumers never have to model the
    absent case or invent a fallback.
    """

    width: float
    height: float
    # How many facet panels each dimension was divided across to get
    # ``width`` / ``height`` — 1 when nothing was divided. Chrome outside the
    # plot (title block, view padding) is a whole-chart cost paid once, so a
    # panel owes only its share of it; ``axis_title_budget`` divides by these.
    # ``panel_rows`` is 1 even on a row-faceted chart when no height was
    # authored: VL then falls back to ``config.view.continuousHeight``, which
    # is already a per-unit height, so nothing was divided.
    panel_rows: int = 1
    panel_cols: int = 1


@dataclass
class ChartSpec:
    """Mutable render-local intermediate produced by an emitter and mutated by features.

    Attributes:
        mark: VL mark name (``"circle"``) OR the structural sentinel
            ``"geoshape"`` that drives composition shape detection in
            ``assemble_final_vl``.  Emitters write VL names directly — no dbt
            charts-native mark vocabulary.
        encoding: Channel name → encoding config (VL keys).
        layers: Geo composition layers (background geometry, choropleth
            overlay, point layer).  Empty for a single-mark map.
        config: Vega-Lite ``config`` block passthrough.  Only VL ``config``
            object keys belong here — no hint-bag entries.
        mark_props: Extra mark-level VL properties merged into the mark object
            (e.g. ``{"innerRadius": 90}`` for donut charts).  When non-empty,
            ``assemble_final_vl`` emits ``{"mark": {"type": <vl_mark>, ...}}``
            instead of a bare mark string.
        projection: Map projection name; emitted as ``{"projection": {"type": ...}}``
            by ``assemble_final_vl`` for geoshape/circle families.
        href_link: VL calculate expression string set by ``ClickInteractivityFeature``.
            ``assemble_final_vl`` injects ``{"calculate": href_link, "as": "__df_href__"}``
            into transforms and wires an href encoding.  ``None`` → no href.
        tooltip_description: Per-datum expression string set by
            ``NativeTooltipAffixFeature``. ``assemble_final_vl`` wires it to VL's
            native ``description`` channel as a ``{"value": {"expr": ...}}`` def
            (see ``translate.py::_apply_structured_tooltip``). ``description``
            fully replaces the mark's aria-label (no merge with
            channel-derived content — see ``emitters/_tooltip.py``).  ``None`` →
            no hover card, matching the ``None``-is-unset idiom of the sibling
            ``href_link`` field.
        geo_data: Geo data source block ``{"values": <document>, "format": {...}}`` for
            geoshape/map families.  Set by ``GeoshapeEmitter`` on the outer spec
            (no-data path) or on each layer (choropleth layered path).
            ``assemble_final_vl`` emits it as the VL ``data`` key.
        transforms: VL ``transform`` list. Set by ``GeoshapeEmitter`` on the choropleth
            overlay layer (lookup join).  Empty list → no transform key emitted.
    """

    mark: str
    encoding: dict[str, Any] = field(default_factory=dict)
    layers: list[ChartSpec] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    mark_props: dict[str, Any] = field(default_factory=dict)
    # Simple string name ("mercator") or full projection dict ({"type": "conic...", "center": [...]}).
    projection: str | dict[str, Any] | None = None
    href_link: str | None = None
    tooltip_description: str | None = None
    title: str | None = None
    subtitle: str | None = None
    title_font: ResolvedFontStyle | None = None
    background: str | None = None
    title_style: TitleStyle | None = None
    geo_data: dict[str, Any] | None = None
    transforms: list[dict[str, Any]] = field(default_factory=list)
    # Row data; set centrally by BoardRenderSession.emit_chart.
    # assemble_final_vl emits it as {"data": {"values": [...]}} in _base_spec.
    data: list[dict[str, Any]] | None = None
