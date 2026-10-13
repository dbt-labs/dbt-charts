from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.title_overflow import (
    apply_title_overflow_to_spec,
    compute_title_limit,
    fix_title_alignment,
    prepare_title_text,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_MARKS_SVG_TEMPLATE = """\
<svg class="marks" width="{width}" height="400" viewBox="0 0 {width} 400">\
<rect width="{width}" height="400"/>\
<g fill="none" stroke-miterlimit="10" transform="translate({main_x},50)">\
<g class="mark-group role-frame root">\
<g transform="translate(0,0)"><path class="background" d="M0,0h300v300h-300Z"/>\
<g class="mark-group role-title">\
<g transform="translate({title_x},-48)">\
<text transform="translate(0,19)">My Title</text>\
</g></g></g></g></g>\
</svg>"""


def _make_svg(width: int, main_x: float, title_x: float) -> str:
    return _MARKS_SVG_TEMPLATE.format(width=width, main_x=main_x, title_x=title_x)


class TestComputeTitleLimit:
    def test_returns_none_without_positive_width(self):
        assert compute_title_limit(None, 12) is None
        assert compute_title_limit(0, 12) is None

    def test_supports_numeric_and_mapping_padding(self):
        assert compute_title_limit(200, 12) == 176
        assert compute_title_limit(200, {"left": 16, "right": 24}) == 160


class TestPrepareTitleText:
    def test_empty_string_returns_unchanged(self):
        text, truncated = prepare_title_text(
            "", overflow="wrap-two", limit=40, font_size=12
        )
        assert text == ""
        assert truncated is False

    def test_long_single_token_wraps(self):
        # Long tokens should still break across multiple precise-measured lines.
        rendered, truncated = prepare_title_text(
            "Configuration",
            overflow="wrap",
            limit=12,
            font_size=10,
        )
        lines = rendered.split("\n")
        assert len(lines) > 1
        assert "".join(lines) == "Configuration"
        assert truncated is False

    def test_wrap_two_leaves_short_text_on_one_line(self):
        text, truncated = prepare_title_text(
            "Revenue", overflow="wrap-two", limit=80, font_size=12
        )
        assert text == "Revenue"
        assert truncated is False

    def test_wrap_two_clips_second_line_with_ellipsis(self):
        rendered, truncated = prepare_title_text(
            "Revenue performance by enterprise segment and partner region",
            overflow="wrap-two",
            limit=60,
            font_size=12,
        )
        lines = rendered.splitlines()
        assert len(lines) == 2
        assert lines[1].endswith("…")
        assert truncated is True

    @pytest.mark.parametrize(
        ("limit", "expected"),
        [
            (29, "Reve"),
            (5, "R"),
        ],
    )
    def test_clip_mode_hard_clips_without_ellipsis(self, limit: int, expected: str):
        text, truncated = prepare_title_text(
            "Revenue", overflow="clip", limit=limit, font_size=10
        )
        assert text == expected
        assert truncated is True

    @pytest.mark.parametrize(
        ("limit", "expected"),
        [
            (29, "Rev…"),
            (5, "R"),
        ],
    )
    def test_truncate_mode_adds_ellipsis(self, limit: int, expected: str):
        text, truncated = prepare_title_text(
            "Revenue", overflow="truncate", limit=limit, font_size=10
        )
        assert text == expected
        assert truncated is True


class TestApplyTitleOverflowToSpec:
    def _make_spec(self, title: str, width: int = 200) -> dict:
        return {"title": {"text": title}, "width": width}

    def test_short_title_stays_as_string(self):
        spec = self._make_spec("Revenue")
        apply_title_overflow_to_spec(spec, None)
        assert isinstance(spec["title"]["text"], str)
        assert spec["title"]["text"] == "Revenue"

    def test_wrap_two_produces_list_for_long_title(self):
        spec = self._make_spec(
            "Revenue performance by enterprise segment and partner region", width=200
        )
        apply_title_overflow_to_spec(spec, None)
        assert isinstance(spec["title"]["text"], list), (
            "wrap-two overflow must produce a list for Vega-Lite multi-line title"
        )
        assert len(spec["title"]["text"]) == 2

    def test_limit_is_set_on_title_block(self):
        spec = self._make_spec("Revenue")
        apply_title_overflow_to_spec(spec, None)
        assert "limit" in spec["title"]

    def test_no_op_when_width_missing(self):
        spec = {"title": {"text": "Revenue"}}
        apply_title_overflow_to_spec(spec, None)
        # No limit, no modification to text
        assert spec["title"]["text"] == "Revenue"
        assert "limit" not in spec["title"]

    def test_uses_font_size_from_spec_config(self):
        """Regression: wrapping must use the rendered font size (24px from theme),
        not the hardcoded 18px fallback.

        At 18px and limit=325: max_chars ≈ 31 → 30-char title fits on one line.
        At 24px and limit=325: max_chars ≈ 23 → same title wraps.
        """
        spec = {
            "title": {"text": "Bar Chart - Revenue by Product"},
            "width": 350,
            "padding": {"left": 20, "right": 5},
            "config": {"title": {"fontSize": 24}},
        }
        apply_title_overflow_to_spec(spec, None)
        assert isinstance(spec["title"]["text"], list), (
            "title must wrap when 24px font is used; 18px fallback would skip wrapping"
        )


class TestFixTitleAlignment:
    def test_title_already_at_padding_left_unchanged(self):
        svg = _make_svg(width=400, main_x=20, title_x=0)
        result = fix_title_alignment(svg, padding_left=20)
        assert "translate(0," in result  # unchanged

    def test_title_left_of_padding_moved_right(self):
        """Regression: title starting at SVG x < padding_left must be moved to padding_left."""
        # main_x=66, title_x=-71 → title_svg_x = -5 (clipped by viewport)
        svg = _make_svg(width=160, main_x=66, title_x=-71)
        result = fix_title_alignment(svg, padding_left=20)
        # new_title_x = 20 - 66 = -46
        assert "translate(-46.00," in result

    def test_title_between_zero_and_padding_moved_right(self):
        """Regression: title at positive SVG x but < padding_left must align to padding_left."""
        # main_x=55, title_x=-38 → title_svg_x = 17 (misaligned, < 20)
        svg = _make_svg(width=544, main_x=55, title_x=-38)
        result = fix_title_alignment(svg, padding_left=20)
        # new_title_x = 20 - 55 = -35
        assert "translate(-35.00," in result

    def test_title_past_padding_left_unchanged(self):
        """Title already past padding_left (e.g. left-axis chart) must not be moved left."""
        # main_x=30, title_x=0 → title_svg_x=30 > padding_left=20
        svg = _make_svg(width=400, main_x=30, title_x=0)
        result = fix_title_alignment(svg, padding_left=20)
        assert "translate(0," in result  # unchanged — don't move it left

    def test_no_title_group_unchanged(self):
        svg = '<svg class="marks"><g fill="none" stroke-miterlimit="10" transform="translate(20,50)"></g></svg>'
        result = fix_title_alignment(svg, padding_left=20)
        assert result == svg

    def test_no_marks_svg_unchanged(self):
        svg = '<svg><g transform="translate(20,50)"><g class="mark-group role-title"><g transform="translate(-5,-48)"></g></g></g></svg>'
        result = fix_title_alignment(svg, padding_left=20)
        assert result == svg


class TestTitleLimitWithCardPadding:
    """Regression: title limit must use the actual card padding, not the default spec padding."""

    _data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]

    def _make_chart(self):
        from dbt_charts.core.compile.models.chart.normalized import BarChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        compiled = BarChart(
            id="bar_test",
            type="bar",
            title="Revenue performance by enterprise segment and partner region year over year",
            x="month",
            y="revenue",
            query=SqlQuery(sql="SELECT 1", source="test_db"),
        )
        return resolve(compiled, self._data, chart_style_context=_BOARD_CTX)

    def test_card_padding_drives_title_limit(self):
        """When the render function receives a card_padding, the title limit must
        equal width - left_pad - right_pad using that padding, not the default spec
        padding {left:20, right:5}. Otherwise, pre-wrapped text can overflow the visible
        card area when card_padding overrides the default spec padding at finalize time.
        """

        card_padding = 16
        width = 575
        spec = render_resolved_chart(
            self._make_chart(),
            self._data,
            _BOARD_RS,
            width=width,
            padding={
                "left": card_padding,
                "right": card_padding,
                "top": card_padding,
                "bottom": card_padding,
            },
        ).payload
        expected_limit = width - 2 * card_padding
        assert spec["title"]["limit"] == expected_limit, (
            f"title limit {spec['title']['limit']} must match width - 2*card_padding = "
            f"{expected_limit}, not the default spec padding {{left:20, right:5}}"
        )

    def test_asymmetric_card_padding_drives_title_limit(self):
        """Asymmetric padding: limit must use left + right, not double one side."""

        width = 600
        spec = render_resolved_chart(
            self._make_chart(),
            self._data,
            _BOARD_RS,
            width=width,
            padding={"left": 24, "right": 8, "top": 8, "bottom": 8},
        ).payload
        assert spec["title"]["limit"] == width - 24 - 8


class TestSubtitleOverflowPreprocessing:
    """Regression: subtitle must be preprocessed through prepare_title_text, not left raw."""

    def test_subtitle_is_wrapped_when_long(self) -> None:
        """apply_title_overflow_to_spec must bound subtitle text, not leave it unbounded."""
        long_subtitle = (
            "This is an extremely long subtitle that would overflow any reasonable chart width "
            "if left as a raw single-line string without any wrapping or truncation applied"
        )
        spec: dict = {
            "title": {"text": "Revenue", "subtitle": long_subtitle},
            "width": 300,
        }
        apply_title_overflow_to_spec(spec, None)
        result = spec["title"]["subtitle"]
        # Must not be the original raw string — must be shortened or split into lines
        assert result != long_subtitle, (
            "subtitle must be preprocessed by prepare_title_text, not left as a raw unbounded string"
        )

    def test_short_subtitle_stays_intact(self) -> None:
        """A subtitle that fits the limit must not be altered."""
        spec: dict = {
            "title": {"text": "Revenue", "subtitle": "Q4 results"},
            "width": 500,
        }
        apply_title_overflow_to_spec(spec, None)
        assert spec["title"]["subtitle"] == "Q4 results"

    def test_no_subtitle_is_noop(self) -> None:
        """apply_title_overflow_to_spec must not crash when subtitle is absent."""
        spec: dict = {"title": {"text": "Revenue"}, "width": 300}
        apply_title_overflow_to_spec(spec, None)
        assert "subtitle" not in spec["title"]

    def test_subtitle_overflow_respects_subtitle_style(self) -> None:
        """When title_style.subtitle.overflow is 'truncate', subtitle must be truncated."""
        from types import SimpleNamespace

        subtitle_style = SimpleNamespace(
            overflow="truncate", font=SimpleNamespace(size=12.0)
        )
        title_style = SimpleNamespace(
            overflow="wrap-two",
            font=SimpleNamespace(size=18.0, family=None),
            subtitle=subtitle_style,
        )
        long_subtitle = (
            "This subtitle is long enough to require truncation on a narrow chart "
            "width and should end with an ellipsis when truncate mode is used"
        )
        spec: dict = {
            "title": {"text": "Revenue", "subtitle": long_subtitle},
            "width": 200,
        }
        apply_title_overflow_to_spec(spec, title_style)
        result = spec["title"]["subtitle"]
        assert isinstance(result, str)
        # truncate mode: result must end with ellipsis and fit on one line
        assert "\n" not in result
        assert result.endswith("…")
