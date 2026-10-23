"""Tests for chart converter — specifically the vl-convert missing dependency path."""

from __future__ import annotations

import base64
import re
import sys
import types
from unittest import mock

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
)
from dbt_charts.core.render.errors import FormatError


def _noop_register_fonts(vlc_module: object) -> None:
    pass


def test_render_vega_spec_raises_format_error_when_vlconvert_missing() -> None:
    """render_vega_spec must raise FormatError (not ImportError) when vl-convert is absent."""
    from dbt_charts.core.diagnostics.codes_render import (
        ERR_FORMAT_CONVERTER_UNAVAILABLE,
    )

    # Block the import so render_vega_spec hits the except branch.
    with mock.patch.dict(sys.modules, {"vl_convert": None}):
        # Re-import to get a fresh call that hits the guarded import.
        from dbt_charts.core.render.converters.chart import render_vega_spec

        with pytest.raises(
            FormatError, match="pip install vl-convert-python"
        ) as exc_info:
            render_vega_spec(
                {"$schema": "..."},
                "svg",
                resolve_style(get_theme_style()),
                None,
                None,
                False,
                chart_id="chart",
            )
        assert exc_info.value.code is ERR_FORMAT_CONVERTER_UNAVAILABLE


@pytest.mark.parametrize(
    ("module_name", "helper_name"),
    [
        ("png", "svg_to_png"),
        ("pdf", "svg_to_pdf"),
    ],
)
def test_vl_convert_export_normalizes_inter_family_only_at_boundary(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    helper_name: str,
) -> None:
    """vl-convert exports should keep authored SVG as Inter but normalize internally."""
    module = __import__(
        f"dbt_charts.core.render.converters.{module_name}", fromlist=["*"]
    )
    fake_helper = mock.Mock(return_value=b"bytes")
    fake_vlc = types.SimpleNamespace(
        register_font_directory=mock.Mock(),
        **{helper_name: fake_helper},
    )
    monkeypatch.setitem(sys.modules, "vl_convert", fake_vlc)
    monkeypatch.setattr(module, "register_vl_convert_fonts", _noop_register_fonts)

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40">'
        "<style>.cls{font-family:Inter;} :root { --dbt-font-family: Inter; }</style>"
        '<text x="10" y="30" class="cls" font-family="Inter">Visible</text>'
        "</svg>"
    )

    if module_name == "png":
        result = module.to_png(svg, scale=1.0)
        fake_helper.assert_called_once()
        normalized_svg = fake_helper.call_args.args[0]
        assert fake_helper.call_args.kwargs["scale"] == 1.0
    else:
        result = module.to_pdf(svg)
        fake_helper.assert_called_once()
        normalized_svg = fake_helper.call_args.args[0]

    assert result == b"bytes"
    assert 'font-family="Inter"' in svg
    assert "font-family:Inter;" in svg
    assert "--dbt-font-family: Inter;" in svg
    assert 'font-family="Inter Variable"' in normalized_svg
    assert "font-family:Inter Variable;" in normalized_svg
    assert "--dbt-font-family: Inter Variable;" in normalized_svg


@pytest.mark.parametrize(
    ("module_name", "helper_name"),
    [
        ("png", "svg_to_png"),
        ("pdf", "svg_to_pdf"),
    ],
)
def test_vl_convert_export_preserves_dbt_serif_oldstyle_families(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    helper_name: str,
) -> None:
    """Derived serif families should pass through unchanged to vl-convert."""
    module = __import__(
        f"dbt_charts.core.render.converters.{module_name}", fromlist=["*"]
    )
    fake_helper = mock.Mock(return_value=b"bytes")
    fake_vlc = types.SimpleNamespace(
        register_font_directory=mock.Mock(),
        **{helper_name: fake_helper},
    )
    monkeypatch.setitem(sys.modules, "vl_convert", fake_vlc)
    monkeypatch.setattr(module, "register_vl_convert_fonts", _noop_register_fonts)

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40">'
        '<text x="10" y="30" '
        'font-family="dbt Serif Oldstyle Proportional">Visible</text>'
        "</svg>"
    )

    if module_name == "png":
        result = module.to_png(svg, scale=1.0)
    else:
        result = module.to_pdf(svg)

    normalized_svg = fake_helper.call_args.args[0]
    assert result == b"bytes"
    assert "dbt Serif Oldstyle Proportional" in normalized_svg


@pytest.mark.parametrize(
    ("format_name", "helper_name", "expected_bytes"),
    [
        ("png", "to_png", b"png-bytes"),
        ("pdf", "to_pdf", b"pdf-bytes"),
    ],
)
def test_render_vega_spec_derives_binary_formats_from_svg(
    monkeypatch: pytest.MonkeyPatch,
    format_name: str,
    helper_name: str,
    expected_bytes: bytes,
) -> None:
    """PNG/PDF chart exports should derive from the same SVG render path."""
    from dbt_charts.core.render.converters import chart as chart_converter

    fake_vlc = types.SimpleNamespace(
        vegalite_to_svg=mock.Mock(return_value='<svg width="600" height="320"></svg>'),
        vegalite_to_png=mock.Mock(side_effect=AssertionError("unexpected direct png")),
        vegalite_to_pdf=mock.Mock(side_effect=AssertionError("unexpected direct pdf")),
    )
    monkeypatch.setitem(sys.modules, "vl_convert", fake_vlc)
    monkeypatch.setattr(
        chart_converter, "register_vl_convert_fonts", _noop_register_fonts
    )
    helper = mock.Mock(return_value=expected_bytes)
    monkeypatch.setattr(chart_converter, helper_name, helper)

    encoded = chart_converter.render_vega_spec(
        {"$schema": "..."},
        format_name,
        resolve_style(get_theme_style()),
        600,
        320,
        False,
        chart_id="chart",
    )

    fake_vlc.vegalite_to_svg.assert_called_once_with({"$schema": "..."})
    if format_name == "png":
        helper.assert_called_once_with(
            '<svg width="600" height="320"></svg>', scale=1.0
        )
    else:
        helper.assert_called_once_with('<svg width="600" height="320"></svg>')
    assert base64.b64decode(encoded) == expected_bytes


def test_stamp_roles_gives_axes_and_marks_a_machine_key() -> None:
    """The hover runtime selects axes and recedable marks by ``data-dct-role``,
    never by the (translatable) aria text."""
    from dbt_charts.core.render.converters import chart as chart_converter

    svg = (
        '<g class="mark-group role-axis" role="graphics-symbol" '
        'aria-roledescription="axis" aria-label="X-axis for a linear scale">'
        '<path aria-label="a: 1" role="graphics-symbol" aria-roledescription="bar"/>'
        '<path aria-label="b: 1" role="graphics-symbol" aria-roledescription="arc mark"/>'
        '<g aria-roledescription="arc mark container"/>'
        '<g class="mark-group role-axis" role="graphics-symbol" '
        'aria-roledescription="axis" aria-label="Y-axis for a linear scale"/>'
    )

    out = chart_converter._stamp_roles(svg)

    assert out.count('data-dct-role="x-axis"') == 1
    assert out.count('data-dct-role="y-axis"') == 1
    assert 'data-dct-role="bar" aria-label="a: 1"' in out
    assert 'data-dct-role="arc" aria-label="b: 1"' in out
    assert 'aria-roledescription="arc mark container"/>' in out


def test_hconcat_subtitle_bounded_before_overshoot_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subtitle must be bounded before the overshoot probe for hconcat specs.

    Without pre-probe bounding, an unbounded long subtitle contributes its full
    natural width to the scenegraph, producing a huge overshoot that yields
    new_w <= 0 and raises ChartDataError. After the fix, the subtitle is wrapped
    to at most two lines before the probe, so the probe returns a reasonable
    width and pane[0].width remains positive after correction.

    This is the regression test for the endpoint-labels blow-up: long subtitle
    + fixed-width label pane (hconcat[1]) pushing the label pane off-canvas.
    """
    from dbt_charts.core.render.converters import chart as chart_converter

    target_width = 400.0
    pane_width = 380.0
    # A subtitle that, if unbounded, would report ~900px natural width —
    # far wider than the 400px target, producing new_w = 380 - 500 = -120 (negative).
    long_subtitle = "Endpoint labels regression: " + ("long subtitle text " * 25)

    pane0 = {
        "width": pane_width,
        "padding": {"left": 10, "right": 10},
        "title": {"text": "Title", "subtitle": long_subtitle},
        "mark": "line",
        "encoding": {},
    }
    pane1 = {"width": 50, "height": 300}
    spec = {
        "$df_target_width": target_width,
        "hconcat": [pane0, pane1],
    }

    # The probe width depends on whether subtitle was bounded before the call:
    # bounded (list) → small overshoot; unbounded (long str) → huge overshoot.
    def fake_scenegraph(s: object) -> dict:
        subtitle = s["hconcat"][0].get("title", {}).get("subtitle", "")  # type: ignore[union-attr]
        if isinstance(subtitle, list) or (
            isinstance(subtitle, str) and len(subtitle) < 200
        ):
            # Bounded: small overshoot (axis/legend chrome)
            return {"width": target_width + 30.0, "height": 300.0}
        # Unbounded: overshoot so large new_w would be negative
        return {"width": target_width + 500.0, "height": 300.0}

    fake_vlc = types.SimpleNamespace(
        vegalite_to_scenegraph=fake_scenegraph,
        vegalite_to_svg=mock.Mock(return_value='<svg viewBox="0 0 400 300"></svg>'),
    )
    monkeypatch.setitem(sys.modules, "vl_convert", fake_vlc)
    monkeypatch.setattr(
        chart_converter, "register_vl_convert_fonts", _noop_register_fonts
    )

    # Must not raise — pre-probe bounding keeps new_w positive.
    chart_converter.render_vega_spec(
        spec,
        "svg",
        resolve_style(get_theme_style()),
        target_width,
        None,
        False,
        chart_id="chart",
    )

    corrected_w = pane0.get("width")
    assert isinstance(corrected_w, float) and corrected_w > 0, (
        f"pane[0].width must be positive after correction, got {corrected_w}"
    )


def test_namespace_svg_ids_rewrites_definition_and_references() -> None:
    """_namespace_svg_ids must rewrite a clipPath id and every reference to it,
    scoped by a content token plus chart_id, and leave unrelated ids/refs
    untouched.
    """
    from dbt_charts.core.render.converters import chart as chart_converter

    svg = (
        '<svg><defs><clipPath id="clip3"><rect width="10" height="10"/></clipPath>'
        "</defs>"
        '<g clip-path="url(#clip3)"><rect id="other-id"/></g>'
        '<a xlink:href="#clip3">link</a>'
        '<a href="#clip3">plain link</a>'
        "</svg>"
    )

    result = chart_converter._namespace_svg_ids(svg, "revenue_trend")

    assert re.search(r'id="clip3-[0-9a-f]{8}-revenue_trend"', result)
    assert 'id="clip3"' not in result
    assert re.search(r"url\(#clip3-[0-9a-f]{8}-revenue_trend\)", result)
    assert re.search(r'xlink:href="#clip3-[0-9a-f]{8}-revenue_trend"', result)
    assert re.search(r'href="#clip3-[0-9a-f]{8}-revenue_trend"', result)
    assert 'id="other-id"' in result, "unrelated ids must be left untouched"


def test_namespace_svg_ids_rewrites_gradient_ids_too() -> None:
    """linearGradient ids (`gradient_0`) come from the same vl-convert counter
    as clipPath ids and collide the same way.
    """
    from dbt_charts.core.render.converters import chart as chart_converter

    svg = (
        '<svg><defs><linearGradient id="gradient_0"><stop offset="0"/>'
        "</linearGradient></defs>"
        '<rect fill="url(#gradient_0)"/></svg>'
    )

    result = chart_converter._namespace_svg_ids(svg, "sales_by_region")

    assert re.search(r'id="gradient_0-[0-9a-f]{8}-sales_by_region"', result)
    assert re.search(r"url\(#gradient_0-[0-9a-f]{8}-sales_by_region\)", result)
    assert 'id="gradient_0"' not in result


def test_namespace_svg_ids_ignores_bare_text_matching_the_id_pattern() -> None:
    """A bare `#clip3` inside a text node/aria-label must not be rewritten."""
    from dbt_charts.core.render.converters import chart as chart_converter

    svg = '<svg><text aria-label="channel: #clip3">#clip3</text></svg>'
    assert chart_converter._namespace_svg_ids(svg, "c") == svg


def test_namespace_svg_ids_is_idempotent() -> None:
    """Applying the rewrite twice must not re-suffix an already-namespaced id
    -- guards the reference regex's word-boundary from re-matching across the
    inserted hyphen (`clip3-c` re-splitting into `clip3` + `-c`).
    """
    from dbt_charts.core.render.converters import chart as chart_converter

    svg = (
        '<svg><defs><clipPath id="clip3"><rect/></clipPath></defs>'
        '<g clip-path="url(#clip3)"/></svg>'
    )

    once = chart_converter._namespace_svg_ids(svg, "c")
    twice = chart_converter._namespace_svg_ids(once, "c")

    assert once == twice
    assert re.search(r'id="clip3-[0-9a-f]{8}-c"', once)
    assert re.search(r"url\(#clip3-[0-9a-f]{8}-c\)", once)


def test_namespace_svg_ids_sanitizes_chart_id_for_funciri_safety() -> None:
    """Must hold for arbitrary board-authored text, not just the well-formed
    ids already in the corpus -- nothing slugifies an authored `charts:` key.
    """
    from dbt_charts.core.render.converters import chart as chart_converter

    svg = '<svg><defs><clipPath id="clip1"/></defs><g clip-path="url(#clip1)"/></svg>'

    for unsafe_chart_id in ("my chart", "a)b", "a & b"):
        result = chart_converter._namespace_svg_ids(svg, unsafe_chart_id)
        definition = re.search(r'id="(clip1-[^"]+)"', result)
        reference = re.search(r"url\(#(clip1-[A-Za-z0-9_-]+)\)", result)
        assert definition is not None
        assert reference is not None
        assert definition.group(1) == reference.group(1)
        assert re.fullmatch(r"clip1-[A-Za-z0-9_-]+", definition.group(1))


def test_namespace_svg_ids_disambiguates_chart_ids_that_sanitize_to_the_same_token() -> (
    None
):
    """Two chart ids that only collide after sanitizing (`"a b"` and `"a_b"`
    both land on `a_b`) must not end up sharing a suffix.
    """
    from dbt_charts.core.render.converters import chart as chart_converter

    svg = '<svg><defs><clipPath id="clip1"/></defs></svg>'

    id_a = re.search(
        r'id="(clip1-[^"]+)"', chart_converter._namespace_svg_ids(svg, "a b")
    )
    id_b = re.search(
        r'id="(clip1-[^"]+)"', chart_converter._namespace_svg_ids(svg, "a_b")
    )
    assert id_a is not None
    assert id_b is not None
    assert id_a.group(1) != id_b.group(1)
    assert id_b.group(1).endswith("-a_b"), "an already-safe chart id is untouched"


def test_namespace_svg_ids_noop_when_no_vlc_ids_present() -> None:
    """A chart fragment with no vl-convert-minted ids is returned unchanged."""
    from dbt_charts.core.render.converters import chart as chart_converter

    svg = '<svg><rect id="chart-my_chart"/></svg>'
    assert chart_converter._namespace_svg_ids(svg, "my_chart") == svg


_FAKE_CLIPPED_SVG = (
    '<svg width="600" height="320">'
    '<defs><clipPath id="clip0"><rect width="10" height="10"/></clipPath>'
    '<clipPath id="clip1"><rect width="20" height="20"/></clipPath>'
    '<linearGradient id="gradient_0"><stop offset="0"/></linearGradient></defs>'
    '<g clip-path="url(#clip0)"><rect width="10" height="10"/></g>'
    '<g clip-path="url(#clip1)"><rect width="20" height="20"/></g>'
    '<rect fill="url(#gradient_0)"/>'
    "</svg>"
)


def test_render_vega_spec_namespaces_ids_through_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Goes through ``svg_cache_scope`` rather than calling the helper
    directly: the cached bytes themselves must already be namespaced, so a
    cache hit (chart_a, rendered once then read back) and a fresh render
    (chart_b, minting the same raw ids chart_a's cold render did) must never
    collide once concatenated into one board document.
    """
    from dbt_charts.core.render.converters import chart as chart_converter
    from dbt_charts.core.render.svg_cache import RenderedSvgCache, svg_cache_scope

    fake_vlc = types.SimpleNamespace(
        vegalite_to_svg=mock.Mock(return_value=_FAKE_CLIPPED_SVG)
    )
    monkeypatch.setitem(sys.modules, "vl_convert", fake_vlc)
    monkeypatch.setattr(
        chart_converter, "register_vl_convert_fonts", _noop_register_fonts
    )

    style = resolve_style(get_theme_style())
    with svg_cache_scope(RenderedSvgCache()):
        svg_a_cold = chart_converter.render_vega_spec(
            {"$schema": "chart-a"}, "svg", style, 600, 320, False, chart_id="chart_a"
        )
        svg_a_from_cache = chart_converter.render_vega_spec(
            {"$schema": "chart-a"}, "svg", style, 600, 320, False, chart_id="chart_a"
        )
        svg_b_fresh = chart_converter.render_vega_spec(
            {"$schema": "chart-b"}, "svg", style, 600, 320, False, chart_id="chart_b"
        )

    assert fake_vlc.vegalite_to_svg.call_count == 2, (
        "chart_a's second render must be served from the cache, not vl-convert"
    )
    assert svg_a_from_cache == svg_a_cold, (
        "a cache hit must return the already-namespaced bytes, not re-derive them"
    )

    board_svg = svg_a_from_cache + svg_b_fresh
    ids = re.findall(r'id="([^"]+)"', board_svg)
    duplicate_ids = {i for i in ids if ids.count(i) > 1}
    assert not duplicate_ids, (
        f"colliding ids across independently-cached charts: {duplicate_ids}"
    )
    assert re.search(r'id="clip0-[0-9a-f]{8}-chart_a"', board_svg)
    assert re.search(r'id="clip0-[0-9a-f]{8}-chart_b"', board_svg)
    assert re.search(r'id="gradient_0-[0-9a-f]{8}-chart_a"', board_svg)
    assert re.search(r'id="gradient_0-[0-9a-f]{8}-chart_b"', board_svg)
