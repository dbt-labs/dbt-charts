"""Tests for ``compile_file()`` and ``render_dashboard()`` source threading.

``compile_file()`` reads sources from ``project.sources``; callers that pre-stuff
``project.__dict__["sources"]`` bypass the disk read. ``render_dashboard()`` takes
a ``project`` kwarg and routes its sources through the same cached_property, so the
as_link branch never re-reads disk when the caller supplied a pre-cached Project.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile, compile_file
from dbt_charts.core.compile.config import ProjectSourcesConfig

# source: warehouse matches the named registry the threading test supplies.
# Inline chart queries now validate their source against the project registry
# (same as up-top queries), so a source the registry lacks fails compile.
_BOARD_YAML = (
    "source: warehouse\n"
    "charts:\n"
    "  rev:\n"
    "    type: bar\n"
    "    x: month\n"
    "    y: revenue\n"
    "    query:\n"
    "      sql: 'SELECT 1 AS month, 1 AS revenue'\n"
)


def test_compile_does_not_read_sources_when_project_sources_provided(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """compile_file must not call load_project_sources when project.sources is cached."""
    from dbt_charts.core.compile import config as config_module

    def boom(*args: object, **kwargs: object) -> ProjectSourcesConfig:
        raise AssertionError(
            "compile_file must not load sources when project.sources is pre-cached"
        )

    monkeypatch.setattr(config_module, "load_project_sources", boom)

    board_path = tmp_path / "board.yml"
    board_path.write_text(_BOARD_YAML)

    project = local_project(tmp_path)
    project.__dict__["sources"] = ProjectSourcesConfig(sources={})

    result = compile_file(project.path("board.yml").read_board())

    assert result is not None
    assert result.success, [str(e) for e in result.errors]


def test_compile_threads_named_sources_from_project_sources(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """compile_file threads sources from project.sources into the compiled board."""
    board_path = tmp_path / "board.yml"
    board_path.write_text(_BOARD_YAML)

    project = local_project(tmp_path)
    project.__dict__["sources"] = ProjectSourcesConfig(
        sources={
            "warehouse": {"type": "duckdb", "path": str(tmp_path / "wh.duckdb")},
        },
    )

    result = compile_file(project.path("board.yml").read_board())

    assert result.success, [str(e) for e in result.errors]
    assert result.board is not None
    assert "warehouse" in result.board.sources
    assert result.board.sources["warehouse"]["path"] == str(tmp_path / "wh.duckdb")


def test_render_dashboard_as_link_threads_project_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """as_link=True must use project.sources, not re-read disk."""
    from dbt_charts.core import board as dashboard_module
    from dbt_charts.core.compile import config as config_module

    board_path = tmp_path / "board.yml"
    board_path.write_text(_BOARD_YAML)

    def boom(*args: object, **kwargs: object) -> ProjectSourcesConfig:
        raise AssertionError(
            "render_dashboard(as_link=True) must use project.sources, not reload"
        )

    monkeypatch.setattr(config_module, "load_project_sources", boom)

    project = local_project(tmp_path)
    project.__dict__["sources"] = ProjectSourcesConfig(sources={})

    result = dashboard_module.render_dashboard(
        board=project.path("board.yml").read_board(),
        project=project,
        as_link=True,
        result_cache=None,
    )

    assert result.status == "ok", result.validation_errors


def test_compile_file_accepts_boardfile(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """compile_file must accept a BoardFile as its first argument."""
    board_path = tmp_path / "board.yml"
    board_path.write_text(_BOARD_YAML)

    project = local_project(tmp_path)
    result = compile_file(project.path("board.yml").read_board())

    assert result.success, [str(e) for e in result.errors]


def test_compile_error_stamps_relpath_not_absolute(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Compile errors carry the project-relative path — never a reconstructed
    absolute disk path (meaningless for non-disk backends)."""
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "bad.yml").write_text("charts:\n  rev:\n    type: nope\n")

    project = local_project(tmp_path)
    result = compile_file(project.path("charts/bad.yml").read_board())

    assert not result.success
    err = result.errors[0]
    assert err.range is not None
    assert err.range.file == "charts/bad.yml"


def test_compile_reserved_word_query_ref_returns_clean_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A {{ queries.X }} reference to a reserved-word query name must return a
    CompileResult(success=False), not raise uncaught.

    detect_query_dependencies() (core/compile/template/jinja.py) raises a bare
    CompilationError for this case via _validate_query_ref_name — JinjaError's
    *parent*, not a subclass — while compiler.py's STEP 5 only catches
    `except JinjaError`. The reserved-word check escapes uncaught through
    compile_file() and crashes every caller (dct query --validate/--describe,
    dct render, ...).
    """
    board_yaml = (
        "source: warehouse\n"
        "queries:\n"
        "  order:\n"
        "    sql: SELECT 1 AS one\n"
        "  bad:\n"
        "    sql: SELECT * FROM {{ queries.order }}\n"
        "charts:\n"
        "  c:\n"
        "    query: bad\n"
        "    type: kpi\n"
        "    value: one\n"
    )
    board_path = tmp_path / "board.yml"
    board_path.write_text(board_yaml)

    project = local_project(tmp_path)
    project.__dict__["sources"] = ProjectSourcesConfig(
        sources={"warehouse": {"type": "duckdb", "path": str(tmp_path / "wh.duckdb")}},
    )

    result = compile_file(project.path("board.yml").read_board())

    assert not result.success
    assert any("reserved word" in e.message for e in result.errors), result.errors


def test_compile_stamps_the_correct_list_item_not_the_first_match() -> None:
    """The old regex-based line finder walked the raw text key-by-key and had
    no notion of *which* list item a nested field error belonged to — it
    silently matched the first `col:` it saw (item 0's, line 11) even when
    the error was on item 1 (line 13). A real source map, built from the
    parsed YAML node tree, resolves the exact sequence position instead of a
    plausible-looking wrong one."""
    yaml_content = (
        "charts:\n"
        "  c1:\n"
        "    type: bar\n"
        "    x: month\n"
        "    y: revenue\n"
        "    query:\n"
        "      sql: 'select 1 as month, 1 as revenue'\n"
        "grid:\n"
        "  items:\n"
        "    - item: c1\n"
        "      col: 0\n"
        "    - item: c1\n"
        "      col: not-a-number\n"
    )
    result = compile(yaml_content, file="board.yaml")

    assert not result.success
    err = result.errors[0]
    assert err.path == "grid.items.1.col"
    assert err.range is not None
    assert err.range.file == "board.yaml"
    assert err.range.start_line == 13


def test_compile_syntax_error_reports_the_actual_problem_line_not_the_context_line() -> (
    None
):
    """The old regex-based line extractor (`_extract_line_from_yaml_error`)
    grabbed the FIRST "line N" mention in PyYAML's rendered error string —
    which for a multi-mark error (context_mark printed before problem_mark) is
    where the construct *started*, not where the actual problem was found.
    Reading `.problem_mark` directly off the PyYAML exception gives the real
    position (line 4: the unterminated flow sequence's end), plus a column,
    which the regex never produced at all.
    """
    yaml_content = "title: Test\ncharts:\n  c1: [1, 2\n"

    result = compile(yaml_content, file="board.yaml")

    assert not result.success
    err = result.errors[0]
    assert err.range is not None
    assert err.range.file == "board.yaml"
    assert err.range.start_line == 4
    assert err.range.columns is not None
    assert err.range.columns.start_col == 1


def test_compile_file_requires_board_argument(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """compile_file must require its board argument — no default, no None fallback."""
    with pytest.raises(TypeError):
        compile_file()  # type: ignore[call-arg]


def test_compile_ref_free_board_succeeds_without_base_dir() -> None:
    """A ref-free board compiles with base_dir=None — no directory anchor needed.

    Programmatically-generated boards (registered views, ephemeral previews) have
    no cross-file refs, so they do not need a base_dir context. This pins the
    contract that the render_pipeline/dashboard call sites rely on after dropping
    their throwaway board.yml stub.
    """
    result = compile(_BOARD_YAML)

    assert result.success, [str(e) for e in result.errors]


def test_compile_cross_file_ref_without_base_dir_errors_clearly() -> None:
    """A cross-file ref with base_dir=None fails with a clear, actionable error.

    This is the guard that makes dropping base_dir safe on the ref-free
    registered-view path: if a ref is ever hit with no base context, compilation
    surfaces why rather than silently misresolving.
    """
    yaml_with_ref = (
        "title: Main\n"
        "queries:\n"
        "  imported: shared.queries.shared_query\n"
        "charts:\n"
        "  c:\n"
        "    query: imported\n"
        "    type: kpi\n"
        "    value: source\n"
        "rows:\n"
        "  - c\n"
    )

    result = compile(yaml_with_ref)

    assert not result.success
    assert any("base directory context" in str(e) for e in result.errors), [
        str(e) for e in result.errors
    ]


def test_render_dashboard_in_memory_board_resolves_boards_relative_ref(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """In-memory content anchored at charts/ resolves cross-file refs relative to it.

    A board being previewed lives logically in charts/ (the chat-save path and
    on-disk boards both base there), so a sibling/partial ref like
    `shared.queries.q` must resolve to charts/shared.yml — not the project root,
    and not error for lack of a base. The caller anchors the BoardFile's path
    under charts/ explicitly; render_dashboard resolves refs relative to
    `board.path.parent`, whatever that is.
    """
    from dbt_charts.core import board as dashboard_module
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.project import InMemoryBoard

    db_path = tmp_path / "wh.duckdb"
    import duckdb

    conn = duckdb.connect(str(db_path))
    conn.close()
    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  wh:\n    type: duckdb\n    path: '{db_path}'\n"
    )
    boards_dir = tmp_path / "charts"
    boards_dir.mkdir()
    (boards_dir / "shared.yml").write_text(
        "queries:\n  shared_q:\n    sql: SELECT 1 AS v\n    source: wh\n"
    )

    yaml_content = (
        "title: Preview\n"
        "queries:\n"
        "  imported: shared.queries.shared_q\n"
        "charts:\n"
        "  c:\n"
        "    query: imported\n"
        "    type: kpi\n"
        "    value: v\n"
        "rows:\n"
        "  - c\n"
    )

    project = local_project(tmp_path)
    adapter_registry = build_adapter_registry(
        project,
        read_only=False,
        allow_external_access_in_readonly=False,
        duckdb_config=None,
        profile_type="duckdb",
        target="dev",
    )

    result = dashboard_module.render_dashboard(
        board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
        project=project,
        adapter_registry=adapter_registry,
        format="json",
        result_cache=None,
    )

    assert result.status == "ok", result.validation_errors


class TestEmptyYamlDocumentDiagnostic:
    """An empty board file (e.g. dct init's comment-only meta.yml,
    fed to a verb that treats it as a board) must not surface as ERR-INTERNAL —
    that code signals a bug, not a well-understood input shape."""

    _COMMENT_ONLY = "# just a comment, nothing else\n"

    def test_compile_reports_its_own_code_not_internal(self) -> None:
        result = compile(self._COMMENT_ONLY, file="charts/meta.yml")

        assert not result.success
        assert len(result.errors) == 1
        assert result.errors[0].code == "ERR-EMPTY-YAML-DOCUMENT"

    def test_compile_names_the_file_in_the_message(self) -> None:
        result = compile(self._COMMENT_ONLY, file="charts/meta.yml")

        assert "charts/meta.yml" in result.errors[0].message

    def test_compile_file_reports_its_own_code_not_internal(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        board_path = tmp_path / "charts" / "meta.yml"
        board_path.parent.mkdir(parents=True)
        board_path.write_text(self._COMMENT_ONLY)

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/meta.yml").read_board())

        assert not result.success
        error = result.errors[0]
        assert error.code == "ERR-EMPTY-YAML-DOCUMENT"
        assert "charts/meta.yml" in error.message

    def test_compile_without_a_file_still_gets_its_own_code(self) -> None:
        """No board identity available (e.g. raw stdin content) — still not
        ERR-INTERNAL, and the message still reads sensibly with no filename
        to name (regression: deleting the `file or "..."` fallback default
        still type-checks and silently renders "Empty YAML document: None.")."""
        result = compile(self._COMMENT_ONLY)

        assert result.errors[0].code == "ERR-EMPTY-YAML-DOCUMENT"
        assert (
            result.errors[0].message == "Empty YAML document: the given YAML content."
        )
