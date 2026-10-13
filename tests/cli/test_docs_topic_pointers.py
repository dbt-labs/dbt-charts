from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

import dbt_charts
from dbt_charts.cli.main import app

_POINTER_RE = re.compile(r"dct docs ([a-z][a-z-]*)")
_SHIPPED_SUFFIXES = {".py", ".md"}


def _pointed_at_topics() -> list[str]:
    root = Path(dbt_charts.__file__).parent
    return sorted(
        {
            topic
            for path in root.rglob("*")
            if path.suffix in _SHIPPED_SUFFIXES
            for topic in _POINTER_RE.findall(path.read_text(encoding="utf-8"))
        }
    )


@pytest.mark.parametrize("topic", _pointed_at_topics())
def test_docs_pointer_names_an_existing_topic(topic: str) -> None:
    result = CliRunner().invoke(app, ["docs", topic])

    assert result.exit_code == 0, result.output
    assert "Unknown topic" not in result.output
