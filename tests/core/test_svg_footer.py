"""Tests for SVG render footer functionality.

The footer is a right-aligned muted text line at the bottom of every board,
with an optional hairline rule above. Theme-controlled, always-on by default,
disabled by setting style.footer.visible: false. The brand phrase "dbt Charts"
in the footer text links to style.footer.link (a subtle watermark) when set.
"""

import re

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.controls import controls_stylesheet

from ._svg_render import render_board_to_svg


class TestSVGFooter:
    def test_footer_text_is_visible_by_default(self):
        svg = render_board_to_svg()
        assert "made with" in svg
        assert "dbt Charts" in svg

    def test_footer_text_is_right_anchored_and_uses_configured_styling(self):
        footer = resolve_style(get_theme_style(get_default_theme_name())).footer
        assert footer.font is not None
        assert footer.font.size is not None
        expected_size = int(footer.font.size)
        expected_color = footer.font.color

        svg_output = render_board_to_svg()

        # Each run is its own right-anchored <text>; this pins the prefix run.
        pattern = (
            rf'<text[^>]*text-anchor="end"[^>]*font-size="{expected_size}"'
            rf'[^>]*fill="{re.escape(expected_color)}"[^>]*>made with<'
        )
        assert re.search(pattern, svg_output) is not None

    def test_footer_hairline_rule_renders_above_text_by_default(self):
        svg_output = render_board_to_svg()
        footer_idx = svg_output.find("made with")
        assert footer_idx > -1
        rule = resolve_style(get_theme_style(get_default_theme_name())).footer.divider
        assert rule is not None
        rule_pattern = rf'<line[^>]*stroke="{re.escape(rule.color)}"[^>]*/>'
        prefix = svg_output[:footer_idx]
        assert re.search(rule_pattern, prefix) is not None

    def test_footer_not_rendered_when_visible_is_false(self):
        yaml = """\
title: Test
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
style:
  footer:
    visible: false
"""
        assert "made with dbt Charts" not in render_board_to_svg(yaml)

    def test_footer_text_is_user_overridable(self):
        yaml = """\
title: Test
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
style:
  footer:
    text: "Acme Corp \xb7 attribution"
"""
        assert "Acme Corp \xb7 attribution" in render_board_to_svg(yaml)

    def test_footer_text_comes_from_style_not_rendering_config(self):
        """style.footer.text controls attribution text; rendering config has no footer_text."""
        yaml = """\
title: Test
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
style:
  footer:
    text: "custom attribution"
"""
        svg = render_board_to_svg(yaml)
        assert "custom attribution" in svg
        assert "made with dbt Charts" not in svg

    def test_footer_hairline_rule_color_matches_theme(self):
        footer = resolve_style(get_theme_style(get_default_theme_name())).footer
        assert footer.divider.visible
        svg_output = render_board_to_svg()
        footer_idx = svg_output.find("made with")
        prefix = svg_output[:footer_idx]
        rule_pattern = rf'<line[^>]*stroke="{re.escape(footer.divider.color)}"[^>]*/>'
        assert re.search(rule_pattern, prefix) is not None

    # --- brand-word link -----------------------------------------------------

    def test_footer_brand_word_links_to_configured_url_by_default(self):
        footer = resolve_style(get_theme_style(get_default_theme_name())).footer
        assert footer.link
        svg = render_board_to_svg()
        # "dbt Charts" is wrapped in an anchor to the configured URL; "made with "
        # stays plain text outside the link.
        pattern = (
            rf'<a class="dbt-footer-link" href="{re.escape(footer.link)}"[^>]*><text'
            r"[^>]*>dbt Charts</text></a>"
        )
        assert re.search(pattern, svg) is not None

    def test_run_positions_are_snapped_to_whole_pixels(self):
        """Snapping is what makes the Python and Rust engines byte-equal.

        The two measure the same variable font face instanced at 600 but
        interpolate ~0.004px apart, so the board sweep's exact chrome-byte
        comparison only holds because the emitted position is an integer.
        Remove the snap and the ordering assertions still pass while the
        sweep breaks, which is why this pins the integer directly.
        """
        svg = render_board_to_svg()
        for content in ("made with", "dbt Charts"):
            run = re.search(rf'<text x="([\d.]+)"[^>]*>{content}</text>', svg)
            assert run is not None, content
            assert float(run.group(1)).is_integer(), f"{content}: {run.group(1)}"

    def test_the_prefix_run_ends_left_of_the_brand_run(self):
        """The runs are positioned, not laid out by the renderer.

        Each is its own right-anchored <text>; if the brand run's measured
        width were wrong the prefix would land on top of it. Asserting on the
        painted x of each run is what catches that.
        """
        svg = render_board_to_svg()
        prefix = re.search(r'<text x="([\d.]+)"[^>]*>made with</text>', svg)
        brand = re.search(r'<text x="([\d.]+)"[^>]*>dbt Charts</text>', svg)
        assert prefix is not None and brand is not None
        assert float(prefix.group(1)) < float(brand.group(1))

    def test_a_suffix_run_stays_right_of_the_brand_run(self):
        yaml = """\
title: Test
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
style:
  footer:
    text: "made with dbt Charts for Acme"
"""
        svg = render_board_to_svg(yaml)
        brand = re.search(r'<text x="([\d.]+)"[^>]*>dbt Charts</text>', svg)
        suffix = re.search(r'<text x="([\d.]+)"[^>]*>for Acme</text>', svg)
        assert brand is not None and suffix is not None
        assert float(brand.group(1)) < float(suffix.group(1))

    def test_the_brand_phrase_is_heavier_in_every_renderer_not_only_in_css(self):
        """font-weight rides as a presentation attribute, not only a CSS class.

        A rasterizer that does not resolve a CSS class against the brand run —
        the PNG and PDF paths do not — would otherwise drop the weight silently
        and set the phrase at the same weight as "made with".
        """
        svg = render_board_to_svg()
        brand = re.search(r"<text[^>]*>dbt Charts</text>", svg)
        assert brand is not None
        assert 'font-weight="600"' in brand.group(0)

    def test_the_brand_phrase_is_heavier_even_with_no_link(self):
        """Weight is brand styling, not link affordance: it survives link: null."""
        yaml = """\
title: Test
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
style:
  footer:
    link: null
"""
        svg = render_board_to_svg(yaml)
        # The stylesheet always carries the rule; what must be absent is the
        # anchor element itself.
        assert '<a class="dbt-footer-link"' not in svg
        brand = re.search(r"<text[^>]*>dbt Charts</text>", svg)
        assert brand is not None, "brand phrase must still be a weighted run"
        assert 'font-weight="600"' in brand.group(0)

    def test_footer_link_styling_is_subtle_not_blue(self):
        """The linked phrase inherits the footer fill (no blue) and is styled via CSS."""
        svg = render_board_to_svg()
        # The run carries the footer's own fill, never a link color.
        assert re.search(
            r'<a class="dbt-footer-link"[^>]*><text[^>]*>dbt Charts</text></a>', svg
        )
        assert 'class="dbt-footer-link"' in svg
        # The hover affordance is interaction, so it ships with the host
        # stylesheet, never inside the board; fill and weight are inline on the run.
        assert ".dbt-footer-link:hover" not in svg
        assert ".dbt-footer-link:hover" in controls_stylesheet()

    def test_footer_link_absent_when_brand_word_not_in_text(self):
        yaml = """\
title: Test
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
style:
  footer:
    text: "custom attribution"
"""
        svg = render_board_to_svg(yaml)
        assert "custom attribution" in svg
        # No brand phrase to link, so no anchor element. (The class definition
        # itself always lives in the stylesheet, so assert on the element.)
        assert '<a class="dbt-footer-link"' not in svg
