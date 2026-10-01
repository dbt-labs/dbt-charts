"""Tests for render-warning suppression: partition helper + three ignore layers.

TDD: failing tests written before implementation.

Suppression layers (union semantics — a warning is dropped if it appears in ANY layer):
  1. CLI / caller: `ignore_codes` parameter on render() / render_dashboard()
  2. Project: `warnings: { ignore: [CODE] }` in dbt_charts.yml
  3. Per-chart: `warnings_ignore: [CODE]` on a chart object in board yaml

Per-chart suppression only drops warnings whose `chart` field matches that
chart id.  Board-level warnings (chart: None) are NOT suppressed by per-chart
ignore lists.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_project_warnings_ignore,
)
from dbt_charts.core.diagnostics import (
    WARN_FANOUT_RISK,
    WARN_REDUNDANT_ENCODING,
    Diagnostic,
)
from dbt_charts.core.diagnostics.registry import REGISTRY
from dbt_charts.core.diagnostics.suppression import partition
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render
from dbt_charts.core.render.warnings import registry as _registry

# ---------------------------------------------------------------------------
# partition() unit tests
# ---------------------------------------------------------------------------


def test_partition_empty_inputs() -> None:
    """No warnings, no codes → both lists empty."""
    active, suppressed = partition([], set(), set(), {})
    assert active == []
    assert suppressed == []


def test_partition_cli_suppresses_by_code() -> None:
    w = Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="m", chart="c1")
    active, suppressed = partition(
        [w],
        cli_codes={WARN_REDUNDANT_ENCODING.code},
        project_codes=set(),
        per_chart_codes={},
    )
    assert active == []
    assert suppressed == [w]


def test_partition_project_suppresses_by_code() -> None:
    w = Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="m", chart="c1")
    active, suppressed = partition(
        [w],
        cli_codes=set(),
        project_codes={WARN_REDUNDANT_ENCODING.code},
        per_chart_codes={},
    )
    assert active == []
    assert suppressed == [w]


def test_partition_per_chart_suppresses_matching_chart() -> None:
    w = Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="m", chart="c1")
    active, suppressed = partition(
        [w],
        cli_codes=set(),
        project_codes=set(),
        per_chart_codes={"c1": {WARN_REDUNDANT_ENCODING.code}},
    )
    assert active == []
    assert suppressed == [w]


def test_partition_per_chart_does_not_suppress_different_chart() -> None:
    """Per-chart suppression on c2 must not suppress a warning tagged to c1."""
    w = Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="m", chart="c1")
    active, suppressed = partition(
        [w],
        cli_codes=set(),
        project_codes=set(),
        per_chart_codes={"c2": {WARN_REDUNDANT_ENCODING.code}},
    )
    assert active == [w]
    assert suppressed == []


def test_partition_per_chart_does_not_suppress_board_level_warning() -> None:
    """Per-chart suppression must NOT reach warnings with chart=None."""
    w = Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="m", chart=None)
    active, suppressed = partition(
        [w],
        cli_codes=set(),
        project_codes=set(),
        per_chart_codes={"c1": {WARN_REDUNDANT_ENCODING.code}},
    )
    assert active == [w]
    assert suppressed == []


def test_partition_union_project_alone_is_sufficient() -> None:
    """Even if per-chart ignore is empty, project ignore suppresses the warning."""
    w = Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="m", chart="c1")
    active, suppressed = partition(
        [w],
        cli_codes=set(),
        project_codes={WARN_REDUNDANT_ENCODING.code},
        per_chart_codes={"c1": set()},
    )
    assert active == []
    assert suppressed == [w]


def test_partition_multiple_warnings_mixed() -> None:
    """Only the warning whose code is in an ignore set is suppressed."""
    w_fake = Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="m1", chart="c1")
    w_other = Diagnostic.from_code(WARN_FANOUT_RISK, message="m2", chart="c1")
    active, suppressed = partition(
        [w_fake, w_other],
        cli_codes={WARN_REDUNDANT_ENCODING.code},
        project_codes=set(),
        per_chart_codes={},
    )
    assert active == [w_other]
    assert suppressed == [w_fake]


# ---------------------------------------------------------------------------
# validate_suppression_codes() unit tests — typo'd codes must not silently no-op
# ---------------------------------------------------------------------------


def test_validate_suppression_codes_accepts_registered_codes() -> None:
    from dbt_charts.core.diagnostics.suppression import validate_suppression_codes

    validate_suppression_codes(["WARN-FANOUT-RISK", "WARN-REAGGREGATION"], source="x")


def test_validate_suppression_codes_rejects_unknown_code() -> None:
    from dbt_charts.core.diagnostics.suppression import validate_suppression_codes

    with pytest.raises(ValueError, match="WARN-PIE-TOO-MANY-SEGMENT"):
        validate_suppression_codes(["WARN-PIE-TOO-MANY-SEGMENT"], source="chart 'c'")


def test_validate_suppression_codes_keeps_a_retired_code_loading_with_a_warning() -> (
    None
):
    """A code a release removed still loads, and says it no longer exists."""
    from dbt_charts.core.diagnostics.suppression import validate_suppression_codes

    with pytest.warns(UserWarning, match="WARN-ADJACENT-TEXT-ROWS.*no longer"):
        validate_suppression_codes(["WARN-ADJACENT-TEXT-ROWS"], source="dbt_charts.yml")


def test_validate_suppression_codes_error_names_the_source() -> None:
    from dbt_charts.core.diagnostics.suppression import validate_suppression_codes

    with pytest.raises(ValueError, match="chart 'revenue'"):
        validate_suppression_codes(["NOT-A-CODE"], source="chart 'revenue'")


def test_validate_suppression_codes_rejects_error_level_code() -> None:
    """Suppressing an ERR-* code gets a distinguishable message, not 'unknown'."""
    from dbt_charts.core.diagnostics.suppression import validate_suppression_codes

    with pytest.raises(ValueError, match="error code, not a warning code"):
        validate_suppression_codes(["ERR-NO-LAYOUT"], source="x")


# ---------------------------------------------------------------------------
# The three suppression surfaces reject an unregistered code at compile time
# ---------------------------------------------------------------------------


def test_project_warnings_ignore_yaml_rejects_unregistered_code(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A typo'd code in dbt_charts.yml warnings.ignore fails loud, not silently."""
    (tmp_path / "dbt_charts.yml").write_text(
        "warnings:\n  ignore:\n    - WARN-PIE-TOO-MANY-SEGMENT\n"  # missing trailing S
    )
    with pytest.raises(ValueError, match="WARN-PIE-TOO-MANY-SEGMENT"):
        get_project_warnings_ignore(local_project(tmp_path))


def test_chart_warnings_ignore_rejects_unregistered_code() -> None:
    """A typo'd code in a chart's warnings_ignore: fails compile, not silently."""
    board_yaml = """
title: Test
charts:
  c:
    query: q
    type: bar
    x: category
    y: value
    warnings_ignore:
      - WARN-PIE-TOO-MANY-SEGMENT
queries:
  q:
    sql: SELECT 'a' AS category, 1 AS value
    source: test_source
rows:
  - c
"""
    result = compile(board_yaml)
    assert not result.success
    assert any("WARN-PIE-TOO-MANY-SEGMENT" in e.message for e in result.errors)


def test_query_ignore_rejects_unregistered_code() -> None:
    """A typo'd code in a query's ignore: fails compile, not silently."""
    board_yaml = """
title: Test
charts:
  c:
    query: q
    type: bar
    x: category
    y: value
queries:
  q:
    sql: SELECT 'a' AS category, 1 AS value
    source: test_source
    ignore:
      - WARN-PIE-TOO-MANY-SEGMENT
rows:
  - c
"""
    result = compile(board_yaml)
    assert not result.success
    assert any("WARN-PIE-TOO-MANY-SEGMENT" in e.message for e in result.errors)


def test_query_ignore_suppresses_fanout_risk() -> None:
    """queries.<name>.ignore: actually suppresses a query-domain diagnostic.

    This is the correct, already-working lever for FR-79's trap: chart-scoped
    `warnings_ignore` can never reach WARN-FANOUT-RISK/WARN-REAGGREGATION
    (they never carry a chart id), so this is where an author must suppress
    them instead."""
    from dbt_charts.core.compile.compiler import validate_compiled_queries

    board_yaml = """
title: Test
charts:
  c:
    query: q
    type: bar
    x: category
    y: value
queries:
  q:
    sql: >-
      SELECT o.id AS category, COUNT(*) AS value
      FROM orders o JOIN line_items li ON o.id = li.order_id
      GROUP BY o.id
    source: test_source
    ignore:
      - WARN-FANOUT-RISK
rows:
  - c
"""
    result = compile(board_yaml)
    assert result.success
    validate_compiled_queries(result)

    assert not any(w.code == "WARN-FANOUT-RISK" for w in result.warnings)
    assert any(w.code == "WARN-FANOUT-RISK" for w in result.suppressed_warnings)


# ---------------------------------------------------------------------------
# render() integration: ignore_codes parameter (CLI seam)
# ---------------------------------------------------------------------------

_BOARD_YAML = """
title: Test
charts:
  c:
    query: q
    type: bar
    x: category
    y: value
queries:
  q:
    sql: SELECT 'a' AS category, 1 AS value
    source: test_source
rows:
  - c
"""


def _make_executor(board, query_registry):  # type: ignore[no-untyped-def]
    from unittest.mock import Mock

    ok = Mock()
    ok.is_success = True
    ok.data = [{"category": "a", "value": 1}]
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


def _fake_detector(code: str, chart_id: str | None = "c") -> ModuleType:
    """Build a fake detector module that always emits one warning with the given
    (registered) code."""
    fake = ModuleType(f"fake_detector_{code}")

    def _detect(ctx):  # type: ignore[no-untyped-def]
        return [
            Diagnostic.from_code(
                REGISTRY.get(code), message="test warning", chart=chart_id
            )
        ]

    fake.detect = _detect  # type: ignore[attr-defined]
    return fake


def test_render_ignore_codes_suppresses_matching_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI-style: ignore_codes={code} moves the warning to suppressed_warnings."""
    result = compile(_BOARD_YAML)
    assert result.success
    assert result.board is not None
    executor = _make_executor(result.board, result.query_registry)

    monkeypatch.setattr(
        _registry, "DETECTORS", [_fake_detector(WARN_REDUNDANT_ENCODING.code)]
    )

    render_result = render(
        result.board,
        executor,
        format="json",
        ignore_codes={WARN_REDUNDANT_ENCODING.code},
    )
    assert render_result.warnings == []
    assert len(render_result.suppressed_warnings) == 1
    assert render_result.suppressed_warnings[0].code == WARN_REDUNDANT_ENCODING.code


def test_render_ignore_codes_does_not_suppress_unmatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A code not in ignore_codes stays in warnings."""
    result = compile(_BOARD_YAML)
    assert result.success
    assert result.board is not None
    executor = _make_executor(result.board, result.query_registry)

    monkeypatch.setattr(_registry, "DETECTORS", [_fake_detector(WARN_FANOUT_RISK.code)])

    render_result = render(
        result.board,
        executor,
        format="json",
        ignore_codes={WARN_REDUNDANT_ENCODING.code},
    )
    assert len(render_result.warnings) == 1
    assert render_result.warnings[0].code == WARN_FANOUT_RISK.code
    assert render_result.suppressed_warnings == []


def test_render_suppressed_warnings_empty_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ignore_codes the suppressed_warnings list is empty."""
    result = compile(_BOARD_YAML)
    assert result.success
    assert result.board is not None
    executor = _make_executor(result.board, result.query_registry)

    monkeypatch.setattr(
        _registry, "DETECTORS", [_fake_detector(WARN_REDUNDANT_ENCODING.code)]
    )

    render_result = render(result.board, executor, format="json")
    assert len(render_result.warnings) == 1
    assert render_result.suppressed_warnings == []


# ---------------------------------------------------------------------------
# Project-level ignore (dbt_charts.yml warnings.ignore)
# ---------------------------------------------------------------------------

_BOARD_YAML_NAMED_CHART = """
title: Test
charts:
  c:
    query: q
    type: bar
    x: category
    y: value
queries:
  q:
    sql: SELECT 'a' AS category, 1 AS value
    source: test_source
rows:
  - c
"""


def test_project_warnings_ignore_suppresses_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """warnings_ignore=frozenset({code}) suppresses matching render warnings."""
    result = compile(_BOARD_YAML_NAMED_CHART)
    assert result.success
    assert result.board is not None
    executor = _make_executor(result.board, result.query_registry)

    monkeypatch.setattr(
        _registry, "DETECTORS", [_fake_detector(WARN_REDUNDANT_ENCODING.code)]
    )

    render_result = render(
        result.board,
        executor,
        format="json",
        warnings_ignore=frozenset({WARN_REDUNDANT_ENCODING.code}),
    )
    assert render_result.warnings == []
    assert len(render_result.suppressed_warnings) == 1
    assert render_result.suppressed_warnings[0].code == WARN_REDUNDANT_ENCODING.code


def test_project_warnings_ignore_does_not_suppress_other_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A code not in warnings_ignore stays active."""
    result = compile(_BOARD_YAML_NAMED_CHART)
    assert result.success
    assert result.board is not None
    executor = _make_executor(result.board, result.query_registry)

    monkeypatch.setattr(
        _registry, "DETECTORS", [_fake_detector(WARN_REDUNDANT_ENCODING.code)]
    )

    render_result = render(
        result.board,
        executor,
        format="json",
        warnings_ignore=frozenset({WARN_FANOUT_RISK.code}),
    )
    assert len(render_result.warnings) == 1
    assert render_result.suppressed_warnings == []


def test_get_project_warnings_ignore_reads_fresh_per_call(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Edits to dbt_charts.yml must be visible on the next call.

    get_project_warnings_ignore no longer caches; each call re-reads disk.
    """
    dbt_charts_yml = tmp_path / "dbt_charts.yml"
    dbt_charts_yml.write_text("warnings:\n  ignore:\n    - WARN-FANOUT-RISK\n")

    first = get_project_warnings_ignore(local_project(tmp_path))
    assert "WARN-FANOUT-RISK" in first

    # Edit the file in place — no invalidation call.
    dbt_charts_yml.write_text("warnings:\n  ignore:\n    - WARN-REAGGREGATION\n")

    second = get_project_warnings_ignore(local_project(tmp_path))
    assert "WARN-REAGGREGATION" in second
    assert "WARN-FANOUT-RISK" not in second, (
        "get_project_warnings_ignore returned stale cached data; FR-005 requires fresh read"
    )


def test_get_project_warnings_ignore_requires_project() -> None:
    """project is required — no cwd fallback."""
    with pytest.raises(TypeError):
        get_project_warnings_ignore()  # type: ignore[call-arg]


def test_get_project_warnings_ignore_rejects_non_string_entries(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Mirror the chart-side Pydantic strict-str check: int/bool entries must raise.

    Without this, a `dbt_charts.yml` typo like `- True` (unquoted) silently becomes
    the string "True" and the user's ignore list quietly no-ops.
    """
    dbt_charts_yml = tmp_path / "dbt_charts.yml"
    dbt_charts_yml.write_text("warnings:\n  ignore:\n    - 42\n    - good_code\n")

    with pytest.raises(TypeError, match="warnings.ignore entries must be strings"):
        get_project_warnings_ignore(local_project(tmp_path))


# ---------------------------------------------------------------------------
# Per-chart ignore (warnings_ignore: [CODE] on chart in board yaml)
# ---------------------------------------------------------------------------

# These plumbing tests use WARN-REDUNDANT-ENCODING (a render-domain code) via a
# fake detector, not because that code is realistically chart-suppressible in
# production — they're proving the per-chart suppression mechanics work for any
# diagnostic that actually carries a chart id. A *query*-domain code (e.g.
# WARN-FANOUT-RISK) would be wrong here: `from_query_diagnostic` never sets a
# chart id (a query can back more than one chart), so chart-scoped
# `warnings_ignore` can never match it — see
# test_chart_warnings_ignore_rejects_query_domain_code below.
_BOARD_WITH_WARNINGS_IGNORE = """
title: Test
charts:
  c:
    query: q
    type: bar
    x: category
    y: value
    warnings_ignore:
      - WARN-REDUNDANT-ENCODING
queries:
  q:
    sql: SELECT 'a' AS category, 1 AS value
    source: test_source
rows:
  - c
"""

_BOARD_WITH_WARNINGS_IGNORE_OTHER_CHART = """
title: Test
charts:
  c:
    query: q
    type: bar
    x: category
    y: value
  c2:
    query: q
    type: bar
    x: category
    y: value
    warnings_ignore:
      - WARN-REDUNDANT-ENCODING
queries:
  q:
    sql: SELECT 'a' AS category, 1 AS value
    source: test_source
rows:
  - c
  - c2
"""


def test_per_chart_warnings_ignore_suppresses_own_chart_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """warnings_ignore on chart c suppresses a warning tagged to chart c."""
    result = compile(_BOARD_WITH_WARNINGS_IGNORE)
    assert result.success
    assert result.board is not None
    # Verify the field compiled through
    assert result.board.charts["c"].warnings_ignore == ["WARN-REDUNDANT-ENCODING"]

    executor = _make_executor(result.board, result.query_registry)

    monkeypatch.setattr(
        _registry,
        "DETECTORS",
        [_fake_detector("WARN-REDUNDANT-ENCODING", chart_id="c")],
    )

    render_result = render(result.board, executor, format="json")
    assert render_result.warnings == []
    assert len(render_result.suppressed_warnings) == 1
    assert render_result.suppressed_warnings[0].code == "WARN-REDUNDANT-ENCODING"


def test_per_chart_warnings_ignore_does_not_suppress_different_chart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """warnings_ignore on c2 must not suppress a warning tagged to c."""
    result = compile(_BOARD_WITH_WARNINGS_IGNORE_OTHER_CHART)
    assert result.success
    assert result.board is not None

    executor = _make_executor(result.board, result.query_registry)

    # Detector emits warning tagged to "c" (not c2 which has the ignore list)
    monkeypatch.setattr(
        _registry,
        "DETECTORS",
        [_fake_detector("WARN-REDUNDANT-ENCODING", chart_id="c")],
    )

    render_result = render(result.board, executor, format="json")
    assert len(render_result.warnings) == 1
    assert render_result.warnings[0].code == "WARN-REDUNDANT-ENCODING"
    assert render_result.suppressed_warnings == []


def test_per_chart_warnings_ignore_does_not_suppress_board_level_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-chart warnings_ignore must NOT suppress board-level warnings (chart=None)."""
    result = compile(_BOARD_WITH_WARNINGS_IGNORE)
    assert result.success
    assert result.board is not None

    executor = _make_executor(result.board, result.query_registry)

    # Detector emits board-level warning (chart=None)
    monkeypatch.setattr(
        _registry,
        "DETECTORS",
        [_fake_detector("WARN-REDUNDANT-ENCODING", chart_id=None)],
    )

    render_result = render(result.board, executor, format="json")
    assert len(render_result.warnings) == 1
    assert render_result.warnings[0].chart is None
    assert render_result.suppressed_warnings == []


def test_chart_warnings_ignore_rejects_query_domain_code() -> None:
    """A query-domain code (WARN-FANOUT-RISK) in a chart's warnings_ignore:
    fails compile with an actionable message, instead of silently no-oping.

    Query-lint diagnostics (`from_query_diagnostic`) never carry a chart id —
    a query can back more than one chart — so chart-scoped suppression can
    never match one of these codes; without this guard the author sees a
    clean compile and an unsuppressed warning (FR-79)."""
    board_yaml = """
title: Test
charts:
  c:
    query: q
    type: bar
    x: category
    y: value
    warnings_ignore:
      - WARN-FANOUT-RISK
queries:
  q:
    sql: SELECT 'a' AS category, 1 AS value
    source: test_source
rows:
  - c
"""
    result = compile(board_yaml)
    assert not result.success
    assert any(
        "WARN-FANOUT-RISK" in e.message and "queries.<name>.ignore" in e.message
        for e in result.errors
    ), [e.message for e in result.errors]


# ---------------------------------------------------------------------------
# BoardRenderResult.suppressed_warnings field
# ---------------------------------------------------------------------------


def test_rendered_dashboard_has_suppressed_warnings_field() -> None:
    """BoardRenderResult carries suppressed_warnings and it serializes."""
    from dbt_charts.core.board import BoardRenderResult

    warning = Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="m")
    dashboard = BoardRenderResult(
        status="ok",
        warnings=[],
        suppressed_warnings=[warning],
    )
    dumped = dashboard.model_dump()
    assert "suppressed_warnings" in dumped
    assert dumped["suppressed_warnings"] == [warning.model_dump()]


_INLINE_KPI_YAML = (
    "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
    "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
)


def test_render_dashboard_threads_suppressed_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """render_dashboard() with ignore_codes propagates suppressed_warnings."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.project import InMemoryBoard

    monkeypatch.setattr(
        _registry,
        "DETECTORS",
        [_fake_detector(WARN_REDUNDANT_ENCODING.code, chart_id="c")],
    )

    project = local_project(tmp_path)
    adapter_registry = build_adapter_registry(project, read_only=False)

    result = render_dashboard(
        board=InMemoryBoard(_INLINE_KPI_YAML, path=project.path("charts/_t.yml")),
        adapter_registry=adapter_registry,
        format="json",
        ignore_codes={WARN_REDUNDANT_ENCODING.code},
        project=project,
        result_cache=None,
    )
    assert result.status == "ok"
    assert result.warnings == []
    assert len(result.suppressed_warnings) == 1
    assert result.suppressed_warnings[0].code == WARN_REDUNDANT_ENCODING.code


def test_render_dashboard_honors_project_warnings_ignore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """project.warnings_ignore (dbt_charts.yml) is the sole source of project-level
    suppression — render_dashboard applies it with no override argument."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.project import InMemoryBoard

    (tmp_path / "dbt_charts.yml").write_text(
        "warnings:\n  ignore:\n    - WARN-FANOUT-RISK\n"
    )
    monkeypatch.setattr(
        _registry, "DETECTORS", [_fake_detector("WARN-FANOUT-RISK", chart_id="c")]
    )

    project = local_project(tmp_path)
    adapter_registry = build_adapter_registry(project, read_only=False)

    result = render_dashboard(
        board=InMemoryBoard(_INLINE_KPI_YAML, path=project.path("charts/_t.yml")),
        adapter_registry=adapter_registry,
        format="json",
        project=project,
        result_cache=None,
    )
    assert result.status == "ok"
    assert result.warnings == []
    assert len(result.suppressed_warnings) == 1
    assert result.suppressed_warnings[0].code == "WARN-FANOUT-RISK"
