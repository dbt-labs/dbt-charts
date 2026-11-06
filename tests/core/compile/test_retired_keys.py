"""`retired_keys`: which keys a declared migration would act on, by concrete path."""

from __future__ import annotations

from dbt_charts.core.compile.migrations import retired_keys


def test_a_conditional_move_names_the_bound_successor_and_its_version() -> None:
    found = retired_keys(
        {"charts": {"c": {"type": "kpi", "style": {"tone": "positive"}}}}
    )

    key = found[("charts", "c", "style", "tone")]
    assert key.successor == ("charts", "c", "support", "tone")
    assert key.retired_in == "0.5.0"
    assert key.reason is None


def test_a_deletion_has_no_successor() -> None:
    found = retired_keys({"style": {"charts": {"animation_duration": 100}}})

    key = found[("style", "charts", "animation_duration")]
    assert key.successor is None


def test_a_current_document_reports_nothing() -> None:
    assert retired_keys({"title": "t", "rows": []}) == {}


def test_a_key_in_a_list_item_is_named_by_its_position() -> None:
    found = retired_keys(
        {"rows": [{"charts": {"c": {"type": "kpi", "style": {"tone": "positive"}}}}]}
    )

    assert found
    assert all(path[:2] == ("rows", "0") for path in found)
