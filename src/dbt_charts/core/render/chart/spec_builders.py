"""Shared Vega-Lite spec builder helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.text.case import apply_case

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import TitleShift
    from dbt_charts.core.compile.models.style.theme import PaddingStyle
    from dbt_charts.core.render.chart.artifacts import RenderArtifact
    from dbt_charts.core.text.case import CaseValue


def additive_padding(card_pad: float, chart_padding: PaddingStyle) -> dict[str, float]:
    """Add card_padding to per-family chart padding on each side independently.

    ``chart_padding`` is the per-family resolved padding — typically
    ``resolved_chart.layout_padding``, baked in at construction time. It
    already carries board → family fill via InheritSlot and any chart-local
    ``style.<family>.padding`` override merged in by ``build_chart_style_context``.
    Each side stacks ON TOP of card_padding so author-specified insets compose
    with the global card layout rather than replacing it. With no chart-local
    override and board default {0,0,0,0}, this collapses to a uniform
    card_pad on all four sides.
    """
    return {
        "left": card_pad + chart_padding.left,
        "right": card_pad + chart_padding.right,
        "top": card_pad + chart_padding.top,
        "bottom": card_pad + chart_padding.bottom,
    }


def bump_padding_bottom(spec: dict[str, Any], add_px: float) -> None:
    """Increase spec-level `padding.bottom` by add_px in place.

    Assumes the spec already carries a 4-key padding dict. Other sides are left alone.
    """
    padding = dict(spec["padding"])
    padding["bottom"] = float(padding.get("bottom", 0)) + add_px
    spec["padding"] = padding


def bump_padding_top(spec: dict[str, Any], add_px: float) -> None:
    """Increase spec-level `padding.top` by add_px in place.

    Symmetric counterpart to ``bump_padding_bottom`` — used when a strip
    is attached above the plot (``style.support_table.position: top``).
    """
    padding = dict(spec["padding"])
    padding["top"] = float(padding.get("top", 0)) + add_px
    spec["padding"] = padding


def bump_padding_left(
    spec: dict[str, Any],  # type-state: explicit_any — VL fragment
    add_px: float,
) -> None:
    """Increase spec-level `padding.left` by add_px in place.

    Sibling of ``bump_padding_top``/``bump_padding_bottom`` — used when a
    support_table value-column block is attached beside the plot
    (``style.support_table.position: left``).
    """
    padding = dict(spec["padding"])
    raw_left = padding.get("left", 0)  # type-state: silent_fallback — additive read
    padding["left"] = float(raw_left) + add_px
    spec["padding"] = padding


def bump_padding_right(
    spec: dict[str, Any],  # type-state: explicit_any — VL fragment
    add_px: float,
) -> None:
    """Increase spec-level `padding.right` by add_px in place.

    Symmetric counterpart to ``bump_padding_left`` — used for
    ``style.support_table.position: right``.
    """
    padding = dict(spec["padding"])
    raw_right = padding.get("right", 0)  # type-state: silent_fallback — additive read
    padding["right"] = float(raw_right) + add_px
    spec["padding"] = padding


# Vega's own default for title.offset, which applies when neither the title block
# nor config.title sets one.
VEGA_TITLE_OFFSET = 4
# Vega's own default for the gap between a title and its subtitle, which applies
# because the spec never sets subtitlePadding; the subtitle line is as tall as
# its font size because the spec never sets subtitleLineHeight either.
VEGA_SUBTITLE_PADDING = 3


def shift_chart_title(spec: VLDict, shift: TitleShift) -> None:
    """Move a titled spec's plot down ``body_dy`` and its title down about ``title_dy``.

    Top padding moves title and plot together. ``title.offset`` then moves the
    plot alone, by whole pixels (``round(body_dy - title_dy)``) with the padding
    carrying the remainder, so the plot lands exactly ``body_dy`` lower and the
    baseline within half a pixel of ``title_dy``. An hconcat spec (endpoint-label
    rail) holds its title on pane 0; the panes share a y scale and align by view,
    so the rail follows. The spec must already carry a title block and 4-key
    padding.
    """
    if shift.is_zero:
        return
    gap_extra = round(shift.body_dy - shift.title_dy)
    bump_padding_top(spec, shift.body_dy - gap_extra)
    title_block = (spec["hconcat"][0] if "hconcat" in spec else spec)["title"]
    if "offset" in title_block:
        current_offset = title_block["offset"]
    else:
        # A null themed offset means Vega's own default.
        current_offset = spec["config"]["title"].get(
            "offset", VEGA_TITLE_OFFSET
        )  # type-state: silent_fallback — a null themed offset is Vega's own default
    title_block["offset"] = current_offset + gap_extra


def shift_artifact_title(artifact: RenderArtifact, shift: TitleShift) -> None:
    """``shift_chart_title`` for a ``vega_spec`` artifact.

    An ``svg`` artifact came from a hand-drawn family that shifted its own title.
    """
    if shift.is_zero or artifact.kind != "vega_spec":
        return
    assert isinstance(artifact.payload, dict)
    shift_chart_title(artifact.payload, shift)


def set_chart_title(
    spec: dict[str, Any],
    title: str | None,
    subtitle: str | None = None,
    *,
    case: CaseValue | None = None,
) -> None:
    """Apply a standard title block when a title is present.

    ``case`` is a plain title-case value (``chart.title_style.font.case``),
    not a style bag — title-case is VL-presentation-only: ``chart.title``
    itself stays raw because it also backs non-VL consumers (e.g. the
    ``data-chart-title`` wire attribute) that emit the authored text as-is.
    Subtitle is not case-transformed — chart subtitles have always been
    emitted raw.
    """
    if title and case is not None and case != "none":
        title = apply_case(title, case)
    if title or subtitle:
        title_block: dict[str, Any] = {"text": title or ""}
        if subtitle:
            title_block["subtitle"] = subtitle
        spec["title"] = title_block


def tooltip_entry(
    field: str,
    field_type: str,
    *,
    title: str | None = None,
    format: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a tooltip field definition."""
    entry: dict[str, Any] = {"field": field, "type": field_type}
    if title is not None:
        entry["title"] = title
    if format is not None:
        entry["format"] = format
    entry.update(extra)
    return entry
