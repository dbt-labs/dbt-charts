"""The engine owns the ``email`` / ``url`` formats: the payload carries them as authored."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dbt_charts import _engine

from ..._svg_render import render_board_to_svg
from ..._text_format_board import ROWS, link_hrefs, text_format_board


@pytest.fixture
def payloads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    real = _engine.render_chart

    def spy(payload_json: str) -> Any:
        seen.append(json.loads(payload_json))
        return real(payload_json)

    monkeypatch.setattr(_engine, "render_chart", spy)
    return seen


def test_table_payload_keeps_the_text_format_and_blank_cells(
    payloads: list[dict[str, Any]],
) -> None:
    svg = render_board_to_svg(text_format_board(ROWS))
    payload = payloads[-1]
    columns = payload["resolved"]["columns"]
    assert columns["email"]["format"]["spec"] == "email"
    assert columns["site"]["format"]["spec"] == "url"
    assert payload["rows"][1]["email"] == ""
    assert link_hrefs(svg) == [
        "mailto:ann%2Bx%40example.com",
        "https://a.example/p?q=1",
    ]
    assert "ann+x@example.com" in svg
    assert "—" in svg


def test_kpi_payload_sends_text_link_beside_link(
    payloads: list[dict[str, Any]],
) -> None:
    rows = "      - {email: 'ann@example.com'}\n"
    chart = "    type: kpi\n    value: email\n"
    svg = render_board_to_svg(
        text_format_board(rows, chart, formats="      email: email\n")
    )
    resolved = payloads[-1]["resolved"]
    assert resolved["format"]["spec"] == "email"
    assert resolved["link"] is None
    assert resolved["text_link"] == "mailto:ann@example.com"
    assert link_hrefs(svg) == ["mailto:ann@example.com"]
    assert "ann@example.com" in svg
