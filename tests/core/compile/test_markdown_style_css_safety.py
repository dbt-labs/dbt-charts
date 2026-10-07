"""An authored font or color string cannot inject CSS into a rendered board."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.compile.resolve.style.markdown_style import get_compact_style
from dbt_charts.core.diagnostics.codes_compile import ERR_STYLE_CSS_VALUE_INVALID

_EVIL = "x;} svg{background:url(https://evil)} .a{"


def _style_with(model_copy_at: Callable[..., Any], leaf: str, value: str) -> Any:
    return resolve_style(model_copy_at(get_theme_style(), f"text.font.{leaf}", value))


def test_authored_font_family_is_escaped_in_prose_css(
    model_copy_at: Callable[..., Any],
) -> None:
    from mdsvg import render

    svg = render(
        "hello", style=get_compact_style(_style_with(model_copy_at, "family", _EVIL))
    )
    css = re.search(r"<style>(.*?)</style>", svg, re.S)
    assert css is not None
    selectors = re.findall(r"([^{}]*)\{", css.group(1))
    assert all(sel.strip().startswith(".md-") for sel in selectors)


def test_authored_color_with_css_breakout_raises_clean_diagnostic(
    model_copy_at: Callable[..., Any],
) -> None:
    with pytest.raises(CompilationError) as exc:
        get_compact_style(
            _style_with(model_copy_at, "color", "red;} svg{background:url(//evil)}")
        )
    assert exc.value.code is ERR_STYLE_CSS_VALUE_INVALID
