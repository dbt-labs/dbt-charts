"""Tests for Levenshtein hint generators.

Exercised through `suggest_close_source` — one of the two generators actually
wired to a code's `hint_generator` (ERR-SOURCE-NOT-FOUND in codes_compile).
The three cases below cover the shared `_suggest_close_match` core that
`suggest_close_theme` also runs on. That the wiring survives a real raise is
pinned separately in tests/core/execute/test_source_resolver_diagnostics.py.

`available` is a `Sequence[str]` contract, not a comma-separated string — a
raise site writes `available=sorted(...)`, the expression it naturally
reaches for. `str` is itself a `Sequence[str]` of characters, so passing one
must raise `TypeError` rather than silently scoring single letters.
"""

from __future__ import annotations

import pytest


class TestHints:
    def test_suggest_close_source_hit(self) -> None:
        from dbt_charts.core.diagnostics.hints import suggest_close_source

        hint = suggest_close_source(
            source="postgre", available=["postgres", "duckdb", "bigquery", "snowflake"]
        )
        assert hint is not None
        assert "postgres" in hint

    def test_suggest_close_source_no_hit(self) -> None:
        from dbt_charts.core.diagnostics.hints import suggest_close_source

        hint = suggest_close_source(source="zzzzzzz", available=["postgres", "duckdb"])
        assert hint is None

    def test_suggest_close_source_empty_available(self) -> None:
        from dbt_charts.core.diagnostics.hints import suggest_close_source

        hint = suggest_close_source(source="postgres", available=[])
        assert hint is None

    def test_suggest_close_match_rejects_bare_str(self) -> None:
        """`str` is a `Sequence[str]` of characters — must reject, not silently
        score character-by-character or coerce."""
        from dbt_charts.core.diagnostics.hints import _suggest_close_match

        with pytest.raises(TypeError):
            _suggest_close_match("postgre", "postgres, duckdb")


class TestRespellRetiredVariantToken:
    """The one whole-token transformation behind both the migration's
    value map (`versions/v0_9_0.py`) and palette.py's live "was retired"
    hint -- so the two can never name different replacements."""

    @pytest.mark.parametrize(
        ("retired", "replacement"),
        [
            ("category_dark[2]", "category[2].dark"),
            ("category_ghost[1]", "category[1].pale"),
            ("category_ink.gray", "category.gray.deep"),
            ("category_light.blue", "category.blue.light"),
            ("category_ink", "category.deep"),
            ("vivid-10-dark.3", "vivid-10.3.dark"),
            ("editorial-10-ghost.5", "editorial-10.5.pale"),
            ("editorial-10-ink", "editorial-10.deep"),
            # `:N`/`_r` shorthand rides along, after the family, before the variant.
            ("vivid-10-dark:4", "vivid-10:4.dark"),
            ("editorial-10-ghost:3_r", "editorial-10:3_r.pale"),
        ],
    )
    def test_every_retired_shape_respells_as_a_whole_token(
        self, retired: str, replacement: str
    ) -> None:
        from dbt_charts.core.diagnostics.hints import respell_retired_variant_token

        assert respell_retired_variant_token(retired) == replacement

    @pytest.mark.parametrize(
        "token",
        [
            # Current grammar, never retired.
            "category[2]",
            "category.blue",
            "category[2].dark",
            "vivid-10.3",
            "vivid-10.3.dark",
            # A continuous palette's own shipped `-dark` fork is a live name
            # (the closed-set invariant: only the two companion families
            # and the one role prefix were retired).
            "dbt-seq-blue-dark",
            "dbt-seq-blue-dark.3",
            # A retired role with a second dotted segment after the alias is
            # not a spelling the old grammar ever accepted; respelling it
            # would name a token that cannot resolve (`category.blue.dark.dark`).
            "category_dark.blue.dark",
            # A family slot must be an integer for the replacement to resolve.
            "vivid-10-dark.blue",
        ],
    )
    def test_anything_outside_the_closed_retired_set_is_left_alone(
        self, token: str
    ) -> None:
        from dbt_charts.core.diagnostics.hints import respell_retired_variant_token

        assert respell_retired_variant_token(token) is None


class TestSuggestClosePalette:
    """A retired companion spelling gets its successor, never a fuzzy match:
    the nearest shipped name to `vivid-10-dark` is `vivid-10`, which
    compiles clean and silently drops the darkening the author wrote it
    for -- `RETIRED_FORMAT_SUCCESSORS`' own reasoning, applied here."""

    def test_a_retired_spelling_names_its_replacement(self) -> None:
        from dbt_charts.core.diagnostics.hints import suggest_close_palette

        hint = suggest_close_palette("vivid-10-dark", available=["vivid-10", "tableau"])
        assert hint == "'vivid-10-dark' was retired; use 'vivid-10.dark' instead."

    def test_a_current_typo_still_gets_a_fuzzy_match(self) -> None:
        from dbt_charts.core.diagnostics.hints import suggest_close_palette

        hint = suggest_close_palette("vivd-10", available=["vivid-10", "tableau"])
        assert hint == "Did you mean 'vivid-10'?"
