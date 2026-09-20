"""Board-artifact codec: style hoisting round-trips and fails loudly.

Every nested board serializes a byte-identical copy of the same ``ResolvedStyle``,
which is 69-88% of a board artifact. The codec hoists each occurrence into a
content-addressed table so it is stored once.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.board_artifact import (
    COMPILED_SCHEMA_ID,
    SCHEMA_ID,
    DanglingStyleRefError,
    dump_compiled_board_artifact,
    dump_resolved_board_artifact,
    hoist_styles,
    inline_styles,
    load_compiled_board_artifact,
    load_resolved_board_artifact,
)
from dbt_charts.core.compile.compiler import compile as compile_board, compile_file
from dbt_charts.core.compile.models.board.resolved import (
    ChartResolveFailure,
    ResolvedBoard,
)
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_UNKNOWN_QUERY,
    WARN_HTML_POLICY_CAPPED,
)
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.board_resolve import (
    build_resolved_board,
    build_resolved_board_static,
)

_ADAPTER: TypeAdapter[ResolvedBoard] = TypeAdapter(ResolvedBoard)

# Nested boards come from nested layout groups (`cols:` inside `rows:`), not from
# an authored `board:` key. Two levels deep: a single-level board would pass a
# broken hoist, since there would be nothing to deduplicate.
_NESTED_YAML = """
title: Outer Board
queries:
  q:
    columns: [a, b]
    values:
      - ["x", 1]
      - ["y", 2]
charts:
  outer:
    query: q
    type: bar
    x: a
    y: b
  middle:
    query: q
    type: line
    x: a
    y: b
  inner:
    query: q
    type: area
    x: a
    y: b
rows:
  - outer
  - cols:
      - middle
      - rows:
          - inner
"""


def _resolved_board(yaml_text: str) -> ResolvedBoard:
    result = compile_board(yaml_text)
    assert result.success and result.board is not None, result.errors
    return build_resolved_board_static(result.board)


def _board_json(yaml_text: str) -> dict[str, Any]:
    return json.loads(_ADAPTER.dump_json(_resolved_board(yaml_text)))


def _count_boards(board: dict[str, Any]) -> int:
    total = 1
    for item in (board.get("layout") or {}).get("items") or []:
        if item.get("board"):
            total += _count_boards(item["board"])
    return total


class TestHoistRoundTrip:
    def test_nested_board_reconstructs_an_equal_board(self) -> None:
        original = _board_json(_NESTED_YAML)
        board, styles = hoist_styles(original)
        assert inline_styles(board, styles) == original

    def test_reconstructed_board_revalidates_as_a_resolved_board(self) -> None:
        original = _board_json(_NESTED_YAML)
        board, styles = hoist_styles(original)
        restored = _ADAPTER.validate_python(inline_styles(board, styles))
        assert restored == _ADAPTER.validate_python(original)

    def test_every_board_in_the_tree_is_hoisted(self) -> None:
        original = _board_json(_NESTED_YAML)
        assert _count_boards(original) >= 3
        board, _ = hoist_styles(original)

        def assert_ref_only(board: dict[str, Any]) -> None:
            assert set(board["style"]) == {"$style_ref"}
            for item in (board.get("layout") or {}).get("items") or []:
                if item.get("board"):
                    assert_ref_only(item["board"])

        assert_ref_only(board)

    def test_identical_styles_collapse_to_one_table_entry(self) -> None:
        original = _board_json(_NESTED_YAML)
        _, styles = hoist_styles(original)
        # The corpus resolves one style for the whole tree; the point of the
        # table is that N copies become 1 entry, not that N entries appear.
        assert len(styles) == 1

    def test_hoisting_shrinks_the_payload(self) -> None:
        original = _board_json(_NESTED_YAML)
        board, styles = hoist_styles(original)
        before = len(json.dumps(original, separators=(",", ":")))
        after = len(
            json.dumps({"board": board, "styles": styles}, separators=(",", ":"))
        )
        assert after < before


class TestDistinctStyles:
    """The case a "just store one style" shortcut would silently corrupt."""

    def test_two_distinct_styles_produce_two_entries_and_round_trip(self) -> None:
        original = _board_json(_NESTED_YAML)
        nested = next(
            item["board"] for item in original["layout"]["items"] if item.get("board")
        )
        # Force a genuinely different resolved style on the nested board.
        nested["style"] = {**nested["style"], "palettes": {"sentinel": "#123456"}}

        board, styles = hoist_styles(original)
        assert len(styles) == 2
        assert inline_styles(board, styles) == original


class TestFailsLoudly:
    def test_dangling_style_ref_raises(self) -> None:
        original = _board_json(_NESTED_YAML)
        board, styles = hoist_styles(original)
        with pytest.raises(DanglingStyleRefError):
            inline_styles(board, {})
        assert styles  # the table was non-empty; the failure is the empty one

    def test_dangling_nested_style_ref_raises(self) -> None:
        original = _board_json(_NESTED_YAML)
        board, styles = hoist_styles(original)
        nested = next(
            item["board"] for item in board["layout"]["items"] if item.get("board")
        )
        nested["style"] = {"$style_ref": "0" * 64}
        with pytest.raises(DanglingStyleRefError):
            inline_styles(board, styles)


class TestStableKeys:
    def test_table_keys_are_stable_across_key_ordering(self) -> None:
        """Keys hash canonical JSON, so dict insertion order cannot change them."""
        original = _board_json(_NESTED_YAML)
        _, styles_a = hoist_styles(original)

        shuffled = {
            **original,
            "style": dict(reversed(list(original["style"].items()))),
        }
        _, styles_b = hoist_styles(shuffled)

        assert set(styles_a) == set(styles_b)


class TestEnvelope:
    """The publishable artifact: {$id, version, styles, board}."""

    def test_round_trip_reconstructs_an_equal_resolved_board(self) -> None:
        original = _resolved_board(_NESTED_YAML)

        artifact = dump_resolved_board_artifact(original)
        assert set(artifact) == {"$id", "version", "styles", "board"}
        assert artifact["$id"] == SCHEMA_ID
        assert isinstance(artifact["version"], str) and artifact["version"]

        restored = load_resolved_board_artifact(artifact)
        assert restored == original

    def test_envelope_is_smaller_than_the_bare_dump(self) -> None:
        original = _resolved_board(_NESTED_YAML)
        bare = len(_ADAPTER.dump_json(original))
        artifact = dump_resolved_board_artifact(original)
        wrapped = len(json.dumps(artifact, separators=(",", ":")))
        assert wrapped < bare

    def test_load_raises_on_dangling_style_ref(self) -> None:
        original = _resolved_board(_NESTED_YAML)
        artifact = dump_resolved_board_artifact(original)
        artifact["styles"] = {}
        with pytest.raises(DanglingStyleRefError):
            load_resolved_board_artifact(artifact)

    def test_round_trip_reconstructs_a_wide_measure_board(self) -> None:
        """A wide y: [a, b] chart's resolved model must dump and load
        back to an equal board."""
        original = _resolved_board("""
title: Wide Measure Board
queries:
  q:
    columns: [month, revenue, cost]
    values:
      - ["Jan", 10, 5]
      - ["Feb", 20, 8]
charts:
  wide:
    query: q
    type: bar
    x: month
    y: [revenue, cost]
""")

        artifact = dump_resolved_board_artifact(original)
        restored = load_resolved_board_artifact(artifact)
        assert restored == original


class TestResolveFailureRoundTrip:
    """A board carrying a failed chart must survive dump → load.

    ``dump_resolved_board_artifact`` serializes with ``warnings="error"``, so a
    ``Diagnostic.fields`` value that is not JSON-plain raises at dump time, not
    at schema-check time — a failure mode the schema drift test cannot see.
    """

    _BROKEN_PIE_YAML = """
title: Board With A Broken Pie
queries:
  q_good:
    columns: [value]
    values:
      - [42]
  q_pie:
    columns: [label, amount]
    values:
      - ["a", null]
      - ["b", 3]
charts:
  good:
    query: q_good
    type: kpi
    value: value
  broken:
    query: q_pie
    type: pie
    theta: amount
    color: label
cols:
  - good
  - broken
"""

    def _resolved_with_failure(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> tuple[ResolvedBoard, dict[str, ChartResolveFailure]]:
        (tmp_path / "board.yml").write_text(self._BROKEN_PIE_YAML)
        project = local_project(tmp_path)
        result = compile_file(project.path("board.yml").read_board())
        assert result.success, result.errors
        assert result.board is not None

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(project),
            query_registry=result.query_registry,
        )
        failures: dict[str, ChartResolveFailure] = {}
        resolved, _cache = build_resolved_board(
            result.board, executor, {}, resolve_errors=failures
        )
        assert set(failures) == {"broken"}
        return resolved, failures

    def test_round_trip_preserves_the_failed_chart(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        original, _failures = self._resolved_with_failure(tmp_path, local_project)

        restored = load_resolved_board_artifact(dump_resolved_board_artifact(original))

        assert restored == original
        broken = next(
            item for item in restored.layout.items if item.chart_error is not None
        )
        assert broken.chart is None
        assert broken.chart_error is not None
        assert broken.chart_error.identity.id == "broken"
        assert broken.chart_error.diagnostic.code == "ERR-PIE-NULL-THETA"


# ============================================================================
# resolved_stops in board artifact round-trip
# ============================================================================

_HEATMAP_NAMED_PALETTE_YAML = """
title: Heatmap palette round-trip
queries:
  q:
    columns: [day, hour, count]
    values:
      - [Mon, 9am, 5]
      - [Tue, 10am, 8]
charts:
  h:
    type: heatmap
    query: q
    x: day
    y: hour
    color: count
    style:
      color:
        gradient:
          palette: dbt-seq-blue
rows:
  - h
"""


def test_resolved_stops_appears_in_board_artifact_json() -> None:
    """resolved_stops must be serialized into the dump_board_artifact JSON, not
    stripped by exclude_none or lost via the pydantic union type declaration.
    Regression: a ResolvedNamedPaletteScaleTargetConfig stored in a
    ResolvedScaleTarget | None field must serialize as the runtime subtype, or
    the no-compile replay path reloads a plain ResolvedScaleTargetConfig with no
    resolved_stops."""
    from dbt_charts.core.render.board_replay import dump_board_artifact

    resolved = _resolved_board(_HEATMAP_NAMED_PALETTE_YAML)
    artifact_bytes = dump_board_artifact(resolved)
    artifact = json.loads(artifact_bytes)
    # resolved_stops must appear as a JSON key — assert on the raw JSON string
    # so a silently-missing field can't be hidden by object construction.
    artifact_str = json.dumps(artifact)
    assert "resolved_stops" in artifact_str, (
        "resolved_stops not found in board artifact JSON — "
        "pydantic union serialization did not emit the runtime subtype's field"
    )


def test_resolved_stops_survives_artifact_round_trip() -> None:
    """load_board_artifact must reload resolved_stops exactly — no precision loss,
    no type downgrade to the plain ResolvedScaleTargetConfig supertype."""
    from dbt_charts.core.compile.models.primitives import (
        ResolvedNamedPaletteScaleTargetConfig,
    )
    from dbt_charts.core.compile.resolve.style.palette import palette as resolve_palette
    from dbt_charts.core.render.board_replay import (
        dump_board_artifact,
        load_board_artifact,
    )

    resolved = _resolved_board(_HEATMAP_NAMED_PALETTE_YAML)
    artifact_bytes = dump_board_artifact(resolved)
    reloaded = load_board_artifact(artifact_bytes)

    color_scale = reloaded.charts["h"].resolved_channels["color"].scale
    assert isinstance(color_scale, ResolvedNamedPaletteScaleTargetConfig), (
        f"Expected ResolvedNamedPaletteScaleTargetConfig, got {type(color_scale)}"
    )
    assert color_scale.resolved_stops == tuple(resolve_palette("dbt-seq-blue"))


# ============================================================================
# Compiled board artifact (normalized Board — the compile()/compile_file()
# output, not the resolved-at-render one above)
# ============================================================================


class TestCompiledEnvelope:
    def test_round_trip_reconstructs_an_equal_board(self) -> None:
        result = compile_board(_NESTED_YAML)
        assert result.success, result.errors

        artifact = dump_compiled_board_artifact(result)
        assert set(artifact) == {"$id", "version", "styles", "board", "diagnostics"}
        assert artifact["$id"] == COMPILED_SCHEMA_ID
        assert isinstance(artifact["version"], str) and artifact["version"]
        assert artifact["diagnostics"] == []

        restored = load_compiled_board_artifact(artifact)
        assert restored == result.board

    def test_style_table_dedups_across_nested_boards(self) -> None:
        result = compile_board(_NESTED_YAML)
        assert result.success, result.errors

        artifact = dump_compiled_board_artifact(result)
        # Every board in the tree shares one resolved_style and one
        # chart_style_context; both hoist into the same content-addressed
        # table, so this is 2 entries total, never N per nested board.
        assert len(artifact["styles"]) == 2

    def test_load_raises_on_dangling_style_ref(self) -> None:
        result = compile_board(_NESTED_YAML)
        assert result.success, result.errors
        artifact = dump_compiled_board_artifact(result)
        artifact["styles"] = {}
        with pytest.raises(DanglingStyleRefError):
            load_compiled_board_artifact(artifact)

    def test_diagnostics_carry_errors_then_warnings_in_order(self) -> None:
        result = compile_board(_NESTED_YAML)
        assert result.success, result.errors
        # Fabricated diagnostics — checking envelope ordering, not compiler
        # behavior, so there's no need for a YAML fixture that naturally
        # produces both an error and a warning.
        result.errors = [Diagnostic(code=ERR_UNKNOWN_QUERY.code, message="boom")]
        result.warnings = [
            Diagnostic(code=WARN_HTML_POLICY_CAPPED.code, message="capped")
        ]

        artifact = dump_compiled_board_artifact(result)
        assert [d["code"] for d in artifact["diagnostics"]] == [
            ERR_UNKNOWN_QUERY.code,
            WARN_HTML_POLICY_CAPPED.code,
        ]

    def test_raises_when_compile_failed(self) -> None:
        result = compile_board("charts: {bad: {type: bar, query: missing}}")
        assert not result.success
        with pytest.raises(ValueError, match="failed compile"):
            dump_compiled_board_artifact(result)
