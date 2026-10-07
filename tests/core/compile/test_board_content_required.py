"""A board must declare something that renders.

A board needs a layout, text, charts, or a title. Notes and styling alone do not
qualify: neither paints anything inside the box.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.board.authored import AuthoredBoard

from .._svg_render import render_board_to_svg

_NO_CONTENT = "Board must have at least one layout type, text, title, or chart"


class TestEmptyBoardRejected:
    def test_bare_board_raises(self):
        with pytest.raises(ValidationError, match="at least one layout type"):
            AuthoredBoard.model_validate({})

    def test_style_only_board_raises(self):
        """Styling is not content. A background paints an empty region."""
        with pytest.raises(ValidationError, match="at least one layout type"):
            AuthoredBoard.model_validate({"style": {"background": "vivid-10.1"}})

    def test_style_with_padding_only_raises(self):
        with pytest.raises(ValidationError, match="at least one layout type"):
            AuthoredBoard.model_validate({"style": {"padding": "8px"}})

    def test_notes_only_board_raises_at_parse(self) -> None:
        with pytest.raises(ValidationError) as exc:
            AuthoredBoard.model_validate({"notes": "why this board exists"})
        assert exc.value.errors()[0]["msg"] == f"Value error, {_NO_CONTENT}"


class TestBoardWithContentAccepted:
    """Each content key on its own satisfies the rule."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"text": "hello"},
            {"title": "Section"},
            {"rows": ["some_chart"]},
        ],
        ids=["text", "title", "rows"],
    )
    def test_content_key_is_enough(self, payload: dict[str, object]):
        AuthoredBoard.model_validate(payload)

    def test_styled_cell_with_text_is_the_swatch_shape(self):
        """The palette reference's swatch cells: a styled box carrying text."""
        AuthoredBoard.model_validate(
            {
                "text": " ",
                "width": "28px",
                "style": {"background": "vivid-10.1"},
            }
        )


def test_title_only_board_renders() -> None:
    assert "Hello" in render_board_to_svg("title: Hello\n")
