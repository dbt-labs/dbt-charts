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

    def spy(rows: Any, datasets: Any, layout_json: str) -> Any:
        seen.append(json.loads(layout_json))
        return real(rows, datasets, layout_json)

    monkeypatch.setattr(_engine, "render_chart", spy)
    return seen


@pytest.fixture
def loaded_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    real = _engine.load_rows

    def spy(rows_json: str) -> Any:
        seen.append(json.loads(rows_json))
        return real(rows_json)

    monkeypatch.setattr(_engine, "load_rows", spy)
    return seen


def test_table_payload_keeps_the_text_format_and_blank_cells(
    payloads: list[dict[str, Any]], loaded_rows: list[dict[str, Any]]
) -> None:
    svg = render_board_to_svg(text_format_board(ROWS))
    payload = payloads[-1]
    columns = payload["resolved"]["columns"]
    assert columns["email"]["format"]["spec"] == "email"
    assert columns["site"]["format"]["spec"] == "url"
    assert link_hrefs(svg) == [
        "mailto:ann%2Bx%40example.com",
        "https://a.example/p?q=1",
    ]
    assert "ann+x@example.com" in svg
    assert "—" in svg
    assert [load["rows"][1]["email"] for load in loaded_rows] == [""]
