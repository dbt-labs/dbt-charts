"""Tests for the comment-preserving YAML path setter.

Tests verify that:
1. Top-level scalars can be set, replaced, or deleted without disturbing
   unrelated lines (comments, blank lines, key order).
2. Nested dot-path leaves (`style.frame.width`) can be set, creating
   intermediate block mappings when needed, and appending to existing blocks.
3. Deleting via `None` removes an existing key; an absent key is a no-op.
4. Values that need YAML quoting round-trip through `yaml.safe_load` to the
   exact Python value that was set.
5. Constructs the setter can't handle safely (flow-style parents, anchors/
   aliases, duplicate keys, non-mapping intermediate values) raise
   `ValueError` rather than silently falling back to a whole-document dump.
"""

import pytest
import yaml

from dbt_charts.core.compile.authoring.yaml_patch import (
    rename_key_at_path,
    set_board_values,
)


class TestSetTopLevelScalar:
    def test_replaces_existing_value(self) -> None:
        original = "title: Old Title\n# a comment\nrows: []\n"
        result = set_board_values(original, {"title": "New Title"})
        assert result == 'title: "New Title"\n# a comment\nrows: []\n'

    def test_unrelated_lines_stay_byte_identical(self) -> None:
        original = "title: Old\n# keep me\n\nnotes: A dashboard\nrows: []\n"
        result = set_board_values(original, {"notes": "New notes"})
        expected = 'title: Old\n# keep me\n\nnotes: "New notes"\nrows: []\n'
        assert result == expected

    def test_inserts_new_key_at_front_of_document(self) -> None:
        original = "title: Existing\nrows: []\n"
        result = set_board_values(original, {"theme": "neon"})
        assert result == 'theme: "neon"\ntitle: Existing\nrows: []\n'

    def test_inserts_new_key_after_leading_document_marker(self) -> None:
        original = "---\ntitle: Existing\n"
        result = set_board_values(original, {"theme": "neon"})
        assert result == '---\ntheme: "neon"\ntitle: Existing\n'

    def test_deletes_existing_key(self) -> None:
        original = "theme: neon\ntitle: Existing\n"
        result = set_board_values(original, {"theme": None})
        assert result == "title: Existing\n"

    def test_deleting_absent_key_is_noop(self) -> None:
        original = "title: Existing\nrows: []\n"
        result = set_board_values(original, {"theme": None})
        assert result == original

    def test_tolerates_trailing_non_yaml_content(self) -> None:
        # Cloud markdown boards store `title: X\n\nBody prose.\n` -- the body
        # isn't YAML at all, so the self-check can't `yaml.safe_load` the
        # whole file. Only the recognized front matter is touched/verified.
        original = "title: Revenue\n\nBody.\n"
        result = set_board_values(original, {"theme": "neon"})
        assert result == 'theme: "neon"\ntitle: Revenue\n\nBody.\n'

    def test_a_bulleted_body_is_not_mistaken_for_the_keys_value(self) -> None:
        """A key that already carries an inline value owns no block sequence.

        The sequence walk skips blanks and comments looking for the first `- `
        line, and an unfenced markdown body's bullet list is exactly that — so
        rewriting `title:` spliced the heading and the bullets away, and
        deleting it emptied the file, both reported as success.

        The leaf splice now requires the first item on the line right after the
        key, so `title:`'s own line is all it claims and the body survives the
        edit. The self-check then reads the document back, cannot load bullets
        hanging off a mapping, and refuses — the file is never written. Both
        halves matter: the splice is what stopped destroying, and the refusal is
        what keeps a board nobody can parse from being reported as saved.
        """
        prose = "title: Revenue\n\n# Heading\n\n- bullet one\n- bullet two\n\nEnd.\n"
        body = "\n# Heading\n\n- bullet one\n- bullet two\n\nEnd.\n"
        # Deleting the only key leaves no mapping for the self-check to read
        # back, so the body passes through untouched — the splice took one line.
        assert set_board_values(prose, {"title": None}) == body
        for update in ({"title": "New"}, {"description": "D"}):
            with pytest.raises(ValueError, match="not valid YAML"):
                set_board_values(prose, update)

    def test_fenced_front_matter_insert_lands_inside_the_fence(self) -> None:
        # The canonical markdown-board format fences the YAML between two
        # `---` lines; a new key must go inside the fence, and the closing
        # fence + prose body must pass through untouched.
        original = "---\ntitle: Revenue\n---\n\n# Heading\n\nProse body.\n"
        result = set_board_values(original, {"theme": "neon"})
        assert (
            result
            == '---\ntheme: "neon"\ntitle: Revenue\n---\n\n# Heading\n\nProse body.\n'
        )

    def test_fenced_front_matter_replace_and_delete(self) -> None:
        original = '---\ntheme: "neon"\ntitle: Revenue\n---\nProse.\n'
        replaced = set_board_values(original, {"theme": "stark"})
        assert replaced == '---\ntheme: "stark"\ntitle: Revenue\n---\nProse.\n'
        deleted = set_board_values(original, {"theme": None})
        assert deleted == "---\ntitle: Revenue\n---\nProse.\n"

    def test_multiple_updates_in_one_call(self) -> None:
        original = "title: Old\ndescription: Old desc\nrows: []\n"
        result = set_board_values(
            original, {"title": "New", "description": None, "theme": "neon"}
        )
        assert result == 'theme: "neon"\ntitle: "New"\nrows: []\n'


class TestSetNestedPath:
    def test_sets_leaf_when_parent_block_exists(self) -> None:
        original = "title: X\nstyle:\n  background: white\n"
        result = set_board_values(original, {"style.accent": "blue"})
        assert result == ('title: X\nstyle:\n  background: white\n  accent: "blue"\n')

    def test_creates_intermediate_blocks_when_missing(self) -> None:
        original = "title: X\n"
        result = set_board_values(original, {"style.frame.width": 800})
        assert result == ("style:\n  frame:\n    width: 800\ntitle: X\n")

    def test_appends_leaf_to_existing_nested_block(self) -> None:
        original = "style:\n  frame:\n    width: 800\n"
        result = set_board_values(original, {"style.frame.min_height": 400})
        assert result == "style:\n  frame:\n    width: 800\n    min_height: 400\n"

    def test_replaces_existing_nested_leaf(self) -> None:
        original = "style:\n  frame:\n    width: 800\n  background: white\n"
        result = set_board_values(original, {"style.frame.width": 900})
        assert result == ("style:\n  frame:\n    width: 900\n  background: white\n")

    def test_deletes_existing_nested_leaf(self) -> None:
        original = "style:\n  frame:\n    width: 800\n    min_height: 400\n"
        result = set_board_values(original, {"style.frame.min_height": None})
        assert result == "style:\n  frame:\n    width: 800\n"

    def test_deleting_absent_nested_leaf_is_noop(self) -> None:
        original = "style:\n  frame:\n    width: 800\n"
        result = set_board_values(original, {"style.frame.min_height": None})
        assert result == original


class TestDeleteEmptiedParents:
    """A parent left with no keys parses back as `None`, not as absent.

    `font:` with nothing under it loads as `font: None`, which every model
    that types the field as a sub-model rejects on load -- so clearing the
    last control in a nested block breaks the board it was cleared from.
    The self-check can't see it: it walks to `None` and reads that as
    "deleted", which is exactly what was asked for.
    """

    def test_prunes_every_parent_the_delete_empties(self) -> None:
        original = "title: X\nstyle:\n  title:\n    font:\n      size: 12\n"
        result = set_board_values(original, {"style.title.font.size": None})
        assert result == "title: X\n"
        assert yaml.safe_load(result) == {"title": "X"}

    def test_stops_at_the_first_parent_with_another_key(self) -> None:
        original = "style:\n  title:\n    font:\n      size: 12\n    align: left\n"
        result = set_board_values(original, {"style.title.font.size": None})
        assert result == "style:\n  title:\n    align: left\n"

    def test_a_comment_under_the_parent_is_not_content(self) -> None:
        original = "style:\n  font:\n    # the only note\n    size: 12\ntitle: X\n"
        result = set_board_values(original, {"style.font.size": None})
        # Kept, at its old indent: an orphaned comment is a smaller loss than
        # deleting a line the author wrote.
        assert result == "    # the only note\ntitle: X\n"

    def test_refuses_when_pruning_would_empty_a_sequence_item(self) -> None:
        original = "rows:\n  - style:\n      font:\n        size: 12\n"
        with pytest.raises(ValueError, match="renumber"):
            set_board_values(original, {"rows.0.style.font.size": None})

    def test_a_sequence_written_at_its_key_column_is_not_read_as_empty(self) -> None:
        # 75 boards in this repo author `rows:` with its items at column 0. The
        # ordinary block extent stops at the first line indented back to the
        # key, so it sees none of them -- the descent already asks
        # `_sequence_span` first, and the prune has to ask the same question or
        # it reads a full board as an emptied parent and deletes `rows:`.
        original = "rows:\n- title: A\n  style:\n    font:\n      size: 12\n"
        result = set_board_values(original, {"rows.0.style.font.size": None})
        assert result == "rows:\n- title: A\n"
        assert yaml.safe_load(result) == {"rows": [{"title": "A"}]}

    def test_an_emptied_item_head_is_promoted_when_the_item_has_other_keys(
        self,
    ) -> None:
        # Emptying `style:` here costs the item nothing: `title` still holds it
        # open, so no index moves and there is nothing to refuse.
        original = "rows:\n  - style:\n      font:\n        size: 12\n    title: Y\n"
        result = set_board_values(original, {"rows.0.style.font.size": None})
        assert result == "rows:\n  - title: Y\n"
        assert yaml.safe_load(result) == {"rows": [{"title": "Y"}]}


class TestQuotingRoundTrips:
    @pytest.mark.parametrize(
        "value",
        ["1", "yes", "on", "null", "a: b", "a # b", "true", ""],
    )
    def test_ambiguous_strings_round_trip_exactly(self, value: str) -> None:
        original = "title: X\n"
        result = set_board_values(original, {"title": value})
        loaded = yaml.safe_load(result)
        assert loaded["title"] == value

    @pytest.mark.parametrize("value", [1, 800, 3.5, True, False])
    def test_non_string_scalars_round_trip_exactly(self, value: object) -> None:
        original = "title: X\n"
        result = set_board_values(original, {"card_gap": value})
        loaded = yaml.safe_load(result)
        assert loaded["card_gap"] == value
        assert type(loaded["card_gap"]) is type(value)

    def test_ambiguous_list_items_round_trip_exactly(self) -> None:
        result = set_board_values("y: revenue\n", {"y": ["yes", "1"]})
        assert yaml.safe_load(result)["y"] == ["yes", "1"]

    def test_a_long_list_stays_on_one_line(self) -> None:
        # PyYAML's emitter wraps a flow sequence at 80 columns, and the setter
        # splices what it renders into a single line — a wrapped sequence puts
        # the continuation where the next key belongs. A multi-series `y:` of
        # real column names passes 80 characters easily.
        columns = [f"revenue_by_region_{n}" for n in range(6)]
        result = set_board_values("y: revenue\n", {"y": columns})
        assert result.count("\n") == 1
        assert yaml.safe_load(result)["y"] == columns

    def test_a_list_replaces_an_indented_block_sequence(self) -> None:
        # How the value being *replaced* is written, not the new one. Every
        # table chart in `examples/playground/charts/cards/pivot-tables.yml`
        # authors its pivot channels this way, and the extent check reads the
        # item lines as a nested mapping under the key.
        original = "charts:\n  t:\n    type: table\n    rows:\n      - region\n"
        result = set_board_values(original, {"charts.t.rows": ["product", "sku"]})
        assert yaml.safe_load(result)["charts"]["t"]["rows"] == ["product", "sku"]
        assert "region" not in result

    def test_a_list_replaces_a_sequence_at_its_keys_own_column(self) -> None:
        # The other block-sequence spelling, and the worse one: the ordinary
        # extent stops before the first item, so the key line was rewritten and
        # the items left behind as an orphaned sequence.
        original = "y:\n- revenue\n- cost\ntitle: T\n"
        result = set_board_values(original, {"y": ["margin"]})
        assert yaml.safe_load(result) == {"y": ["margin"], "title": "T"}

    def test_clearing_a_block_sequence_removes_its_items(self) -> None:
        original = "title: T\ny:\n  - revenue\n  - cost\n"
        assert set_board_values(original, {"y": None}) == "title: T\n"


class TestBlockScalarLeaves:
    # A block scalar is a scalar spelled over lines — `description: |` is how a
    # value containing `: ` has to be authored, and the Design panel edits
    # descriptions. The extent check used to read the indented body as a nested
    # mapping and refuse the save.

    def test_set_replaces_a_literal_block_scalar(self) -> None:
        original = (
            "charts:\n"
            "  visitors:\n"
            "    type: line\n"
            "    description: |\n"
            "      Data source: Segment\n"
            "    x: week\n"
        )
        result = set_board_values(original, {"charts.visitors.description": "New"})
        parsed = yaml.safe_load(result)["charts"]["visitors"]
        assert parsed["description"] == "New"
        assert parsed["x"] == "week"
        assert "Segment" not in result

    def test_set_replaces_a_folded_block_scalar_with_chomping(self) -> None:
        original = "description: >-\n  A long\n  folded line\ntitle: T\n"
        result = set_board_values(original, {"description": "Short"})
        assert yaml.safe_load(result) == {"description": "Short", "title": "T"}

    def test_delete_removes_a_block_scalar_and_its_body(self) -> None:
        original = "description: |\n  Data source: Segment\ntitle: T\n"
        assert set_board_values(original, {"description": None}) == "title: T\n"

    def test_set_on_an_item_head_block_scalar_keeps_the_dash(self) -> None:
        original = "rows:\n  - text: |\n      Some prose here.\n    title: A\n"
        result = set_board_values(original, {"rows.0.text": "Terse"})
        assert yaml.safe_load(result)["rows"] == [{"text": "Terse", "title": "A"}]

    def test_a_blank_line_after_the_body_survives_the_edit(self) -> None:
        original = "description: |\n  Old text\n\ntitle: T\n"
        result = set_board_values(original, {"description": "New"})
        assert result == 'description: "New"\n\ntitle: T\n'


class TestUnsupportedConstructsRaise:
    def test_flow_style_parent_mapping_raises(self) -> None:
        original = "style: {frame: {width: 800}}\n"
        with pytest.raises(ValueError, match="flow"):
            set_board_values(original, {"style.frame.width": 900})

    def test_anchor_raises(self) -> None:
        original = "style: &base\n  background: white\n"
        with pytest.raises(ValueError, match="anchor"):
            set_board_values(original, {"style.background": "blue"})

    def test_alias_raises(self) -> None:
        original = "base: &b white\nstyle:\n  background: *b\n"
        with pytest.raises(ValueError, match="alias"):
            set_board_values(original, {"style.background": "blue"})

    def test_duplicate_key_on_path_raises(self) -> None:
        original = "title: A\ntitle: B\n"
        with pytest.raises(ValueError, match="duplicate"):
            set_board_values(original, {"title": "C"})

    def test_non_mapping_intermediate_value_raises(self) -> None:
        original = "style: solid\n"
        with pytest.raises(ValueError, match="mapping"):
            set_board_values(original, {"style.frame.width": 900})

    def test_delete_of_key_owning_nested_mapping_raises(self) -> None:
        # Deleting only the parent line would orphan its children into
        # top-level keys — scalar-leaves-only applies to deletes too.
        original = "style:\n  frame:\n    width: 800\n"
        with pytest.raises(ValueError, match="nested mapping"):
            set_board_values(original, {"style": None})

    def test_delete_of_nested_key_owning_mapping_raises(self) -> None:
        original = "style:\n  frame:\n    width: 800\n  bg: white\n"
        with pytest.raises(ValueError, match="nested mapping"):
            set_board_values(original, {"style.frame": None})

    def test_malformed_front_matter_raises_value_error(self) -> None:
        # The post-edit self-check reparse must surface as the documented
        # ValueError contract, not a bare yaml.YAMLError that escapes the
        # Cloud callsite's designed error handling.
        original = "title: [unclosed\n"
        with pytest.raises(ValueError, match="valid YAML"):
            set_board_values(original, {"description": "x"})

    def test_crlf_input_raises(self) -> None:
        # Editing CRLF content would emit LF-only lines and silently mix
        # line endings — reject rather than corrupt.
        original = "title: Old\r\ncard_gap: false\r\n"
        with pytest.raises(ValueError, match="CRLF"):
            set_board_values(original, {"title": "New"})


class TestNoWholeDocumentDump:
    def test_result_is_not_reformatted_by_a_dump_round_trip(self) -> None:
        # A dumped-and-reloaded document would drop the comment and reorder
        # nothing observable here, so assert on the literal preserved text
        # instead of introspecting the implementation.
        original = "# leading comment\ntitle: Old\nrows:\n  - a\n  - b\n"
        result = set_board_values(original, {"title": "New"})
        assert result == '# leading comment\ntitle: "New"\nrows:\n  - a\n  - b\n'


class TestLongValues:
    def test_long_string_stays_on_one_line(self) -> None:
        # PyYAML's emitter wraps long quoted scalars at width=80 by default;
        # a wrapped value would corrupt the single-line splice.
        long_title = "A dashboard title that goes on and on " * 6  # ~230 chars
        original = "title: Old\nrows: []\n"
        result = set_board_values(original, {"title": long_title})
        assert len(result.split("\n")) == len(original.split("\n"))
        assert yaml.safe_load(result)["title"] == long_title

    def test_a_newline_inside_a_list_item_is_refused_like_a_bare_one(self) -> None:
        """The guard was on the string arm, and the list arm walked past it.

        `["a\\nb"]` dumps as three lines from a writer whose whole contract is a
        single-line splice — the extra lines land where the next key belongs,
        and the refusal that already covers `"a\\nb"` is the same refusal.
        """
        original = "rows:\n  - title: T\n    type: bar\n    query: q\n    y: revenue\n"
        with pytest.raises(ValueError, match="multi-line"):
            set_board_values(original, {"rows.0.y": ["revenue", "co\nst"]})

    def test_a_bare_multi_line_string_is_refused_too(self) -> None:
        """The refusal the list arm borrows, asked of the value it came from.

        `default_style='"'` escapes the newline, so the rendered line is
        single-line and a check on the output cannot see it — leaving a writer
        that refused one spelling of a multi-line value and quietly rewrote the
        other into an escape the author never typed.
        """
        with pytest.raises(ValueError, match="multi-line"):
            set_board_values("title: Old\nrows: []\n", {"title": "one\ntwo"})

    def test_long_string_on_nested_path(self) -> None:
        long_value = "x" * 300
        original = "style:\n  background: white\n"
        result = set_board_values(original, {"style.accent": long_value})
        assert yaml.safe_load(result)["style"]["accent"] == long_value


class TestRenameKeyAtPath:
    """rename_key_at_path edits only the key token on its own line, so --
    unlike set_board_values -- it isn't restricted to scalar leaves: a key
    that opens a nested block never needs its content relocated or
    reindented, only the one line spelling its name."""

    def test_renames_scalar_leaf_key(self) -> None:
        original = "title: Old\nband_position: left\nrows: []\n"
        result = rename_key_at_path(original, "band_position", "position")
        assert result == "title: Old\nposition: left\nrows: []\n"

    def test_renames_block_valued_key_leaving_nested_content_untouched(self) -> None:
        original = (
            "axis_x:\n"
            "  fill: zero\n"
            "  label:\n"
            "    overlap:\n"
            "      skip: false\n"
            "axis_y:\n"
            "  fill: null\n"
        )
        result = rename_key_at_path(original, "axis_x.label", "labels")
        assert result == (
            "axis_x:\n"
            "  fill: zero\n"
            "  labels:\n"
            "    overlap:\n"
            "      skip: false\n"
            "axis_y:\n"
            "  fill: null\n"
        )

    def test_renames_nested_dot_path_key(self) -> None:
        original = "charts:\n  bar1:\n    style:\n      axis_x:\n        label:\n          angle: 45\n"
        result = rename_key_at_path(
            original, "charts.bar1.style.axis_x.label", "labels"
        )
        assert result == (
            "charts:\n  bar1:\n    style:\n      axis_x:\n        labels:\n          angle: 45\n"
        )

    def test_unrelated_lines_stay_byte_identical(self) -> None:
        original = (
            "# a comment above\n"
            "axis_x:\n"
            "  label:\n"
            "    format: auto\n"
            "  # a comment inside\n"
            "  grid:\n"
            "    visible: true\n"
        )
        result = rename_key_at_path(original, "axis_x.label", "labels")
        assert result == (
            "# a comment above\n"
            "axis_x:\n"
            "  labels:\n"
            "    format: auto\n"
            "  # a comment inside\n"
            "  grid:\n"
            "    visible: true\n"
        )

    def test_preserves_trailing_inline_comment_on_renamed_line(self) -> None:
        original = "axis_x:\n  label: auto  # kept as-is\n"
        result = rename_key_at_path(original, "axis_x.label", "labels")
        assert result == "axis_x:\n  labels: auto  # kept as-is\n"

    def test_missing_key_raises(self) -> None:
        original = "axis_x:\n  grid: {}\n"
        with pytest.raises(ValueError, match="label"):
            rename_key_at_path(original, "axis_x.label", "labels")

    def test_missing_parent_raises(self) -> None:
        original = "axis_y:\n  grid: {}\n"
        with pytest.raises(ValueError, match="axis_x"):
            rename_key_at_path(original, "axis_x.label", "labels")

    def test_sibling_collision_raises(self) -> None:
        original = "axis_x:\n  label:\n    angle: 1\n  labels:\n    angle: 2\n"
        with pytest.raises(ValueError, match="labels"):
            rename_key_at_path(original, "axis_x.label", "labels")

    def test_crlf_input_raises(self) -> None:
        original = "axis_x:\r\n  label: auto\r\n"
        with pytest.raises(ValueError, match="CRLF"):
            rename_key_at_path(original, "axis_x.label", "labels")

    def test_invalid_path_raises(self) -> None:
        with pytest.raises(ValueError, match="dot-path"):
            rename_key_at_path("title: Old\n", "", "labels")


class TestRenameKeyAtPathThroughSequences:
    """A numeric segment descends into a sequence item on the way to the key
    being renamed (`rows.0.description`) -- the same shape `set_board_values`
    supports, and it must resolve through both styles a sequence value can be
    written in: indented under its key, or column-aligned with it (what
    `yaml.dump` emits and what a hand-authored `rows:` at column 0 looks
    like)."""

    def test_renames_a_key_inside_an_indented_sequence_item(self) -> None:
        original = "rows:\n  - title: A\n    description: hi\n"
        result = rename_key_at_path(original, "rows.0.description", "notes")
        assert yaml.safe_load(result)["rows"][0] == {"title": "A", "notes": "hi"}

    def test_renames_a_key_inside_a_column_aligned_sequence_item(self) -> None:
        """The block extent from `rows:` alone stops before every item at
        the key's own column, so a naive descent reports '0' not found."""
        original = "rows:\n- title: A\n  description: hi\n"
        result = rename_key_at_path(original, "rows.0.description", "notes")
        assert yaml.safe_load(result)["rows"][0] == {"title": "A", "notes": "hi"}

    def test_renames_a_key_that_is_the_sequence_item_head(self) -> None:
        """The renamed key sits on the `- ` line itself, so the token to
        rewrite follows the dash rather than starting the line. Only a
        sequence descent can reach this shape, so nothing else covers it."""
        original = "rows:\n  - description: Section notes\n    cols: [c]\n"
        result = rename_key_at_path(original, "rows.0.description", "notes")
        assert yaml.safe_load(result)["rows"][0] == {
            "notes": "Section notes",
            "cols": ["c"],
        }

    def test_renames_a_key_at_a_nested_sequence_position(self) -> None:
        original = (
            "rows:\n"
            "  - cols:\n"
            "      - title: A\n"
            "        description: hi\n"
            "      - title: B\n"
            "        description: bye\n"
        )
        result = rename_key_at_path(original, "rows.0.cols.1.description", "notes")
        parsed = yaml.safe_load(result)
        assert parsed["rows"][0]["cols"][0] == {"title": "A", "description": "hi"}
        assert parsed["rows"][0]["cols"][1] == {"title": "B", "notes": "bye"}

    def test_renames_a_key_whose_value_is_a_block_scalar(self) -> None:
        original = "rows:\n  - title: A\n    description: |\n      Multi\n      line\n"
        result = rename_key_at_path(original, "rows.0.description", "notes")
        parsed = yaml.safe_load(result)
        assert parsed["rows"][0]["notes"] == "Multi\nline\n"

    def test_leaf_numeric_segment_addressing_a_sequence_item_raises(self) -> None:
        """A leaf numeric segment names the whole sequence item (`rows.0`),
        not a key within it -- there is no key token on that line to rename.
        Must fail loud rather than rename the item's own first key by
        accident."""
        original = "rows:\n  - title: A\n    description: hi\n"
        with pytest.raises(ValueError, match="not a key"):
            rename_key_at_path(original, "rows.0", "notes")


class TestSequenceItemPaths:
    """A numeric segment addresses the Nth item of a sequence.

    This is the shape every inline chart has (`rows.0.cols.1.title`) — the
    spelling the renderer stamps, the source map keys, and diagnostics all
    already use. Without it the Design panel can render a control for an
    inline chart and never save it.
    """

    BOARD = (
        "title: Revenue Overview\n"
        "\n"
        "rows:\n"
        "  - cols:\n"
        "      - title: Monthly Revenue   # the shape the docs teach\n"
        "        type: bar\n"
        "        style:\n"
        "          color: palette.1\n"
        "      - title: Customers\n"
        "        type: kpi\n"
    )

    def test_sets_a_scalar_inside_a_sequence_item(self) -> None:
        result = set_board_values(self.BOARD, {"rows.0.cols.0.type": "area"})
        assert 'type: "area"' in result
        assert "- title: Monthly Revenue   # the shape the docs teach" in result

    def test_addresses_the_second_item_not_the_first(self) -> None:
        """The off-by-one this class of walker gets wrong."""
        result = set_board_values(self.BOARD, {"rows.0.cols.1.type": "callout"})
        assert 'type: "callout"' in result
        assert "        type: bar\n" in result  # the first item is untouched

    def test_sets_a_leaf_below_a_nested_mapping_inside_an_item(self) -> None:
        result = set_board_values(self.BOARD, {"rows.0.cols.0.style.color": "red"})
        assert 'color: "red"' in result
        assert "palette.1" not in result

    def test_creates_a_missing_parent_mapping_inside_an_item(self) -> None:
        """The Design panel's characteristic save: overriding an inherited value."""
        result = set_board_values(self.BOARD, {"rows.0.cols.1.style.color": "blue"})
        parsed = yaml.safe_load(result)
        assert parsed["rows"][0]["cols"][1]["style"]["color"] == "blue"
        assert parsed["rows"][0]["cols"][0]["style"]["color"] == "palette.1"

    def test_deletes_a_leaf_inside_an_item(self) -> None:
        result = set_board_values(self.BOARD, {"rows.0.cols.0.style.color": None})
        cols = yaml.safe_load(result)["rows"][0]["cols"]
        # Assert the surviving shape, not just the absence: `"color" not in
        # (... or {})` is equally true when the whole item was destroyed.
        assert len(cols) == 2
        assert cols[0]["title"] == "Monthly Revenue"
        assert cols[1]["title"] == "Customers"
        # `style` held nothing but that color, so it goes too: an empty
        # `style:` parses as None, which the chart model rejects on load.
        assert "style" not in cols[0]

    def test_every_other_line_stays_byte_identical(self) -> None:
        result = set_board_values(self.BOARD, {"rows.0.cols.1.type": "callout"})
        before, after = self.BOARD.split("\n"), result.split("\n")
        differing = [
            i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b
        ]
        assert len(differing) == 1
        assert "type" in before[differing[0]]

    def test_out_of_range_index_raises_naming_the_path(self) -> None:
        with pytest.raises(ValueError, match=r"rows\.0\.cols\.9\.title"):
            set_board_values(self.BOARD, {"rows.0.cols.9.title": "nope"})

    def test_numeric_segment_against_a_mapping_still_resolves_the_key(self) -> None:
        """A mapping key that happens to be digits is a key, not an index."""
        original = "counts:\n  0: zero\n  1: one\n"
        result = set_board_values(original, {"counts.0": "ZERO"})
        assert yaml.safe_load(result)["counts"][0] == "ZERO"

    def test_non_numeric_segment_against_a_sequence_raises(self) -> None:
        with pytest.raises(ValueError, match="rows"):
            set_board_values(self.BOARD, {"rows.cols.title": "nope"})

    def test_flow_style_sequence_raises_rather_than_guessing(self) -> None:
        original = "rows:\n  - cols: [{title: a}]\n"
        with pytest.raises(ValueError, match=r"rows\.0\.cols\.0\.title"):
            set_board_values(original, {"rows.0.cols.0.title": "nope"})


class TestSequenceItemPathRefusals:
    """Shapes the sequence walker must not silently mangle.

    Each was a real defect: the writer produced output that either did not
    parse or quietly meant something else, and the self-check passed anyway.
    """

    def test_sequence_written_at_its_parent_key_indent_is_reached(self) -> None:
        """`yaml.dump` writes items at the key's own column, so this is common.

        Nothing in `_block_extent` looks past a line that dedents to the parent,
        so the whole sequence used to fall outside the block, the digit segment
        was taken for a mapping key, and the setter produced unparseable YAML
        while reporting success.
        """
        original = "rows:\n- cols:\n  - title: A\n"
        result = set_board_values(original, {"rows.0.cols.0.title": "NEW"})
        assert yaml.safe_load(result)["rows"][0]["cols"][0]["title"] == "NEW"

    def test_digit_segment_never_invents_a_mapping_key(self) -> None:
        """No sequence to index means refuse — never create `0:` beside it.

        Creating the key turns a list into a mapping, or writes a second
        structure next to a sequence the walker failed to recognize.
        """
        with pytest.raises(ValueError, match=r"rows\.0\.title"):
            set_board_values("title: T\nrows:\n", {"rows.0.title": "X"})

    def test_deleting_an_items_only_key_raises_rather_than_dropping_the_item(
        self,
    ) -> None:
        """Removing the item would renumber every later index.

        Authoring paths derive from tree position, so a silent renumber
        re-points every later save at a different chart. Refusing is the only
        honest outcome — this setter deletes scalar leaves, not list items.
        """
        original = "rows:\n  - cols:\n      - title: A\n      - type: kpi\n"
        with pytest.raises(ValueError, match=r"rows\.0\.cols\.0\.title"):
            set_board_values(original, {"rows.0.cols.0.title": None})

    def test_deleting_a_block_sequence_head_key_raises_rather_than_corrupting(
        self,
    ) -> None:
        """The value's own lines are part of the key, so the extent must cover
        them.

        `y:` heads its item and its value is a block sequence below it. Measuring
        the key as the single `y:` line left the `- revenue` line behind, and the
        item that had been a mapping silently became a list — `rows[0]` came back
        as `['revenue']`, parseable and wrong. The extent now walks to the end of
        the nested sequence, which lands this on the same refusal as any other
        only-key deletion.
        """
        original = "rows:\n- y:\n  - revenue\n- title: B\n"
        with pytest.raises(ValueError, match=r"rows\.0\.y"):
            set_board_values(original, {"rows.0.y": None})

    def test_deleting_a_block_sequence_beside_another_key_removes_all_its_lines(
        self,
    ) -> None:
        """The same extent, where the deletion is allowed: no orphan `- revenue`."""
        original = "rows:\n- title: A\n  y:\n  - revenue\n  - cost\n- title: B\n"
        result = set_board_values(original, {"rows.0.y": None})
        assert yaml.safe_load(result)["rows"] == [{"title": "A"}, {"title": "B"}]

    def test_deleting_a_non_head_key_still_works(self) -> None:
        """The refusal above is about emptying an item, not about deletion."""
        original = "rows:\n  - cols:\n      - title: A\n        type: kpi\n"
        result = set_board_values(original, {"rows.0.cols.0.type": None})
        chart = yaml.safe_load(result)["rows"][0]["cols"][0]
        assert chart == {"title": "A"}

    def test_deleting_an_item_head_promotes_the_next_key(self) -> None:
        original = "rows:\n  - cols:\n      - title: A\n        type: kpi\n"
        result = set_board_values(original, {"rows.0.cols.0.title": None})
        assert yaml.safe_load(result)["rows"][0]["cols"][0] == {"type": "kpi"}


class TestWhatASequenceValueIsNot:
    """The two shapes the sequence walk must not claim as a key's value.

    Claiming one is not a refused edit — it is a *successful* one that reports
    success while deleting content the caller never addressed. Both were a
    `ValueError` before the walk widened to reach a top-level `rows:`.
    """

    _MARKDOWN = (
        "title: Revenue\n"
        "description:\n"
        "\n"
        "# Heading\n"
        "\n"
        "- bullet one\n"
        "- bullet two\n"
        "\n"
        "End.\n"
    )

    @pytest.mark.parametrize("value", ["X", None])
    def test_an_empty_front_matter_key_does_not_claim_the_prose_below_it(
        self, value: str | None
    ) -> None:
        """An unfenced markdown board's body is prose, not `description:`'s value.

        `description:` carries no inline value, so the walk went looking for a
        block sequence below it — skipped the blank line and the `# Heading`
        (which reads as a comment), and found the body's bullet list. The splice
        then wrote the key, removed the heading and both bullets, and returned
        success.

        Refused, not repaired, and that is the merge base's answer too. YAML
        itself reads those bullets as the key's value — the file says they are —
        so nothing here can tell prose from a sequence written at column 0. What
        it can do is decline to guess: the self-check reparses and the document
        does not load, so the edit never reaches disk.
        """
        with pytest.raises(ValueError, match="not valid YAML"):
            set_board_values(self._MARKDOWN, {"description": value})

    def test_a_sequence_of_mappings_is_refused_not_replaced(self) -> None:
        """The docstring's promise: a nested mapping is a refusal, not a rewrite.

        A layer stack is a sequence *of mappings*. Writing a scalar over it
        replaced the whole stack with one flow line, which is a structural edit
        this setter does not make — and it does not have the caller's consent for
        it, because the caller addressed a leaf.
        """
        original = "charts:\n  c:\n    layers:\n      - type: line\n        y: t\n"
        with pytest.raises(ValueError, match=r"charts\.c\.layers"):
            set_board_values(original, {"charts.c.layers": "boom"})

    def test_deleting_a_sequence_of_mappings_is_refused_too(self) -> None:
        original = "charts:\n  c:\n    layers:\n      - type: line\n        y: t\n"
        with pytest.raises(ValueError, match=r"charts\.c\.layers"):
            set_board_values(original, {"charts.c.layers": None})

    def test_a_sequence_of_scalars_is_still_this_key_s_value(self) -> None:
        """The containment must not cost the shape the walk widened to reach."""
        at_parent_column = "rows:\n- a\n- b\n"
        indented = "charts:\n  c:\n    y:\n      - revenue\n      - cost\n"
        assert yaml.safe_load(set_board_values(at_parent_column, {"rows": ["x"]}))[
            "rows"
        ] == ["x"]
        assert yaml.safe_load(set_board_values(indented, {"charts.c.y": ["x"]}))[
            "charts"
        ]["c"]["y"] == ["x"]

    @pytest.mark.parametrize(
        "item", ["{type: line, y: t}", "{title: A}", "[a, b]", "the key: value"]
    )
    def test_a_mapping_item_yaml_spells_some_other_way_is_refused_too(
        self, item: str
    ) -> None:
        """The rejection is "not a scalar", not "matches a `key:` regex".

        A flow mapping leads with `{`, and a key may hold a space — neither
        matches the key pattern, so both read as scalar items and the splice
        replaced a layer stack with a string, or deleted a whole layout tree,
        while reporting success. The base raised on every one of these.
        """
        original = f"charts:\n  c:\n    type: bar\n    layers:\n    - {item}\n"
        with pytest.raises(ValueError, match=r"charts\.c\.layers"):
            set_board_values(original, {"charts.c.layers": "boom"})
        with pytest.raises(ValueError, match=r"charts\.c\.layers"):
            set_board_values(original, {"charts.c.layers": None})

    def test_a_column_aligned_sequence_of_mappings_is_refused_on_delete(self) -> None:
        """The refusal has to fire from the sequence walk, not from the extent.

        Routing the rejection back through `_block_extent` cannot see a sequence
        at its parent key's own column: every item line is indented back to the
        parent, so the extent is empty, `_has_content` is False, and the delete
        went through — taking the whole layout with it and leaving a document
        that no longer parses.
        """
        board = (
            "rows:\n- title: A\n  type: kpi\n- title: B\n  type: bar\ntitle: Revenue\n"
        )
        with pytest.raises(ValueError, match=r"rows"):
            set_board_values(board, {"rows": None})


class TestASequenceSeparatedFromItsKey:
    """A comment or a blank line between `rows:` and its first item.

    Legal YAML, and the shape `company-overview-tight.yml` ships. The leaf
    branch must not claim what follows a gap — that is how an unfenced markdown
    body gets spliced away — but the *descent* has to walk through it, or the
    panel cannot edit any field on such a board, including fields nowhere near
    the sequence.
    """

    @pytest.mark.parametrize(
        "board",
        [
            "rows:\n# a comment\n- title: A\n  type: bar\n",
            "rows:\n\n- title: A\n  type: bar\n",
            "title: T\nrows:\n# a comment\n- title: A\n  type: bar\n",
            "title: T\nrows:\n\n\n- title: A\n  type: bar\n",
        ],
    )
    def test_the_descent_reaches_the_item_through_the_gap(self, board: str) -> None:
        result = set_board_values(board, {"rows.0.title": "B"})
        assert yaml.safe_load(result)["rows"][0]["title"] == "B"

    def test_the_gap_itself_survives_the_edit(self) -> None:
        board = "rows:\n# keep me\n- title: A\n  type: bar\n"
        assert "# keep me" in set_board_values(board, {"rows.0.title": "B"})

    def test_but_the_leaf_still_refuses_to_claim_across_the_gap(self) -> None:
        """The markdown hazard is the leaf branch's, and it stays closed.

        Matches the key the self-check names explicitly, not whatever the
        underlying YAML parser's error text happens to mention — libyaml and
        the pure-Python scanner word this differently.
        """
        board = "rows:\n# a comment\n- a\n- b\n"
        with pytest.raises(ValueError, match=r"editing rows"):
            set_board_values(board, {"rows": ["x"]})


class TestSequenceItemHeadEdits:
    """Leaf edits landing on the `- ` line itself.

    The item head is where `title:` sits in the shape the docs teach, so it is
    the line most edits target — and the only one whose rewrite has to preserve
    a dash. Every other sequence test addresses a following key, which never
    exercises that path.
    """

    BOARD = (
        "rows:\n"
        "  - cols:\n"
        "      - title: Monthly Revenue   # the item head\n"
        "        type: bar\n"
        "      - title: Customers\n"
        "        type: kpi\n"
    )

    def test_rewriting_an_item_head_keeps_its_dash(self) -> None:
        result = set_board_values(self.BOARD, {"rows.0.cols.0.title": "MRR"})
        cols = yaml.safe_load(result)["rows"][0]["cols"]
        assert [c["title"] for c in cols] == ["MRR", "Customers"]
        assert [c["type"] for c in cols] == ["bar", "kpi"]

    def test_rewriting_the_second_items_head(self) -> None:
        result = set_board_values(self.BOARD, {"rows.0.cols.1.title": "Accounts"})
        cols = yaml.safe_load(result)["rows"][0]["cols"]
        assert [c["title"] for c in cols] == ["Monthly Revenue", "Accounts"]

    def test_deleting_an_item_head_leaves_the_item_and_its_siblings(self) -> None:
        result = set_board_values(self.BOARD, {"rows.0.cols.0.title": None})
        cols = yaml.safe_load(result)["rows"][0]["cols"]
        assert len(cols) == 2
        assert cols[0] == {"type": "bar"}
        assert cols[1]["title"] == "Customers"

    def test_a_digit_leaf_addresses_an_item_not_a_scalar(self) -> None:
        with pytest.raises(ValueError, match=r"rows\.0\.cols\.0"):
            set_board_values(self.BOARD, {"rows.0.cols.0": "nope"})


class TestBlockScalarHashBodyLines:
    def test_a_hash_leading_body_line_is_replaced_not_orphaned(self) -> None:
        # Inside a block scalar a `#` line is content. The walk-back once
        # treated it as a trailing comment and left it behind as a stray
        # comment line that accumulated on every subsequent edit.
        original = (
            "title: Board\ndescription: |\n  Intro\n\n  ## Next section\ntheme: cream\n"
        )
        result = set_board_values(original, {"description": "Short"})
        assert result == 'title: Board\ndescription: "Short"\ntheme: cream\n'

    def test_delete_takes_the_hash_body_lines_with_it(self) -> None:
        original = "description: |\n  Intro\n  ## Heading\ntitle: T\n"
        assert set_board_values(original, {"description": None}) == "title: T\n"

    def test_a_dedented_comment_after_the_body_survives(self) -> None:
        # A `#` line at the key's own indent (or shallower) is a real comment
        # about the next key, not body — it must survive both arms.
        original = (
            "title: Board\n"
            "description: |\n"
            "  Intro\n"
            "# a comment about theme\n"
            "theme: paper\n"
        )
        result = set_board_values(original, {"description": "Short"})
        assert result == (
            'title: Board\ndescription: "Short"\n# a comment about theme\ntheme: paper\n'
        )
        deleted = set_board_values(original, {"description": None})
        assert deleted == "title: Board\n# a comment about theme\ntheme: paper\n"

    def test_a_comment_shallower_than_a_deep_body_survives(self) -> None:
        # The body/comment boundary is the block BODY's indent, not the key's:
        # with an extra-indented body, a `#` line between the key's indent and
        # the body's is a comment (YAML ends the scalar there), not content.
        original = (
            "title: X\ndescription: |\n      deep body\n  # a real comment\nother: 1\n"
        )
        result = set_board_values(original, {"description": "Short"})
        assert (
            result == 'title: X\ndescription: "Short"\n  # a real comment\nother: 1\n'
        )
        deleted = set_board_values(original, {"description": None})
        assert deleted == "title: X\n  # a real comment\nother: 1\n"


class TestScalarSequenceItemLeafRewrite:
    """A leaf numeric segment (`palette.0`) addressing a block-sequence item
    whose own value is a plain scalar -- `_sequence_item_span` already
    finds the item's span; this rewrites just its value in place, the same
    comment-preserving contract the rest of this module keeps. A flow-style
    list still refuses (a different code path entirely -- `_is_sequence_block`
    never recognizes `[...]` on the key's own line), and anything that
    isn't a single-line plain scalar item (a nested mapping, a multi-line
    item) falls back to the existing refusal."""

    def test_block_style_item_rewrites_byte_for_byte_except_the_value(self) -> None:
        original = 'palette:\n  - category_dark.blue\n  - "#abc"\n'
        result = set_board_values(original, {"palette.0": "category.blue.dark"})
        assert result == 'palette:\n  - category.blue.dark\n  - "#abc"\n'

    def test_flow_style_list_still_refuses_with_the_pinned_message(self) -> None:
        original = "palette: [category_dark.blue]\n"
        with pytest.raises(ValueError, match="is not a mapping"):
            set_board_values(original, {"palette.0": "category.blue.dark"})

    def test_a_trailing_comment_survives_the_rewrite(self) -> None:
        original = "palette:\n  - category_dark.blue  # the brand blue\n"
        result = set_board_values(original, {"palette.0": "category.blue.dark"})
        assert result == "palette:\n  - category.blue.dark  # the brand blue\n"

    def test_a_double_quoted_item_keeps_its_quotes(self) -> None:
        original = 'palette:\n  - "category_dark.blue"\n'
        result = set_board_values(original, {"palette.0": "category.blue.dark"})
        assert result == 'palette:\n  - "category.blue.dark"\n'

    def test_a_single_quoted_item_keeps_its_quotes(self) -> None:
        original = "palette:\n  - 'category_dark.blue'\n"
        result = set_board_values(original, {"palette.0": "category.blue.dark"})
        assert result == "palette:\n  - 'category.blue.dark'\n"

    def test_the_second_item_rewrites_not_the_first(self) -> None:
        original = "palette:\n  - vivid-10\n  - category_dark.blue\n"
        result = set_board_values(original, {"palette.1": "category.blue.dark"})
        assert result == "palette:\n  - vivid-10\n  - category.blue.dark\n"

    def test_a_nested_mapping_item_still_refuses(self) -> None:
        original = "palette:\n  - name: category_dark.blue\n"
        with pytest.raises(
            ValueError, match=r"'0' addresses a whole sequence item, not a scalar leaf"
        ):
            set_board_values(original, {"palette.0": "category.blue.dark"})

    def test_deleting_a_sequence_item_leaf_still_refuses(self) -> None:
        original = "palette:\n  - category_dark.blue\n"
        with pytest.raises(
            ValueError, match=r"'0' addresses a whole sequence item, not a scalar leaf"
        ):
            set_board_values(original, {"palette.0": None})

    def test_a_hash_with_no_preceding_whitespace_is_part_of_the_value(self) -> None:
        """`- abc#def` is a valid plain YAML scalar -- YAML only starts a
        comment at whitespace-then-`#`. A sibling item on another line
        rewrites; `abc#def` itself must not be split at the `#` and must
        not be corrupted by an edit to its neighbor."""
        original = "palette:\n  - abc#def\n  - category_dark.blue\n"
        result = set_board_values(original, {"palette.1": "category.blue.dark"})
        assert result == "palette:\n  - abc#def\n  - category.blue.dark\n"

    def test_a_hash_bearing_item_itself_still_rewrites(self) -> None:
        """The item containing the mid-value `#` is the one being
        rewritten, not just a neighbor left alone: the regex must parse
        `abc#def` as a whole bare value (not `abc` + a false "#def"
        comment) before substituting it -- get that wrong and the rewrite
        still "succeeds" syntactically, replacing only the pre-`#` half
        and leaving `#def` stuck onto the new value; the self-check then
        catches the mismatch and raises `_verify_written_values`'s "Setter
        bug" `ValueError` rather than silently writing the wrong content."""
        original = "palette:\n  - abc#def\n"
        result = set_board_values(original, {"palette.0": "category.blue.dark"})
        assert result == "palette:\n  - category.blue.dark\n"

    def test_a_hash_preceded_by_whitespace_is_still_a_comment(self) -> None:
        """The other half of the same rule: whitespace *does* start a
        comment, even when the value itself contains no `#`."""
        original = "palette:\n  - category_dark.blue #def\n"
        result = set_board_values(original, {"palette.0": "category.blue.dark"})
        assert result == "palette:\n  - category.blue.dark #def\n"
