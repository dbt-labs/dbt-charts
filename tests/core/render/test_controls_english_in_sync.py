"""The committed English controls words match the catalog they are generated from."""

from __future__ import annotations

from importlib.resources import files

from dbt_charts.core.render.controls import (
    controls_english_source,
    controls_runtime_source,
)


def _committed() -> str:
    path = files("dbt_charts.core.render") / "templates/scripts/controls_english.js"
    return path.read_text(encoding="utf-8")


def test_controls_english_js_matches_the_catalog() -> None:
    assert _committed() == controls_english_source(), (
        "controls_english.js is stale; run `just gen-controls-english`"
    )


def test_the_shipped_runtime_defines_the_words_before_the_controls_use_them() -> None:
    source = controls_runtime_source()
    assert source.index("window.dbtControlsEnglish") < source.index("function _words")
