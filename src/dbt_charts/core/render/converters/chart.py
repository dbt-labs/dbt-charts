"""Chart output conversion helpers."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.primitives import LinkOpen
from dbt_charts.core.render.board_links import get_link_context, resolve_href
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.artifacts import RenderArtifact

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_FORMAT_CONVERTER_UNAVAILABLE
from dbt_charts.core.render.chart.title_overflow import fix_title_alignment
from dbt_charts.core.render.converters.pdf import to_pdf
from dbt_charts.core.render.converters.png import to_png
from dbt_charts.core.render.errors import FormatError
from dbt_charts.core.render.font_support import register_vl_convert_fonts
from dbt_charts.core.render.svg_cache import active_svg_cache, svg_cache_key
from dbt_charts.core.render.svg_utils import (
    authored_kind_attr,
    checked_href,
    escape_attr,
)

# Every <a xlink:href="..."> vl_convert emits for chart click interactivity.
# vl_convert uses xlink:href and mangles relative URLs, so a *relative* chart
# link gets a sentinel prefix (embedded so vl_convert's own sanitizer sees a
# harmless absolute http(s) URL) that this function detects and strips. An
# absolute/template-first link (https://…, ./…, #…) never gets that sentinel
# and never passes through Python before Vega emits it — this is the only
# place either shape reaches a Python scheme check, so every match here runs
# through checked_href, sentinel or not.
_XLINK_HREF_RE = re.compile(r'<a xlink:href="([^"]*)"')
_SENTINEL_PREFIX = "http://dct.invalid"


def _fix_chart_click_hrefs(svg: str, link_open: LinkOpen) -> str:
    """Convert every chart-link xlink:href to a plain, scheme-checked href,
    stamping ``data-dbt-link-open`` on a non-page link's anchors and
    ``target="_blank"`` on a new-tab link's.

    Converts ``<a xlink:href="http://dct.invalid/path?q=v">``
    to ``<a href="/path?q=v">`` so variables.js can intercept variable-update
    clicks and the browser can follow navigation clicks directly; an absolute
    link (no sentinel) keeps its URL and gets the same plain ``href``.

    When a link context is active (board resolver), board-root paths are
    rewritten for the current runtime (serve vs Cloud). Query-string-only
    links (?var=value) pass through unchanged — they are in-page variable
    updates intercepted by variables.js, not cross-board navigation.
    """
    ctx = get_link_context()
    open_attr = (
        f' data-dbt-link-open="{escape_attr(link_open)}"' if link_open != "page" else ""
    )
    if link_open == "new_tab":
        open_attr += ' target="_blank"'

    def _replace(m: re.Match[str]) -> str:
        # vl_convert's own SVG serialization already XML-escaped this
        # attribute value (e.g. "&" -> "&amp;" between query params) --
        # unescape before resolve_href (which expects a raw URL) and before
        # escape_attr re-escapes it, or a two-param link doubles to &amp;amp;.
        url = html.unescape(m.group(1))
        if url.startswith(_SENTINEL_PREFIX):
            url = url[len(_SENTINEL_PREFIX) :]
            if ctx is not None:
                url = resolve_href(url, ctx)
        return f'<a href="{escape_attr(checked_href(url))}"{open_attr}'

    return _XLINK_HREF_RE.sub(_replace, svg)


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
    dev-only retired per-chart sweep harness in ``libs/chart-svg`` — imports it to
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


# `data-dct-role` is the machine key the hover runtime selects axes and marks
# by (`chart_interactivity.js`); the aria text beside it is a translatable label.
_AXIS_GROUP_RE = re.compile(
    r'(class="mark-group role-axis") (role="graphics-symbol" '
    r'aria-roledescription="axis" aria-label="([XY])-axis[^"]*")'
)
_MARK_ROLES = {
    "bar": "bar",
    "arc mark": "arc",
    "rect mark": "rect",
    "point": "point",
    "rule mark": "rule",
    "text mark": "text",
    "path mark": "path",
}
_MARK_ROLE_RE = re.compile(
    r'(aria-label="[^"]*" role="graphics-symbol" '
    f'aria-roledescription="({"|".join(map(re.escape, _MARK_ROLES))})")'
)


def _stamp_roles(svg: str) -> str:
    """Stamp ``data-dct-role`` on every axis group and described mark item,
    ahead of the aria text it stands in for."""
    svg = _AXIS_GROUP_RE.sub(
        lambda m: (
            f'{m.group(1)} data-dct-role="{escape_attr(m.group(3).lower())}-axis" '
            f"{m.group(2)}"
        ),
        svg,
    )
    return _MARK_ROLE_RE.sub(
        lambda m: (
            f'data-dct-role="{escape_attr(_MARK_ROLES[m.group(2)])}" {m.group(1)}'
        ),
        svg,
    )


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

    link_open = spec.pop("$df_link_open", "page")
    try:
        svg_result = vlc.vegalite_to_svg(spec)
    except Exception as exc:
        # vl-convert JS errors (e.g. Vega scene-graph TypeErrors on unsupported
        # layered specs) escape as Python exceptions.  Re-raise as ChartDataError
        # so render_chart_item records a per-tile error card instead of aborting
        # the entire dashboard render process.
        raise ChartDataError(str(exc)) from exc
    svg_result = _namespace_svg_ids(svg_result, chart_id)
    svg_result = _fix_chart_click_hrefs(svg_result, link_open)
    svg_result = _stamp_chart_title_kind(svg_result)
    svg_result = _stamp_roles(svg_result)

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
