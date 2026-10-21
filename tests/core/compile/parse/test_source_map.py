"""Tests for the YAML source map: dotted-path -> SourceRange, built once per
compile from the composed PyYAML node tree (not the old regex line-finder).
"""

from __future__ import annotations

import yaml

from dbt_charts.core.compile.parse.source_map import (
    LiteralBlock,
    build_source_index,
    build_source_index_from_node,
)
from dbt_charts.core.diagnostics.diagnostic import ColumnSpan, Diagnostic, SourceRange


class TestSourceMap:
    def test_the_node_form_is_what_the_string_form_builds(self) -> None:
        """A caller that already composed the board (the design target) hands
        the node over instead of paying a second scan, and gets the same index."""
        yaml_content = "\n".join(
            [
                "title: t",
                "queries:",
                "  q: |",
                "    select 1",
                "charts:",
                "  c1: {type: line, query: q}",
                "rows:",
                "  - c1",
            ]
        )
        node = yaml.compose(yaml_content, Loader=yaml.SafeLoader)
        assert node is not None
        assert build_source_index_from_node(
            node, yaml_content, "f.yaml"
        ) == build_source_index(yaml_content, "f.yaml")

    def test_top_level_scalar_key_resolves_to_its_line_and_columns(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "title: My Dashboard\n"
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        assert "title" in source_map
        r = source_map["title"]
        assert r.file == "f.yaml"
        assert r.start_line == 1
        assert r.end_line == 1
        assert r.columns is not None
        assert r.columns.start_col == 1

    def test_nested_mapping_key_resolves_by_dotted_path(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "title: t",
                "charts:",
                "  c1:",
                "    type: line",
                "    query: q1",
            ]
        )
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        assert "charts.c1.type" in source_map
        assert source_map["charts.c1.type"].start_line == 4
        assert "charts.c1.query" in source_map
        assert source_map["charts.c1.query"].start_line == 5

    def test_sequence_items_resolve_by_numeric_index(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "grid:",
                "  items:",
                "    - col: 1",
                "      row: 1",
                "    - col: 2",
                "      row: 1",
            ]
        )
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        assert "grid.items.0.col" in source_map
        assert source_map["grid.items.0.col"].start_line == 3
        assert "grid.items.1.col" in source_map
        assert source_map["grid.items.1.col"].start_line == 5

    def test_empty_content_returns_empty_map(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        assert build_source_index("", "f.yaml").source_map == {}

    def test_unparseable_yaml_returns_empty_map_not_a_crash(self) -> None:
        """A source map for invalid YAML cannot exist — this is the same
        'honestly no position known' outcome as an unresolvable path, not a
        fabricated fallback."""
        from dbt_charts.core.compile.parse.source_map import build_source_index

        assert build_source_index("charts: [unterminated", "f.yaml").source_map == {}

    def test_multiline_value_gets_no_column_span(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "charts:",
                "  c1:",
                "    type: line",
                "    query: q1",
            ]
        )
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        # "charts.c1" spans multiple lines (key on line 2, value ends line 4)
        r = source_map["charts.c1"]
        assert r.start_line == 2
        assert r.end_line == 4
        assert r.columns is None

    def test_a_blocks_range_stops_before_the_next_sibling(self) -> None:
        """PyYAML's ``end_mark`` points at the *next* token, so taking it
        verbatim ran every multi-line range one line long — a whole-block
        squiggle bled into the following key. Both consumers treat
        ``end_line`` as inclusive (``lint.ts`` takes ``doc.line(end_line).to``;
        the LSP takes that line's length), so the overlap was visible.
        """
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "charts:",
                "  c1:",
                "    type: line",
                "  c2:",
                "    type: bar",
                "rows:",
                "  - c1",
            ]
        )
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        assert source_map["charts.c1"].end_line == 3
        assert source_map["charts.c2"].end_line == 5
        assert source_map["charts"].end_line == 5
        assert source_map["charts.c1"].end_line < source_map["charts.c2"].start_line

    def test_a_blocks_range_ignores_trailing_blank_lines(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "charts:",
                "  c1:",
                "    type: line",
                "",
                "",
                "rows:",
                "  - c1",
            ]
        )
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        assert source_map["charts.c1"].end_line == 3

    def test_a_comment_documenting_the_next_key_is_not_absorbed(self) -> None:
        """A comment above a key documents *that* key. Walking back over blank
        lines only would stop on it and squiggle someone else's annotation as
        part of the block above — and a leading comment is far more common in
        real board YAML than two blank lines."""
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "charts:",
                "  c1:",
                "    type: line",
                "  # c2 shows the other family",
                "  c2:",
                "    type: bar",
            ]
        )
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        assert source_map["charts.c1"].end_line == 3

    def test_a_comment_inside_a_block_scalar_is_kept(self) -> None:
        """`#` inside a block scalar is that scalar's own content, not a YAML
        comment — trimming it would cut the range short."""
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "queries:",
                "  q1:",
                "    sql: |",
                "      SELECT 1",
                "      # trailing content, not a comment",
                "charts: {}",
            ]
        )
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        assert source_map["queries.q1.sql"].end_line == 5

    def test_a_collapsed_range_emits_no_columns(self) -> None:
        """An empty block scalar's marks straddle two lines, so the walk-back
        collapses `end_line` back onto `start_line` — but `end.column` still
        belongs to the line the mark landed on. Deriving the span from the
        adjusted lines emits `start_col=3, end_col=1`: inverted, and both
        consumers pass it through unguarded (`lint.ts` hands CodeMirror
        `from > to`; `_lsp_range` emits a backwards Range). Columns come from
        the raw marks instead, so a collapsed range simply has none.
        """
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "queries:\n  q1: |\n\ncharts: {}\n"
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        r = source_map["queries.q1"]
        assert r.start_line == 2
        assert r.end_line == 2
        assert r.columns is None

    def test_block_scalar_range_covers_only_its_body(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "queries:",
                "  q1:",
                "    sql: |",
                "      SELECT 1",
                "      FROM t",
                "charts:",
                "  c1:",
                "    type: line",
            ]
        )
        source_map = build_source_index(yaml_content, "f.yaml").source_map

        r = source_map["queries.q1.sql"]
        assert r.start_line == 3
        assert r.end_line == 5


class TestLiteralBlocks:
    """Dotted-path -> LiteralBlock, for literal (`|`) block scalars only —
    the translation input `stamp_diagnostics` needs to narrow a whole-block
    range down to one SQL token."""

    def test_literal_block_scalar_indent_and_lines_are_captured(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "queries:",
                "  q1:",
                "    sql: |",
                "      SELECT 1",
                "      FROM t",
            ]
        )
        blocks = build_source_index(yaml_content, "f.yaml").literal_blocks
        assert blocks["queries.q1.sql"].indent == 6
        # Dedented, so they can be compared against the SQL a diagnostic's
        # position was measured against.
        assert blocks["queries.q1.sql"].lines == ("SELECT 1", "FROM t")

    def test_block_body_stops_at_the_next_key(self) -> None:
        """PyYAML's end_mark sits on the following token's line, so slicing to
        it drags a dedented fragment of the next key in ("source: db" ->
        "urce: db"). The body must end where the block does."""
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "queries:",
                "  q1:",
                "    sql: |",
                "      SELECT 1",
                "      FROM t",
                "    source: db",
                "charts:",
                "  c1:",
                "    type: table",
            ]
        )
        blocks = build_source_index(yaml_content, "f.yaml").literal_blocks
        assert blocks["queries.q1.sql"].lines == ("SELECT 1", "FROM t")

    def test_folded_block_scalar_is_absent(self) -> None:
        """`>` re-flows lines — a sqlglot line/col can't map back to one
        physical source line without reimplementing YAML folding, so this is
        never in the map (stamp_diagnostics keeps the whole-block range)."""
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "queries:",
                "  q1:",
                "    sql: >",
                "      SELECT 1",
                "      FROM t",
            ]
        )
        blocks = build_source_index(yaml_content, "f.yaml").literal_blocks
        assert "queries.q1.sql" not in blocks

    def test_plain_scalar_is_absent(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "queries:\n  q1:\n    sql: SELECT 1\n"
        blocks = build_source_index(yaml_content, "f.yaml").literal_blocks
        assert "queries.q1.sql" not in blocks

    def test_empty_content_returns_empty_map(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        assert build_source_index("", "f.yaml").literal_blocks == {}

    def test_unparseable_yaml_returns_empty_map_not_a_crash(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        assert (
            build_source_index("charts: [unterminated", "f.yaml").literal_blocks == {}
        )


class TestDiagnosticPathToSourceMapKey:
    def test_plain_path_is_unchanged(self) -> None:
        from dbt_charts.core.compile.parse.source_map import (
            diagnostic_path_to_source_map_key,
        )

        assert diagnostic_path_to_source_map_key("title") == "title"
        assert diagnostic_path_to_source_map_key("charts.c1.query") == "charts.c1.query"

    def test_chart_family_discriminator_is_stripped(self) -> None:
        from dbt_charts.core.compile.parse.source_map import (
            diagnostic_path_to_source_map_key,
        )

        # Pydantic's discriminated-union loc inserts the chart-type tag at
        # index 2 — never a real YAML key.
        assert (
            diagnostic_path_to_source_map_key("charts.c1.line.style.legend.disable")
            == "charts.c1.style.legend.disable"
        )

    def test_numeric_list_index_is_kept(self) -> None:
        from dbt_charts.core.compile.parse.source_map import (
            diagnostic_path_to_source_map_key,
        )

        # Unlike the old regex-based line finder, the source map resolves
        # sequence positions natively — no stripping needed.
        assert (
            diagnostic_path_to_source_map_key("grid.items.0.bogus_grid_field")
            == "grid.items.0.bogus_grid_field"
        )

    def test_chart_name_matching_a_chart_type_name_is_not_stripped(self) -> None:
        """Only strip at the discriminator position (index 2) — a chart named
        e.g. 'line' must not be mistaken for the discriminator tag."""
        from dbt_charts.core.compile.parse.source_map import (
            diagnostic_path_to_source_map_key,
        )

        assert (
            diagnostic_path_to_source_map_key("charts.line.bar.style")
            == "charts.line.style"
        )


class TestStampDiagnostics:
    def test_resolves_range_for_a_diagnostic_with_a_matching_path(self) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_compile import ERR_VALIDATION_FIELD

        source_map = {
            "charts.c1.query": SourceRange(file="f.yaml", start_line=5, end_line=5)
        }
        d = Diagnostic.from_code(
            ERR_VALIDATION_FIELD, message="bad", path="charts.c1.query"
        )
        stamp_diagnostics([d], source_map)

        assert d.range == SourceRange(file="f.yaml", start_line=5, end_line=5)

    def test_a_query_failure_ranges_at_the_query_not_the_whole_chart(self) -> None:
        """An execute-time query error names the chart it broke, so its path is
        the whole `charts.<id>` block — but the SQL is what's wrong. With no
        better position inside the chart, point at the query.

        `path` is untouched: it is the click-to-source vocabulary
        (`data-authored-path` on the rendered chart), so the callout must still
        reveal its chart. The two fields answer different questions.
        """
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_execute import ERR_BINDER_UNKNOWN_COLUMN

        source_map = {
            "charts.c1": SourceRange(file="f.yaml", start_line=20, end_line=27),
            "queries.q1": SourceRange(file="f.yaml", start_line=4, end_line=9),
            "queries.q1.sql": SourceRange(file="f.yaml", start_line=5, end_line=9),
        }
        d = Diagnostic.from_code(
            ERR_BINDER_UNKNOWN_COLUMN,
            message="unknown column",
            path="charts.c1",
            query="q1",
        )
        stamp_diagnostics([d], source_map)

        assert d.range == SourceRange(file="f.yaml", start_line=5, end_line=9)
        assert d.path == "charts.c1"

    def test_a_path_inside_the_chart_beats_the_query(self) -> None:
        """`charts.c1.x` already says exactly which line is wrong — naming a
        query too must not drag the mark off it and onto the SQL."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_execute import ERR_BINDER_UNKNOWN_COLUMN

        source_map = {
            "charts.c1.x": SourceRange(file="f.yaml", start_line=22, end_line=22),
            "queries.q1.sql": SourceRange(file="f.yaml", start_line=5, end_line=9),
        }
        d = Diagnostic.from_code(
            ERR_BINDER_UNKNOWN_COLUMN,
            message="unknown column",
            path="charts.c1.x",
            query="q1",
        )
        stamp_diagnostics([d], source_map)

        assert d.range == SourceRange(file="f.yaml", start_line=22, end_line=22)

    def test_an_inline_query_ranges_at_its_own_sql_block(self) -> None:
        """A chart with an inline `query:` has no entry under `queries.` at all,
        so the named-query lookup misses — but its SQL still sits under the
        chart, and that is a better mark than the whole chart block."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_execute import ERR_BINDER_UNKNOWN_COLUMN

        source_map = {
            "charts.c1": SourceRange(file="f.yaml", start_line=3, end_line=9),
            "charts.c1.query.sql": SourceRange(file="f.yaml", start_line=6, end_line=8),
        }
        # An inline query names itself `_inline_query_<chart>`, so a real
        # binder error against one still carries `query`.
        d = Diagnostic.from_code(
            ERR_BINDER_UNKNOWN_COLUMN,
            message="unknown column",
            path="charts.c1",
            query="_inline_query_c1",
        )
        stamp_diagnostics([d], source_map)

        assert d.range == SourceRange(file="f.yaml", start_line=6, end_line=8)

    def test_a_chart_diagnostic_with_no_query_stays_on_the_chart(self) -> None:
        """Every render warning has `path="charts.<id>"` and no query — they are
        about the chart (a dominant slice, crowded labels), not its SQL. The
        query retarget must not capture them, even when the chart happens to
        carry an inline `query:` whose block would resolve."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_render import (
            WARN_PIE_DOMINANT_SEGMENT,
        )

        source_map = {
            "charts.c1": SourceRange(file="f.yaml", start_line=3, end_line=9),
            "charts.c1.query.sql": SourceRange(file="f.yaml", start_line=6, end_line=8),
        }
        d = Diagnostic.from_code(
            WARN_PIE_DOMINANT_SEGMENT, message="one big wedge", path="charts.c1"
        )
        stamp_diagnostics([d], source_map)

        assert d.range == SourceRange(file="f.yaml", start_line=3, end_line=9)

    def test_a_named_query_wins_over_an_inline_one(self) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_execute import ERR_BINDER_UNKNOWN_COLUMN

        source_map = {
            "charts.c1": SourceRange(file="f.yaml", start_line=20, end_line=27),
            "charts.c1.query.sql": SourceRange(
                file="f.yaml", start_line=22, end_line=23
            ),
            "queries.q1.sql": SourceRange(file="f.yaml", start_line=5, end_line=9),
        }
        d = Diagnostic.from_code(
            ERR_BINDER_UNKNOWN_COLUMN,
            message="unknown column",
            path="charts.c1",
            query="q1",
        )
        stamp_diagnostics([d], source_map)

        assert d.range == SourceRange(file="f.yaml", start_line=5, end_line=9)

    def test_an_unresolvable_query_falls_back_to_the_chart(self) -> None:
        """`ERR-UNKNOWN-QUERY` names a query that by definition isn't in the
        map — the chart block stays the best answer."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_compile import ERR_UNKNOWN_QUERY

        source_map = {
            "charts.c1": SourceRange(file="f.yaml", start_line=20, end_line=27)
        }
        d = Diagnostic.from_code(
            ERR_UNKNOWN_QUERY, message="no such query", path="charts.c1", query="nope"
        )
        stamp_diagnostics([d], source_map)

        assert d.range == SourceRange(file="f.yaml", start_line=20, end_line=27)

    def test_no_fallback_when_path_does_not_resolve(self) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_compile import ERR_VALIDATION_FIELD

        d = Diagnostic.from_code(
            ERR_VALIDATION_FIELD, message="bad", path="does.not.exist"
        )
        stamp_diagnostics(
            [d], {"unrelated.key": SourceRange(file="f.yaml", start_line=1, end_line=1)}
        )

        assert d.range is None

    def test_diagnostic_with_no_path_is_untouched(self) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_compile import ERR_VALIDATION_FIELD

        d = Diagnostic.from_code(ERR_VALIDATION_FIELD, message="bad")
        stamp_diagnostics(
            [d], {"title": SourceRange(file="f.yaml", start_line=1, end_line=1)}
        )

        assert d.range is None

    def test_a_brand_new_code_needs_zero_position_wiring(
        self, register_temporarily
    ) -> None:
        """The whole point of a central stamping pass: a code that doesn't
        exist yet in any codes_*.py module — registered here purely as a
        throwaway, never touched by source_map.py or stamp_diagnostics — still
        gets `.range` resolved automatically as long as its Diagnostic carries
        `.path`. No per-code position wiring, ever.
        """
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.registry import ErrorCode

        brand_new = ErrorCode(
            code="ERR-TOTALLY-NEW-TEST-CODE",
            title="Totally new test code",
            domain="compile",
            docs_topic="errors",
            message_template="brand new",
            doc="A throwaway code that exists only for this test.",
        )
        register_temporarily(brand_new)
        d = Diagnostic.from_code(
            brand_new, message="brand new: x", path="charts.c1.query"
        )
        source_map = {
            "charts.c1.query": SourceRange(file="f.yaml", start_line=5, end_line=5)
        }

        stamp_diagnostics([d], source_map)

        assert d.range == SourceRange(file="f.yaml", start_line=5, end_line=5)

    def test_does_not_overwrite_an_already_set_range(self) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_compile import ERR_VALIDATION_FIELD

        existing = SourceRange(file="f.yaml", start_line=9, end_line=9)
        d = Diagnostic.from_code(
            ERR_VALIDATION_FIELD, message="bad", path="title", range=existing
        )
        stamp_diagnostics(
            [d], {"title": SourceRange(file="f.yaml", start_line=1, end_line=1)}
        )

        assert d.range == existing


class TestStampDiagnosticsNarrowsSqlPosition:
    """The coordinate translation: a diagnostic carrying sqlglot's SQL-local
    position — as UnparseableSqlError's Diagnostic does — gets its
    just-stamped whole-block range narrowed to the offending token, using the
    block's own start_line (the `sql:` key's line — content always starts on
    the very next line, since YAML requires the `|`/`>` indicator on the
    key's own line) plus the literal block's indent.

    The narrowing is gated on `sql_line_text` matching the authored line
    character-for-character: the parser sees the *resolved* SQL, which is not
    always the authored text.
    """

    _SOURCE_MAP = {
        "queries.q1.sql": SourceRange(file="f.yaml", start_line=3, end_line=5),
    }
    _BLOCKS = {
        "queries.q1.sql": LiteralBlock(indent=6, lines=("SELECT 1", "FROM bogus tbl"))
    }

    def _diagnostic(self, **fields: object) -> Diagnostic:
        from dbt_charts.core.diagnostics.codes_execute import ERR_UNPARSEABLE_SQL

        return Diagnostic.from_code(
            ERR_UNPARSEABLE_SQL,
            message="bad sql",
            path="charts.c1",
            query="q1",
            fields=fields or None,
        )

    def test_narrows_to_the_offending_token_within_a_literal_block(self) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics

        d = self._diagnostic(
            sql_line=2, sql_start_col=1, sql_end_col=5, sql_line_text="FROM bogus tbl"
        )
        stamp_diagnostics([d], self._SOURCE_MAP, self._BLOCKS)

        # content starts line 4 (start_line + 1); sql_line=2 -> board line 5.
        # indent=6 offsets both columns.
        assert d.range == SourceRange(
            file="f.yaml",
            start_line=5,
            end_line=5,
            columns=ColumnSpan(start_col=7, end_col=11),
        )

    def test_does_not_narrow_when_the_parsed_line_differs_from_the_authored_one(
        self,
    ) -> None:
        """The guard runs on resolved SQL — `{{ var }}` already swapped for a
        bind placeholder, `limit` possibly wrapped around line 1. Those shift
        every column after them, so a position measured against the resolved
        text does not address the authored text. Keep the whole-block range
        rather than underlining an arbitrary substring."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics

        d = self._diagnostic(
            sql_line=2, sql_start_col=1, sql_end_col=5, sql_line_text="FROM $1 tbl"
        )
        stamp_diagnostics([d], self._SOURCE_MAP, self._BLOCKS)

        assert d.range == SourceRange(file="f.yaml", start_line=3, end_line=5)

    def test_does_not_narrow_without_a_literal_block(self) -> None:
        """Folded/plain/quoted scalars have no entry in literal_blocks — keep
        the untouched whole-block range rather than guess."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics

        d = self._diagnostic(
            sql_line=2, sql_start_col=1, sql_end_col=5, sql_line_text="FROM bogus tbl"
        )
        stamp_diagnostics([d], self._SOURCE_MAP, {})

        assert d.range == SourceRange(file="f.yaml", start_line=3, end_line=5)
        assert d.range is not None and d.range.columns is None

    def test_does_not_narrow_when_literal_blocks_is_omitted(self) -> None:
        """The default (empty) preserves stamp_diagnostics' un-narrowed
        behavior exactly — every caller that hasn't opted in is unaffected."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics

        d = self._diagnostic(
            sql_line=2, sql_start_col=1, sql_end_col=5, sql_line_text="FROM bogus tbl"
        )
        stamp_diagnostics([d], self._SOURCE_MAP)

        assert d.range == SourceRange(file="f.yaml", start_line=3, end_line=5)

    def test_does_not_narrow_when_the_line_falls_outside_the_block(self) -> None:
        """A line past the block's own content means the position isn't
        relative to this node (e.g. a setup_sql error sharing the main sql
        field's path) — bail rather than mismark."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics

        d = self._diagnostic(
            sql_line=50, sql_start_col=1, sql_end_col=2, sql_line_text="whatever"
        )
        stamp_diagnostics([d], self._SOURCE_MAP, self._BLOCKS)

        assert d.range == SourceRange(file="f.yaml", start_line=3, end_line=5)

    def test_does_not_narrow_a_diagnostic_with_no_sql_position(self) -> None:
        """ERR-WAREHOUSE-RUNTIME and other positionless errors must keep
        highlighting the whole block — never degrade to a guessed column."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics

        d = self._diagnostic()  # no sql_* fields at all
        stamp_diagnostics([d], self._SOURCE_MAP, self._BLOCKS)

        assert d.range == SourceRange(file="f.yaml", start_line=3, end_line=5)

    def test_does_not_narrow_a_diagnostic_that_already_has_columns(self) -> None:
        """A single-line range already has real columns from PyYAML marks
        (e.g. a plain scalar) — nothing to narrow further."""
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_execute import ERR_UNPARSEABLE_SQL

        existing = SourceRange(
            file="f.yaml",
            start_line=3,
            end_line=3,
            columns=ColumnSpan(start_col=10, end_col=20),
        )
        d = Diagnostic.from_code(
            ERR_UNPARSEABLE_SQL,
            message="bad sql",
            path="charts.c1",
            query="q1",
            fields={
                "sql_line": 1,
                "sql_start_col": 1,
                "sql_end_col": 2,
                "sql_line_text": "SELECT 1",
            },
            range=existing,
        )
        stamp_diagnostics([d], {"queries.q1.sql": existing}, self._BLOCKS)

        assert d.range == existing


class TestContainerPaths:
    """Which dotted paths address a mapping/sequence node rather than a scalar.

    A container's range spans its whole body, so a diagnostic about the
    container itself should mark the key line naming it, not condemn every
    line inside. Distinguishing the two cases needs the node kind: a block
    scalar's range is also multi-line with no columns, and *its* whole-block
    range is the correct mark.
    """

    def test_mapping_and_sequence_paths_are_containers(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "title: t",
                "charts:",
                "  c1:",
                "    type: line",
                "rows:",
                "  - c1",
            ]
        )
        containers = build_source_index(yaml_content, "f.yaml").container_paths

        assert "charts" in containers
        assert "charts.c1" in containers
        assert "rows" in containers

    def test_scalar_paths_are_not_containers(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        yaml_content = "\n".join(
            [
                "title: t",
                "queries:",
                "  q1:",
                "    sql: |",
                "      SELECT 1",
                "      FROM t",
            ]
        )
        containers = build_source_index(yaml_content, "f.yaml").container_paths

        assert "title" not in containers
        assert "queries.q1.sql" not in containers
        assert "queries.q1" in containers

    def test_empty_and_unparseable_content_yield_no_containers(self) -> None:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        assert build_source_index("", "f.yaml").container_paths == frozenset()
        assert (
            build_source_index("charts: [unterminated", "f.yaml").container_paths
            == frozenset()
        )


class TestStampDiagnosticsCollapsesContainerRanges:
    """A diagnostic about a container marks its key line, not its whole body.

    Regression: every render warning resolved to `charts.<id>`, whose range
    spans the chart's entire body, so a warning about one field underlined
    30 lines — including the lines that were fine.
    """

    _YAML = "\n".join(
        [
            "charts:",
            "  c1:",
            "    type: line",
            "    x: month",
            "    y: revenue",
            "queries:",
            "  q1:",
            "    sql: |",
            "      SELECT 1",
            "      FROM t",
        ]
    )

    def _map_and_containers(self) -> tuple[dict[str, SourceRange], frozenset[str]]:
        from dbt_charts.core.compile.parse.source_map import build_source_index

        index = build_source_index(self._YAML, "f.yaml")
        return index.source_map, index.container_paths

    def test_a_container_range_collapses_to_its_key_line(self) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_compile import WARN_UNREFERENCED_CHART

        source_map, containers = self._map_and_containers()
        # The map itself still describes the whole block — other consumers
        # (chart reveal) rely on that; only the diagnostic narrows.
        assert source_map["charts.c1"].end_line == 5

        d = Diagnostic.from_code(
            WARN_UNREFERENCED_CHART, message="orphan", path="charts.c1"
        )
        stamp_diagnostics([d], source_map, container_paths=containers)

        assert d.range is not None
        assert d.range.start_line == 2
        assert d.range.end_line == 2

    def test_a_block_scalar_range_is_not_collapsed(self) -> None:
        """A SQL error's whole-block underline is the correct mark — the
        collapse is keyed on node kind, not on 'the range is multi-line',
        precisely so this case is untouched.
        """
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_query import WARN_PARSE_ERROR

        source_map, containers = self._map_and_containers()
        d = Diagnostic.from_code(
            WARN_PARSE_ERROR, message="unparseable", path="queries.q1.sql"
        )
        stamp_diagnostics([d], source_map, container_paths=containers)

        assert d.range == source_map["queries.q1.sql"]
        assert d.range.end_line > d.range.start_line

    def test_omitting_container_paths_preserves_the_old_behavior(self) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_compile import WARN_UNREFERENCED_CHART

        source_map, _ = self._map_and_containers()
        d = Diagnostic.from_code(
            WARN_UNREFERENCED_CHART, message="orphan", path="charts.c1"
        )
        stamp_diagnostics([d], source_map)

        assert d.range == source_map["charts.c1"]


class TestStampDiagnosticsWalksUpUnresolvablePaths:
    """An anchor naming a key the author never wrote resolves to its nearest
    authored ancestor.

    This is what lets a detector name the field it means without knowing
    whether the author wrote it — several warnings are *about* an absent key
    (`WARN-REDUNDANT-ENCODING` has no key to point at when the author wrote none), so an over-specific anchor has to degrade rather than lose
    its position entirely.
    """

    def test_an_unauthored_leaf_resolves_to_its_nearest_authored_ancestor(
        self,
    ) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_render import WARN_TOO_MANY_X_CATEGORIES

        source_map = {
            "charts.c1": SourceRange(file="f.yaml", start_line=2, end_line=8),
            "charts.c1.style": SourceRange(file="f.yaml", start_line=6, end_line=8),
        }
        d = Diagnostic.from_code(
            WARN_TOO_MANY_X_CATEGORIES,
            message="too many",
            path="charts.c1.style.axis_y.format",
        )
        stamp_diagnostics([d], source_map)

        assert d.range == source_map["charts.c1.style"]

    def test_the_walk_stops_at_the_first_resolvable_ancestor(self) -> None:
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_render import WARN_TOO_MANY_X_CATEGORIES

        source_map = {
            "charts.c1": SourceRange(file="f.yaml", start_line=2, end_line=8),
            "charts.c1.x": SourceRange(file="f.yaml", start_line=4, end_line=4),
        }
        d = Diagnostic.from_code(
            WARN_TOO_MANY_X_CATEGORIES, message="too many", path="charts.c1.x"
        )
        stamp_diagnostics([d], source_map)

        assert d.range == source_map["charts.c1.x"]

    def test_a_wholly_unresolvable_path_still_leaves_no_range(self) -> None:
        """The walk must never fabricate a position — an ancestor that does not
        resolve either leaves `range` None, exactly as before.
        """
        from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
        from dbt_charts.core.diagnostics.codes_render import WARN_TOO_MANY_X_CATEGORIES

        source_map = {
            "queries.q1": SourceRange(file="f.yaml", start_line=2, end_line=4)
        }
        d = Diagnostic.from_code(
            WARN_TOO_MANY_X_CATEGORIES, message="too many", path="charts.nope.x"
        )
        stamp_diagnostics([d], source_map)

        assert d.range is None

    def test_the_walk_never_degrades_to_a_bare_top_level_section(self) -> None:
        """A path naming a chart that isn't there must not mark every chart.

        `charts.nope.x` walking all the way up hits `charts`, whose range is the
        whole section — so a diagnostic about one missing chart would squiggle
        every chart in the file. That is the failure this walk exists to remove,
        not one it may cause: the last resolvable ancestor has to still be
        *about* the thing named, so the walk stops before the bare root segment.
        """
        from dbt_charts.core.compile.parse.source_map import (
            build_source_index,
            stamp_diagnostics,
        )
        from dbt_charts.core.diagnostics.codes_render import WARN_TOO_MANY_X_CATEGORIES

        yaml_content = "\n".join(
            [
                "charts:",
                "  c1:",
                "    type: line",
                "    x: month",
                "    y: rev",
            ]
        )
        source_map, _, containers = build_source_index(yaml_content, "f.yaml")
        assert "charts" in source_map  # the tempting wrong answer is available

        d = Diagnostic.from_code(
            WARN_TOO_MANY_X_CATEGORIES, message="too many", path="charts.nope.x"
        )
        stamp_diagnostics([d], source_map, container_paths=containers)

        assert d.range is None

    def test_a_single_segment_path_still_resolves_to_itself(self) -> None:
        """Bounding the walk must not stop `title`/`text` resolving — they *are*
        top-level keys, and a diagnostic about one is about exactly it."""
        from dbt_charts.core.compile.parse.source_map import (
            build_source_index,
            stamp_diagnostics,
        )
        from dbt_charts.core.diagnostics.codes_compile import WARN_DOUBLE_HEADER

        source_map = build_source_index("title: Sales\n", "f.yaml").source_map
        d = Diagnostic.from_code(
            WARN_DOUBLE_HEADER, message="two headers", path="title"
        )
        stamp_diagnostics([d], source_map)

        assert d.range == source_map["title"]
