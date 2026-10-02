"""Unit tests for ``apply_legend_entry_order`` and its pure resolution helper.

The one writer of the RESOLVED entry order onto
``encoding.color.legend.values`` in ``emitters/``: engine
display order wins when the author pinned nothing; an authored
``legend.values`` list is resolved against the real domain (exact match,
case/separator-fold, and provenance-built aliases) and wins outright,
reordering and filtering the legend to exactly the entries it names.
Neither function ever raises -- an unresolved entry is always dropped
(the render-warnings detector is what surfaces it); see
``tests/render/warnings/test_legend_values_unresolved.py``.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedLegendElementStyle,
    ResolvedLegendPosition,
    ResolvedLegendStyle,
)
from dbt_charts.core.render.chart.emitters._channels import (
    apply_color_legend,
    apply_legend_entry_order,
    categorical_color_encoding,
    resolve_legend_entries,
)


def _legend_element_style() -> ResolvedLegendElementStyle:
    from dbt_charts.core.compile.models.style.resolved._base import ResolvedFontStyle

    font = ResolvedFontStyle(
        family="sans-serif",
        color="#000",
        size=12.0,
        weight="400",
        style="normal",
        decoration="none",
        case="none",
        line_height=1.25,
        tabular_figures=False,
    )
    return ResolvedLegendElementStyle(font=font, padding=4.0, visible=True)


def _legend_with_values(values: list[str]) -> ResolvedLegendStyle:
    """A real ResolvedLegendStyle instance carrying authored values --
    construction-final, never copied-with-update (compile/models/AGENTS.md)."""
    elem = _legend_element_style()
    return ResolvedLegendStyle(
        position=ResolvedLegendPosition(edge="right", align="start", overlay=False),
        direction="vertical",
        columns=0,
        compact_columns=1,
        label=elem,
        title=elem,
        visible=True,
        values=values,
    )


class TestResolveLegendEntries:
    def test_exact_match_filters_and_reorders(self):
        resolved, unmatched = resolve_legend_entries(["B", "A"], ["A", "B"])
        assert resolved == ["B", "A"]
        assert unmatched == []

    def test_filter_to_a_subset(self):
        resolved, unmatched = resolve_legend_entries(["A"], ["A", "B"])
        assert resolved == ["A"]
        assert unmatched == []

    def test_case_fold_match(self):
        resolved, unmatched = resolve_legend_entries(["net revenue"], ["Net Revenue"])
        assert resolved == ["Net Revenue"]
        assert unmatched == []

    def test_separator_fold_match(self):
        resolved, unmatched = resolve_legend_entries(["net-revenue"], ["net_revenue"])
        assert resolved == ["net_revenue"]
        assert unmatched == []

    def test_alias_resolves_column_name_to_label(self):
        aliases = {"net revenue": frozenset({"net_revenue", "net revenue"})}
        resolved, unmatched = resolve_legend_entries(
            ["net_revenue"], ["net revenue"], aliases
        )
        assert resolved == ["net revenue"]
        assert unmatched == []

    def test_no_match_is_reported_not_dropped(self):
        resolved, unmatched = resolve_legend_entries(["C"], ["A", "B"])
        assert resolved == []
        assert unmatched == ["C"]

    def test_partial_match_reports_only_the_miss(self):
        resolved, unmatched = resolve_legend_entries(["A", "C"], ["A", "B"])
        assert resolved == ["A"]
        assert unmatched == ["C"]

    def test_exact_spelling_wins_even_when_another_entry_would_fold_match(self):
        # "Total" is byte-identical to one domain entry; the fact that it
        # would ALSO fold-match "total" (same casefold) never matters --
        # the exact tier is deterministic and runs first.
        resolved, unmatched = resolve_legend_entries(["Total"], ["Total", "total"])
        assert resolved == ["Total"]
        assert unmatched == []

    def test_exact_match_wins_even_when_a_different_entrys_alias_collides(self):
        # A base `y: revenue` and a layer `y: revenue, label: "revenue
        # trend"` share the y-column name "revenue" as both a literal
        # domain entry AND an alias of the OTHER entry -- the literal
        # entry's own name must still resolve deterministically, not fall
        # to the fold tier and find two candidates.
        aliases = {"revenue trend": frozenset({"revenue trend", "revenue"})}
        resolved, unmatched = resolve_legend_entries(
            ["revenue"], ["revenue", "revenue trend"], aliases
        )
        assert resolved == ["revenue"]
        assert unmatched == []

    def test_ambiguous_fold_match_is_unmatched_not_guessed(self):
        # No entry is an EXACT spelling of the authored token, so it falls
        # to the fold tier; two domain entries fold to the same key there,
        # and an authored token that matches both is left unmatched --
        # picking a winner would silently look correct while matching the
        # wrong series.
        aliases = {"Revenue": frozenset({"revenue"})}
        resolved, unmatched = resolve_legend_entries(
            ["REVENUE"], ["Revenue", "revenue"], aliases
        )
        assert resolved == []
        assert unmatched == ["REVENUE"]

    def test_fold_tier_carries_the_exact_tiers_own_name_precedence(self):
        # domain=["value", "target", "Target trend"], with "Target trend"
        # aliased to its own y-column name "target". The literal token
        # "target" resolves cleanly via the exact tier (own_names always
        # wins over any alias). A CASE-DIFFERENT spelling of that exact
        # same domain entry ("Target") must resolve just as cleanly at the
        # fold tier -- not collide with "Target trend"'s alias of the same
        # spelling and come out ambiguous.
        aliases = {"Target trend": frozenset({"Target trend", "target"})}
        resolved, unmatched = resolve_legend_entries(
            ["Target"], ["value", "target", "Target trend"], aliases
        )
        assert resolved == ["target"]
        assert unmatched == []

    def test_two_different_entries_aliased_to_the_same_spelling_is_unmatched(self):
        # Two DIFFERENT domain entries can share one alias that is no
        # entry's own name -- an overlay with layers: [{y: target, label:
        # Actual}, {y: target, label: Forecast}] gives "Actual" and
        # "Forecast" both the alias "target" (their shared y-column
        # name). Pass 2's `exact_to_entry[spelling] = None` poisoning is
        # the only thing stopping the exact tier from binding "target" to
        # whichever alias source it enumerated first; deleting those two
        # lines leaves this silently picking one instead of leaving it
        # unmatched.
        aliases = {
            "Actual": frozenset({"Actual", "target"}),
            "Forecast": frozenset({"Forecast", "target"}),
        }
        resolved, unmatched = resolve_legend_entries(
            ["target"], ["Actual", "Forecast"], aliases
        )
        assert resolved == []
        assert unmatched == ["target"]


class TestApplyLegendEntryOrder:
    def test_no_authored_values_pins_engine_order(self):
        enc = {"legend": {}}
        apply_legend_entry_order(enc, ["B", "A"], authored=None)
        assert enc["legend"]["values"] == ["B", "A"]

    def test_authored_values_survive_instead_of_being_clobbered(self):
        enc = {"legend": {}}
        apply_legend_entry_order(enc, ["A", "B"], authored=["B", "A"])
        assert enc["legend"]["values"] == ["B", "A"]

    def test_no_op_when_legend_suppressed(self):
        enc = {"legend": None}
        apply_legend_entry_order(enc, ["A", "B"], authored=None)
        assert enc["legend"] is None

    def test_never_raises_and_falls_back_to_engine_order(self):
        enc = {"legend": {}}
        apply_legend_entry_order(enc, ["A", "B"], authored=["nope"])
        assert enc["legend"]["values"] == ["A", "B"]

    def test_partial_match_keeps_the_matched_subset(self):
        enc = {"legend": {}}
        apply_legend_entry_order(enc, ["A", "B"], authored=["B", "nope"])
        assert enc["legend"]["values"] == ["B"]

    def test_explicit_empty_authored_list_is_respected_not_overwritten(self):
        # An authored `values: []` is a real, if odd, author choice -- it
        # must not be silently replaced by the full engine order the way
        # an all-unmatched list is.
        enc = {"legend": {}}
        apply_legend_entry_order(enc, ["A", "B"], authored=[])
        assert enc["legend"]["values"] == []

    def test_unauthored_order_with_a_collision_dedupes_the_pin(self):
        # An overlay's shared datum-scale domain can legitimately repeat one
        # label (base + a layer whose labels collide, e.g. two y: fields
        # both named "tickets") -- Vega's own domain-inference dedupes that
        # for an unpinned legend; an explicit pin has to match, or the
        # collision renders as two identical swatches.
        enc = {"legend": {}}
        apply_legend_entry_order(enc, ["tickets", "tickets"], authored=None)
        assert enc["legend"]["values"] == ["tickets"]

    def test_resolved_authored_duplicates_are_deduped(self):
        # Two authored spellings that both resolve to the same domain entry
        # must not double the swatch either.
        enc = {"legend": {}}
        apply_legend_entry_order(enc, ["A", "B"], authored=["A", "a"])
        assert enc["legend"]["values"] == ["A"]


class TestApplyColorLegendDropValues:
    def test_drop_values_strips_the_authored_list(self):
        legend = _legend_with_values(["a", "b"])
        enc: dict = {}
        apply_color_legend(enc, legend, drop_values=True)
        assert "values" not in enc["legend"]

    def test_without_drop_values_the_authored_list_survives(self):
        legend = _legend_with_values(["a", "b"])
        enc: dict = {}
        apply_color_legend(enc, legend)
        assert enc["legend"]["values"] == ["a", "b"]


class TestCategoricalColorEncoding:
    """The shared gate every family re-derived before calling
    ``apply_legend_entry_order``: a series-mode channel, a nominal or
    ordinal VL type, and a bound field. Covers the spellings it replaces
    across bar/line/area/pie/scatter/heatmap (``color_ch.mode ==
    "series"`` plus a per-family ``enc.get("type")`` / ``color_enc_type``
    check plus a per-family field truthiness check).
    """

    def _series_channel(self, data_field: str = "category") -> ResolvedStyleChannel:
        return ResolvedStyleChannel(
            channel="color", mode="series", data_field=data_field
        )

    def test_nominal_series_with_field_is_categorical(self):
        assert categorical_color_encoding(self._series_channel(), "nominal") is True

    def test_ordinal_series_with_field_is_categorical(self):
        assert categorical_color_encoding(self._series_channel(), "ordinal") is True

    def test_quantitative_series_is_not_categorical(self):
        assert (
            categorical_color_encoding(self._series_channel(), "quantitative") is False
        )

    def test_missing_field_is_not_categorical(self):
        assert (
            categorical_color_encoding(self._series_channel(data_field=""), "nominal")
            is False
        )

    def test_non_series_mode_is_not_categorical(self):
        ch = ResolvedStyleChannel(channel="color", mode="literal", literal_value="red")
        assert categorical_color_encoding(ch, "nominal") is False

    def test_no_color_channel_is_not_categorical(self):
        assert categorical_color_encoding(None, "nominal") is False

    def test_no_encoding_type_is_not_categorical(self):
        assert categorical_color_encoding(self._series_channel(), None) is False
