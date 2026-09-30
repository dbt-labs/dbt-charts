"""Migration coverage for the built-in theme-set collapse
(dbt_charts.core.compile.migrations.versions.v0_6_0), the identity-path
value-mapped ``theme:`` Move that renames retired built-in theme names in
memory.
"""

from __future__ import annotations

import textwrap
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile, compile_file
from dbt_charts.core.compile.migrations import prepare_board_mapping
from dbt_charts.core.compile.migrations.migrations import (
    Move,
    _apply_move,
)
from dbt_charts.core.compile.migrations.versions.v0_6_0 import THEME_VALUE_MAP
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)

_BODY = textwrap.dedent(
    """\
    queries:
      q:
        columns: [a, b]
        values: [[1, 2]]
    charts:
      c:
        type: bar
        query: q
        x: a
        y: b
    rows:
      - c
    """
)


def _board(header: str) -> str:
    return f"{header}\n{_BODY}"


class TestRetiredThemeNamesMigrateInMemory:
    @pytest.mark.parametrize(
        ("retired_name", "expected_theme"),
        [
            ("solid", "clarity"),
            ("plain", "clarity"),
            ("editorial", "clarity"),
            ("cream", "paper"),
        ],
    )
    def test_retired_theme_name_migrates_and_compiles(
        self, retired_name: str, expected_theme: str
    ) -> None:
        """A retired `theme:` value migrates to its successor in memory --
        an identity-path Move rewrites the value back under the *same*
        `theme:` key. `THEME_VALUE_MAP`, which backs this Move, is total
        over both the retired names and every current `ThemeName` value
        (see its comment in `versions/v0_6_0.py`).
        """
        result = compile(_board(f"title: t\ntheme: {retired_name}"))

        assert result.success, result.errors
        assert result.board is not None
        assert result.board.theme == expected_theme

    def test_a_retired_theme_name_warns_with_the_softened_dct_migrate_wording(
        self,
    ) -> None:
        """`_apply_identity_moves`'s own warning literal, pinned directly --
        it has no `Deletion.reason` to check against, and no other test
        asserts its text (the twin generic notice in `migrate_mapping` is
        pinned by `test_deletion_without_reason_emits_no_reason_warning`,
        a different code path). A full valid board (not a bare `theme:`
        mapping) so the rename alone satisfies the currency check and
        `migrate_mapping` is never reached -- exactly one warning fires."""
        result = compile(_board("title: t\ntheme: solid"))

        assert result.success, result.errors
        messages = [
            d.message for d in result.warnings if d.code == "WARN-SCHEMA-MIGRATED"
        ]
        assert messages == [
            "dbt charts migrated this YAML in memory; `dct migrate` may be "
            "able to update the file."
        ]


class TestRetiredThemeNamesMigrateViaUncappedTextRewrite:
    """The on-disk text-rewrite lane (`migrate_yaml_text`) the identity-path
    `theme:` Move must pass through -- the file-rewrite lane
    `prepare_board_mapping`'s in-memory warning tells an author to run.
    Unlike `_apply_identity_moves`, `migrate_yaml_text` re-validates its
    result against the current schema before returning (see
    `_incomplete_migration_error`), so a value the map sent somewhere
    `theme:` cannot hold would raise here even though the in-memory lane
    compiled it happily.

    Calls `migrate_yaml_text` directly (uncapped) against the real theme
    Move, not `migrate_board_yaml_text` (which caps `dct migrate` at the
    latest *frozen* version -- this Move is still declared on the pending
    `0.5.0 -> current` boundary, not frozen, so the capped CLI path would
    leave it untouched). This still exercises the real declaration and the
    real text-rewrite mechanics; it is not testing the CLI entry point
    specifically. See `test_description_notes_migration.py`'s equivalent
    tests for the same reasoning.
    """

    @pytest.mark.parametrize(
        ("retired_name", "expected_theme"),
        [
            ("solid", "clarity"),
            ("plain", "clarity"),
            ("editorial", "clarity"),
            ("cream", "paper"),
        ],
    )
    def test_migrate_yaml_text_rewrites_the_theme_value(
        self, retired_name: str, expected_theme: str
    ) -> None:
        from dbt_charts.core.compile.migrations import migrate_yaml_text
        from dbt_charts.core.compile.migrations.migrations import (
            _board_migration_context,
        )
        from dbt_charts.core.compile.parse.parser import load_yaml_mapping, parse_yaml

        yaml_text = _board(f"title: t\ntheme: {retired_name}")
        catalog, registry = _board_migration_context()

        migrated = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

        doc = load_yaml_mapping(migrated)
        assert doc["theme"] == expected_theme
        # The same sanity check `agent_api.migrate.migrate_paths` runs on every
        # file it rewrites before writing it back -- the rewritten text must
        # itself parse clean under the current grammar, not just contain the
        # right string. `theme:` desugars to `extends:` at parse time
        # (`desugar_theme`, a module-level function run as a BeforeValidator),
        # so the parsed `AuthoredBoard` carries it there.
        board = parse_yaml(migrated)
        assert board.extends == expected_theme

    def test_current_theme_name_is_not_rewritten(self) -> None:
        """A board already on a current theme name is returned byte-identical
        -- the writer must not touch a file with nothing to migrate."""
        from dbt_charts.core.compile.migrations import migrate_yaml_text
        from dbt_charts.core.compile.migrations.migrations import (
            _board_migration_context,
        )

        yaml_text = _board("title: t\ntheme: vivid")
        catalog, registry = _board_migration_context()

        assert (
            migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)
            == yaml_text
        )


def test_surviving_theme_name_is_untouched() -> None:
    """A current name never engages the Move at all -- recognition returns
    `current` before any transition is checked, so this also proves the
    identity value_map is not itself corrupting live boards."""
    result = compile(_board("title: t\ntheme: vivid"))

    assert result.success, result.errors
    assert result.board is not None
    assert result.board.theme == "vivid"


def test_stark_theme_name_is_not_retired() -> None:
    """`stark` kept its name across this boundary -- unlike `plain`/`solid`/
    `editorial`/`cream`, it is a live `ThemeName` value, not an entry in
    `THEME_VALUE_MAP`'s retired half, so `theme: stark` never engages the
    Move and never warns."""
    result = compile(_board("title: t\ntheme: stark"))

    assert result.success, result.errors
    assert result.board is not None
    assert result.board.theme == "stark"
    assert [
        d.message for d in result.warnings if d.code == "WARN-SCHEMA-MIGRATED"
    ] == []


def test_unmapped_theme_value_is_left_untouched_by_the_move() -> None:
    """An identity-path Move's source path (``("theme",)``) never disappears
    from any grammar -- unlike a structural rename, its mere presence in a
    document is not evidence the document is old (see
    ``move_source_locations``' value gate). A garbage value absent from
    THEME_VALUE_MAP was never legal `theme:` syntax at any schema version, so
    the gate leaves it alone rather than forcing it through the map: it
    surfaces later as the ordinary ERR-UNKNOWN-THEME (see
    test_unknown_theme_errors.py) instead of an internal migration error.
    """
    schema_catalog = load_yaml_schema_catalog()
    move = Move(
        source_schema="0.5.0",
        target_schema=schema_catalog.dev.version,
        old_path=("theme",),
        new_path=("theme",),
        value_map=THEME_VALUE_MAP,
    )
    mapping: dict[str, Any] = {"theme": "totally-fake-theme"}

    _apply_move(mapping, move, schema_catalog)

    assert mapping == {"theme": "totally-fake-theme"}


def test_current_theme_name_does_not_misreport_an_unrelated_error() -> None:
    """The identity-path Move's source path is present in every document,
    valid or not -- ``theme:`` is permanent authoring sugar and never
    disappears from any grammar. A board that already authors a *current*
    theme name plus some real, unrelated authoring mistake must report that
    mistake directly; it must not fire the Move on key presence alone and
    wrap the real error in a false "could not finish migrating" warning.
    """
    mapping: dict[str, Any] = {
        "title": "T",
        "theme": "clarity",
        "queries": {"q": {"sql": "select 1 as revenue"}},
        "charts": {"c": {"type": "bar", "query": "q", "x": "revenue", "y": "revenue"}},
        "rows": ["c"],
        "totally_unknown_field_xyz": True,
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = prepare_board_mapping(dict(mapping))

    assert [str(w.message) for w in caught] == []
    assert result == mapping


def test_theme_path_ref_does_not_misfire_the_move() -> None:
    """``theme: ./fragment.yaml`` desugars to ``extends: ./fragment.yaml``,
    an unambiguous board reference (`validate/dispatch.py` skips path refs
    when checking theme names) -- not a candidate for THEME_VALUE_MAP, whose
    domain is `theme:`'s bounded enum. The value gate must leave it alone
    rather than raising "has no entry in the value map".
    """
    mapping: dict[str, Any] = {"title": "T", "theme": "./_fragment.yaml"}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = prepare_board_mapping(dict(mapping))

    assert [str(w.message) for w in caught] == []
    assert result == mapping


def test_extends_path_fragment_still_compiles(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """The value_map covers `theme:`'s bounded domain only -- a `theme:`
    path fragment is untouched, proving the identity Move did not capture it
    even though it desugars into `extends:` before validation ever sees it.

    Discriminating on ``result.success`` alone: a regression that removed
    the value gate (``_identity_value_would_change``) would route this path
    ref into ``_mapped_value``, which cannot find it in ``THEME_VALUE_MAP``
    and raises -- catchable in more than one place along
    ``compile_file``'s parse path, several of which would still report
    success with the fragment silently unresolved. Asserting no
    ``WARN-SCHEMA-MIGRATED`` fired, the same check
    ``test_theme_path_ref_does_not_misfire_the_move`` makes at the
    ``prepare_board_mapping`` level, closes that gap here at the full
    ``compile_file`` level -- a real project, real fragment file, real
    extends resolution."""
    charts = tmp_path / "charts"
    charts.mkdir()
    (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
    (charts / "_fragment.yaml").write_text("style:\n  charts: {}\n", encoding="utf-8")
    (charts / "board.yaml").write_text(
        _board("title: t\ntheme: ./_fragment.yaml"), encoding="utf-8"
    )

    project = local_project(tmp_path)
    result = compile_file(project.path("charts/board.yaml").read_board())

    assert result.success, result.errors
    assert [
        d.message for d in result.warnings if d.code == "WARN-SCHEMA-MIGRATED"
    ] == []


class TestRetiredExtendsNamesResolveInAProject:
    """`extends:`'s authored type (``ThemeName | str | list[str]``) makes a
    retired builtin name ambiguous with a real project board of the same
    name -- unlike `theme:`, which is unambiguous authoring sugar and can
    migrate unconditionally at parse time. `extends:` retired-name
    resolution therefore lives in the merge/extends layer (`merge.py`'s
    `_retired_theme_redirect`), only after a matching project board file has
    already been ruled out.
    """

    @pytest.mark.parametrize(
        ("retired_name", "expected_theme"),
        [
            ("solid", "clarity"),
            ("plain", "clarity"),
            ("editorial", "clarity"),
            ("cream", "paper"),
        ],
    )
    def test_retired_name_resolves_and_compiles(
        self,
        retired_name: str,
        expected_theme: str,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (charts / "board.yaml").write_text(
            _board(f"title: t\nextends: {retired_name}"), encoding="utf-8"
        )

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/board.yaml").read_board())

        assert result.success, result.errors
        assert result.board is not None
        assert result.board.theme == expected_theme
        [notice] = [d for d in result.warnings if d.code == "WARN-SCHEMA-MIGRATED"]
        assert notice.message.startswith(
            f"charts/board.yaml: dbt charts resolved retired `extends:` theme name "
            f"{retired_name!r}"
        )

    def test_stark_extends_is_not_retired(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """`extends: stark` is a live theme name, not a `THEME_RENAMES`
        entry -- it resolves directly through `_resolve_entry`'s theme-names
        branch, never through `_retired_theme_redirect`, and never warns."""
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (charts / "board.yaml").write_text(
            _board("title: t\nextends: stark"), encoding="utf-8"
        )

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/board.yaml").read_board())

        assert result.success, result.errors
        assert result.board is not None
        assert result.board.theme == "stark"
        assert [
            d.message for d in result.warnings if d.code == "WARN-SCHEMA-MIGRATED"
        ] == []

    def test_a_real_project_board_of_the_same_name_wins(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The retired-name redirect must never shadow a real board -- it
        only fires once a matching board file has been ruled out."""
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (tmp_path / "cream.yaml").write_text(
            "title: a real project board named cream\n", encoding="utf-8"
        )
        (charts / "board.yaml").write_text(_board("extends: cream"), encoding="utf-8")

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/board.yaml").read_board())

        assert result.success, result.errors
        assert result.board is not None
        assert result.board.title == "a real project board named cream"

    def test_list_form_migrates_only_the_retired_entry(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A retired name inside a list entry resolves per-entry, the same as
        a scalar -- `_resolve_entry` runs once per list item regardless of
        whether the author wrote a scalar or a list, so this falls out of the
        same redirect without a separate mechanism. The path-ref sibling
        entry is untouched."""
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (charts / "_fragment.yaml").write_text(
            "style:\n  charts: {}\n", encoding="utf-8"
        )
        (charts / "board.yaml").write_text(
            _board("title: t\nextends: [cream, ./_fragment.yaml]"),
            encoding="utf-8",
        )

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/board.yaml").read_board())

        assert result.success, result.errors
        assert result.board is not None
        assert result.board.theme == "paper"

    def test_cloud_default_theme_shape_compiles(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The exact shape Cloud's `set_project_default_theme` writes into
        `charts/meta.yml` (`extends: <theme>, theme: None`) -- the retired
        name must resolve without needing `theme:` at all."""
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (charts / "meta.yml").write_text("extends: cream\n", encoding="utf-8")
        (charts / "board.yaml").write_text(_board("title: t"), encoding="utf-8")

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/board.yaml").read_board())

        assert result.success, result.errors
        assert result.board is not None
        assert result.board.theme == "paper"


def test_extends_retired_name_still_fails_loud_outside_a_project() -> None:
    """The bare in-memory `compile()` lane never invokes the merge/extends
    layer for a plain `extends:` entry -- `_theme_from_extends`'s own
    docstring states nothing folds a chain outside `compile_file`'s root
    board -- so the retired-name redirect (`merge.py`) has nowhere to run.
    This is the one remaining case Fix 2 does not migrate; pinned per
    `migrations/AGENTS.md`'s rule that an unmigratable claim ships a test.
    """
    result = compile(_board("title: t\nextends: cream"))

    assert not result.success
    assert any(e.code == "ERR-UNKNOWN-THEME" for e in result.errors)


def test_retired_theme_name_in_a_nested_sub_board_fails_loud() -> None:
    """Neither `_apply_identity_moves` nor `_retired_theme_redirect` walks
    the document tree -- `theme:`'s Move is hand-declared at the document
    root only, and `_resolve_entry` only ever sees entries its own caller's
    extends chain hands it (see `versions/v0_6_0.py`'s module docstring).
    A retired name authored on a nested sub-board's own `theme:` therefore
    reaches neither mechanism: it fails loud as an ordinary unknown theme,
    with no migration warning telling the author anything was attempted.
    Pinned per `migrations/AGENTS.md`'s rule that an unmigratable claim
    ships a test.
    """
    yaml_content = textwrap.dedent(
        """\
        title: t
        charts:
          c:
            type: bar
            query: q
            x: a
            y: b
        queries:
          q:
            columns: [a, b]
            values: [[1, 2]]
        rows:
          - theme: cream
            rows:
              - c
        """
    )

    result = compile(yaml_content)

    assert not result.success
    assert any(e.code == "ERR-UNKNOWN-THEME" for e in result.errors)
    assert [
        d.message for d in result.warnings if d.code == "WARN-SCHEMA-MIGRATED"
    ] == []
