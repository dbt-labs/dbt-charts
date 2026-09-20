"""A tabs item with no `rows:`/`cols:`/`grid:`/`tabs:` of its own is compiled
into a nested `Board` directly in `_resolve_tab_items`, bypassing
`normalize_board`'s `meta` stamp. Every other nested board in the tree
carries `meta: {compiled_at, version}`; a content-only or empty leaf tab
should too.
"""

from __future__ import annotations

from dbt_charts.core.compile.compiler import compile


class TestTabItemMeta:
    def test_content_only_leaf_tab_gets_meta(self) -> None:
        yaml_content = "\n".join(
            [
                "title: My Dashboard",
                "queries:",
                "  q1:",
                "    type: values",
                "    columns: [a]",
                "    values:",
                "      - [1]",
                "charts:",
                "  real_chart:",
                "    type: bar",
                "    query: q1",
                "    x: a",
                "    y: a",
                "tabs:",
                "  items:",
                "    - title: Chart Tab",
                "      rows:",
                "        - real_chart",
                "    - title: Raw",
                "      text: hi",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert not result.errors
        assert result.board is not None
        items = result.board.layout.items
        assert len(items) == 2

        nested_with_layout = items[0].board
        content_only_leaf = items[1].board
        assert nested_with_layout is not None
        assert content_only_leaf is not None

        assert nested_with_layout.meta.get("version")
        assert nested_with_layout.meta.get("compiled_at")
        assert content_only_leaf.meta.get("version")
        assert content_only_leaf.meta.get("compiled_at")

    def test_empty_leaf_tab_gets_meta(self) -> None:
        yaml_content = "\n".join(
            [
                "title: My Dashboard",
                "tabs:",
                "  items:",
                "    - title: Empty Tab",
            ]
        )
        result = compile(yaml_content, file="f.yaml")

        assert not result.errors
        assert result.board is not None
        empty_leaf = result.board.layout.items[0].board
        assert empty_leaf is not None
        assert empty_leaf.meta.get("version")
        assert empty_leaf.meta.get("compiled_at")
