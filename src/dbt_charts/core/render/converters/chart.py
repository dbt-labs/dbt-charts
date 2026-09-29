"""Chart output conversion helpers."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.style.theme import TitleStyle
from dbt_charts.core.render.board_links import get_link_context, resolve_href
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.artifacts import RenderArtifact
from dbt_charts.core.render.chart.emitters._tooltip import (
    MUTED,
    ROLE_ORDER,
    ROLE_SERIES,
)
from dbt_charts.core.render.chart.endpoint_label_overflow import (
    EndpointLabelGapOverflow,
    record_endpoint_label_gap_overflow,
)
from dbt_charts.core.render.chart.features.endpoint_labels import (
    recascade_endpoint_labels,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_CONCAT_OVERSHOOT_NONPOSITIVE,
    ERR_FORMAT_CONVERTER_UNAVAILABLE,
)
from dbt_charts.core.render.chart.title_overflow import (
    apply_title_overflow_to_spec,
    fix_title_alignment,
)
from dbt_charts.core.render.converters.pdf import to_pdf
from dbt_charts.core.render.converters.png import to_png
from dbt_charts.core.render.errors import FormatError
from dbt_charts.core.render.font_support import register_vl_convert_fonts
from dbt_charts.core.render.svg_cache import active_svg_cache, svg_cache_key
from dbt_charts.core.render.svg_utils import authored_kind_attr, escape_attr

# Strip sentinel prefix from vl_convert-rendered chart href <a> elements.
# vl_convert uses xlink:href and mangles relative URLs, so we embed a sentinel
# prefix that we can detect and strip here. The prefix is defined as
# _HREF_SENTINEL in render/chart/features/click_interactivity.py — change both.
_SENTINEL_HREF_RE = re.compile(r'<a xlink:href="http://dct\.invalid([^"]*)"')


def _fix_chart_click_hrefs(svg: str) -> str:
    """Convert sentinel xlink:href to plain href in SVG <a> elements.

    Converts ``<a xlink:href="http://dct.invalid/path?q=v">``
    to ``<a href="/path?q=v">`` so variables.js can intercept variable-update
    clicks and the browser can follow navigation clicks directly.

    When a link context is active (board resolver), board-root paths are
    rewritten for the current runtime (serve vs Cloud). Query-string-only
    links (?var=value) pass through unchanged — they are in-page variable
    updates intercepted by variables.js, not cross-board navigation.
    """
    ctx = get_link_context()

    def _replace(m: re.Match[str]) -> str:
        # vl_convert's own SVG serialization already XML-escaped this
        # attribute value (e.g. "&" -> "&amp;" between query params) --
        # unescape before resolve_href (which expects a raw URL) and before
        # escape_attr re-escapes it, or a two-param link doubles to &amp;amp;.
        url = html.unescape(m.group(1))
        if ctx is not None:
            url = resolve_href(url, ctx)
        return f'<a href="{escape_attr(url)}"'

    return _SENTINEL_HREF_RE.sub(_replace, svg)


# Inject stroke-linecap="round" on legend-symbol <path> elements emitted by
# vl_convert. Vega-Lite has no spec-level surface for this (probed
# legend.symbolStrokeCap, config.legend.symbolStrokeCap, config.style.symbol.
# strokeCap, config.mark.strokeCap — all silently dropped), so the legend's
# dash segments render with butt caps while the chart lines use round caps
# (LineStyle theme default). The mismatch is most visible when the dash
# palette contains a dotted entry: round caps render 0-length dashes as
# circular dots, butt caps render them as nothing. Match the chart's cap by
# stamping round onto the legend paths during post-processing — but only on
# charts that actually use the strokeDash encoding (gated by the caller),
# so that charts with no dashes keep producing byte-identical SVG output.
_LEGEND_SYMBOL_LINECAP_RE = re.compile(
    r'(class="[^"]*role-legend-symbol[^"]*"[^>]*><path)(?![^/]*stroke-linecap)([^/]*?)(/>)'
)


def _fix_legend_symbol_linecap(svg: str) -> str:
    """Stamp ``stroke-linecap="round"`` onto legend-symbol paths."""
    return _LEGEND_SYMBOL_LINECAP_RE.sub(r'\1 stroke-linecap="round"\2\3', svg)


# vl-convert mints its own ids for generated defs -- clipPath (`clip3`) and
# linearGradient (`gradient_0`) -- with a counter scoped to a single
# vegalite_to_svg call, not to the board or the process. Two charts rendered
# in separate calls in one process can mint the same low-numbered ids; two
# BOARDS rendered in separate processes (docs' per-theme preview, a Cloud
# page stacking more than one board's SVG) both start that counter at zero
# too, so a chart-id suffix alone is not enough once two processes' output
# shares a page. `_namespace_svg_ids` below suffixes every vl-convert id with
# a content token *and* the chart id so neither collision survives. Scoped to
# vl-convert's own id families only.
_VLC_ID_FAMILY = r"(?:clip|gradient_)\d+"
_VLC_ID_DEF_RE = re.compile(rf'id="({_VLC_ID_FAMILY})"')
# Anchored to the reference forms vl-convert actually emits -- url(#id),
# xlink:href="#id", href="#id" -- rather than a bare "#id" anywhere in the
# document, so a data value or aria-label that happens to contain "#clip3"
# text is never rewritten. The negative lookahead keeps a second application
# a no-op instead of re-suffixing an already-namespaced id.
_VLC_ID_REF_RE = re.compile(
    rf'(url\(#|(?:xlink:href|href)="#)({_VLC_ID_FAMILY})(?![\w-])'
)
# chart_id lands inside a bare, unquoted url(#...) FuncIRI token, not just an
# XML attribute -- HTML-escaping is not enough there: a space or `)` produces
# a reference the renderer cannot resolve, silently dropping the clip.
# Nothing slugifies an authored `charts:` key before it reaches render, so
# every character outside this set is replaced with `_`.
_UNSAFE_ID_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]")


def safe_svg_id(chart_id: str) -> str:
    """The chart-id half of the id-namespace suffix ``_namespace_svg_ids``
    appends to every vl-convert-minted id: ``chart_id`` sanitized to
    FuncIRI-safe characters, plus a short digest suffix when sanitizing was
    lossy. The suffix's other half — a content token — is computed by
    ``_namespace_svg_ids`` itself and placed *before* this one, so this
    function's own output always lands at the very end of the id.

    Public (not ``_``-prefixed) because ``chart_svg_dev.contract`` — the
    dev-only golden-sweep harness in ``libs/chart-svg`` — imports it to
    classify a Rust-rendered chart's ids as namespaced or not, the same way
    production namespaces them.
    """
    safe_id = _UNSAFE_ID_CHARS_RE.sub("_", chart_id)
    if safe_id != chart_id:
        # Sanitizing is many-to-one (`"a b"` and `"a_b"` both land on `a_b`) --
        # append a short digest of the raw id so two charts that only collide
        # after sanitizing still don't share a suffix. Never taken for a
        # chart_id that was already FuncIRI-safe, so the common case (and
        # every id in the current golden corpus) is untouched.
        digest = hashlib.md5(  # noqa: S324 — non-cryptographic, stable SVG id
            chart_id.encode(), usedforsecurity=False
        ).hexdigest()[:8]
        safe_id = f"{safe_id}-{digest}"
    return safe_id


def _namespace_svg_ids(svg: str, chart_id: str) -> str:
    """Suffix every vl-convert-minted id (and its references) with a content
    token plus ``chart_id``, so ids stay unique across independently-rendered
    charts on the same board *and* across independently-rendered processes
    whose output lands on one page (docs' per-theme board preview, a Cloud
    surface stacking more than one board's SVG).

    The token is a short sha256 of this chart's own SVG fragment with
    vl-convert's own id *digits* dropped first. Those digits are vl-convert's
    counter position, not content -- it varies between renders of the exact
    same chart (even in one process), so hashing the raw text would make two
    renders of one chart disagree and break every same-run comparison. Two
    charts that render byte-identical fragments (same theme, same data)
    legitimately share a token: their defs are identical, so a reference
    resolving to either paints the same -- the same reasoning as mdsvg's own
    content-hashed class scope (``_compute_class_prefix``). Any real content
    difference changes the canonicalized text and mints a new token.

    The token goes *between* the id family and ``chart_id`` -- never after --
    so ``chart_svg_dev.contract``'s ``endswith(f"-{safe_svg_id(chart_id)}")``
    classification (and its Rust mirror, ``contract.rs``) needs no change.
    """
    strip_digits = str.maketrans("", "", "0123456789")
    canon = _VLC_ID_DEF_RE.sub(
        lambda m: f'id="{escape_attr(m.group(1).translate(strip_digits))}"', svg
    )
    canon = _VLC_ID_REF_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2).translate(strip_digits)}", canon
    )
    token = hashlib.sha256(canon.encode()).hexdigest()[:8]
    safe_id = f"{token}-{safe_svg_id(chart_id)}"
    svg = _VLC_ID_DEF_RE.sub(
        lambda m: f'id="{escape_attr(m.group(1))}-{escape_attr(safe_id)}"', svg
    )
    return _VLC_ID_REF_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}-{safe_id}", svg)


# Vega's own role-title-text / role-title-subtitle classes are a genuine 1:1
# signal for the chart's title:/subtitle: keys (unlike mdsvg's scoped md-<hash>-heading, which the
# renderer also emits for prose headings) — transcribed here into the
# data-authored-kind leaf vocabulary rather than left for Cloud to read the
# foreign class directly. Selectors.md's "data-* for JS selection, never a
# class" rule carves out core-generated dbt-* classes as safe to query; these
# are vl_convert's, not ours, so the carve-out does not reach them and the
# dependency stays inside the module that already owns vl_convert output.
_TITLE_KIND_RE = re.compile(r'(class="mark-text role-title-text")')
_SUBTITLE_KIND_RE = re.compile(r'(class="mark-text role-title-subtitle")')


def _stamp_chart_title_kind(svg: str) -> str:
    """Tag the chart's own title/subtitle text runs with their leaf kind."""
    title_attr = authored_kind_attr("title")
    subtitle_attr = authored_kind_attr("subtitle")
    svg = _TITLE_KIND_RE.sub(lambda m: f"{m.group(1)}{title_attr}", svg)
    return _SUBTITLE_KIND_RE.sub(lambda m: f"{m.group(1)}{subtitle_attr}", svg)


_AXIS_TITLE_GROUP = '<g class="mark-text role-axis-title" pointer-events="none">'


def _stamp_axis_title_kinds(svg: str, kind_by_axis: dict[str, str] | None) -> str:
    """Tag the first X- and Y-axis title runs with the authored label key
    each axis actually carries.

    ``kind_by_axis`` maps vl_convert's aria axis name ("X"/"Y") to the
    authored key ("x_label"/"y_label") and is decided where the resolved
    chart is in hand (`$df_axis_label_kinds` in vega_lite.py) — the aria
    text names the Vega layout channel, which a horizontal orientation or a
    facet decouples from the authored key. No mapping, no stamping: an axis
    title that is not editable beats one that edits the wrong key. First
    per axis only, so a dual-scale layer's secondary title never points an
    edit at the chart's own key. ``pointer-events`` is left exactly as
    painted — Cloud re-enables it in CSS — so stamped and unstamped output
    normalize identically for the visual goldens.
    """
    if not kind_by_axis:
        return svg
    out: list[str] = []
    pos = 0
    done: set[str] = set()
    while (hit := svg.find(_AXIS_TITLE_GROUP, pos)) != -1:
        x_at = svg.rfind('aria-label="X-axis', 0, hit)
        y_at = svg.rfind('aria-label="Y-axis', 0, hit)
        axis = "X" if x_at > y_at else "Y" if y_at != -1 else None
        end = hit + len(_AXIS_TITLE_GROUP)
        kind = kind_by_axis.get(axis) if axis is not None else None
        if kind is None or kind in done:
            out.append(svg[pos:end])
        else:
            done.add(kind)
            out.append(svg[pos:hit])
            out.append(
                '<g class="mark-text role-axis-title" pointer-events="none"'
                f"{authored_kind_attr(kind)}>"
            )
        pos = end
    out.append(svg[pos:])
    return "".join(out)


def _stamp_value_label_layers(svg: str, layer_indices: list[int], chart_id: str) -> str:
    """Tag each value-label text-mark GROUP with a JS selection hook.

    ``layer_indices`` (``$df_value_label_layers``, stamped in vega_lite.py's
    ``_stamp_value_label_layer_sentinel``) names the ``vl["layer"]`` position
    of every text sublayer that is genuinely a mark's own printed value (empty
    when the chart has none; the sentinel is only written when one exists) --
    vl_convert names a flat layer array's mark group by that same position,
    so the position IS the join key; no scenegraph correspondence needed here,
    unlike ``_stamp_legend_series_key`` above. But the class string vl_convert
    writes around that position is not one fixed shape: a plain unit spec gets
    the bare ``layer_N_marks``, a facet's inner spec gets ``child_layer_N_marks``
    (repeated once per panel -- every occurrence must be stamped, not just the
    first), and the endpoint-label right-pane/top-rail concat wrapper
    (``translate.py``'s ``_wrap_hconcat_label_pane``/``_wrap_vconcat_label_rail``)
    qualifies its main pane's own groups ``concat_N_layer_N_marks``. Facet and
    that concat wrapper never nest -- not because either module checks for
    the other (structurally they would), but because
    ``ERR_MULTIPLES_ENDPOINT_LABELS`` (raised in ``features/facet.py`` and
    ``features/mirror_axis.py``) refuses the small-multiples + endpoint-label
    combination outright. If that guard is ever relaxed, the prefix regex
    above goes incomplete.

    chart_interactivity.js's ``recedableMarks()`` reads this attribute to
    scope its twin search to marked layers only -- a bar's own printed value
    still recedes with its bar, while a pie's center total and outside
    labels (never marked; built directly in emitters/pie.py) stay lit
    unconditionally. See that function's docstring.

    A layer index with zero matching groups is a wiring bug, not a
    legitimate empty case: vl_convert emits a layer's mark-text group at that
    index even when the layer's own filter drops every row (self-closing, no
    children) -- ``test_stamp_value_label_layers_does_not_raise_on_a_real_zero_row_layer``
    (``tests/core/render/test_chart_converter.py``) pins exactly that shape,
    a single-category ``curve: step`` line whose only labeled row is the band
    edge.
    """
    for index in layer_indices:
        marker_re = re.compile(
            rf'class="mark-text role-mark (?:child_|concat_\d+_)?layer_{index}_marks"'
        )
        matches = list(marker_re.finditer(svg))
        if not matches:
            raise ChartDataError(
                f"value-label layer {index} has no matching mark-text group in "
                "the rendered SVG (checked the bare, facet child_, and concat_N_ "
                "prefixes) -- the value-label sentinel and vl_convert's own group "
                "naming have diverged",
                chart_id=chart_id,
            )
        out: list[str] = []
        pos = 0
        for match in matches:
            out.append(svg[pos : match.end()])
            out.append(' data-dbt-value-label="true"')
            pos = match.end()
        out.append(svg[pos:])
        svg = "".join(out)
    return svg


# Joins a mark to its legend entry on the raw series value baked into the
# structured tooltip's ROLE_SERIES row, never on rendered/truncated text.
# Marks already resolve their own series value correctly today via
# chart_interactivity.js's parseAriaLabel/markSeriesEntry (the mark's own
# aria-label is never truncated) -- only a rendered legend LABEL is
# unreliable, since Vega ellipsizes it past labelLimit. So only
# .role-legend-label groups get stamped here.
_ARIA_LABEL_RE = re.compile(r'aria-label="([^"]*)"')
_LEGEND_LABEL_GROUP_RE = re.compile(r'<g class="[^"]*role-legend-label[^"]*"[^>]*>')


def _role_marker_present(svg: str, marker: str) -> bool:
    """True when ``marker`` genuinely acts as a ROLE_* role marker inside
    some aria-label on ``svg`` -- not merely present anywhere in the document.

    ROLE_SERIES/ROLE_ORDER are zero-width Unicode codepoints reserved for
    this role-tagging scheme, but the codepoints themselves are not reserved
    to us: ROLE_ORDER is U+200C ZERO WIDTH NON-JOINER, which occurs natively
    in real text (Persian/Indic scripts). Mirrors chart_interactivity.js's
    parseAriaLabel.
    """
    for aria_label in _ARIA_LABEL_RE.findall(svg):
        for raw in aria_label.split(";"):
            pair = raw.strip()
            if not pair:
                continue
            if pair[0] == MUTED:
                pair = pair[1:]
            if pair[:1] == marker:
                return True
    return False


def _legend_label_texts(
    scenegraph: Any,  # type-state: explicit_any — untyped vl-convert scenegraph JSON
) -> list[str] | None:
    """Collect each discrete legend entry's full text, in scenegraph order.

    A discrete (nominal) legend gives each series its own ``legend-label``
    node with exactly one ``items`` entry. A continuous/gradient COLOR
    legend (numeric or boolean color field) instead renders ONE
    ``legend-label`` node whose ``items`` holds every tick label as a
    separate instance -- there is no per-series text to extract there, and
    no series identity to join a mark to. Returns None on encountering that
    shape, signaling the caller to skip stamping entirely rather than
    mis-stamp a tick label as a series value. A continuous *size* legend
    renders one node per tick with one item each -- the same shape as a
    discrete entry -- so it is indistinguishable from the discrete case at
    this gate and would be (wrongly) treated as one.
    """
    texts: list[str] = []
    gradient_legend = False

    def _collect(
        node: Any,  # type-state: explicit_any — untyped vl-convert scenegraph JSON
    ) -> None:
        nonlocal gradient_legend
        if gradient_legend:
            return
        if isinstance(node, dict):
            if node.get("role") == "legend-label":
                items = node.get(
                    "items", []
                )  # type-state: silent_fallback — a legend-label node always carries items; defensive read against untyped foreign JSON, not masked bad input
                if len(items) != 1:
                    gradient_legend = True
                    return
                texts.append(items[0]["text"])
            for value in node.values():
                _collect(value)
        elif isinstance(node, list):
            for value in node:
                _collect(value)

    _collect(scenegraph)
    return None if gradient_legend else texts


def _stamp_legend_series_key(
    svg: str,
    spec: dict[str, Any],  # type-state: explicit_any — foreign VL JSON spec
    vlc: Any,  # type-state: explicit_any — vl_convert has no type stubs
    chart_id: str,
) -> str:
    """Stamp ``data-dbt-series`` on every ``.role-legend-label`` group with
    its full, untruncated text -- unobtainable from the SVG string alone
    once Vega has ellipsized it past ``labelLimit``. ``vegalite_to_scenegraph``
    on this SAME spec returns the pre-truncation text for each legend-label
    node's own ``items[0].text``; scenegraph/SVG element order match 1:1
    since vl-convert serializes the SVG from this same scenegraph.

    A no-op (unstamped legend labels) when the scenegraph call throws, or
    when the legend turns out to be continuous/gradient rather than discrete
    (see ``_legend_label_texts``) -- chart_interactivity.js's
    legendSeriesOrder() falls back to trimmed textContent for any element it
    finds with no stamped attribute, so this only degrades ordering back to
    the pre-fix behavior for those cases, never breaks it outright.
    """
    try:
        scenegraph = vlc.vegalite_to_scenegraph(spec)
    except Exception:  # noqa: BLE001, S110 — vl-convert throws untyped JS errors
        return svg

    texts = _legend_label_texts(scenegraph)
    if texts is None:
        return svg

    matches = list(_LEGEND_LABEL_GROUP_RE.finditer(svg))
    if len(matches) != len(texts):
        # vl-convert serializes the SVG from this exact scenegraph, so a
        # length mismatch means this module's own assumption about their
        # 1:1 correspondence broke.
        raise ChartDataError(
            f"legend-label scenegraph node count ({len(texts)}) does not match "
            f"role-legend-label SVG element count ({len(matches)})",
            chart_id=chart_id,
        )

    out: list[str] = []
    pos = 0
    for match, text in zip(matches, texts, strict=True):
        out.append(svg[pos : match.start()])
        out.append(
            f'{match.group(0)[:-1]} data-dbt-series="{escape_attr(text.strip())}">'
        )
        pos = match.end()
    out.append(svg[pos:])
    return "".join(out)


def _stamp_series_keys(
    svg: str,
    spec: dict[str, Any],  # type-state: explicit_any — foreign VL JSON spec
    vlc: Any,  # type-state: explicit_any — vl_convert has no type stubs
    chart_id: str,
) -> str:
    """Stamp a stable ``data-dbt-series`` join key on legend labels.

    Scoped to the fallback path: families that bake a ``ROLE_ORDER`` rank
    (see ``features/structured_tooltip.py``) already sort by that rank and
    never reach the legend join at all, so the gate no-ops for them,
    producing byte-identical SVG. Likewise a no-op when no series row
    exists, or when no legend actually rendered (e.g. a line using
    endpoint labels instead).
    """
    if (
        "role-legend-label" not in svg
        or not _role_marker_present(svg, ROLE_SERIES)
        or _role_marker_present(svg, ROLE_ORDER)
    ):
        return svg
    return _stamp_legend_series_key(svg, spec, vlc, chart_id)


def _spec_has_encoding(spec: dict[str, Any], channels: Iterable[str]) -> bool:
    """True when ``spec`` carries any of ``channels`` in an encoding.

    Recurses through ``spec["encoding"]``, ``spec["layer"]``, ``spec["hconcat"]``,
    and ``spec["vconcat"]`` at every depth — deep enough to catch the
    endpoint-label-rail wrapper that wraps a layered line chart inside
    ``hconcat[0]``, and any future nested composition.  Cheap gate used before
    SVG post-processors so charts that don't use the channel keep producing
    byte-identical SVG.
    """
    channel_set = frozenset(channels)

    def _has(node: dict[str, Any]) -> bool:
        if channel_set & node.get("encoding", {}).keys():
            return True
        for child in node.get("layer", []):
            if isinstance(child, dict) and _has(child):
                return True
        for child in node.get("hconcat", []):
            if isinstance(child, dict) and _has(child):
                return True
        for child in node.get("vconcat", []):
            if isinstance(child, dict) and _has(child):
                return True
        return False

    return _has(spec)


def _padding_side(spec: VLDict, side: str) -> float:
    """One side of a spec's root padding.

    Vega-Lite accepts either a 4-key dict or a single number standing for all
    four sides; both reach here from authored themes and from our own emitters.
    """
    padding = spec.get("padding")
    if isinstance(padding, dict):
        return float(padding.get(side, 0) or 0)
    if isinstance(padding, (int, float)):
        return float(padding)
    return 0.0


def _label_pane_mark_leaves(probe: VLDict, chart_id: str) -> list[VLDict]:
    """Text-mark leaves for the label pane in vl-convert's probe scenegraph.

    Fixed nesting confirmed against vl_convert-python 1.9.0's scenegraph shape
    for the two-pane endpoint-label hconcat: pane[1]'s ``concat_1_marks`` text
    group sits at ``items[0].items[1].items[0].items[0].items``.
    ``vegalite_to_scenegraph`` carries no ``scale``/``domain`` anywhere — this
    is pixel-space mark data only, which is exactly what a slope measurement
    needs.
    """
    try:
        return probe["scenegraph"]["items"][0]["items"][1]["items"][0]["items"][0][
            "items"
        ]
    except (KeyError, IndexError, TypeError) as exc:
        raise ChartDataError(
            f"could not locate endpoint-label marks in the probe scenegraph: {exc}",
            chart_id=chart_id,
        ) from exc


def _recascade_endpoint_label_pane(
    spec: VLDict,
    probe: VLDict,
    cascade: VLDict,
    chart_id: str,
    height_correction_ratio: float,
) -> None:
    """Rewrite the label pane's inline dataset with re-cascaded positions.

    Mechanical: reads the pre-probe (raw anchor) rows the label pane rendered,
    hands them plus this same probe's own label marks and the pane's
    height-correction ratio to the one pure cascade entry point in
    ``features/endpoint_labels.py``, and writes the returned positions back.
    All cascade math and pixel<->data conversion — including the slope's
    height-correction adjustment — lives in that module; this function moves
    data, it does not compute it.

    Runs when the spec carries the ``$df_endpoint_label_cascade`` sentinel *and*
    a probe was obtained. A spec with an attached ``support_table`` strip (no
    ``$df_target_height``) still qualifies — it re-cascades off this same probe
    with ``height_correction_ratio == 1.0``, since nothing shrank.

    Two paths skip it: the caller returns early when both size targets are None,
    and the probe's own ``except`` returns when ``vegalite_to_scenegraph``
    throws. Labels then keep their un-cascaded anchor positions. No case is
    known where the scenegraph call fails while the SVG render of the same spec
    succeeds, but the cost of that path is now all of the collision avoidance,
    not just the size correction — worth knowing before widening the catch.
    """
    series_field = cascade["series_field"]
    value_alias = cascade["value_alias"]
    pane = spec["hconcat"][1]
    # From the sentinel, not the pane: the pane's rows may have been spread
    # apart purely so the probe could measure the scale (translate.py's
    # _spread_for_measurement). These are the real endpoint values.
    anchors = {name: float(y) for name, y in cascade["anchors"]}
    emitted = {
        row[series_field]: float(row[value_alias]) for row in pane["data"]["values"]
    }
    result = recascade_endpoint_labels(
        anchors=anchors,
        pixel_gap=cascade["gap_px"],
        y_domain_min=cascade["y_domain_min"],
        y_domain_max=cascade["y_domain_max"],
        label_mark_leaves=_label_pane_mark_leaves(probe, chart_id),
        height_correction_ratio=height_correction_ratio,
        emitted=emitted,
    )
    pane["data"]["values"] = [
        {series_field: s, value_alias: y} for s, y in result.positions
    ]
    # Recorded unconditionally, including a `fit` outcome as None: a chart
    # can be recascaded more than once while a sink is open (render-first
    # sizing retrying at a taller cols-aligned height, say), and only the
    # last call here corresponds to what actually shipped. A `fit` here must
    # clear any overflow an earlier, discarded trial recorded — see
    # endpoint_label_overflow.py's module docstring.
    record_endpoint_label_gap_overflow(
        chart_id,
        None
        if result.outcome == "fit"
        else EndpointLabelGapOverflow(
            series_count=len(anchors),
            gap_px=cascade["gap_px"],
            cause=result.outcome,
            dropped_series=tuple(result.dropped),
        ),
    )


def _correct_concat_overshoot(
    spec: dict[str, Any],
    target_width: float | None,
    target_height: float | None,
    vlc: Any,
    endpoint_label_cascade: VLDict | None,
    chart_id: str,
) -> None:
    """Two-pass width/height correction for hconcat/vconcat endpoint-label specs.

    vl-convert ignores ``autosize: fit`` on concat children, so the first
    render measures the actual outer dimensions via ``vegalite_to_scenegraph``
    (structured — no SVG-string regex); the overshoot is subtracted from the
    resizable pane(s) before the real render.

    Width: hconcat shrinks pane[0] only (label pane is fixed-width). vconcat
    shrinks both panes — they share the x scale and must resize together so rail
    labels stay centered.

    Height: hconcat panes sit side by side, so both shrink equally — neither may
    govern a taller total. vconcat panes stack, and the rail's height is fixed
    chrome, so only the chart pane absorbs the overshoot; shrinking the rail
    would clip the series labels.

    A spec carrying an attached ``support_table`` strip opts out of the height pass
    by arriving without ``$df_target_height`` at all (dropped in
    ``vega_lite._apply_support_table_strip``): the strip's layers are pixel literals
    anchored to ``spec.height``, so resizing the pane here would detach them.
    Don't reinstate a height target for those specs — their fit is handled by a
    pre-shrunk re-render (``layout_sizing._correct_support_table_height``).

    ``endpoint_label_cascade`` (right_pane endpoint-label charts only — see
    ``vega_lite.py``'s ``$df_endpoint_label_cascade`` sentinel) re-cascades the
    label pane's positions against this same probe once it's in hand, so a
    chart carrying it pays for exactly one ``vegalite_to_scenegraph`` call
    regardless of which corrections fire.
    """
    if target_width is None and target_height is None:
        return
    is_hconcat = "hconcat" in spec and spec["hconcat"]
    is_vconcat = "vconcat" in spec and spec["vconcat"]
    if not is_hconcat and not is_vconcat:
        return
    try:
        probe = vlc.vegalite_to_scenegraph(spec)
    except Exception:  # noqa: BLE001, S110 — vl-convert throws untyped JS errors
        return
    if target_width is not None:
        overshoot = float(probe["width"]) - float(target_width)
        if overshoot > 0:
            width_panes = [spec["hconcat"][0]] if is_hconcat else list(spec["vconcat"])
            for pane in width_panes:
                orig_w = float(pane.get("width", target_width))
                new_w = orig_w - overshoot
                if new_w <= 0:
                    raise ChartDataError.from_code(
                        ERR_CONCAT_OVERSHOOT_NONPOSITIVE,
                        new_w=new_w,
                        orig_w=orig_w,
                        overshoot=overshoot,
                        target_width=target_width,
                    )
                pane["width"] = new_w
    if target_height is not None:
        overshoot = float(probe["height"]) - float(target_height)
        if overshoot > 0:
            # Stacked vconcat panes share the total, so only the chart pane
            # (index 1) gives up the overshoot — the rail above it is fixed.
            height_panes = list(spec["hconcat"]) if is_hconcat else [spec["vconcat"][1]]
            for pane in height_panes:
                orig_h = float(pane.get("height", target_height))
                new_h = orig_h - overshoot
                # Height overshoot is driven by Vega chrome (axes, legend) that
                # is outside author control — a zero/negative result is possible
                # for very tall chrome on a small canvas, and is a layout concern,
                # not a bug signal.  Width overshoot can be caused by title, subtitle,
                # axis tick labels, axis titles, or the fixed-width label pane itself —
                # anything that contributes to the scenegraph width; non-positive width
                # is always a bug signal.
                if new_h > 0:
                    pane["height"] = new_h
    if endpoint_label_cascade is not None and is_hconcat:
        # The label pane carries no axis/title chrome (see
        # recascade_endpoint_labels' docstring), so its plot rectangle scales
        # 1:1 with its declared height — the measured slope must be scaled by
        # the same ratio the height-correction pass above just applied to it.
        # vega_lite.py always stamps pane[1]'s pre-correction declared height
        # to exactly target_height in the same step it stamps
        # $df_target_height, so target_height IS that pre-correction value —
        # no separate snapshot needed. Exactly 1.0 — the identity, not an
        # estimate — when target_height is None: there was no declared
        # height to correct (height=None, "let Vega auto-size vertically"),
        # so nothing shrank and the probe's own slope already matches the
        # real render.
        height_correction_ratio = 1.0
        if target_height is not None:
            # vega_lite.py stamps pane[1]["height"] in the same step it stamps
            # $df_target_height, so the key is always present here — direct
            # indexing, not a defaulted read, is the honest contract.
            pane1_height_after = float(spec["hconcat"][1]["height"])
            height_correction_ratio = pane1_height_after / target_height
        _recascade_endpoint_label_pane(
            spec,
            probe,
            endpoint_label_cascade,
            chart_id,
            height_correction_ratio,
        )


def _correct_facet_overshoot(
    spec: dict[str, Any],  # type-state: explicit_any — foreign VL JSON spec
    target_width: float | None,
    target_height: float | None,
    vlc: Any,  # type-state: explicit_any — vl_convert has no type stubs
    panel_cols: int | None,
    panel_rows: int | None,
) -> None:
    """Shrink small-multiples panels to absorb decoration overshoot vl-convert won't reflow.

    ``facet_panel_width()`` (compile/resolve/chart/adaptive_stroke.py) budgets
    a flat chrome gutter for the row-header/left axis and, when mirrored, the
    far-edge axis — but vl-convert never reflows facet panels under
    ``autosize: fit`` (see ``vega_lite.py``'s ``_apply_facet_layout``), so
    whatever chrome a real render actually needs — a nominal/gradient legend,
    a mirrored ghost axis, or a facet header title on the last panel — paints
    at its true measured size regardless of the declared per-panel width. One
    probe render measures the real composite extent; the overshoot, divided
    evenly across panel columns/rows, comes off every panel's own width/
    height before the real render. Single-pass, not iterative — mirrors
    ``_correct_concat_overshoot``'s own single-pass shrink, generalized from
    concat's per-pane geometry to facet's per-panel geometry (shrinking N
    identical panels by ``overshoot / N`` reduces the composite's total width
    by exactly ``overshoot``, since the decorations driving the overshoot
    don't scale with a few pixels of panel width).

    No-op when ``spec`` is not a facet spec, when neither target is set, or
    when the panel counts weren't stamped (``$df_facet_panel_cols/rows`` are
    only present on a facet spec — see ``_apply_facet_layout``).

    A shrink that would leave a panel non-positive is skipped rather than
    raised: unlike concat's fixed-width label pane (where a non-positive pane
    is always a bug), a facet panel legitimately can run out of the room a
    huge decoration wants, and ``WARN-FACET-PANEL-WIDTH-BELOW-MINIMUM``
    already advises on that — the card boundary still wins, this correction
    just gets it closer.

    The probe keeps the root title, and both axes depend on that: the title
    sits on the facet root (unlike hconcat, which moves it into ``pane[0]``
    before probing) and occupies real vertical extent, so a title-blind probe
    under-measures the height overshoot by the whole title band. It is safe to
    keep only because ``_apply_facet_layout``'s caller now bounds it — a facet
    composite carries no top-level ``width``, so ``apply_title_overflow_to_spec``
    is passed the card width explicitly (``available_width``) rather than
    bailing and letting an unwrapped subtitle report its full natural width
    into the scenegraph.

    ``unit["width"]`` / ``unit["height"]`` are indexed directly, not defaulted:
    the caller stamps ``$df_target_width`` under the same ``width > 0`` guard
    that sets ``unit["width"]`` (and ``$df_target_height`` under the same guard
    as ``unit["height"]``), so reaching a branch here means the matching key is
    present. Same contract as ``_correct_concat_overshoot``'s ``pane[1]``.
    """
    if target_width is None and target_height is None:
        return
    if "facet" not in spec or panel_cols is None or panel_rows is None:
        return
    try:
        probe = vlc.vegalite_to_scenegraph(spec)
    except Exception:  # noqa: BLE001, S110 — vl-convert throws untyped JS errors
        return
    unit = spec["spec"]
    if target_width is not None:
        overshoot = float(probe["width"]) - float(target_width)
        if overshoot > 0:
            orig_w = float(unit["width"])
            new_w = orig_w - overshoot / panel_cols
            if new_w > 0:
                unit["width"] = new_w
    if target_height is not None:
        overshoot = float(probe["height"]) - float(target_height)
        if overshoot > 0:
            orig_h = float(unit["height"])
            new_h = orig_h - overshoot / panel_rows
            if new_h > 0:
                unit["height"] = new_h


def render_svg_content(svg_content: str, format: str, *, scale: float = 1.0) -> str:
    """Convert SVG content to the requested encoded output."""
    if format == "svg":
        return svg_content
    if format == "png":
        return base64.b64encode(to_png(svg_content, scale=scale)).decode("utf-8")
    if format == "pdf":
        return base64.b64encode(to_pdf(svg_content)).decode("utf-8")
    raise ValueError(f"Unsupported SVG format: {format}")


def render_vega_spec(
    spec: dict[str, Any],
    format: str,
    resolved_style: ResolvedStyle,
    width: float | None,
    height: float | None,
    is_placeholder: bool,
    chart_id: str,
) -> str:
    """Render a Vega-Lite spec into SVG, PNG, or PDF."""
    try:
        import vl_convert as vlc
    except ImportError:
        raise FormatError.from_code(
            ERR_FORMAT_CONVERTER_UNAVAILABLE, format=format
        ) from None

    register_vl_convert_fonts(vlc)

    # Content-addressed memo. Keyed before the ``$df_*`` pops below, which mutate
    # ``spec`` in place and shape the output. A hit skips the overshoot probe
    # render as well as the real one — the probe is inside this function, so
    # memoizing at this level is what makes both free.
    cache = active_svg_cache()
    cache_key: str | None = None
    if cache is not None:
        cache_key = svg_cache_key(
            spec,
            output_format=format,
            width=width,
            height=height,
            is_placeholder=is_placeholder,
            resolved_style=resolved_style,
            # Read below by _fix_chart_click_hrefs, which bakes this host's
            # board-root prefix into the markup — so it is content, not context.
            link_context=get_link_context(),
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # Two-pass width/height correction for hconcat endpoint-label specs.
    # vl-convert ignores autosize:fit on concat children, so the first render
    # measures the actual outer dimensions; the overshoots are subtracted from
    # the resizable pane(s) before the real render. Every $df_* sentinel is
    # stamped in vega_lite.py's _render_vl_artifact and must be popped before
    # rendering. $df_title_style carries the chart's own resolved title style
    # (chart-local overflow mode / font-size fallback) as a plain dict — see
    # the sentinel's JSON-safety note in vega_lite.py's _render_vl_artifact —
    # so this deferred wrap reads the same source as the non-hconcat path,
    # instead of re-deriving it from the board-level resolved_style.chart_defaults.title.
    target_width = spec.pop("$df_target_width", None)
    target_height = spec.pop("$df_target_height", None)
    facet_panel_cols = spec.pop("$df_facet_panel_cols", None)
    facet_panel_rows = spec.pop("$df_facet_panel_rows", None)
    endpoint_label_cascade = spec.pop("$df_endpoint_label_cascade", None)
    title_style_dict = spec.pop("$df_title_style", None)
    axis_label_kinds = spec.pop("$df_axis_label_kinds", None)
    value_label_layers = spec.pop("$df_value_label_layers", [])
    title_style = (
        TitleStyle.model_validate(title_style_dict)
        if title_style_dict is not None
        else None
    )
    if target_width is not None or target_height is not None:
        # Bound title/subtitle BEFORE the overshoot probe. Without this, an
        # unbounded subtitle reports its full natural width into the
        # scenegraph, driving a huge overshoot that can produce new_w <= 0
        # (raising ChartDataError). Wrapping now against pane[0]'s current
        # (pre-correction, usually wider) width only needs to bound the probe
        # — the raw strings are saved so the post-correction pass below can
        # re-wrap from source at the real, corrected limit instead of
        # re-truncating this pass's (too-wide) wrapped lines.
        title_block = (
            spec["hconcat"][0].get("title")
            if "hconcat" in spec and spec["hconcat"]
            else None
        )
        original_text = (
            title_block.get("text") if isinstance(title_block, dict) else None
        )
        original_subtitle = (
            title_block.get("subtitle") if isinstance(title_block, dict) else None
        )
        if "hconcat" in spec and spec["hconcat"]:
            apply_title_overflow_to_spec(spec["hconcat"][0], title_style)
        _correct_concat_overshoot(
            spec, target_width, target_height, vlc, endpoint_label_cascade, chart_id
        )
        _correct_facet_overshoot(
            spec, target_width, target_height, vlc, facet_panel_cols, facet_panel_rows
        )
        if isinstance(title_block, dict):
            # Restore the raw strings so this pass re-wraps from source at the
            # corrected limit, rather than reusing the pre-correction wrap
            # (which Vega would then re-truncate a second time, cutting text
            # off far earlier than the wrap-two layout intended).
            if original_text is not None:
                title_block["text"] = original_text
            if original_subtitle is not None:
                title_block["subtitle"] = original_subtitle
            # pane[0]["width"] after correction is the *data-plot* width only
            # — vl-convert ignores autosize:fit on concat children, so the
            # y-axis tick-label gutter renders outside "width". Title/subtitle
            # span pane[0]'s whole visual footprint (gutter + plot), so use
            # target_width minus the fixed label pane and spacing instead of
            # the shrunk plot width, or the title wraps far earlier than the
            # chart actually has room for. Root padding comes off too: it is
            # outside that footprint, and a title allowed to run into it
            # governs the concat's width and pushes the whole chart past the
            # box it declared.
            hconcat_panes = spec["hconcat"]
            available_width = None
            if target_width is not None and len(hconcat_panes) > 1:
                pane1_width_raw = hconcat_panes[1].get("width")
                pane1_width = (
                    pane1_width_raw
                    if isinstance(pane1_width_raw, (int, float))
                    else 0.0
                )
                spacing_raw = spec.get("spacing")
                spacing = spacing_raw if isinstance(spacing_raw, (int, float)) else 0.0
                available_width = (
                    target_width
                    - pane1_width
                    - spacing
                    - _padding_side(spec, "left")
                    - _padding_side(spec, "right")
                )
            apply_title_overflow_to_spec(
                spec["hconcat"][0],
                title_style,
                available_width=available_width,
            )

    try:
        svg_result = vlc.vegalite_to_svg(spec)
    except Exception as exc:
        # vl-convert JS errors (e.g. Vega scene-graph TypeErrors on unsupported
        # layered specs) escape as Python exceptions.  Re-raise as ChartDataError
        # so render_chart_item records a per-tile error card instead of aborting
        # the entire dashboard render process.
        raise ChartDataError(str(exc)) from exc
    svg_result = _namespace_svg_ids(svg_result, chart_id)
    svg_result = _fix_chart_click_hrefs(svg_result)
    svg_result = _stamp_chart_title_kind(svg_result)
    svg_result = _stamp_axis_title_kinds(svg_result, axis_label_kinds)
    svg_result = _stamp_value_label_layers(svg_result, value_label_layers, chart_id)
    svg_result = _stamp_series_keys(svg_result, spec, vlc, chart_id)
    if _spec_has_encoding(spec, ["strokeDash"]):
        svg_result = _fix_legend_symbol_linecap(svg_result)

    padding_left = _padding_side(spec, "left")
    if padding_left > 0:
        svg_result = fix_title_alignment(svg_result, padding_left)

    if is_placeholder:
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.placeholder import (
            add_placeholder_overlay,
            apply_placeholder_opacity,
        )

        chart_width = width or 400
        chart_height = height or resolved_style.chart_defaults.default_chart_height
        svg_result = apply_placeholder_opacity(
            svg_result, resolved_style=resolved_style
        )
        svg_result = add_placeholder_overlay(
            svg_result,
            chart_width,
            chart_height,
            font=FontStyle(family=resolved_style.font.family),
            resolved_style=resolved_style,
        )

    rendered = render_svg_content(svg_result, format, scale=1.0)
    if cache is not None and cache_key is not None:
        cache.put(cache_key, rendered, chart_id)
    return rendered


def render_chart_artifact(
    artifact: RenderArtifact,
    format: str,
    resolved_style: ResolvedStyle,
    width: float | None,
    height: float | None,
    chart_id: str,
    is_placeholder: bool = False,
) -> str:
    """Render a chart-domain artifact into the requested output format."""
    if artifact.kind == "json":
        if format != "json":
            raise ValueError(f"JSON artifact cannot render as {format}")
        return json.dumps(artifact.payload, indent=2)

    if artifact.kind == "svg":
        return render_svg_content(str(artifact.payload), format)

    if artifact.kind == "vega_spec":
        if not isinstance(artifact.payload, dict):
            raise ValueError("Vega spec artifact payload must be a dictionary")
        return render_vega_spec(
            artifact.payload,
            format,
            resolved_style=resolved_style,
            width=width,
            height=height,
            is_placeholder=is_placeholder,
            chart_id=chart_id,
        )

    raise ValueError(f"Unsupported artifact kind: {artifact.kind}")
