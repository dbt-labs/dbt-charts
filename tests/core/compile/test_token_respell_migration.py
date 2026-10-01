"""Migration coverage for the 0.7.0 -> 0.9.0 boundary
(dbt_charts.core.compile.migrations.versions.v0_9_0): the categorical color
variant grammar's `TokenRespell` (identity-path `Move`s with a value_map)
and `MapKeyDeletion` (the four retired `style.palettes` role keys).

Both are declared even though neither shows up in `_current_schema_rejections`
-- a retired token spelling is still a valid string, and a retired role key
is still a valid entry in the open map `style.palettes` -- see
`versions/v0_9_0.py`'s module docstring and `migrations/AGENTS.md`.
"""

from __future__ import annotations

import textwrap
import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    prepare_board_mapping,
)
from dbt_charts.core.compile.migrations.migrations import (
    _board_migration_context,
    migrate_board_yaml_text,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.versions.v0_9_0 import (
    RETIRED_PALETTE_ROLE_KEYS,
    RETIRED_TOKEN_VALUE_MAP,
)
from dbt_charts.core.compile.models.board.patch import BoardPatch

_QUERIES = textwrap.dedent(
    """\
    queries:
      q:
        columns: [a, b]
        values: [[1, 2]]
    """
)


def _board(body: str) -> str:
    return f"title: t\n{body}\n{_QUERIES}"


class TestRetiredTokensRespellInMemory:
    """`prepare_board_mapping` -- the only automatic path; Cloud has no other."""

    def test_a_board_matching_both_identity_predicates_applies_moves_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retired `theme:` name (`_theme_value_needs_recheck`) and a
        retired categorical token (`_retired_categorical_token_needs_recheck`)
        on the same board both gate a call to `_apply_identity_moves` --
        which applies the *whole* registry's identity moves in one pass,
        theme rename and TokenRespell alike. Calling it a second time would
        be a wasted no-op walk (a deep-copy that changes nothing), not a
        correctness bug -- this pins the efficiency, not a new behavior."""
        from dbt_charts.core.compile.migrations import migrations as _impl

        calls = 0
        original = _impl._apply_identity_moves

        def _counting(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(_impl, "_apply_identity_moves", _counting)

        mapping: dict[str, Any] = {
            "title": "t",
            "theme": "solid",
            "style": {"palettes": {"category": "vivid-10-dark"}},
            "charts": {"c": {"type": "bar", "query": "q", "x": "a", "y": "b"}},
            "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
            "rows": ["c"],
        }

        result = prepare_board_mapping(dict(mapping))

        assert calls == 1
        assert result["theme"] == "clarity"
        assert result["style"]["palettes"]["category"] == "vivid-10.dark"

    def test_every_retired_spelling_and_role_key_migrates_on_one_board(self) -> None:
        mapping: dict[str, Any] = {
            "title": "t",
            "style": {
                "palettes": {
                    "category": "vivid-10",
                    "category_dark": "vivid-10-dark",
                    "category_light": "vivid-10-light",
                    "category_ghost": "vivid-10-ghost",
                    "category_ink": "vivid-10-ink",
                },
                "charts": {
                    "color": {
                        "categorical": {"single_series_palette": "category_dark.blue"}
                    }
                },
            },
            "charts": {
                "c": {
                    "type": "bar",
                    "query": "q",
                    "x": "a",
                    "y": "b",
                    "style": {"color": {"static": "category_dark[2]"}},
                }
            },
            "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
            "rows": ["c"],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = prepare_board_mapping(dict(mapping))

        assert result["style"]["palettes"] == {"category": "vivid-10"}
        assert (
            result["style"]["charts"]["color"]["categorical"]["single_series_palette"]
            == "category.blue.dark"
        )
        assert result["charts"]["c"]["style"]["color"]["static"] == "category[2].dark"
        messages = {str(w.message) for w in caught}
        for role in RETIRED_PALETTE_ROLE_KEYS:
            assert any(role in message for message in messages), (role, messages)

    def test_extends_theme_fragment_migrates_via_boardpatch(self) -> None:
        """A theme YAML's `style:`-only content -- `BoardPatch`, not
        `AuthoredBoard` (which cannot validate a bare fragment)."""
        patch: dict[str, Any] = {
            "style": {
                "palettes": {
                    "category": "vivid-10",
                    "category_dark": "vivid-10-dark",
                }
            }
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = prepare_board_mapping(dict(patch), model=BoardPatch)

        assert result == {"style": {"palettes": {"category": "vivid-10"}}}
        assert any("category_dark" in str(w.message) for w in caught)

    def test_current_board_takes_the_fast_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A board with no retired spelling or role key must never build the
        migration registry -- the same guarantee
        `test_prepare_mapping_current_schema_patch_takes_fast_path_under_boardpatch`
        pins for the theme rename.

        Patches `_board_migration_context` itself, not the
        `_build_board_migration_context` it wraps: that wrapper is
        `@cache`d, so once any earlier test in the same process warms the
        cache, patching only the builder behind it never gets called --
        the assertion passes without the fast path ever being exercised.
        Same pattern as `test_prepare_mapping_current_schema_patch_takes_fast_path_under_boardpatch`.
        """
        from dbt_charts.core.compile.migrations import migrations as _impl

        def _boom() -> Any:
            raise AssertionError(
                "built the migration registry for a fully current board"
            )

        monkeypatch.setattr(_impl, "_board_migration_context", _boom)

        mapping: dict[str, Any] = {
            "title": "t",
            "extends": "stark",
            "style": {"palettes": {"category": "vivid-10"}},
            "charts": {"c": {"type": "bar", "query": "q", "x": "a", "y": "b"}},
            "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
            "rows": ["c"],
        }

        result = prepare_board_mapping(dict(mapping))

        assert result == mapping

    def test_a_current_value_that_happens_to_be_a_role_name_is_untouched(self) -> None:
        """A value the map has never heard of (a current family name used as
        some other role's value) must not be forced through the map -- the
        same value gate the theme rename relies on
        (`_identity_value_would_change`)."""
        mapping: dict[str, Any] = {
            "title": "t",
            "style": {"palettes": {"category": "vivid-10", "sequence": "tableau"}},
            "charts": {
                "c": {
                    "type": "bar",
                    "query": "q",
                    "x": "a",
                    "y": "b",
                    # Present so the presence predicate fires and the eager
                    # hook actually runs -- proves the *other* role's current
                    # value survives untouched, not merely that nothing ran.
                    "style": {"color": {"static": "category_dark[1]"}},
                }
            },
            "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
            "rows": ["c"],
        }

        result = prepare_board_mapping(dict(mapping))

        assert result["style"]["palettes"] == {
            "category": "vivid-10",
            "sequence": "tableau",
        }

    def test_a_nested_sub_boards_own_chart_token_is_a_known_gap(self) -> None:
        """`TokenRespell`'s paths come from `_relative_field_paths` walking
        `AuthoredBoard`'s model tree -- the same walk `suffix_rename_moves`
        uses, with the same `seen`-guard limitation `v0_7_0.py`'s
        `THRESHOLD_RENAMES` docstring already documents: a self-nested
        `AuthoredBoard` (a sub-board under `rows`/`cols`/`grid`/`tabs`) is
        opaque past the first level, so a chart *inside that sub-board's own*
        `charts:` map is not reached. Pinned per `migrations/AGENTS.md`'s
        rule that an unmigratable claim ships a test -- this is not a
        regression this boundary introduces, it is the same gap every other
        `_relative_field_paths`-derived rename in this package already has.
        """
        mapping: dict[str, Any] = {
            "title": "t",
            "tabs": {
                "items": [
                    {
                        "title": "tab one",
                        "charts": {
                            "c": {
                                "type": "bar",
                                "query": "q",
                                "x": "a",
                                "y": "b",
                                "style": {"color": {"static": "category_dark[2]"}},
                            }
                        },
                        "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
                        "rows": ["c"],
                    }
                ]
            },
        }

        result = prepare_board_mapping(dict(mapping))

        # Unmigrated -- the known gap. A future fix that starts respelling
        # this position should update this test, not silently leave it
        # asserting the old (unmigrated) behavior.
        assert (
            result["tabs"]["items"][0]["charts"]["c"]["style"]["color"]["static"]
            == "category_dark[2]"
        )

    def test_a_chart_directly_under_a_tab_item_is_respelled(self) -> None:
        """The contrast case to the one above: an *inline* chart living
        directly under `tabs.items.*.cols.*` (not inside a nested
        sub-board's own `charts:` map) is an ordinary position
        `_relative_field_paths` reaches without crossing the self-nesting
        `seen` guard at all -- `AuthoredChart` is not self-referential the
        way `AuthoredBoard` is."""
        mapping: dict[str, Any] = {
            "title": "t",
            "tabs": {
                "items": [
                    {
                        "title": "tab one",
                        "cols": [
                            {
                                "type": "bar",
                                "query": "q",
                                "x": "a",
                                "y": "b",
                                "style": {"color": {"static": "category_dark[2]"}},
                            }
                        ],
                    }
                ]
            },
            "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
        }

        result = prepare_board_mapping(dict(mapping))

        assert (
            result["tabs"]["items"][0]["cols"][0]["style"]["color"]["static"]
            == "category[2].dark"
        )

    @pytest.mark.parametrize(
        ("retired", "replacement"),
        [
            ("vivid-10-dark:4", "vivid-10:4.dark"),
            ("vivid-10-dark_r", "vivid-10_r.dark"),
            ("vivid-10-dark:4_r", "vivid-10:4_r.dark"),
        ],
    )
    def test_a_retired_family_in_shorthand_form_is_respelled(
        self, retired: str, replacement: str
    ) -> None:
        """`vivid-10-dark:4` compiled at the branch point (the companion file
        existed; `:N`/`_r` is ordinary whole-list shorthand), so it is a
        retired spelling like any other and the map must carry it -- the
        variant trails the shorthand in the replacement, the order
        `palette()` parses."""
        mapping: dict[str, Any] = {
            "title": "t",
            "charts": {
                "c": {
                    "type": "bar",
                    "query": "q",
                    "x": "a",
                    "y": "b",
                    "style": {"color": {"categorical": {"palette": retired}}},
                }
            },
            "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
            "rows": ["c"],
        }

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SchemaMigrationWarning)
            result = prepare_board_mapping(dict(mapping))

        assert (
            result["charts"]["c"]["style"]["color"]["categorical"]["palette"]
            == replacement
        )


class TestRetiredTokensRespellOnDisk:
    """`migrate_yaml_text`/`migrate_board_yaml_text` -- the `dct migrate` file rewrite."""

    def test_dct_migrate_respells_a_retired_token(self) -> None:
        text = _board(
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query: q\n"
            "    x: a\n"
            "    y: b\n"
            "    style:\n"
            "      color:\n"
            "        static: category_dark[2]\n"
        )

        assert 'static: "category[2].dark"' in migrate_board_yaml_text(text)

    def test_dct_migrate_respells_scalar_tokens(self) -> None:
        catalog, registry = _board_migration_context()
        text = _board(
            "style:\n"
            "  charts:\n"
            "    color:\n"
            "      categorical:\n"
            "        single_series_palette: category_dark.blue\n"
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query: q\n"
            "    x: a\n"
            "    y: b\n"
            "    style:\n"
            "      color:\n"
            "        static: category_dark[2]\n"
        )

        result = migrate_yaml_text(
            text,
            catalog=catalog,
            registry=registry,
            stop_target=catalog.latest_released.version,
        )

        assert 'single_series_palette: "category.blue.dark"' in result
        assert 'static: "category[2].dark"' in result

    @pytest.mark.parametrize(
        ("retired", "replacement"),
        [
            ("vivid-10-dark:4", "vivid-10:4.dark"),
            ("vivid-10-dark_r", "vivid-10_r.dark"),
            ("vivid-10-dark:4_r", "vivid-10:4_r.dark"),
        ],
    )
    def test_dct_migrate_respells_shorthand_family_forms(
        self, retired: str, replacement: str
    ) -> None:
        catalog, registry = _board_migration_context()
        text = _board(
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query: q\n"
            "    x: a\n"
            "    y: b\n"
            "    style:\n"
            "      color:\n"
            "        categorical:\n"
            f"          palette: {retired}\n"
        )

        result = migrate_yaml_text(
            text,
            catalog=catalog,
            registry=registry,
            stop_target=catalog.latest_released.version,
        )

        assert f'palette: "{replacement}"' in result

    def test_dct_migrate_strikes_the_retired_role_keys(self) -> None:
        catalog, registry = _board_migration_context()
        text = _board(
            "style:\n"
            "  palettes:\n"
            "    category: vivid-10\n"
            "    category_dark: vivid-10-dark\n"
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query: q\n"
            "    x: a\n"
            "    y: b\n"
        )

        with pytest.warns(match="category_dark"):
            result = migrate_yaml_text(
                text,
                catalog=catalog,
                registry=registry,
                stop_target=catalog.latest_released.version,
            )

        assert "category_dark" not in result
        assert "category: vivid-10" in result

    def test_a_crlf_file_is_refused_not_corrupted(self) -> None:
        """`set_board_values`' own CRLF contract: refuse loudly rather than
        silently mix line endings. Reached through the MapKeyDeletion/Move
        text rewrite the same as any other edit that file would need."""
        catalog, registry = _board_migration_context()
        text = _board(
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query: q\n"
            "    x: a\n"
            "    y: b\n"
            "    style:\n"
            "      color:\n"
            "        static: category_dark[2]\n"
        ).replace("\n", "\r\n")

        with pytest.raises(ValueError, match="CRLF line endings are not supported"):
            migrate_yaml_text(
                text,
                catalog=catalog,
                registry=registry,
                stop_target=catalog.latest_released.version,
            )


class TestRetiredTokensInsideAList:
    """A retired token authored as a *list item*
    (`single_series_palette: [category_dark.blue]`, `palette:
    [category_dark[2], "#abc"]`).

    Fixed in memory: `_schema_path_exists` now reads a bare
    `{"type": "array"}` branch (no `items` key -- exactly what the
    generated schema renders for these authoring-shorthand unions) as
    "accepts any item" per JSON Schema's own default, not "accepts
    nothing"; `_move_value` gained a list-index write path for the
    identity-only case a trailing wildcard produces. `moves()` now
    declares the wildcard Move over `palette`/`single_series_palette`
    list items everywhere the bare form is declared.

    Respells on disk too, for the block-style shape every migration
    fixture actually writes: `set_board_values` (`yaml_patch.py`) can
    descend *through* a numeric sequence-index segment to reach a nested
    leaf (`rows.0.cols.1.title`), and now rewrites in place when the
    numeric segment is itself a leaf addressing a plain scalar item --
    `_rewrite_scalar_sequence_item` parses the item's own line (indent,
    quote style or its absence, trailing comment) and substitutes only
    the value. Still refused: a **flow-style** list -- a different code
    path entirely (`_is_sequence_block` never recognizes single-line
    `[...]` as a sequence block, so the numeric segment falls through to
    an ordinary, failing key lookup on `single_series_palette` itself,
    "is not a mapping") -- and anything that isn't a single-line plain
    scalar item (a nested mapping item, a multi-line one), which keeps
    the original "addresses a whole sequence item, not a scalar leaf"
    refusal as the safety net. Direct unit coverage of the rewrite
    (byte-for-byte, quote styles, trailing comments, the still-refused
    shapes) lives in `test_yaml_patch.py::TestScalarSequenceItemLeafRewrite`;
    the tests here pin the migration's own use of it end to end.
    """

    def test_in_memory_single_item_list_respells(self) -> None:
        mapping: dict[str, Any] = {
            "title": "t",
            "style": {
                "charts": {
                    "color": {
                        "categorical": {"single_series_palette": ["category_dark.blue"]}
                    }
                }
            },
            "charts": {"c": {"type": "bar", "query": "q", "x": "a", "y": "b"}},
            "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
            "rows": ["c"],
        }

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = prepare_board_mapping(dict(mapping))

        assert result["style"]["charts"]["color"]["categorical"][
            "single_series_palette"
        ] == ["category.blue.dark"]
        assert any("migrated this YAML" in str(w.message) for w in caught)

    def test_in_memory_mixed_two_item_list_respells_only_the_token(self) -> None:
        """`palette: [category_dark[2], "#abc"]` -- the retired token
        respells; the literal hex is left exactly as authored, since it is
        not a key `RETIRED_TOKEN_VALUE_MAP` has ever heard of."""
        mapping: dict[str, Any] = {
            "title": "t",
            "style": {
                "charts": {
                    "color": {"categorical": {"palette": ["category_dark[2]", "#abc"]}}
                }
            },
            "charts": {"c": {"type": "bar", "query": "q", "x": "a", "y": "b"}},
            "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
            "rows": ["c"],
        }

        result = prepare_board_mapping(dict(mapping))

        assert result["style"]["charts"]["color"]["categorical"]["palette"] == [
            "category[2].dark",
            "#abc",
        ]

    def test_dct_migrate_respells_a_block_style_list_item(self) -> None:
        catalog, registry = _board_migration_context()
        text = _board(
            "style:\n"
            "  charts:\n"
            "    color:\n"
            "      categorical:\n"
            "        single_series_palette:\n"
            "          - category_dark.blue  # brand ink\n"
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query: q\n"
            "    x: a\n"
            "    y: b\n"
        )

        result = migrate_yaml_text(
            text,
            catalog=catalog,
            registry=registry,
            stop_target=catalog.latest_released.version,
        )

        assert "- category.blue.dark  # brand ink" in result
        assert "category_dark" not in result

    def test_on_disk_flow_style_list_item_is_refused_not_corrupted(self) -> None:
        catalog, registry = _board_migration_context()
        text = _board(
            "style:\n"
            "  charts:\n"
            "    color:\n"
            "      categorical:\n"
            "        single_series_palette: [category_dark.blue]\n"
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query: q\n"
            "    x: a\n"
            "    y: b\n"
        )

        with pytest.raises(ValueError, match=r"is not a mapping"):
            migrate_yaml_text(
                text,
                catalog=catalog,
                registry=registry,
                stop_target=catalog.latest_released.version,
            )


def test_no_categorical_palette_exceeds_the_respell_slot_ceiling() -> None:
    """`RETIRED_TOKEN_VALUE_MAP`'s bracket/dot-slot forms are enumerated up
    to `MAX_CATEGORICAL_SLOTS` -- this must fail loud, not silently
    under-cover, the day a categorical palette ships more stops than that."""
    from dbt_charts.core.compile.migrations.versions.v0_9_0 import (
        MAX_CATEGORICAL_SLOTS,
    )
    from dbt_charts.core.compile.resolve.style.palette import list_palettes, palette

    for name in list_palettes("categorical"):
        assert len(palette(name)) <= MAX_CATEGORICAL_SLOTS, name


def test_value_map_is_total_over_the_worksheet_retired_replacement_table() -> None:
    """Spot-check every row of the worksheet's retired -> replacement table
    resolves through `RETIRED_TOKEN_VALUE_MAP` exactly as written there."""
    assert RETIRED_TOKEN_VALUE_MAP["category_dark[2]"] == "category[2].dark"
    assert RETIRED_TOKEN_VALUE_MAP["category_dark.blue"] == "category.blue.dark"
    assert RETIRED_TOKEN_VALUE_MAP["category_ghost[3]"] == "category[3].pale"
    assert RETIRED_TOKEN_VALUE_MAP["category_ghost.sage"] == "category.sage.pale"
    assert RETIRED_TOKEN_VALUE_MAP["category_ink[9]"] == "category[9].deep"
    assert RETIRED_TOKEN_VALUE_MAP["category_ink.gray"] == "category.gray.deep"
    assert RETIRED_TOKEN_VALUE_MAP["vivid-10-dark.4"] == "vivid-10.4.dark"
    assert RETIRED_TOKEN_VALUE_MAP["editorial-10-ghost.5"] == "editorial-10.5.pale"
    assert RETIRED_TOKEN_VALUE_MAP["editorial-10-ink"] == "editorial-10.deep"
    assert RETIRED_TOKEN_VALUE_MAP["vivid-10-dark:4"] == "vivid-10:4.dark"
    assert RETIRED_TOKEN_VALUE_MAP["vivid-10-dark_r"] == "vivid-10_r.dark"
    assert RETIRED_TOKEN_VALUE_MAP["editorial-10-ink:10_r"] == "editorial-10:10_r.deep"


class TestSchemaPathExistsBareArrayBranch:
    """`_schema_path_exists`'s trailing-`*` case, on a branch shaped exactly
    like the authoring-shorthand unions `palette`/`single_series_palette`
    render as: `{"type": "array"}`, no `items` key. JSON Schema's own
    default for an absent `items` keyword is "accepts any item" -- not
    "accepts nothing" -- so a wildcard destination inside one must be
    provable."""

    def test_bare_array_branch_with_no_items_key_accepts_a_wildcard(self) -> None:
        from dbt_charts.core.compile.migrations.migrations import _schema_path_exists

        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "stops": {
                    "anyOf": [
                        {"type": "array"},
                        {"type": "string"},
                    ]
                }
            },
        }
        assert _schema_path_exists(schema, ("stops", "*"))

    def test_array_branch_with_a_real_items_schema_is_unaffected(self) -> None:
        """The new bare-array fallback must not paper over a genuinely
        absent destination when `items` names one explicitly."""
        from dbt_charts.core.compile.migrations.migrations import _schema_path_exists

        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "stops": {"type": "array", "items": {"type": "integer"}},
            },
        }
        # The wildcard itself always exists (there is an items schema)...
        assert _schema_path_exists(schema, ("stops", "*"))
        # ...but a further segment past a scalar items schema does not.
        assert not _schema_path_exists(schema, ("stops", "*", "nested"))

    def test_a_dict_branch_with_no_additional_properties_still_refuses(self) -> None:
        """The fallback is scoped to `type: array` branches only -- a closed
        object (no `additionalProperties`, not an array) must not start
        admitting a wildcard it never declared."""
        from dbt_charts.core.compile.migrations.migrations import _schema_path_exists

        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"fixed": {"type": "object", "properties": {"a": {}}}},
        }
        assert not _schema_path_exists(schema, ("fixed", "*"))
