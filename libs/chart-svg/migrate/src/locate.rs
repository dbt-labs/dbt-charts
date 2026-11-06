//! Where a move fires: the document walked in step with the source and
//! current grammars, each yield gated on identity (never validity).

use yaml::{Kind, Node, Value};

use crate::catalog::{current_grammar, grammar};
use crate::decl::{KeyPath, Mapped, Move, ScalarKey, ValueMap};
use crate::doc::{at, child, map};
use crate::schema::{Pos, Schema};

/// A source key a move rewrites: its concrete document path, and the segments
/// bound to the path's wildcards (board path first for a nested board).
#[derive(Debug, Clone, PartialEq)]
pub struct Loc {
    pub path: Vec<String>,
    pub bindings: Vec<String>,
}

/// Schema positions of the value at `path` below `node`, narrowed by each
/// mapping on the way down.
pub fn positions_along(
    node: &Node,
    path: &[String],
    schema: &'static Schema,
    positions: Vec<Pos>,
) -> Vec<Pos> {
    let mut positions = positions;
    let mut node = node;
    for part in path {
        match &node.kind {
            Kind::Map(m) => {
                positions = schema.plausible(&positions, node);
                positions = schema.child_positions(&positions, part);
                match m.get(part) {
                    Some(e) => node = &e.value,
                    None => break,
                }
            }
            Kind::Seq(items) => {
                positions = schema.item_positions(&positions);
                match part.parse::<usize>().ok().and_then(|i| items.get(i)) {
                    Some(n) => node = n,
                    None => break,
                }
            }
            Kind::Scalar(_) => break,
        }
    }
    positions
}

/// Document values reached by `parts` from `node`, with their concrete paths.
fn follow<'a>(node: &'a Node, parts: &[String]) -> Vec<(Vec<String>, &'a Node)> {
    let Some((head, rest)) = parts.split_first() else {
        return vec![(Vec::new(), node)];
    };
    let children: Vec<(String, &Node)> = match (&node.kind, head.as_str()) {
        (Kind::Map(m), "*") => m.iter().map(|(k, e)| (k.clone(), &e.value)).collect(),
        (Kind::Seq(s), "*") => s
            .iter()
            .enumerate()
            .map(|(i, n)| (i.to_string(), n))
            .collect(),
        (Kind::Map(m), h) => m
            .get(h)
            .map(|e| (h.to_string(), &e.value))
            .into_iter()
            .collect(),
        _ => Vec::new(),
    };
    let mut out = Vec::new();
    for (key, c) in children {
        for (mut path, v) in follow(c, rest) {
            path.insert(0, key.clone());
            out.push((path, v));
        }
    }
    out
}

pub struct Board {
    pub at: Vec<String>,
    pub positions: Vec<Pos>,
    pub live_positions: Vec<Pos>,
}

/// The root and every board nested in it that holds `lead` (`*`: every board),
/// with the positions each sits at in `schema` and `live`.
pub fn boards(
    doc: &Node,
    prefixes: &[KeyPath],
    lead: &str,
    schema: &'static Schema,
    live: &'static Schema,
) -> Vec<Board> {
    let mut out = Vec::new();
    let mut stack: Vec<Vec<String>> = vec![Vec::new()];
    while let Some(here) = stack.pop() {
        let Some(node) = at(doc, &here) else { continue };
        if lead == "*" || child(node, lead).is_some() {
            out.push(Board {
                positions: positions_along(doc, &here, schema, vec![schema.root()]),
                live_positions: positions_along(doc, &here, live, vec![live.root()]),
                at: here.clone(),
            });
        }
        let mut kids = Vec::new();
        for prefix in prefixes {
            for (path, c) in follow(node, prefix) {
                if map(c).is_some() {
                    kids.push([&here[..], &path[..]].concat());
                }
            }
        }
        stack.extend(kids.into_iter().rev());
    }
    out
}

#[allow(clippy::too_many_arguments)]
fn source_locations(
    node: &Node,
    parts: &[String],
    schema: &'static Schema,
    positions: &[Pos],
    live: &'static Schema,
    live_positions: &[Pos],
    base: &[String],
    bindings: &[String],
    out: &mut Vec<Loc>,
) {
    let Some((part, rest)) = parts.split_first() else {
        return;
    };
    let extend = |key: &str, b: &[String]| {
        (
            [base, std::slice::from_ref(&key.to_string())].concat(),
            [b, std::slice::from_ref(&key.to_string())].concat(),
        )
    };
    if rest.is_empty() {
        match (&node.kind, part.as_str()) {
            (Kind::Map(m), "*") => {
                for key in m.keys() {
                    let (path, b) = extend(key, bindings);
                    out.push(Loc { path, bindings: b });
                }
            }
            (Kind::Seq(s), "*") => {
                for i in 0..s.len() {
                    let (path, b) = extend(&i.to_string(), bindings);
                    out.push(Loc { path, bindings: b });
                }
            }
            (Kind::Map(m), p) => {
                if let Some(e) = m.get(p) {
                    let plausible = schema.plausible(positions, node);
                    let live_plausible = live.plausible(live_positions, node);
                    if schema.declares_tail(&plausible, std::slice::from_ref(part))
                        && !schema.open_map_claims(&plausible, live, &live_plausible, p, &e.value)
                    {
                        out.push(Loc {
                            path: [base, std::slice::from_ref(part)].concat(),
                            bindings: bindings.to_vec(),
                        });
                    }
                }
            }
            _ => {}
        }
        return;
    }
    let mut descend = |key: &str, c: &Node, wild: bool, kp: Vec<Pos>, lp: Vec<Pos>| {
        let (path, b) = extend(key, bindings);
        let b = if wild { b } else { bindings.to_vec() };
        source_locations(c, rest, schema, &kp, live, &lp, &path, &b, out);
    };
    match (&node.kind, part.as_str()) {
        (Kind::Map(m), "*") => {
            for (k, e) in m {
                descend(
                    k,
                    &e.value,
                    true,
                    schema.child_positions(positions, k),
                    live.child_positions(live_positions, k),
                );
            }
        }
        (Kind::Seq(s), "*") => {
            let (ip, lp) = (
                schema.item_positions(positions),
                live.item_positions(live_positions),
            );
            for (i, n) in s.iter().enumerate() {
                descend(&i.to_string(), n, true, ip.clone(), lp.clone());
            }
        }
        (Kind::Map(m), p) => {
            if let Some(e) = m.get(p) {
                descend(
                    p,
                    &e.value,
                    false,
                    schema.child_positions(positions, p),
                    live.child_positions(live_positions, p),
                );
            }
        }
        _ => {}
    }
}

pub fn is_scalar_key(node: &Node) -> Option<ScalarKey> {
    match &node.kind {
        Kind::Scalar(Value::Null | Value::Timestamp(_)) | Kind::Seq(_) | Kind::Map(_) => None,
        Kind::Scalar(_) => ScalarKey::of(&node.to_json()),
    }
}

/// True when `value_map` actually renames `value`: a current name is mapped to
/// itself, so presence alone does not say a spelling is retired.
pub fn identity_value_would_change(value_map: &ValueMap, value: &Node) -> bool {
    let Some(key) = is_scalar_key(value) else {
        return false;
    };
    value_map
        .get(&key)
        .is_some_and(|m| mapped_json(m) != value.to_json())
}

pub fn mapped_json(m: &Mapped) -> serde_json::Value {
    match m {
        Mapped::Str(s) => serde_json::Value::String(s.clone()),
        Mapped::Bool(b) => serde_json::Value::Bool(*b),
        Mapped::Int(i) => serde_json::Value::from(*i),
    }
}

/// `parts` at the location `bindings` name; a nested board's location leads
/// with that board's concrete path.
pub fn move_path(m: &Move, parts: &[String], bindings: &[String]) -> Result<Vec<String>, String> {
    let wild = parts.iter().filter(|p| *p == "*").count();
    let lead = if m.sub_board_prefixes.is_empty() {
        0
    } else {
        bindings.len().saturating_sub(wild)
    };
    let (head, tail) = bindings.split_at(lead.min(bindings.len()));
    let mut it = tail.iter();
    let mut out: Vec<String> = head.to_vec();
    for part in parts {
        if part == "*" {
            out.push(
                it.next()
                    .ok_or("Destination path has more wildcards than source path")?
                    .clone(),
            );
        } else {
            out.push(part.clone());
        }
    }
    if it.next().is_some() {
        return Err("Source path has more wildcards than destination path".into());
    }
    Ok(out)
}

/// `type:` of the innermost mapping along `path` that carries one.
fn enclosing_type<'a>(board: &'a Node, path: &[String]) -> Option<&'a Node> {
    let mut found = None;
    for depth in 1..path.len() {
        if let Some(t) = path
            .get(..depth)
            .and_then(|p| at(board, p))
            .and_then(|n| child(n, "type"))
        {
            found = Some(t);
        }
    }
    found
}

fn chart_type_matches(wanted: &str, found: Option<&Node>) -> bool {
    match found {
        None => wanted.is_empty(),
        Some(n) => matches!(&n.kind, Kind::Scalar(Value::Str(s)) if s == wanted),
    }
}

fn location_applies(doc: &Node, m: &Move, identity: bool, loc: &Loc) -> bool {
    if identity {
        let Some(current) = at(doc, &loc.path) else {
            return false;
        };
        if let Some(rw) = &m.rewrite {
            if rw(&current.to_json()) == crate::decl::Rewritten::Value(current.to_json()) {
                return false;
            }
        } else if let Some(vm) = &m.value_map {
            if !identity_value_would_change(vm, current) {
                return false;
            }
        }
    }
    match &m.chart_type {
        None => true,
        Some(want) => move_path(m, &m.old_path, &loc.bindings)
            .map(|p| chart_type_matches(want, enclosing_type(doc, &p)))
            .unwrap_or(false),
    }
}

/// Document positions `m` rewrites, gated on the source grammar.
pub fn move_source_locations(doc: &Node, m: &Move) -> Vec<Loc> {
    let (Some(schema), Some(live)) = (grammar(m.source), current_grammar()) else {
        return Vec::new();
    };
    if m.expansion.is_some() {
        return expansion_locations(doc, m)
            .into_iter()
            .map(|(_, loc)| loc)
            .collect();
    }
    let identity = m.old_path == m.new_path && (m.value_map.is_some() || m.rewrite.is_some());
    let lead = m.old_path.first().map_or("*", String::as_str);
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for b in boards(doc, &m.sub_board_prefixes, lead, schema, live) {
        let Some(board) = at(doc, &b.at) else {
            continue;
        };
        let mut found = Vec::new();
        source_locations(
            board,
            &m.old_path,
            schema,
            &b.positions,
            live,
            &b.live_positions,
            &b.at,
            &[],
            &mut found,
        );
        for loc in found {
            if !seen.insert(loc.path.clone()) {
                continue;
            }
            let loc = Loc {
                bindings: [&b.at[..], &loc.bindings[..]].concat(),
                path: loc.path,
            };
            if location_applies(doc, m, identity, &loc) {
                out.push(loc);
            }
        }
    }
    out
}

/// Where `m`'s expansion fires: the board it sits in, and the location relative to that board.
pub fn expansion_locations(doc: &Node, m: &Move) -> Vec<(Vec<String>, Loc)> {
    let (Some(schema), Some(live), Some(expansion)) =
        (grammar(m.source), current_grammar(), &m.expansion)
    else {
        return Vec::new();
    };
    let lead = m.old_path.first().map_or("*", String::as_str);
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for b in boards(doc, &m.sub_board_prefixes, lead, schema, live) {
        let Some(board) = at(doc, &b.at) else {
            continue;
        };
        let mut found = Vec::new();
        source_locations(
            board,
            &m.old_path,
            schema,
            &b.positions,
            live,
            &b.live_positions,
            &b.at,
            &[],
            &mut found,
        );
        for loc in found {
            let hit = at(doc, &loc.path).is_some_and(
                |v| matches!(&v.kind, Kind::Scalar(Value::Str(s)) if expansion.contains_key(s)),
            );
            if hit && seen.insert(loc.path.clone()) {
                out.push((b.at.clone(), loc));
            }
        }
    }
    out
}
