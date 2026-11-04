from __future__ import annotations

import pytest

from dbt_charts.core.compile import merge
from dbt_charts.core.compile.merge import resolve_built_in_theme


def test_built_in_theme_yaml_is_parsed_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every board compile folds its theme for the restated-style warnings;
    the theme files are package data, so only the first fold reads them."""
    resolve_built_in_theme("clarity")
    parsed: list[str] = []
    real = merge._fragment_from_yaml

    def _counting(*args: object, **kwargs: object) -> object:
        parsed.append(str(args[1]))
        return real(*args, **kwargs)

    monkeypatch.setattr(merge, "_fragment_from_yaml", _counting)
    resolve_built_in_theme("clarity")
    assert parsed == []


def test_built_in_theme_callers_do_not_share_one_patch() -> None:
    first = resolve_built_in_theme("clarity")
    assert first.style is not None
    first.style = None
    assert resolve_built_in_theme("clarity").style is not None
