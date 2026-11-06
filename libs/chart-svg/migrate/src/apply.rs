//! Applying declarations to the working document, each change recorded as an
//! [`Edit`] and each author-facing consequence as a notice.

use serde_json::Value as Json;
use yaml::{Kind, Node, Value};

use crate::catalog::{current_grammar, grammar};
use crate::decl::{ConditionalMove, Deletion, MapKeyDeletion, Mapped, Move, Rewritten, ValueMap};
use crate::doc::{
    at, at_mut, child, empty_map, map, map_mut, scalar, segs, spans, tail_present, tail_value,
    try_delete_tail, Edit, Seg,
};
use crate::locate::{
    expansion_locations, is_scalar_key, mapped_json, move_path, move_source_locations,
    positions_along,
};
use crate::pyrepr::py_repr;
use crate::schema::{Pos, Schema};

#[derive(Debug, Clone, PartialEq)]
pub enum MigrationError {
    /// Two spellings of a renamed key collided.
    Conflict(String),
    /// A value would not map, a path would not resolve, or a shape was unexpected.
    Other(String),
}

impl MigrationError {
    pub fn message(&self) -> &str {
        match self {
            MigrationError::Conflict(m) | MigrationError::Other(m) => m,
        }
    }
}

fn other<T>(message: String) -> Result<T, MigrationError> {
    Err(MigrationError::Other(message))
}

pub fn format_path(path: &[String]) -> String {
    path.join(".")
}

/// The dotted path as Python's `!r` prints it.
fn quoted_path(path: &[String]) -> String {
    py_repr(&format_path(path))
}

#[derive(Default)]
pub struct Applied {
    pub notices: Vec<String>,
    pub edits: Vec<Edit>,
}

/// Rewrites a scalar through a move's value map; anything the map cannot cover raises.
fn mapped_value(map: &ValueMap, old: &[String], value: &Node) -> Result<Mapped, MigrationError> {
    is_scalar_key(value)
        .and_then(|k| map.get(&k))
        .cloned()
        .ok_or_else(|| {
            MigrationError::Other(format!(
                "Cannot migrate {}: value {} has no entry in the value map. Migrate this field manually.",
                quoted_path(old),
                py_value(value)
            ))
        })
}

fn py_value(n: &Node) -> String {
    match &n.kind {
        Kind::Scalar(Value::Str(s)) => py_repr(s),
        Kind::Scalar(Value::Null) => "None".into(),
        Kind::Scalar(Value::Bool(b)) => if *b { "True" } else { "False" }.into(),
        _ => n.to_json().to_string(),
    }
}

/// A rewrite sees JSON, where a timestamp is its ISO string. A string it hands
/// back unchanged at the position a timestamp held is that timestamp.
fn keep_timestamps(new: &mut Node, old: &Node) {
    match (&mut new.kind, &old.kind) {
        (Kind::Scalar(Value::Str(s)), Kind::Scalar(Value::Timestamp(t))) if s == t => {
            new.kind = Kind::Scalar(Value::Timestamp(t.clone()));
        }
        (Kind::Seq(a), Kind::Seq(b)) => {
            a.iter_mut().zip(b).for_each(|(n, o)| keep_timestamps(n, o))
        }
        (Kind::Map(a), Kind::Map(b)) => {
            for (k, e) in a.iter_mut() {
                if let Some(o) = b.get(k) {
                    keep_timestamps(&mut e.value, &o.value);
                }
            }
        }
        _ => {}
    }
}

fn mapped_node(m: &Mapped, span: yaml::Span) -> Node {
    scalar(
        span,
        match m {
            Mapped::Str(s) => Value::Str(s.clone()),
            Mapped::Bool(b) => Value::Bool(*b),
            Mapped::Int(i) => Value::Number((*i).into()),
        },
    )
}

/// The child of `parent` at `part`, made an empty mapping when absent or null.
fn descend<'a>(parent: &'a mut Node, part: &str, m: &Move) -> Result<&'a mut Node, MigrationError> {
    match &mut parent.kind {
        Kind::Seq(items) => {
            part.parse::<usize>().ok().and_then(|i| items.get_mut(i)).ok_or_else(|| {
                MigrationError::Other(format!(
                    "Cannot move {} to {}: list index {part:?} does not exist. Migrate this field manually.",
                    quoted_path(&m.old_path),
                    quoted_path(&m.new_path)
                ))
            })
        }
        Kind::Map(map) => {
            let entry = map.entry(part.to_string()).or_insert_with(|| {
                yaml::Entry::string_key(crate::doc::SYNTHETIC, part, empty_map())
            });
            if matches!(entry.value.kind, Kind::Scalar(Value::Null)) {
                entry.value = empty_map();
            }
            Ok(&mut entry.value)
        }
        Kind::Scalar(_) => other(format!(
            "Cannot move {} to {}: {part:?} must be a mapping or list. Migrate this field manually.",
            quoted_path(&m.old_path),
            quoted_path(&m.new_path)
        )),
    }
}

fn move_value(
    doc: &mut Node,
    source: &[String],
    destination: &[String],
    m: &Move,
) -> Result<Landed, MigrationError> {
    let (Some((dest_key, dest_parent)), Some((src_key, src_parent))) =
        (destination.split_last(), source.split_last())
    else {
        return other("empty move path".into());
    };
    let mut parent: &mut Node = doc;
    for part in dest_parent {
        let next = descend(parent, part, m)?;
        if matches!(next.kind, Kind::Scalar(_)) {
            return other(format!(
                "Cannot move {} to {}: {part:?} must be a mapping or list. Migrate this field manually.",
                quoted_path(&m.old_path),
                quoted_path(&m.new_path)
            ));
        }
        parent = next;
    }
    if matches!(parent.kind, Kind::Seq(_)) {
        if m.old_path != m.new_path {
            return other(format!(
                "Cannot move {} to {}: destination ends inside a list. Migrate this field manually.",
                quoted_path(&m.old_path),
                quoted_path(&m.new_path)
            ));
        }
        if let (Some(vm), Kind::Seq(items)) = (&m.value_map, &mut parent.kind) {
            let item = dest_key
                .parse::<usize>()
                .ok()
                .and_then(|i| items.get_mut(i));
            if let Some(item) = item {
                let new = mapped_value(vm, &m.old_path, item)?;
                let span = item.span;
                *item = mapped_node(&new, span);
                return Ok(Landed::Changed(mapped_json(&new)));
            }
        }
        return Ok(Landed::Same);
    }
    let same_slot = dest_parent == src_parent && dest_key == src_key;
    let occupied = child(parent, dest_key).is_some();
    if occupied && !same_slot {
        return Err(MigrationError::Conflict(format!(
            "Cannot move {} to {}: both fields exist. Choose one value and migrate manually.",
            quoted_path(&m.old_path),
            quoted_path(&m.new_path)
        )));
    }
    // Pop the source. The destination chain was created above, so the source
    // parent is read afresh; a list source parent shifts later indices.
    let popped = pop(doc, src_parent, src_key);
    let Some((key_span, mut value)) = popped else {
        return other(format!(
            "Cannot move {}: the source is gone",
            quoted_path(&m.old_path)
        ));
    };
    let mut changed = None;
    if let Some(rw) = &m.rewrite {
        match rw(&value.to_json()) {
            Rewritten::Drop => return Ok(Landed::Dropped),
            Rewritten::Refuse(message) => return other(message),
            Rewritten::Value(j) => {
                if j != value.to_json() {
                    changed = Some(j.clone());
                }
                let mut rewritten = crate::doc::from_json(&j, value.span);
                keep_timestamps(&mut rewritten, &value);
                value = rewritten;
            }
        }
    }
    if let Some(vm) = &m.value_map {
        let new = mapped_value(vm, &m.old_path, &value)?;
        changed = Some(mapped_json(&new));
        value = mapped_node(&new, value.span);
    }
    let Some(dest) = at_mut(doc, dest_parent).and_then(map_mut) else {
        return other(format!(
            "Cannot move {}: destination vanished",
            quoted_path(&m.old_path)
        ));
    };
    dest.insert(
        dest_key.clone(),
        yaml::Entry::string_key(key_span, dest_key, value),
    );
    Ok(changed.map_or(Landed::Same, Landed::Changed))
}

/// What a moved value became.
enum Landed {
    Dropped,
    Same,
    Changed(Json),
}

fn pop(doc: &mut Node, parent: &[String], key: &str) -> Option<(yaml::Span, Node)> {
    let p = at_mut(doc, parent)?;
    match &mut p.kind {
        Kind::Map(m) => m.shift_remove(key).map(|e| (e.key_span, e.value)),
        Kind::Seq(s) => {
            let i = key.parse::<usize>().ok().filter(|i| *i < s.len())?;
            let n = s.remove(i);
            Some((n.span, n))
        }
        Kind::Scalar(_) => None,
    }
}

/// Strikes the mappings a moved key left empty, up to the first still holding keys.
fn prune_emptied_ancestors(doc: &mut Node, source: &[String]) {
    let Some((_, ancestors)) = source.split_last() else {
        return;
    };
    let mut chain: Vec<(Vec<String>, String)> = Vec::new();
    for (depth, part) in ancestors.iter().enumerate() {
        let holder_path = source.get(..depth).unwrap_or_default();
        match at(doc, holder_path).map(|n| &n.kind) {
            Some(Kind::Seq(_)) => chain.clear(),
            Some(Kind::Map(m)) if m.contains_key(part) => {
                chain.push((holder_path.to_vec(), part.clone()))
            }
            _ => return,
        }
    }
    for (holder, part) in chain.into_iter().rev() {
        let empty = at(doc, &holder)
            .and_then(|h| child(h, &part))
            .is_some_and(|c| map(c).is_some_and(|m| m.is_empty()));
        if !empty {
            return;
        }
        if let Some(m) = at_mut(doc, &holder).and_then(map_mut) {
            m.shift_remove(&part);
        }
    }
}

fn is_plain_scalar(n: &Node) -> bool {
    matches!(
        n.kind,
        Kind::Scalar(Value::Str(_) | Value::Number(_) | Value::Bool(_))
    )
}

pub fn apply_move(doc: &mut Node, m: &Move, out: &mut Applied) -> Result<(), MigrationError> {
    if m.expansion.is_some() {
        return apply_expansion(doc, m, out);
    }
    for loc in move_source_locations(doc, m) {
        let destination =
            move_path(m, &m.new_path, &loc.bindings).map_err(MigrationError::Other)?;
        let Some((_, parent_path)) = loc.path.split_last() else {
            return other("empty location".into());
        };
        if at(doc, parent_path).and_then(map).is_some() {
            for tail in &m.conflicts_with {
                if at(doc, parent_path).is_some_and(|p| tail_present(p, tail)) {
                    return Err(MigrationError::Conflict(format!(
                        "Cannot move {} to {}: {} is also set. Choose one value and migrate manually.",
                        quoted_path(&m.old_path),
                        quoted_path(&m.new_path),
                        quoted_path(tail)
                    )));
                }
            }
        }
        let Some(current) = at(doc, &loc.path) else {
            continue;
        };
        if let (Some(note), Some(rw)) = (m.rewrite_note, &m.rewrite) {
            if rw(&current.to_json()) != Rewritten::Value(current.to_json()) {
                out.notices.push(note.to_string());
            }
        }
        let source = move_path(m, &m.old_path, &loc.bindings).map_err(MigrationError::Other)?;
        let moved_scalar = is_plain_scalar(current);
        let (key_span, value_span) = spans(doc, &loc.path);
        let from = segs(doc, &source);
        let before = current.to_json();
        let landed = move_value(doc, &loc.path, &destination, m)?;
        let after = match landed {
            Landed::Dropped => None,
            Landed::Same => Some(before.clone()),
            Landed::Changed(j) => Some(j),
        };
        out.edits.push(Edit::Move {
            from,
            to: segs(doc, &destination),
            key_span,
            value_span,
            before,
            after,
            rewrite: m.rewrite.is_some(),
            value_map: m.value_map.is_some(),
            expansion: false,
            declared: format_path(&m.old_path),
        });
        if m.rewrite.is_none()
            && moved_scalar
            && source.split_last().map(|s| s.1) != destination.split_last().map(|d| d.1)
        {
            prune_emptied_ancestors(doc, &source);
        }
    }
    Ok(())
}

fn apply_expansion(doc: &mut Node, m: &Move, out: &mut Applied) -> Result<(), MigrationError> {
    let (Some(expansion), Some(target)) = (&m.expansion, grammar(m.target)) else {
        return Ok(());
    };
    for (board_at, loc) in expansion_locations(doc, m) {
        let destination =
            move_path(m, &m.new_path, &loc.bindings).map_err(MigrationError::Other)?;
        let Some(board) = at(doc, &board_at) else {
            continue;
        };
        let Some(value) = at(board, &destination).and_then(|n| match &n.kind {
            Kind::Scalar(Value::Str(s)) => Some(s.clone()),
            _ => None,
        }) else {
            return other("expansion source is not a string".into());
        };
        let positions = positions_along(board, &destination, target, vec![target.root()]);
        let mut expanded = indexmap::IndexMap::new();
        let mut notes = Vec::new();
        for (leaf, leaf_value) in expansion.get(&value).map(Vec::as_slice).unwrap_or_default() {
            if target.declares_tail(&positions, std::slice::from_ref(&(*leaf).to_string())) {
                expanded.insert(
                    (*leaf).to_string(),
                    yaml::Entry::string_key(
                        crate::doc::SYNTHETIC,
                        leaf,
                        mapped_node(leaf_value, crate::doc::SYNTHETIC),
                    ),
                );
            } else if *leaf_value == Mapped::Bool(true) {
                if let Some(note) = m.drop_notes.get(leaf) {
                    notes.push((*note).to_string());
                }
            }
        }
        if expanded.is_empty() {
            return other(format!(
                "Cannot expand {} value {}: {} declares none of its leaves here.",
                quoted_path(&m.old_path),
                py_repr(&value),
                m.target
            ));
        }
        out.notices.extend(notes);
        let (key_span, value_span) = spans(doc, &loc.path);
        let json = Node {
            span: value_span,
            kind: Kind::Map(expanded),
        };
        out.edits.push(Edit::Move {
            from: segs(doc, &loc.path),
            to: segs(doc, &loc.path),
            key_span,
            value_span,
            before: Json::String(value.clone()),
            after: Some(json.to_json()),
            rewrite: false,
            value_map: false,
            expansion: true,
            declared: format_path(&m.old_path),
        });
        if let Some(slot) = at_mut(doc, &loc.path) {
            *slot = json;
        }
    }
    Ok(())
}

// ---- deletions ----

/// The gate pair a deletion needs: the source grammar and the current one,
/// each with the positions the walk has reached.
struct Walk {
    schema: &'static Schema,
    live: &'static Schema,
}

pub fn apply_deletions(doc: &mut Node, deletions: &[&Deletion], out: &mut Applied) {
    let Some(first) = deletions.first() else {
        return;
    };
    let (Some(schema), Some(live)) = (grammar(first.source), current_grammar()) else {
        return;
    };
    let w = Walk { schema, live };
    let mut prefix: Vec<Seg> = Vec::new();
    delete_tails(
        doc,
        deletions,
        &w,
        &[schema.root()],
        &[live.root()],
        &mut prefix,
        out,
    );
}

fn live_declares_tail(w: &Walk, live_positions: &[Pos], node: &Node, tail: &[String]) -> bool {
    w.live
        .declares_tail(&w.live.plausible(live_positions, node), tail)
}

fn fires(w: &Walk, d: &Deletion, node: &Node, matching: &[Pos], live_positions: &[Pos]) -> bool {
    if let Some(ct) = &d.chart_type {
        let is_type = child(node, "type")
            .is_some_and(|t| matches!(&t.kind, Kind::Scalar(Value::Str(s)) if s == ct));
        if !is_type || !w.schema.declares_chart_type(matching, ct) {
            return false;
        }
    }
    w.schema.declares_tail(matching, &d.path)
        && !live_declares_tail(w, live_positions, node, &d.path)
}

fn delete_tails(
    node: &mut Node,
    deletions: &[&Deletion],
    w: &Walk,
    positions: &[Pos],
    live_positions: &[Pos],
    prefix: &mut Vec<Seg>,
    out: &mut Applied,
) {
    match &node.kind {
        Kind::Map(_) => {
            let present: Vec<&&Deletion> = deletions
                .iter()
                .filter(|d| tail_present(node, &d.path))
                .collect();
            let matching = if present.is_empty() {
                Vec::new()
            } else {
                w.schema.matching(positions, w.live, live_positions, node)
            };
            let firing: Vec<&Deletion> = present
                .into_iter()
                .filter(|d| fires(w, d, node, &matching, live_positions))
                .map(|d| &**d)
                .collect();
            for d in firing {
                if tail_present(node, &d.path) {
                    if let Some(reason) = &d.reason {
                        out.notices
                            .push(format!("`{}` was removed: {reason}", format_path(&d.path)));
                    }
                }
                let doc_here = prefix.clone();
                let struck = try_delete_tail(node, &d.path, 0);
                if let Some((depth, key_span, value_span)) = struck.top {
                    let mut path = doc_here;
                    path.extend(d.path.iter().take(depth + 1).map(|k| Seg::Key(k.clone())));
                    out.edits.push(Edit::Delete {
                        path,
                        key_span,
                        value_span,
                    });
                }
            }
            let keys: Vec<String> = map(node)
                .map(|m| m.keys().cloned().collect())
                .unwrap_or_default();
            for key in keys {
                let (kp, lp) = (
                    w.schema.child_positions(positions, &key),
                    w.live.child_positions(live_positions, &key),
                );
                let Some(entry) = map_mut(node).and_then(|m| m.get_mut(&key)) else {
                    continue;
                };
                let was_empty = map(&entry.value).is_some_and(|m| m.is_empty());
                prefix.push(Seg::Key(key.clone()));
                delete_tails(&mut entry.value, deletions, w, &kp, &lp, prefix, out);
                prefix.pop();
                let now_empty = map(&entry.value).is_some_and(|m| m.is_empty());
                if now_empty && !was_empty {
                    if let Some(e) = map_mut(node).and_then(|m| m.shift_remove(&key)) {
                        let mut path = prefix.clone();
                        path.push(Seg::Key(key));
                        out.edits.push(Edit::Delete {
                            path,
                            key_span: e.key_span,
                            value_span: e.value.span,
                        });
                    }
                }
            }
        }
        Kind::Seq(_) => {
            let (ip, lp) = (
                w.schema.item_positions(positions),
                w.live.item_positions(live_positions),
            );
            let len = if let Kind::Seq(s) = &node.kind {
                s.len()
            } else {
                0
            };
            for i in 0..len {
                if let Kind::Seq(s) = &mut node.kind {
                    if let Some(item) = s.get_mut(i) {
                        prefix.push(Seg::Index(i));
                        delete_tails(item, deletions, w, &ip, &lp, prefix, out);
                        prefix.pop();
                    }
                }
            }
        }
        Kind::Scalar(_) => {}
    }
}

/// Read-only mirror of `delete_tails`: each deletion that would strike something.
pub fn deletions_that_fire(doc: &Node, deletions: &[&Deletion]) -> bool {
    let Some(first) = deletions.first() else {
        return false;
    };
    let (Some(schema), Some(live)) = (grammar(first.source), current_grammar()) else {
        return false;
    };
    let w = Walk { schema, live };
    walk_deletions(doc, deletions, &w, &[schema.root()], &[live.root()])
}

fn walk_deletions(
    node: &Node,
    deletions: &[&Deletion],
    w: &Walk,
    positions: &[Pos],
    live_positions: &[Pos],
) -> bool {
    match &node.kind {
        Kind::Map(m) => {
            let present: Vec<&&Deletion> = deletions
                .iter()
                .filter(|d| tail_present(node, &d.path))
                .collect();
            if !present.is_empty() {
                let matching = w.schema.matching(positions, w.live, live_positions, node);
                if present
                    .iter()
                    .any(|d| fires(w, d, node, &matching, live_positions))
                {
                    return true;
                }
            }
            m.iter().any(|(k, e)| {
                walk_deletions(
                    &e.value,
                    deletions,
                    w,
                    &w.schema.child_positions(positions, k),
                    &w.live.child_positions(live_positions, k),
                )
            })
        }
        Kind::Seq(items) => {
            let (ip, lp) = (
                w.schema.item_positions(positions),
                w.live.item_positions(live_positions),
            );
            items
                .iter()
                .any(|i| walk_deletions(i, deletions, w, &ip, &lp))
        }
        Kind::Scalar(_) => false,
    }
}

// ---- conditional moves ----

pub fn apply_conditional_move(
    doc: &mut Node,
    rule: &ConditionalMove,
    out: &mut Applied,
) -> Result<(), MigrationError> {
    let Some(schema) = grammar(rule.source) else {
        return Ok(());
    };
    let mut prefix: Vec<Seg> = Vec::new();
    cond_recursive(
        doc,
        rule,
        "<root>",
        schema,
        &[schema.root()],
        &mut prefix,
        out,
    )
}

fn is_chart(
    node: &Node,
    rule: &ConditionalMove,
    schema: &'static Schema,
    positions: &[Pos],
) -> bool {
    child(node, "type")
        .is_some_and(|t| matches!(&t.kind, Kind::Scalar(Value::Str(s)) if s == rule.chart_type))
        && schema.declares_chart_type(positions, rule.chart_type)
}

fn cond_recursive(
    node: &mut Node,
    rule: &ConditionalMove,
    last_key: &str,
    schema: &'static Schema,
    positions: &[Pos],
    path: &mut Vec<Seg>,
    out: &mut Applied,
) -> Result<(), MigrationError> {
    match &node.kind {
        Kind::Map(_) => {
            if is_chart(node, rule, schema, positions) {
                try_conditional_move(node, rule, last_key, path, out)?;
            }
            let keys: Vec<String> = map(node)
                .map(|m| m.keys().cloned().collect())
                .unwrap_or_default();
            for key in keys {
                let kp = schema.child_positions(positions, &key);
                if let Some(e) = map_mut(node).and_then(|m| m.get_mut(&key)) {
                    path.push(Seg::Key(key.clone()));
                    cond_recursive(&mut e.value, rule, &key, schema, &kp, path, out)?;
                    path.pop();
                }
            }
        }
        Kind::Seq(items) => {
            let ip = schema.item_positions(positions);
            for i in 0..items.len() {
                if let Kind::Seq(s) = &mut node.kind {
                    if let Some(item) = s.get_mut(i) {
                        path.push(Seg::Index(i));
                        cond_recursive(item, rule, last_key, schema, &ip, path, out)?;
                        path.pop();
                    }
                }
            }
        }
        Kind::Scalar(_) => {}
    }
    Ok(())
}

fn try_conditional_move(
    chart: &mut Node,
    rule: &ConditionalMove,
    chart_id: &str,
    chart_path: &[Seg],
    out: &mut Applied,
) -> Result<(), MigrationError> {
    let Some((old_key, old_parent_path)) = rule.old_tail.split_last() else {
        return Ok(());
    };
    let Some(old_parent) = tail_value(chart, old_parent_path).filter(|p| map(p).is_some()) else {
        return Ok(());
    };
    let Some(old_entry) = map(old_parent).and_then(|m| m.get(old_key)) else {
        return Ok(());
    };
    let old_value = old_entry.value.clone();
    let sibling = tail_value(chart, &rule.sibling_tail).filter(|s| map(s).is_some());
    let delete_old = |chart: &mut Node, out: &mut Applied| {
        let struck = try_delete_tail(chart, &rule.old_tail, 0);
        if let Some((depth, key_span, value_span)) = struck.top {
            let mut path = chart_path.to_vec();
            path.extend(
                rule.old_tail
                    .iter()
                    .take(depth + 1)
                    .map(|k| Seg::Key(k.clone())),
            );
            out.edits.push(Edit::Delete {
                path,
                key_span,
                value_span,
            });
        }
    };
    let Some(sibling) = sibling else {
        delete_old(chart, out);
        out.notices.push(
            rule.drop_warning
                .replace("{chart!r}", &py_repr(chart_id))
                .replace("{chart}", chart_id),
        );
        return Ok(());
    };
    let rest = rule
        .new_tail
        .get(rule.sibling_tail.len()..)
        .unwrap_or_default();
    let Some((last, mid)) = rest.split_last() else {
        return Ok(());
    };
    if tail_value(sibling, mid).is_some_and(|c| map(c).is_some_and(|m| m.contains_key(last))) {
        return Err(MigrationError::Conflict(format!(
            "Cannot conditionally move {} to {} in chart {}: both fields exist. Choose one value and migrate manually.",
            quoted_path(&rule.old_tail),
            quoted_path(&rule.new_tail),
            py_repr(chart_id)
        )));
    }
    let (key_span, value_span) = (old_entry.key_span, old_value.span);
    let from_segs: Vec<Seg> = [
        chart_path,
        &rule
            .old_tail
            .iter()
            .map(|k| Seg::Key(k.clone()))
            .collect::<Vec<_>>()[..],
    ]
    .concat();
    delete_old(chart, out);
    // `delete_old` records a plain delete; the value lands below, so the pair replays as a move.
    out.edits.pop();
    let sibling_path: Vec<String> = rule.sibling_tail.clone();
    let mut dest = at_mut(chart, &sibling_path);
    for part in mid {
        dest = dest.and_then(|d| {
            let m = map_mut(d)?;
            let e = m.entry(part.clone()).or_insert_with(|| {
                yaml::Entry::string_key(crate::doc::SYNTHETIC, part, empty_map())
            });
            if map(&e.value).is_none() {
                e.value = empty_map();
            }
            Some(&mut e.value)
        });
    }
    if let Some(m) = dest.and_then(map_mut) {
        m.insert(
            last.clone(),
            yaml::Entry::string_key(key_span, last, old_value.clone()),
        );
    }
    let to: Vec<Seg> = [
        chart_path,
        &rule
            .new_tail
            .iter()
            .map(|k| Seg::Key(k.clone()))
            .collect::<Vec<_>>()[..],
    ]
    .concat();
    out.edits.push(Edit::Move {
        from: from_segs,
        to,
        key_span,
        value_span,
        before: old_value.to_json(),
        after: Some(old_value.to_json()),
        rewrite: false,
        value_map: false,
        expansion: false,
        declared: format_path(&rule.old_tail),
    });
    Ok(())
}

/// Read-only mirror: would `rule` fire anywhere?
pub fn conditional_move_fires(doc: &Node, rule: &ConditionalMove) -> bool {
    let Some(schema) = grammar(rule.source) else {
        return false;
    };
    walk_cond(doc, rule, schema, &[schema.root()])
}

fn walk_cond(
    node: &Node,
    rule: &ConditionalMove,
    schema: &'static Schema,
    positions: &[Pos],
) -> bool {
    match &node.kind {
        Kind::Map(m) => {
            (is_chart(node, rule, schema, positions) && tail_present(node, &rule.old_tail))
                || m.iter().any(|(k, e)| {
                    walk_cond(
                        &e.value,
                        rule,
                        schema,
                        &schema.child_positions(positions, k),
                    )
                })
        }
        Kind::Seq(items) => {
            let ip = schema.item_positions(positions);
            items.iter().any(|i| walk_cond(i, rule, schema, &ip))
        }
        Kind::Scalar(_) => false,
    }
}

// ---- map key deletions ----

pub fn apply_map_key_deletions(
    doc: &mut Node,
    deletions: &[&MapKeyDeletion],
    out: &mut Applied,
) -> bool {
    let mut sources: Vec<&'static str> = Vec::new();
    for d in deletions {
        if !sources.contains(&d.source) {
            sources.push(d.source);
        }
    }
    let mut struck = false;
    for source in sources {
        let Some(schema) = grammar(source) else {
            continue;
        };
        let group: Vec<&&MapKeyDeletion> =
            deletions.iter().filter(|d| d.source == source).collect();
        let mut prefix: Vec<Seg> = Vec::new();
        struck |= delete_map_keys(doc, &group, schema, &[schema.root()], &mut prefix, out);
    }
    struck
}

fn delete_map_keys(
    node: &mut Node,
    deletions: &[&&MapKeyDeletion],
    schema: &'static Schema,
    positions: &[Pos],
    prefix: &mut Vec<Seg>,
    out: &mut Applied,
) -> bool {
    let mut struck = false;
    match &node.kind {
        Kind::Map(_) => {
            let plausible = schema.plausible(positions, node);
            for d in deletions {
                if !schema.declares_tail(&plausible, &d.container_path) {
                    continue;
                }
                let Some(container) = tail_value(node, &d.container_path).and_then(map) else {
                    continue;
                };
                let hits: Vec<&(String, String)> = d
                    .messages
                    .iter()
                    .filter(|(k, _)| container.contains_key(k))
                    .collect();
                for (key, message) in hits {
                    let mut cpath = prefix.clone();
                    cpath.extend(d.container_path.iter().map(|k| Seg::Key(k.clone())));
                    let removed = at_mut(node, &d.container_path)
                        .and_then(map_mut)
                        .and_then(|m| m.shift_remove(key));
                    if let Some(e) = removed {
                        out.notices.push(message.clone());
                        let mut path = cpath;
                        path.push(Seg::Key(key.clone()));
                        out.edits.push(Edit::Delete {
                            path,
                            key_span: e.key_span,
                            value_span: e.value.span,
                        });
                        struck = true;
                    }
                }
            }
            let keys: Vec<String> = map(node)
                .map(|m| m.keys().cloned().collect())
                .unwrap_or_default();
            for key in keys {
                let kp = schema.child_positions(positions, &key);
                if let Some(e) = map_mut(node).and_then(|m| m.get_mut(&key)) {
                    prefix.push(Seg::Key(key));
                    struck |= delete_map_keys(&mut e.value, deletions, schema, &kp, prefix, out);
                    prefix.pop();
                }
            }
        }
        Kind::Seq(_) => {
            let ip = schema.item_positions(positions);
            let len = if let Kind::Seq(s) = &node.kind {
                s.len()
            } else {
                0
            };
            for i in 0..len {
                if let Kind::Seq(s) = &mut node.kind {
                    if let Some(item) = s.get_mut(i) {
                        prefix.push(Seg::Index(i));
                        struck |= delete_map_keys(item, deletions, schema, &ip, prefix, out);
                        prefix.pop();
                    }
                }
            }
        }
        Kind::Scalar(_) => {}
    }
    struck
}

pub fn map_key_deletion_fires(doc: &Node, deletions: &[&MapKeyDeletion]) -> bool {
    let Some(first) = deletions.first() else {
        return false;
    };
    let Some(schema) = grammar(first.source) else {
        return false;
    };
    would_fire(doc, deletions, schema, &[schema.root()])
}

fn would_fire(
    node: &Node,
    deletions: &[&MapKeyDeletion],
    schema: &'static Schema,
    positions: &[Pos],
) -> bool {
    match &node.kind {
        Kind::Map(m) => {
            let plausible = schema.plausible(positions, node);
            for d in deletions {
                if !schema.declares_tail(&plausible, &d.container_path) {
                    continue;
                }
                if let Some(c) = tail_value(node, &d.container_path).and_then(map) {
                    if d.messages.iter().any(|(k, _)| c.contains_key(k)) {
                        return true;
                    }
                }
            }
            m.iter().any(|(k, e)| {
                would_fire(
                    &e.value,
                    deletions,
                    schema,
                    &schema.child_positions(positions, k),
                )
            })
        }
        Kind::Seq(items) => {
            let ip = schema.item_positions(positions);
            items.iter().any(|i| would_fire(i, deletions, schema, &ip))
        }
        Kind::Scalar(_) => false,
    }
}

// ---- retired keys: where each declaration would fire, read-only ----

/// Every concrete key path a deletion in `deletions` would strike, with the
/// deletion that strikes it: the read-only mirror of `delete_tails` that
/// reports instead of striking.
pub fn fired_deletions<'a>(
    doc: &Node,
    deletions: &[&'a Deletion],
) -> Vec<(Vec<String>, &'a Deletion)> {
    let Some(first) = deletions.first() else {
        return Vec::new();
    };
    let (Some(schema), Some(live)) = (grammar(first.source), current_grammar()) else {
        return Vec::new();
    };
    let w = Walk { schema, live };
    let mut found = Vec::new();
    collect_deletions(
        doc,
        deletions,
        &w,
        &[schema.root()],
        &[live.root()],
        &mut Vec::new(),
        &mut found,
    );
    found
}

fn collect_deletions<'a>(
    node: &Node,
    deletions: &[&'a Deletion],
    w: &Walk,
    positions: &[Pos],
    live_positions: &[Pos],
    prefix: &mut Vec<String>,
    found: &mut Vec<(Vec<String>, &'a Deletion)>,
) {
    match &node.kind {
        Kind::Map(m) => {
            let present: Vec<&&Deletion> = deletions
                .iter()
                .filter(|d| tail_present(node, &d.path))
                .collect();
            if !present.is_empty() {
                let matching = w.schema.matching(positions, w.live, live_positions, node);
                for d in present {
                    if fires(w, d, node, &matching, live_positions) {
                        let mut path = prefix.clone();
                        path.extend(d.path.iter().cloned());
                        found.push((path, *d));
                    }
                }
            }
            for (k, e) in m {
                prefix.push(k.clone());
                collect_deletions(
                    &e.value,
                    deletions,
                    w,
                    &w.schema.child_positions(positions, k),
                    &w.live.child_positions(live_positions, k),
                    prefix,
                    found,
                );
                prefix.pop();
            }
        }
        Kind::Seq(items) => {
            let (ip, lp) = (
                w.schema.item_positions(positions),
                w.live.item_positions(live_positions),
            );
            for (i, item) in items.iter().enumerate() {
                prefix.push(i.to_string());
                collect_deletions(item, deletions, w, &ip, &lp, prefix, found);
                prefix.pop();
            }
        }
        Kind::Scalar(_) => {}
    }
}

/// The path of every chart `rule` would relocate a tail of.
pub fn fired_conditionals(doc: &Node, rule: &ConditionalMove) -> Vec<Vec<String>> {
    let Some(schema) = grammar(rule.source) else {
        return Vec::new();
    };
    let mut found = Vec::new();
    collect_cond(
        doc,
        rule,
        schema,
        &[schema.root()],
        &mut Vec::new(),
        &mut found,
    );
    found
}

fn collect_cond(
    node: &Node,
    rule: &ConditionalMove,
    schema: &'static Schema,
    positions: &[Pos],
    prefix: &mut Vec<String>,
    found: &mut Vec<Vec<String>>,
) {
    match &node.kind {
        Kind::Map(m) => {
            if is_chart(node, rule, schema, positions) && tail_present(node, &rule.old_tail) {
                found.push(prefix.clone());
            }
            for (k, e) in m {
                prefix.push(k.clone());
                collect_cond(
                    &e.value,
                    rule,
                    schema,
                    &schema.child_positions(positions, k),
                    prefix,
                    found,
                );
                prefix.pop();
            }
        }
        Kind::Seq(items) => {
            let ip = schema.item_positions(positions);
            for (i, item) in items.iter().enumerate() {
                prefix.push(i.to_string());
                collect_cond(item, rule, schema, &ip, prefix, found);
                prefix.pop();
            }
        }
        Kind::Scalar(_) => {}
    }
}
