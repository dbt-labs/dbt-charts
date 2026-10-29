"""An exception no registered code classifies keeps what a bug report needs."""

from __future__ import annotations

import logging

import pytest

from dbt_charts.core.diagnostics import ERR_INTERNAL
from dbt_charts.core.render.errors import RenderError


def _raised() -> TypeError:
    try:
        raise TypeError("lineage() missing 1 required positional argument: 'schema'")
    except TypeError as exc:
        return exc


def test_unexpected_error_names_the_exception_type() -> None:
    err = RenderError.from_unexpected(_raised())

    assert err.code is ERR_INTERNAL
    assert str(err) == (
        "TypeError: lineage() missing 1 required positional argument: 'schema'"
    )


def test_unexpected_error_carries_the_traceback_to_the_diagnostic() -> None:
    diagnostic = RenderError.from_unexpected(_raised()).to_diagnostic()

    assert diagnostic.detail is not None
    assert diagnostic.detail.startswith("Traceback (most recent call last):")
    assert "in _raised" in diagnostic.detail


def test_unexpected_error_logs_the_traceback(caplog: pytest.LogCaptureFixture) -> None:
    exc = _raised()
    with caplog.at_level(logging.ERROR):
        RenderError.from_unexpected(exc)

    (record,) = caplog.records
    assert record.exc_info is not None
    assert record.exc_info[1] is exc
