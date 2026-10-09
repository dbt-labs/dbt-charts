"""Each pattern- or Literal-typed authored field fails with its own remediation text."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.board.authored import AuthoredBoard


def _board(**fields: object) -> dict[str, object]:
    return {"title": "t", **fields}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (
            {"queries": {"q": {"sql": "select 1", "ignore": ["WARN-NOPE"]}}},
            r"unknown warning code 'WARN-NOPE' in ignore",
        ),
        (
            {"queries": {"q": {"sql": "select 1", "ignore": ["ERR-NO-LAYOUT"]}}},
            r"error codes cannot be suppressed",
        ),
        (
            {"queries": {"q": {"schema": "s", "source": "w"}}},
            r"needs `type: schema`",
        ),
        (
            {"queries": {"q": {"ref": "nope"}}},
            r"Invalid query reference 'nope'.*'<file>\.queries\.<name>'",
        ),
        (
            {"aliases": ["old"]},
            r"Alias 'old' is not absolute.*/old-reports/",
        ),
        (
            {"incremental": 5},
            r"incremental: 5 is not valid.*watermark column",
        ),
        (
            {"incremental": " "},
            r"incremental: ' ' is not valid.*watermark column",
        ),
        (
            {
                "style": {
                    "charts": {
                        "category_colors": {"region": {"values": {"east": "junk!"}}}
                    }
                }
            },
            r"category_colors color 'junk!' is neither a palette token.*hex color",
        ),
    ],
)
def test_board_field_failure_carries_its_remediation(
    fields: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        AuthoredBoard.model_validate(_board(**fields))


def test_chart_warnings_ignore_names_the_query_scoped_home() -> None:
    chart = {
        "type": "bar",
        "query": "q",
        "x": "a",
        "y": "b",
        "warnings_ignore": ["WARN-REAGGREGATION"],
    }
    with pytest.raises(ValidationError, match=r"queries\.<name>\.ignore"):
        AuthoredBoard.model_validate(_board(charts={"c": chart}))


def test_glyph_names_the_rule() -> None:
    from dbt_charts.core.compile.models.style.authored import TableColumnConfig

    with pytest.raises(ValidationError, match=r"glyph must be a non-empty string"):
        TableColumnConfig.model_validate({"glyph": " "})


def test_null_schema_with_a_table_still_names_the_missing_type() -> None:
    with pytest.raises(ValidationError, match=r"needs `type: schema`"):
        AuthoredBoard.model_validate(
            _board(queries={"q": {"schema": None, "table": "t"}})
        )


@pytest.mark.parametrize(
    "chart",
    [
        {"type": "bar", "query": "q", "x": "a", "y": "b", "y_start": None},
        {
            "type": "bar",
            "query": "q",
            "x": "a",
            "y": "b",
            "layers": [{"type": "bar", "y": "c", "y_start": None}],
        },
    ],
)
def test_null_y_start_is_rejected_with_the_column_hint(
    chart: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="names no column"):
        AuthoredBoard.model_validate(_board(charts={"c": chart}))


def test_a_type_error_keeps_pydantics_message() -> None:
    chart = {"type": "pie", "query": "q", "theta": "v", "color": 5}
    with pytest.raises(ValidationError, match="valid string"):
        AuthoredBoard.model_validate(_board(charts={"c": chart}))
