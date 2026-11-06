use indexmap::IndexMap;
use serde_json::{Map, Number, Value as Json};

/// A source region: byte offsets into the text and 1-based line and column of
/// the start. `end` stops at the node's last character, not at the next node.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Span {
    pub start: usize,
    pub end: usize,
    pub line: usize,
    pub column: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    /// Always finite; an integer when the text was one.
    Number(Number),
    Str(String),
    /// A YAML 1.1 timestamp as the ISO string a board dumps (`Z` for a zero
    /// offset, microseconds only when nonzero). Whether a date is allowed
    /// where it stands is the reader's call.
    Timestamp(String),
}

/// The scalar a map key resolves to under YAML 1.1. The map key itself is
/// always the JSON text of that scalar (`0x10` is `"16"`, `yes` is `"true"`),
/// so a typed reader parses a number key from the map key and needs this only
/// to tell `1` from `'1'`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyKind {
    Str,
    Int,
    Float,
    Bool,
    Null,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub key_span: Span,
    pub key_kind: KeyKind,
    /// The key as authored: quotes included, `1_000` not `1000`.
    pub key_text: String,
    pub value: Node,
}

impl Entry {
    /// An entry under a string key, for a node built rather than parsed.
    pub fn string_key(key_span: Span, key: &str, value: Node) -> Entry {
        Entry {
            key_span,
            key_kind: KeyKind::Str,
            key_text: key.to_string(),
            value,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Kind {
    Scalar(Value),
    Seq(Vec<Node>),
    Map(IndexMap<String, Entry>),
}

#[derive(Debug, Clone, PartialEq)]
pub struct Node {
    pub span: Span,
    pub kind: Kind,
}

impl Node {
    /// The node as JSON, the form the sweeps compare: a timestamp is its ISO
    /// string, a map keeps its authored key order.
    pub fn to_json(&self) -> Json {
        match &self.kind {
            Kind::Scalar(Value::Null) => Json::Null,
            Kind::Scalar(Value::Bool(b)) => Json::Bool(*b),
            Kind::Scalar(Value::Number(n)) => Json::Number(n.clone()),
            Kind::Scalar(Value::Str(s) | Value::Timestamp(s)) => Json::String(s.clone()),
            Kind::Seq(items) => Json::Array(items.iter().map(Node::to_json).collect()),
            Kind::Map(map) => Json::Object(
                map.iter()
                    .map(|(k, e)| (k.clone(), e.value.to_json()))
                    .collect::<Map<_, _>>(),
            ),
        }
    }
}
