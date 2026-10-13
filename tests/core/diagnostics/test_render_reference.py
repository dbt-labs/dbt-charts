"""The rendered error/warning references survive real Markdown."""

from __future__ import annotations

import re
from typing import Literal

import markdown
import pytest

from dbt_charts.core.diagnostics.render_reference import render_reference


@pytest.mark.parametrize("level", ["error", "warning"])
def test_rendered_reference_survives_real_markdown(
    level: Literal["error", "warning"],
) -> None:
    """Render the generator's output through real Markdown with the docs
    site's `attr_list` extension and check the HTML itself. A message
    template's backticks or newlines must stay inside their fence
    (`_fence_for`), and `attr_list` must not read a trailing `{placeholder}`
    as an inline attribute list.
    """
    html = markdown.markdown(
        render_reference(level),
        extensions=["attr_list", "pymdownx.superfences", "toc"],
    )
    # A backtick inside a fenced <pre><code> block is the raw, correctly-fenced
    # payload — strip fenced blocks so only a backtick that broke *out* of its
    # fence and corrupted the surrounding prose is caught.
    prose_only = re.sub(r"<pre>.*?</pre>", "", html, flags=re.DOTALL)
    assert "`" not in prose_only, (
        f"A literal backtick survived into the rendered {level} reference "
        "prose (outside a <pre> block) — a message template's backticks "
        "broke out of its fence."
    )
    for code_tag in re.findall(r"<code[^>]*>", html):
        assert re.fullmatch(r"<code(?: class=\"[\w -]+\")?>", code_tag), (
            f"<code> tag carries an unexpected attribute in the rendered "
            f"{level} reference: {code_tag!r} — likely `attr_list` misreading "
            "a `{placeholder}` in a message template as an inline attribute list."
        )
