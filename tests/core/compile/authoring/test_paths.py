"""Authored paths quote a segment that dots or brackets would misread."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.authoring.paths import join_path, split_path


@pytest.mark.parametrize(
    ("segments", "path"),
    [
        (["rows", "0", "title"], "rows.0.title"),
        (["tabs", "Überblick"], "tabs.Überblick"),
        (["tabs", "Q1.5 / Q2", "rows", "0"], 'tabs["Q1.5 / Q2"].rows.0'),
        (["tabs", 'say "hi" [x]'], 'tabs["say \\"hi\\" [x]"]'),
    ],
)
def test_a_path_round_trips_its_segments(segments: list[str], path: str) -> None:
    assert join_path(segments) == path
    assert split_path(path) == segments


@pytest.mark.parametrize("path", ["rows..0", "rows.", 'tabs["open'])
def test_a_malformed_path_is_refused(path: str) -> None:
    with pytest.raises(ValueError, match="Invalid path"):
        split_path(path)
