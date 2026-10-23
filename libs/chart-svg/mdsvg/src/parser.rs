//! Markdown parser that produces an AST.
//!
//! Transliterated from `libs/markdown-svg/src/mdsvg/parser.py`. Same regex
//! patterns, same method names and branch order, so the two can be read side
//! by side. Python's `str.strip()`/`.startswith()` etc. translate to Rust's
//! `str::trim`/`starts_with`; indices are byte offsets on ASCII/marker
//! characters only, so they carry the same meaning as Python's codepoint
//! offsets everywhere they are used (list-marker indentation, block-starter
//! prefixes).

use regex::Regex;
use std::sync::OnceLock;

use crate::pygments_re::is_py_space;
use crate::types::{
    AnyBlock, Blockquote, CodeBlock, Document, Heading, HorizontalRule, ImageBlock, ListItem,
    OrderedList, Paragraph, RawHtmlBlock, Span, SpanType, Table, TableCell, TableRow,
    UnorderedList,
};
use crate::utils::{normalize_whitespace, split_lines};

const HTML_VOID_TAGS: &[&str] = &[
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
];

macro_rules! regex_field {
    ($name:ident, $pat:expr) => {
        fn $name() -> &'static Regex {
            static RE: OnceLock<Regex> = OnceLock::new();
            RE.get_or_init(|| Regex::new($pat).unwrap())
        }
    };
}

// Regex patterns for block-level elements — same patterns as the Python
// class-level constants, same names lower_snake_cased for Rust fn form.
regex_field!(heading_pattern, r"^(#{1,6})\s+(.+)$");
regex_field!(fenced_code_start, r"^```(\w*)\s*$");
regex_field!(fenced_code_end, r"^```\s*$");
regex_field!(horizontal_rule, r"^(?:[-*_]){3,}\s*$");
regex_field!(unordered_list_item, r"^(\s*)([-*+])\s+(.+)$");
regex_field!(ordered_list_item, r"^(\s*)(\d+)\.\s+(.+)$");
regex_field!(blockquote_re, r"^>\s?(.*)$");
regex_field!(table_row, r"^\|(.+)\|$");
regex_field!(table_separator, r"^\|[\s\-:|]+\|$");
/// `parser.py::_IMAGE_SIZE_ATTRS`: `{width=X height=Y}` after an image, captured
/// only when the braces hold nothing but numeric sizes — any other braces are
/// the author's text.
macro_rules! image_size_attrs {
    () => {
        r"(?:\{\s*((?:(?i:width|height)\s*=\s*\d+(?:\.\d+)?\s*)+)\})"
    };
}
regex_field!(
    image_block_re,
    concat!(
        r#"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+["']([^"']+)["'])?\)"#,
        image_size_attrs!(),
        r"?\s*$"
    )
);
regex_field!(fenced_html_start, r"^:::\s*html\s*$");
regex_field!(fenced_html_end, r"^:::\s*$");
regex_field!(html_block_start, r"^<([a-zA-Z][a-zA-Z0-9]*)\b");
regex_field!(image_attr, r"(\w+)\s*=\s*(\d+(?:\.\d+)?)");
regex_field!(decimal_digit, r"^\d$");

// Regex patterns for inline elements.
regex_field!(inline_code, r"`([^`]+)`");
regex_field!(bold_italic_re, r"\*\*\*(.+?)\*\*\*|___(.+?)___");
regex_field!(bold_re, r"\*\*(.+?)\*\*|__(.+?)__");
regex_field!(italic_re, r"\*([^*]+)\*|_([^_]+)_");
regex_field!(
    link_re,
    r#"\[([^\]]+)\]\(([^)\s]+)(?:\s+["']([^"']+)["'])?\)"#
);
regex_field!(
    image_re,
    concat!(
        r#"!\[([^\]]*)\]\(([^)\s]+)(?:\s+["']([^"']+)["'])?\)"#,
        image_size_attrs!(),
        "?"
    )
);

/// `float()` reads any Unicode decimal digit, and `\d` matches one. Each `Nd`
/// run is ten consecutive code points starting at a zero, so a digit's value
/// is its distance from the start of its run.
fn ascii_digits(text: &str) -> String {
    text.chars()
        .map(|c| {
            if c.is_ascii() {
                return c;
            }
            let is_digit = |c: char| decimal_digit().is_match(c.encode_utf8(&mut [0; 4]));
            let mut zero = c as u32;
            while char::from_u32(zero - 1).is_some_and(is_digit) {
                zero -= 1;
            }
            char::from_digit((c as u32 - zero) % 10, 10).expect("a digit below ten")
        })
        .collect()
}

/// `parser.py::_parse_image_attrs`: the optional `{width=X height=Y}` of a
/// standalone or inline image.
fn parse_image_attrs(attrs: Option<&str>) -> (Option<f64>, Option<f64>) {
    let (mut width, mut height) = (None, None);
    for caps in image_attr().captures_iter(attrs.unwrap_or_default()) {
        let value: f64 = ascii_digits(&caps[2]).parse().unwrap();
        match caps[1].to_lowercase().as_str() {
            "width" => width = Some(value),
            "height" => height = Some(value),
            _ => {}
        }
    }
    (width, height)
}

pub struct MarkdownParser;

impl MarkdownParser {
    pub fn new() -> Self {
        MarkdownParser
    }

    /// Parse Markdown text into a document AST.
    pub fn parse(&self, text: &str) -> Document {
        if text.chars().all(is_py_space) {
            return Vec::new();
        }
        let lines = split_lines(text);
        self.parse_blocks(&lines)
    }

    /// Parse a list of lines into blocks.
    fn parse_blocks(&self, lines: &[String]) -> Document {
        let mut blocks: Document = Vec::new();
        let mut i = 0usize;

        while i < lines.len() {
            let line = &lines[i];

            if line.chars().all(is_py_space) {
                i += 1;
                continue;
            }

            let (block, consumed) = self.try_parse_block(lines, i);

            if let Some(block) = block {
                blocks.push(block);
                i += consumed;
            } else {
                let (para_lines, consumed) = self.collect_paragraph_lines(lines, i);
                if !para_lines.is_empty() {
                    let para_text = para_lines.join(" ");
                    let spans = self.parse_inline(&para_text);
                    blocks.push(AnyBlock::Paragraph(Paragraph { spans }));
                }
                i += consumed;
            }
        }

        blocks
    }

    /// Try to parse a block starting at the given line index.
    fn try_parse_block(&self, lines: &[String], start: usize) -> (Option<AnyBlock>, usize) {
        let line = &lines[start];

        // Heading
        if let Some(caps) = heading_pattern().captures(line) {
            let level = caps[1].len() as u8;
            let text = caps[2].trim_matches(is_py_space);
            let spans = self.parse_inline(text);
            return (Some(AnyBlock::Heading(Heading::new(level, spans))), 1);
        }

        // Horizontal rule
        if horizontal_rule().is_match(line) {
            return (Some(AnyBlock::HorizontalRule(HorizontalRule)), 1);
        }

        // Fenced code block
        if let Some(caps) = fenced_code_start().captures(line) {
            let language = caps
                .get(1)
                .map(|m| m.as_str())
                .filter(|s| !s.is_empty())
                .map(str::to_string);
            let mut code_lines: Vec<String> = Vec::new();
            let mut i = start + 1;
            while i < lines.len() {
                if fenced_code_end().is_match(&lines[i]) {
                    i += 1;
                    break;
                }
                code_lines.push(lines[i].clone());
                i += 1;
            }
            let code = code_lines.join("\n");
            return (
                Some(AnyBlock::CodeBlock(CodeBlock { code, language })),
                i - start,
            );
        }

        // Indented code block (4 spaces or 1 tab)
        if line.starts_with("    ") || line.starts_with('\t') {
            let mut code_lines_indented: Vec<String> = Vec::new();
            let mut i = start;
            while i < lines.len() {
                let current = &lines[i];
                if let Some(rest) = current.strip_prefix("    ") {
                    code_lines_indented.push(rest.to_string());
                } else if let Some(rest) = current.strip_prefix('\t') {
                    code_lines_indented.push(rest.to_string());
                } else if current.chars().all(is_py_space) {
                    code_lines_indented.push(String::new());
                } else {
                    break;
                }
                i += 1;
            }
            while code_lines_indented.last().is_some_and(|s| s.is_empty()) {
                code_lines_indented.pop();
            }
            if !code_lines_indented.is_empty() {
                return (
                    Some(AnyBlock::CodeBlock(CodeBlock {
                        code: code_lines_indented.join("\n"),
                        language: None,
                    })),
                    i - start,
                );
            }
        }

        // Blockquote
        if blockquote_re().is_match(line) {
            let mut quote_lines: Vec<String> = Vec::new();
            let mut i = start;
            while i < lines.len() {
                if let Some(caps) = blockquote_re().captures(&lines[i]) {
                    quote_lines.push(caps[1].to_string());
                } else if lines[i].chars().all(is_py_space) {
                    if i + 1 < lines.len() && blockquote_re().is_match(&lines[i + 1]) {
                        quote_lines.push(String::new());
                    } else {
                        break;
                    }
                } else {
                    break;
                }
                i += 1;
            }
            let inner_text = quote_lines.join("\n");
            let inner_blocks = self.parse(&inner_text);
            return (
                Some(AnyBlock::Blockquote(Blockquote {
                    blocks: inner_blocks,
                })),
                i - start,
            );
        }

        // Table
        if table_row().is_match(line) {
            if let (Some(table), consumed) = self.parse_table(lines, start) {
                return (Some(AnyBlock::Table(table)), consumed);
            }
        }

        // Unordered list
        if unordered_list_item().is_match(line) {
            return self.parse_list(lines, start, false);
        }

        // Ordered list
        if ordered_list_item().is_match(line) {
            return self.parse_list(lines, start, true);
        }

        // Image block (standalone)
        if let Some(caps) = image_block_re().captures(line.trim_matches(is_py_space)) {
            let alt = caps[1].to_string();
            let url = caps[2].to_string();
            let title = caps.get(3).map(|m| m.as_str().to_string());

            let (width, height) = parse_image_attrs(caps.get(4).map(|m| m.as_str()));

            return (
                Some(AnyBlock::ImageBlock(ImageBlock {
                    url,
                    alt,
                    title,
                    width,
                    height,
                })),
                1,
            );
        }

        // ::: html fenced block
        if fenced_html_start().is_match(line) {
            let mut html_lines: Vec<String> = Vec::new();
            let mut i = start + 1;
            while i < lines.len() {
                if fenced_html_end().is_match(&lines[i]) {
                    i += 1;
                    break;
                }
                html_lines.push(lines[i].clone());
                i += 1;
            }
            return (
                Some(AnyBlock::RawHtmlBlock(RawHtmlBlock {
                    html: html_lines.join("\n"),
                })),
                i - start,
            );
        }

        // HTML block
        if let Some(caps) = html_block_start().captures(line) {
            let tag_name = caps[1].to_string();
            return self.parse_html_block(lines, start, &tag_name);
        }

        (None, 0)
    }

    /// Collect lines that belong to a paragraph.
    fn collect_paragraph_lines(&self, lines: &[String], start: usize) -> (Vec<String>, usize) {
        let mut para_lines: Vec<String> = Vec::new();
        let mut i = start;

        while i < lines.len() {
            let line = &lines[i];

            if line.chars().all(is_py_space) {
                break;
            }

            if heading_pattern().is_match(line)
                || horizontal_rule().is_match(line)
                || fenced_code_start().is_match(line)
                || fenced_html_start().is_match(line)
                || blockquote_re().is_match(line)
                || unordered_list_item().is_match(line)
                || ordered_list_item().is_match(line)
                || table_row().is_match(line)
                || (line.starts_with("    ") && para_lines.is_empty())
                || html_block_start().is_match(line)
            {
                break;
            }

            para_lines.push(normalize_whitespace(line));
            i += 1;
        }

        (para_lines, i - start)
    }

    /// Parse an HTML block starting at the given line.
    ///
    /// Collects lines until the closing tag is found (for paired tags) or
    /// returns a single line (for self-closing / void tags).
    fn parse_html_block(
        &self,
        lines: &[String],
        start: usize,
        tag_name: &str,
    ) -> (Option<AnyBlock>, usize) {
        let tag_lower = tag_name.to_lowercase();
        let first_line = &lines[start];

        let closing = format!("</{tag_name}>");
        if HTML_VOID_TAGS.contains(&tag_lower.as_str()) || first_line.contains(&closing) {
            return (
                Some(AnyBlock::RawHtmlBlock(RawHtmlBlock {
                    html: first_line.clone(),
                })),
                1,
            );
        }

        let closing_lower = closing.to_lowercase();
        let mut html_lines: Vec<String> = vec![first_line.clone()];
        let mut i = start + 1;
        while i < lines.len() {
            html_lines.push(lines[i].clone());
            if lines[i].to_lowercase().contains(&closing_lower) {
                i += 1;
                break;
            }
            i += 1;
        }

        (
            Some(AnyBlock::RawHtmlBlock(RawHtmlBlock {
                html: html_lines.join("\n"),
            })),
            i - start,
        )
    }

    /// Collect body lines for a list item (lines after its marker line).
    ///
    /// Stops at a blank line, the next marker at indent <= base_indent, a
    /// block-starter (heading, HR, fenced code, blockquote, table row, HTML
    /// block) at the same or lower indent, or end of input. Lines with
    /// greater indent are nested content and pass through to the caller.
    ///
    /// A blank line is kept as part of the body -- rather than ending it --
    /// when the next non-blank line is indented deeper than base_indent (a
    /// loose list: the blank sits between a parent item and its
    /// deeper-indented nested content, e.g. a sub-list).
    fn collect_item_body(
        &self,
        lines: &[String],
        start: usize,
        base_indent: usize,
    ) -> (Vec<String>, usize) {
        let mut body: Vec<String> = Vec::new();
        let mut i = start;
        while i < lines.len() {
            let line = &lines[i];
            if line.chars().all(is_py_space) {
                let next = lines[i + 1..].iter().find(|l| !l.chars().all(is_py_space));
                if next.is_some_and(|l| {
                    l.len() - l.trim_start_matches(is_py_space).len() > base_indent
                }) {
                    body.push(line.clone());
                    i += 1;
                    continue;
                }
                break;
            }
            if let Some(caps) = unordered_list_item().captures(line) {
                if caps[1].len() <= base_indent {
                    break;
                }
            }
            if let Some(caps) = ordered_list_item().captures(line) {
                if caps[1].len() <= base_indent {
                    break;
                }
            }
            let line_indent = line.len() - line.trim_start_matches(is_py_space).len();
            if line_indent <= base_indent {
                let stripped = line.trim_start_matches(is_py_space);
                if heading_pattern().is_match(stripped)
                    || horizontal_rule().is_match(stripped)
                    || fenced_code_start().is_match(stripped)
                    || fenced_html_start().is_match(stripped)
                    || blockquote_re().is_match(stripped)
                    || table_row().is_match(stripped)
                    || html_block_start().is_match(stripped)
                {
                    break;
                }
            }
            body.push(line.clone());
            i += 1;
        }
        (body, i - start)
    }

    /// Parse an unordered or ordered list.
    ///
    /// Each item claims its marker line plus all following body lines up to
    /// the next marker at base indent (see collect_item_body for exactly
    /// where a body ends, including loose-list blank-line handling). Body
    /// lines that are themselves list markers at a deeper indent become
    /// ListItem.children; plain continuation lines are joined with the
    /// marker text into spans.
    fn parse_list(
        &self,
        lines: &[String],
        start: usize,
        is_ordered: bool,
    ) -> (Option<AnyBlock>, usize) {
        let mut items: Vec<ListItem> = Vec::new();
        let mut i = start;
        let mut base_indent: Option<usize> = None;
        let mut start_num: i64 = 1;

        while i < lines.len() {
            let line = &lines[i];

            if line.chars().all(is_py_space) {
                i += 1;
                continue;
            }

            let caps_opt = if is_ordered {
                ordered_list_item().captures(line)
            } else {
                unordered_list_item().captures(line)
            };

            if let Some(caps) = caps_opt {
                let indent = caps[1].len();

                match base_indent {
                    None => {
                        base_indent = Some(indent);
                        if is_ordered {
                            start_num = caps[2].parse().unwrap();
                        }
                    }
                    Some(b) if indent < b => break,
                    Some(b) if indent > b => {
                        // A deeper-indented marker reaching the main loop
                        // (rather than being consumed as nested content by
                        // collect_item_body above) has no item to attach to
                        // -- known lossy edge for malformed/ambiguous
                        // indentation.
                        i += 1;
                        continue;
                    }
                    _ => {}
                }
                let base_indent_val = base_indent.unwrap();

                let marker_text = caps[3].to_string();
                let (body_lines, body_consumed) =
                    self.collect_item_body(lines, i + 1, base_indent_val);

                // Split body: plain continuation text vs. nested-list content.
                let mut continuation: Vec<String> = Vec::new();
                let mut sub_content: Vec<String> = Vec::new();
                let mut in_sub = false;
                for bl in &body_lines {
                    if in_sub {
                        sub_content.push(bl.clone());
                        continue;
                    }
                    let m_u = unordered_list_item().captures(bl);
                    let m_o = ordered_list_item().captures(bl);
                    let deeper = m_u
                        .as_ref()
                        .is_some_and(|c| c[1].len() > base_indent_val)
                        || m_o.as_ref().is_some_and(|c| c[1].len() > base_indent_val);
                    if deeper {
                        in_sub = true;
                        sub_content.push(bl.clone());
                    } else {
                        continuation.push(bl.trim_matches(is_py_space).to_string());
                    }
                }

                let mut parts: Vec<String> = vec![marker_text];
                for c in continuation {
                    if !c.is_empty() {
                        parts.push(c);
                    }
                }
                let full_text = parts.join(" ");
                let spans = self.parse_inline(&full_text);

                let mut children: Vec<AnyBlock> = Vec::new();
                if !sub_content.is_empty() {
                    let min_ind = sub_content
                        .iter()
                        .filter(|ln| !ln.chars().all(is_py_space))
                        .map(|ln| ln.chars().take_while(|&c| is_py_space(c)).count())
                        .min()
                        .unwrap_or(0);
                    let dedented: Vec<String> = sub_content
                        .iter()
                        .map(|ln| {
                            if ln.chars().all(is_py_space) {
                                ln.clone()
                            } else {
                                ln.chars().skip(min_ind).collect()
                            }
                        })
                        .collect();
                    children = self.parse_blocks(&dedented);
                }

                items.push(ListItem { spans, children });
                i += body_consumed + 1;
            } else if !line.starts_with(' ') && !line.starts_with('\t') {
                break;
            } else {
                i += 1;
            }
        }

        let block = if is_ordered {
            AnyBlock::OrderedList(OrderedList {
                items,
                start: start_num,
            })
        } else {
            AnyBlock::UnorderedList(UnorderedList { items })
        };
        (Some(block), i - start)
    }

    /// Parse a GFM-style table.
    fn parse_table(&self, lines: &[String], start: usize) -> (Option<Table>, usize) {
        if start + 1 >= lines.len() {
            return (None, 0);
        }

        let header_line = &lines[start];
        let separator_line = &lines[start + 1];

        if !table_row().is_match(header_line) {
            return (None, 0);
        }
        if !table_separator().is_match(separator_line) {
            return (None, 0);
        }

        let alignments = self.parse_table_alignments(separator_line);

        let header_cells = self.parse_table_row(header_line, &alignments, None);
        let header = TableRow {
            cells: header_cells,
        };

        let mut rows: Vec<TableRow> = Vec::new();
        let mut i = start + 2;

        while i < lines.len() {
            let line = &lines[i];
            if !table_row().is_match(line) {
                break;
            }
            let row_cells = self.parse_table_row(line, &alignments, Some(header.cells.len()));
            rows.push(TableRow { cells: row_cells });
            i += 1;
        }

        (Some(Table { header, rows }), i - start)
    }

    /// Parse column alignments from table separator row.
    fn parse_table_alignments(&self, separator: &str) -> Vec<Option<String>> {
        let content = separator.trim_matches(is_py_space).trim_matches('|');
        content
            .split('|')
            .map(|cell| {
                let cell = cell.trim_matches(is_py_space);
                if cell.starts_with(':') && cell.ends_with(':') {
                    Some("center".to_string())
                } else if cell.ends_with(':') {
                    Some("right".to_string())
                } else if cell.starts_with(':') {
                    Some("left".to_string())
                } else {
                    None
                }
            })
            .collect()
    }

    /// Parse a single table row. `columns` is the header's cell count: as in
    /// GFM, a body row drops cells past it and pads a short row with empty
    /// cells. `None` keeps the row's own width (the header).
    fn parse_table_row(
        &self,
        line: &str,
        alignments: &[Option<String>],
        columns: Option<usize>,
    ) -> Vec<TableCell> {
        let content = line.trim_matches(is_py_space).trim_matches('|');
        let mut cells: Vec<TableCell> = content
            .split('|')
            .enumerate()
            .map(|(idx, cell_text)| {
                let cell_text = cell_text.trim_matches(is_py_space);
                let spans = self.parse_inline(cell_text);
                let align = alignments.get(idx).cloned().flatten();
                TableCell { spans, align }
            })
            .collect();
        if let Some(columns) = columns {
            cells.truncate(columns);
            for idx in cells.len()..columns {
                let align = alignments.get(idx).cloned().flatten();
                cells.push(TableCell { spans: Vec::new(), align });
            }
        }
        cells
    }

    /// Parse inline formatting in text: bold, italic, bold+italic, inline
    /// code, links, images.
    fn parse_inline(&self, text: &str) -> Vec<Span> {
        if text.is_empty() {
            return Vec::new();
        }

        let mut spans: Vec<Span> = Vec::new();
        let mut remaining = text;

        while !remaining.is_empty() {
            let mut earliest_pos = remaining.len();
            let mut earliest_end = 0usize;
            let mut match_type: Option<&str> = None;
            let mut groups: Vec<Option<String>> = Vec::new();

            macro_rules! consider {
                ($re:expr, $kind:literal) => {
                    if let Some(caps) = $re.captures(remaining) {
                        let m = caps.get(0).unwrap();
                        if m.start() < earliest_pos {
                            earliest_pos = m.start();
                            earliest_end = m.end();
                            match_type = Some($kind);
                            groups = (1..caps.len())
                                .map(|i| caps.get(i).map(|g| g.as_str().to_string()))
                                .collect();
                        }
                    }
                };
            }

            // Priority order matches the Python: code, image, link,
            // bold_italic, bold, italic — later checks only win on a
            // strictly earlier start.
            consider!(inline_code(), "code");
            consider!(image_re(), "image");
            consider!(link_re(), "link");
            consider!(bold_italic_re(), "bold_italic");
            consider!(bold_re(), "bold");
            consider!(italic_re(), "italic");

            let Some(kind) = match_type else {
                if !remaining.is_empty() {
                    spans.push(Span::text(remaining));
                }
                break;
            };

            if earliest_pos > 0 {
                spans.push(Span::text(&remaining[..earliest_pos]));
            }

            match kind {
                "code" => {
                    let code_text = groups[0].clone().unwrap();
                    spans.push(Span::new(code_text, SpanType::Code, None, None));
                }
                "image" => {
                    let alt = groups[0].clone().unwrap_or_default();
                    let url = groups[1].clone().unwrap();
                    let title = groups.get(2).cloned().flatten();
                    let (width, height) = parse_image_attrs(groups[3].as_deref());
                    spans.push(Span {
                        width,
                        height,
                        ..Span::new(alt, SpanType::Image, Some(url), title)
                    });
                }
                "link" => {
                    let link_text = groups[0].clone().unwrap();
                    let url = groups[1].clone().unwrap();
                    let title = groups.get(2).cloned().flatten();
                    spans.push(Span::new(link_text, SpanType::Link, Some(url), title));
                }
                "bold_italic" => {
                    let inner = groups[0].clone().or(groups[1].clone()).unwrap();
                    spans.push(Span::new(inner, SpanType::BoldItalic, None, None));
                }
                "bold" => {
                    let inner = groups[0].clone().or(groups[1].clone()).unwrap();
                    spans.push(Span::new(inner, SpanType::Bold, None, None));
                }
                "italic" => {
                    let inner = groups[0].clone().or(groups[1].clone()).unwrap();
                    spans.push(Span::new(inner, SpanType::Italic, None, None));
                }
                _ => unreachable!(),
            }

            remaining = &remaining[earliest_end..];
        }

        spans
    }
}

impl Default for MarkdownParser {
    fn default() -> Self {
        Self::new()
    }
}

/// Parse Markdown text into a document AST. Convenience function mirroring
/// the Python module-level `parse()`.
pub fn parse(text: &str) -> Document {
    MarkdownParser::new().parse(text)
}

#[cfg(test)]
mod tests {
    include!("parser_tests.rs");
}
