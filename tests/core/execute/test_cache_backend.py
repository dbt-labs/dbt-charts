from datetime import UTC, datetime

import pytest

from dbt_charts.core.execute.cache_backend import CacheHit

_NOW = datetime.now(UTC)


@pytest.mark.parametrize("reason", [None, "max_rows", "max_result_bytes"])
def test_known_truncated_reason_is_accepted(reason: str | None) -> None:
    assert CacheHit(rows=[], written_at=_NOW, truncated_reason=reason).rows == []


def test_unknown_truncated_reason_raises() -> None:
    with pytest.raises(ValueError, match="truncated_reason.*'bogus'"):
        CacheHit(rows=[], written_at=_NOW, truncated_reason="bogus")
