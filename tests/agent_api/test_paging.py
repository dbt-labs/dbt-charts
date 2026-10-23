from __future__ import annotations

import pytest

from dbt_charts.agent_api.paging import cursor_offset, paginate


def test_a_zero_limit_page_has_no_cursor_to_loop_on() -> None:
    assert paginate([1, 2, 3], limit=0, cursor=None) == ([], None)


def test_a_cursor_that_is_not_a_next_value_is_refused() -> None:
    with pytest.raises(ValueError, match="is not a cursor"):
        cursor_offset("ERR-NO-LAYOUT")
