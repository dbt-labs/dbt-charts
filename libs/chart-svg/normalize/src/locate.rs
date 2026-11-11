//! Where a finding sits in the board file. Emit sites name a model path; one pass resolves each
//! to a byte range against the parsed root document, and the wire form turns the ranges into the
//! lines and columns Python's `Diagnostic` carries.
//!
//! Resolution follows `core/compile/parse/source_map.py`: a `key: value` entry spans the key to
//! the end of its value, a sequence item its own node; a path that does not resolve drops its last
//! segment until one does; a container collapses to its key line; a `source_needle` narrows a
//! literal block scalar to the line equal to it.

use diagnostic::{Diagnostic, ModelPath, Related, SourceRange};
use project::ProjectPath;
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;
use yaml::{Kind, Node};

/// The root board file a compile read, and its node as migration left it.
#[derive(Debug, Clone, PartialEq)]
pub struct Source {
    pub file: ProjectPath,
    pub text: String,
    pub node: Node,
}

/// The node at `path` and where its entry starts.
fn find<'a>(root: &'a Node, path: &ModelPath) -> Option<(usize, &'a Node)> {
    let mut node = root;
    let mut start = root.span.start;
    for seg in &path.0 {
        match &node.kind {
            Kind::Map(map) => {
                let entry = map.get(seg)?;
                if entry.key_span == migrate::SYNTHETIC {
                    return None;
                }
                start = entry.key_span.start;
                node = &entry.value;
            }
            Kind::Seq(items) => {
                node = items.get(seg.parse::<usize>().ok()?)?;
                start = node.span.start;
            }
            Kind::Scalar(_) => return None,
        }
    }
    // A node migration made is not in the authored text.
    (node.span != migrate::SYNTHETIC || path.0.is_empty()).then_some((start, node))
}

fn line_end(text: &str, at: usize) -> usize {
    text[at..].find('\n').map_or(text.len(), |i| at + i)
}

fn line_start(text: &str, at: usize) -> usize {
    text[..at].rfind('\n').map_or(0, |i| i + 1)
}

/// The block scalar's content line equal to `needle`, as the range of that line's text.
fn needle_line(text: &str, value: &Node, needle: &str) -> Option<SourceRange> {
    if !text[value.span.start..].starts_with('|') {
        return None;
    }
    let first = line_end(text, value.span.start) + 1;
    let first_line = text.get(first..)?.lines().next()?;
    let indent = first_line.len() - first_line.trim_start().len();
    let mut at = first;
    for line in text.get(first..)?.split_inclusive('\n') {
        let body = line.trim_end_matches(['\n', '\r']);
        if !body.trim().is_empty() && body.len() - body.trim_start().len() < indent {
            break;
        }
        if body.get(indent..) == Some(needle) {
            return Some(SourceRange::new(at + indent, at + body.len()));
        }
        at += line.len();
    }
    None
}

fn resolve(root: &Node, text: &str, path: &ModelPath, needle: Option<&str>) -> Option<SourceRange> {
    let mut path = path.clone();
    let (start, node) = loop {
        if let Some(found) = find(root, &path) {
            break found;
        }
        // Never up to the bare top-level section: `charts:` spans every chart in the file.
        if path.0.len() <= 2 {
            return None;
        }
        path.0.pop();
    };
    let range = SourceRange::new(start, node.span.end);
    if matches!(node.kind, Kind::Map(_) | Kind::Seq(_)) {
        let end = line_end(text, start);
        return Some(match range.end > end {
            true => SourceRange {
                end,
                lines_only: true,
                ..range
            },
            false => range,
        });
    }
    let narrowed = needle.and_then(|n| needle_line(text, node, n));
    Some(narrowed.unwrap_or(range))
}

/// Fills `range` from `path` on every diagnostic and related place that names a path and has no
/// range yet. A path that resolves nowhere leaves the range absent.
pub fn stamp(diagnostics: &mut [Diagnostic], root: &Node, text: &str) {
    for d in diagnostics {
        let needle = d
            .params
            .get("source_needle")
            .and_then(Value::as_str)
            .map(str::to_string);
        if d.range.is_none() {
            if let Some(path) = &d.path {
                d.range = resolve(root, text, path, needle.as_deref());
            }
        }
        for r in &mut d.related {
            if r.range.is_none() {
                if let Some(path) = &r.path {
                    r.range = resolve(root, text, path, None);
                }
            }
        }
    }
}

fn position(text: &str, at: usize) -> (usize, usize) {
    let start = line_start(text, at);
    (
        text[..start].matches('\n').count() + 1,
        text[start..at].chars().count() + 1,
    )
}

/// The diagnostic wire; `dbt_charts.core.diagnostics.diagnostic.Diagnostic` reads it.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct WireDiagnostic<'a> {
    pub code: &'static str,
    pub params: &'a BTreeMap<&'static str, Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub level: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub range: Option<WireRange>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hint: Option<&'a str>,
    pub related: Vec<WireRelated>,
}

/// `SourceRange`: a resolved position in a project file.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct WireRange {
    pub file: String,
    pub start_line: usize,
    pub end_line: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub columns: Option<WireColumns>,
}

/// `ColumnSpan`: 1-based columns within a one-line range.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct WireColumns {
    pub start_col: usize,
    pub end_col: usize,
}

/// `RelatedLocation`.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct WireRelated {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub range: Option<WireRange>,
    pub message: String,
}

/// `None` for a range outside the root text: a span a parse error took from another file the
/// board imports names no place here.
fn wire_range(source: &Source, r: &SourceRange) -> Option<WireRange> {
    let text = &source.text;
    let fits = r.start <= r.end && text.is_char_boundary(r.start) && text.is_char_boundary(r.end);
    if !fits {
        return None;
    }
    let (start_line, start_col) = position(text, r.start);
    let (end_line, end_col) = position(text, r.end);
    // The line of the last character the range covers; `end` itself is exclusive.
    let last_char = text[..r.end].chars().next_back().map_or(0, char::len_utf8);
    let last = position(text, r.end.saturating_sub(last_char).max(r.start)).0;
    Some(WireRange {
        file: source.file.as_str().into(),
        start_line,
        end_line: last,
        columns: (!r.lines_only && start_line == end_line)
            .then_some(WireColumns { start_col, end_col }),
    })
}

fn wire_related(source: &Source, r: &Related) -> WireRelated {
    WireRelated {
        path: r.path.as_ref().map(ToString::to_string),
        range: r.range.as_ref().and_then(|r| wire_range(source, r)),
        message: r.message.clone(),
    }
}

/// The diagnostic in Python's wire shape: `code`, `params`, `level`, and when known `path`,
/// `range`, `hint` and `related`. Message and fix are the host's to render from `code` and
/// `params`.
pub fn wire(d: &Diagnostic, source: &Source) -> Value {
    let wire = WireDiagnostic {
        code: d.code,
        params: &d.params,
        level: d.registered().map(|code| code.level.as_str()),
        path: d.path.as_ref().map(ToString::to_string),
        range: d.range.as_ref().and_then(|r| wire_range(source, r)),
        hint: d.hint(),
        related: d.related.iter().map(|r| wire_related(source, r)).collect(),
    };
    serde_json::to_value(wire).unwrap_or_else(|e| unreachable!("a diagnostic is plain data: {e}"))
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::panic, clippy::indexing_slicing)]
mod tests {
    use super::*;

    fn at(text: &str, path: &str) -> Option<SourceRange> {
        let node = yaml::parse_node(text).unwrap();
        resolve(
            &node,
            text,
            &ModelPath(path.split('.').map(str::to_string).collect()),
            None,
        )
    }

    #[test]
    fn a_path_walks_up_to_its_nearest_authored_ancestor_but_never_to_the_bare_section() {
        let text = "charts:\n  a:\n    type: bar\n    x: n\ntitle: t\n";
        assert!(at(text, "charts.a.style.axis_x").is_some());
        // `charts:` spans every chart, so a finding about a missing one is located nowhere.
        assert_eq!(at(text, "charts.gone.x"), None);
        assert_eq!(at(text, "charts.gone"), None);
        assert!(at(text, "title").is_some());
    }

    #[test]
    fn a_node_migration_made_is_not_in_the_text_and_resolves_to_its_authored_ancestor() {
        let text = "rows:\n  - text: hi\n    title: t\n";
        let mut node = yaml::parse_node(text).unwrap();
        let path = |p: &str| ModelPath(p.split('.').map(str::to_string).collect());
        let Kind::Map(root) = &mut node.kind else {
            panic!("map")
        };
        let Kind::Seq(rows) = &mut root.get_mut("rows").unwrap().value.kind else {
            panic!("seq")
        };
        let Kind::Map(row) = &mut rows[0].kind else {
            panic!("map")
        };
        row.get_mut("text").unwrap().value.span = migrate::SYNTHETIC;
        row.get_mut("title").unwrap().key_span = migrate::SYNTHETIC;
        // the row itself is authored, so a made value or key lands on it, never on line 1
        let row_range = resolve(&node, text, &path("rows.0"), None).unwrap();
        assert!(row_range.start > 0);
        for made in ["rows.0.text", "rows.0.title"] {
            assert_eq!(
                resolve(&node, text, &path(made), None),
                Some(row_range),
                "{made}"
            );
        }
    }

    #[test]
    fn a_range_outside_the_text_or_inside_a_character_is_dropped() {
        let source = Source {
            file: ProjectPath::try_from("b.yaml").unwrap(),
            text: "a: \u{e9}\n".to_string(),
            node: yaml::parse_node("a: 1\n").unwrap(),
        };
        assert!(wire_range(&source, &SourceRange::new(0, 5)).is_some());
        assert!(wire_range(&source, &SourceRange::new(0, 99)).is_none());
        // byte 4 is the middle of the two-byte character
        assert!(wire_range(&source, &SourceRange::new(0, 4)).is_none());
    }
}
