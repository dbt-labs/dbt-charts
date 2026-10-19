"""Tests for the extends/metas resolution layer: merge_extends, merge_metas.

TDD: all tests written before implementation.
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


def _node(data: dict) -> object:
    """Return a BoardPatch node for merge_extends testing.

    merge_extends accepts any BaseModel with an ``extends`` field.
    BoardPatch is used here to avoid the AuthoredBoard layout-presence
    validator — extends fragments and metas are legitimately partial.
    """
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    return BoardPatch.model_validate(data)


def _write(tmp_path: Path, relpath: str, content: str) -> None:
    p = tmp_path / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _board_file(
    tmp_path: Path, relpath: str, local_project: Callable[..., FilesystemProject]
) -> object:
    return local_project(tmp_path).path(relpath)


def _directory(
    tmp_path: Path, relpath: str, local_project: Callable[..., FilesystemProject]
) -> object:
    return local_project(tmp_path).directory(relpath)


# ===========================================================================
# merge_extends — basic semantics
# ===========================================================================


def test_merge_extends_none_returns_empty(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """extends=None → all fields unset (identity element)."""
    from dbt_charts.core.compile.merge import merge_extends
    from dbt_charts.core.compile.models.board.patch import EMPTY_PATCH

    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({})
    result = merge_extends(node, board_file)
    assert result.model_fields_set == set()  # type: ignore[union-attr]
    assert result is EMPTY_PATCH


def test_merge_extends_theme_name_loads_style(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """extends: paper (built-in theme) → patch with style data from the theme YAML."""
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "paper"})
    result = merge_extends(node, board_file)
    # Theme name resolves to the actual paper YAML — style is set, not a theme bridge.
    assert "style" in result.model_fields_set  # type: ignore[union-attr]


def test_merge_extends_theme_name_never_checks_migration_currency(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Built-in theme YAML is package data we ship and keep current by
    construction — loading it must never invoke migration-currency
    detection at all, not even the BoardPatch-checked fast path."""
    from dbt_charts.core.compile import migrations
    from dbt_charts.core.compile.merge import merge_extends

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "built-in theme loading must not call prepare_board_mapping"
        )

    monkeypatch.setattr(migrations, "prepare_board_mapping", _boom)

    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "paper"})
    result = merge_extends(node, board_file)
    assert "style" in result.model_fields_set  # type: ignore[union-attr]


def test_merge_extends_user_fragment_checks_currency_against_boardpatch(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user-authored extends fragment is patch-shaped, not a full board —
    its migration-currency check must run against BoardPatch, not
    AuthoredBoard (which would reject any style-only fragment as
    non-current regardless of whether it's actually current schema)."""
    from dbt_charts.core.compile import migrations
    from dbt_charts.core.compile.merge import merge_extends
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    seen_models: list[type] = []
    real_prepare_board_mapping = migrations.prepare_board_mapping

    def _spy(mapping: object, *, model: type | None = None, **kwargs: object) -> object:
        seen_models.append(model)  # type: ignore[arg-type]
        return real_prepare_board_mapping(mapping, model=model, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(migrations, "prepare_board_mapping", _spy)

    _write(tmp_path, "board.yaml", "")
    _write(
        tmp_path,
        "_fragment.yaml",
        "style:\n  charts:\n    bar:\n      legend:\n        visible: true\n",
    )
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "./_fragment.yaml"})
    result = merge_extends(node, board_file)

    assert "style" in result.model_fields_set  # type: ignore[union-attr]
    assert seen_models == [BoardPatch]


def test_merge_extends_unknown_name_no_boards_root_raises(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Unknown extends entry with no boards_root → CompilationError."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "nonexistent_board"})
    with pytest.raises(CompilationError, match="nonexistent_board"):
        merge_extends(node, board_file)


def test_merge_extends_relative_path_title(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """extends: ./base.yaml loads fragment and its title is in the patch."""
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "base.yaml", "title: BaseTitle\n")
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "./base.yaml"})
    result = merge_extends(node, board_file)
    assert result.title == "BaseTitle"  # type: ignore[union-attr]


def test_merge_extends_list_folds_low_to_high(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """[base, override] — override's title replaces base's."""
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "base.yaml", "title: BaseTitle\n")
    _write(tmp_path, "override.yaml", "title: OverrideTitle\n")
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": ["./base.yaml", "./override.yaml"]})
    result = merge_extends(node, board_file)
    assert result.title == "OverrideTitle"  # type: ignore[union-attr]


def test_merge_extends_list_merges_disjoint_fields(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """[theme_fragment, title_fragment] — both fields end up in the patch."""
    from dbt_charts.core.compile.merge import merge_extends

    # theme: paper desugars to extends: paper → loads the paper theme YAML → style set.
    _write(tmp_path, "themed.yaml", "theme: paper\n")
    _write(tmp_path, "titled.yaml", "title: MyTitle\n")
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": ["./themed.yaml", "./titled.yaml"]})
    result = merge_extends(node, board_file)
    assert "style" in result.model_fields_set  # type: ignore[union-attr]
    assert result.title == "MyTitle"  # type: ignore[union-attr]


def test_merge_extends_recursive_chain(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A extends B extends paper theme: A's patch carries style from the theme YAML."""
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "base.yaml", "extends: paper\n")
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "./base.yaml"})
    result = merge_extends(node, board_file)
    # paper YAML loads its style; the patch carries style, not a theme bridge.
    assert "style" in result.model_fields_set  # type: ignore[union-attr]


def test_merge_extends_extends_not_forwarded_into_patch(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """The 'extends' field itself is consumed, never propagated to the patch."""
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "base.yaml", "extends: paper\ntitle: BaseTitle\n")
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "./base.yaml"})
    result = merge_extends(node, board_file)
    # 'extends' must not appear in model_fields_set of the returned patch
    assert "extends" not in result.model_fields_set  # type: ignore[union-attr]


def test_merge_extends_schema_version_not_forwarded_into_patch(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A base carrying _schema_version (dct migrate-written, identity-scoped
    to its own file) must not leak into a board that extends it -- same
    reasoning as id/aliases, same _EXTENDS_STRIP mechanism."""
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "base.yaml", '_schema_version: "0.5.0"\ntitle: BaseTitle\n')
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "./base.yaml"})
    result = merge_extends(node, board_file)
    assert "schema_version" not in result.model_fields_set  # type: ignore[union-attr]
    assert result.title == "BaseTitle"  # type: ignore[union-attr]


# ===========================================================================
# merge_extends — cycle detection
# ===========================================================================


def test_merge_extends_direct_cycle_raises(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A extends B, B extends A → CompilationError naming the cycle."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "a.yaml", "extends: ./b.yaml\n")
    _write(tmp_path, "b.yaml", "extends: ./a.yaml\n")
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "./a.yaml"})
    with pytest.raises(CompilationError, match="[Cc]ircular"):
        merge_extends(node, board_file)


def test_merge_extends_self_cycle_raises(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """File extends itself → CompilationError."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "a.yaml", "extends: ./a.yaml\n")
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "./a.yaml"})
    with pytest.raises(CompilationError, match="[Cc]ircular"):
        merge_extends(node, board_file)


# ===========================================================================
# merge_extends — cross-directory path anchoring
# ===========================================================================


def test_merge_extends_cross_dir_path_anchored_to_fragment(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Fragment in subdir/ has extends: ./inner.yaml → resolves from subdir/, not root."""
    from dbt_charts.core.compile.merge import merge_extends

    # subdir/fragment.yaml extends ./inner.yaml (= subdir/inner.yaml)
    _write(tmp_path, "subdir/inner.yaml", "title: InnerTitle\n")
    _write(tmp_path, "subdir/fragment.yaml", "extends: ./inner.yaml\n")
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "./subdir/fragment.yaml"})
    result = merge_extends(node, board_file)
    # title comes from subdir/inner.yaml, resolved relative to subdir/
    assert result.title == "InnerTitle"  # type: ignore[union-attr]


def test_merge_extends_cross_dir_does_not_resolve_from_root(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A ./inner.yaml next to root/board.yaml must NOT satisfy subdir/fragment's extends."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.merge import merge_extends

    # inner.yaml exists only at root, NOT in subdir/
    _write(tmp_path, "inner.yaml", "title: RootInner\n")
    # fragment extends ./inner.yaml — expects subdir/inner.yaml (missing)
    _write(tmp_path, "subdir/fragment.yaml", "extends: ./inner.yaml\n")
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    node = _node({"extends": "./subdir/fragment.yaml"})
    with pytest.raises(CompilationError, match="not found"):
        merge_extends(node, board_file)


# ===========================================================================
# merge_extends — named board lookup via boards_root
# ===========================================================================


def test_merge_extends_named_board_via_boards_root(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """extends: shared (no path sep, no .yaml) → resolved from boards_root."""
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "charts/shared.yaml", "title: SharedTitle\n")
    _write(tmp_path, "charts/my.yaml", "")
    board_file = _board_file(tmp_path, "charts/my.yaml", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    node = _node({"extends": "shared"})
    result = merge_extends(node, board_file, boards_root=boards_root)
    assert result.title == "SharedTitle"  # type: ignore[union-attr]


def test_merge_extends_unknown_named_board_raises(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Named board that doesn't exist in boards_root → CompilationError."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "charts/my.yaml", "")
    board_file = _board_file(tmp_path, "charts/my.yaml", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    node = _node({"extends": "nonexistent"})
    with pytest.raises(CompilationError, match="nonexistent"):
        merge_extends(node, board_file, boards_root=boards_root)


# ===========================================================================
# merge_metas — meta walk behavior (replaces get_nearest_meta tests)
# ===========================================================================


def test_merge_metas_empty_returns_empty_patch(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.core.compile.merge import merge_metas
    from dbt_charts.core.compile.models.board.patch import EMPTY_PATCH

    _write(tmp_path, "charts/sub/.keep", "")
    board_dir = _directory(tmp_path, "charts/sub", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    result = merge_metas(board_dir, boards_root)
    assert result.model_fields_set == set()  # type: ignore[union-attr]
    assert result is EMPTY_PATCH


def test_merge_metas_finds_meta_in_same_dir(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """meta.yml in the same directory as the board is collected."""
    from dbt_charts.core.compile.merge import merge_metas

    _write(tmp_path, "charts/sub/meta.yml", "title: SubMeta\n")
    board_dir = _directory(tmp_path, "charts/sub", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    result = merge_metas(board_dir, boards_root)
    assert result.title == "SubMeta"  # type: ignore[union-attr]


def test_merge_metas_finds_meta_in_parent(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """meta.yml in the parent directory is found when sub-dir has none."""
    from dbt_charts.core.compile.merge import merge_metas

    _write(tmp_path, "charts/meta.yml", "title: ParentMeta\n")
    _write(tmp_path, "charts/sub/.keep", "")
    board_dir = _directory(tmp_path, "charts/sub", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    result = merge_metas(board_dir, boards_root)
    assert result.title == "ParentMeta"  # type: ignore[union-attr]


def test_merge_metas_bounded_by_boards_root(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Meta above boards_root is not included."""
    _write(tmp_path, "meta.yml", "title: AboveRoot\n")
    _write(tmp_path, "charts/.keep", "")
    board_dir = _directory(tmp_path, "charts", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    from dbt_charts.core.compile.merge import merge_metas
    from dbt_charts.core.compile.models.board.patch import EMPTY_PATCH

    result = merge_metas(board_dir, boards_root)
    assert result is EMPTY_PATCH


def test_merge_metas_single_meta(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.core.compile.merge import merge_metas

    # theme: paper desugars to extends: paper → loads paper YAML → style set.
    _write(tmp_path, "charts/meta.yml", "theme: paper\n")
    board_dir = _directory(tmp_path, "charts", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    result = merge_metas(board_dir, boards_root)
    assert "style" in result.model_fields_set  # type: ignore[union-attr]


def test_merge_metas_root_to_leaf_chain(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Root meta sets theme via extends, leaf meta sets title — both in final patch."""
    from dbt_charts.core.compile.merge import merge_metas

    # theme: paper desugars to extends: paper → style set.
    _write(tmp_path, "charts/meta.yml", "theme: paper\n")
    _write(tmp_path, "charts/sub/meta.yml", "title: SubTitle\n")
    board_dir = _directory(tmp_path, "charts/sub", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    result = merge_metas(board_dir, boards_root)
    assert "style" in result.model_fields_set  # type: ignore[union-attr]
    assert result.title == "SubTitle"  # type: ignore[union-attr]


def test_merge_metas_leaf_overrides_root(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Leaf meta's title wins over root meta's title."""
    from dbt_charts.core.compile.merge import merge_metas

    _write(tmp_path, "charts/meta.yml", "title: RootTitle\n")
    _write(tmp_path, "charts/sub/meta.yml", "title: LeafTitle\n")
    board_dir = _directory(tmp_path, "charts/sub", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    result = merge_metas(board_dir, boards_root)
    assert result.title == "LeafTitle"  # type: ignore[union-attr]


def test_merge_metas_meta_with_extends(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Meta file's own extends chain is resolved before merging."""
    from dbt_charts.core.compile.merge import merge_metas

    # base_theme.yaml has theme: paper → desugars to extends: paper → style set.
    _write(tmp_path, "charts/base_theme.yaml", "theme: paper\n")
    _write(tmp_path, "charts/meta.yml", "extends: ./base_theme.yaml\n")
    board_dir = _directory(tmp_path, "charts", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    result = merge_metas(board_dir, boards_root)
    assert "style" in result.model_fields_set  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Diamond extends — seen forks per entry, not accumulated across siblings


def test_merge_extends_diamond_no_false_cycle(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A extends [B, C]; B and C both extend D — must not raise a cycle error.

    seen is threaded per-entry-resolution, not shared across siblings.
    D's contribution must survive into the final patch.
    """
    from dbt_charts.core.compile.merge import merge_extends

    _write(tmp_path, "charts/d.yaml", "title: FromD\n")
    _write(tmp_path, "charts/b.yaml", "extends: ./d.yaml\n")
    _write(tmp_path, "charts/c.yaml", "extends: ./d.yaml\n")
    _write(tmp_path, "charts/a.yaml", "extends:\n  - ./b.yaml\n  - ./c.yaml\n")
    _write(tmp_path, "charts/board.yaml", "")
    board_file = _board_file(tmp_path, "charts/board.yaml", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    node = _node({"extends": ["./b.yaml", "./c.yaml"]})
    result = merge_extends(node, board_file, boards_root=boards_root)
    assert result.title == "FromD"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Bounds checks — directory outside boards_root


def test_merge_metas_raises_if_outside_boards_root(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Directories whose LOGICAL path is outside boards_root raise ValueError.

    Symlinked directories whose logical path IS inside boards_root are handled
    by the caller (merged_patch passes board_file.parent, not a resolved path)
    and never reach this guard.  A directory whose logical path is genuinely
    outside must raise so callers can detect configuration errors.
    """
    from dbt_charts.core.compile.merge import merge_metas

    _write(tmp_path, "charts/.keep", "")
    _write(tmp_path, "other/.keep", "")
    outside = _directory(tmp_path, "other", local_project)
    boards_root = _directory(tmp_path, "charts", local_project)
    with pytest.raises(ValueError, match="not under"):
        merge_metas(outside, boards_root)


def test_merged_patch_link_false_survives_extends_in_both_directions(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """The merge property that makes false (not an explicit null) the per-chart
    `links.auto` opt-out: a child board's link: false suppresses a template chart's
    link, and a child's string link re-links a template's link: false."""
    from dbt_charts.core.compile.merge import merged_patch

    _write(
        tmp_path,
        "_template.yaml",
        "charts:\n  t:\n    type: table\n    query: orders\n    link: /rows\n",
    )
    _write(tmp_path, "board.yaml", "")
    board_file = _board_file(tmp_path, "board.yaml", local_project)
    boards_root = _directory(tmp_path, ".", local_project)

    suppressed = merged_patch(
        _node(
            {
                "extends": "./_template.yaml",
                "charts": {"t": {"type": "table", "link": False}},
            }
        ),
        board_file,  # type: ignore[arg-type]
        boards_root,  # type: ignore[arg-type]
    )
    assert suppressed.charts["t"].link is False  # type: ignore[union-attr]

    _write(
        tmp_path,
        "_unlinked.yaml",
        "charts:\n  t:\n    type: table\n    query: orders\n    link: false\n",
    )
    relinked = merged_patch(
        _node(
            {
                "extends": "./_unlinked.yaml",
                "charts": {"t": {"type": "table", "link": "/x"}},
            }
        ),
        board_file,  # type: ignore[arg-type]
        boards_root,  # type: ignore[arg-type]
    )
    assert relinked.charts["t"].link == "/x"  # type: ignore[union-attr]
