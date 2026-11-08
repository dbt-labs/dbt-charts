//! Navigation and edits over a mutable [`Node`] tree, and the span-addressed
//! edit record.

use indexmap::IndexMap;
use serde_json::Value as Json;
use yaml::{Entry, Kind, Node, Span, Value};

/// One step of a document path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Seg {
    Key(String),
    Index(usize),
}

/// An applied change, addressed in the document as it stood when it applied
/// and by the source spans of what it touched. The engine does not rewrite
/// text: a `dct migrate` writer replays these in order. This is the contract
/// that writer consumes.
#[derive(Debug, Clone, PartialEq)]
pub enum Edit {
    /// A value moved, renamed in place (`from` and `to` share a parent), or
    /// respelled in place (`from == to`).
    Move {
        from: Vec<Seg>,
        to: Vec<Seg>,
        key_span: Span,
        value_span: Span,
        /// The value at `from` before the move.
        before: Json,
        /// The value as it landed at `to`; `None` when a rewrite dropped it.
        after: Option<Json>,
        /// The declaration carries a value rewrite (a named function).
        rewrite: bool,
        /// The declaration carries a value map.
        value_map: bool,
        /// A string became a mapping under the same key (`before` is the string).
        expansion: bool,
        /// The declared old path (`a.*.b`), for the writer's refusal texts.
        declared: String,
    },
    /// A value a document-aware rewrite owes a position no move fires at,
    /// creating the mappings it passes through; `None` strikes the key and the
    /// mappings it empties.
    Write { path: Vec<Seg>, value: Option<Json> },
    /// A key struck together with the ancestors it emptied: the shallowest entry removed.
    Delete {
        path: Vec<Seg>,
        key_span: Span,
        value_span: Span,
    },
}

pub const SYNTHETIC: Span = Span {
    start: 0,
    end: 0,
    line: 0,
    column: 0,
};

pub fn empty_map() -> Node {
    Node {
        span: SYNTHETIC,
        kind: Kind::Map(IndexMap::new()),
    }
}

pub fn map(node: &Node) -> Option<&IndexMap<String, Entry>> {
    match &node.kind {
        Kind::Map(m) => Some(m),
        _ => None,
    }
}

pub fn map_mut(node: &mut Node) -> Option<&mut IndexMap<String, Entry>> {
    match &mut node.kind {
        Kind::Map(m) => Some(m),
        _ => None,
    }
}

pub fn child<'a>(node: &'a Node, key: &str) -> Option<&'a Node> {
    map(node)?.get(key).map(|e| &e.value)
}

/// The value at `path`; a numeric segment indexes a sequence.
pub fn at<'a>(root: &'a Node, path: &[String]) -> Option<&'a Node> {
    let mut node = root;
    for part in path {
        node = match &node.kind {
            Kind::Map(m) => &m.get(part)?.value,
            Kind::Seq(s) => s.get(part.parse::<usize>().ok()?)?,
            Kind::Scalar(_) => return None,
        };
    }
    Some(node)
}

pub fn at_mut<'a>(root: &'a mut Node, path: &[String]) -> Option<&'a mut Node> {
    let mut node = root;
    for part in path {
        node = match &mut node.kind {
            Kind::Map(m) => &mut m.get_mut(part)?.value,
            Kind::Seq(s) => s.get_mut(part.parse::<usize>().ok()?)?,
            Kind::Scalar(_) => return None,
        };
    }
    Some(node)
}

/// `path` with each segment typed by the container it indexes.
pub fn segs(root: &Node, path: &[String]) -> Vec<Seg> {
    let mut node = Some(root);
    path.iter()
        .map(|part| {
            let seq = node.is_some_and(|n| matches!(n.kind, Kind::Seq(_)));
            node = node.and_then(|n| at(n, std::slice::from_ref(part)));
            match (seq, part.parse::<usize>()) {
                (true, Ok(i)) => Seg::Index(i),
                _ => Seg::Key(part.clone()),
            }
        })
        .collect()
}

/// The key span and value span of the entry at `path` (a sequence item's key span is its own).
pub fn spans(root: &Node, path: &[String]) -> (Span, Span) {
    let value = at(root, path).map_or(SYNTHETIC, |n| n.span);
    let key = match path.split_last() {
        Some((last, parent)) => at(root, parent)
            .and_then(map)
            .and_then(|m| m.get(last))
            .map_or(value, |e| e.key_span),
        None => value,
    };
    (key, value)
}

pub fn scalar(span: Span, value: Value) -> Node {
    Node {
        span,
        kind: Kind::Scalar(value),
    }
}

pub fn from_json(json: &Json, span: Span) -> Node {
    let kind = match json {
        Json::Null => Kind::Scalar(Value::Null),
        Json::Bool(b) => Kind::Scalar(Value::Bool(*b)),
        Json::Number(n) => Kind::Scalar(Value::Number(n.clone())),
        Json::String(s) => Kind::Scalar(Value::Str(s.clone())),
        Json::Array(a) => Kind::Seq(a.iter().map(|j| from_json(j, span)).collect()),
        Json::Object(o) => Kind::Map(
            o.iter()
                .map(|(k, v)| (k.clone(), Entry::string_key(span, k, from_json(v, span))))
                .collect(),
        ),
    };
    Node { span, kind }
}

/// Every mapping key at every depth.
pub fn document_keys(node: &Node) -> std::collections::HashSet<&str> {
    let mut keys = std::collections::HashSet::new();
    let mut pending = vec![node];
    while let Some(n) = pending.pop() {
        match &n.kind {
            Kind::Map(m) => {
                for (k, e) in m {
                    keys.insert(k.as_str());
                    pending.push(&e.value);
                }
            }
            Kind::Seq(s) => pending.extend(s),
            Kind::Scalar(_) => {}
        }
    }
    keys
}

/// Whether the key chain `tail` exists below `node`, following mappings only.
pub fn tail_present(node: &Node, tail: &[String]) -> bool {
    tail_value(node, tail).is_some()
}

pub fn tail_value<'a>(node: &'a Node, tail: &[String]) -> Option<&'a Node> {
    let mut cur = node;
    for part in tail {
        cur = child(cur, part)?;
    }
    Some(cur)
}

/// What a tail deletion removed: the shallowest entry struck, as an index into the tail.
pub struct Struck {
    pub emptied: bool,
    pub top: Option<(usize, Span, Span)>,
}

/// Deletes `tail` below `node`; a mapping the deletion empties goes with it.
pub fn try_delete_tail(node: &mut Node, tail: &[String], depth: usize) -> Struck {
    let none = Struck {
        emptied: false,
        top: None,
    };
    let Some(m) = map_mut(node) else { return none };
    let Some((first, rest)) = tail.split_first() else {
        return none;
    };
    if rest.is_empty() {
        return match m.shift_remove(first) {
            Some(e) => Struck {
                emptied: m.is_empty(),
                top: Some((depth, e.key_span, e.value.span)),
            },
            None => none,
        };
    }
    let Some(entry) = m.get_mut(first) else {
        return none;
    };
    let inner = try_delete_tail(&mut entry.value, rest, depth + 1);
    if inner.emptied {
        if let Some(e) = m.shift_remove(first) {
            return Struck {
                emptied: m.is_empty(),
                top: Some((depth, e.key_span, e.value.span)),
            };
        }
    }
    Struck {
        emptied: false,
        top: inner.top,
    }
}

/// Sets `value` at `path` below `node`, creating the mappings it passes
/// through (a null or scalar on the way gives way to one); `None` strikes the
/// key and the mappings and lists it empties instead. A list on the way is
/// indexed, never created.
pub fn write_leaf(node: &mut Node, path: &[String], value: Option<&Json>) {
    let Some((head, rest)) = path.split_first() else {
        return;
    };
    let m = match &mut node.kind {
        Kind::Seq(items) => {
            if let Some(item) = head.parse::<usize>().ok().and_then(|i| items.get_mut(i)) {
                write_leaf(item, rest, value);
            }
            return;
        }
        Kind::Map(m) => m,
        Kind::Scalar(_) => return,
    };
    if rest.is_empty() {
        match value {
            None => {
                m.shift_remove(head);
            }
            Some(v) => match m.get_mut(head) {
                Some(e) => e.value = from_json(v, SYNTHETIC),
                None => {
                    m.insert(
                        head.clone(),
                        Entry::string_key(SYNTHETIC, head, from_json(v, SYNTHETIC)),
                    );
                }
            },
        }
        return;
    }
    let container = m
        .get(head)
        .is_some_and(|e| matches!(e.value.kind, Kind::Map(_) | Kind::Seq(_)));
    if !container {
        if value.is_none() {
            return;
        }
        m.insert(
            head.clone(),
            Entry::string_key(SYNTHETIC, head, empty_map()),
        );
    }
    let Some(entry) = m.get_mut(head) else { return };
    write_leaf(&mut entry.value, rest, value);
    let emptied = match &entry.value.kind {
        Kind::Map(inner) => inner.is_empty(),
        Kind::Seq(inner) => inner.is_empty(),
        Kind::Scalar(_) => false,
    };
    if value.is_none() && emptied {
        m.shift_remove(head);
    }
}
