use std::cell::RefCell;
use std::collections::{HashMap, HashSet};

use dbt_yaml::StringNode;
use diagnostic::Diagnostic;
use indexmap::IndexMap;

use crate::errors::{internal, refused, syntax};
use crate::key::{identity, json_key, py_repr};
use crate::node::{Entry, KeyKind, Kind, Node, Span, Value};
use crate::resolve::{plain, Refusal};

/// The deepest nesting the loader accepts (`MAX_YAML_NESTING` in Python).
const MAX_NESTING: usize = 100;

pub struct Builder<'a> {
    text: &'a str,
    anchors: RefCell<HashMap<String, Lead>>,
}

/// What the source text says about a node that `StringNode` discards.
#[derive(Clone, Copy, Default)]
struct Lead {
    tagged: bool,
    block: bool,
}

impl<'a> Builder<'a> {
    pub fn new(text: &'a str) -> Self {
        Builder {
            text,
            anchors: RefCell::default(),
        }
    }

    /// The node's own text: `StringNode` runs a span to the start of the next
    /// node, so the key's `:` and any comment after the node are cut here.
    fn span(&self, node: &StringNode, key: bool) -> Result<Span, Diagnostic> {
        let s = node.span();
        let start = s.start.index;
        let raw = self.text.get(start..s.end.index).unwrap_or("");
        let plain = match node {
            StringNode::Scalar(v, _) => Some(v.as_str()),
            _ => None,
        };
        let end = start + own_len(raw, key, self.lead(start)?.block, plain);
        Ok(Span {
            start,
            end,
            line: s.start.line,
            column: s.start.column,
        })
    }

    /// Tag and block style, read from the text at the node's start. An alias
    /// takes its anchor's, since the expanded copy keeps neither; anchors are
    /// recorded as the tree is walked, a mapping's keys before its values: an
    /// alias used as a key whose anchor is on an earlier value is refused.
    fn lead(&self, start: usize) -> Result<Lead, Diagnostic> {
        let mut pos = start;
        let mut lead = Lead::default();
        let mut names = Vec::new();
        loop {
            let rest = self.text.get(pos..).unwrap_or("");
            let Some(c) = rest.chars().next() else { break };
            let token = rest
                .find(|c: char| c.is_whitespace() || ",]}".contains(c))
                .unwrap_or(rest.len());
            match c {
                '!' => {
                    lead.tagged = true;
                    break;
                }
                '|' | '>' => {
                    lead.block = true;
                    break;
                }
                '&' => {
                    names.push(rest.get(1..token).unwrap_or("").to_string());
                    pos += token;
                }
                '*' => {
                    let name = rest.get(1..token).unwrap_or("");
                    lead = *self.anchors.borrow().get(name).ok_or_else(|| {
                        internal("an alias to an anchor the parse has not reached")
                    })?;
                    break;
                }
                '#' => pos += rest.find('\n').unwrap_or(rest.len()),
                c if c.is_whitespace() => pos += rest.len() - rest.trim_start().len(),
                _ => break,
            }
        }
        self.anchors
            .borrow_mut()
            .extend(names.into_iter().map(|n| (n, lead)));
        Ok(lead)
    }

    fn scalar(&self, node: &StringNode, span: Span) -> Result<Value, Diagnostic> {
        let lead = self.lead(span.start)?;
        if lead.tagged {
            return Err(self.tagged(span));
        }
        match node {
            StringNode::Scalar(s, _) if !lead.block => plain(s).map_err(|r| self.refusal(r, span)),
            StringNode::Scalar(s, _) | StringNode::QuotedScalar(s, _) => Ok(Value::Str(s.clone())),
            _ => Err(internal("a collection read as a scalar")),
        }
    }

    fn tagged(&self, span: Span) -> Diagnostic {
        refused(
            span.line,
            span.column,
            "an explicit YAML tag (quote the value instead)",
        )
    }

    fn refusal(&self, r: Refusal, span: Span) -> Diagnostic {
        let (line, col) = (span.line, span.column);
        match r {
            Refusal::Invalid(what) => syntax(self.text, line, col, what),
            Refusal::Uncarried(what) => refused(line, col, what),
            // As a key `<<` merges; as a value it has no constructor and raises.
            Refusal::Merge => syntax(
                self.text,
                line,
                col,
                "could not determine a constructor for the tag 'tag:yaml.org,2002:merge'",
            ),
            Refusal::Internal => internal("the YAML 1.1 resolver tables failed to build"),
        }
    }

    pub fn node(&self, node: &StringNode, depth: usize) -> Result<Node, Diagnostic> {
        let span = self.span(node, false)?;
        match node {
            StringNode::Scalar(..) | StringNode::QuotedScalar(..) => Ok(Node {
                span,
                kind: Kind::Scalar(self.scalar(node, span)?),
            }),
            StringNode::Sequence(items, _) => {
                self.nest(depth, span)?;
                if self.lead(span.start)?.tagged {
                    return Err(self.tagged(span));
                }
                let items = items
                    .iter()
                    .map(|i| self.node(i, depth + 1))
                    .collect::<Result<Vec<Node>, _>>()?;
                let end = self.collection_end(span, items.last().map(|n| n.span.end));
                let span = Span { end, ..span };
                Ok(Node {
                    span,
                    kind: Kind::Seq(items),
                })
            }
            StringNode::Mapping(pairs, _) => {
                self.nest(depth, span)?;
                if self.lead(span.start)?.tagged {
                    return Err(self.tagged(span));
                }
                let map = self.entries(pairs, depth, true)?;
                let end = self.collection_end(span, map.values().last().map(|e| e.value.span.end));
                Ok(Node {
                    span: Span { end, ..span },
                    kind: Kind::Map(map),
                })
            }
        }
    }

    /// A mapping's entries as `SafeConstructor` builds them: `<<` merges first
    /// (a sequence of mappings last to first), then the mapping's own keys, a
    /// later key replacing an earlier one in place. `scan` is
    /// `UniqueKeyLoader`'s duplicate check, which Python runs on a mapping it
    /// constructs and not on one it only merges from.
    fn entries(
        &self,
        pairs: &[(StringNode, StringNode)],
        depth: usize,
        scan: bool,
    ) -> Result<IndexMap<String, Entry>, Diagnostic> {
        let mut own = Vec::with_capacity(pairs.len());
        let mut merges = Vec::new();
        let mut seen = HashSet::new();
        // Python scans every key for a duplicate before it builds any value, so
        // a key we refuse is held until the scan is done.
        let mut refused_key = None;
        let mut unhashable = None;
        for (k, v) in pairs {
            let key_span = self.span(k, true)?;
            if self.is_merge_key(k, key_span)? {
                merges.push(v);
                continue;
            }
            let scalar = match k {
                StringNode::Scalar(..) | StringNode::QuotedScalar(..) => self.scalar(k, key_span),
                _ => {
                    unhashable.get_or_insert(key_span);
                    continue;
                }
            };
            let keyed = scalar.and_then(|value| {
                json_key(&value).ok_or_else(|| {
                    refused(key_span.line, key_span.column, "a timestamp mapping key")
                })
            });
            let (kind, key) = match keyed {
                Ok(keyed) => keyed,
                Err(e) => {
                    refused_key.get_or_insert(e);
                    continue;
                }
            };
            if scan && !seen.insert(identity(kind, &key)) {
                return Err(syntax(
                    self.text,
                    key_span.line,
                    key_span.column,
                    &format!("duplicate key: {}", py_repr(kind, &key)),
                ));
            }
            own.push((key, kind, key_span, v));
        }
        let mut map = IndexMap::new();
        for merge in merges {
            self.merge(merge, depth, &mut map)?;
        }
        if let Some(span) = unhashable {
            return Err(syntax(
                self.text,
                span.line,
                span.column,
                "while constructing a mapping: found unhashable key",
            ));
        }
        if let Some(e) = refused_key {
            return Err(e);
        }
        for (key, key_kind, key_span, v) in own {
            let entry = Entry {
                key_span,
                key_kind,
                key_text: self
                    .text
                    .get(key_span.start..key_span.end)
                    .unwrap_or("")
                    .into(),
                value: self.node(v, depth + 1)?,
            };
            insert(&mut map, key, entry)?;
        }
        Ok(map)
    }

    /// `<<` is the plain scalar `<<`, as the resolver reads it; quoted, it is a string.
    fn is_merge_key(&self, key: &StringNode, span: Span) -> Result<bool, Diagnostic> {
        let lead = self.lead(span.start)?;
        Ok(matches!(key, StringNode::Scalar(s, _) if s == "<<") && !lead.block && !lead.tagged)
    }

    /// `SafeConstructor.flatten_mapping` for one `<<` value.
    fn merge(
        &self,
        value: &StringNode,
        depth: usize,
        map: &mut IndexMap<String, Entry>,
    ) -> Result<(), Diagnostic> {
        let bad = |node: &StringNode, expected: &str| -> Result<Diagnostic, Diagnostic> {
            let s = self.span(node, false)?;
            let found = match node {
                StringNode::Sequence(..) => "sequence",
                _ => "scalar",
            };
            Ok(syntax(
                self.text,
                s.line,
                s.column,
                &format!("while constructing a mapping: expected {expected}, but found {found}"),
            ))
        };
        let mut from = |node: &StringNode| -> Result<(), Diagnostic> {
            let StringNode::Mapping(pairs, _) = node else {
                return Err(bad(node, "a mapping for merging")?);
            };
            let span = self.span(node, false)?;
            self.nest(depth + 1, span)?;
            if self.lead(span.start)?.tagged {
                return Err(self.tagged(span));
            }
            for (key, entry) in self.entries(pairs, depth + 1, false)? {
                insert(map, key, entry)?;
            }
            Ok(())
        };
        match value {
            StringNode::Mapping(..) => from(value),
            StringNode::Sequence(items, _) => items.iter().rev().try_for_each(from),
            _ => Err(bad(value, "a mapping or list of mappings for merging")?),
        }
    }

    /// A flow collection ends at its closing bracket, a block one with its last child.
    fn collection_end(&self, span: Span, last_child: Option<usize>) -> usize {
        let raw = self.text.get(span.start..span.end).unwrap_or("");
        match (flow_len(raw), last_child) {
            (Some(len), _) => span.start + len,
            (None, Some(end)) => end,
            (None, None) => span.end,
        }
    }

    fn nest(&self, depth: usize, span: Span) -> Result<(), Diagnostic> {
        if depth >= MAX_NESTING {
            return Err(syntax(
                self.text,
                span.line,
                span.column,
                &format!("nesting deeper than {MAX_NESTING} levels"),
            ));
        }
        Ok(())
    }
}

fn own_len(raw: &str, key: bool, block: bool, plain: Option<&str>) -> usize {
    if raw.starts_with('*') {
        return raw.find(char::is_whitespace).unwrap_or(raw.len());
    }
    let at = prefix_len(raw);
    let body = raw.get(at..).unwrap_or("");
    if block {
        return at + block_len(body);
    }
    if let Some(q) = body.chars().next().filter(|c| matches!(c, '"' | '\'')) {
        let mut chars = body.char_indices().skip(1).peekable();
        while let Some((i, c)) = chars.next() {
            if q == '"' && c == '\\' {
                chars.next();
            } else if c == q {
                if q == '\'' && chars.peek().is_some_and(|(_, n)| *n == '\'') {
                    chars.next();
                } else {
                    return at + i + 1;
                }
            }
        }
        return raw.trim_end().len();
    }
    if let Some(end) = plain.and_then(|value| plain_len(body, value)) {
        return at + end;
    }
    let mut prev = ' ';
    let mut chars = body.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        let cut = (key && c == ':' && chars.peek().is_none_or(|(_, n)| n.is_whitespace()))
            || (!block && c == '#' && prev.is_whitespace());
        if cut {
            return at + body.get(..i).unwrap_or("").trim_end().len();
        }
        prev = c;
    }
    raw.trim_end().len()
}

fn prefix_len(raw: &str) -> usize {
    let mut at = 0;
    while raw
        .get(at..)
        .is_some_and(|r| r.starts_with(['&', '*', '!']))
    {
        let rest = raw.get(at..).unwrap_or("");
        at += rest.find(char::is_whitespace).unwrap_or(rest.len());
        let rest = raw.get(at..).unwrap_or("");
        at += rest.len() - rest.trim_start().len();
    }
    at
}

/// A block scalar ends at its last line indented as far as its content; blank
/// lines and less-indented comment lines after it are not part of it.
fn block_len(body: &str) -> usize {
    let indent = |l: &str| l.len() - l.trim_start().len();
    let mut lines = body.split_inclusive('\n');
    let header = lines.next().map_or(0, str::len);
    let rest: Vec<&str> = lines.collect();
    let Some(content) = rest
        .iter()
        .find(|l| !l.trim().is_empty())
        .map(|l| indent(l))
    else {
        return body.trim_end().len().max(header.saturating_sub(1));
    };
    let mut end = header;
    let mut offset = header;
    for line in &rest {
        offset += line.len();
        if !line.trim().is_empty() && indent(line) >= content {
            end = offset;
        }
    }
    body.get(..end).unwrap_or(body).trim_end().len()
}

fn flow_len(raw: &str) -> Option<usize> {
    let at = prefix_len(raw);
    let body = raw.get(at..)?;
    if !body.starts_with(['[', '{']) {
        return None;
    }
    let (mut depth, mut quote) = (0_usize, None);
    // A quote opens only where a flow item can start; `it's` is plain text.
    let (mut prev, mut item_start) = (' ', true);
    let mut chars = body.char_indices();
    while let Some((i, c)) = chars.next() {
        match quote {
            Some(q) if c == '\\' && q == '"' => {
                chars.next();
            }
            Some(q) if c == q => quote = None,
            Some(_) => {}
            None if c == '#' && prev.is_whitespace() => {
                for (_, n) in chars.by_ref() {
                    if n == '\n' {
                        break;
                    }
                }
            }
            None if (c == '"' || c == '\'') && item_start => quote = Some(c),
            None if c == '[' || c == '{' => depth += 1,
            None if c == ']' || c == '}' => {
                depth = depth.saturating_sub(1);
                if depth == 0 {
                    return Some(at + i + 1);
                }
            }
            None => {}
        }
        if quote.is_none() && !c.is_whitespace() {
            item_start = matches!(c, '[' | '{' | ',' | ':');
        }
        prev = c;
    }
    None
}

/// A plain scalar's text is its value with line folding, so its end is found by
/// matching the value's words through the source.
fn plain_len(body: &str, value: &str) -> Option<usize> {
    let mut pos = 0;
    for word in value.split_whitespace() {
        pos += body.get(pos..)?.len() - body.get(pos..)?.trim_start().len();
        if !body.get(pos..)?.starts_with(word) {
            return None;
        }
        pos += word.len();
    }
    Some(pos)
}

/// Adds an entry; a key already there is replaced in place. Keys that read
/// alike as JSON text but are different keys in Python (`1` and `'1'`), or the
/// reverse (`1` and `1.0`), have no one-key map form and refuse.
fn insert(map: &mut IndexMap<String, Entry>, key: String, entry: Entry) -> Result<(), Diagnostic> {
    let numeric = |k: KeyKind| matches!(k, KeyKind::Int | KeyKind::Float | KeyKind::Bool);
    let id = identity(entry.key_kind, &key);
    let collides = match map.get(&key) {
        Some(prior) => prior.key_kind != entry.key_kind,
        None => {
            numeric(entry.key_kind)
                && map
                    .iter()
                    .any(|(k, e)| numeric(e.key_kind) && identity(e.key_kind, k) == id)
        }
    };
    if collides {
        let s = entry.key_span;
        return Err(refused(
            s.line,
            s.column,
            "a mapping key that is the same key as another in Python but not in JSON, or the reverse",
        ));
    }
    map.insert(key, entry);
    Ok(())
}
