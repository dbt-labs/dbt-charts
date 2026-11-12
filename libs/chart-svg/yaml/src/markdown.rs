//! Markdown boards: a `.md` file read into the board's [`Node`] tree, spans into the `.md`.
//!
//! `core/compile/parse/markdown.py` turns a Markdown file into a YAML string; this builds the
//! node directly (spec § 6), so a diagnostic about the board points at a line of the file.
//!
//! - The frontmatter is document metadata, except a single `board:` key whose mapping is the
//!   board. Without `board:` the file is a document: every frontmatter key is metadata.
//! - A ` ```charts ` fence holds one `rows:` item (a chart, a board or the name of a file); the
//!   prose around fences becomes `text:` rows. A document's Jinja openers are escaped so its text
//!   prints as written.
//! - Each embedded YAML is parsed on its own, padded with newlines so its lines are the file's,
//!   and its spans are moved onto the file's bytes.
//!
//! Lines split at `\n` (a `\r` is whitespace), where Python's `splitlines` also splits at `\r`
//! alone, `\v`, `\f`, `\x1c`-`\x1e`, `\x85`, `\u2028` and `\u2029`.

use crate::{parse_any, Entry, Kind, Node, Span, Value};
use diagnostic::diag::err_validation_field;
use diagnostic::{py_repr, py_str_repr, Diagnostic};
use indexmap::IndexMap;

/// A chart fence: its lines in the file (0-based, inclusive) and its index in the board's `rows:`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChartFence {
    pub first_line: usize,
    pub last_line: usize,
    pub row: usize,
}

/// A markdown file read as a board.
#[derive(Debug, Clone, PartialEq)]
pub struct MarkdownBoard {
    /// The board mapping: the `board:` block, then the `rows:` the body makes.
    pub board: Node,
    /// The frontmatter keys other than `board:`, never read as board configuration.
    pub metadata: IndexMap<String, Entry>,
    pub fences: Vec<ChartFence>,
    /// No `board:` key: the file is a document, and its text is not a template.
    pub is_document: bool,
}

/// Where each line of `text` starts, for the line and column of a byte.
struct Lines<'a> {
    text: &'a str,
    starts: Vec<usize>,
}

impl<'a> Lines<'a> {
    fn new(text: &'a str) -> Self {
        let mut starts = vec![0];
        starts.extend(text.match_indices('\n').map(|(i, _)| i + 1));
        Lines { text, starts }
    }

    fn span(&self, start: usize, end: usize) -> Span {
        let line = self.starts.partition_point(|s| *s <= start);
        let from = self
            .starts
            .get(line.saturating_sub(1))
            .copied()
            .unwrap_or(0);
        let column = self.text.get(from..start).map_or(0, |s| s.chars().count()) + 1;
        Span {
            start,
            end,
            line,
            column,
        }
    }
}

/// `text`'s lines with their ends and byte offsets.
fn lines(text: &str) -> Vec<(usize, &str)> {
    let mut at = 0;
    text.split_inclusive('\n')
        .map(|l| {
            let start = at;
            at += l.len();
            (start, l)
        })
        .collect()
}

/// The run of one fence character a line opens a fenced block with: three or more of `` ` `` or
/// `~` after any indent. Python matches `^\s*(`{3,}|~{3,})([^\r\n]*)$`, whose `$` stops before only
/// a final `\n`: a line that ends `\r\n` opens no fence, so a CRLF file has none.
fn fence_marker(line: &str) -> Option<String> {
    let t = line.trim_start();
    let c = t.chars().next().filter(|c| matches!(c, '`' | '~'))?;
    let run = t.chars().take_while(|x| *x == c).count();
    let rest = t.get(run * c.len_utf8()..).unwrap_or("");
    let rest = rest.strip_suffix('\n').unwrap_or(rest);
    (run >= 3 && !rest.contains('\r')).then(|| c.to_string().repeat(run))
}

fn closes_fence(line: &str, marker: &str) -> bool {
    let t = line.trim();
    let c = marker.chars().next();
    !t.is_empty() && t.chars().all(|x| Some(x) == c) && t.len() >= marker.len()
}

/// A piece of markdown: prose, or a closed fence (`options` is the rest of an opening
/// `` ```charts `` line, `Some` only for a chart fence).
#[derive(Debug)]
struct Piece {
    start: usize,
    end: usize,
    chart: Option<String>,
    fence: bool,
}

/// `text` as prose and closed fences, in order. An unclosed fence is prose.
fn pieces(text: &str) -> Vec<Piece> {
    let mut out: Vec<Piece> = Vec::new();
    let mut open: Option<(usize, String)> = None;
    let mut prose_from: Option<usize> = None;
    let flush = |out: &mut Vec<Piece>, from: &mut Option<usize>, to: usize| {
        if let Some(start) = from.take() {
            if start < to {
                out.push(Piece {
                    start,
                    end: to,
                    chart: None,
                    fence: false,
                });
            }
        }
    };
    for (at, line) in lines(text) {
        if let Some((from, marker)) = &open {
            if closes_fence(line, marker) {
                let end = at + line.len();
                let source = text.get(*from..end).unwrap_or("");
                out.push(Piece {
                    start: *from,
                    end,
                    chart: chart_options(source),
                    fence: true,
                });
                open = None;
            }
            continue;
        }
        if let Some(marker) = fence_marker(line) {
            flush(&mut out, &mut prose_from, at);
            open = Some((at, marker));
            continue;
        }
        prose_from.get_or_insert(at);
    }
    if let Some((from, _)) = open {
        // The fence never closed: its lines are prose, after any prose before it.
        prose_from.get_or_insert(from);
    }
    flush(&mut out, &mut prose_from, text.len());
    out
}

/// What follows `charts` on the opening line of a fence that is a chart fence.
fn chart_options(fence: &str) -> Option<String> {
    let first = fence.split('\n').next().unwrap_or("");
    let indent = first.chars().take_while(|c| *c == ' ').count();
    if indent > 3 {
        return None;
    }
    let t = first.get(indent..)?;
    let c = t.chars().next().filter(|c| matches!(c, '`' | '~'))?;
    let run = t.chars().take_while(|x| *x == c).count();
    if run < 3 {
        return None;
    }
    let rest = t
        .get(run..)?
        .trim_start_matches([' ', '\t'])
        .strip_prefix("charts")?;
    if rest
        .chars()
        .next()
        .is_some_and(|n| n.is_alphanumeric() || n == '_' || n == '-')
    {
        return None;
    }
    Some(rest.to_string())
}

/// Whether markdown `text` holds a chart fence, without reading what is in it.
pub fn has_chart_fence(text: &str) -> bool {
    pieces(text).iter().any(|p| p.chart.is_some())
}

/// `text` with each Jinja opener (`{{`, `{%`, `{#`) outside fenced code rewritten to an
/// expression that prints it.
fn escape_jinja_outside_fences(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    for p in pieces(text) {
        out.push_str(text.get(last..p.start).unwrap_or(""));
        let piece = text.get(p.start..p.end).unwrap_or("");
        if p.fence {
            out.push_str(piece);
        } else {
            let mut rest = piece;
            while let Some((before, after)) = rest.find('{').and_then(|i| rest.split_at_checked(i))
            {
                out.push_str(before);
                match after.chars().nth(1) {
                    Some(c @ ('{' | '%' | '#')) => {
                        out.push_str(&format!("{{{{ \"{{{c}\" }}}}"));
                        rest = after.get(2..).unwrap_or("");
                    }
                    _ => {
                        out.push('{');
                        rest = after.get(1..).unwrap_or("");
                    }
                }
            }
            out.push_str(rest);
        }
        last = p.end;
    }
    out.push_str(text.get(last..).unwrap_or(""));
    out
}

/// Python's `repr` of a loaded YAML value: containers print their elements' `repr`s.
fn python_repr(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::Array(items) => {
            format!(
                "[{}]",
                items.iter().map(python_repr).collect::<Vec<_>>().join(", ")
            )
        }
        serde_json::Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, v)| format!("{}: {}", py_str_repr(k), python_repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        scalar => py_repr(scalar),
    }
}

fn fail(path: &str, message: String) -> Vec<Diagnostic> {
    vec![err_validation_field(path, message)]
}

/// An embedded YAML document: `text` is the file's bytes `at..`, and starts on file line `line`
/// (0-based). It is parsed padded with that many newlines, so an error names a line of the
/// file, and its spans are moved from the padded text onto the file.
fn embedded(file: &str, text: &str, at: usize, line: usize) -> Result<Node, Vec<Diagnostic>> {
    let padded = format!("{}{text}", "\n".repeat(line));
    let mut node = parse_any(&padded).map_err(|mut found| {
        found.iter_mut().for_each(|d| crate::requote(d, file));
        found
    })?;
    remap(&mut node, &|offset| offset + at - line);
    Ok(node)
}

fn remap(node: &mut Node, to: &dyn Fn(usize) -> usize) {
    node.span.start = to(node.span.start);
    node.span.end = to(node.span.end);
    match &mut node.kind {
        Kind::Scalar(_) => {}
        Kind::Seq(items) => items.iter_mut().for_each(|n| remap(n, to)),
        Kind::Map(map) => {
            for entry in map.values_mut() {
                entry.key_span.start = to(entry.key_span.start);
                entry.key_span.end = to(entry.key_span.end);
                remap(&mut entry.value, to);
            }
        }
    }
}

/// The frontmatter mapping, and the body's byte offset and line (0-based).
fn frontmatter(text: &str) -> Result<(IndexMap<String, Entry>, usize, usize), Vec<Diagnostic>> {
    let all = lines(text);
    if all.first().is_none_or(|(_, l)| l.trim() != "---") {
        return Ok((IndexMap::new(), 0, 0));
    }
    let Some(close) = all.iter().skip(1).position(|(_, l)| l.trim() == "---") else {
        return Err(fail(
            "frontmatter",
            "Markdown board file frontmatter is missing a closing --- delimiter".into(),
        ));
    };
    let close = close + 1;
    let body_at = all.get(close + 1).map_or(text.len(), |(s, _)| *s);
    let body_line = close + 1;
    let from = all.get(1).map_or(text.len(), |(s, _)| *s);
    let to = all.get(close).map_or(text.len(), |(s, _)| *s);
    let fm = text.get(from..to).unwrap_or("");
    if fm.trim().is_empty() {
        return Ok((IndexMap::new(), body_at, body_line));
    }
    let node = embedded(text, fm, from, 1)?;
    match node.kind {
        Kind::Scalar(Value::Null) => Ok((IndexMap::new(), body_at, body_line)),
        Kind::Map(map) => Ok((map, body_at, body_line)),
        _ => Err(fail(
            "frontmatter",
            "Markdown board file frontmatter must be a YAML mapping".into(),
        )),
    }
}

/// The `rows:` item a chart fence holds. `fence` is its source, `line` its first line (0-based).
fn fence_row(
    text: &str,
    start: usize,
    end: usize,
    options: &str,
    line: usize,
) -> Result<Node, Vec<Diagnostic>> {
    if !options.trim().is_empty() {
        return Err(fail(
            "charts",
            format!(
                "A `charts` fence takes no options, got {}: write a file path as the fence's content",
                py_str_repr(options.trim())
            ),
        ));
    }
    let source = text.get(start..end).unwrap_or("");
    let all = lines(source);
    let inner_from = all.get(1).map_or(source.len(), |(s, _)| *s);
    let inner_to = all.last().map_or(source.len(), |(s, _)| *s);
    let content = source
        .get(inner_from..inner_to.max(inner_from))
        .unwrap_or("");
    let item = embedded(text, content, start + inner_from, line + 1)?;
    match item.kind {
        Kind::Scalar(Value::Null) => Err(fail(
            "charts",
            format!("The `charts` fence on line {} is empty", line + 1),
        )),
        Kind::Map(_) | Kind::Scalar(Value::Str(_)) => Ok(item),
        _ => Err(fail(
            "charts",
            format!(
                "The `charts` fence on line {} must hold one chart, board or file",
                line + 1
            ),
        )),
    }
}

fn text_row(index: &Lines, start: usize, end: usize, value: String) -> Node {
    text_row_at(index.span(start, end), value)
}

/// A `text:` row whose value is `value`, on `span`; its key is a zero-width mark at the span's start.
fn text_row_at(span: Span, value: String) -> Node {
    let mut map = IndexMap::new();
    map.insert(
        "text".to_string(),
        Entry::string_key(
            Span {
                end: span.start,
                ..span
            },
            "text",
            Node {
                span,
                kind: Kind::Scalar(Value::Str(value)),
            },
        ),
    );
    Node {
        span,
        kind: Kind::Map(map),
    }
}

/// The body as `rows:` items: its chart fences, and the prose between them.
fn body_rows(
    text: &str,
    index: &Lines,
    body_at: usize,
    body_line: usize,
    is_document: bool,
) -> Result<(Vec<Node>, Vec<ChartFence>), Vec<Diagnostic>> {
    let body = text.get(body_at..).unwrap_or("");
    let mut rows: Vec<Node> = Vec::new();
    let mut fences = Vec::new();
    let mut prose: Option<(usize, usize)> = None;
    let mut line = body_line;
    let flush = |rows: &mut Vec<Node>, prose: &mut Option<(usize, usize)>| {
        let Some((from, to)) = prose.take() else {
            return;
        };
        let raw = text.get(from..to).unwrap_or("");
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            return;
        }
        let lead = raw.len() - raw.trim_start().len();
        let value = if is_document {
            escape_jinja_outside_fences(trimmed)
        } else {
            trimmed.to_string()
        };
        rows.push(text_row(
            index,
            from + lead,
            from + lead + trimmed.len(),
            value,
        ));
    };
    for p in pieces(body) {
        let (start, end) = (body_at + p.start, body_at + p.end);
        let source = text.get(start..end).unwrap_or("");
        match &p.chart {
            Some(options) => {
                flush(&mut rows, &mut prose);
                let last_line = line + source.trim_end_matches('\n').matches('\n').count();
                fences.push(ChartFence {
                    first_line: line,
                    last_line,
                    row: rows.len(),
                });
                rows.push(fence_row(text, start, end, options, line)?);
            }
            None => {
                prose = Some((prose.map_or(start, |(from, _)| from), end));
            }
        }
        line += source.matches('\n').count();
    }
    flush(&mut rows, &mut prose);
    Ok((rows, fences))
}

/// Reads a markdown board file. See the module doc for what it makes of the frontmatter and body.
pub fn parse_markdown_board(text: &str) -> Result<MarkdownBoard, Vec<Diagnostic>> {
    let index = Lines::new(text);
    let (mut fm, body_at, body_line) = frontmatter(text)?;
    let is_document = !fm.contains_key("board");
    let mut config = match fm.shift_remove("board") {
        None => IndexMap::new(),
        Some(entry) => match entry.value.kind {
            Kind::Map(map) => map,
            _ => {
                return Err(fail(
                    "frontmatter",
                    "'board:' in markdown frontmatter must be a YAML mapping".into(),
                ))
            }
        },
    };
    let (rows, fences) = body_rows(text, &index, body_at, body_line, is_document)?;
    let body_span = index.span(body_at, text.len());
    let key = |name: &str| Entry::string_key(index.span(body_at, body_at), name, empty());
    if !rows.is_empty() {
        let value = Node {
            span: body_span,
            kind: Kind::Seq(rows),
        };
        match config.get_mut("rows") {
            Some(entry) => entry.value = value,
            None => {
                config.insert(
                    "rows".into(),
                    Entry {
                        value,
                        ..key("rows")
                    },
                );
            }
        }
    } else if !config.contains_key("text") {
        let value = Node {
            span: index.span(text.len(), text.len()),
            kind: Kind::Scalar(Value::Str(String::new())),
        };
        config.insert(
            "text".into(),
            Entry {
                value,
                ..key("text")
            },
        );
    }
    Ok(MarkdownBoard {
        board: Node {
            span: index.span(0, text.len()),
            kind: Kind::Map(config),
        },
        metadata: fm,
        fences,
        is_document,
    })
}

fn empty() -> Node {
    Node {
        span: Span {
            start: 0,
            end: 0,
            line: 1,
            column: 1,
        },
        kind: Kind::Scalar(Value::Null),
    }
}

/// The document metadata as a two-column Markdown table, the first `text:` row of the board when a
/// host asks for it (`markdown_to_yaml(metadata_table=True)`). Metadata is data, never a template:
/// its Jinja tokens print as written. A timestamp prints as its ISO string.
///
/// The board's own `rows:` follow the table, else its `text:` becomes a row after it (an empty
/// `text:` is the placeholder and goes). Python folds whatever it finds there by truthiness, which
/// turns `rows: abc` into three rows and drops `text: 0`; a `rows:` that is not a list and a `text:`
/// that is not a string are refused here instead.
pub fn with_metadata_table(mut board: MarkdownBoard) -> Result<MarkdownBoard, Vec<Diagnostic>> {
    if board.metadata.is_empty() {
        return Ok(board);
    }
    let cell = |s: &str| escape_jinja_outside_fences(&s.replace('|', "\\|").replace('\n', " "));
    let mut table = vec!["| Key | Value |".to_string(), "|---|---|".to_string()];
    for (key, entry) in &board.metadata {
        let value = match entry.value.to_json() {
            serde_json::Value::String(s) => s,
            other => python_repr(&other),
        };
        table.push(format!("| {} | {} |", cell(key), cell(&value)));
    }
    let start = Span {
        start: 0,
        end: 0,
        line: 1,
        column: 1,
    };
    let first = text_row_at(start, table.join("\n"));
    let span = board.board.span;
    let Kind::Map(config) = &mut board.board.kind else {
        return Ok(board);
    };
    let mut rows = vec![first];
    let own = match config.shift_remove("rows") {
        Some(Entry {
            value: Node {
                kind: Kind::Seq(items),
                ..
            },
            ..
        }) => items,
        Some(_) => return Err(fail("rows", "rows must be a list".into())),
        None => Vec::new(),
    };
    if own.is_empty() {
        match config.shift_remove("text") {
            Some(Entry {
                value:
                    Node {
                        kind: Kind::Scalar(Value::Str(text)),
                        span,
                    },
                ..
            }) => {
                if !text.is_empty() {
                    rows.push(text_row_at(span, text));
                }
            }
            Some(_) => return Err(fail("text", "text must be a string".into())),
            None => {}
        }
    }
    // The empty `text:` a board with no body gets is a placeholder, whichever rows it sits beside.
    if matches!(config.get("text"), Some(e) if matches!(&e.value.kind, Kind::Scalar(Value::Str(t)) if t.is_empty()))
    {
        config.shift_remove("text");
    }
    rows.extend(own);
    config.insert(
        "rows".into(),
        Entry::string_key(
            start,
            "rows",
            Node {
                span,
                kind: Kind::Seq(rows),
            },
        ),
    );
    Ok(board)
}

/// Whether markdown `text` is a board. Content only; the caller supplies `in_boards` (the file sits
/// under a `charts/` directory) and `named` (the user named this file directly).
///
/// - In a boards directory, any readable markdown is a board.
/// - A file the user named is a board when its body has a chart fence: a folder listing never names
///   its files, so a README that shows a fence is not swept up.
/// - Otherwise the frontmatter must hold a top-level `board:` key.
pub fn is_markdown_board_content(text: &str, in_boards: bool, named: bool) -> bool {
    if in_boards {
        return true;
    }
    let Ok((fm, body_at, _)) = frontmatter(text) else {
        return false;
    };
    fm.contains_key("board") || (named && has_chart_fence(text.get(body_at..).unwrap_or("")))
}
