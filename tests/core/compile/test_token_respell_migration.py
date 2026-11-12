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
    migrate_board_yaml_text,
    migrate_yaml_text,
    package_schemas,
)

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

    def test_a_board_matching_both_identity_predicates_respells_both(self) -> None:
        """A retired `theme:` name and a retired categorical token on one
        board are both respelled in the one pass."""
        mapping: dict[str, Any] = {
            "title": "t",
            "theme": "solid",
            "style": {"palettes": {"category": "vivid-10-dark"}},
            "charts": {"c": {"type": "bar", "query": "q", "x": "a", "y": "b"}},
            "queries": {"q": {"columns": ["a", "b"], "values": [[1, 2]]}},
            "rows": ["c"],
        }

        result = prepare_board_mapping(dict(mapping))

        assert result["theme"] == "clarity"
        assert result["style"]["palettes"]["category"] == "vivid-10.dark"

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
            result = prepare_board_mapping(dict(patch))

        assert result == {"style": {"palettes": {"category": "vivid-10"}}}
        assert any("category_dark" in str(w.message) for w in caught)

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
            result["tabs"]["items"][0]["cols"][0]["style"]["marks"]["bar"]["color"]
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
            stop_target=package_schemas().latest_released.version,
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
            stop_target=package_schemas().latest_released.version,
        )

        assert f'palette: "{replacement}"' in result

    def test_dct_migrate_strikes_the_retired_role_keys(self) -> None:
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
                stop_target=package_schemas().latest_released.version,
            )

        assert "category_dark" not in result
        assert "category: vivid-10" in result

    def test_a_crlf_file_is_refused_not_corrupted(self) -> None:
        """`set_board_values`' own CRLF contract: refuse loudly rather than
        silently mix line endings. Reached through the MapKeyDeletion/Move
        text rewrite the same as any other edit that file would need."""
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
                stop_target=package_schemas().latest_released.version,
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
            stop_target=package_schemas().latest_released.version,
        )

        assert "- category.blue.dark  # brand ink" in result
        assert "category_dark" not in result

    def test_on_disk_flow_style_list_item_is_refused_not_corrupted(self) -> None:
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
                stop_target=package_schemas().latest_released.version,
            )


class TestSchemaPathExistsBareArrayBranch:
    """`_schema_path_exists`'s trailing-`*` case, on a branch shaped exactly
    like the authoring-shorthand unions `palette`/`single_series_palette`
    render as: `{"type": "array"}`, no `items` key. JSON Schema's own
    default for an absent `items` keyword is "accepts any item" -- not
    "accepts nothing" -- so a wildcard destination inside one must be
    provable."""


class TestCategoryColorPins:
    """`style.charts.category_colors.<field>.values.<label>` holds color tokens
    under author-named data-value keys; a retired 0.9-era spelling there is
    respelled at the 0.9.0 boundary like any other color field."""
