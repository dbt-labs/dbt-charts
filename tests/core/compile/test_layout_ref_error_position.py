"""A layout item naming an unknown chart must carry a resolved position.

A layout reference failure without a `field_path` gives `stamp_diagnostics`
nothing to look up, so the diagnostic reaches every UI surface with
`range=None`: no banner coordinate and no editor squiggle.

The nesting tests guard against deriving the path from `item_id` (`"row0"`,
`"col2"`), a chart-ID generation string in the *local* layout's coordinate
space — reusing it inside a nested board would stamp a line in the parent's
space. No range is correct; a confident wrong line is not.
"""

from __future__ import annotations

from dbt_charts.core.compile.compiler import compile


def _line_of(yaml_content: str, needle: str) -> int:
    for i, line in enumerate(yaml_content.splitlines(), start=1):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not in the fixture")


class TestTopLevelLayoutRefPosition:
    def test_rows_ref_to_unknown_chart_resolves_a_range(self) -> None:
        yaml_content = "\n".join(
            [
                "title: My Dashboard",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "charts:",
                "  real_chart:",
                "    type: bar",
                "    query: q1",
                "rows:",
                "  - real_chart",
                "  - attention_stage_0",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.errors, "expected an unresolved-reference error"
        error = result.errors[0]
        assert error.range is not None, "layout ref error carries no position"
        assert error.range.start_line == _line_of(yaml_content, "attention_stage_0")

    def test_cols_ref_to_unknown_chart_resolves_a_range(self) -> None:
        yaml_content = "\n".join(
            [
                "title: My Dashboard",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "charts:",
                "  real_chart:",
                "    type: bar",
                "    query: q1",
                "cols:",
                "  - real_chart",
                "  - nope_not_here",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.errors
        error = result.errors[0]
        assert error.range is not None
        assert error.range.start_line == _line_of(yaml_content, "nope_not_here")


class TestNestedLayoutRefPosition:
    """Never stamp a parent-space line for a nested item."""

    def test_nested_board_ref_never_stamps_a_parent_space_line(self) -> None:
        yaml_content = "\n".join(
            [
                "title: My Dashboard",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "charts:",
                "  real_chart:",
                "    type: bar",
                "    query: q1",
                "rows:",
                "  - real_chart",
                "  - title: Nested",
                "    rows:",
                "      - missing_in_nested",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.errors
        error = result.errors[0]
        assert error.range is not None, "nested layout ref carries no position"
        assert error.range.start_line != _line_of(yaml_content, "- real_chart"), (
            "nested ref stamped a parent-space line — a local index leaked "
            "across the nesting boundary"
        )
        assert error.range.start_line == _line_of(yaml_content, "missing_in_nested")

    def test_tabs_ref_resolves_through_the_tab_index(self) -> None:
        yaml_content = "\n".join(
            [
                "title: My Dashboard",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "    source: db",
                "charts:",
                "  real_chart:",
                "    type: bar",
                "    query: q1",
                "tabs:",
                "  A:",
                "    rows:",
                "      - real_chart",
                "  B:",
                "    rows:",
                "      - missing_in_tab",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.errors
        error = result.errors[0]
        assert error.range is not None
        assert error.range.start_line == _line_of(yaml_content, "missing_in_tab")

    def test_nested_board_tabs_ref_resolves_a_range(self) -> None:
        """A bad reference inside a nested board's `tabs:` must not fall
        through to the normalizer's pathless raise."""
        yaml_content = "\n".join(
            [
                "title: My Dashboard",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "    source: db",
                "charts:",
                "  real_chart:",
                "    type: bar",
                "    query: q1",
                "rows:",
                "  - real_chart",
                "  - title: Nested",
                "    tabs:",
                "      A:",
                "        rows:",
                "          - missing_in_nested_tab",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.errors
        error = result.errors[0]
        assert error.range is not None, "nested tabs ref carries no position"
        assert error.range.start_line == _line_of(yaml_content, "missing_in_nested_tab")

    def test_nested_board_grid_ref_resolves_a_range(self) -> None:
        """A bad reference inside a nested board's `grid:` must not fall
        through to the normalizer's pathless raise."""
        yaml_content = "\n".join(
            [
                "title: My Dashboard",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "    source: db",
                "charts:",
                "  real_chart:",
                "    type: bar",
                "    query: q1",
                "rows:",
                "  - real_chart",
                "  - title: Nested",
                "    grid:",
                "      columns: 12",
                "      items:",
                "        - item: missing_in_nested_grid",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.errors
        error = result.errors[0]
        assert error.range is not None, "nested grid ref carries no position"
        assert error.range.start_line == _line_of(
            yaml_content, "missing_in_nested_grid"
        )


class TestGlobalChartRegistryIsHonored:
    """Validation must resolve against the same namespace the compiler does.

    Chart and query registries are global across the board — "any layout can
    reference any chart" — so a nested board may name a chart declared in a
    sibling board. Validating a nested position against only the top-level
    `charts:` reports that valid authoring as an unresolved reference.
    """

    def test_nested_board_may_reference_a_sibling_boards_chart(self) -> None:
        yaml_content = "\n".join(
            [
                "title: Board",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "    source: db",
                "rows:",
                "  - title: A",
                "    charts:",
                "      shared:",
                "        type: bar",
                "        query: q1",
                "        x: a",
                "        y: a",
                "    rows:",
                "      - shared",
                "  - title: B",
                "    rows:",
                "      - shared",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.success, [(e.code, e.message, e.path) for e in result.errors]

    def test_top_level_row_may_reference_a_nested_boards_chart(self) -> None:
        """The same namespace bug, from the top level down."""
        yaml_content = "\n".join(
            [
                "title: Board",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "    source: db",
                "rows:",
                "  - title: A",
                "    charts:",
                "      shared:",
                "        type: bar",
                "        query: q1",
                "        x: a",
                "        y: a",
                "    rows:",
                "      - shared",
                "  - shared",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.success, [(e.code, e.message, e.path) for e in result.errors]

    def test_a_genuinely_unknown_chart_is_still_reported(self) -> None:
        """Widening the namespace must not blind the check."""
        yaml_content = "\n".join(
            [
                "title: Board",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "    source: db",
                "rows:",
                "  - title: A",
                "    charts:",
                "      shared:",
                "        type: bar",
                "        query: q1",
                "        x: a",
                "        y: a",
                "    rows:",
                "      - shared",
                "  - title: B",
                "    rows:",
                "      - genuinely_missing",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert not result.success
        assert result.errors[0].code == "ERR-UNRESOLVED-REFERENCE"
        assert result.errors[0].range is not None
        assert result.errors[0].range.start_line == _line_of(
            yaml_content, "genuinely_missing"
        )


class TestGridItemPosition:
    """`GridItem.item` is a nested field, so the authored scalar lives at
    `grid.items.<n>.item` — building `grid.items.<n>.query` names a key the
    source map never emits, which silently costs the position."""

    def test_grid_inline_chart_bad_query_resolves_to_its_query_line(self) -> None:
        yaml_content = "\n".join(
            [
                "title: Board",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "    source: db",
                "grid:",
                "  columns: 12",
                "  items:",
                "    - item:",
                "        type: bar",
                "        query: revenue_typo",
                "        x: a",
                "        y: a",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.errors
        error = result.errors[0]
        assert error.path == "grid.items.0.item.query"
        assert error.range is not None
        assert error.range.start_line == _line_of(yaml_content, "revenue_typo")

    def test_grid_string_ref_to_unknown_chart_resolves_a_range(self) -> None:
        yaml_content = "\n".join(
            [
                "title: Board",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "    source: db",
                "charts:",
                "  real_chart:",
                "    type: bar",
                "    query: q1",
                "grid:",
                "  columns: 12",
                "  items:",
                "    - item: real_chart",
                "    - item: missing_in_grid",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.errors
        error = result.errors[0]
        assert error.range is not None
        assert error.range.start_line == _line_of(yaml_content, "missing_in_grid")


class TestLayoutRefErrorCode:
    def test_layout_ref_failure_carries_the_typed_code_not_err_internal(self) -> None:
        """The layout check built a bare ValidationError, which stamps the
        ERR-INTERNAL fallback — the same authoring mistake reported one code
        from the validator and another from the normalizer."""
        yaml_content = "\n".join(
            [
                "title: My Dashboard",
                "queries:",
                "  q1:",
                "    sql: SELECT 1 AS a",
                "    source: db",
                "charts:",
                "  real_chart:",
                "    type: bar",
                "    query: q1",
                "rows:",
                "  - attention_stage_0",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert result.errors
        assert result.errors[0].code == "ERR-UNRESOLVED-REFERENCE"
