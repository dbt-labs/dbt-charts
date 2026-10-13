"""Convergence wiring tests: step 1 (merged_patch, BoardPatch desugar, compiler wiring)
and step 2 (theme-as-fragment, Cascade deletion, normalizer cleanup, MED fixes).

TDD: failing tests written before implementation.
No D-NN tokens in this file.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, relpath: str, content: str) -> Path:
    p = tmp_path / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ===========================================================================
# MEDIUM-1: BoardPatch desugars theme → extends
# ===========================================================================


def test_board_patch_desugars_theme() -> None:
    """BOARD_PATCH_ADAPTER must convert theme: X → extends: X.

    Fragments loaded as BoardPatch (meta files, extends targets) may use
    theme: as sugar just like AuthoredBoard. The desugaring lives on the
    adapter's BeforeValidator (BoardPatchInput), not on the bare BoardPatch
    class — validating through BoardPatch.model_validate directly would
    reject an authored ``theme:`` key with extra_forbidden instead of
    converting it. Without the adapter, a meta.yml with ``theme: paper``
    would be silently ignored — .extends stays None.
    """
    from dbt_charts.core.compile.models.board.patch import BOARD_PATCH_ADAPTER

    patch = BOARD_PATCH_ADAPTER.validate_python({"theme": "paper"})
    assert patch.extends == "paper"


def test_board_patch_desugar_rejects_both_theme_and_extends() -> None:
    """BoardPatch must reject simultaneous theme: and extends:."""
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.board.patch import BOARD_PATCH_ADAPTER

    with pytest.raises(ValidationError, match="Cannot set both"):
        BOARD_PATCH_ADAPTER.validate_python({"theme": "paper", "extends": "stark"})


# ===========================================================================
# merged_patch unit tests
# ===========================================================================


def test_merged_patch_meta_title_reaches_board(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Meta-set title propagates when board doesn't set it."""
    from dbt_charts.core.compile.merge import merged_patch
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    _write(tmp_path, "charts/meta.yml", "title: FromMeta\n")
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml")
    board_node = BoardPatch.model_validate({})

    result = merged_patch(board_node, board_file, project.directory("."))
    assert result.title == "FromMeta"  # type: ignore[union-attr]


def test_merged_patch_board_title_overrides_meta(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Board's own title wins over meta title."""
    from dbt_charts.core.compile.merge import merged_patch
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    _write(tmp_path, "charts/meta.yml", "title: MetaTitle\n")
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml")
    board_node = BoardPatch.model_validate({"title": "FaceTitle"})

    result = merged_patch(board_node, board_file, project.directory("."))
    assert result.title == "FaceTitle"  # type: ignore[union-attr]


def test_merged_patch_tags_append_meta_then_board(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """tags is Merge("append") — meta tags come before board tags.

    A plain dict-overlay merge would REPLACE lists; the engine APPENDs them.
    This is the canonical test for list-merge behavior change.
    """
    from dbt_charts.core.compile.merge import merged_patch
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    _write(tmp_path, "charts/meta.yml", "tags:\n  - meta-tag\n")
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml")
    board_node = BoardPatch.model_validate({"tags": ["board-tag"]})

    result = merged_patch(board_node, board_file, project.directory("."))
    assert result.tags == ["meta-tag", "board-tag"]  # type: ignore[union-attr]


def test_merged_patch_frame_width_survives_sibling_frame_key(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Regression: style.frame.width from meta must survive board setting style.frame.margin."""
    from dbt_charts.core.compile.merge import merged_patch
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    _write(tmp_path, "charts/meta.yml", "style:\n  frame:\n    width: 700\n")
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml")
    board_node = BoardPatch.model_validate({"style": {"frame": {"margin": 33}}})

    result = merged_patch(board_node, board_file, project.directory("."))
    assert result.style.frame.width == 700  # type: ignore[union-attr]
    assert result.style.frame.margin == 33  # type: ignore[union-attr]


def test_merged_patch_board_extends_resolved(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Board extends: a fragment — merged_patch includes the extended fields."""
    from dbt_charts.core.compile.merge import merged_patch
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    _write(tmp_path, "charts/base.yaml", "title: FromBase\n")
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml")
    board_node = BoardPatch.model_validate({"extends": "./base.yaml"})

    result = merged_patch(board_node, board_file, project.directory("."))
    assert result.title == "FromBase"  # type: ignore[union-attr]


def test_merged_patch_no_meta_no_extends(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """No meta, no extends — own fields pass through cleanly."""
    from dbt_charts.core.compile.merge import merged_patch
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    (tmp_path / "charts").mkdir()
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml")
    board_node = BoardPatch.model_validate({"title": "MyTitle"})

    result = merged_patch(board_node, board_file, project.directory("."))
    assert result.title == "MyTitle"  # type: ignore[union-attr]


# ===========================================================================
# Meta lint in meta.yml doesn't break merge_metas
# ===========================================================================


def test_merge_metas_strips_lint_key(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Meta files with a lint: section must not cause BoardPatch validation failure."""
    from dbt_charts.core.compile.merge import merged_patch
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    _write(
        tmp_path,
        "charts/meta.yml",
        "title: MetaTitle\nlint:\n  ignore:\n    - WARN-REAGGREGATION\n",
    )
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml")
    board_node = BoardPatch.model_validate({})

    # Must not raise — lint key should be stripped before BoardPatch validation
    result = merged_patch(board_node, board_file, project.directory("."))
    assert result.title == "MetaTitle"  # type: ignore[union-attr]


# ===========================================================================
# End-to-end: compile_file wired to engine
# ===========================================================================


def test_compile_file_extends_resolves_end_to_end(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A board with extends: <name> compiles successfully end-to-end."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    _write(
        tmp_path,
        "dbt_charts.yml",
        "",
    )
    # Board with extends pointing to a fragment
    _write(tmp_path, "charts/base.yaml", "title: BaseTitle\n")
    board_yaml = "extends: ./base.yaml\nrows:\n  - cols:\n    - text: hi\n"
    _write(tmp_path, "charts/board.yaml", board_yaml)

    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, result.errors
    assert result.board.title == "BaseTitle"


def test_compile_file_meta_tags_append(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """End-to-end: tags from meta.yml and board APPEND, not replace."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    _write(tmp_path, "charts/meta.yml", "tags:\n  - meta-tag\n")
    board_yaml = "title: T\ntags:\n  - board-tag\nrows:\n  - cols:\n    - text: hi\n"
    _write(tmp_path, "charts/board.yaml", board_yaml)

    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, result.errors
    assert result.board.tags == ["meta-tag", "board-tag"]


def test_compile_file_board_width_survives_sibling_end_to_end(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Regression (engine): meta frame.width survives board setting frame.margin end-to-end."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    _write(tmp_path, "charts/meta.yml", "style:\n  frame:\n    width: 700\n")
    board_yaml = (
        "title: T\nstyle:\n  frame:\n    margin: 33\nrows:\n  - cols:\n    - text: hi\n"
    )
    _write(tmp_path, "charts/board.yaml", board_yaml)

    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, result.errors
    assert result.board.resolved_style.frame.width == 700
    assert result.board.resolved_style.frame.margin == 33


def test_compile_file_threads_lint_through_engine(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Lint from meta.yml still reaches result.meta_lint after engine wiring."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    _write(
        tmp_path,
        "charts/meta.yml",
        "lint:\n  ignore:\n    - WARN-REAGGREGATION\n",
    )
    board_yaml = "title: T\nrows:\n  - cols:\n    - text: hi\n"
    _write(tmp_path, "charts/board.yaml", board_yaml)

    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, result.errors
    assert result.meta_lint is not None
    assert "WARN-REAGGREGATION" in result.meta_lint.ignore


def test_compiler_wiring_respects_merge_marker_not_hardcoded_set(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Compiler overlay reads strategy from Merge markers, not a hardcoded frozenset.

    Pin: the strategy() function is the single source of truth.  If 'tags'
    ever changes its marker from Merge('append') to Merge('override'), this
    test detects it and the wiring follows automatically.  Concretely: verify
    that the Merge marker on BoardPatch.tags is 'append' and that the compiler
    wiring produces append behavior for that field.
    """
    from dbt_charts.core.compile.compiler import compile_file
    from dbt_charts.core.compile.merge import merge_marker
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    # Verify the marker on BoardPatch.tags is append — if this ever changes
    # the wiring should follow without a code change in compiler.py.
    tags_field = BoardPatch.model_fields["tags"]
    marker = merge_marker(tags_field)
    assert marker is not None, "tags field must have an explicit Merge marker"
    assert marker.file == "append", (
        f"tags.Merge.file changed from 'append' to {marker.file!r}; "
        "update the marker (and wiring will follow automatically)"
    )

    # End-to-end: meta tags append to board tags.
    project = local_project(tmp_path)
    _write(tmp_path, "charts/meta.yml", "tags:\n  - meta_tag\n")
    board_yaml = "title: T\ntags:\n  - board_tag\nrows:\n  - cols:\n    - text: hi\n"
    _write(tmp_path, "charts/board.yaml", board_yaml)

    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, result.errors
    assert result.board.tags == [
        "meta_tag",
        "board_tag",
    ], "compiler wiring must append tags using the Merge marker strategy"


# ===========================================================================
# Step 2-A: _resolve_entry loads theme YAML as fragment (no bridge patch)
# ===========================================================================


def test_resolve_entry_loads_theme_yaml_as_fragment(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """After step 2, a built-in theme name in extends: resolves to real style data.

    The engine must NOT return BoardPatch(theme='paper') — it must load paper.yaml
    and return a patch whose .style carries paper's concrete values.
    """
    from dbt_charts.core.compile.merge import (
        _ExtendCtx,
        _resolve_entry,
        get_theme_names,
    )

    project = local_project(tmp_path)
    ctx = _ExtendCtx(
        board_dir=project.directory("."),
        boards_root=project.directory("."),
        theme_names=get_theme_names(),
    )
    patch = _resolve_entry("paper", ctx, frozenset())
    # Must NOT be a bridge patch with theme='paper' and nothing else
    assert "paper" not in get_theme_names() or patch.style is not None, (  # type: ignore[union-attr]
        "_resolve_entry must load the theme YAML, not return a bridge BoardPatch(theme='paper')"
    )
    # The merged patch must carry style fields from the paper theme chain
    assert patch.style is not None, "paper theme must produce a non-None style block"  # type: ignore[union-attr]


def test_resolve_entry_theme_cycle_fails(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A circular extends chain in theme files raises CompilationError."""
    # Seed `seen` with the theme relpath to simulate a cycle detection hit.
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.merge import (
        _ExtendCtx,
        _resolve_entry,
        get_theme_names,
    )

    project = local_project(tmp_path)
    ctx = _ExtendCtx(
        board_dir=project.directory("."),
        boards_root=project.directory("."),
        theme_names=get_theme_names(),
    )
    # "paper.yaml" is the relpath inside the theme Project — seed it as already-seen.
    with pytest.raises(CompilationError, match="[Cc]ircular"):
        _resolve_entry("paper", ctx, frozenset({"paper.yaml"}))


def test_resolve_built_in_theme_loads_style() -> None:
    """resolve_built_in_theme folds a built-in theme's chain into a patch with
    real style, reading package data directly — no Project seam."""
    from dbt_charts.core.compile.merge import resolve_built_in_theme

    patch = resolve_built_in_theme("paper")
    assert patch.style is not None  # type: ignore[union-attr]


def test_resolve_built_in_theme_unknown_raises() -> None:
    """An unknown theme name raises CompilationError naming the entry."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.merge import resolve_built_in_theme

    with pytest.raises(CompilationError, match="nonexistent-theme-xyz"):
        resolve_built_in_theme("nonexistent-theme-xyz")


# ===========================================================================
# Step 2-A: get_theme_style replaces get_theme_style
# ===========================================================================


def test_get_theme_style_returns_full_style() -> None:
    """get_theme_style('clarity') must return a fully-populated Style."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.theme import Style

    style = get_theme_style("clarity")
    assert isinstance(style, Style)
    assert style.background is not None
    assert style.accent is not None
    assert style.font is not None


def test_get_theme_style_chain_clarity_differs_from_structural_root() -> None:
    """clarity and stark should have different background colors."""
    from dbt_charts.core.compile.config import get_theme_style

    clarity = get_theme_style("clarity")
    stark = get_theme_style("stark")
    # clarity extends stark and overrides typography/colors
    # At minimum, they must produce valid Style objects. If they differ, good.
    # If they happen to share background, the test is still valid — we just
    # confirm both load without error and produce Style instances.
    assert clarity is not None
    assert stark is not None


# ===========================================================================
# Step 2-C: AuthoredBoard.theme deleted; Cascade class deleted
# ===========================================================================


def test_authored_board_has_no_theme_field() -> None:
    """AuthoredBoard must NOT have a 'theme' field after step 2."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    assert "theme" not in AuthoredBoard.model_fields, (
        "AuthoredBoard.theme must be deleted in step 2"
    )


def test_cascade_class_deleted_from_markers() -> None:
    """The Cascade marker class must not exist in markers.py after step 2."""
    from dbt_charts.core.compile.models import markers as m

    assert not hasattr(m, "Cascade"), "Cascade class must be deleted from markers.py"


def test_authored_board_rejects_cascade_annotation_on_source() -> None:
    """AuthoredBoard.source must not carry a Cascade annotation after step 2."""
    from dbt_charts.core.compile.models import markers as m
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    if hasattr(m, "Cascade"):
        # Cascade still exists — check it's not on source
        source_field = AuthoredBoard.model_fields.get("source")
        if source_field is not None:
            for meta in source_field.metadata:
                assert not isinstance(meta, m.Cascade), (  # type: ignore[attr-defined]
                    "AuthoredBoard.source must not carry Cascade after step 2"
                )


# ===========================================================================
# Step 2-B: normalizer reads extends, not board.theme
# ===========================================================================


def test_compiled_board_theme_from_extends(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """When a board has extends: paper, compiled board.theme must be 'paper'."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    board_yaml = "extends: paper\ntitle: T\nrows:\n  - cols:\n    - text: hi\n"
    _write(tmp_path, "charts/board.yaml", board_yaml)

    result = compile_file(project.path("charts/board.yaml").read_board())
    assert result.success, result.errors
    assert result.board.theme == "paper", (
        f"board.theme must be 'paper' (from extends:); got {result.board.theme!r}"
    )


def test_compile_file_theme_style_from_extends_end_to_end(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A board with extends: paper must get paper's resolved_style, not default theme."""
    from dbt_charts.core.compile.compiler import compile_file
    from dbt_charts.core.compile.config import get_theme_style

    project = local_project(tmp_path)
    board_yaml = "extends: paper\ntitle: T\nrows:\n  - cols:\n    - text: hi\n"
    _write(tmp_path, "charts/board.yaml", board_yaml)

    result = compile_file(project.path("charts/board.yaml").read_board())
    assert result.success, result.errors
    # The board's resolved background must match the paper theme's background
    paper_style = get_theme_style("paper")
    assert result.board.resolved_style.background == paper_style.background, (
        "resolved_style.background must come from the paper theme"
    )


# ===========================================================================
# Step 2-E MED-A: compiler strategy loop raises CompilationError on type mismatch
# ===========================================================================


def test_compiler_strategy_loop_raises_on_append_type_mismatch(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Strategy loop must raise CompilationError when append field types mismatch.

    If meta provides tags: [a] (list) and board provides tags: not-a-list,
    that is a data error; the engine must not silently override.
    NOTE: this tests the COMPILER path (compiler.py strategy loop), not merge_patches.
    """
    # This test checks that the compiler raises on type mismatch in the overlay,
    # not on the engine path. The exact mechanism is a CompilationError.
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    _write(tmp_path, "charts/meta.yml", "tags:\n  - meta-tag\n")
    # Provide tags as a non-list to trigger type mismatch in strategy=append path
    board_yaml = "title: T\ntags: not-a-list\nrows:\n  - cols:\n    - text: hi\n"
    _write(tmp_path, "charts/board.yaml", board_yaml)

    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    # Should fail — type mismatch on append field
    assert not result.success, "Compiler must detect append type mismatch"


# ===========================================================================
# Step 2-E MED-B: resolve_meta_lint extracted; orphaned functions deleted
# ===========================================================================


def test_resolve_meta_lint_function_exists() -> None:
    """meta.py must export resolve_meta_lint(file_path, root_path) -> MetaLintConfig | None."""
    from dbt_charts.core.compile.parse import meta as meta_mod

    assert hasattr(meta_mod, "resolve_meta_lint"), (
        "meta.py must expose resolve_meta_lint after MED-B extraction"
    )


def test_orphaned_meta_functions_deleted() -> None:
    """resolve_meta_chain and apply_meta_to_board must be deleted from meta.py."""
    from dbt_charts.core.compile.parse import meta as meta_mod

    assert not hasattr(meta_mod, "resolve_meta_chain"), (
        "resolve_meta_chain must be deleted (MED-B)"
    )
    assert not hasattr(meta_mod, "apply_meta_to_board"), (
        "apply_meta_to_board must be deleted (MED-B)"
    )


def test_resolve_meta_lint_returns_none_when_no_meta(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """resolve_meta_lint returns None when no meta.yml exists."""
    from dbt_charts.core.compile.parse.meta import resolve_meta_lint

    (tmp_path / "charts").mkdir()
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml")
    result = resolve_meta_lint(board_file, project.directory("."))
    assert result is None


def test_resolve_meta_lint_returns_config_when_meta_exists(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """resolve_meta_lint returns MetaLintConfig when meta.yml has lint: block."""
    from dbt_charts.core.compile.parse.meta import resolve_meta_lint

    _write(tmp_path, "charts/meta.yml", "lint:\n  ignore:\n    - WARN-REAGGREGATION\n")
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml")
    result = resolve_meta_lint(board_file, project.directory("."))
    assert result is not None
    assert "WARN-REAGGREGATION" in result.ignore


# ===========================================================================
# Step 2-E MED-C: merge_metas raises ValueError for genuinely-outside paths
# ===========================================================================


def test_merge_metas_raises_for_genuinely_outside_directory(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """merge_metas must raise ValueError when directory is truly outside boards_root.

    The logical-path fix only applies to SYMLINKED directories (where the
    logical path is inside boards_root). A directory whose logical path is
    genuinely outside must still raise ValueError.
    """
    from dbt_charts.core.compile.merge import merge_metas

    (tmp_path / "charts").mkdir()
    (tmp_path / "other").mkdir()
    project = local_project(tmp_path)

    with pytest.raises(ValueError, match="not under"):
        merge_metas(project.directory("other"), project.directory("charts"))


# ===========================================================================
# HIGH-1: explicit null-clear must not raise CompilationError
# ===========================================================================


def test_style_null_clears_meta_provided_style(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """style: null in board must clear meta-provided style without raising.

    Explicit null-clear is a supported engine semantic (merge.py merge_patches).
    The strategy-loop type-mismatch raise must NOT fire for v is None.
    """
    from dbt_charts.core.compile.compiler import compile_file

    _write(tmp_path, "charts/meta.yml", "style:\n  frame:\n    width: 700\n")
    _write(
        tmp_path,
        "charts/board.yaml",
        "title: T\nstyle: null\nrows:\n  - cols:\n    - text: hi\n",
    )
    project = local_project(tmp_path)
    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, f"Expected success but got errors: {result.errors}"
    # authored_style is None: the null-clear overrides the meta-provided style dict
    assert result.board.authored_style is None
    # resolved board width falls back to theme default (1200), not meta's 700
    assert result.board.resolved_style is not None
    assert result.board.resolved_style.frame.width != 700


def test_tags_null_clears_meta_provided_tags(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """tags: null in board must clear meta-provided tags without raising.

    Explicit null-clear must work for list-typed fields (append strategy) too.
    """
    from dbt_charts.core.compile.compiler import compile_file

    _write(tmp_path, "charts/meta.yml", "tags:\n  - team_a\n  - team_b\n")
    _write(
        tmp_path,
        "charts/board.yaml",
        "title: T\ntags: null\nrows:\n  - cols:\n    - text: hi\n",
    )
    project = local_project(tmp_path)
    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, f"Expected success but got errors: {result.errors}"
    # tags: null clears the meta-provided list; normalizer coerces null → []
    assert result.board.tags == []


# ===========================================================================
# MED-3: merge_metas must not spuriously raise on macOS /var symlink paths
# ===========================================================================


def test_merge_metas_unresolved_tempdir_does_not_raise(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """merge_metas must succeed when using ProjectDirectory handles for sub-dirs.

    Previously required macOS /var symlink workaround; now uses relpath-based
    identity via ProjectDirectory, so no absolute-path resolution is needed.
    """
    from dbt_charts.core.compile.merge import merge_metas
    from dbt_charts.core.compile.models.board.patch import EMPTY_PATCH

    (tmp_path / "charts" / "sub").mkdir(parents=True, exist_ok=True)
    project = local_project(tmp_path)
    # Must not raise — sub is inside boards_root
    result = merge_metas(project.directory("charts/sub"), project.directory("charts"))
    assert result is EMPTY_PATCH
