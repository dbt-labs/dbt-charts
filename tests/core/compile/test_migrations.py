from __future__ import annotations

from typing import cast

import pytest

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    prepare_board_mapping,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
)


def test_current_recognition_schema_accepts_layout_name_shorthand() -> None:
    raw = {"rows": ["revenue_trend"]}

    assert prepare_board_mapping(cast(JsonObject, raw)) == raw


def test_current_pydantic_shorthand_is_not_rejected_by_schema_recognition() -> None:
    raw = {"theme": "vivid"}

    assert prepare_board_mapping(raw) == raw


def test_prepare_mapping_current_schema_patch_is_returned_untouched() -> None:
    """A current-schema, patch-shaped fragment (the shape of every theme YAML
    and meta.yml) is not a migration candidate."""
    raw: JsonObject = {"style": {"charts": {"axis_x": {"labels": {"format": "auto"}}}}}

    assert prepare_board_mapping(raw) is raw


@pytest.mark.parametrize(
    "raw",
    [{"title": "Revenue", "theme": "paper"}, {"theme": "paper"}],
)
def test_prepare_mapping_theme_sugar_is_current(raw: JsonObject) -> None:
    """`theme:` is permanent sugar for `extends:`, so a board authoring it is
    current."""
    assert prepare_board_mapping(raw) is raw


def test_prepare_mapping_migrates_style_only_patch_against_real_catalog() -> None:
    """A style-only fragment (meta.yml, extends target) has no board content,
    so the board's cross-field rules must not block its migration."""

    with pytest.warns(SchemaMigrationWarning, match="migrated this YAML in memory"):
        prepared = prepare_board_mapping({"style": {"title": {"sizes": [20, 16]}}})
    style = prepared["style"]
    assert isinstance(style, dict)
    assert style["fonts"] == {"heading": {"sizes": [20, 16]}}


def test_prepare_mapping_returns_numeric_keys_as_the_numbers_they_were() -> None:
    """`thresholds: {0: red}` authors a number key; the engine reads keys as
    text and hands the number back, in a document it migrated elsewhere."""
    raw: JsonObject = {
        "charts": {
            "t": {
                "type": "table",
                "query": "q",
                "style": {
                    "columns": {
                        "v": {
                            "spark": {"type": "bar", "thresholds": {0: "red", 1.5: "b"}}
                        }
                    }
                },
            }
        },
        "style": {"title": {"sizes": [20, 16]}},
    }

    with pytest.warns(SchemaMigrationWarning):
        prepared = prepare_board_mapping(raw)

    style = prepared["style"]
    assert isinstance(style, dict)
    assert style["fonts"] == {"heading": {"sizes": [20, 16]}}
    assert prepared["charts"] == raw["charts"]


def test_prepare_mapping_refuses_a_value_that_is_not_the_raw_document() -> None:
    """Migration runs before merge and typing, so a model instance reaching it
    is a caller bug named in an ERR-INTERNAL, never passed through."""
    from dbt_charts.core.compile.errors import ParseError
    from dbt_charts.core.diagnostics import ERR_INTERNAL

    with pytest.raises(ParseError, match="test_migrations") as caught:
        prepare_board_mapping({"cache": object()})

    assert caught.value.code is ERR_INTERNAL


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 2**70])
def test_prepare_mapping_refuses_numbers_json_cannot_hold(value: float) -> None:
    from dbt_charts.core.compile.errors import ParseError
    from dbt_charts.core.diagnostics.codes_render import ERR_ENGINE_REFUSED

    with pytest.raises(ParseError) as caught:
        prepare_board_mapping({"width": value})

    assert caught.value.code is ERR_ENGINE_REFUSED


@pytest.mark.parametrize(
    "document",
    [{(1, 2): 1}, {"blob": b"x"}, {"tags": {1, 2}}, {"a\ud800": 1}],
)
def test_prepare_mapping_refuses_authored_values_the_engine_does_not_carry(
    document: dict[object, object],
) -> None:
    """PyYAML loads these from authored text; like the `yaml` crate, the engine
    refuses them by name, which is not a bug in the calling function."""
    from dbt_charts.core.compile.errors import ParseError
    from dbt_charts.core.diagnostics.codes_render import ERR_ENGINE_REFUSED

    with pytest.raises(ParseError) as caught:
        prepare_board_mapping(document)

    assert caught.value.code is ERR_ENGINE_REFUSED


def test_prepare_mapping_carries_null_bool_and_number_keys_as_they_were() -> None:
    """PyYAML loads `~: x`, `yes: x` and `1.5: x` as non-string keys; the
    engine keeps their kind and hands the same objects back."""
    document: dict[object, object] = {"title": "t", None: 1, True: 2, 1.5: 3, 7: 4}

    prepared = prepare_board_mapping(document)

    assert prepared == document
    assert [type(k) for k in prepared] == [type(k) for k in document]


def test_prepare_mapping_refuses_two_keys_that_are_one_key_in_json_text() -> None:
    """`1` and `"1"` are two Python keys but one JSON key: the engine refuses
    the document by name rather than let one entry overwrite the other."""
    from dbt_charts.core.compile.errors import ParseError
    from dbt_charts.core.diagnostics.codes_render import ERR_ENGINE_REFUSED

    with pytest.raises(ParseError) as caught:
        prepare_board_mapping({"title": "t", "options": {1: "a", "1": "b"}})

    assert caught.value.code is ERR_ENGINE_REFUSED
