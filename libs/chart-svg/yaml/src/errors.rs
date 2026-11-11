use diagnostic::{codes, Diagnostic, SourceRange};
use serde_json::Value;

pub const UNNAMED_SOURCE: &str = "the given YAML content";

/// `ERR-YAML-SYNTAX` as Python's `yaml_syntax_error` builds it: the position
/// and the offending line come from `line`/`column` (1-based) in `text`.
pub fn syntax(text: &str, line: usize, column: usize, problem: &str) -> Diagnostic {
    let unified = text.replace("\r\n", "\n");
    let offending = unified
        .split(['\n', '\r', '\u{85}', '\u{2028}', '\u{2029}'])
        .nth(line.saturating_sub(1))
        .map_or("", str::trim);
    let quoted = if offending.is_empty() {
        String::new()
    } else {
        format!("\n    {offending}")
    };
    Diagnostic::new(
        &codes::ERR_YAML_SYNTAX,
        [
            ("source", Value::from(UNNAMED_SOURCE)),
            (
                "where",
                Value::from(format!(" at line {line}, column {column}")),
            ),
            ("problem", Value::from(problem)),
            ("quoted_line", Value::from(quoted)),
        ],
    )
    .with_sink_param("line", Value::from(line))
    .with_sink_param("column", Value::from(column))
    .at(SourceRange::new(
        offset_of(text, line, column),
        offset_of(text, line, column),
    ))
}

/// The byte offset of `line`/`column` (1-based, in characters, YAML's line breaks), or the end of
/// the text when it lies past it.
fn offset_of(text: &str, line: usize, column: usize) -> usize {
    let (mut l, mut c) = (1, 1);
    let mut chars = text.char_indices().peekable();
    while let Some((i, ch)) = chars.next() {
        if (l, c) == (line, column) {
            return i;
        }
        match ch {
            '\r' | '\n' | '\u{85}' | '\u{2028}' | '\u{2029}' => {
                if ch == '\r' && matches!(chars.peek(), Some((_, '\n'))) {
                    chars.next();
                }
                (l, c) = (l + 1, 1);
            }
            _ => c += 1,
        }
    }
    text.len()
}

pub fn empty() -> Diagnostic {
    Diagnostic::new(
        &codes::ERR_EMPTY_YAML_DOCUMENT,
        [("path", Value::from(UNNAMED_SOURCE))],
    )
}

/// A document the engine does not carry, named at its position.
pub fn refused(line: usize, column: usize, what: &str) -> Diagnostic {
    Diagnostic::refused(format!(
        "{what} at line {line}, column {column} is not supported"
    ))
}

pub fn internal(message: &str) -> Diagnostic {
    Diagnostic::new(&codes::ERR_INTERNAL, [("message", Value::from(message))])
}

/// A failure with no position, as Python reports an unmarked `YAMLError`.
pub fn syntax_unmarked(problem: &str) -> Diagnostic {
    Diagnostic::new(
        &codes::ERR_YAML_SYNTAX,
        [
            ("source", Value::from(UNNAMED_SOURCE)),
            ("where", Value::from("")),
            ("problem", Value::from(problem)),
            ("quoted_line", Value::from("")),
        ],
    )
}
