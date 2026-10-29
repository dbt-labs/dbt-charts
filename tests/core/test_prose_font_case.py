"""``style.text.font.case`` is honored in board prose, headings and body alike."""

from __future__ import annotations

import re
from collections.abc import Callable

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.theme.style import Style
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.compile.resolve.style.markdown_style import (
    prose_content_widths,
)
from dbt_charts.core.render.prose import render_prose_svg

from ._prose_plan import full_width_plan

_MARKDOWN = "# the quick brown fox\n\nbody `code span` text here"


def _painted_text(case: str | None, model_copy_at: Callable[..., Style]) -> str:
    theme = get_theme_style("clarity")
    if case is not None:
        theme = model_copy_at(theme, "text.font.case", case)
    resolved = resolve_style(theme)
    svg, _ = render_prose_svg(
        _MARKDOWN,
        600.0,
        resolved.text,
        resolved,
        authored=_MARKDOWN,
        base_dir=None,
        error_collector=None,
        plan=full_width_plan(resolved, 600.0),
    )
    return " ".join(re.findall(r">([^<]+)</", svg))


def test_title_case_reaches_headings_and_body_but_not_code(
    model_copy_at: Callable[..., Style],
) -> None:
    painted = _painted_text("title", model_copy_at)
    assert "The Quick Brown Fox" in painted
    assert "Text Here" in painted
    assert "code span" in painted


def test_upper_case_applies_to_body_text(model_copy_at: Callable[..., Style]) -> None:
    painted = _painted_text("upper", model_copy_at)
    assert "THE QUICK BROWN FOX" in painted
    assert "TEXT HERE" in painted


def test_unset_case_leaves_prose_as_written(
    model_copy_at: Callable[..., Style],
) -> None:
    painted = _painted_text(None, model_copy_at)
    assert "text here" in painted
    # The heading follows style.title.font.case (title in clarity), not text.font.case.
    assert "The Quick Brown Fox" in painted


def test_content_width_is_measured_on_the_cased_text(
    model_copy_at: Callable[..., Style],
) -> None:
    text = "revenue grew steadily across every region"

    def widths(case: str) -> tuple[float, float]:
        theme = model_copy_at(get_theme_style("clarity"), "text.font.case", case)
        return prose_content_widths(text, resolve_style(theme), {}, None)

    lower_floor, lower_max = widths("lower")
    upper_floor, upper_max = widths("upper")
    assert upper_floor > lower_floor
    assert upper_max > lower_max


def test_title_case_keeps_a_heading_path_as_written(
    model_copy_at: Callable[..., Style],
) -> None:
    markdown = "# Directory /sales\n\nbody"
    resolved = resolve_style(get_theme_style("clarity"))
    svg, _ = render_prose_svg(
        markdown,
        600.0,
        resolved.text,
        resolved,
        authored=markdown,
        base_dir=None,
        error_collector=None,
        plan=full_width_plan(resolved, 600.0),
    )
    assert "Directory /sales" in " ".join(re.findall(r">([^<]+)</", svg))
