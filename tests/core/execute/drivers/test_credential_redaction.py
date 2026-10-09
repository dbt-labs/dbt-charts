"""A driver that quotes a credential back must not carry it into a result.

`redact_values` is the exact-match half: a driver holds the options it just sent,
so it can remove those values by identity, which catches a token quoted in prose
with no field name beside it for a pattern to find.
"""

from __future__ import annotations

from dbt_charts.core.execute.adapters.base import redact_values

_TOKEN = "fake-warehouse-token-value"


def test_every_occurrence_of_a_secret_is_removed() -> None:
    scrubbed = redact_values(f"401 for {_TOKEN}; retry with {_TOKEN}", [_TOKEN])
    assert _TOKEN not in scrubbed


def test_the_rest_of_the_message_survives() -> None:
    scrubbed = redact_values(f"cannot reach api.example: bad {_TOKEN}", [_TOKEN])
    assert "cannot reach api.example" in scrubbed


def test_a_longer_secret_is_removed_before_one_it_contains() -> None:
    long = "abcd-long-secret"
    scrubbed = redact_values(f"x {long} y", ["abcd", long])
    assert "long-secret" not in scrubbed


def test_a_trivially_short_value_is_not_treated_as_a_secret() -> None:
    """Removing every `1` would shred the message it was found in."""
    assert redact_values("port 5439 on host 1", ["1"]) == "port 5439 on host 1"
