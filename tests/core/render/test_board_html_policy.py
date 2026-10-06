"""Tests for the board-level html_policy field.

When html_policy is set on a board, the body text rendering is governed by the
tier:
- "none" (default): HTML in text is escaped / rendered as plain markdown.
- "safe-subset": fails closed — no foreignObject.
- "trusted-raw": HTML in text IS rendered via foreignObject (mdsvg's
  allow_raw_html=True path), sanitized by nh3. TRUSTED-CONTENT ONLY.

Covers:
- html_policy="none" (default): HTML in text is NOT rendered via foreignObject.
- html_policy="safe-subset": HTML in text is NOT rendered via foreignObject
  (fail-closed).
- html_policy="trusted-raw": HTML in text IS rendered via foreignObject.
- Model round-trip: authored YAML html_policy -> normalized Board -> ResolvedBoard.
- Reflected-XSS regression: a viewer-controlled variable interpolated on its
  own line does not produce event handlers in rendered output.
- Ceiling render: DCT_HTML_POLICY_CEILING=safe-subset prevents foreignObject
  even when the board explicitly requests trusted-raw.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import render as render_board


def _compile_and_render(
    board_yaml: str,
    tmp_path: object,
    local_project: Callable[..., Project],
    variables: dict[str, str] | None = None,
) -> str:
    """Compile board_yaml, render to SVG, and return the SVG string."""
    assert isinstance(tmp_path, Path)
    result = compile(board_yaml)
    assert result.board is not None
    rendered = render_board(
        result.board,
        Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        ),
        format="svg",
        variables=variables,
    )
    output = rendered.output
    assert isinstance(output, str)
    return output


_HTML_TEXT = '<div style="display:flex">hello</div>'

_BOARD_DEFAULT = f"""\
title: Test
text: |
  {_HTML_TEXT}
"""

_BOARD_NONE = f"""\
title: Test
html_policy: "none"
text: |
  {_HTML_TEXT}
"""

_BOARD_SAFE_SUBSET = f"""\
title: Test
html_policy: "safe-subset"
text: |
  {_HTML_TEXT}
"""

_BOARD_TRUSTED_RAW = f"""\
title: Test
html_policy: "trusted-raw"
text: |
  {_HTML_TEXT}
"""

# XSS payload that would execute if onerror is not stripped.  Placed on its own
# line so mdsvg's HTML-block detector (line starts with "<") routes it through
# _sanitize_html() rather than escaping it as plain paragraph text.
_XSS_PAYLOAD = "<img src=x onerror=alert(1)>"

_BOARD_VAR_TRUSTED_RAW = """\
title: Test
html_policy: "trusted-raw"
text: |
  {{ xss_var }}
"""

_BOARD_VAR_SAFE_SUBSET = """\
title: Test
html_policy: "safe-subset"
text: |
  {{ xss_var }}
"""


class TestHtmlPolicyModel:
    def test_default_is_none(self) -> None:
        result = compile(_BOARD_DEFAULT)
        assert result.board is not None
        assert result.board.html_policy == "none"

    def test_authored_none_propagates(self) -> None:
        result = compile(_BOARD_NONE)
        assert result.board is not None
        assert result.board.html_policy == "none"

    def test_authored_safe_subset_propagates(self) -> None:
        result = compile(_BOARD_SAFE_SUBSET)
        assert result.board is not None
        assert result.board.html_policy == "safe-subset"

    def test_authored_trusted_raw_propagates(self) -> None:
        result = compile(_BOARD_TRUSTED_RAW)
        assert result.board is not None
        assert result.board.html_policy == "trusted-raw"

    def test_resolved_board_carries_none(self) -> None:
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = compile(_BOARD_DEFAULT)
        assert result.board is not None
        resolved = resolve_board(result.board)
        assert resolved.html_policy == "none"

    def test_resolved_board_carries_safe_subset(self) -> None:
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = compile(_BOARD_SAFE_SUBSET)
        assert result.board is not None
        resolved = resolve_board(result.board)
        assert resolved.html_policy == "safe-subset"

    def test_resolved_board_carries_trusted_raw(self) -> None:
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = compile(_BOARD_TRUSTED_RAW)
        assert result.board is not None
        resolved = resolve_board(result.board)
        assert resolved.html_policy == "trusted-raw"


class TestHtmlPolicyRender:
    def test_policy_none_does_not_produce_foreignobject(
        self, tmp_path: object, local_project: Callable[..., Project]
    ) -> None:
        output = _compile_and_render(_BOARD_DEFAULT, tmp_path, local_project)
        assert "<foreignObject" not in output

    def test_policy_safe_subset_does_not_produce_foreignobject(
        self, tmp_path: object, local_project: Callable[..., Project]
    ) -> None:
        """safe-subset fails closed — no foreignObject."""
        output = _compile_and_render(_BOARD_SAFE_SUBSET, tmp_path, local_project)
        assert "<foreignObject" not in output

    def test_policy_trusted_raw_produces_foreignobject(
        self, tmp_path: object, local_project: Callable[..., Project]
    ) -> None:
        output = _compile_and_render(_BOARD_TRUSTED_RAW, tmp_path, local_project)
        assert "<foreignObject" in output
        assert "<div" in output


_BOARD_ROW_TEXT = f"""\
title: Test
html_policy: "trusted-raw"
rows:
  - text: |
      {_HTML_TEXT}
"""

_BOARD_ROW_TEXT_OVERRIDE = f"""\
title: Test
html_policy: "trusted-raw"
rows:
  - html_policy: "none"
    text: |
      {_HTML_TEXT}
"""

_BOARD_TAB_TEXT = f"""\
title: Test
html_policy: "trusted-raw"
tabs:
  items:
    - title: One
      text: |
        {_HTML_TEXT}
"""

_BOARD_TAB_ROWS_TEXT = f"""\
title: Test
html_policy: "trusted-raw"
tabs:
  items:
    - title: One
      rows:
        - text: |
            {_HTML_TEXT}
"""


class TestHtmlPolicyInheritedByNestedBoards:
    """A layout `- text:` row or tab is a nested board; it inherits the parent's tier."""

    @pytest.mark.parametrize(
        "yaml_", [_BOARD_ROW_TEXT, _BOARD_TAB_TEXT, _BOARD_TAB_ROWS_TEXT]
    )
    def test_nested_text_inherits_trusted_raw(
        self,
        yaml_: str,
        tmp_path: object,
        local_project: Callable[..., Project],
    ) -> None:
        output = _compile_and_render(yaml_, tmp_path, local_project)
        assert "<foreignObject" in output
        assert "&lt;div" not in output

    def test_nested_authored_policy_wins_over_parent(
        self, tmp_path: object, local_project: Callable[..., Project]
    ) -> None:
        output = _compile_and_render(_BOARD_ROW_TEXT_OVERRIDE, tmp_path, local_project)
        assert "<foreignObject" not in output
        assert "&lt;div" in output

    def test_ceiling_caps_inherited_policy(
        self,
        tmp_path: object,
        local_project: Callable[..., Project],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "safe-subset")
        output = _compile_and_render(_BOARD_ROW_TEXT, tmp_path, local_project)
        assert "<foreignObject" not in output


class TestReflectedXssViaBoardVariable:
    """Regression: URL-variable reflected-XSS path is closed by the nh3 sanitizer.

    board_variables_from_query (view_state.py) passes request.GET directly as
    variable values — the viewer controls the variable, not the board author.  A
    board with html_policy above "none" whose body text interpolates a dashboard
    variable on its own line is the attack surface: the resolved value starts with
    "<", which mdsvg's HTML-block detector parses as a RawHtmlBlock and routes
    through _sanitize_html().

    - trusted-raw: the payload reaches _sanitize_html(), which strips onerror.
    - safe-subset: fails closed — no foreignObject, so the payload never
      reaches the raw-HTML code path.
    """

    def test_trusted_raw_strips_event_handler_from_interpolated_variable(
        self, tmp_path: object, local_project: Callable[..., Project]
    ) -> None:
        """trusted-raw: nh3 sanitizer strips onerror from a viewer-controlled variable."""
        output = _compile_and_render(
            _BOARD_VAR_TRUSTED_RAW, tmp_path, local_project, {"xss_var": _XSS_PAYLOAD}
        )
        # The payload started a line with "<" — mdsvg parsed it as a RawHtmlBlock
        # and routed it through _sanitize_html().  foreignObject appears, but the
        # event handler must not survive the nh3 allowlist walk.
        assert "<foreignObject" in output
        assert "onerror" not in output

    def test_safe_subset_excludes_raw_html_from_interpolated_variable(
        self, tmp_path: object, local_project: Callable[..., Project]
    ) -> None:
        """safe-subset: fails closed — no foreignObject, so no raw HTML at all.

        The XSS payload is escaped as SVG display text (safe), not injected as
        HTML.  "onerror" may appear as visible text content in the SVG; what
        matters is that no unescaped <img element and no foreignObject exist.
        """
        output = _compile_and_render(
            _BOARD_VAR_SAFE_SUBSET, tmp_path, local_project, {"xss_var": _XSS_PAYLOAD}
        )
        assert "<foreignObject" not in output
        # The "<" in <img is HTML-escaped, so the element never appears literally.
        assert "<img" not in output


class TestHtmlPolicyCeilingRender:
    """Regression: ceiling-capped trusted-raw boards produce safe SVG output.

    compile/ tests (test_html_policy_ceiling.py) confirm the normalized model's
    .html_policy is downgraded by the ceiling; these confirm the rendered SVG
    also has no <foreignObject> — the ceiling applies end-to-end, not just at
    the model level.

    A deployment that pins DCT_HTML_POLICY_CEILING=safe-subset must render
    a board that explicitly requests trusted-raw without any foreignObject.
    """

    def test_ceiling_safe_subset_prevents_foreignobject(
        self,
        tmp_path: object,
        local_project: Callable[..., Project],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DCT_HTML_POLICY_CEILING=safe-subset: trusted-raw board has no foreignObject.

        A deployment that pins the ceiling to safe-subset gets fail-closed
        rendering — no raw HTML reaches the browser regardless of the board's
        authored html_policy.
        """
        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "safe-subset")
        output = _compile_and_render(_BOARD_TRUSTED_RAW, tmp_path, local_project)
        assert "<foreignObject" not in output
