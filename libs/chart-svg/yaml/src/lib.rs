//! Authored YAML to a spanned [`Node`].
//!
//! ```text
//! text -> dbt-yaml StringNode -> YAML 1.1 scalar resolution -> Node
//! ```
//!
//! [`parse_node`] is the whole API, with [`Node::to_json`] for the sweeps.
//! `dbt-yaml` is confined to this crate; no type of it crosses.
//!
//! A [`Node`] is a [`Kind::Scalar`] of a resolved [`Value`], a [`Kind::Seq`] or
//! a [`Kind::Map`] (an `IndexMap` in authored order, keys always strings). Every
//! node and every map key carries a [`Span`]. A node reached through an alias
//! carries the alias's span.
//!
//! A non-string scalar key is stored as the text `json.dumps` gives it (`0x10`
//! is `"16"`, `yes` is `"true"`, `~` is `"null"`, a float is Python's `repr`);
//! its [`Entry`] records the [`KeyKind`] it resolved to and the key as
//! authored. The merge key `<<` resolves as PyYAML's `SafeLoader` does: a
//! mapping, or a sequence of mappings (first wins), merges in under the
//! mapping's explicit keys, and a merged entry keeps its source's spans.
//!
//! Plain scalars resolve as PyYAML does (YAML 1.1: `yes` is a boolean,
//! `1_000` an integer, `1e3` a string, `2001-12-14` a timestamp). Quoted and
//! block scalars are strings.
//!
//! Refused at build, each as a [`Diagnostic`](diagnostic::Diagnostic):
//! - as Python's loader: a syntax error, a duplicate key, more than one document
//!   and nesting past 100 levels are `ERR-YAML-SYNTAX` with Python's `problem`,
//!   position and quoted line; an empty (or `null`) document is
//!   `ERR-EMPTY-YAML-DOCUMENT`; a root that is not a mapping is `ERR-INTERNAL`;
//!   an integer with no digits, a date that is not one, the `=` value tag and
//!   `<<` as a value are `ERR-YAML-SYNTAX`.
//!   A `<<` whose value is not a mapping or a sequence of mappings, and a
//!   sequence or mapping used as a key, are `ERR-YAML-SYNTAX` too. Keys equal
//!   in Python (`1`, `1.0`, `true`) are duplicates.
//! - not carried (`ERR-ENGINE-REFUSED`, Python accepts them): an explicit tag
//!   (quote the value instead), a timestamp key, keys that are one key in JSON
//!   text but two in Python (`1` and `'1'`) or the reverse (`1` and `1.0`), a
//!   number JSON cannot hold (an integer beyond 64 bits, `.inf`, `.nan`).
//!
//! Only the first error is reported, as Python's loader raises one.

#![cfg_attr(
    not(test),
    deny(
        clippy::unwrap_used,
        clippy::expect_used,
        clippy::panic,
        clippy::indexing_slicing
    )
)]

mod build;
mod errors;
mod key;
mod node;
mod resolve;

use dbt_yaml::StringNode;
use diagnostic::Diagnostic;
pub use node::{Entry, KeyKind, Kind, Node, Span, Value};

/// Parses one YAML document into a [`Node`] whose root is a mapping.
pub fn parse_node(text: &str) -> Result<Node, Vec<Diagnostic>> {
    let tree = StringNode::from_str(text).map_err(|e| vec![load_error(text, &e)])?;
    let root = build::Builder::new(text)
        .node(&tree, 0)
        .map_err(|d| vec![d])?;
    match &root.kind {
        Kind::Map(_) => Ok(root),
        Kind::Scalar(Value::Null) => Err(vec![errors::empty()]),
        other => Err(vec![errors::internal(&format!(
            "YAML must be a mapping, got {}",
            python_type(other)
        ))]),
    }
}

fn python_type(kind: &Kind) -> &'static str {
    match kind {
        Kind::Seq(_) => "list",
        Kind::Map(_) => "dict",
        Kind::Scalar(Value::Null) => "NoneType",
        Kind::Scalar(Value::Bool(_)) => "bool",
        Kind::Scalar(Value::Number(n)) if n.is_f64() => "float",
        Kind::Scalar(Value::Number(_)) => "int",
        Kind::Scalar(Value::Str(_)) => "str",
        Kind::Scalar(Value::Timestamp(s)) if s.contains('T') => "datetime",
        Kind::Scalar(Value::Timestamp(_)) => "date",
    }
}

/// A libyaml failure as PyYAML's C loader reports it: `context: problem` at the
/// problem mark.
fn load_error(text: &str, e: &dbt_yaml::Error) -> Diagnostic {
    let message = e.to_string();
    let Some(mark) = e.location().map(|l| (l.line(), l.column())) else {
        if message.contains("more than one document") {
            return match second_document(text) {
                Some((line, column, explicit)) => {
                    let problem = if explicit {
                        SECOND_DOCUMENT
                    } else {
                        "did not find expected <document start>"
                    };
                    errors::syntax(text, line, column, problem)
                }
                None => errors::internal("a second YAML document with no position"),
            };
        }
        return errors::syntax_unmarked(&message);
    };
    let problem = if message.contains("recursion limit") {
        "nesting deeper than 100 levels".to_string()
    } else if message.contains("duplicate anchor") {
        "found duplicate anchor; first occurrence: second occurrence".to_string()
    } else if message.contains("unknown anchor") {
        "found undefined alias".to_string()
    } else {
        // `{problem} at line L column C[, {context} at line L column C]`
        let head = message.split(" at line ").next().unwrap_or("");
        let context = message
            .split_once(", ")
            .and_then(|(_, c)| c.split(" at line ").next())
            .filter(|c| c.starts_with("while "));
        match context {
            Some(c) => format!("{c}: {head}"),
            None => head.to_string(),
        }
    };
    errors::syntax(text, mark.0, mark.1, &problem)
}

const SECOND_DOCUMENT: &str =
    "expected a single document in the stream: but found another document";

/// Where the second document starts: its `---` (explicit), or the first content
/// line after a `...` end marker, which libyaml rejects without a `---`.
fn second_document(text: &str) -> Option<(usize, usize, bool)> {
    let (mut seen_content, mut ended) = (false, false);
    for (i, line) in text.split('\n').enumerate() {
        let is = |m: &str| {
            line.starts_with(m)
                && line
                    .get(3..)
                    .is_none_or(|r| r.is_empty() || r.starts_with([' ', '\t', '\r']))
        };
        let trimmed = line.trim();
        let body = !trimmed.is_empty() && !trimmed.starts_with('#') && !line.starts_with('%');
        if is("---") && (seen_content || ended) {
            return Some((i + 1, 1, true));
        }
        if is("...") {
            ended = true;
            seen_content = false;
        } else if body && ended {
            return Some((i + 1, line.len() - line.trim_start().len() + 1, false));
        } else if is("---") || body {
            seen_content = true;
        }
    }
    None
}
