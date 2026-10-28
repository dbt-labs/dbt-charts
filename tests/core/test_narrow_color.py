"""TDD tests for narrow-color task.

Covers:
- Chart color narrowed to str | None (bare column ref only)
- Four banned color: shapes rejected with actionable errors
- KPI value_color/glyph_color flat shortcuts rejected
- KpiSupportConfig value_color/glyph_color flat shortcuts rejected
- Layer.color narrowed to str | None
- ColorStyle: static, categorical, and gradient forms in ChartStylePatch.color
- channel.py upgrade: style_color.scale → gradient mode
- Full corpus regression guard
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.authored import AuthoredChart

_chart_patch_adapter = TypeAdapter(AuthoredChart)


@pytest.fixture(autouse=True)
def _reset():
    from dbt_charts.core.compile.config import reset_config

    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BAR_BASE = {
    "type": "bar",
    "x": "month",
    "y": "revenue",
    "query": "q",
}

_PIE_BASE = {
    "type": "pie",
    "theta": "value",
    "color": "segment",
    "query": "q",
}

_KPI_BASE = {
    "type": "kpi",
    "value": "revenue",
    "label": "Total Revenue",
    "query": "q",
}


def _patch(**overrides):
    return _chart_patch_adapter.validate_python({**_BAR_BASE, **overrides})


def _patch_kpi(**overrides):
    return _chart_patch_adapter.validate_python({**_KPI_BASE, **overrides})


# ---------------------------------------------------------------------------
# 1. Bare column ref is accepted
# ---------------------------------------------------------------------------


class TestBarColumnRef:
    """color: <column> is the only accepted form at chart root."""

    def test_color_bare_column_ref_accepted(self):
        ch = _patch(color="segment")
        assert ch.color == "segment"

    def test_color_none_accepted(self):
        ch = _patch(color=None)
        assert ch.color is None


# ---------------------------------------------------------------------------
# 2–5. Four banned color: shapes at chart root
# ---------------------------------------------------------------------------


class TestBannedColorShapes:
    """All four paint/scale/conditional dict forms are rejected at chart root."""

    def test_literal_hex_string_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match=r"literal color.*belongs in style"):
            _patch(color="#0a0")

    def test_value_dict_raises(self):
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError, match=r"color:.*value.*no longer accepted at chart root"
        ):
            _patch(color={"value": "#0a0"})

    def test_inline_scale_raises(self):
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError, match=r"Inline scale config.*no longer accepted"
        ):
            _patch(color={"column": "revenue", "scale": {"palette": ["#fee", "#900"]}})

    def test_inline_conditional_raises(self):
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError,
            match=r"Inline conditional.*no longer accepted.*no longer supports rule-driven conditional_formatting.*gradient scale",
        ):
            _patch(
                color={
                    "column": "status",
                    "when": [{"eq": "danger", "font": {"color": "#900"}}],
                }
            )

    def test_inline_conditional_points_at_gradient_fix_not_conditional_formatting(
        self,
    ):
        """The remediation must not send the author to a field this family no
        longer accepts — it points at the same GRADIENT_COLOR_FIX block the
        sibling `scale` branch already uses."""
        from dbt_charts.core.compile.models.chart.authored._base import (
            GRADIENT_COLOR_FIX,
        )

        with pytest.raises(ValidationError) as excinfo:
            _patch(
                color={
                    "column": "status",
                    "when": [{"eq": "danger", "font": {"color": "#900"}}],
                }
            )
        message = str(excinfo.value)
        assert "conditional_formatting:" not in message.split("[type=")[0]
        assert GRADIENT_COLOR_FIX in message


# ---------------------------------------------------------------------------
# 6–7. KPI flat color shortcuts rejected
# ---------------------------------------------------------------------------


class TestKpiFlatShortcutsRejected:
    """value_color and glyph_color at KPI chart root are removed."""

    def test_value_color_at_kpi_root_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="value_color"):
            _patch_kpi(glyph="▲", value_color="#0a0")

    def test_glyph_color_at_kpi_root_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="glyph_color"):
            _patch_kpi(glyph="▲", glyph_color="#16a34a")


# ---------------------------------------------------------------------------
# 8. KpiSupportConfig flat shortcuts rejected
# ---------------------------------------------------------------------------


class TestKpiSupportFlatShortcutsRejected:
    """value_color/glyph_color on KpiSupportConfig are removed."""

    def test_support_value_color_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match=r"value_color"):
            _patch_kpi(support={"value": "ytd_revenue", "value_color": "#0a0"})

    def test_support_glyph_color_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match=r"glyph_color"):
            _patch_kpi(
                support={"value": "ytd_revenue", "glyph": "▲", "glyph_color": "#16a34a"}
            )


# ---------------------------------------------------------------------------
# 9. Layer.color narrowed (tested on BarLayer — TypedLayerBase applies to all)
# ---------------------------------------------------------------------------


class TestLayerColorNarrowed:
    """BarLayer.color: hex string and dict forms rejected."""

    def test_layer_color_hex_literal_raises(self):
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.chart.authored import BarLayer

        with pytest.raises(ValidationError, match=r"data channel.*style\.marks"):
            BarLayer.model_validate({"type": "bar", "y": "revenue", "color": "#0a0"})

    def test_layer_color_bare_column_ref_accepted(self):
        from dbt_charts.core.compile.models.chart.authored import BarLayer

        layer = BarLayer.model_validate(
            {"type": "bar", "y": "revenue", "color": "segment"}
        )
        assert layer.color == "segment"


# ---------------------------------------------------------------------------
# 10. ColorStyle: static, categorical, and gradient forms
# ---------------------------------------------------------------------------


class TestColorStyle:
    """BarChartStylePatch.color accepts ColorStyle sub-fields."""

    def test_style_color_static_accepted(self):
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        patch = BarChartStylePatch.model_validate({"color": {"static": "#0a0"}})
        assert patch.color is not None
        assert patch.color.static == "#0a0"

    def test_style_color_object_with_gradient_accepted(self):
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        patch = BarChartStylePatch.model_validate(
            {"color": {"gradient": {"palette": ["#fee", "#900"]}}}
        )
        # BarChartStylePatch.color is ColorStylePatch (the all-Optional patch variant);
        # gradient is ScaleTargetConfigPatch at this stage.
        assert patch.color is not None
        assert patch.color.gradient is not None

    def test_style_color_empty_dict_accepted(self):
        """style.color: {} is valid — all sub-fields are optional."""
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        # An empty ColorStyle is valid — all fields default to None
        patch = BarChartStylePatch.model_validate({"color": {}})
        assert patch.color is not None


# ---------------------------------------------------------------------------
# 11. channel.py: style_color.scale upgrades series → gradient
# ---------------------------------------------------------------------------


class TestChannelScaleUpgrade:
    """When style_color has a gradient, the series channel upgrades to gradient mode."""

    def test_series_channel_upgraded_to_gradient_with_scale(self):
        from dbt_charts.core.compile.models.chart.authored import ScaleTargetConfig
        from dbt_charts.core.compile.models.primitives import ColorStyle
        from dbt_charts.core.compile.resolve.chart.channel import (
            normalize_chart_channels,
        )

        class _FakeChart:
            type = "bar"
            color = "revenue"
            background = None
            opacity = None
            stroke = None

        scale = ScaleTargetConfig.model_validate({"palette": ["#fee", "#900"]})
        style_color = ColorStyle(gradient=scale)
        channels = normalize_chart_channels(
            _FakeChart(), {"revenue"}, style_color=style_color
        )
        assert channels["color"].mode == "gradient"
        # After normalize_chart_channels, the scale is a resolved subtype — not the
        # authored ScaleTargetConfig. Assert on the palette field value instead.
        assert channels["color"].scale is not None
        assert channels["color"].scale.palette == ["#fee", "#900"]

    def test_series_channel_stays_series_without_scale(self):
        from dbt_charts.core.compile.resolve.chart.channel import (
            normalize_chart_channels,
        )

        class _FakeChart:
            type = "bar"
            color = "segment"
            background = None
            opacity = None
            stroke = None

        channels = normalize_chart_channels(_FakeChart(), {"segment"})
        assert channels["color"].mode == "series"

    def test_literal_style_color_does_not_create_channel(self):
        """style.color.static is static paint — no data channel created."""
        from dbt_charts.core.compile.models.primitives import ColorStyle
        from dbt_charts.core.compile.resolve.chart.channel import (
            normalize_chart_channels,
        )

        class _FakeChart:
            type = "bar"
            color = None  # no data channel
            background = None
            opacity = None
            stroke = None

        style_color = ColorStyle(static="#0a0")
        channels = normalize_chart_channels(
            _FakeChart(), set(), style_color=style_color
        )
        assert "color" not in channels  # static paint, not a channel

    def test_scale_without_data_channel_raises(self):
        """style.color: {gradient: ...} without chart.color is a hard error."""
        from dbt_charts.core.compile.models.chart.authored import ScaleTargetConfig
        from dbt_charts.core.compile.models.primitives import ColorStyle
        from dbt_charts.core.compile.resolve.chart.channel import (
            normalize_chart_channels,
        )

        class _FakeChart:
            type = "bar"
            color = None  # NO data channel
            background = None
            opacity = None
            stroke = None

        scale = ScaleTargetConfig.model_validate({"palette": ["#fff", "#00f"]})
        style_color = ColorStyle(gradient=scale)
        with pytest.raises(ValueError, match="requires chart.color"):
            normalize_chart_channels(_FakeChart(), set(), style_color=style_color)


class TestColorErrorRemediationsRender:
    """The YAML a color error tells you to write must *render*, not just parse.

    Two review rounds caught a remediation that validated and then failed: first
    `style: color: '#...'` (a string where the field wants a mapping), then
    `gradient: {palette: ["dbt-seq-blue"]}` — inside a list a palette name is a
    stop, not a palette, so following the hint dead-ended the author in a
    second, unrelated error. Both passed a validate-only check. Rendering is
    the only assertion that establishes the fix works, so the blocks are named
    constants and this renders each one on a real board.
    """

    @staticmethod
    def _render(patch_yaml: str, extra: dict[str, object], tmp_path) -> None:
        import textwrap

        import yaml as _yaml

        from dbt_charts.core.board import raise_on_dashboard_failure
        from dbt_charts.core.project import InMemoryBoard

        patch = _yaml.safe_load(textwrap.dedent(patch_yaml))
        assert patch and patch.get("style"), (
            f"remediation names no field: {patch_yaml!r}"
        )
        board = {
            "title": "Remediation",
            "queries": {"q": {"columns": ["seg", "v"], "values": [["a", 1], ["b", 2]]}},
            "charts": {
                "c": {
                    "query": "q",
                    "type": "bar",
                    "x": "seg",
                    "y": "v",
                    **extra,
                    **patch,
                }
            },
            "rows": ["c"],
        }
        from dbt_charts.agent_api import ProjectSession

        session = ProjectSession.open(tmp_path, read_only=False)
        try:
            result = session.render_board(
                board=InMemoryBoard(_yaml.safe_dump(board), path=None), format="html"
            )
            raise_on_dashboard_failure(result)
            # The assertion that matters, and the one three passes missed:
            # a chart that paints nothing still returns a board. `status`
            # is "partial" and the failure is in `chart_errors` — it does
            # not raise, and the HTML is non-empty either way.
            assert not result.chart_errors, result.chart_errors
            assert result.status == "ok", result.status
        finally:
            session.close()

    def test_the_static_color_fix_renders(self, tmp_path) -> None:
        from dbt_charts.core.compile.models.chart.authored._base import STATIC_COLOR_FIX

        self._render(STATIC_COLOR_FIX, {}, tmp_path)

    def test_the_gradient_color_fix_renders_with_the_field_binding_kept(
        self, tmp_path
    ) -> None:
        # The error tells the author to keep `color: <field>`; the block alone
        # raises, so the hint is only correct together with that instruction.
        from dbt_charts.core.compile.models.chart.authored._base import (
            GRADIENT_COLOR_FIX,
        )

        self._render(GRADIENT_COLOR_FIX, {"color": "v"}, tmp_path)


class TestColorErrorRemediationsParse:
    """The YAML a color error tells you to write must itself be accepted.

    All three hints were wrong: two pointed at ``style: color: '#...'`` (a
    string, where the field wants a mapping) and one at ``color: {scale: ...}``
    (``scale`` is not a key — ERR-EXTRA-FIELD). Following the message got you a
    second error, and each one modeled a hex literal where the language wants a
    palette token. This pins the remediations by *parsing them*, so a reworded
    hint that stops working fails here.
    """

    @staticmethod
    def _hint_yaml(exc: Exception) -> str:
        """The `style:` block a color error prints as its remediation.

        Cut at pydantic's `[type=...]` marker first: everything past it is the
        input echoed back, which for these cases is the very hex the caller
        passed in — reading it as part of the hint would pass the no-hex
        assertion's opposite and fail the parse.
        """
        lines = str(exc).split("[type=")[0].splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == "style:")
        base = len(lines[start]) - len(lines[start].lstrip())
        block = [lines[start]]
        for line in lines[start + 1 :]:
            if not line.strip() or len(line) - len(line.lstrip()) <= base:
                break
            block.append(line)
        return "\n".join(block)

    @pytest.mark.parametrize(
        "bad_color",
        [
            pytest.param("#ff0000", id="literal-hex-string"),
            pytest.param({"value": "#ff0000"}, id="value-dict"),
            pytest.param({"scale": {"palette": ["#eee", "#333"]}}, id="scale-dict"),
            pytest.param(
                {"column": "status", "when": [{"eq": "danger"}]}, id="when-dict"
            ),
        ],
    )
    def test_the_remediation_is_valid_yaml_that_parses(self, bad_color: object) -> None:
        import yaml

        with pytest.raises(ValidationError) as excinfo:
            _chart_patch_adapter.validate_python({**_BAR_BASE, "color": bad_color})

        hint = self._hint_yaml(excinfo.value)
        assert hint.strip(), f"no remediation block in: {excinfo.value}"
        # Dedent the printed block and mount it on a real chart.
        patch = yaml.safe_load("\n".join(line[2:] for line in hint.splitlines()))
        # A hint that kept `style:` and lost its children parses to
        # {"style": None}, which validates and teaches nothing — the assertion
        # this test exists to make would go vacuous without this line.
        assert patch["style"], f"remediation names no field: {hint!r}"
        _chart_patch_adapter.validate_python({**_BAR_BASE, **patch})

    @pytest.mark.parametrize(
        "bad_color",
        [
            pytest.param("#ff0000", id="literal-hex-string"),
            pytest.param({"value": "#ff0000"}, id="value-dict"),
            pytest.param({"scale": {"palette": ["#eee", "#333"]}}, id="scale-dict"),
            pytest.param(
                {"column": "status", "when": [{"eq": "danger"}]}, id="when-dict"
            ),
        ],
    )
    def test_the_remediation_shows_a_token_not_a_hex(self, bad_color: object) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _chart_patch_adapter.validate_python({**_BAR_BASE, "color": bad_color})

        assert "#" not in self._hint_yaml(excinfo.value), (
            "the error models a hex literal in the very message that is teaching "
            "the author how to write a color"
        )
