"""Tests for D3 formatting utilities.

Tests the dbt_charts.render.format_utils module for formatting values
using D3-style format specifications and theme-defined format aliases.
"""

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style, list_built_in_themes
from dbt_charts.core.compile.format import (
    finalize_kpi_value_format,
    get_format_prefix_suffix,
    resolve_format_parts,
    resolve_format_parts_for_values,
    resolve_label_format,
)
from dbt_charts.core.compile.models.primitives import FormatConfig, ResolvedFormat
from dbt_charts.core.diagnostics.codes_render import ERR_PERCENT_RANGE
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.format_utils import (
    format_d3,
    format_kpi_parts,
    format_value,
    resolve_format,
)
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_NUMBER_NAMES,
    PREDEFINED_SPECS,
    PredefinedNumberFormat,
)

_ALIAS_FORMATS = {"tilde": "~s", "money": "$,.2f"}


class TestResolveLabelFormat:
    """resolve_label_format: alias-vs-literal is the only register signal.

    House (narrative) fires exactly when the raw string is a theme alias
    resolving to an SI spec — never based on who set it. A hand-typed literal
    SI spec is always a native opt-out, alias or not otherwise.

    ``resolved`` is a ``FormatConfig`` (not a bare string): the affix
    (``prefix``/``suffix``) rides forward alongside the spec now, since d3's
    grammar admits only "$"/"#" as a spec's own symbol character and any
    other affix has nowhere else to survive this call. Every case
    below authors no affix, so ``resolved.prefix``/``.suffix`` stay ``None``.
    """

    def test_alias_si_is_house(self):
        # "number" is a predefined name, not a user alias — hits predefined
        # path regardless of the formats dict; the _ALIAS_FORMATS dict is
        # irrelevant here but kept for API-contract clarity.
        resolved, is_house = resolve_label_format("number", _ALIAS_FORMATS)
        assert resolved.spec == ".3~s"
        assert is_house is True

    def test_literal_si_is_not_house(self):
        # Inline d3 specs are native (three-way contract): no trim injection,
        # no house-notation register — .2s stays .2s. No affix authored --
        # bare string.
        resolved, is_house = resolve_label_format(".2s", _ALIAS_FORMATS)
        assert resolved.spec == ".2s"
        assert is_house is False

    def test_non_si_alias_is_not_house(self):
        # No affix authored -- bare string.
        resolved, is_house = resolve_label_format("money", _ALIAS_FORMATS)
        assert resolved.spec == "$,.2f"
        assert is_house is False

    def test_non_si_literal_is_not_house(self):
        # No affix authored -- bare string.
        resolved, is_house = resolve_label_format("$,.2f", _ALIAS_FORMATS)
        assert resolved.spec == "$,.2f"
        assert is_house is False

    def test_none_is_not_house(self):
        resolved, is_house = resolve_label_format(None, _ALIAS_FORMATS)
        assert resolved is None
        assert is_house is False

    def test_unknown_key_not_in_formats_treated_as_literal(self):
        # A string that isn't a real alias and isn't valid d3 grammar either
        # is a compile-time authoring error elsewhere; resolve_label_format
        # itself just reports is_alias=False for it (not house). No affix
        # authored -- bare string.
        resolved, is_house = resolve_label_format("~s", None)
        assert resolved.spec == "~s"
        assert is_house is False

    def test_various_alias_si_shapes(self):
        # User-alias keys that are not predefined names: native d3 path.
        # No trim injection, not house — the spec is returned verbatim.
        formats = {"a": "~s", "b": ".2~s", "c": ".3s", "d": "s"}
        expected_resolved = {"a": "~s", "b": ".2~s", "c": ".3s", "d": "s"}
        for key in formats:
            resolved, is_house = resolve_label_format(key, formats)
            assert resolved.spec == expected_resolved[key], key
            assert is_house is False, key

    def test_format_config_object_uses_spec_for_alias_check(self):
        # "number" is a predefined name; FormatConfig.spec is the lookup key.
        # No affix authored -- bare string.
        resolved, is_house = resolve_label_format(
            FormatConfig(spec="number"), _ALIAS_FORMATS
        )
        assert resolved.spec == ".3~s"
        assert is_house is True

    def test_authored_prefix_survives_alongside_the_resolved_spec(self):
        # d3's grammar admits only "$"/"#" as a spec's own symbol character
        # (libs/d3-format's spec.py).
        resolved, is_house = resolve_label_format(
            FormatConfig(spec=",.0f", prefix="€"), _ALIAS_FORMATS
        )
        assert (resolved.spec, resolved.prefix, resolved.suffix) == (",.0f", "€", "")
        assert is_house is False

    def test_authored_suffix_survives_through_a_style_formats_alias(self):
        # A bare alias name whose style.formats target is itself a
        # FormatConfig with an affix.
        formats = {"eur_trailing": FormatConfig(spec=",.0f", suffix=" €")}
        resolved, is_house = resolve_label_format("eur_trailing", formats)
        assert (resolved.spec, resolved.prefix, resolved.suffix) == (",.0f", "", " €")
        assert is_house is False

    def test_authored_notation_survives_alongside_the_resolved_spec(self):
        # An authored notation override on a non-house literal spec must also ride
        # forward on the returned FormatConfig.
        resolved, is_house = resolve_label_format(
            FormatConfig(spec=".3~s", notation="narrative"), _ALIAS_FORMATS
        )
        assert (resolved.spec, resolved.notation) == (".3~s", "narrative")
        assert is_house is False


class TestRetiredFormatSuccessors:
    """The retired-name hint is the entire migration path for this rename.

    A value rename cannot be a schema migration — `value_map` is total over its
    field's domain and `format:` is an open string — so the diagnostic is all an
    author gets. Delete the table and CI stays green while every hint regresses
    to the fuzzy match the module's own comment warns against: the nearest string
    to `currency_compact` is `currency_whole`, which compiles clean and silently
    drops compaction.
    """

    # Literal expectations, not a loop over the table under test: reading the
    # successor back out of `RETIRED_FORMAT_SUCCESSORS` makes every assertion
    # true of any table, including an empty one and a corrupted one.
    EXPECTED = {
        "currency_compact": "'currency_compact' was renamed to 'currency'.",
        "compact": "'compact' was renamed to 'number'.",
        "number_default": "'number_default' was renamed to 'number'.",
    }

    def test_each_retired_name_names_its_successor(self):
        from dbt_charts.core.diagnostics.hints import suggest_close_format

        available = sorted(PREDEFINED_NUMBER_NAMES)
        for retired, expected in self.EXPECTED.items():
            assert suggest_close_format(retired, available) == expected

    def test_the_table_covers_exactly_the_names_this_release_retired(self):
        """A deleted or extended table has to move this test, not slip past it."""
        from dbt_charts.core.diagnostics.hints import RETIRED_FORMAT_SUCCESSORS

        assert set(RETIRED_FORMAT_SUCCESSORS) == set(self.EXPECTED)

    def test_every_successor_still_resolves(self):
        from dbt_charts.core.diagnostics.hints import RETIRED_FORMAT_SUCCESSORS

        assert RETIRED_FORMAT_SUCCESSORS, "an empty table is a deleted migration path"
        for retired, successor in RETIRED_FORMAT_SUCCESSORS.items():
            assert successor in PREDEFINED_NUMBER_NAMES, (
                f"{retired} points at {successor}, which is not a live name"
            )

    def test_no_retired_name_survives_in_the_vocabulary(self):
        from dbt_charts.core.diagnostics.hints import RETIRED_FORMAT_SUCCESSORS

        assert RETIRED_FORMAT_SUCCESSORS, "an empty table is a deleted migration path"
        assert not (set(RETIRED_FORMAT_SUCCESSORS) & set(PREDEFINED_NUMBER_NAMES))

    def test_a_successor_illegal_in_this_slot_is_not_offered(self):
        """`available` is scoped to the slot's half of the vocabulary.

        Offering `number` to a `time_format:` slot would trade a fuzzy wrong
        answer for a confident one.
        """
        from dbt_charts.core.diagnostics.hints import suggest_close_format

        hint = suggest_close_format("compact", ["date_short", "time_short"])
        assert hint is None or "number" not in hint

    def test_a_typo_still_gets_the_fuzzy_match(self):
        from dbt_charts.core.diagnostics.hints import suggest_close_format

        hint = suggest_close_format("currancy", sorted(PREDEFINED_NUMBER_NAMES))
        assert hint is not None and "currency" in hint


class TestResolveFormatThemeLookup:
    """resolve_format resolves aliases from a caller-supplied formats dict."""

    def test_alias_in_formats_dict(self):
        assert resolve_format("money", {"money": "$,.2f"}) == "$,.2f"

    def test_custom_alias_in_formats_dict(self):
        assert resolve_format("revenue", {"revenue": "$~s"}) == "$~s"

    def test_raw_d3_spec_passthrough_when_not_in_formats(self):
        assert resolve_format("$,.0f", {"currency": "$,.2f"}) == "$,.0f"

    def test_no_formats_dict_predefined_resolves(self):
        # Predefined names resolve via the engine spec regardless of formats dict.
        assert resolve_format("currency_full", None) == "$,.2f"

    def test_predefined_resolves_without_formats_dict(self):
        # "number" is a predefined member — trim already in spec, round_aware_spec is no-op.
        assert resolve_format("number") == ".3~s"

    def test_null_format_input_returns_empty(self):
        assert resolve_format(None, {"currency": "$,.2f"}) == ""

    def test_null_format_input_no_formats_returns_empty(self):
        assert resolve_format(None) == ""

    def test_format_config_spec_resolved_via_formats(self):
        config = FormatConfig(spec="money")
        assert resolve_format(config, {"money": "$,.2f"}) == "$,.2f"

    def test_format_config_raw_d3_passthrough(self):
        config = FormatConfig(spec="$,.2f")
        assert resolve_format(config, {"currency": "$,.2f"}) == "$,.2f"

    def test_dict_format_spec_resolved_via_formats(self):
        assert resolve_format({"spec": "money"}, {"money": "$,.2f"}) == "$,.2f"

    def test_dict_format_raw_d3_passthrough(self):
        assert resolve_format({"spec": "$,.2f"}, {"currency": "$,.2f"}) == "$,.2f"

    def test_empty_spec_returns_empty(self):
        assert resolve_format(FormatConfig(spec=None), {"currency": "$,.2f"}) == ""
        assert resolve_format(FormatConfig(spec=""), {"currency": "$,.2f"}) == ""
        assert resolve_format({"spec": ""}, {"currency": "$,.2f"}) == ""

    def test_formats_dict_case_sensitive(self):
        # Lookup is case-sensitive — theme key "Currency" != "currency"
        assert resolve_format("Currency", {"currency": "$,.2f"}) == "Currency"

    def test_unknown_alias_passthrough(self):
        # Not in formats → passthrough (d3 may reject at render time)
        assert resolve_format("~s", {"currency": "$,.2f"}) == "~s"
        assert resolve_format("+$,.2f", {"currency": "$,.2f"}) == "+$,.2f"


class TestD3Passthrough:
    """D3 format strings pass through unchanged regardless of formats dict."""

    def test_d3_formats_passed_through_with_formats(self):
        # Inline d3 specs are native — no trim, no substitution.
        fmt = {"currency": "$,.2f"}
        assert resolve_format("$,.2f", fmt) == "$,.2f"
        assert resolve_format(".1%", fmt) == ".1%"
        assert resolve_format(",.2s", fmt) == ",.2s"
        assert resolve_format(".0f", fmt) == ".0f"

    def test_d3_formats_passed_through_without_formats(self):
        assert resolve_format("$,.2f") == "$,.2f"
        assert resolve_format(".1%") == ".1%"


class TestGetFormatPrefixSuffix:
    """Tests for prefix/suffix extraction."""

    def test_string_format_no_prefix_suffix(self):
        assert get_format_prefix_suffix("currency") == ("", "")
        assert get_format_prefix_suffix("$,.2f") == ("", "")

    def test_format_config_prefix_suffix(self):
        config = FormatConfig(spec=",.0f", prefix="$", suffix=" USD")
        assert get_format_prefix_suffix(config) == ("$", " USD")

    def test_dict_prefix_suffix(self):
        fmt = {"spec": ",.0f", "prefix": "€", "suffix": " EUR"}
        assert get_format_prefix_suffix(fmt) == ("€", " EUR")

    def test_none_values(self):
        assert get_format_prefix_suffix(None) == ("", "")
        config = FormatConfig(spec=",.0f")
        assert get_format_prefix_suffix(config) == ("", "")

    def test_object_form_naming_an_alias_follows_the_alias_affix(self):
        """format: {spec: eur} (the outer object form naming an alias, with no affix of
        its own) must still reach the alias's own prefix/suffix.
        """
        formats = {"eur": FormatConfig(spec=",.0f", prefix="€")}
        assert get_format_prefix_suffix(FormatConfig(spec="eur"), formats) == (
            "€",
            "",
        )
        assert get_format_prefix_suffix({"spec": "eur"}, formats) == ("€", "")

    def test_object_form_s_own_affix_wins_over_the_alias_it_names(self):
        """An explicit affix on the outer object always wins over the alias's own."""
        formats = {"eur": FormatConfig(spec=",.0f", prefix="€")}
        assert get_format_prefix_suffix(
            FormatConfig(spec="eur", suffix=" net"), formats
        ) == ("", " net")


class TestResolvedNotation:
    """Notation follows an alias like the affix -- mirrors TestGetFormatPrefixSuffix."""

    @staticmethod
    def _notation(value, formats=None):
        return resolve_format_parts(value, formats, no_format_default=None).notation

    def test_format_config_notation(self):
        assert self._notation(FormatConfig(spec="~s", notation="narrative")) == (
            "narrative"
        )

    def test_none_values(self):
        assert self._notation(None) is None
        assert self._notation(FormatConfig(spec="~s")) is None

    def test_object_form_naming_an_alias_follows_the_alias_notation(self):
        """format: {spec: mine} (the outer object form naming an alias, with no notation
        of its own) must still reach the alias's own notation.
        """
        formats = {"mine": FormatConfig(spec="~s", notation="narrative")}
        assert self._notation(FormatConfig(spec="mine"), formats) == "narrative"
        assert self._notation({"spec": "mine"}, formats) == "narrative"

    def test_object_form_s_own_notation_wins_over_the_alias_it_names(self):
        formats = {"mine": FormatConfig(spec="~s", notation="narrative")}
        assert (
            self._notation(FormatConfig(spec="mine", notation="analytic"), formats)
            == "analytic"
        )


class TestFormatD3:
    """Tests for D3-style value formatting — d3-correct output via libs/d3-format/."""

    def test_fixed_point_formatting(self):
        assert format_d3(1234.567, ",.2f") == "1,234.57"
        assert format_d3(1234.567, ".2f") == "1234.57"
        assert format_d3(1234.567, ",.0f") == "1,235"

    def test_currency_formatting(self):
        assert format_d3(1234.56, "$,.2f") == "$1,234.56"
        assert format_d3(1000, "$,.0f") == "$1,000"

    def test_percent_formatting(self):
        assert format_d3(0.123, ".1%") == "12.3%"
        assert format_d3(0.1234, ".2%") == "12.34%"
        assert format_d3(1, ".0%") == "100%"

    def test_si_prefix_formatting(self):
        # d3's .2s = 2 significant figures. format_d3 with notation=None
        # (the default) returns raw d3 output — no house notation substitution.
        # Notation substitution only fires when the caller passes notation= explicitly.
        assert format_d3(1500000, ",.2s") == "1.5M"
        assert format_d3(1500, ",.2s") == "1.5k"
        assert format_d3(1500000000, ",.2s") == "1.5G"
        assert format_d3(1500000000000, ",.2s") == "1.5T"

    def test_custom_prefix_suffix(self):
        assert (
            format_d3(
                1234, ",.0f", prefix="$", suffix=" USD", sign_placement="before_prefix"
            )
            == "$1,234 USD"
        )
        assert format_d3(100, ",.0f", suffix=" users") == "100 users"

    def test_none_value(self):
        assert format_d3(None, ",.2f") == "—"
        assert format_d3(None, "$,.2f", prefix="$") == "$—"

    def test_empty_format(self):
        assert format_d3(1234, "") == "1234"
        assert (
            format_d3(1234, "", prefix="$", sign_placement="before_prefix") == "$1234"
        )

    def test_negative_values(self):
        # d3 uses U+2212 (−) and places sign before the currency symbol.
        assert format_d3(-1234.56, "$,.2f") == "−$1,234.56"
        assert format_d3(-0.15, ".1%") == "−15.0%"

    def test_negative_si_values(self):
        # d3 uses U+2212 minus sign. Raw d3 output with notation=None.
        assert format_d3(-1500000, ",.2s") == "−1.5M"

    def test_negative_value_with_custom_prefix_places_sign_before_prefix(self):
        # A non-d3-native prefix (e.g. "€", not embedded in the spec) must still honor
        # sign-first ordering, same as d3's own $ symbol handling in
        # test_negative_values above: "−€500", never "€−500".
        assert (
            format_d3(-500, ",.0f", prefix="€", sign_placement="before_prefix")
            == "−€500"
        )

    def test_negative_value_with_custom_suffix_stays_trailing(self):
        # Suffix already trails everything, sign included — the locale-correct
        # form authors get by choosing suffix over prefix.
        assert format_d3(-500, ",.0f", suffix=" €") == "−500 €"


class TestFormatValue:
    """Tests for the main format_value entry point — accepts formats dict."""

    def test_alias_formatting_with_formats(self):
        formats = {"money": "$,.2f", "pct": ".1%", "tilde": ",.2s"}
        assert format_value(1234567.89, "money", formats) == "$1,234,567.89"
        assert format_value(0.123, "percent", formats) == "12.3%"
        # .2s = 2 significant figures via d3_format lib
        assert format_value(1500000, "number", formats) == "1.5 M"

    def test_d3_formatting_without_formats(self):
        assert format_value(1234.56, "$,.2f") == "$1,234.56"
        assert format_value(0.123, ".1%") == "12.3%"

    def test_format_config_object(self):
        config = FormatConfig(spec=",.0f", prefix="$", suffix=" USD")
        assert format_value(1234, config) == "$1,234 USD"

    def test_dict_format_config(self):
        # Dict format config with no notation key: native d3 (no substitution).
        config = {"spec": ",.2s", "suffix": " users"}
        assert format_value(1500000, config) == "1.5M users"

    def test_none_format(self):
        assert format_value(1234, None) == "1234"

    def test_none_value_with_prefix_suffix_config(self):
        # None falls through to format_d3 which returns f"{prefix}—{suffix}".
        assert (
            format_value(None, FormatConfig(spec=".2f", prefix="£", suffix=" GBP"))
            == "£— GBP"
        )


class TestNewAliases:
    """Verify predefined format names that were previously theme aliases.

    These are now engine-owned predefined formats. The formats dict is ignored
    for these names — they always resolve through the predefined-spec path.
    """

    def test_currency_whole_resolves(self):
        assert resolve_format("currency_whole") == "$,.0f"

    def test_currency_whole_formats_value(self):
        assert format_value(1234.56, "currency_whole") == "$1,235"

    def test_currency_resolves(self):
        # Engine spec "$.3~s" — trim already set; round_aware_spec is a no-op.
        assert resolve_format("currency") == "$.3~s"

    def test_currency_formats_value(self):
        assert format_value(1_500_000, "currency") == "$1.5 M"

    def test_percent_whole_resolves(self):
        assert resolve_format("percent_whole") == ".0%"

    def test_percent_whole_formats_value(self):
        assert format_value(0.154, "percent_whole") == "15%"

    def test_percent_delta_resolves(self):
        assert resolve_format("percent_delta") == "+.1%"

    def test_percent_delta_positive_value(self):
        assert format_value(0.054, "percent_delta") == "+5.4%"

    def test_percent_delta_negative_value(self):
        assert format_value(-0.054, "percent_delta") == "−5.4%"


class TestDeltaAlias:
    """`delta` predefined format — signed integer, for KPI support deltas.

    d3's `+` sign flag forces a leading sign on positive values; the minus
    sign on negatives is d3's normal behavior (U+2212, not ASCII hyphen).
    Now engine-owned (predefined), so no theme cascade needed.
    """

    def test_delta_resolves(self):
        # Engine-owned predefined: resolves regardless of formats dict.
        assert resolve_format("delta") == "+,d"

    def test_delta_positive_value(self):
        assert format_value(31, "delta") == "+31"

    def test_delta_negative_value(self):
        assert format_value(-31, "delta") == "−31"

    @pytest.mark.parametrize(
        "theme", [t for t in list_built_in_themes() if not t.startswith("_")]
    )
    def test_delta_predefined_every_theme_formats_is_none(self, theme: str):
        """delta is now a predefined engine format, not a theme alias.
        Themes no longer carry it in style.formats."""
        theme_formats = get_theme_style(theme).formats
        assert theme_formats is None or "delta" not in theme_formats


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_value_with_formats(self):
        formats = {"money": "$,.2f", "pct": ".1%", "tilde": ",.2s"}
        assert format_value(0, "money", formats) == "$0.00"
        # .1% is not an SI spec, so round-awareness doesn't apply — 0 → "0.0%"
        assert format_value(0, "pct", formats) == "0.0%"
        # Round-aware on a predefined SI name: no SI prefix and no
        # false-precision zero. A raw `,.2s` alias keeps d3's own `0.0` — the
        # trim is the engine's, not d3's, which is what this pair pins.
        assert format_value(0, "number", formats) == "0"
        assert format_value(0, "tilde", formats) == "0.0"

    def test_very_large_numbers(self):
        # d3's .2s = 2 significant figures: 999_999_999_999 → 1.0T not 1,000B.
        # User alias "big_si" (non-predefined) → native d3 path, no notation
        # substitution — raw d3 suffix "T" stays as-is (no space).
        assert format_value(999999999999, "big_si", {"big_si": ",.2s"}) == "1.0T"
        assert format_value(1234567890123, "big_si", {"big_si": ",.2s"}) == "1.2T"

    def test_float_precision(self):
        result = format_value(0.1 + 0.2, ".1f")
        assert result == "0.3"


class TestNotationFamilies:
    """Tests for analytic and narrative notation families."""

    # --- Analytic notation (default) ---

    def test_no_default_notation_gives_raw_d3(self):
        # format_d3 without notation= returns raw d3 output — no house
        # substitution. Analytic/narrative require explicit notation= by the
        # predefined-format caller; inline specs use native d3.
        assert format_d3(1_500, ",.2s") == "1.5k"
        assert format_d3(2_500_000, ",.2s") == "2.5M"
        assert format_d3(3_500_000_000, ",.2s") == "3.5G"
        assert format_d3(4_500_000_000_000, ",.2s") == "4.5T"

    def test_analytic_notation_explicit(self):
        assert format_d3(1_500, ",.2s", notation="analytic") == "1.5 K"
        assert format_d3(2_500_000, ",.2s", notation="analytic") == "2.5 M"

    # --- Narrative notation ---

    def test_narrative_notation_suffixes(self):
        assert format_d3(1_500, ",.2s", notation="narrative") == "1.5k"
        assert format_d3(2_500_000, ",.2s", notation="narrative") == "2.5mn"
        assert format_d3(3_500_000_000, ",.2s", notation="narrative") == "3.5bn"
        assert format_d3(4_500_000_000_000, ",.2s", notation="narrative") == "4.5trn"

    def test_narrative_notation_negative(self):
        # d3 uses U+2212 minus sign
        assert format_d3(-1_500_000, ",.2s", notation="narrative") == "−1.5mn"

    def test_narrative_notation_below_threshold(self):
        assert format_d3(500, ",.2s", notation="narrative") == "500"

    # --- FormatConfig threading ---

    def test_format_config_notation_field(self):
        config = FormatConfig(spec=",.2s", notation="narrative")
        assert config.notation == "narrative"

    def test_format_config_notation_default(self):
        config = FormatConfig(spec=",.2s")
        assert config.notation is None

    def test_format_value_with_narrative_config(self):
        config = FormatConfig(spec=",.2s", notation="narrative")
        assert format_value(2_500_000, config) == "2.5mn"

    def test_format_value_with_dict_notation(self):
        config = {"spec": ",.2s", "notation": "narrative"}
        assert format_value(2_500_000, config) == "2.5mn"

    def test_format_kpi_parts_with_narrative(self):
        config = FormatConfig(spec=",.2s", notation="narrative")
        assert format_kpi_parts(2_500_000, config) == ("", "2.5", "mn")

    def test_format_value_dict_no_notation_is_native(self):
        # Dict format without notation= takes the native-d3 path: no substitution.
        assert format_value(1_500_000, {"spec": ",.2s"}) == "1.5M"

    def test_unknown_notation_raises(self):
        with pytest.raises(ValueError, match="Unknown notation"):
            format_d3(1_500, ",.2s", notation="scientific")

    def test_notation_only_affects_si_format(self):
        config = FormatConfig(spec=",.2f", notation="narrative")
        assert format_value(1_500_000, config) == "1,500,000.00"

    def test_format_value_follows_a_bare_alias_names_own_notation(self):
        """A bare alias name's own notation must reach format_value the same way
        resolve_format_parts (every Vega painter's own resolver) already follows it.
        """
        formats = {
            "eur": FormatConfig(spec=".3~s", notation="narrative", prefix="EUR ")
        }
        assert format_value(1_500_000, "eur", formats) == "EUR 1.5mn"

    def test_format_kpi_parts_follows_a_bare_alias_names_own_notation(self):
        formats = {
            "eur": FormatConfig(spec=".3~s", notation="narrative", prefix="EUR ")
        }
        # format_kpi_parts strips its own prefix lane (its 3-part decomposition
        # convention) -- "EUR", not "EUR ".
        assert format_kpi_parts(1_500_000, "eur", formats) == ("EUR", "1.5", "mn")


class TestPercentRangeGuard:
    """percent-family non-delta formats reject 0-100-shaped values instead of
    silently multiplying them by 100 (e.g. 18.2 -> "1820%").

    Threshold is 10.0 for `percent` / `percent_whole` (shares and rates where
    >10 is nearly always a SQL mistake). Delta specs (d3 `+` sign flag, e.g.
    `+.1%` = `percent_delta`) are unconditionally exempt — large deltas like
    +1200% growth (12.0) are legitimate data, not SQL mistakes.
    Callers short-circuit None before the guard.
    """

    _FORMATS = {"percent": ".1%", "percent_whole": ".0%", "percent_delta": "+.1%"}

    def test_percent_whole_rejects_0_100_shaped_value(self):
        with pytest.raises(RenderError) as exc_info:
            format_value(18.2, "percent_whole", self._FORMATS)
        assert exc_info.value.code is ERR_PERCENT_RANGE

    def test_percent_whole_ratio_value_unchanged(self):
        assert format_value(0.182, "percent_whole", self._FORMATS) == "18%"

    def test_boundary_9_9_is_a_legitimate_high_ratio(self):
        # 9.9 is below the 10.0 threshold — a genuine 990% ratio (e.g. 9x ROI),
        # not 0-100-shaped SQL data.
        assert format_value(9.9, "percent_whole", self._FORMATS) == "990%"

    def test_boundary_10_0_rejected(self):
        with pytest.raises(RenderError) as exc_info:
            format_value(10.0, "percent_whole", self._FORMATS)
        assert exc_info.value.code is ERR_PERCENT_RANGE

    def test_percent_delta_large_values_never_raise(self):
        # Delta specs (d3 + sign flag) are unconditionally exempt — no threshold.
        # +1200% growth (12.0) and -1820% (−18.2) are legitimate delta values.
        assert format_value(3.0, "percent_delta", self._FORMATS) == "+300.0%"
        assert format_value(12.0, "percent_delta", self._FORMATS) == "+1200.0%"
        assert format_value(-18.2, "percent_delta", self._FORMATS) == "−1820.0%"

    def test_negative_0_100_shaped_value_rejected(self):
        with pytest.raises(RenderError) as exc_info:
            format_value(-18.2, "percent_whole", self._FORMATS)
        assert exc_info.value.code is ERR_PERCENT_RANGE

    def test_percent_guarded_not_delta(self):
        with pytest.raises(RenderError):
            format_value(18.2, "percent", self._FORMATS)
        # percent_delta is exempt — same value renders without error
        assert format_value(18.2, "percent_delta", self._FORMATS) == "+1820.0%"

    def test_raw_d3_percent_spec_also_guarded(self):
        # Non-delta raw d3 "%" specs get the guard — failure mode identical.
        with pytest.raises(RenderError):
            format_value(18.2, ".0%")

    def test_raw_delta_d3_spec_not_guarded(self):
        # Raw d3 "+.0%" is a delta spec (d3 sign flag) — exempt from the guard.
        assert format_value(18.2, "+.0%") == "+1820%"

    def test_error_message_mentions_dividing_by_100(self):
        with pytest.raises(RenderError) as exc_info:
            format_value(18.2, "percent_whole", self._FORMATS)
        assert "100" in str(exc_info.value)

    def test_percent_number_native_formatter_not_guarded(self):
        # percent_number is a native formatter (the value already IS the
        # percent, e.g. 12.8 means 12.8%) — not part of the d3 "%"-type
        # percent family, so the range guard does not apply to it.
        assert format_value(18.2, "percent_number") == "18.2%"

    def test_format_kpi_parts_also_guarded(self):
        with pytest.raises(RenderError) as exc_info:
            format_kpi_parts(18.2, "percent_whole", self._FORMATS)
        assert exc_info.value.code is ERR_PERCENT_RANGE


class TestNotationRenameRegression:
    """Old `bi`/`editorial` names are gone; only `analytic`/`narrative` validate."""

    def test_analytic_validates(self):
        assert FormatConfig(notation="analytic").notation == "analytic"

    def test_narrative_validates(self):
        assert FormatConfig(notation="narrative").notation == "narrative"

    def test_old_bi_name_rejected(self):
        with pytest.raises(ValidationError):
            FormatConfig(notation="bi")

    def test_old_editorial_name_rejected(self):
        with pytest.raises(ValidationError):
            FormatConfig(notation="default")

    def test_format_d3_analytic_renders_analytic_register(self):
        assert format_d3(1_500, "$.2s", notation="analytic") == "$1.5 K"

    def test_format_d3_narrative_renders_narrative_register(self):
        assert format_d3(1_500_000_000, ".2s", notation="narrative") == "1.5bn"


class TestRoundAwareSignificantFigures:
    """Trim (``~``) on SI specs: baked once at resolve_format for predefined formats.

    format_d3 is a pure D3 formatter — it no longer injects ``~`` internally.
    Authors who want trailing-zero trimming on inline SI specs must write ``~``
    themselves (``".2~s"``), or use a predefined format whose resolve_format
    bakes the trim.

    format_kpi_parts uses _d3_format directly for its SI split path; trim only
    applies when the spec already carries ``~``.
    """

    def test_inline_si_without_tilde_keeps_trailing_zero(self):
        # No ~ in spec: format_d3 does not inject it — "$1.0M" (trailing zero).
        assert format_d3(1_000_000, "$,.2s") == "$1.0M"

    def test_non_clean_value_keeps_its_digits(self):
        assert format_d3(1_250_000, "$,.3s") == "$1.25M"

    def test_explicit_trim_flag_removes_trailing_zero(self):
        # Author-supplied ~: trim is applied by d3 itself.
        assert format_d3(1_000_000, "$,.2~s") == "$1M"

    def test_non_si_spec_is_unaffected(self):
        assert format_d3(1_000_000, ",.2f") == "1,000,000.00"

    def test_kpi_parts_inline_d3_no_trim_in_spec(self):
        # Inline "$,.2s" — no trim → "1.0M".
        assert format_kpi_parts(1_000_000, "$,.2s") == ("$", "1.0", "M")

    def test_kpi_parts_predefined_trim_baked(self):
        # "currency" → resolve_format returns "$.3~s" (trim already set) → "1M".
        assert format_kpi_parts(1_000_000, "currency") == ("$", "1", "M")

    def test_kpi_parts_real_digit_survives(self):
        assert format_kpi_parts(1_250_000, "$,.3s") == ("$", "1.25", "M")


class TestAboveTrillionsNarrativeFallback:
    """No accepted narrative form exists above trillions, so narrative
    borrows the analytic (spaced, capital-letter) suffix rather than emit
    d3's bare, unmapped SI letter — a shipped artifact must always speak a
    house register."""

    def test_peta_falls_back_to_analytic_form(self):
        assert format_d3(1e15, ",.2~s", notation="narrative") == "1 P"

    def test_fallback_is_never_the_bare_d3_letter(self):
        # d3's own raw SI output for 1e15 is "1P" -- the house register must
        # never let that reach a shipped artifact.
        assert format_d3(1e15, ",.2~s", notation="narrative") != "1P"

    def test_exa_zetta_yotta_all_fall_back(self):
        assert format_d3(1e18, ",.2~s", notation="narrative") == "1 E"
        assert format_d3(1e21, ",.2~s", notation="narrative") == "1 Z"
        assert format_d3(1e24, ",.2~s", notation="narrative") == "1 Y"

    def test_analytic_unaffected_by_the_fallback(self):
        assert format_d3(1e15, ",.2~s", notation="analytic") == "1 P"

    def test_kpi_parts_headline_fallback_has_no_leading_space(self):
        # format_kpi_parts splits the magnitude into its own lane; visual
        # spacing is geometry (kpi.py's dx="2"), not a string character, so
        # the lane-suffix stays bare like every other magnitude token.
        assert format_kpi_parts(1e15, {"spec": ",.2~s", "notation": "narrative"}) == (
            "",
            "1",
            "P",
        )


class TestNullZeroSharedContract:
    """Null and zero render from one shared constant, not per-surface literals."""

    def test_format_d3_null_uses_the_shared_constant(self):
        from dbt_charts.core.text.format_d3 import NULL_DISPLAY

        assert format_d3(None, ",.2f") == NULL_DISPLAY

    def test_format_kpi_parts_null_uses_the_shared_constant(self):
        from dbt_charts.core.text.format_d3 import NULL_DISPLAY

        assert format_kpi_parts(None, ",.2f") == ("", NULL_DISPLAY, "")

    def test_zero_is_a_real_value_not_the_null_display(self):
        assert format_d3(0, ",.0f") == "0"
        assert format_kpi_parts(0, ",.0f") == ("", "0", "")


class TestYearAlias:
    """A year is an identifier, not a quantity — no grouping, no SI scaling."""

    def test_year_resolves(self):
        assert resolve_format("year") == "d"

    def test_year_formats_a_value_with_no_thousands_separator(self):
        assert format_value(2025, "year") == "2025"

    @pytest.mark.parametrize(
        "theme", [t for t in list_built_in_themes() if not t.startswith("_")]
    )
    def test_year_alias_theme_formats_is_none_or_has_no_year_key(self, theme: str):
        """year is now a predefined engine format, not a theme alias.
        Themes no longer need to carry it in style.formats."""
        theme_formats = get_theme_style(theme).formats
        assert theme_formats is None or "year" not in theme_formats


class TestPercentagePointsDeltaToken:
    """The difference of two percentages is expressed in points, not the `%`
    that d3's `%` type would multiply by 100 (which is right for a ratio
    delta but wrong for a percentage-point delta)."""

    def test_positive_value(self):
        assert format_value(3.2, "percentage_points_delta") == "+3.2 pts"

    def test_negative_value(self):
        # PREDEFINED_NATIVE bypasses d3_format, so its own lambda is
        # responsible for the house minus glyph (U+2212), same as every
        # d3-formatted negative.
        assert format_value(-1.5, "percentage_points_delta") == "−1.5 pts"

    def test_null_value(self):
        from dbt_charts.core.text.format_d3 import NULL_DISPLAY

        assert format_value(None, "percentage_points_delta") == NULL_DISPLAY

    def test_kpi_parts_splits_unit_into_suffix_lane(self):
        """The ' pts' unit must land in the suffix lane, not ride inside the
        number lane — table symbol_mode: anchors anchors the suffix lane to
        the first row; a unit stuck in the number lane repeats on every row
        and the digits don't align down the column."""
        prefix, number, suffix = format_kpi_parts(-3.2, "percentage_points_delta")
        assert prefix == ""
        assert number == "−3.2"
        assert suffix == " pts"

    def test_negative_value_with_authored_prefix_keeps_the_sign_in_the_number_lane(
        self,
    ):
        """format_kpi_parts itself leaves the sign attached to the number lane, even
        when a prefix is present.
        """
        config = FormatConfig(spec=",.0f", prefix="EUR ")
        prefix, number, suffix = format_kpi_parts(-500, config)
        assert prefix == "EUR"
        assert number == "−500"
        assert suffix == ""


class TestFormatValueNativePresetAffix:
    """The measured text of a native-preset cell is the painted text."""

    @pytest.mark.parametrize(
        ("config", "value"),
        [
            (FormatConfig(spec="percent_number", suffix=" YoY"), 12.3),
            (FormatConfig(spec="percent_number_delta", prefix="~"), -12.3),
            (
                FormatConfig(
                    spec="percentage_points_delta", prefix="~", suffix=" vs LY"
                ),
                1.5,
            ),
        ],
    )
    def test_matches_the_painted_lanes(self, config, value):
        prefix, number, suffix = format_kpi_parts(
            value, config, None, default_number=True
        )
        assert format_value(value, config) == prefix + number + suffix


class TestFormatKpiPartsNative:
    """format_kpi_parts(native=True) keeps raw d3 SI suffix chars instead of
    substituting them with the house analytic/narrative vocabulary."""

    def test_native_true_keeps_raw_d3_suffix(self):
        """native=True: 1_200_000 with ~s format → suffix "M" (raw d3), not "mn"."""
        prefix, num, suffix = format_kpi_parts(
            1_200_000,
            FormatConfig(spec="~s", notation="narrative"),
            formats={},
            native=True,
        )
        assert suffix == "M", f"expected raw d3 'M', got {suffix!r}"
        assert num == "1.2"
        assert prefix == ""

    def test_native_false_uses_narrative_notation(self):
        """native=False (default): FormatConfig with notation='narrative' → suffix 'mn'."""
        _, _, suffix = format_kpi_parts(
            1_200_000,
            FormatConfig(spec="~s", notation="narrative"),
            formats={},
            native=False,
        )
        assert suffix == "mn"

    def test_native_true_thousands(self):
        """native=True: 5_000 with ~s format → suffix "k" (raw d3 lowercase)."""
        _, num, suffix = format_kpi_parts(
            5_000,
            FormatConfig(spec="~s", notation="narrative"),
            formats={},
            native=True,
        )
        assert suffix == "k"
        assert num == "5"

    def test_native_true_billions_keeps_g_suffix(self):
        """native=True: 3.2e9 with ~s → suffix "G" (raw d3 giga, not house "B").

        MAGNITUDE_SUFFIXES must include raw d3 keys (k/M/G/T/…) in addition to
        house notation values (K/M/B/T/…). Without "G", table symbol_mode:anchors
        strips the suffix from non-first rows, making 3.2B render as "3.2".
        """
        _, _num, suffix = format_kpi_parts(
            3_200_000_000,
            "~s",
            formats={},
            native=True,
        )
        from dbt_charts.core.render.format_utils import MAGNITUDE_SUFFIXES

        assert suffix == "G", f"expected raw d3 'G', got {suffix!r}"
        assert "G" in MAGNITUDE_SUFFIXES, "MAGNITUDE_SUFFIXES must include raw d3 'G'"


class TestJinjaFormatFilter:
    """The {{ value | format(...) }} Jinja filter uses format_d3 with no trim injection.

    Before this branch, format_d3 silently injected the round-aware '~' trim flag
    and defaulted notation to 'analytic'. Now the filter is native d3 only: the
    spec passes through unchanged and no notation is applied. Authors who want trim
    or notation use a predefined name on the chart-level format: field, not the
    Jinja filter.
    """

    def _render(self, template: str, value: float) -> str:
        from dbt_charts.core.compile.template.labels_env import label_jinja_env

        env = label_jinja_env()
        return env.from_string(template).render(value=value)

    def test_si_spec_no_trim_injected(self) -> None:
        """Inline SI spec keeps trailing zeros — no '~' trim is baked in."""
        result = self._render("{{ value | format('.3s') }}", 1_000_000.0)
        assert result == "1.00M", f"expected '1.00M', got {result!r}"

    def test_si_spec_with_explicit_trim_trims(self) -> None:
        """Author-supplied '~' in the spec is honored as native d3."""
        result = self._render("{{ value | format('.3~s') }}", 1_000_000.0)
        assert result == "1M", f"expected '1M', got {result!r}"

    def test_no_analytic_notation_applied(self) -> None:
        """No house notation — '1.5G' not rewritten to '1.5 B'."""
        result = self._render("{{ value | format('.2s') }}", 1_500_000_000.0)
        assert result == "1.5G", f"expected raw d3 '1.5G', got {result!r}"


class TestCompactNamesAreActuallyCompact:
    """`number`/`currency` must not be less compact than the default.

    d3's `s` type defaults to 6 significant digits and `~` only trims trailing
    zeros, so a bare `~s` renders 50752.9 as `50.7529 K` — six digits under a
    name that promises brevity, and wider than the engine's own
    `number` (`.3~s`). The engine owns what its predefined names mean;
    a literal authored `$~s` is native d3 and stays six digits.
    """

    @pytest.mark.parametrize(
        ("name", "value", "expected"),
        [
            ("number", 50_752.9, "50.8 K"),
            ("currency", 50_752.9, "$50.8 K"),
            ("number", 1_234_567.89, "1.23 M"),
            ("currency", 1_234_567.89, "$1.23 M"),
        ],
    )
    def test_compact_name_trims_to_three_significant_digits(
        self, name: str, value: float, expected: str
    ) -> None:
        assert format_value(value, name) == expected, (
            f"{name} rendered six significant digits; the name promises a short "
            f"number and must match number's precision"
        )

    @pytest.mark.parametrize(
        ("name", "value", "expected"),
        [
            ("number", 999, "999"),
            ("currency", 999, "$999"),
            ("number", 1_500, "1.5 K"),
            ("currency", 2_000_000, "$2 M"),
        ],
    )
    def test_already_short_values_are_unchanged(
        self, name: str, value: float, expected: str
    ) -> None:
        """Three digits, not two: `.2~s` would round 999 up to a false `1 K`."""
        assert format_value(value, name) == expected

    @pytest.mark.parametrize("spec", ["~s", "$~s"])
    def test_literal_d3_spec_keeps_full_d3_precision(self, spec: str) -> None:
        """An authored d3 spec is native d3 — rewriting it would be silent magic."""
        assert "50.7529" in format_d3(50_752.9, spec)


class TestSubCentMoneyKeepsSi:
    """Below half a cent the two-decimal fallback cannot represent the value.

    `$,.2f` renders anything under 0.005 as `$0.00`, so applying the floor
    there would paint an entire CPC, per-token-pricing or FX column as zero on
    every row — visually identical to the genuine zeros this same band is
    careful to leave as `$0`. `$670m` is a misread a reader can catch; `$0.00`
    is not. The floor is therefore 0.005, not 0.0: it confines the swap to the
    range the fallback can actually express, and sub-cent money keeps the SI
    rendering it had before this rule existed.
    """

    def test_sub_cent_money_keeps_its_si_rendering(self):
        assert format_value(0.0023, "currency") == "$2.3m"
        assert format_value(0.0001, "currency") == "$100µ"

    def test_the_floor_engages_where_the_fallback_can_represent_the_value(self):
        assert format_value(0.0051, "currency") == "$0.01"
        assert format_value(0.67, "currency") == "$0.67"

    def test_no_money_value_in_the_band_renders_as_a_bare_zero_string(self):
        """The property that matters, stated directly: only a real zero paints zero."""
        for cents in range(1, 200):
            value = cents / 10000.0
            painted = format_value(value, "currency")
            assert painted not in ("$0.00", "$0"), (
                f"{value} painted {painted!r} — indistinguishable from zero"
            )

    def test_kpi_parts_takes_the_same_floor(self):
        # Milli stays glued in the number lane — it is not in the house suffix
        # vocabulary, which is the whole reason this rule exists.
        assert format_kpi_parts(0.0023, "currency", None) == ("$", "2.3m", "")


class TestSiSubUnitFloor:
    """Money below $1 has no sub-cent unit to name, so it must not take an SI
    spec — d3's `m` (milli) glues onto the number as e.g. `$670m` for 67
    cents, colliding case-only with the house million grammar. A plain
    quantity's fraction reads correctly as milli (`0.671` -> `671m`) and must
    keep its SI spec unchanged. Money at or above $1 is unchanged too — the
    `$13` for `$12.99` rounding case is a separate, tracked issue.
    """

    def test_money_below_one_falls_back_to_currency_full(self) -> None:
        assert format_value(0.67, "currency") == "$0.67"

    def test_money_pound_prefix_below_one_falls_back(self) -> None:
        # A "£" board has no "$" in its resolved spec — the affix alone must
        # be enough to detect money.
        config = FormatConfig(spec="number", prefix="£")
        assert format_value(0.67, config) == "£0.67"

    def test_money_usd_suffix_below_one_falls_back(self) -> None:
        config = FormatConfig(spec="number", suffix=" USD")
        assert format_value(0.67, config) == "0.67 USD"

    def test_plain_quantity_fraction_keeps_milli_reading(self) -> None:
        assert format_value(0.671, "number") == "671m"

    def test_money_at_or_above_one_is_unchanged(self) -> None:
        assert format_value(1.0, "currency") == "$1"
        assert format_value(12.99, "currency") == "$13"

    def test_format_kpi_parts_money_below_one_falls_back(self) -> None:
        assert format_kpi_parts(0.67, "currency") == ("$", "0.67", "")

    def test_format_kpi_parts_plain_quantity_keeps_milli(self) -> None:
        # "m" (milli) is not a house magnitude suffix (_D3_TO_ANALYTIC has no
        # entry for it), so it stays glued to the number, unaffected by the
        # money-only floor.
        assert format_kpi_parts(0.671, "number") == ("", "671m", "")

    def test_negative_money_below_one_falls_back(self) -> None:
        assert format_value(-0.67, "currency") == "−$0.67"

    def test_kg_suffix_keeps_si_reading(self) -> None:
        # A non-blank suffix alone must NOT be enough to call this money --
        # "kg" is a unit, not a currency. Two grams must render as an SI
        # milli reading, not get zeroed out by the money-only fallback.
        config = FormatConfig(spec="number", suffix=" kg")
        assert format_value(0.002, config) == "2m kg"

    def test_ms_suffix_keeps_si_reading(self) -> None:
        config = FormatConfig(spec="number", suffix=" ms")
        assert format_value(0.002, config) == "2m ms"

    def test_unrecognized_suffix_keeps_si_reading(self) -> None:
        config = FormatConfig(spec="number", suffix=" rpm")
        assert format_value(0.002, config) == "2m rpm"

    def test_euro_prefix_below_one_falls_back(self) -> None:
        config = FormatConfig(spec="number", prefix="€")
        assert format_value(0.67, config) == "€0.67"

    def test_zero_currency_is_not_floored_to_two_decimals(self) -> None:
        # Zero is exactly representable, not "below the minor unit" -- it
        # must not take the sub-unit fallback. currency(0) and number(0)
        # must agree in shape (no decimals), same as every other value.
        assert format_value(0, "currency") == "$0"
        assert format_value(0, "number") == "0"

    def test_zero_currency_kpi_parts_is_not_floored(self) -> None:
        assert format_kpi_parts(0, "currency") == ("$", "0", "")

    def test_default_number_table_cell_money_prefix_below_one_falls_back(
        self,
    ) -> None:
        # format: {prefix: "£"} with no spec goes through the default_number
        # branch, which installs the "number" predefined spec. That branch
        # must mark _raw as the house name too, or the sub-unit-floor guard
        # never sees it and a table cell like this renders "£670m" for 67p.
        assert format_kpi_parts(0.67, {"prefix": "£"}, default_number=True) == (
            "£",
            "0.67",
            "",
        )

    def test_kpi_headline_pipeline_money_prefix_below_one_falls_back(self) -> None:
        # A KPI headline never reaches format_kpi_parts with the author's raw {prefix:
        # "£"}.
        finalized = finalize_kpi_value_format(FormatConfig(prefix="£"), 0.67)
        assert format_kpi_parts(0.67, finalized) == ("£", "0.67", "")


class TestSubUnitFloorVote:
    """resolve_format_parts_for_values: the Vega-painted analog of TestSiSubUnitFloor.

    Vega paints per-datum inside its own runtime, so the per-value floor
    format_value/format_kpi_parts apply cannot run there -- this decides the
    register once, at resolve, from the values a slot will paint. Per-set
    semantics: any value in the sub-$1 band pulls the whole set to the
    plain-digit fallback, even members >= $1.
    """

    def test_all_values_below_one_falls_back_to_currency_full(self) -> None:
        assert (
            resolve_format_parts_for_values(
                "currency", None, [0.42, 0.25, 0.67], no_format_default=None
            ).spec
            == PREDEFINED_SPECS["currency_full"]
        )

    def test_one_value_below_one_pulls_the_whole_set(self) -> None:
        # Mixed set: any member in the band pulls the register.
        assert (
            resolve_format_parts_for_values(
                "currency", None, [0.42, 3.0], no_format_default=None
            ).spec
            == PREDEFINED_SPECS["currency_full"]
        )

    def test_negative_money_below_one_falls_back(self) -> None:
        # The band test is on abs(v) -- a negative sub-$1 value must floor
        # the same as its positive counterpart.
        assert (
            resolve_format_parts_for_values(
                "currency", None, [-0.42], no_format_default=None
            ).spec
            == PREDEFINED_SPECS["currency_full"]
        )

    def test_all_values_at_or_above_one_keeps_si_spec(self) -> None:
        assert (
            resolve_format_parts_for_values(
                "currency", None, [12.99, 100.0], no_format_default=None
            ).spec
            == PREDEFINED_SPECS["currency"]
        )

    def test_all_zero_keeps_si_spec(self) -> None:
        assert (
            resolve_format_parts_for_values(
                "currency", None, [0.0, 0.0], no_format_default=None
            ).spec
            == (PREDEFINED_SPECS["currency"])
        )

    def test_sub_cent_money_keeps_si_spec(self) -> None:
        # Below MONEY_SUB_UNIT_FLOOR the fallback can't represent the value
        # either ($,.2f would print $0.00) -- stays on SI, same as the
        # per-value predicate.
        assert (
            resolve_format_parts_for_values(
                "currency", None, [0.0023], no_format_default=None
            ).spec
            == (PREDEFINED_SPECS["currency"])
        )

    def test_plain_quantity_fraction_keeps_si_spec(self) -> None:
        # "number" below 1 reads correctly as an SI milli value -- only
        # money is wrong below the floor.
        assert (
            resolve_format_parts_for_values(
                "number", None, [0.671], no_format_default=None
            ).spec
            == (PREDEFINED_SPECS["number"])
        )

    def test_money_prefix_below_one_falls_back(self) -> None:
        config = FormatConfig(spec="number", prefix="£")
        assert (
            resolve_format_parts_for_values(
                config, None, [0.67], no_format_default=None
            ).spec
            == PREDEFINED_SPECS["number_full"]
        )

    def test_inline_d3_spec_never_takes_house_floor(self) -> None:
        # Native d3 (not a predefined member) is a native-d3 opt-out --
        # never touched by the house sub-unit-floor vote.
        assert (
            resolve_format_parts_for_values(
                "$.3~s", None, [0.67], no_format_default=None
            ).spec
            == "$.3~s"
        )

    def test_style_formats_alias_never_takes_house_floor(self) -> None:
        assert (
            resolve_format_parts_for_values(
                "money", _ALIAS_FORMATS, [0.67], no_format_default=None
            ).spec
            == "$,.2f"
        )

    def test_none_values_are_skipped(self) -> None:
        assert (
            resolve_format_parts_for_values(
                "currency", None, [None, None], no_format_default=None
            ).spec
            == PREDEFINED_SPECS["currency"]
        )

    def test_parity_with_format_value_on_a_band_crossing_set(self) -> None:
        values = [0.42, 0.25, 0.67]
        result_spec = resolve_format_parts_for_values(
            "currency", None, values, no_format_default=None
        ).spec
        for value in values:
            assert format_d3(value, result_spec) == format_value(value, "currency")


class TestSubUnitFloorVoteSpecLessAffix:
    """resolve_format_parts_for_values's ``no_format_default`` keyword."""

    def test_spec_less_affix_dict_below_one_falls_back_to_number_full(self) -> None:
        assert (
            resolve_format_parts_for_values(
                {"prefix": "£"},
                None,
                [0.67],
                no_format_default=PredefinedNumberFormat.number,
            ).spec
            == PREDEFINED_SPECS["number_full"]
        )

    def test_spec_less_affix_format_config_below_one_falls_back(self) -> None:
        assert (
            resolve_format_parts_for_values(
                FormatConfig(prefix="£"),
                None,
                [0.67],
                no_format_default=PredefinedNumberFormat.number,
            ).spec
            == PREDEFINED_SPECS["number_full"]
        )

    def test_spec_less_affix_alias_below_one_falls_back(self) -> None:
        # An alias name is a plain string with no affix of its own until
        # resolution follows it to its target -- same shape, same fix.
        formats = {"gbp": FormatConfig(prefix="£")}
        assert (
            resolve_format_parts_for_values(
                "gbp",
                formats,
                [0.67],
                no_format_default=PredefinedNumberFormat.number,
            ).spec
            == PREDEFINED_SPECS["number_full"]
        )

    def test_spec_less_affix_currency_default_below_one_falls_back(self) -> None:
        assert (
            resolve_format_parts_for_values(
                {"prefix": "£"},
                None,
                [0.67],
                no_format_default=PredefinedNumberFormat.currency,
            ).spec
            == PREDEFINED_SPECS["currency_full"]
        )

    def test_spec_less_affix_above_one_keeps_si_spec(self) -> None:
        assert (
            resolve_format_parts_for_values(
                {"prefix": "£"},
                None,
                [12400.0],
                no_format_default=PredefinedNumberFormat.number,
            ).spec
            == PREDEFINED_SPECS["number"]
        )

    def test_no_affix_no_default_is_unaffected(self) -> None:
        # A caller that omits no_format_default gets a spec-less, affix-less input
        # resolved to no spec at all; it never votes (nothing to vote with a "" raw).
        assert (
            resolve_format_parts_for_values(
                None, None, [0.67], no_format_default=None
            ).spec
            == ""
        )

    def test_already_authored_spec_is_never_overridden_by_the_default(self) -> None:
        # no_format_default only ever substitutes when resolution produces an affix but
        # no spec -- an explicit spec (with or without affix) is untouched.
        assert (
            resolve_format_parts_for_values(
                FormatConfig(spec="currency", prefix="£"),
                None,
                [0.67],
                no_format_default=PredefinedNumberFormat.number,
            ).spec
            == PREDEFINED_SPECS["currency_full"]
        )


class TestResolveFormatPartsForValues:
    """The one vote every Vega-painted slot resolves through."""

    def test_voted_spec_carries_the_authored_affix_and_notation(self) -> None:
        parts = resolve_format_parts_for_values(
            FormatConfig(prefix="£", suffix=" GBP", notation="analytic"),
            None,
            [0.5, 1234.5, 2456789.0],
            no_format_default=PredefinedNumberFormat.number,
        )
        assert parts.spec == PREDEFINED_SPECS["number_full"]
        assert (parts.prefix, parts.suffix, parts.notation) == (
            "£",
            " GBP",
            "analytic",
        )

    def test_writing_no_spec_votes_exactly_like_writing_the_default(self) -> None:
        values = [2e6, 4.5e6, 7e6]
        spec_less = resolve_format_parts_for_values(
            {"prefix": "GBP "},
            None,
            values,
            no_format_default=PredefinedNumberFormat.number,
        )
        explicit = resolve_format_parts_for_values(
            {"spec": "number", "prefix": "GBP "},
            None,
            values,
            no_format_default=PredefinedNumberFormat.number,
        )
        assert spec_less == explicit

    def test_no_format_keeps_no_spec(self) -> None:
        parts = resolve_format_parts_for_values(
            None, None, [0.67], no_format_default=PredefinedNumberFormat.number
        )
        assert parts.spec == "" and not parts.has_affix


class TestResolveLabelFormatValuesAware:
    """resolve_label_format's ``values`` parameter."""

    def test_spec_less_affix_label_below_one_falls_back_to_plain_digits(self) -> None:
        resolved, is_house = resolve_label_format(
            FormatConfig(prefix="£"), None, values=[0.67]
        )
        assert (resolved.spec, resolved.prefix) == (
            PREDEFINED_SPECS["number_full"],
            "£",
        )
        assert is_house is True

    def test_spec_less_affix_label_above_one_keeps_si_spec(self) -> None:
        resolved, is_house = resolve_label_format(
            FormatConfig(prefix="£"), None, values=[12400.0]
        )
        assert (resolved.spec, resolved.prefix) == (PREDEFINED_SPECS["number"], "£")
        assert is_house is True

    def test_bare_predefined_name_label_below_one_falls_back(self) -> None:
        # Not just the spec-less-affix shape -- an explicit "currency"/"number" label
        # format is itself a Vega-painted bake with no per-value floor.
        resolved, is_house = resolve_label_format("currency", None, values=[0.42])
        assert resolved.spec == PREDEFINED_SPECS["currency_full"]
        assert is_house is True

    def test_no_values_passed_keeps_si_spec(self) -> None:
        # Omitting values (the default) never votes.
        resolved, is_house = resolve_label_format("currency", None)
        assert resolved.spec == PREDEFINED_SPECS["currency"]
        assert is_house is True


class TestAliasToPreset:
    """An alias naming a preset resolves to that preset, house rules included."""

    @pytest.mark.parametrize(
        ("value", "formats", "prefix"),
        [
            pytest.param("mine", {"mine": "number"}, "", id="bare-alias"),
            pytest.param(
                "eur",
                {"eur": FormatConfig(spec="number", prefix="EUR ")},
                "EUR ",
                id="alias-with-affix",
            ),
            pytest.param(
                FormatConfig(spec="mine", suffix=" units"),
                {"mine": "number"},
                "",
                id="alias-in-spec",
            ),
        ],
    )
    def test_resolves_as_the_preset(self, value, formats, prefix) -> None:
        parts = resolve_format_parts(value, formats, no_format_default=None)
        assert parts.spec == resolve_format("number")
        assert parts.raw == "number"
        assert parts.is_house
        assert parts.prefix == prefix

    def test_sub_unit_vote_applies_through_the_alias(self) -> None:
        direct = resolve_format_parts_for_values(
            "number", None, [0.67], no_format_default=None
        )
        aliased = resolve_format_parts_for_values(
            "mine", {"mine": "number"}, [0.67], no_format_default=None
        )
        assert aliased.spec == direct.spec


class TestSignPlacement:
    """``sign_placement`` resolves once; unset follows the authored prefix."""

    @pytest.mark.parametrize(
        ("config", "formats", "expected"),
        [
            pytest.param(
                FormatConfig(prefix="EUR "), None, "after_prefix", id="spaced"
            ),
            pytest.param(FormatConfig(prefix="€"), None, "before_prefix", id="glued"),
            pytest.param(
                FormatConfig(spec="currency"), None, "before_prefix", id="preset"
            ),
            pytest.param(
                FormatConfig(prefix="EUR ", sign_placement="before_prefix"),
                None,
                "before_prefix",
                id="explicit-before",
            ),
            pytest.param(
                FormatConfig(prefix="€", sign_placement="after_prefix"),
                None,
                "after_prefix",
                id="explicit-after",
            ),
            pytest.param(
                "eur", {"eur": FormatConfig(prefix="EUR ")}, "after_prefix", id="alias"
            ),
            pytest.param(
                FormatConfig(spec="eur"),
                {"eur": FormatConfig(prefix="EUR ", sign_placement="before_prefix")},
                "before_prefix",
                id="alias-option",
            ),
        ],
    )
    def test_resolves(self, config, formats, expected) -> None:
        parts = resolve_format_parts(config, formats, no_format_default="number")
        assert parts.placement == expected

    @pytest.mark.parametrize(
        ("config", "formats", "authored"),
        [
            (FormatConfig(prefix="EUR "), None, None),
            (
                FormatConfig(prefix="€", sign_placement="before_prefix"),
                None,
                "before_prefix",
            ),
            (
                FormatConfig(spec="eur"),
                {"eur": FormatConfig(prefix="€", sign_placement="after_prefix")},
                "after_prefix",
            ),
        ],
    )
    def test_stores_only_the_authored_value(self, config, formats, authored) -> None:
        parts = resolve_format_parts(config, formats, no_format_default="number")
        assert parts.sign_placement == authored

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            (FormatConfig(spec=",.0f", prefix="EUR "), "EUR −500"),
            (FormatConfig(spec=",.0f", prefix="€"), "−€500"),
            (FormatConfig(spec="$,.0f", prefix="US "), "US −$500"),
            (
                FormatConfig(
                    spec=",.0f", prefix="EUR ", sign_placement="before_prefix"
                ),
                "−EUR 500",
            ),
            (
                FormatConfig(spec=",.0f", prefix="€", sign_placement="after_prefix"),
                "€−500",
            ),
        ],
    )
    def test_format_value_places_the_sign(self, config, expected) -> None:
        assert format_value(-500, config) == expected

    def test_kpi_lanes_keep_the_sign_after_a_spaced_prefix(self) -> None:
        config = FormatConfig(spec=",.0f", prefix="EUR ")
        assert format_kpi_parts(-500, config, sign_in_prefix=True) == (
            "EUR",
            "−500",
            "",
        )

    def test_kpi_lanes_lead_with_the_sign_before_a_glued_prefix(self) -> None:
        config = FormatConfig(spec=",.0f", prefix="€")
        assert format_kpi_parts(-500, config, sign_in_prefix=True) == ("−€", "500", "")

    def test_kpi_lanes_put_the_sign_between_authored_prefix_and_native_symbol(
        self,
    ) -> None:
        config = FormatConfig(spec="$,.0f", prefix="US ")
        assert format_kpi_parts(-500, config, sign_in_prefix=True) == (
            "US",
            "−$500",
            "",
        )

    def test_table_lanes_keep_the_sign_in_the_number_lane(self) -> None:
        config = FormatConfig(spec=",.0f", prefix="€")
        assert format_kpi_parts(-500, config) == ("€", "−500", "")


class TestRepeatResolves:
    def test_unset_is_none(self) -> None:
        parts = resolve_format_parts(
            FormatConfig(prefix="€"), None, no_format_default="number"
        )
        assert parts.repeat is None

    def test_follows_the_alias(self) -> None:
        formats = {"eur": FormatConfig(prefix="€", repeat="every")}
        parts = resolve_format_parts("eur", formats, no_format_default="number")
        assert parts.repeat == "every"

    def test_own_wins_over_the_alias(self) -> None:
        formats = {"eur": FormatConfig(prefix="€", repeat="every")}
        parts = resolve_format_parts(
            FormatConfig(spec="eur", repeat="anchor"),
            formats,
            no_format_default="number",
        )
        assert parts.repeat == "anchor"


class TestResolvedFormatWire:
    def test_round_trips_sign_placement_and_repeat(self) -> None:
        parts = resolve_format_parts(
            FormatConfig(spec=",.0f", prefix="EUR ", repeat="anchor"),
            None,
            no_format_default=None,
        )
        assert ResolvedFormat.model_validate(parts.model_dump(mode="json")) == parts

    def test_an_unset_sign_placement_takes_the_prefix_default(self) -> None:
        assert ResolvedFormat(spec=",.0f", prefix="EUR ").placement == "after_prefix"
        assert ResolvedFormat(spec=",.0f", prefix="€").placement == "before_prefix"
