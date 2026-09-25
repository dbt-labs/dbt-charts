"""Board-level category→color bindings: typed model, token resolution, planner.

Covers the compile-side half of consistent
category color mapping:

  - ``CategoryColorBinding`` as an authored ``style.charts.category_colors:`` entry
  - value validation (palette token *or* literal hex; anything else rejected)
  - palette-token resolution through the board style cascade
  - ``plan_category_colors`` — the pure planner that turns per-chart observed
    values plus the authored block into one board-wide slot assignment per
    field, including the two-or-more-charts threshold gate and palette
    exhaustion.

The render-side half (scale injection) lives in
``dbt-charts/tests/core/render/test_category_color_scale_injection.py``.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from dbt_charts.core.colors import is_color_token
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorBinding,
    color_at,
)
from dbt_charts.core.compile.resolve.style.category_colors import (
    CategoryColorScale,
    plan_category_colors,
)
from dbt_charts.core.compile.resolve.style.palette import UnknownColorError
from dbt_charts.core.compile.resolve.style.tokens import _resolve_one_color_token
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_CATEGORY_COLOR_PALETTE_EXHAUSTED,
    ERR_CATEGORY_COLOR_PIN_DUPLICATE,
)

_VALID_BOARD_BASE = {"text": "hello"}

# Three stops, deliberately not any real theme palette — see the repo rule
# against pinning theme values in tests.
_PALETTE = ("#111111", "#222222", "#333333")


def _binding(**values: str) -> CategoryColorBinding:
    return CategoryColorBinding(values=dict(values))


class TestBindingModel:
    """The authored ``style.charts.category_colors.<field>.values:`` surface."""

    def test_accepts_literal_hex(self) -> None:
        b = _binding(Electronics="#1f77b4", Tools="#ff7f0e")
        assert b.values["Electronics"] == "#1f77b4"

    def test_accepts_palette_token(self) -> None:
        # Unresolved at model level — token resolution needs theme context.
        b = _binding(Electronics="category[1]", Tools="dbt-grays.gray-60")
        assert b.values["Tools"] == "dbt-grays.gray-60"

    def test_malformed_hex_rejected(self) -> None:
        with pytest.raises(ValidationError, match="#ggg"):
            _binding(Electronics="#ggg")

    def test_arbitrary_string_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not-a-color"):
            _binding(Electronics="not-a-color")

    def test_value_order_is_preserved(self) -> None:
        """Domain order is authored order — the block is an ordered mapping."""
        b = _binding(Tools="#111111", Electronics="#222222", Accessories="#333333")
        assert list(b.values) == ["Tools", "Electronics", "Accessories"]

    def test_every_token_the_docs_offer_actually_resolves(self) -> None:
        """A description naming a token nobody can use is a false claim.

        `dbt-grays.gray-60` shipped in this field's description and in the
        validator's own error message; `dbt-grays` has no `gray-60` alias, so
        an author who copied the example got an UnknownColorError. The example
        tokens are documentation, so they are held to the same bar as the
        grammar they illustrate.
        """
        try:
            _binding(Electronics="not-a-color")
        except ValidationError as exc:
            validator_message = str(exc)
        else:  # pragma: no cover — the validator above always raises
            raise AssertionError("expected the value validator to reject this")
        described = CategoryColorBinding.model_fields["values"].description or ""
        offered = {
            token
            for token in re.findall(r"`([^`]+)`", described + validator_message)
            if is_color_token(token)
        }
        assert offered, "the description should illustrate at least one token"
        style = get_theme_style()
        for token in sorted(offered):
            # The cascade's own resolver, not `color_from_theme` alone —
            # a `palette.slot` address like `dbt-grays.muted` resolves on
            # the direct path before role indirection is tried.
            _resolve_one_color_token(token, style.palettes, style.roles)

    def test_extra_subkey_rejected(self) -> None:
        """There is no `overrides:` subkey — authors edit `values:` directly."""
        with pytest.raises(ValidationError):
            CategoryColorBinding.model_validate(
                {"values": {"Electronics": "#111111"}, "overrides": {}}
            )


class TestAuthoredSurface:
    """The block reaches ``AuthoredBoard`` through the generated StylePatch."""

    def test_board_accepts_category_colors_block(self) -> None:
        board = AuthoredBoard.model_validate(
            {
                **_VALID_BOARD_BASE,
                "style": {
                    "charts": {
                        "category_colors": {
                            "category": {"values": {"Electronics": "#111111"}}
                        }
                    }
                },
            }
        )
        assert board.style is not None
        assert board.style.charts is not None
        assert board.style.charts.category_colors is not None
        binding = board.style.charts.category_colors["category"]
        assert binding.values == {"Electronics": "#111111"}

    def test_board_rejects_malformed_value(self) -> None:
        with pytest.raises(ValidationError, match="#ggg"):
            AuthoredBoard.model_validate(
                {
                    **_VALID_BOARD_BASE,
                    "style": {
                        "charts": {
                            "category_colors": {"category": {"values": {"E": "#ggg"}}}
                        }
                    },
                }
            )


class TestTokenResolution:
    """Tokens resolve to hex through the board style cascade.

    ``_resolve_tokens_on_patch`` deliberately does not descend into dicts
    (tokens.py); ``category_colors`` is the first color-bearing dict on
    ``ChartsStyle``, so this pins that it is wired in explicitly rather than
    passed through raw.
    """

    def test_palette_token_resolves_to_hex(self) -> None:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.tokens import (
            _resolve_tokens_on_patch,
        )

        base = get_theme_style()
        patch = StylePatch.model_validate(
            {
                "charts": {
                    "category_colors": {
                        "category": {"values": {"Electronics": "category[1]"}}
                    }
                }
            }
        )
        resolved = _resolve_tokens_on_patch(patch, base)
        got = resolved.charts.category_colors["category"].values["Electronics"]
        assert got.startswith("#")

    def test_literal_hex_passes_through_unchanged(self) -> None:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.tokens import (
            _resolve_tokens_on_patch,
        )

        base = get_theme_style()
        patch = StylePatch.model_validate(
            {
                "charts": {
                    "category_colors": {"category": {"values": {"Tools": "#abcdef"}}}
                }
            }
        )
        resolved = _resolve_tokens_on_patch(patch, base)
        assert resolved.charts.category_colors["category"].values["Tools"] == "#abcdef"

    def test_unresolvable_token_raises(self) -> None:
        """Same failure mode any other unresolvable style color token gets."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.tokens import (
            _resolve_tokens_on_patch,
        )

        base = get_theme_style()
        patch = StylePatch.model_validate(
            {
                "charts": {
                    "category_colors": {
                        "category": {"values": {"E": "bogus-palette.3"}}
                    }
                }
            }
        )
        with pytest.raises(UnknownColorError, match="bogus-palette"):
            _resolve_tokens_on_patch(patch, base)

    @pytest.mark.parametrize("theme_name", ["clarity", "stark"])
    def test_single_series_token_matches_the_themes_single_series_ink(
        self, theme_name: str
    ) -> None:
        """`single_series[1]` pins a category to whatever ink a plain
        one-series bar gets on this theme, without naming the theme's current
        palette token."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.tokens import (
            _resolve_tokens_on_patch,
        )

        base = get_theme_style(theme_name)
        patch = StylePatch.model_validate(
            {
                "charts": {
                    "category_colors": {
                        "category": {"values": {"Total": "single_series[1]"}}
                    }
                }
            }
        )
        resolved = _resolve_tokens_on_patch(patch, base)
        got = resolved.charts.category_colors["category"].values["Total"]
        assert got == base.charts.color.categorical.single_series_palette[0]


class TestPlanner:
    """``plan_category_colors`` — observed values + authored block → scales.

    A value's identity is its palette SLOT, not a hex or a domain position —
    see ``CategoryColorScale``'s docstring. These tests assert on
    ``scale.slots``/``scale.overrides`` (plus the color a slot resolves to
    against ``_PALETTE``, via ``color_at``), never on a ``domain``/``range``
    pair — that shape no longer exists.
    """

    def test_two_charts_sharing_a_field_get_one_scale(self) -> None:
        plan = plan_category_colors(
            [{"category": ("Electronics", "Tools")}, {"category": ("Accessories",)}],
            authored={},
            palette=_PALETTE,
        )
        assert plan["category"] == CategoryColorScale(
            field="category",
            slots={"Electronics": 0, "Tools": 1, "Accessories": 2},
            overrides={},
        )

    def test_same_value_gets_same_color_regardless_of_chart(self) -> None:
        """The whole point: one value, one swatch, across every chart."""
        plan = plan_category_colors(
            [
                {"category": ("Accessories", "Electronics", "Tools")},
                {"category": ("Electronics", "Tools")},
                {"category": ("Electronics",)},
            ],
            authored={},
            palette=_PALETTE,
        )
        scale = plan["category"]
        assert color_at(scale, "Electronics", _PALETTE) == _PALETTE[1]

    def test_threshold_gate_single_chart_field_gets_no_binding(self) -> None:
        plan = plan_category_colors(
            [
                {"category": ("Electronics",), "region": ("North",)},
                {"category": ("Tools",)},
            ],
            authored={},
            palette=_PALETTE,
        )
        assert "category" in plan
        assert "region" not in plan

    def test_authored_field_binds_below_the_threshold(self) -> None:
        """An authored entry is intent, so it applies even on one chart."""
        plan = plan_category_colors(
            [{"region": ("North",)}],
            authored={"region": _binding(North="#111111")},
            palette=_PALETTE,
        )
        scale = plan["region"]
        assert set(scale.slots) == {"North"}
        assert color_at(scale, "North", _PALETTE) == "#111111"

    def test_authored_order_leads_and_new_values_append(self) -> None:
        plan = plan_category_colors(
            [{"category": ("Outdoor", "Electronics")}, {"category": ("Tools",)}],
            authored={"category": _binding(Electronics="#aa0000", Tools="#bb0000")},
            palette=_PALETTE,
        )
        scale = plan["category"]
        # Authored order first, then discovered — same order the old
        # `domain` carried, now the dict's own insertion order.
        assert list(scale.slots) == ["Electronics", "Tools", "Outdoor"]
        # Pins are literals outside the palette: they land in `overrides` and
        # still burn a slot, so nothing else claims their companion ink.
        assert scale.overrides == {"Electronics": "#aa0000", "Tools": "#bb0000"}
        assert color_at(scale, "Electronics", _PALETTE) == "#aa0000"
        assert color_at(scale, "Tools", _PALETTE) == "#bb0000"
        # Pins outside the palette still burn a slot in iteration order (0,
        # 1) even though their fill is the literal override — the discovered
        # value takes the next free one.
        assert color_at(scale, "Outdoor", _PALETTE) == _PALETTE[2]

    def test_discovered_value_never_reuses_a_pinned_swatch(self) -> None:
        """Two categories sharing a color is the confusion this removes."""
        plan = plan_category_colors(
            [{"category": ("Outdoor", "Electronics")}, {"category": ("Outdoor",)}],
            authored={"category": _binding(Electronics=_PALETTE[0])},
            palette=_PALETTE,
        )
        scale = plan["category"]
        # The pin names a palette member: it claims THAT slot rather than
        # landing in overrides, freeing the discovered value to take the next.
        assert scale.overrides == {}
        assert scale.slots == {"Electronics": 0, "Outdoor": 1}
        colors = {color_at(scale, v, _PALETTE) for v in scale.slots}
        assert len(colors) == len(scale.slots)

    def test_a_pin_naming_a_value_the_board_never_draws_changes_nothing(
        self,
    ) -> None:
        """A misspelled pin used to seat a phantom and recolor everything else.

        The reported shape: pinning `growth` against a `Growth` column was
        accepted, claimed a slot nothing drew, and pushed every real value
        along one, so adding a second bad pin moved the whole board again.
        Such a pin is now simply not seated, which leaves the board exactly
        where it would be with no pins at all.
        """
        observations = [{"series": ("Core", "Growth")}] * 2
        unpinned = plan_category_colors(observations, {}, _PALETTE)
        one_bad = plan_category_colors(
            observations, {"series": _binding(growth=_PALETTE[1])}, _PALETTE
        )
        two_bad = plan_category_colors(
            observations,
            {"series": _binding(growth=_PALETTE[1], core=_PALETTE[2])},
            _PALETTE,
        )
        assert one_bad["series"].slots == unpinned["series"].slots
        assert two_bad["series"].slots == unpinned["series"].slots

    def test_a_dropped_pin_is_recorded_on_the_scale(self) -> None:
        """The drop stays silent for color assignment but not for the author.

        `unseen_pins` is the signal the render-time detector reads to warn —
        the scale must carry the dropped value and its authored color even
        though it never claims a slot.
        """
        observations = [{"series": ("Core", "Growth")}] * 2
        plan = plan_category_colors(
            observations,
            {"series": _binding(growth=_PALETTE[1], core=_PALETTE[2])},
            _PALETTE,
        )
        # "growth" (mis-cased against "Growth") never matches a drawn value;
        # "core" mis-cased against "Core" the same way.
        assert plan["series"].unseen_pins == {
            "growth": _PALETTE[1],
            "core": _PALETTE[2],
        }

    def test_a_scale_whose_pins_all_match_has_no_unseen_pins(self) -> None:
        plan = plan_category_colors(
            [{"category": ("Electronics", "Tools")}] * 2,
            authored={"category": _binding(Electronics="#aa0000")},
            palette=_PALETTE,
        )
        assert plan["category"].unseen_pins == {}

    def test_a_pin_never_moves_when_another_pin_is_added(self) -> None:
        """Adding a second pin must leave the first one exactly where it was."""
        one = plan_category_colors(
            [{"series": ("Core", "Growth")}, {"series": ("Core", "Growth")}],
            authored={"series": _binding(Growth=_PALETTE[1])},
            palette=_PALETTE,
        )
        two = plan_category_colors(
            [{"series": ("Core", "Growth")}, {"series": ("Core", "Growth")}],
            authored={"series": _binding(Growth=_PALETTE[1], Core=_PALETTE[2])},
            palette=_PALETTE,
        )
        assert one["series"].slots["Growth"] == two["series"].slots["Growth"] == 1
        assert two["series"].slots["Core"] == 2

    def test_a_pin_on_a_value_todays_data_lacks_is_skipped_not_raised(self) -> None:
        """The planner sees one render's rows, so absence is not a typo.

        A variable filter, or a sibling chart whose query failed and was
        skipped, removes a real value from the observed set. Raising there
        would blank the whole board over a typo that does not exist.
        """
        plan = plan_category_colors(
            [{"category": ("Electronics",)}] * 2,
            authored={"category": _binding(Electronics="#111111", Retired="#222222")},
            palette=_PALETTE,
        )
        assert set(plan["category"].slots) == {"Electronics"}
        assert color_at(plan["category"], "Electronics", _PALETTE) == "#111111"

    def test_a_pin_on_a_field_no_chart_draws_is_ignored(self) -> None:
        """`dct render --chart` narrows the layout; board pins outlive it.

        A field nothing on screen encodes has no values to check a pin
        against and no scale to build, so it is skipped rather than raising.
        """
        plan = plan_category_colors(
            [{"category": ("Electronics",)}, {"category": ("Tools",)}],
            authored={"region": _binding(North="#111111")},
            palette=_PALETTE,
        )
        assert "region" not in plan

    def test_dropping_a_value_does_not_reflow_the_others(self) -> None:
        """Chart 2 losing Accessories must not shift Electronics/Tools."""
        full = plan_category_colors(
            [{"c": ("Accessories", "Electronics", "Tools")}, {"c": ("Electronics",)}],
            authored={},
            palette=_PALETTE,
        )
        reduced = plan_category_colors(
            [{"c": ("Accessories", "Electronics", "Tools")}, {"c": ("Tools",)}],
            authored={},
            palette=_PALETTE,
        )
        assert full["c"] == reduced["c"]

    def test_too_many_values_declines_to_bind(self) -> None:
        """Binding is volunteered, so it declines rather than breaking a board.

        Colors must stay distinct, so a field the palette cannot seat gets no
        binding at all and keeps the chart-local coloring it had before this
        feature existed — the same way the two-chart threshold declines.
        """
        values = tuple(f"v{i}" for i in range(len(_PALETTE) + 1))
        plan = plan_category_colors(
            [{"category": values}, {"category": values}],
            authored={},
            palette=_PALETTE,
        )
        assert plan == {}

    def test_too_many_values_raises_when_the_author_named_the_field(self) -> None:
        """An authored field is explicit intent — silence would be wrong here."""
        values = tuple(f"v{i}" for i in range(len(_PALETTE) + 1))
        with pytest.raises(ChartDataError, match="4 values") as raised:
            plan_category_colors(
                [{"category": values}, {"category": values}],
                authored={"category": _binding(v0="#aa0000")},
                palette=_PALETTE,
            )
        assert raised.value.code is ERR_CATEGORY_COLOR_PALETTE_EXHAUSTED

    def test_no_observations_yields_no_scales(self) -> None:
        assert plan_category_colors([], authored={}, palette=_PALETTE) == {}

    def test_two_pins_naming_the_same_color_raise(self) -> None:
        """Two categories pinned to the identical color would share a swatch
        -- the one thing CategoryColorScale guarantees never happens. An
        authored field is explicit intent, so this raises rather than
        silently seating both on one slot (same policy as the over-capacity
        branch above: the author named it, so quiet is the wrong kind of
        silence)."""
        with pytest.raises(
            ChartDataError, match="Electronics.*Tools|Tools.*Electronics"
        ) as raised:
            plan_category_colors(
                [{"category": ("Electronics", "Tools")}],
                authored={"category": _binding(Electronics="#111111", Tools="#111111")},
                palette=_PALETTE,
            )
        assert raised.value.code is ERR_CATEGORY_COLOR_PIN_DUPLICATE

    def test_two_pins_naming_different_colors_do_not_raise(self) -> None:
        plan = plan_category_colors(
            [{"category": ("Electronics", "Tools")}],
            authored={"category": _binding(Electronics="#111111", Tools="#222222")},
            palette=_PALETTE,
        )
        scale = plan["category"]
        assert scale.slots["Electronics"] != scale.slots["Tools"]

    def test_a_pin_in_a_different_case_than_the_palette_claims_that_slot(self) -> None:
        """`.casefold()` in the palette-slot lookup: a pin of "#ABCDEF" and a
        palette stop of "#abcdef" are the same swatch, so the pin must claim
        that palette slot rather than being treated as an out-of-palette
        override (which would let a second category claim the companion ink
        that sits alongside it)."""
        mixed_case_palette = ("#AbCdEf", "#222222", "#333333")
        plan = plan_category_colors(
            [{"category": ("Electronics", "Tools")}],
            authored={"category": _binding(Electronics="#ABCDEF")},
            palette=mixed_case_palette,
        )
        scale = plan["category"]
        assert scale.overrides == {}
        assert scale.slots["Electronics"] == 0


class TestAuthoredPinsAcrossNestedBoards:
    """``execute.category_colors._authored_pins`` recurses into nested boards
    and merges the result with the root's own pins, root winning a conflict
    on the same field (it's the outer scope). Every other test board in this
    repo is flat, so deleting the recursion (or the root-wins ordering) is
    undetectable by the rest of the suite -- this pins both directly.
    """

    # Root authors `region` only; the nested board authors `category` (root
    # never touches it) AND its own conflicting `region` pin.
    _BOARD = """
title: Root board

style:
  charts:
    category_colors:
      region:
        values:
          North: "#111111"

queries:
  q:
    type: values
    rows:
      - {category: Electronics, region: North, x: a, y: 1}
      - {category: Tools, region: South, x: a, y: 2}

charts:
  root_chart:
    type: bar
    query: q
    x: x
    y: y
    color: category
  nested_chart:
    type: bar
    query: q
    x: x
    y: y
    color: category

rows:
  - root_chart
  - style:
      charts:
        category_colors:
          category:
            values:
              Tools: "#222222"
          region:
            values:
              North: "#999999"
    cols:
      - nested_chart
"""

    def _pins(self) -> dict[str, CategoryColorBinding]:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.category_colors import _authored_pins

        result = compile(self._BOARD)
        assert result.success and result.board is not None, result.errors
        return _authored_pins(result.board)

    def test_a_nested_only_pin_is_honored(self) -> None:
        """`category` is authored only inside the nested board -- deleting the
        recursion would silently lose it (the root never mentions it)."""
        pins = self._pins()
        assert pins["category"].values == {"Tools": "#222222"}

    def test_a_root_pin_beats_a_conflicting_nested_pin(self) -> None:
        """Both root and the nested board author `region` -- root, the outer
        scope, must win over the nested value for the same field."""
        pins = self._pins()
        assert pins["region"].values == {"North": "#111111"}
