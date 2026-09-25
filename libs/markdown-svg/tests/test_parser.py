"""Tests for the Markdown parser."""

from mdsvg import (
    Blockquote,
    CodeBlock,
    Heading,
    HorizontalRule,
    ImageBlock,
    ListItem,
    OrderedList,
    Paragraph,
    SpanType,
    Table,
    UnorderedList,
    parse,
)
from mdsvg.parser import MarkdownParser


class TestHeadings:
    """Test heading parsing."""

    def test_h1(self) -> None:
        """Test h1 parsing."""
        doc = parse("# Hello World")
        assert len(doc) == 1
        assert isinstance(doc[0], Heading)
        assert doc[0].level == 1
        assert len(doc[0].spans) == 1
        assert doc[0].spans[0].text == "Hello World"

    def test_h2_through_h6(self) -> None:
        """Test h2-h6 parsing."""
        for level in range(2, 7):
            doc = parse("#" * level + " Heading")
            assert len(doc) == 1
            assert isinstance(doc[0], Heading)
            assert doc[0].level == level

    def test_heading_with_inline_formatting(self) -> None:
        """Test heading with bold/italic."""
        doc = parse("# Hello **bold** world")
        assert isinstance(doc[0], Heading)
        spans = doc[0].spans
        assert len(spans) == 3
        assert spans[0].text == "Hello "
        assert spans[1].text == "bold"
        assert spans[1].span_type == SpanType.BOLD
        assert spans[2].text == " world"


class TestParagraphs:
    """Test paragraph parsing."""

    def test_simple_paragraph(self) -> None:
        """Test simple paragraph."""
        doc = parse("Hello world")
        assert len(doc) == 1
        assert isinstance(doc[0], Paragraph)
        assert doc[0].spans[0].text == "Hello world"

    def test_multiple_paragraphs(self) -> None:
        """Test multiple paragraphs separated by blank lines."""
        doc = parse("First paragraph\n\nSecond paragraph")
        assert len(doc) == 2
        assert isinstance(doc[0], Paragraph)
        assert isinstance(doc[1], Paragraph)

    def test_paragraph_with_bold(self) -> None:
        """Test paragraph with bold text."""
        doc = parse("Hello **bold** world")
        assert isinstance(doc[0], Paragraph)
        spans = doc[0].spans
        assert spans[1].span_type == SpanType.BOLD
        assert spans[1].text == "bold"

    def test_paragraph_with_italic(self) -> None:
        """Test paragraph with italic text."""
        doc = parse("Hello *italic* world")
        assert isinstance(doc[0], Paragraph)
        spans = doc[0].spans
        assert spans[1].span_type == SpanType.ITALIC
        assert spans[1].text == "italic"

    def test_paragraph_with_bold_italic(self) -> None:
        """Test paragraph with bold+italic text."""
        doc = parse("Hello ***bolditalic*** world")
        assert isinstance(doc[0], Paragraph)
        spans = doc[0].spans
        assert spans[1].span_type == SpanType.BOLD_ITALIC

    def test_paragraph_with_inline_code(self) -> None:
        """Test paragraph with inline code."""
        doc = parse("Use `code` here")
        assert isinstance(doc[0], Paragraph)
        spans = doc[0].spans
        assert spans[1].span_type == SpanType.CODE
        assert spans[1].text == "code"


class TestLinks:
    """Test link parsing."""

    def test_simple_link(self) -> None:
        """Test simple link."""
        doc = parse("Click [here](https://example.com)")
        assert isinstance(doc[0], Paragraph)
        spans = doc[0].spans
        link_span = next(s for s in spans if s.span_type == SpanType.LINK)
        assert link_span.text == "here"
        assert link_span.url == "https://example.com"

    def test_link_with_title(self) -> None:
        """Test link with title."""
        doc = parse('[link](https://example.com "Title")')
        assert isinstance(doc[0], Paragraph)
        spans = doc[0].spans
        link_span = next(s for s in spans if s.span_type == SpanType.LINK)
        assert link_span.title == "Title"


class TestLists:
    """Test list parsing."""

    def test_unordered_list_dash(self) -> None:
        """Test unordered list with dashes."""
        doc = parse("- Item 1\n- Item 2\n- Item 3")
        assert len(doc) == 1
        assert isinstance(doc[0], UnorderedList)
        assert len(doc[0].items) == 3

    def test_unordered_list_asterisk(self) -> None:
        """Test unordered list with asterisks."""
        doc = parse("* Item 1\n* Item 2")
        assert isinstance(doc[0], UnorderedList)
        assert len(doc[0].items) == 2

    def test_ordered_list(self) -> None:
        """Test ordered list."""
        doc = parse("1. First\n2. Second\n3. Third")
        assert isinstance(doc[0], OrderedList)
        assert len(doc[0].items) == 3
        assert doc[0].start == 1

    def test_ordered_list_custom_start(self) -> None:
        """Test ordered list with custom start number."""
        doc = parse("5. Fifth\n6. Sixth")
        assert isinstance(doc[0], OrderedList)
        assert doc[0].start == 5

    def test_list_item_with_formatting(self) -> None:
        """Test list item with inline formatting."""
        doc = parse("- **Bold** item")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        item = ul.items[0]
        assert any(s.span_type == SpanType.BOLD for s in item.spans)


class TestCodeBlocks:
    """Test code block parsing."""

    def test_fenced_code_block(self) -> None:
        """Test fenced code block."""
        doc = parse("```\ncode here\n```")
        assert len(doc) == 1
        assert isinstance(doc[0], CodeBlock)
        assert doc[0].code == "code here"

    def test_fenced_code_block_with_language(self) -> None:
        """Test fenced code block with language."""
        doc = parse("```python\nprint('hello')\n```")
        assert isinstance(doc[0], CodeBlock)
        assert doc[0].language == "python"
        assert doc[0].code == "print('hello')"

    def test_indented_code_block(self) -> None:
        """Test indented code block."""
        doc = parse("    code line 1\n    code line 2")
        assert isinstance(doc[0], CodeBlock)
        assert "code line 1" in doc[0].code


class TestBlockquotes:
    """Test blockquote parsing."""

    def test_simple_blockquote(self) -> None:
        """Test simple blockquote."""
        doc = parse("> This is a quote")
        assert len(doc) == 1
        assert isinstance(doc[0], Blockquote)
        assert len(doc[0].blocks) == 1

    def test_multiline_blockquote(self) -> None:
        """Test multiline blockquote."""
        doc = parse("> Line 1\n> Line 2")
        assert isinstance(doc[0], Blockquote)


class TestHorizontalRule:
    """Test horizontal rule parsing."""

    def test_dashes(self) -> None:
        """Test horizontal rule with dashes."""
        doc = parse("---")
        assert len(doc) == 1
        assert isinstance(doc[0], HorizontalRule)

    def test_asterisks(self) -> None:
        """Test horizontal rule with asterisks."""
        doc = parse("***")
        assert isinstance(doc[0], HorizontalRule)

    def test_underscores(self) -> None:
        """Test horizontal rule with underscores."""
        doc = parse("___")
        assert isinstance(doc[0], HorizontalRule)


class TestTables:
    """Test table parsing."""

    def test_simple_table(self) -> None:
        """Test simple table."""
        md = """| Header 1 | Header 2 |
| --- | --- |
| Cell 1 | Cell 2 |"""
        doc = parse(md)
        assert len(doc) == 1
        assert isinstance(doc[0], Table)
        table = doc[0]
        assert len(table.header.cells) == 2
        assert len(table.rows) == 1

    def test_table_alignment(self) -> None:
        """Test table column alignment."""
        md = """| Left | Center | Right |
| :--- | :---: | ---: |
| L | C | R |"""
        doc = parse(md)
        table = doc[0]
        assert isinstance(table, Table)
        assert tuple(cell.align for cell in table.header.cells) == (
            "left",
            "center",
            "right",
        )
        assert tuple(cell.align for cell in table.rows[0].cells) == (
            "left",
            "center",
            "right",
        )


class TestImages:
    """Test image parsing."""

    def test_inline_image(self) -> None:
        """Test inline image in paragraph."""
        doc = parse("Text ![alt](image.png) more")
        assert isinstance(doc[0], Paragraph)
        spans = doc[0].spans
        img_span = next(s for s in spans if s.span_type == SpanType.IMAGE)
        assert img_span.text == "alt"
        assert img_span.url == "image.png"

    def test_standalone_image(self) -> None:
        """Test standalone image block."""
        doc = parse("![alt text](image.jpg)")
        assert isinstance(doc[0], ImageBlock)
        assert doc[0].url == "image.jpg"
        assert doc[0].alt == "alt text"


class TestEdgeCases:
    """Test edge cases."""

    def test_empty_input(self) -> None:
        """Test empty input."""
        assert parse("") == []
        assert parse("   ") == []
        assert parse("\n\n") == []

    def test_mixed_content(self) -> None:
        """Test document with mixed content."""
        md = """# Heading

Paragraph with **bold**.

- List item 1
- List item 2

```
code
```

> Quote"""
        doc = parse(md)
        assert isinstance(doc[0], Heading)
        assert isinstance(doc[1], Paragraph)
        assert isinstance(doc[2], UnorderedList)
        assert isinstance(doc[3], CodeBlock)
        assert isinstance(doc[4], Blockquote)


class TestParserClass:
    """Test MarkdownParser class directly."""

    def test_parser_instance(self) -> None:
        """Test creating parser instance."""
        parser = MarkdownParser()
        doc = parser.parse("# Test")
        assert len(doc) == 1


def _item_text(item: ListItem) -> str:
    return "".join(s.text for s in item.spans)


class TestListCases:
    """17 cases pinned against CommonMark behavior.

    21 of 34 assertions fail against the old parser (before _parse_list).
    The failing assertions are annotated with "# old: FAIL".
    """

    # --- Group A: flat lists — old parser correct ---

    def test_flat_unordered_text(self) -> None:
        doc = parse("- alpha\n- beta")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert [_item_text(it) for it in ul.items] == ["alpha", "beta"]

    def test_flat_ordered_text_and_start(self) -> None:
        doc = parse("1. one\n2. two\n3. three")
        ol = doc[0]
        assert isinstance(ol, OrderedList)
        assert ol.start == 1
        assert len(ol.items) == 3
        assert _item_text(ol.items[0]) == "one"

    def test_flat_item_children_empty_by_default(self) -> None:
        doc = parse("- simple item")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert isinstance(ul.items[0], ListItem)
        assert ul.items[0].children == ()

    def test_ordered_custom_start(self) -> None:
        doc = parse("5. fifth\n6. sixth")
        ol = doc[0]
        assert isinstance(ol, OrderedList)
        assert ol.start == 5
        assert len(ol.items) == 2

    # --- Group B: continuation lines — old parser FAILS ---

    def test_continuation_one_indented_line(self) -> None:
        """Repro from issue #6379: two-physical-line item, both lines in spans."""
        doc = parse("- first line\n  second line")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert len(ul.items) == 1
        text = _item_text(ul.items[0])
        assert "first line" in text
        assert "second line" in text  # old: FAIL

    def test_continuation_two_indented_lines(self) -> None:
        doc = parse("- line one\n  line two\n  line three")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        text = _item_text(ul.items[0])
        assert "line one" in text
        assert "line two" in text  # old: FAIL
        assert "line three" in text  # old: FAIL

    def test_continuation_followed_by_next_item(self) -> None:
        doc = parse("- long item\n  continues here\n- short item")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert len(ul.items) == 2
        text0 = _item_text(ul.items[0])
        assert "long item" in text0
        assert "continues here" in text0  # old: FAIL
        assert _item_text(ul.items[1]) == "short item"

    def test_ordered_item_continuation(self) -> None:
        doc = parse("1. first item\n   continues here\n2. second item")
        ol = doc[0]
        assert isinstance(ol, OrderedList)
        assert len(ol.items) == 2
        text0 = _item_text(ol.items[0])
        assert "first item" in text0
        assert "continues here" in text0  # old: FAIL

    def test_continuation_carries_inline_formatting(self) -> None:
        doc = parse("- start of item\n  **bold continuation**")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        spans = ul.items[0].spans
        assert any(s.span_type == SpanType.BOLD for s in spans)  # old: FAIL

    # --- Group C: nested lists — old parser FAILS ---

    def test_nested_single_child_item(self) -> None:
        doc = parse("- parent\n  - child")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert len(ul.items) == 1
        item = ul.items[0]
        assert len(item.children) == 1  # old: FAIL
        assert isinstance(item.children[0], UnorderedList)

    def test_nested_multiple_child_items(self) -> None:
        doc = parse("- parent\n  - child 1\n  - child 2")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert len(ul.items) == 1
        assert len(ul.items[0].children) == 1  # old: FAIL
        child_list = ul.items[0].children[0]
        assert isinstance(child_list, UnorderedList)
        assert len(child_list.items) == 2

    def test_nested_child_text_correct(self) -> None:
        doc = parse("- parent\n  - child text")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert len(ul.items[0].children) == 1  # old: FAIL
        child_list = ul.items[0].children[0]
        assert isinstance(child_list, UnorderedList)
        assert _item_text(child_list.items[0]) == "child text"

    def test_nested_ordered_inside_unordered(self) -> None:
        doc = parse("- intro\n  1. first sub\n  2. second sub")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert len(ul.items[0].children) == 1  # old: FAIL
        sub = ul.items[0].children[0]
        assert isinstance(sub, OrderedList)
        assert len(sub.items) == 2

    def test_sibling_after_nested(self) -> None:
        doc = parse("- parent\n  - child\n- sibling")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert len(ul.items) == 2
        assert len(ul.items[0].children) == 1  # old: FAIL
        assert _item_text(ul.items[1]) == "sibling"

    def test_only_second_item_has_nested(self) -> None:
        doc = parse("- first flat\n- second\n  - sub")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert len(ul.items) == 2
        assert ul.items[0].children == ()
        assert len(ul.items[1].children) == 1  # old: FAIL
        assert isinstance(ul.items[1].children[0], UnorderedList)

    def test_continuation_text_then_nested_child(self) -> None:
        doc = parse("- parent text\n  more text\n  - child item")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        item = ul.items[0]
        text = _item_text(item)
        assert "parent text" in text
        assert "more text" in text  # old: FAIL
        assert len(item.children) == 1  # old: FAIL

    def test_list_ends_before_paragraph(self) -> None:
        doc = parse("- item\n\nParagraph after")
        assert isinstance(doc[0], UnorderedList)
        assert isinstance(doc[1], Paragraph)
        assert len(doc[0].items) == 1

    # --- Group D: block-starters terminate list item ---

    def test_list_item_terminated_by_heading(self) -> None:
        doc = parse("- item\n# heading")
        assert len(doc) == 2
        assert isinstance(doc[0], UnorderedList)
        assert len(doc[0].items) == 1
        assert _item_text(doc[0].items[0]) == "item"
        assert isinstance(doc[1], Heading)
        assert doc[1].level == 1

    def test_list_item_terminated_by_horizontal_rule(self) -> None:
        doc = parse("- item\n---")
        assert len(doc) == 2
        assert isinstance(doc[0], UnorderedList)
        assert isinstance(doc[1], HorizontalRule)
        assert _item_text(doc[0].items[0]) == "item"

    def test_list_item_terminated_by_blockquote(self) -> None:
        doc = parse("- item\n> quote")
        assert len(doc) == 2
        assert isinstance(doc[0], UnorderedList)
        assert isinstance(doc[1], Blockquote)
        assert _item_text(doc[0].items[0]) == "item"

    def test_list_item_terminated_by_fenced_code(self) -> None:
        doc = parse("- item\n```\ncode\n```")
        assert len(doc) == 2
        assert isinstance(doc[0], UnorderedList)
        assert isinstance(doc[1], CodeBlock)
        assert _item_text(doc[0].items[0]) == "item"

    def test_list_item_terminated_by_table_row(self) -> None:
        doc = parse("- item\n| a | b |\n|---|---|\n| 1 | 2 |")
        assert len(doc) == 2
        assert isinstance(doc[0], UnorderedList)
        assert isinstance(doc[1], Table)
        assert _item_text(doc[0].items[0]) == "item"

    def test_indented_block_starter_stays_in_item(self) -> None:
        """An indented block-starter inside an item body is nested content, not a terminator."""
        doc = parse("- item\n  - nested")
        assert len(doc) == 1
        assert isinstance(doc[0], UnorderedList)
        assert len(doc[0].items[0].children) == 1

    # --- Group E: loose lists (blank line between marker and nested content) ---

    def test_loose_list_nested_items_not_dropped(self) -> None:
        """Repro from dbt-labs/dbt-charts#41: a blank line between a parent
        item and its nested list must not drop the nested items."""
        doc = parse("1. Item one\n\n   - sub a\n   - sub b\n\n2. Item two")
        ol = doc[0]
        assert isinstance(ol, OrderedList)
        assert len(ol.items) == 2
        assert _item_text(ol.items[0]) == "Item one"
        assert len(ol.items[0].children) == 1
        sub = ol.items[0].children[0]
        assert isinstance(sub, UnorderedList)
        assert [_item_text(it) for it in sub.items] == ["sub a", "sub b"]
        assert _item_text(ol.items[1]) == "Item two"

    def test_loose_list_top_level_blank_lines_stay_flat(self) -> None:
        """Blank lines between sibling items (no deeper-indented content) must
        not pull a following sibling into the preceding item's body."""
        doc = parse("- alpha\n\n- beta\n\n- gamma")
        ul = doc[0]
        assert isinstance(ul, UnorderedList)
        assert [_item_text(it) for it in ul.items] == ["alpha", "beta", "gamma"]

    def test_blank_line_before_flat_paragraph_still_ends_list(self) -> None:
        """A blank line before a non-indented paragraph still ends the list
        (guards against the loose-list fix over-extending item bodies)."""
        doc = parse("- item\n\nParagraph after")
        assert isinstance(doc[0], UnorderedList)
        assert isinstance(doc[1], Paragraph)
        assert len(doc[0].items) == 1
