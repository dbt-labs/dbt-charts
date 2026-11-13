"""Board titles must not leak typography across boards sharing one HTML page.

Inline SVG has no style scope: when two boards' SVGs land in one HTML
document (the docs themes page, or any Cloud/embed surface stacking more
than one board), their `<style>` blocks are both document-global. Before
mdsvg scoped its `.md-*` class names to a hash of their own style, every
board emitted the same literal `.md-heading` selector, so the last board's
rule won for every title on the page regardless of its own theme.

This reproduces that scenario at the board level: two boards on different
themes, concatenated the way an HTML page would, asserting their heading
rules carry distinct selectors.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from .._run import Run

_HEADING_RULE_RE = re.compile(r"\.(md-[0-9a-f]{8}-heading)\s*\{([^}]*)\}")

_BOARD_YAML = """\
theme: {theme}
title: Board Title
rows:
  - text: hello
"""


def setup_function() -> None:
    reset_config()


def teardown_function() -> None:
    reset_config()


def _render_svg(theme: str, local_project: Callable[..., FilesystemProject]) -> str:
    result = compile_board(_BOARD_YAML.format(theme=theme))
    assert result.success, result.errors
    assert result.board is not None
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
    )
    output = render(result.board, executor.ctx, executor.ledger, format="svg").output
    assert isinstance(output, str)
    return output


def _heading_rule(svg: str) -> tuple[str, str]:
    match = _HEADING_RULE_RE.search(svg)
    assert match is not None, f"no .md-*-heading rule found in: {svg[:400]}"
    return match.group(1), match.group(2)


def test_two_themed_board_titles_dont_share_css_scope_on_one_page(
    local_project: Callable[..., FilesystemProject],
) -> None:
    """The docs-page bug, as a unit test: two boards, two themes, one page."""
    clarity_svg = _render_svg("clarity", local_project)
    neon_svg = _render_svg("neon", local_project)

    clarity_class, clarity_rule = _heading_rule(clarity_svg)
    neon_class, neon_rule = _heading_rule(neon_svg)

    # The core fix: two themes with different heading fonts must not collapse
    # onto the same selector. Before scoping, both were the literal
    # unprefixed "md-heading" — this assertion is false on the old code.
    assert clarity_class != neon_class

    # Each theme's title text is actually painted with its own scoped class,
    # not just declared in an unused style rule.
    assert f'class="{clarity_class}"' in clarity_svg
    assert f'class="{neon_class}"' in neon_svg

    # Composite both boards into one HTML document, as the docs themes page
    # does with inline SVG — no style scope exists between them there.
    combined = f"<html><body>{clarity_svg}{neon_svg}</body></html>"

    # Both boards' own heading rules must survive, independently addressable
    # by their own selector — neon's rule is not overridden by clarity's.
    combined_rules = dict(_HEADING_RULE_RE.findall(combined))
    assert combined_rules[clarity_class] == clarity_rule
    assert combined_rules[neon_class] == neon_rule
