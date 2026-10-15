"""Tests for the merge-engine foundation: Merge marker, strategy(), merge_patches().

TDD: these tests are written before the implementation.
No D-NN tokens in this file.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel, ConfigDict

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.markers import Exclusive, Merge, Strategy

# ---------------------------------------------------------------------------
# Small helper models for merge_patches tests — no dependency on AuthoredBoard.
# ---------------------------------------------------------------------------


class _Inner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: str | None = None
    y: int | None = None


class _InnerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: str | None = None
    y: int | None = None


class _SimplePatch(BaseModel):
    """Minimal patch model: scalar + list + dict + nested model."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str | None, Merge(Strategy.OVERRIDE)] = None
    tags: Annotated[list[str] | None, Merge(Strategy.OVERRIDE)] = None
    mapping: Annotated[dict[str, str] | None, Merge(Strategy.BY_KEY)] = None
    nested: Annotated[_InnerPatch | None, Merge(Strategy.DEEP)] = None


# ===========================================================================
# Merge marker
# ===========================================================================


def test_merge_marker_valid_strategies() -> None:
    assert Merge(Strategy.OVERRIDE).file == Strategy.OVERRIDE
    assert Merge(Strategy.BY_KEY).file == Strategy.BY_KEY


def test_merge_marker_all_strategies_accepted() -> None:
    for strat in (Strategy.OVERRIDE, Strategy.DEEP, Strategy.BY_KEY):
        assert Merge(strat).file == strat


# ===========================================================================
# Strategy enum
# ===========================================================================


def test_strategy_enum_str_compat() -> None:
    """Strategy members compare equal to their string values (str subclass)."""
    assert Strategy.OVERRIDE == "override"
    assert Strategy.DEEP == "deep"
    assert Strategy.BY_KEY == "by_key"


def test_strategy_enum_merge_field_identity() -> None:
    """Merge(Strategy.DEEP).file is the enum member, not a bare string."""
    m = Merge(Strategy.DEEP)
    assert m.file is Strategy.DEEP
    assert isinstance(m.file, Strategy)


def test_strategy_enum_invalid_raises() -> None:
    """Strategy("bogus") raises ValueError — enum IS the validation."""
    with pytest.raises(ValueError, match="bogus"):
        Strategy("bogus")


# ===========================================================================
# strategy()
# ===========================================================================


def test_strategy_reads_explicit_file_marker() -> None:
    from dbt_charts.core.compile.merge import strategy

    field = _SimplePatch.model_fields["tags"]
    assert strategy(field) == "override"


def test_strategy_type_inferred_model() -> None:
    """Field with no Merge marker whose type is a BaseModel -> 'deep'."""
    from dbt_charts.core.compile.merge import strategy

    class _NakedPatch(BaseModel):
        sub: _InnerPatch | None = None

    field = _NakedPatch.model_fields["sub"]
    assert strategy(field) == "deep"


def test_strategy_type_inferred_dict() -> None:
    from dbt_charts.core.compile.merge import strategy

    class _NakedPatch(BaseModel):
        d: dict[str, str] | None = None

    field = _NakedPatch.model_fields["d"]
    assert strategy(field) == "by_key"


def test_strategy_type_inferred_scalar() -> None:
    from dbt_charts.core.compile.merge import strategy

    class _NakedPatch(BaseModel):
        s: str | None = None

    field = _NakedPatch.model_fields["s"]
    assert strategy(field) == "override"


def test_strategy_infers_override_for_an_unmarked_list() -> None:
    from dbt_charts.core.compile.merge import strategy

    class _NakedListPatch(BaseModel):
        items: list[str] | None = None

    field = _NakedListPatch.model_fields["items"]
    assert strategy(field) == "override"


# ===========================================================================
# merge_patches — core semantics
# ===========================================================================


def _make(model: type[BaseModel], **kwargs: object) -> BaseModel:
    """Construct a patch with exactly the given fields set (all others unset)."""
    return model.model_validate(kwargs)


def test_merge_patches_override_upper_wins() -> None:
    from dbt_charts.core.compile.merge import merge_patches

    lo = _make(_SimplePatch, title="base")
    hi = _make(_SimplePatch, title="override")
    result = merge_patches(lo, hi)
    assert result.title == "override"


def test_merge_patches_override_empty_model_upper_changes_nothing() -> None:
    """An empty block (``x: {}``) sets no field, so it replaces nothing.

    The rule is general, not cache-specific: OVERRIDE is the default strategy
    for any un-annotated field, and every other strategy already merges an
    all-unset patch to a no-op — override is the one that would otherwise
    clobber the lower value with a blank model.
    """
    from dbt_charts.core.compile.merge import merge_patches

    class _OuterPatch(BaseModel):
        model_config = ConfigDict(extra="forbid")

        # No Merge marker: OVERRIDE is the default for un-annotated fields.
        inner: _InnerPatch | None = None

    lo = _OuterPatch.model_validate({"inner": {"x": "keep", "y": 1}})
    hi = _OuterPatch.model_validate({"inner": {}})
    assert hi.inner is not None and not hi.inner.model_fields_set

    result = merge_patches(lo, hi)
    assert result.inner is not None
    assert (result.inner.x, result.inner.y) == ("keep", 1)


def test_merge_patches_unset_upper_keeps_lower() -> None:
    from dbt_charts.core.compile.merge import merge_patches

    lo = _make(_SimplePatch, title="base")
    hi = _SimplePatch()  # title not in model_fields_set
    assert "title" not in hi.model_fields_set
    result = merge_patches(lo, hi)
    assert result.title == "base"


def test_merge_patches_explicit_null_clears() -> None:
    from dbt_charts.core.compile.merge import merge_patches

    lo = _make(_SimplePatch, title="base")
    # Explicitly set title=None — must be in model_fields_set
    hi = _SimplePatch.model_construct(title=None)
    # model_construct doesn't track fields_set; validate instead:
    hi = _make(_SimplePatch, title=None)
    # model_validate({"title": None}) sets model_fields_set = {"title"}
    assert "title" in hi.model_fields_set
    result = merge_patches(lo, hi)
    assert result.title is None


def test_merge_patches_by_key_merges_dicts() -> None:
    from dbt_charts.core.compile.merge import merge_patches

    lo = _make(_SimplePatch, mapping={"a": "1"})
    hi = _make(_SimplePatch, mapping={"b": "2"})
    result = merge_patches(lo, hi)
    assert result.mapping == {"a": "1", "b": "2"}


def test_merge_patches_by_key_upper_wins_collision() -> None:
    from dbt_charts.core.compile.merge import merge_patches

    lo = _make(_SimplePatch, mapping={"a": "old"})
    hi = _make(_SimplePatch, mapping={"a": "new"})
    result = merge_patches(lo, hi)
    assert result.mapping == {"a": "new"}


def test_merge_patches_deep_recurses() -> None:
    from dbt_charts.core.compile.merge import merge_patches

    lo = _make(_SimplePatch, nested={"x": "from_lo", "y": 1})
    hi = _make(_SimplePatch, nested={"x": "from_hi"})
    result = merge_patches(lo, hi)
    assert result.nested is not None
    assert result.nested.x == "from_hi"
    assert result.nested.y == 1  # preserved from lo, unset in hi


def test_merge_patches_lo_none_takes_hi() -> None:
    """When lo.field is None and hi.field is set, take hi regardless of strategy."""
    from dbt_charts.core.compile.merge import merge_patches

    lo = _SimplePatch()  # mapping not set (None)
    hi = _make(_SimplePatch, mapping={"k": "v"})
    result = merge_patches(lo, hi)
    assert result.mapping == {"k": "v"}


def test_merge_patches_returns_same_type() -> None:
    from dbt_charts.core.compile.merge import merge_patches

    lo = _SimplePatch()
    hi = _SimplePatch()
    result = merge_patches(lo, hi)
    assert type(result) is _SimplePatch


# ===========================================================================
# Cross-axis layout rule (policy ruling 2)
# ===========================================================================


class _LayoutPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: Annotated[list[str] | None, Exclusive("layout")] = None
    cols: Annotated[list[str] | None, Exclusive("layout")] = None
    title: Annotated[str | None, Merge(Strategy.OVERRIDE)] = None


def test_cross_axis_layout_lower_cleared() -> None:
    """When lower has rows and upper has cols, lower's rows are cleared."""
    from dbt_charts.core.compile.merge import merge_patches

    lo = _make(_LayoutPatch, rows=["x"], title="keep")
    hi = _make(_LayoutPatch, cols=["y"])
    result = merge_patches(lo, hi)
    assert result.rows is None
    assert result.cols == ["y"]
    assert result.title == "keep"  # non-layout field unaffected


def test_same_kind_layout_replaces() -> None:
    from dbt_charts.core.compile.merge import merge_patches

    lo = _make(_LayoutPatch, rows=["x"])
    hi = _make(_LayoutPatch, rows=["y"])
    result = merge_patches(lo, hi)
    assert result.rows == ["y"]


def test_layout_group_marker_matches_at_most_one_of() -> None:
    """The Exclusive("layout") members are exactly the AtMostOneOf layout keys."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.models.board.patch import BoardPatch
    from dbt_charts.core.compile.models.constraints import AtMostOneOf

    (at_most_one,) = [
        c for c in AuthoredBoard.__constraints__ if isinstance(c, AtMostOneOf)
    ]
    for model in (AuthoredBoard, BoardPatch):
        marked = {
            name
            for name, field in model.model_fields.items()
            if Exclusive("layout") in field.metadata
        }
        assert marked == set(at_most_one.keys)


def test_style_layout_cols_keeps_inherited_style_layout_rows() -> None:
    """style.layout has rows/cols fields of its own; they are not board layouts."""
    from dbt_charts.core.compile.merge import merge_patches
    from dbt_charts.core.compile.models.style.authored import StylePatch

    lo = StylePatch.model_validate({"layout": {"rows": {"gap": 5}}})
    hi = StylePatch.model_validate({"layout": {"cols": {"gap": 7}}})
    result = merge_patches(lo, hi)
    assert result.layout.rows.gap == 5
    assert result.layout.cols.gap == 7


# ===========================================================================
# BoardPatch + EMPTY_PATCH
# ===========================================================================


def test_board_patch_all_fields_optional() -> None:
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    p = BoardPatch()
    assert p is not None


def test_board_patch_tags_infers_override() -> None:
    from dbt_charts.core.compile.merge import strategy
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    assert strategy(BoardPatch.model_fields["tags"]) == "override"


def test_board_patch_carries_merge_marker_on_queries() -> None:
    from dbt_charts.core.compile.models.board.patch import BoardPatch
    from dbt_charts.core.compile.models.markers import Merge

    meta = BoardPatch.model_fields["queries"].metadata
    merge_markers = [m for m in meta if isinstance(m, Merge)]
    assert len(merge_markers) == 1
    assert merge_markers[0].file == "by_key"


def test_empty_patch_no_fields_set() -> None:
    from dbt_charts.core.compile.models.board.patch import EMPTY_PATCH

    assert EMPTY_PATCH.model_fields_set == set()


def test_empty_patch_title_is_none() -> None:
    from dbt_charts.core.compile.models.board.patch import EMPTY_PATCH

    assert EMPTY_PATCH.title is None  # type: ignore[attr-defined]


def test_unwrap_type_resolves_tuple_behind_a_nested_annotated_constraint() -> None:
    """A constraint nested on an *inner* Annotated (e.g.
    Annotated[Annotated[tuple[X, X], Field(strict=True)] | None, Merge(...)]) --
    the pattern ScaleContinuousStyle.domain uses so the constraint survives
    build_patch_model_ext -- must still resolve to `tuple`, not the leftover
    Annotated wrapper. _unwrap_type previously peeled Annotated and Union each
    only once, in that fixed order, and missed this case."""
    from dbt_charts.core.compile.merge import _unwrap_type
    from dbt_charts.core.compile.models.style.theme.axis import ScaleContinuousStyle

    field = ScaleContinuousStyle.model_fields["domain"]
    assert _unwrap_type(field) is tuple


# ===========================================================================
# Regression: multi-layer composition of list-typed style fields
# ===========================================================================


def test_multi_layer_title_sizes_merge_does_not_crash(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """board → template → theme: merging style.title.sizes must not raise TypeError."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()

    # Template sets title.sizes — this is the layer that used to crash.
    (tmp_path / "charts" / "base.yaml").write_text(
        "style:\n  title:\n    sizes: [32, 28, 22, 18, 16, 14]\n"
    )
    board_yaml = "extends: ./base.yaml\nrows:\n  - cols:\n    - text: hi\n"
    board_path = tmp_path / "charts" / "board.yaml"
    board_path.write_text(board_yaml)

    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, result.errors


def test_multi_layer_dashes_merge_does_not_crash(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """board → template → theme: merging style.charts.dashes must not raise TypeError."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()

    (tmp_path / "charts" / "base.yaml").write_text(
        "style:\n  charts:\n    dashes: [[4, 2], [8, 4]]\n"
    )
    board_yaml = "extends: ./base.yaml\nrows:\n  - cols:\n    - text: hi\n"
    board_path = tmp_path / "charts" / "board.yaml"
    board_path.write_text(board_yaml)

    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, result.errors


def test_multi_layer_list_style_override_is_idempotent(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """When the same style.title.sizes appears at two cascade levels, last-layer wins.

    Documents the 'override' semantics: the board's own value replaces the template's,
    producing a deterministic result rather than crashing or appending.
    """
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()

    (tmp_path / "charts" / "base.yaml").write_text(
        "style:\n  title:\n    sizes: [32, 28, 22, 18, 16, 14]\n"
    )
    # Board also sets sizes — should override base cleanly.
    board_yaml = (
        "extends: ./base.yaml\n"
        "style:\n  title:\n    sizes: [40, 34, 28, 22, 18, 16]\n"
        "rows:\n  - cols:\n    - text: hi\n"
    )
    board_path = tmp_path / "charts" / "board.yaml"
    board_path.write_text(board_yaml)

    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, result.errors


# ---------------------------------------------------------------------------
# Unknown extends entry must raise CompilationError, not silently fall back.
# ---------------------------------------------------------------------------


def test_unknown_extends_name_raises_compilation_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """An unrecognized extends entry raises CompilationError with the entry name."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.merge import merge_extends
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    (tmp_path / "board.yaml").write_text("")
    project = local_project(tmp_path)
    board_file = project.path("board.yaml")

    node = BoardPatch.model_validate({"extends": "creem"})  # typo of "cream"/"paper"

    with pytest.raises(CompilationError, match="creem"):
        merge_extends(node, board_file, project.directory("."))


def test_known_theme_name_does_not_raise_via_merge_extends(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A valid built-in theme name resolves successfully without raising."""
    from dbt_charts.core.compile.merge import merge_extends
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    (tmp_path / "board.yaml").write_text("")
    project = local_project(tmp_path)
    board_file = project.path("board.yaml")

    node = BoardPatch.model_validate({"extends": "paper"})

    # Must not raise — paper is a valid built-in theme.
    result = merge_extends(node, board_file, project.directory("."))
    assert result is not None


def test_compile_file_surfaces_unknown_extends_as_structured_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """compile_file on a board with unknown extends returns structured error, not a crash."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    (tmp_path / "charts").mkdir()
    board_path = tmp_path / "charts" / "bad.yaml"
    board_path.write_text("extends: creem\nrows:\n  - cols:\n    - text: hi\n")

    result = compile_file(project.path("charts/bad.yaml").read_board())

    assert not result.success
    assert result.errors
    assert any("creem" in e.message for e in result.errors)


def test_compile_file_surfaces_unknown_theme_sugar_as_structured_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """`theme: creem` (sugar form) produces the same structured error as `extends: creem`."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    (tmp_path / "charts").mkdir()
    board_path = tmp_path / "charts" / "bad.yaml"
    board_path.write_text("theme: creem\nrows:\n  - cols:\n    - text: hi\n")

    result = compile_file(project.path("charts/bad.yaml").read_board())

    assert not result.success
    assert result.errors
    assert any("creem" in e.message for e in result.errors)


def test_compile_file_theme_and_extends_conflict_is_structured_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A board with both `theme:` and `extends:` returns a structured error, not a crash."""
    from dbt_charts.core.compile.compiler import compile_file

    project = local_project(tmp_path)
    (tmp_path / "charts").mkdir()
    board_path = tmp_path / "charts" / "conflict.yaml"
    board_path.write_text(
        "theme: paper\nextends: neon\nrows:\n  - cols:\n    - text: hi\n"
    )

    result = compile_file(project.path("charts/conflict.yaml").read_board())

    assert not result.success
    assert result.errors
    # The conflict message must surface — not a Python crash
    assert any(
        "theme" in e.message.lower() or "extends" in e.message.lower()
        for e in result.errors
    )
