"""``regional.time_zone`` is the zone a board reads its clock and timestamps in."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.primitives import Regional


def test_default_zone_is_utc() -> None:
    assert Regional().time_zone == "UTC"


def test_unknown_zone_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown IANA time zone"):
        Regional(time_zone="Mars/Olympus")


def test_today_is_the_board_zone_date() -> None:
    # 03:00 UTC on Oct 21 is still Oct 20 in New York and already Oct 21 in Tokyo.
    instant = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
    assert Regional().today(instant) == date(2026, 10, 21)
    assert Regional(time_zone="America/New_York").today(instant) == date(2026, 10, 20)
    assert Regional(time_zone="Asia/Tokyo").today(instant) == date(2026, 10, 21)


def test_today_rejects_a_naive_instant() -> None:
    with pytest.raises(ValueError, match="naive"):
        Regional().today(datetime(2026, 10, 21, 3, 0))
