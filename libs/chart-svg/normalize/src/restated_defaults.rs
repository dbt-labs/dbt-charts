//! WARN-RESTATED-INFERRED for a key the board file writes equal to its field's declared default
//! (Python's `schema_default_restated`), read off the file as written: only keys the author typed
//! are judged, so the cost is the file's keys, not the model's fields.
//!
//! A declared default is a field's `fill` constant. `auto` asks for inference and a one-value type
//! is a union tag: neither is judged, nor is anything under a `style:` key (the style checks own
//! it). A board's `html_policy` is judged against the board above it instead, `none` at the root.
//!
//! A key that sets the default back over a different value the layers below set (`meta.yml`,
//! `extends:`) is an override: deleting it would bring that value back. The layers are folded
//! only when some key equals its default, and only keys outside a list are looked up there, as
//! Python threads the layers through mappings alone.
//!
//! The walk enters only the fields whose type can reach a judged one, and each struct reads its
//! judged keys from a table built once per process.

use crate::extends::Patch;
use crate::merge_warnings::leaves;
use diagnostic::{diag, restated_text, Diagnostic, ModelPath};
use model_core::schema::{Body, Prop, Schema, Ty};
use model_core::Value as Fill;
use project::ProjectPath;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;
use yaml::{Kind, Node};

const POLICY: &str = "html_policy";

/// Each struct model's judged keys with their defaults, and the `(model, key)` fields the walk
/// enters: those whose type names a model from which a judged key is reachable.
struct Table {
    judged: HashMap<&'static str, Vec<(&'static str, Value)>>,
    enter: HashSet<(&'static str, &'static str)>,
}

fn table(schema: &'static Schema) -> &'static Table {
    static TABLE: OnceLock<Table> = OnceLock::new();
    TABLE.get_or_init(|| {
        let mut judged: HashMap<&'static str, Vec<(&'static str, Value)>> = HashMap::new();
        let mut refs_of: HashMap<&'static str, Vec<(&'static str, Vec<&'static str>)>> =
            HashMap::new();
        for model in schema.models.values() {
            let fields = match &model.body {
                Body::Struct { .. } => schema
                    .flat(model)
                    .props
                    .into_iter()
                    .filter(|p| p.key != "style")
                    .map(|p| {
                        if let Some(d) = declared(p.ty, p.field.attrs.fill_value.as_ref()) {
                            judged.entry(model.name).or_default().push((p.key, d));
                        }
                        let mut refs = Vec::new();
                        p.ty.refs(&mut refs);
                        (p.key, refs)
                    })
                    .collect(),
                Body::Tagged { variants, .. } => {
                    let mut refs = Vec::new();
                    variants.iter().for_each(|(_, t)| t.refs(&mut refs));
                    vec![("", refs)]
                }
            };
            refs_of.insert(model.name, fields);
        }
        // The models a judged key is reachable from, to a fixed point.
        let mut reach: HashSet<&'static str> = judged.keys().copied().collect();
        loop {
            let before = reach.len();
            for (model, fields) in &refs_of {
                if fields
                    .iter()
                    .any(|(_, r)| r.iter().any(|n| reach.contains(n)))
                {
                    reach.insert(model);
                }
            }
            if reach.len() == before {
                break;
            }
        }
        let enter = refs_of
            .iter()
            .flat_map(|(model, fields)| {
                fields
                    .iter()
                    .filter(|(_, r)| r.iter().any(|n| reach.contains(n)))
                    .map(move |(key, _)| (*model, *key))
            })
            .collect();
        Table { judged, enter }
    })
}

/// The findings for `node`, a board file's document as written, over `layers`, the files below it
/// low to high.
pub(crate) fn defaults(
    node: &Node,
    layers: &[(ProjectPath, Patch)],
) -> Result<Vec<Diagnostic>, String> {
    let schema = &crate::constraints::spine()?.schema;
    let table = table(schema);
    let mut below: Option<HashMap<String, Value>> = None;
    let mut lower = |path: &str| -> Option<Value> {
        let held = below.get_or_insert_with(|| {
            let mut folded = Patch::default();
            for (_, layer) in layers {
                folded.merge(layer.clone());
            }
            leaves(&folded).into_iter().collect()
        });
        held.get(path).filter(|v| !v.is_null()).cloned()
    };
    // Each board's own html_policy, by its path: what a board inside it inherits. A board that
    // writes none passes on its parent's; the root's parent is the layers below, else `none`.
    let mut policies: Vec<(String, Option<String>)> = Vec::new();
    let mut out = Vec::new();
    let enter = |model: &str, p: &Prop<'_>| table.enter.contains(&(model, p.key));
    for visit in schema.structs_where(node, &enter) {
        let Some(judged) = table.judged.get(visit.model) else {
            continue;
        };
        let Kind::Map(entries) = &visit.node.kind else {
            continue;
        };
        for (key, default) in judged {
            let Some(entry) = entries.get(*key) else {
                continue;
            };
            if !matches!(entry.value.kind, Kind::Scalar(_)) {
                continue;
            }
            let value = entry.value.to_json();
            let at = join(&visit.path, key);
            let restated = if *key == POLICY {
                let inherited = policies
                    .iter()
                    .rev()
                    .filter(|(b, _)| inside(&visit.path, b))
                    .find_map(|(_, p)| p.clone())
                    .or_else(|| {
                        let nested = !visit.path.is_empty();
                        nested
                            .then(|| lower(POLICY)?.as_str().map(str::to_string))
                            .flatten()
                    })
                    .unwrap_or_else(|| "none".to_string());
                value.as_str() == Some(inherited.as_str())
            } else {
                same(&value, default)
            };
            if restated && (visit.listed || lower(&at).is_none_or(|l| same(&value, &l))) {
                let model_path = ModelPath(at.split('.').map(str::to_string).collect());
                out.push(
                    diag::warn_restated_inferred(at, restated_text(&value), "the default")
                        .with_sink_param("why", "default".into())
                        .at_path(model_path),
                );
            }
        }
        if judged.iter().any(|(k, _)| *k == POLICY) {
            let own = entries
                .get(POLICY)
                .and_then(|e| e.value.to_json().as_str().map(str::to_string));
            policies.push((visit.path.clone(), own));
        }
    }
    Ok(out)
}

/// A field's declared default, when it is one Python judges.
fn declared(ty: &Ty, fill: Option<&Fill>) -> Option<Value> {
    if matches!(ty, Ty::Const(_)) || matches!(ty, Ty::Literals(v) if v.len() == 1) {
        return None;
    }
    match fill? {
        Fill::Null | Fill::Str("auto") => None,
        Fill::Bool(b) => Some(Value::from(*b)),
        Fill::Int(i) => Some(Value::from(*i)),
        Fill::Float(f) => Some(Value::from(*f)),
        Fill::Str(s) => Some(Value::from(*s)),
    }
}

/// Equal as Python compares the parsed values: a number by its value, whatever its spelling.
fn same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => x.as_f64() == y.as_f64(),
        _ => a == b,
    }
}

fn inside(path: &str, board: &str) -> bool {
    board.is_empty() || path.starts_with(&format!("{board}."))
}

fn join(at: &str, key: &str) -> String {
    if at.is_empty() {
        key.to_string()
    } else {
        format!("{at}.{key}")
    }
}
