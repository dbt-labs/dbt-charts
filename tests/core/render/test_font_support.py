"""Fonts register with each vl-convert module once; a stand-in never stands for the real one."""

from __future__ import annotations

from types import ModuleType

import pytest

from dbt_charts.core.render import font_support


def _module(calls: list[str]) -> ModuleType:
    module = ModuleType("vlc")
    module.register_font_directory = calls.append  # type: ignore[attr-defined]
    return module


def test_a_stand_in_module_does_not_mark_the_real_one_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(font_support, "_registered_with", None)
    stand_in_calls: list[str] = []
    real_calls: list[str] = []
    stand_in, real = _module(stand_in_calls), _module(real_calls)

    font_support.register_vl_convert_fonts(stand_in)
    font_support.register_vl_convert_fonts(real)
    font_support.register_vl_convert_fonts(real)

    assert len(stand_in_calls) == 1
    assert len(real_calls) == 1
