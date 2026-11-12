// Transliterated from libs/markdown-svg/tests/test_parser.py. Included into
// parser.rs's `#[cfg(test)] mod tests` so both files can be read side by
// side. Test names and grouping (as comment headers) mirror the Python
// classes 1:1.

use super::*;

fn item_text(item: &ListItem) -> String {
    item.spans.iter().map(|s| s.text.as_str()).collect()
}

// --- TestHeadings ---

#[test]
fn test_h1() {
    let doc = parse("# Hello World");
    assert_eq!(doc.len(), 1);
    let AnyBlock::Heading(h) = &doc[0] else {
        panic!("expected heading")
    };
    assert_eq!(h.level, 1);
    assert_eq!(h.spans.len(), 1);
    assert_eq!(h.spans[0].text, "Hello World");
}

#[test]
fn test_h2_through_h6() {
    for level in 2..=6u8 {
        let hashes = "#".repeat(level as usize);
        let doc = parse(&format!("{hashes} Heading"));
        assert_eq!(doc.len(), 1);
        let AnyBlock::Heading(h) = &doc[0] else {
            panic!("expected heading")
        };
        assert_eq!(h.level, level);
    }
}

#[test]
fn test_heading_with_inline_formatting() {
    let doc = parse("# Hello **bold** world");
    let AnyBlock::Heading(h) = &doc[0] else {
        panic!("expected heading")
    };
    assert_eq!(h.spans.len(), 3);
    assert_eq!(h.spans[0].text, "Hello ");
    assert_eq!(h.spans[1].text, "bold");
    assert_eq!(h.spans[1].span_type, SpanType::Bold);
    assert_eq!(h.spans[2].text, " world");
}

// --- TestParagraphs ---

#[test]
fn test_simple_paragraph() {
    let doc = parse("Hello world");
    assert_eq!(doc.len(), 1);
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    assert_eq!(p.spans[0].text, "Hello world");
}

#[test]
fn test_multiple_paragraphs() {
    let doc = parse("First paragraph\n\nSecond paragraph");
    assert_eq!(doc.len(), 2);
    assert!(matches!(doc[0], AnyBlock::Paragraph(_)));
    assert!(matches!(doc[1], AnyBlock::Paragraph(_)));
}

#[test]
fn test_paragraph_with_bold() {
    let doc = parse("Hello **bold** world");
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    assert_eq!(p.spans[1].span_type, SpanType::Bold);
    assert_eq!(p.spans[1].text, "bold");
}

#[test]
fn test_paragraph_with_italic() {
    let doc = parse("Hello *italic* world");
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    assert_eq!(p.spans[1].span_type, SpanType::Italic);
    assert_eq!(p.spans[1].text, "italic");
}

#[test]
fn test_paragraph_with_bold_italic() {
    let doc = parse("Hello ***bolditalic*** world");
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    assert_eq!(p.spans[1].span_type, SpanType::BoldItalic);
}

#[test]
fn test_paragraph_with_inline_code() {
    let doc = parse("Use `code` here");
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    assert_eq!(p.spans[1].span_type, SpanType::Code);
    assert_eq!(p.spans[1].text, "code");
}

// --- TestLinks ---

#[test]
fn test_simple_link() {
    let doc = parse("Click [here](https://example.com)");
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    let link_span = p
        .spans
        .iter()
        .find(|s| s.span_type == SpanType::Link)
        .unwrap();
    assert_eq!(link_span.text, "here");
    assert_eq!(link_span.url.as_deref(), Some("https://example.com"));
}

#[test]
fn test_link_with_title() {
    let doc = parse(r#"[link](https://example.com "Title")"#);
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    let link_span = p
        .spans
        .iter()
        .find(|s| s.span_type == SpanType::Link)
        .unwrap();
    assert_eq!(link_span.title.as_deref(), Some("Title"));
}

fn link_spans(md: &str) -> Vec<(String, bool, Option<String>, Option<String>)> {
    let doc = parse(md);
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    p.spans
        .iter()
        .filter(|s| s.span_type == SpanType::Link)
        .map(|s| (s.text.clone(), s.code, s.url.clone(), s.title.clone()))
        .collect()
}

#[test]
fn inline_code_in_a_link_label_is_a_code_link() {
    assert_eq!(
        link_spans("See [`file.yml`](docs/file.yml) now"),
        [("file.yml".to_string(), true, Some("docs/file.yml".to_string()), None)]
    );
}

#[test]
fn a_label_mixing_text_and_code_splits_into_link_spans() {
    let (u, t) = (Some("u".to_string()), Some("T".to_string()));
    assert_eq!(
        link_spans(r#"[open `a.yml` now](u "T")"#),
        [
            ("open ".to_string(), false, u.clone(), t.clone()),
            ("a.yml".to_string(), true, u.clone(), t.clone()),
            (" now".to_string(), false, u, t),
        ]
    );
}

#[test]
fn a_plain_label_is_not_a_code_link() {
    assert_eq!(
        link_spans("[here](u)"),
        [("here".to_string(), false, Some("u".to_string()), None)]
    );
}

// --- TestLists ---

#[test]
fn test_unordered_list_dash() {
    let doc = parse("- Item 1\n- Item 2\n- Item 3");
    assert_eq!(doc.len(), 1);
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items.len(), 3);
}

#[test]
fn test_unordered_list_asterisk() {
    let doc = parse("* Item 1\n* Item 2");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items.len(), 2);
}

#[test]
fn test_ordered_list() {
    let doc = parse("1. First\n2. Second\n3. Third");
    let AnyBlock::OrderedList(ol) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ol.items.len(), 3);
    assert_eq!(ol.start, Start::Number(1));
}

#[test]
fn test_ordered_list_custom_start() {
    let doc = parse("5. Fifth\n6. Sixth");
    let AnyBlock::OrderedList(ol) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ol.start, Start::Number(5));
}

#[test]
fn test_list_item_with_formatting() {
    let doc = parse("- **Bold** item");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert!(ul.items[0]
        .spans
        .iter()
        .any(|s| s.span_type == SpanType::Bold));
}

// --- TestCodeBlocks ---

#[test]
fn test_fenced_code_block() {
    let doc = parse("```\ncode here\n```");
    assert_eq!(doc.len(), 1);
    let AnyBlock::CodeBlock(c) = &doc[0] else {
        panic!("expected code block")
    };
    assert_eq!(c.code, "code here");
}

#[test]
fn test_fenced_code_block_with_language() {
    let doc = parse("```python\nprint('hello')\n```");
    let AnyBlock::CodeBlock(c) = &doc[0] else {
        panic!("expected code block")
    };
    assert_eq!(c.language.as_deref(), Some("python"));
    assert_eq!(c.code, "print('hello')");
}

#[test]
fn test_indented_code_block() {
    let doc = parse("    code line 1\n    code line 2");
    let AnyBlock::CodeBlock(c) = &doc[0] else {
        panic!("expected code block")
    };
    assert!(c.code.contains("code line 1"));
}

// --- TestBlockquotes ---

#[test]
fn test_simple_blockquote() {
    let doc = parse("> This is a quote");
    assert_eq!(doc.len(), 1);
    let AnyBlock::Blockquote(b) = &doc[0] else {
        panic!("expected blockquote")
    };
    assert_eq!(b.blocks.len(), 1);
}

#[test]
fn test_multiline_blockquote() {
    let doc = parse("> Line 1\n> Line 2");
    assert!(matches!(doc[0], AnyBlock::Blockquote(_)));
}

// --- TestHorizontalRule ---

#[test]
fn test_dashes() {
    let doc = parse("---");
    assert_eq!(doc.len(), 1);
    assert!(matches!(doc[0], AnyBlock::HorizontalRule(_)));
}

#[test]
fn test_asterisks() {
    let doc = parse("***");
    assert!(matches!(doc[0], AnyBlock::HorizontalRule(_)));
}

#[test]
fn test_underscores() {
    let doc = parse("___");
    assert!(matches!(doc[0], AnyBlock::HorizontalRule(_)));
}

// --- TestTables ---

#[test]
fn test_simple_table() {
    let md = "| Header 1 | Header 2 |\n| --- | --- |\n| Cell 1 | Cell 2 |";
    let doc = parse(md);
    assert_eq!(doc.len(), 1);
    let AnyBlock::Table(t) = &doc[0] else {
        panic!("expected table")
    };
    assert_eq!(t.header.cells.len(), 2);
    assert_eq!(t.rows.len(), 1);
}

#[test]
fn a_ragged_body_row_is_clamped_to_the_header_width() {
    let doc = parse("| A | B |\n|---|---|\n| 1 | 2 | 3 |\n| 4 |");
    let AnyBlock::Table(t) = &doc[0] else {
        panic!("expected table")
    };
    let widths: Vec<usize> = t.rows.iter().map(|r| r.cells.len()).collect();
    assert_eq!(widths, [2, 2]);
    assert!(t.rows[1].cells[1].spans.is_empty());
}

#[test]
fn test_table_alignment() {
    let md = "| Left | Center | Right |\n| :--- | :---: | ---: |\n| L | C | R |";
    let doc = parse(md);
    let AnyBlock::Table(t) = &doc[0] else {
        panic!("expected table")
    };
    let aligns: Vec<Option<String>> = t.header.cells.iter().map(|c| c.align.clone()).collect();
    assert_eq!(
        aligns,
        vec![
            Some("left".to_string()),
            Some("center".to_string()),
            Some("right".to_string())
        ]
    );
    let row_aligns: Vec<Option<String>> =
        t.rows[0].cells.iter().map(|c| c.align.clone()).collect();
    assert_eq!(
        row_aligns,
        vec![
            Some("left".to_string()),
            Some("center".to_string()),
            Some("right".to_string())
        ]
    );
}

// --- TestImages ---

#[test]
fn test_inline_image() {
    let doc = parse("Text ![alt](image.png) more");
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    let img_span = p
        .spans
        .iter()
        .find(|s| s.span_type == SpanType::Image)
        .unwrap();
    assert_eq!(img_span.text, "alt");
    assert_eq!(img_span.url.as_deref(), Some("image.png"));
}

#[test]
fn test_standalone_image() {
    let doc = parse("![alt text](image.jpg)");
    let AnyBlock::ImageBlock(img) = &doc[0] else {
        panic!("expected image block")
    };
    assert_eq!(img.url, "image.jpg");
    assert_eq!(img.alt, "alt text");
}

fn image_span(md: &str) -> Span {
    let doc = parse(md);
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    p.spans.iter().find(|s| s.span_type == SpanType::Image).unwrap().clone()
}

#[test]
fn inline_image_carries_its_authored_size() {
    let span = image_span("a ![Logo](u.png){width=40 height=20} b");
    assert_eq!((span.text.as_str(), span.width, span.height), ("Logo", Some(40.0), Some(20.0)));
    let span = image_span(r#"x ![L](u.png "T"){HEIGHT=2.5}y"#);
    assert_eq!((span.title.as_deref(), span.width, span.height), (Some("T"), None, Some(2.5)));
}

#[test]
fn image_size_reads_unicode_decimal_digits_as_python_float_does() {
    // `\d` matches any `Nd` character and Python's `float()` reads it; an
    // Arabic-Indic 3 and a mathematical-bold 5 and double-struck 0 (runs of fifty).
    let span = image_span("a ![L](u.png){width=\u{663}\u{660}.\u{1D7D3} height=\u{1D7D8}} b");
    assert_eq!((span.width, span.height), (Some(30.5), Some(0.0)));
    let AnyBlock::ImageBlock(img) = &parse("![L](u.png){width=\u{665}\u{665}}")[0] else {
        panic!("expected image block")
    };
    assert_eq!(img.width, Some(55.0));
}

#[test]
fn inline_image_with_empty_alt_has_empty_text() {
    assert_eq!(image_span("a ![](u.png) b").text, "");
}

#[test]
fn image_braces_that_are_not_sizes_stay_text() {
    // `_IMAGE_SIZE_ATTRS` matches only numeric width/height pairs, so the
    // line is a paragraph holding an inline image and the literal braces.
    let doc = parse("![Logo](u.png){foo}");
    let AnyBlock::Paragraph(p) = &doc[0] else {
        panic!("expected paragraph")
    };
    assert_eq!(p.spans[1].text, "{foo}");
    let AnyBlock::ImageBlock(img) = &parse("![Logo](u.png){ WIDTH = 40 }")[0] else {
        panic!("expected image block")
    };
    assert_eq!((img.width, img.height), (Some(40.0), None));
}

// --- TestEdgeCases ---

#[test]
fn test_empty_input() {
    assert_eq!(parse(""), Vec::new());
    assert_eq!(parse("   "), Vec::new());
    assert_eq!(parse("\n\n"), Vec::new());
}

#[test]
fn test_mixed_content() {
    let md = "# Heading\n\nParagraph with **bold**.\n\n- List item 1\n- List item 2\n\n```\ncode\n```\n\n> Quote";
    let doc = parse(md);
    assert!(matches!(doc[0], AnyBlock::Heading(_)));
    assert!(matches!(doc[1], AnyBlock::Paragraph(_)));
    assert!(matches!(doc[2], AnyBlock::UnorderedList(_)));
    assert!(matches!(doc[3], AnyBlock::CodeBlock(_)));
    assert!(matches!(doc[4], AnyBlock::Blockquote(_)));
}

// --- TestParserClass ---

#[test]
fn test_parser_instance() {
    let parser = MarkdownParser::new();
    let doc = parser.parse("# Test");
    assert_eq!(doc.len(), 1);
}

// --- TestListCases: 23 cases pinned against CommonMark behavior ---

// Group A: flat lists

#[test]
fn test_flat_unordered_text() {
    let doc = parse("- alpha\n- beta");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    let texts: Vec<String> = ul.items.iter().map(item_text).collect();
    assert_eq!(texts, vec!["alpha", "beta"]);
}

#[test]
fn test_flat_ordered_text_and_start() {
    let doc = parse("1. one\n2. two\n3. three");
    let AnyBlock::OrderedList(ol) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ol.start, Start::Number(1));
    assert_eq!(ol.items.len(), 3);
    assert_eq!(item_text(&ol.items[0]), "one");
}

#[test]
fn test_flat_item_children_empty_by_default() {
    let doc = parse("- simple item");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert!(ul.items[0].children.is_empty());
}

#[test]
fn test_ordered_custom_start() {
    let doc = parse("5. fifth\n6. sixth");
    let AnyBlock::OrderedList(ol) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ol.start, Start::Number(5));
    assert_eq!(ol.items.len(), 2);
}

// Group B: continuation lines

#[test]
fn test_continuation_one_indented_line() {
    let doc = parse("- first line\n  second line");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items.len(), 1);
    let text = item_text(&ul.items[0]);
    assert!(text.contains("first line"));
    assert!(text.contains("second line"));
}

#[test]
fn test_continuation_two_indented_lines() {
    let doc = parse("- line one\n  line two\n  line three");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    let text = item_text(&ul.items[0]);
    assert!(text.contains("line one"));
    assert!(text.contains("line two"));
    assert!(text.contains("line three"));
}

#[test]
fn test_continuation_followed_by_next_item() {
    let doc = parse("- long item\n  continues here\n- short item");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items.len(), 2);
    let text0 = item_text(&ul.items[0]);
    assert!(text0.contains("long item"));
    assert!(text0.contains("continues here"));
    assert_eq!(item_text(&ul.items[1]), "short item");
}

#[test]
fn test_ordered_item_continuation() {
    let doc = parse("1. first item\n   continues here\n2. second item");
    let AnyBlock::OrderedList(ol) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ol.items.len(), 2);
    let text0 = item_text(&ol.items[0]);
    assert!(text0.contains("first item"));
    assert!(text0.contains("continues here"));
}

#[test]
fn test_continuation_carries_inline_formatting() {
    let doc = parse("- start of item\n  **bold continuation**");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert!(ul.items[0]
        .spans
        .iter()
        .any(|s| s.span_type == SpanType::Bold));
}

// Group C: nested lists

#[test]
fn test_nested_single_child_item() {
    let doc = parse("- parent\n  - child");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items.len(), 1);
    let item = &ul.items[0];
    assert_eq!(item.children.len(), 1);
    assert!(matches!(item.children[0], AnyBlock::UnorderedList(_)));
}

#[test]
fn test_nested_multiple_child_items() {
    let doc = parse("- parent\n  - child 1\n  - child 2");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items.len(), 1);
    assert_eq!(ul.items[0].children.len(), 1);
    let AnyBlock::UnorderedList(child_list) = &ul.items[0].children[0] else {
        panic!("expected nested list")
    };
    assert_eq!(child_list.items.len(), 2);
}

#[test]
fn test_nested_child_text_correct() {
    let doc = parse("- parent\n  - child text");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items[0].children.len(), 1);
    let AnyBlock::UnorderedList(child_list) = &ul.items[0].children[0] else {
        panic!("expected nested list")
    };
    assert_eq!(item_text(&child_list.items[0]), "child text");
}

#[test]
fn test_nested_ordered_inside_unordered() {
    let doc = parse("- intro\n  1. first sub\n  2. second sub");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items[0].children.len(), 1);
    let AnyBlock::OrderedList(sub) = &ul.items[0].children[0] else {
        panic!("expected nested ordered list")
    };
    assert_eq!(sub.items.len(), 2);
}

#[test]
fn test_sibling_after_nested() {
    let doc = parse("- parent\n  - child\n- sibling");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items.len(), 2);
    assert_eq!(ul.items[0].children.len(), 1);
    assert_eq!(item_text(&ul.items[1]), "sibling");
}

#[test]
fn test_only_second_item_has_nested() {
    let doc = parse("- first flat\n- second\n  - sub");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items.len(), 2);
    assert!(ul.items[0].children.is_empty());
    assert_eq!(ul.items[1].children.len(), 1);
    assert!(matches!(
        ul.items[1].children[0],
        AnyBlock::UnorderedList(_)
    ));
}

#[test]
fn test_continuation_text_then_nested_child() {
    let doc = parse("- parent text\n  more text\n  - child item");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    let item = &ul.items[0];
    let text = item_text(item);
    assert!(text.contains("parent text"));
    assert!(text.contains("more text"));
    assert_eq!(item.children.len(), 1);
}

#[test]
fn test_list_ends_before_paragraph() {
    let doc = parse("- item\n\nParagraph after");
    assert!(matches!(doc[0], AnyBlock::UnorderedList(_)));
    assert!(matches!(doc[1], AnyBlock::Paragraph(_)));
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        unreachable!()
    };
    assert_eq!(ul.items.len(), 1);
}

// Group D: block-starters terminate list item

#[test]
fn test_list_item_terminated_by_heading() {
    let doc = parse("- item\n# heading");
    assert_eq!(doc.len(), 2);
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items.len(), 1);
    assert_eq!(item_text(&ul.items[0]), "item");
    let AnyBlock::Heading(h) = &doc[1] else {
        panic!("expected heading")
    };
    assert_eq!(h.level, 1);
}

#[test]
fn test_list_item_terminated_by_horizontal_rule() {
    let doc = parse("- item\n---");
    assert_eq!(doc.len(), 2);
    assert!(matches!(doc[0], AnyBlock::UnorderedList(_)));
    assert!(matches!(doc[1], AnyBlock::HorizontalRule(_)));
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        unreachable!()
    };
    assert_eq!(item_text(&ul.items[0]), "item");
}

#[test]
fn test_list_item_terminated_by_blockquote() {
    let doc = parse("- item\n> quote");
    assert_eq!(doc.len(), 2);
    assert!(matches!(doc[0], AnyBlock::UnorderedList(_)));
    assert!(matches!(doc[1], AnyBlock::Blockquote(_)));
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        unreachable!()
    };
    assert_eq!(item_text(&ul.items[0]), "item");
}

#[test]
fn test_list_item_terminated_by_fenced_code() {
    let doc = parse("- item\n```\ncode\n```");
    assert_eq!(doc.len(), 2);
    assert!(matches!(doc[0], AnyBlock::UnorderedList(_)));
    assert!(matches!(doc[1], AnyBlock::CodeBlock(_)));
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        unreachable!()
    };
    assert_eq!(item_text(&ul.items[0]), "item");
}

#[test]
fn test_list_item_terminated_by_table_row() {
    let doc = parse("- item\n| a | b |\n|---|---|\n| 1 | 2 |");
    assert_eq!(doc.len(), 2);
    assert!(matches!(doc[0], AnyBlock::UnorderedList(_)));
    assert!(matches!(doc[1], AnyBlock::Table(_)));
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        unreachable!()
    };
    assert_eq!(item_text(&ul.items[0]), "item");
}

#[test]
fn test_indented_block_starter_stays_in_item() {
    let doc = parse("- item\n  - nested");
    assert_eq!(doc.len(), 1);
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert_eq!(ul.items[0].children.len(), 1);
}

// Group E: loose lists (blank line between marker and nested content)

#[test]
fn test_loose_list_nested_items_not_dropped() {
    let doc = parse("1. Item one\n\n   - sub a\n   - sub b\n\n2. Item two");
    let AnyBlock::OrderedList(ol) = &doc[0] else {
        panic!("expected ordered list")
    };
    assert_eq!(ol.items.len(), 2);
    assert_eq!(item_text(&ol.items[0]), "Item one");
    assert_eq!(ol.items[0].children.len(), 1);
    let AnyBlock::UnorderedList(sub) = &ol.items[0].children[0] else {
        panic!("expected nested list")
    };
    let texts: Vec<String> = sub.items.iter().map(item_text).collect();
    assert_eq!(texts, ["sub a", "sub b"]);
    assert_eq!(item_text(&ol.items[1]), "Item two");
}

#[test]
fn test_loose_list_top_level_blank_lines_stay_flat() {
    let doc = parse("- alpha\n\n- beta\n\n- gamma");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    let texts: Vec<String> = ul.items.iter().map(item_text).collect();
    assert_eq!(texts, ["alpha", "beta", "gamma"]);
}

#[test]
fn test_blank_line_before_flat_paragraph_still_ends_list() {
    let doc = parse("- item\n\nParagraph after");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected list")
    };
    assert!(matches!(doc[1], AnyBlock::Paragraph(_)));
    assert_eq!(ul.items.len(), 1);
}

// `parser.py`: `not line.strip()` ends a paragraph on a line of `\x1c`-`\x1f`
// as well as on a blank one.
#[test]
fn a_line_of_python_spaces_ends_a_paragraph() {
    let doc = parse("a\n\x1c\nb");
    assert_eq!(doc.len(), 2, "{doc:?}");
}

// `parser.py`: a nested list's content is dedented by `len(ln) - len(ln.lstrip())`
// characters, not bytes.
#[test]
fn nested_list_content_is_dedented_by_characters() {
    let doc = parse("- a\n  - b\n\x1f\u{a0}c");
    let AnyBlock::UnorderedList(ul) = &doc[0] else {
        panic!("expected a list: {doc:?}");
    };
    let AnyBlock::UnorderedList(nested) = &ul.items[0].children[0] else {
        panic!("expected a nested list: {doc:?}");
    };
    assert_eq!(item_text(&nested.items[0]), "b c");
}

#[test]
fn code_inside_emphasis_is_emphasized_code() {
    for (markdown, bold, italic) in [
        ("**`message`**", true, false),
        ("*`message`*", false, true),
        ("***`message`***", true, true),
    ] {
        let doc = parse(markdown);
        let AnyBlock::Paragraph(p) = &doc[0] else { panic!("{markdown}") };
        let [span] = &p.spans[..] else { panic!("{markdown}: {:?}", p.spans) };
        assert_eq!(
            (span.span_type, span.text.as_str(), span.bold, span.italic),
            (SpanType::Code, "message", bold, italic),
            "{markdown}"
        );
    }
}

#[test]
fn emphasis_mixing_text_and_code_splits_into_spans() {
    let doc = parse("**set `tone` here**");
    let AnyBlock::Paragraph(p) = &doc[0] else { panic!() };
    let got: Vec<_> = p.spans.iter().map(|s| (s.span_type, s.text.as_str(), s.bold)).collect();
    assert_eq!(
        got,
        [(SpanType::Bold, "set ", false), (SpanType::Code, "tone", true), (SpanType::Bold, " here", false)]
    );
}

fn ordered_start(markdown: &str) -> Start {
    let doc = parse(markdown);
    let AnyBlock::OrderedList(ol) = &doc[0] else {
        panic!("expected an ordered list")
    };
    ol.start.clone()
}

#[test]
fn test_ordered_start_reads_a_non_ascii_digit_like_python_int() {
    assert_eq!(ordered_start("\u{661}. item"), Start::Number(1));
    assert_eq!(ordered_start("\u{966}\u{967}. item"), Start::Number(1));
}

#[test]
fn test_ordered_start_holds_the_widest_number_it_counts() {
    assert_eq!(
        ordered_start(&format!("{}. item", u128::MAX)),
        Start::Number(u128::MAX)
    );
}

#[test]
fn test_ordered_start_past_u128_is_named_not_a_panic() {
    let digits = format!("{}0", u128::MAX);
    assert_eq!(
        ordered_start(&format!("{digits}. item")),
        Start::TooLarge(digits)
    );
}

#[test]
fn test_inline_patterns_have_the_group_counts_the_matcher_destructures() {
    for (pattern, groups) in [
        (inline_code(), 1),
        (image_re(), 4),
        (link_re(), 3),
        (bold_italic_re(), 2),
        (bold_re(), 2),
        (italic_re(), 2),
    ] {
        assert_eq!(pattern.captures_len() - 1, groups, "{pattern}");
    }
}

#[test]
fn test_lone_pipe_row_is_a_paragraph_not_a_hang() {
    // `parser.py::_collect_paragraph_lines` stops at a table row only when the
    // paragraph already holds a line, so a pipe row `parse_table` refuses
    // still starts a paragraph instead of consuming nothing forever.
    let (done, result) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let _ = done.send(parse("Intro paragraph\n\n|---|---|\n\nTail"));
    });
    let doc = result
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("parse returned");
    assert_eq!(doc.len(), 3);
    assert!(doc.iter().all(|block| matches!(block, AnyBlock::Paragraph(_))));
}
