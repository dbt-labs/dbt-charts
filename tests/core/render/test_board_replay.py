"""dump/load a resolved-board artifact and render it from a recording.

Complements `test_replay_render.py` (which proves the raw
`ReplayDataProvider` + `render_board_svg` seam) by testing the higher-level
`board_replay` functions the `agent_api` emit/load surface calls: JSON
dump/load with registered `ERR-*` codes on malformed input, and
`render_board_from_artifact` wrapping `ReplayDataProvider` construction.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.recording import (
    BoardRecording,
    load_board_recording,
    record_board,
)
from dbt_charts.core.render.board_replay import (
    dump_board_artifact,
    load_board_artifact,
    render_board_from_artifact,
)
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.boards import render_board_svg
from dbt_charts.core.render.controls import interactive_controls

_CLIP_ID = re.compile(r"clip\d+")
_RENDER_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_DATA_AS_OF = re.compile(r"Data as of [^<]+")

_YAML = """
title: Board Replay
queries:
  channel_mix:
    columns: [month, channel, signups]
    values:
      - ["2026-01-01", "Organic", 90]
      - ["2026-02-01", "Organic", 105]
      - ["2026-01-01", "Paid", 40]
      - ["2026-02-01", "Paid", 48]
charts:
  mix:
    query: channel_mix
    type: bar
    x: month
    y: signups
    color: channel
rows:
  - mix
"""

_PIE_YAML = """
title: Pie Replay
queries:
  shares:
    columns: [series, value]
    values:
      - [A, 1]
      - [B, 1]
      - [C, 1]
      - [D, 1]
      - [E, 1]
      - [F, 1]
      - [G, 1]
      - [H, 1]
      - [I, 1]
      - [J, 1]
      - [K, 1]
      - [L, 1]
      - [M, 1]
charts:
  shares:
    query: shares
    type: pie
    theta: value
    color: series
rows:
  - shares
"""


def _normalize(svg: str) -> str:
    out = _CLIP_ID.sub("clipN", svg)
    out = _RENDER_TIME.sub("RENDER_TIME", out)
    return _DATA_AS_OF.sub("DATA_AS_OF", out)


def _live(tmp_path: Path) -> tuple[bytes, BoardRecording, str]:
    """Compile + resolve + render a board live, returning artifact bytes,
    recording, and the live SVG for comparison."""
    result = compile_board(_YAML)
    assert result.success, result.errors
    assert result.board is not None

    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict[str, object] = {}
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    # Match render_board_from_artifact's own background derivation, so the
    # comparison isolates artifact fidelity rather than a background mismatch.
    background = resolved.style.background
    live_svg = render_board_svg(
        resolved,
        executor,
        variables,
        background=None if background == "transparent" else background,
        render_cache=render_cache,
    )
    recording = record_board(resolved, executor, variables)
    return dump_board_artifact(resolved), recording, live_svg


def _pie_live(tmp_path: Path) -> tuple[bytes, BoardRecording, str]:
    result = compile_board(_PIE_YAML)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    variables: dict[str, object] = {}
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    background = resolved.style.background
    live_svg = render_board_svg(
        resolved,
        executor,
        variables,
        background=None if background == "transparent" else background,
        render_cache=render_cache,
    )
    return (
        dump_board_artifact(resolved),
        record_board(resolved, executor, variables),
        live_svg,
    )


def test_dump_and_load_board_artifact_round_trips(tmp_path: Path) -> None:
    artifact_bytes, _recording, _live_svg = _live(tmp_path)

    reloaded = load_board_artifact(artifact_bytes)

    assert reloaded.queries.keys() == {"channel_mix"}


def test_pie_attachment_round_trips_in_board_artifact(tmp_path: Path) -> None:
    from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart

    result = compile_board(_PIE_YAML)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    resolved, _render_cache = build_resolved_board(result.board, executor, {})
    original = resolved.charts["shares"]
    assert isinstance(original, ResolvedPieChart)
    assert original.attached_table is not None

    reloaded = load_board_artifact(dump_board_artifact(resolved))
    restored = reloaded.charts["shares"]

    assert isinstance(restored, ResolvedPieChart)
    assert restored == original
    assert restored.attached_table is not None

    pie_document = json.loads(dump_board_artifact(resolved))["board"]["charts"][
        "shares"
    ]
    assert "attached_rows" not in pie_document
    assert "slice_label_lines" not in pie_document
    assert "__dbt_label" not in json.dumps(pie_document)


def test_pie_artifact_replays_the_same_recording(tmp_path: Path) -> None:
    artifact_bytes, recording, live_svg = _pie_live(tmp_path)
    reloaded_recording = load_board_recording(recording.model_dump_json().encode())

    replay_svg = render_board_from_artifact(
        load_board_artifact(artifact_bytes),
        reloaded_recording,
        reloaded_recording.variables,
    )

    assert _normalize(replay_svg) == _normalize(live_svg)


def test_pie_fingerprint_survives_recording_json_non_finite_values() -> None:
    rows = [{"series": "A", "value": 1, "unrelated": float("nan")}]
    recording = BoardRecording(
        recorded_at=datetime.now(timezone.utc),
        variables={},
        rows_by_query={"shares": rows},
        provenance={},
    )

    reloaded = load_board_recording(recording.model_dump_json().encode())

    assert pie_presentation_fingerprint(
        reloaded.rows_by_query["shares"]
    ) == pie_presentation_fingerprint(rows)


def test_pie_artifact_rejects_changed_recording_rows(tmp_path: Path) -> None:
    artifact_bytes, recording, _live_svg = _pie_live(tmp_path)
    changed_rows = [dict(row) for row in recording.rows_by_query["shares"]]
    changed_rows[0]["value"] = 99
    changed = BoardRecording(
        recorded_at=recording.recorded_at,
        variables=recording.variables,
        rows_by_query=recording.rows_by_query | {"shares": changed_rows},
        provenance=recording.provenance,
    )

    with pytest.raises(DbtChartsError) as exc_info:
        render_board_from_artifact(
            load_board_artifact(artifact_bytes), changed, changed.variables
        )

    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-RESOLVED-PIE-DATA-MISMATCH"


def test_load_board_artifact_raises_registered_error_on_malformed_json() -> None:
    with pytest.raises(DbtChartsError) as exc_info:
        load_board_artifact(b"{not json")

    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-BOARD-ARTIFACT-INVALID"


def test_load_board_artifact_translates_a_dangling_style_ref(tmp_path: Path) -> None:
    """A style table missing an entry is a registered error, not a raw exception.

    The codec raises ``DanglingStyleRefError`` (a plain ``ValueError``) because it
    has no user-facing entry point of its own. This loader *is* that boundary, so
    it must translate — otherwise an incomplete artifact would surface to a CLI
    caller as ``ERR-INTERNAL``, which signals a bug rather than a real, actionable
    failure.
    """
    artifact_bytes, _recording, _live_svg = _live(tmp_path)
    artifact = json.loads(artifact_bytes)
    # Drop the style table while leaving every $style_ref marker in place.
    artifact["styles"] = {}

    with pytest.raises(DbtChartsError) as exc_info:
        load_board_artifact(json.dumps(artifact).encode())

    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-BOARD-ARTIFACT-INVALID"


def test_the_artifact_carries_no_row_data(tmp_path: Path) -> None:
    artifact_bytes, _recording, _live_svg = _live(tmp_path)

    artifact = json.loads(artifact_bytes)

    assert "rows" not in artifact
    assert "data" not in artifact


_FACETED_DATETIME_YAML = """
title: Faceted Datetime Replay
queries:
  channel_mix:
    columns: [month, channel, signups]
    values:
      - [2026-01-01T00:00:00Z, Organic, 90]
      - [2026-02-01T00:00:00Z, Organic, 105]
      - [2026-01-01T00:00:00Z, Paid, 40]
      - [2026-02-01T00:00:00Z, Paid, 48]
charts:
  mix:
    query: channel_mix
    type: bar
    x: channel
    y: signups
    multiples:
      rows: month
rows:
  - mix
"""


def _faceted_datetime_live(tmp_path: Path) -> tuple[bytes, BoardRecording, str]:
    """Same shape as `_live`, but the partition column is a tz-aware `datetime`.

    The YAML parses `2026-01-01T00:00:00Z` (unquoted) as a UTC-aware
    `datetime.datetime`, which round-trips through
    `BoardRecording.model_dump_json()` as `"...T00:00:00Z"` and reads back as
    a plain string on replay.
    """
    result = compile_board(_FACETED_DATETIME_YAML)
    assert result.success, result.errors
    assert result.board is not None

    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict[str, object] = {}
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    background = resolved.style.background
    live_svg = render_board_svg(
        resolved,
        executor,
        variables,
        background=None if background == "transparent" else background,
        render_cache=render_cache,
    )
    recording = record_board(resolved, executor, variables)
    return dump_board_artifact(resolved), recording, live_svg


def test_render_board_from_artifact_replays_a_faceted_datetime_partition(
    tmp_path: Path,
) -> None:
    """Regression: `canonical_key` must be stable across the JSON round-trip.

    A UTC-aware `datetime` renders as `"...+00:00"` under `isoformat()` but
    `BoardRecording.model_dump_json()` (`Any`-typed `rows_by_query`) emits
    `"...Z"`. Before the fix, `regroup()` on replay raises
    `ERR-MULTIPLES-ROW-OUTSIDE-PANEL-AXES` on every row because the two forms
    disagree, and the chart renders as an error card instead of two panels.

    Not a byte-for-byte comparison against `live_svg` like the sibling tests
    below: a `datetime` value survives the JSON round-trip only as a plain
    string (`Any`-typed on `BoardRecording.rows_by_query`), so the replayed
    panel titles are the recorded ISO string ("...T00:00:00Z") rather than
    `str()` of the original `datetime` — a display-fidelity gap that is a
    pre-existing, orthogonal property of replaying any raw `datetime` value
    (independent of faceting) and out of scope here; this test instead pins
    the actual contract: replay produces both panels with every row intact,
    not an error card.
    """
    artifact_bytes, recording, live_svg = _faceted_datetime_live(tmp_path)
    reloaded_recording = load_board_recording(recording.model_dump_json().encode())

    replay_svg = render_board_from_artifact(
        load_board_artifact(artifact_bytes),
        reloaded_recording,
        reloaded_recording.variables,
    )

    assert "ERR-MULTIPLES" not in replay_svg
    live_bar_values = re.findall(r'aria-label="Signups: (\d+)', live_svg)
    replay_bar_values = re.findall(r'aria-label="Signups: (\d+)', replay_svg)
    assert replay_bar_values == live_bar_values
    assert replay_svg.count('role-title-text"') == live_svg.count('role-title-text"')
    assert replay_svg.count('role-title-text"') == 2


def test_render_board_from_artifact_reproduces_the_live_svg(tmp_path: Path) -> None:
    artifact_bytes, recording, live_svg = _live(tmp_path)

    reloaded = load_board_artifact(artifact_bytes)
    replay_svg = render_board_from_artifact(reloaded, recording, recording.variables)

    assert _normalize(replay_svg) == _normalize(live_svg)


_DT_YAML = """
title: Artifact Calibration Test
queries:
  q:
    columns: [month, revenue]
    values:
      - [Jan, 100.0]
      - [Feb, 200.0]
charts:
  dt_chart:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue
    style:
      orientation: vertical
    support_table:
      - source: revenue
rows:
  - dt_chart
"""


def test_render_board_from_artifact_calibrates_title_offset_for_support_table(
    tmp_path: Path,
) -> None:
    """render_board_from_artifact must apply title-offset calibration like dct render.

    Pre-fix: dct artifact render re-emitted the uncalibrated title.offset (= probe),
    making the gap = VL_baseline + probe instead of probe (the live gap).
    Post-fix: the calibration runs in the artifact render path too, so the title gap
    matches the live render (within 5px).
    """
    from dbt_charts.core.render.layout_sizing import _measure_vl_title_plot_gap

    result = compile_board(_DT_YAML)
    assert result.success and result.board is not None, result.errors

    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict[str, object] = {}
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    bg = resolved.style.background
    live_svg = render_board_svg(
        resolved,
        executor,
        variables,
        background=None if bg == "transparent" else bg,
        render_cache=render_cache,
    )
    recording = record_board(resolved, executor, variables)
    artifact_bytes = dump_board_artifact(resolved)
    reloaded = load_board_artifact(artifact_bytes)
    replay_svg = render_board_from_artifact(reloaded, recording, variables)

    def _chart_section(svg: str) -> str:
        m = re.search(r'data-authored-path="charts\.dt_chart"', svg)
        assert m is not None, "dt_chart not found in SVG"
        return svg[m.start() :]

    live_gap = _measure_vl_title_plot_gap(_chart_section(live_svg))
    replay_gap = _measure_vl_title_plot_gap(_chart_section(replay_svg))

    assert live_gap is not None, "title gap not found in live SVG"
    assert replay_gap is not None, "title gap not found in replay SVG"
    assert abs(live_gap - replay_gap) < 5.0, (
        f"live gap ({live_gap:.1f}px) and replay gap ({replay_gap:.1f}px) differ by "
        f"{abs(live_gap - replay_gap):.1f}px > 5px. "
        "dct artifact render must apply the same title-offset calibration as dct render."
    )


def test_render_board_from_artifact_stamps_the_recording_time(tmp_path: Path) -> None:
    artifact_bytes, recording, _live_svg = _live(tmp_path)
    reloaded = load_board_artifact(artifact_bytes)
    stale_recording = recording.model_copy(
        update={"recorded_at": datetime(2019, 3, 4, 5, 6, tzinfo=timezone.utc)}
    )

    replay_svg = render_board_from_artifact(
        reloaded, stale_recording, stale_recording.variables
    )

    assert "Data as of 05:06 UTC on 4 Mar 2019" in replay_svg


def test_render_board_from_artifact_raises_on_unrecorded_query(
    tmp_path: Path,
) -> None:
    artifact_bytes, recording, _live_svg = _live(tmp_path)
    reloaded = load_board_artifact(artifact_bytes)
    truncated_recording = recording.model_copy(update={"rows_by_query": {}})

    with pytest.raises(DbtChartsError) as exc_info:
        render_board_from_artifact(
            reloaded,
            truncated_recording,
            truncated_recording.variables,
        )

    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-BOARD-RECORDING-MISMATCH"


def test_render_board_from_artifact_raises_on_variable_mismatch(
    tmp_path: Path,
) -> None:
    artifact_bytes, recording, _live_svg = _live(tmp_path)
    reloaded = load_board_artifact(artifact_bytes)

    with pytest.raises(DbtChartsError) as exc_info:
        render_board_from_artifact(
            reloaded,
            recording,
            {"region": "West"},
        )

    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-BOARD-RECORDING-MISMATCH"


_PAGINATED_TABLE_YAML = """
title: Paginated Table Replay
queries:
  rows_q:
    columns: [name]
    values:
      - [row_1]
      - [row_2]
      - [row_3]
      - [row_4]
      - [row_5]
      - [row_6]
      - [row_7]
      - [row_8]
      - [row_9]
      - [row_10]
charts:
  t:
    query: rows_q
    type: table
    style:
      pagination:
        enabled: true
        page_rows: 5
rows:
  - t
"""


def _paginated_table_live(
    tmp_path: Path, variables: dict[str, object]
) -> tuple[bytes, BoardRecording, str]:
    result = compile_board(_PAGINATED_TABLE_YAML)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    background = resolved.style.background
    with interactive_controls(True):
        live_svg = render_board_svg(
            resolved,
            executor,
            variables,
            background=None if background == "transparent" else background,
            render_cache=render_cache,
        )
    return (
        dump_board_artifact(resolved),
        record_board(resolved, executor, variables),
        live_svg,
    )


def test_render_board_from_artifact_reproduces_a_paginated_table_page(
    tmp_path: Path,
) -> None:
    """A replay must land on the same page a live render shows.

    board_replay.py's render_board_from_artifact calls render_board_svg
    directly (not through renderer.render()), so the page variable must
    reach the table renderer via that call too -- not just the live render()
    entry point. Regression for the gap where render_board_svg had no
    board_variables() scope of its own and silently painted page 1
    regardless of what `variables` it was handed. interactive_controls(True)
    forces the interactive (not static_multi_page) path, so only the
    requested page's rows actually appear in the SVG.
    """
    artifact_bytes, recording, live_svg = _paginated_table_live(tmp_path, {"t_page": 2})

    # Guard against a false pass: both live and replay landing on the WRONG
    # page (1) would still satisfy string equality below.
    assert "row_6" in live_svg
    assert not re.search(r"\brow_1\b", live_svg)

    reloaded = load_board_artifact(artifact_bytes)
    with interactive_controls(True):
        replay_svg = render_board_from_artifact(
            reloaded, recording, recording.variables
        )

    assert "row_6" in replay_svg
    assert not re.search(r"\brow_1\b", replay_svg)
    assert _normalize(replay_svg) == _normalize(live_svg)


def test_render_board_from_artifact_dedupes_repeated_callout_css() -> None:
    """render_board_from_artifact goes through the same render_board_svg the
    live path uses, so two same-tone callouts dedupe there too -- with no
    board_replay-specific code, since deduplication is a property of
    render_board_svg's own post-pass over the fully assembled SVG, not of
    anything this module does. A board with no queries needs no adapter
    registry or real Executor."""
    yaml_src = """
title: Callout Replay Dedup
charts:
  c1:
    type: callout
    message: First message
  c2:
    type: callout
    message: Second message
cols: [c1, c2]
"""
    result = compile_board(yaml_src)
    assert result.success, result.errors
    assert result.board is not None

    executor = MagicMock(spec=Executor)
    executor.execute_chart.return_value = []
    executor.cache_hit_ats = []
    variables: dict[str, object] = {}
    resolved, _render_cache = build_resolved_board(result.board, executor, variables)
    recording = record_board(resolved, executor, variables)

    reloaded = load_board_artifact(dump_board_artifact(resolved))
    replay_svg = render_board_from_artifact(reloaded, recording, recording.variables)

    assert "First message" in replay_svg
    assert "Second message" in replay_svg
    charts_svg = replay_svg.split('id="chart-c1"', 1)[1]
    assert charts_svg.count("<style>") == 1


_NESTED_BACKGROUND_YAML = """
title: Root
style:
  background: "rgba(255, 0, 0, 0.5)"
rows:
  - title: Child
    text: hello
"""


def test_artifact_replay_does_not_double_paint_an_unstyled_nested_boards_tint(
    tmp_path: Path,
) -> None:
    """An unstyled nested board's own fill must be decided once, at compile
    time -- not inferred at render time by comparing a board's resolved
    style against its live parent's object. dump_board_artifact/
    load_board_artifact always rebuild a fresh ResolvedStyle per board (no
    two boards share an object across the round trip), so any render-side
    signal keyed on object identity sees every reloaded board as "authored
    its own fill" and repaints the parent's tint on top of it."""
    result = compile_board(_NESTED_BACKGROUND_YAML)
    assert result.success, result.errors
    assert result.board is not None

    executor = Executor(
        result.board,
        build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    variables: dict[str, object] = {}
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    background = resolved.style.background
    live_svg = render_board_svg(
        resolved,
        executor,
        variables,
        background=None if background == "transparent" else background,
        render_cache=render_cache,
    )
    recording = record_board(resolved, executor, variables)
    artifact_bytes = dump_board_artifact(resolved)

    reloaded = load_board_artifact(artifact_bytes)
    replay_svg = render_board_from_artifact(reloaded, recording, variables)

    # The root paints its own tint twice legitimately (the page rect and its
    # layout's own "under items" card, both the root's own authored value) --
    # what must not happen is the reload adding a THIRD, from the unstyled
    # nested board repainting a tint that isn't its own.
    tint = 'fill="rgba(255, 0, 0, 0.5)"'
    assert live_svg.count(tint) == 2
    assert replay_svg.count(tint) == live_svg.count(tint)
