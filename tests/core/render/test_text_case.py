"""Tests for apply_case — the case-transform dispatcher.

Covers the documented edge cases from the task worksheet (Gruber algorithm):
acronym preservation, stopword lowercasing, first/last-word capitalization,
hyphenated compounds, apostrophes, post-colon capitalization, and sentence
case trade-offs.
"""

import pytest

from dbt_charts.core.text.case import apply_case

# ---------------------------------------------------------------------------
# none (identity — no letter-case change)
# ---------------------------------------------------------------------------


def test_none_passes_string_unchanged() -> None:
    assert apply_case("Revenue by Segment", "none") == "Revenue by Segment"


def test_none_empty_string() -> None:
    assert apply_case("", "none") == ""


# ---------------------------------------------------------------------------
# upper / lower — trivial
# ---------------------------------------------------------------------------


def test_upper_basic() -> None:
    assert apply_case("region", "upper") == "REGION"


def test_lower_basic() -> None:
    assert apply_case("NORTH AMERICA", "lower") == "north america"


def test_upper_unicode() -> None:
    # Python .upper() is Unicode-aware
    assert apply_case("ñoño", "upper") == "ÑOÑO"


# ---------------------------------------------------------------------------
# sentence — first char upper, rest unchanged (preserves internal caps)
# ---------------------------------------------------------------------------


def test_sentence_basic() -> None:
    assert apply_case("arr by segment", "sentence") == "Arr by segment"


def test_sentence_preserves_internal_caps() -> None:
    # Cautious sentence case: does NOT lowercase the rest of the string —
    # preserves acronyms/proper nouns that the author already capitalized.
    assert apply_case("ARR by Segment", "sentence") == "ARR by Segment"


def test_sentence_already_correct() -> None:
    assert apply_case("Monthly revenue", "sentence") == "Monthly revenue"


def test_sentence_empty_string() -> None:
    assert apply_case("", "sentence") == ""


def test_sentence_single_char() -> None:
    assert apply_case("x", "sentence") == "X"


# ---------------------------------------------------------------------------
# title — Chicago/Gruber algorithm via the engine (display-text case.rs)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Stopwords remain lowercase (not first or last)
        ("revenue by segment", "Revenue by Segment"),
        ("revenue and growth by segment", "Revenue and Growth by Segment"),
        ("from north to south", "From North to South"),
        # First and last words always capitalized regardless of stopword status
        ("from the top", "From the Top"),
        ("a note on the subject", "A Note on the Subject"),
        # Acronym / internal-caps preservation — the key differentiator
        ("ARR growth by region", "ARR Growth by Region"),
        ("iPhone sales in q1", "iPhone Sales in Q1"),
        ("SQL queries and MRR trends", "SQL Queries and MRR Trends"),
        # Hyphenated compounds: each component is capitalized
        ("self-service analytics", "Self-Service Analytics"),
        # Apostrophes: don't corrupt contractions
        ("don't stop me now", "Don't Stop Me Now"),
        # Post-colon: the word after a colon is capitalized
        ("q1: revenue rose", "Q1: Revenue Rose"),
        # Basic all-lower-case title
        (
            "monthly recurring revenue by segment",
            "Monthly Recurring Revenue by Segment",
        ),
        # Standalone all-caps acronym preserved. The engine
        # lowercases whole strings detected as all-caps before per-word
        # logic runs, so the apply_case wrapper has to short-circuit
        # these cases. Single-token acronyms from ``slug_to_text``'s
        # _ABBREVIATIONS lookup (arr → ARR, mrr → MRR, sql → SQL) hit
        # this path.
        ("ARR", "ARR"),
        ("MRR", "MRR"),
        ("SQL", "SQL"),
        # Small-words list extended with editorial-style lowercase
        # words missing from the library's NYT default — ``per``,
        # ``with``, ``from``, ``into`` etc. otherwise pick up the
        # default "capitalize every word" treatment.
        ("revenue per year", "Revenue per Year"),
        ("growth with discounts", "Growth with Discounts"),
        ("from source to sink", "From Source to Sink"),
        ("queries into the warehouse", "Queries into the Warehouse"),
        ("onto the dashboard", "Onto the Dashboard"),
        # Edge: first word is always capitalized even when it matches an
        # extended small-word — same library contract as ``a``/``the``.
        ("per launch", "Per Launch"),
        ("with caveats", "With Caveats"),
        # Edge: last word is always capitalized too — pins the
        # ``range(1, len(words) - 1)`` upper bound in the post-process.
        ("scaling up", "Scaling Up"),
        ("burnout from launch", "Burnout from Launch"),
        # Author intent: a mid-position all-caps token in the extended
        # small-words set (someone shouting ``UP`` or using it as an
        # acronym) must survive the post-process. The callback preserves
        # it; the post-process explicitly skips all-caps tokens.
        ("scale UP now", "Scale UP Now"),
        # "dbt" is stylized lowercase always (dbt Labs' own convention),
        # never an acronym. Without an explicit guard, the engine's title-case
        # library's built-in all-consonant heuristic (the same rule that
        # promotes "mrr"/"sql") misreads "dbt" as an acronym-to-uppercase
        # and renders "DBT Charts ..." — visibly wrong for the product
        # name.
        ("dbt charts quick start", "dbt Charts Quick Start"),
        ("quick start guide to dbt charts", "Quick Start Guide to dbt Charts"),
    ],
)
def test_title_case_edge_cases(text: str, expected: str) -> None:
    assert apply_case(text, "title") == expected


def test_title_empty_string() -> None:
    assert apply_case("", "title") == ""


# ---------------------------------------------------------------------------
# slug — machine identifier form
# ---------------------------------------------------------------------------


def test_slug_replaces_spaces_with_underscore() -> None:
    assert apply_case("Order Status", "slug") == "order_status"


def test_slug_lowercase() -> None:
    assert apply_case("Revenue", "slug") == "revenue"


# ---------------------------------------------------------------------------
# camel — camelCase
# ---------------------------------------------------------------------------


def test_camel_basic() -> None:
    assert apply_case("order status", "camel") == "orderStatus"


def test_camel_single_word() -> None:
    assert apply_case("revenue", "camel") == "revenue"


def test_sentence_keeps_leading_dbt_lowercase() -> None:
    assert apply_case("dbt models grew 12%", "sentence") == "dbt models grew 12%"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "dbt-labs/dbt-charts issues since launch",
            "dbt-labs/dbt-charts Issues Since Launch",
        ),
        ("src/core/case.py rules", "src/core/case.py Rules"),
        ("x/y-axis chart", "X/Y-Axis Chart"),
        ("a/b-test results", "A/B-Test Results"),
        ("price/unit-cost analysis", "Price/Unit-Cost Analysis"),
        ("pie/donut.", "Pie/Donut."),
        ("pie/donut", "Pie/Donut"),
        ("dbt-labs/dbt-charts", "dbt-labs/dbt-charts"),
        ("pie/donut table", "Pie/Donut Table"),
        ("a/b testing results", "A/B Testing Results"),
        ("directory /sales now", "Directory /sales Now"),
    ],
)
def test_title_slash_words(text: str, expected: str) -> None:
    assert apply_case(text, "title") == expected
