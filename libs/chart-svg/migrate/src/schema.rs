//! A frozen grammar and the position algebra the engine walks it with.
//!
//! A *position* is a schema node a document value can correspond to. The walk
//! descends positions in step with the document, so a chart's own `type:`
//! picks its family's style grammar. Everything here answers an identity
//! question ("could this node be one of these?", "is this tail declared
//! here?"), never a validity one, except [`Schema::matching`] which the
//! deletions ask on purpose.

use serde_json::Value as Json;
use std::collections::{BTreeSet, HashMap};
use std::sync::{Arc, Mutex};
use yaml::{Kind, Node, Value};

use crate::validate::{equals, is_valid, resolve};

pub type Pos = &'static Json;

const COMPOSITION: [&str; 6] = ["anyOf", "oneOf", "allOf", "if", "then", "else"];
static NULL: Json = Json::Null;
const CROSS_FIELD_RULE: &str = "x-dbt-charts-cross-field-rule";

type Branches = Arc<Vec<Pos>>;

pub struct Schema {
    root: Json,
    cache: Mutex<HashMap<(usize, Vec<String>), Branches>>,
}

/// The schema without its cross-field rules: the key shapes alone.
fn grammar_only(node: Json) -> Json {
    match node {
        Json::Array(items) => Json::Array(items.into_iter().map(grammar_only).collect()),
        Json::Object(map) => {
            let mut map: serde_json::Map<String, Json> =
                map.into_iter().map(|(k, v)| (k, grammar_only(v))).collect();
            if let Some(Json::Array(rules)) = map.get_mut("allOf") {
                rules.retain(|r| r.get(CROSS_FIELD_RULE).is_none());
                if rules.is_empty() {
                    map.shift_remove("allOf");
                }
            }
            Json::Object(map)
        }
        other => other,
    }
}

impl Schema {
    /// Parses a frozen schema file into its grammar. The file is package data,
    /// so a parse failure is a build defect and surfaces as an empty grammar
    /// that accepts nothing, which the drift tests catch.
    pub(crate) fn leak(text: &str) -> &'static Schema {
        let root = serde_json::from_str(text)
            .map(grammar_only)
            .unwrap_or(Json::Null);
        Box::leak(Box::new(Schema {
            root,
            cache: Mutex::default(),
        }))
    }

    pub fn root(&'static self) -> Pos {
        &self.root
    }

    pub fn valid(&'static self, position: Pos, node: &Node) -> bool {
        is_valid(&self.root, position, node)
    }

    fn resolved(&'static self, node: Pos) -> Pos {
        match node.get("$ref").and_then(Json::as_str) {
            Some(r) => resolve(&self.root, r)
                .filter(|t| t.is_object())
                .unwrap_or(node),
            None => node,
        }
    }

    /// Every concrete branch a value at `node` could satisfy: `$ref`, unions,
    /// intersections, and the `then` of an `if`/`then` pair, flattened.
    pub fn branches(&'static self, node: Pos) -> Branches {
        if node.get("$ref").is_none() && COMPOSITION.iter().all(|k| node.get(k).is_none()) {
            return Arc::new(vec![node]);
        }
        self.expand(node, &BTreeSet::new())
    }

    fn expand(&'static self, node: Pos, seen: &BTreeSet<String>) -> Branches {
        let key = (
            node as *const Json as usize,
            seen.iter().cloned().collect::<Vec<_>>(),
        );
        if let Some(hit) = self.cache.lock().ok().and_then(|c| c.get(&key).cloned()) {
            return hit;
        }
        let mut seen = seen.clone();
        if let Some(r) = node.get("$ref").and_then(Json::as_str) {
            if !seen.insert(r.to_string()) {
                return Arc::new(Vec::new());
            }
        }
        let resolved = self.resolved(node);
        let mut out = vec![resolved];
        let nested = |n: Pos, out: &mut Vec<Pos>| {
            if n.get("$ref").is_none() && COMPOSITION.iter().all(|k| n.get(k).is_none()) {
                out.push(n);
            } else {
                out.extend(self.expand(n, &seen).iter().copied());
            }
        };
        for keyword in ["anyOf", "oneOf", "allOf"] {
            if let Some(Json::Array(branches)) = resolved.get(keyword) {
                for b in branches.iter().filter(|b| b.is_object()) {
                    nested(b, &mut out);
                }
            }
        }
        if let Some(then) = resolved.get("then").filter(|t| t.is_object()) {
            nested(then, &mut out);
        }
        let out = Arc::new(out);
        if let Ok(mut c) = self.cache.lock() {
            c.insert(key, out.clone());
        }
        out
    }

    /// Schema nodes for `key` one step below `positions`. Follows open maps:
    /// reaching inside a user-keyed chart to look at it is fine.
    pub fn child_positions(&'static self, positions: &[Pos], key: &str) -> Vec<Pos> {
        let mut out = Vec::new();
        for p in positions {
            for b in self.branches(p).iter() {
                let declared = b.get("properties").and_then(|p| p.get(key));
                let candidate = declared
                    .filter(|d| d.is_object())
                    .or_else(|| b.get("additionalProperties"));
                if let Some(c) = candidate.filter(|c| c.is_object()) {
                    out.push(c);
                }
            }
        }
        out
    }

    pub fn item_positions(&'static self, positions: &[Pos]) -> Vec<Pos> {
        let mut out = Vec::new();
        for p in positions {
            for b in self.branches(p).iter() {
                if let Some(i) = b.get("items").filter(|i| i.is_object()) {
                    out.push(i);
                }
            }
        }
        out
    }

    /// Property names declared at `positions`, across every branch.
    pub fn declared_names(&'static self, positions: &[Pos]) -> BTreeSet<String> {
        let mut names = BTreeSet::new();
        for p in positions {
            for b in self.branches(p).iter() {
                if let Some(Json::Object(props)) = b.get("properties") {
                    names.extend(props.keys().cloned());
                }
            }
        }
        names
    }

    /// Whether `tail` is a declared property chain at any of `positions`.
    /// `additionalProperties` is deliberately not consulted: that is the whole
    /// distinction between a retired key and a key the author chose.
    pub fn declares_tail(&'static self, positions: &[Pos], tail: &[String]) -> bool {
        let mut current = positions.to_vec();
        for part in tail {
            let mut next = Vec::new();
            for n in &current {
                for b in self.branches(n).iter() {
                    if let Some(c) = b
                        .get("properties")
                        .and_then(|p| p.get(part))
                        .filter(|c| c.is_object())
                    {
                        next.push(c);
                    }
                }
            }
            if next.is_empty() {
                return false;
            }
            current = next;
        }
        true
    }

    /// Whether any position discriminates a chart of `chart_type` via `type:`.
    pub fn declares_chart_type(&'static self, positions: &[Pos], chart_type: &str) -> bool {
        positions.iter().any(|p| {
            self.branches(p).iter().any(|b| {
                b.get("properties")
                    .and_then(|p| p.get("type"))
                    .and_then(|t| t.get("enum"))
                    .and_then(Json::as_array)
                    .is_some_and(|e| e.iter().any(|v| v.as_str() == Some(chart_type)))
            })
        })
    }

    /// Whether `path` resolves in the schema. `open_map_keys` lets a literal
    /// segment resolve through `additionalProperties`; only a move destination
    /// may assert that.
    pub fn path_exists(&'static self, path: &[String], open_map_keys: bool) -> bool {
        let mut nodes = vec![self.root()];
        for part in path {
            let mut next = Vec::new();
            for n in &nodes {
                for b in self.branches(n).iter() {
                    let child: Option<Pos> = if part == "*" {
                        b.get("additionalProperties")
                            .or_else(|| b.get("items"))
                            .or_else(|| {
                                (b.get("type").and_then(Json::as_str) == Some("array"))
                                    .then_some(&NULL)
                            })
                    } else {
                        let props = b.get("properties");
                        props.and_then(|p| p.get(part)).or_else(|| {
                            let none = props.and_then(Json::as_object).is_none_or(|p| p.is_empty());
                            if open_map_keys && none {
                                b.get("additionalProperties")
                            } else {
                                None
                            }
                        })
                    };
                    // A bare `{"type": "array"}` accepts any item; `null` stands for that open `{}`.
                    if let Some(c) = child.filter(|c| c.is_object() || c.is_null()) {
                        next.push(c);
                    }
                }
            }
            nodes = next;
            if nodes.is_empty() {
                return false;
            }
        }
        true
    }

    /// Branches at `positions` that the mapping `node` could be an instance of.
    /// Deliberately weaker than [`Schema::matching`]: a document a move targets
    /// is mid-migration and carries retired spellings. A `then` counts only
    /// when its `if` holds for this node.
    pub fn plausible(&'static self, positions: &[Pos], node: &Node) -> Vec<Pos> {
        let mut out = Vec::new();
        for p in positions {
            self.collect_plausible(p, node, &BTreeSet::new(), &mut out);
        }
        out
    }

    fn collect_plausible(
        &'static self,
        position: Pos,
        node: &Node,
        seen: &BTreeSet<String>,
        out: &mut Vec<Pos>,
    ) {
        let mut seen = seen.clone();
        if let Some(r) = position.get("$ref").and_then(Json::as_str) {
            if !seen.insert(r.to_string()) {
                return;
            }
        }
        let resolved = self.resolved(position);
        if let Some(own) = without_composition(resolved) {
            if could_be(own, node) {
                out.push(own);
            }
        }
        for keyword in ["anyOf", "oneOf", "allOf"] {
            if let Some(Json::Array(branches)) = resolved.get(keyword) {
                for b in branches.iter().filter(|b| b.is_object()) {
                    self.collect_plausible(b, node, &seen, out);
                }
            }
        }
        if let Some(then) = resolved.get("then").filter(|t| t.is_object()) {
            if let Some(guard) = resolved.get("if").filter(|g| g.is_object()) {
                if !self.valid(guard, node) {
                    return;
                }
            }
            self.collect_plausible(then, node, &seen, out);
        }
    }

    /// Narrow `positions` to the branches `node` is a valid instance of. A
    /// node failing every branch is asked once more without the fields the
    /// frozen grammar could not have known (`live` declares them here).
    pub fn matching(
        &'static self,
        positions: &[Pos],
        live: &'static Schema,
        live_positions: &[Pos],
        node: &Node,
    ) -> Vec<Pos> {
        let pick = |n: &Node| -> Vec<Pos> {
            positions
                .iter()
                .copied()
                .filter(|p| self.valid(p, n))
                .collect()
        };
        let matched = pick(node);
        if !matched.is_empty() {
            return matched;
        }
        let probe = self.strip_post_freeze(node, positions, live, live_positions);
        if &probe == node {
            return Vec::new();
        }
        pick(&probe)
    }

    fn strip_post_freeze(
        &'static self,
        node: &Node,
        positions: &[Pos],
        live: &'static Schema,
        live_positions: &[Pos],
    ) -> Node {
        match &node.kind {
            Kind::Map(map) => {
                let known = self.declared_names(positions);
                let newer: BTreeSet<String> = live
                    .declared_names(live_positions)
                    .difference(&known)
                    .cloned()
                    .collect();
                let kept = map
                    .iter()
                    .filter(|(k, _)| !newer.contains(*k))
                    .map(|(k, e)| {
                        let value = self.strip_post_freeze(
                            &e.value,
                            &self.child_positions(positions, k),
                            live,
                            &live.child_positions(live_positions, k),
                        );
                        (
                            k.clone(),
                            yaml::Entry {
                                value,
                                key_span: e.key_span,
                                key_kind: e.key_kind,
                                key_text: e.key_text.clone(),
                            },
                        )
                    })
                    .collect();
                Node {
                    span: node.span,
                    kind: Kind::Map(kept),
                }
            }
            Kind::Seq(items) => {
                let ip = self.item_positions(positions);
                let lp = live.item_positions(live_positions);
                let items = items
                    .iter()
                    .map(|i| self.strip_post_freeze(i, &ip, live, &lp))
                    .collect();
                Node {
                    span: node.span,
                    kind: Kind::Seq(items),
                }
            }
            Kind::Scalar(_) => node.clone(),
        }
    }

    /// Whether `key` reads as an author-chosen map key rather than a field: the
    /// map reading fits, and the declared one does not.
    pub fn open_map_claims(
        &'static self,
        positions: &[Pos],
        live: &'static Schema,
        live_positions: &[Pos],
        key: &str,
        value: &Node,
    ) -> bool {
        let declared_fits = positions.iter().any(|p| {
            p.get("properties")
                .and_then(|props| props.get(key))
                .is_some_and(|sub| sub.is_object() && self.valid(sub, value))
        });
        if declared_fits {
            return false;
        }
        let map_fits = |g: &'static Schema, group: &[Pos]| {
            group.iter().any(|p| {
                p.get("additionalProperties")
                    .is_some_and(|sub| sub.is_object() && g.valid(sub, value))
            })
        };
        map_fits(self, positions) || map_fits(live, live_positions)
    }
}

pub(crate) fn without_composition(node: Pos) -> Option<Pos> {
    if COMPOSITION.iter().all(|k| node.get(k).is_none()) {
        return annotated(node).then_some(node);
    }
    // A composed node's own declarations; leaked once per distinct node.
    static OWN: Mutex<Option<HashMap<usize, Option<Pos>>>> = Mutex::new(None);
    let mut guard = OWN.lock().ok()?;
    let table = guard.get_or_insert_with(HashMap::new);
    *table
        .entry(node as *const Json as usize)
        .or_insert_with(|| {
            let mut own = node.as_object()?.clone();
            for k in COMPOSITION {
                own.remove(k);
            }
            let own: Pos = Box::leak(Box::new(Json::Object(own)));
            annotated(own).then_some(own)
        })
}

/// Whether anything beyond `description`/`title` is declared.
fn annotated(node: &Json) -> bool {
    node.as_object()
        .is_some_and(|m| m.keys().any(|k| k != "description" && k != "title"))
}

/// Required properties and an agreeing `type` discriminator only; never the
/// values' own shapes, which a mid-migration document is expected to fail.
fn could_be(branch: &Json, node: &Node) -> bool {
    let Kind::Map(map) = &node.kind else {
        return false;
    };
    if let Some(Json::Array(required)) = branch.get("required") {
        if required
            .iter()
            .filter_map(Json::as_str)
            .any(|n| !map.contains_key(n))
        {
            return false;
        }
    }
    let declared = branch.get("properties").and_then(|p| p.get("type"));
    let node_type = map
        .get("type")
        .map(|e| &e.value)
        .filter(|v| !matches!(v.kind, Kind::Scalar(Value::Null)));
    let (Some(declared), Some(actual)) = (declared.filter(|d| d.is_object()), node_type) else {
        return true;
    };
    if let Some(Json::Array(e)) = declared.get("enum") {
        if !e.iter().any(|x| equals(x, actual)) {
            return false;
        }
    }
    declared
        .get("const")
        .is_none_or(|c| c.is_null() || equals(c, actual))
}
