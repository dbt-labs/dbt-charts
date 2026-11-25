//! `validate/values.py`: every column of an inline values query declares one kind, as
//! `column_types.literal_schema` reads it off the literals.

use crate::chart_checks::Patch;
use board_model::query::{QueryAuthored, ValuesQueryAuthored};
use diagnostic::{diag, Diagnostic};
use model_core::Tri;
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use yaml::{Kind, Node};

/// A cell value beside its YAML node, kept only where the value holds a date or timestamp.
type Cell<'a> = (&'a Value, Option<&'a Node>);

fn kind((v, node): Cell) -> &'static str {
    if let Some(Node {
        kind: Kind::Scalar(yaml::Value::Timestamp(t)),
        ..
    }) = node
    {
        return match (t.split_once('T'), t.ends_with('Z')) {
            (None, _) => "date",
            (Some(_), true) => "datetime_tz",
            (Some((_, time)), false) if time.contains(['+', '-']) => "datetime_tz",
            _ => "datetime",
        };
    }
    match v {
        Value::Bool(_) => "boolean",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "integer",
        Value::String(_) => "string",
        Value::Array(_) => "list",
        Value::Object(_) => "struct",
        Value::Null => unreachable!("nulls are filtered before a kind is read"),
    }
}

/// `_literal_type`: the declared type of the cells at `path` (the column, then
/// `.field` and `[]` below it), in the `ColumnType` wire form.
fn check(path: &str, values: &[Cell]) -> Result<Value, Diagnostic> {
    let column = path.split(['.', '[']).next().unwrap_or(path).to_string();
    let where_ = if path == column {
        String::new()
    } else {
        format!("{path} ")
    };
    if values.is_empty() {
        return Err(diag::err_result_column_type(
            column,
            format!("{where_}holds no non-null value to type"),
        ));
    }
    let mut kinds: BTreeSet<&str> = values.iter().map(|c| kind(*c)).collect();
    if kinds == BTreeSet::from(["float", "integer"]) {
        kinds = BTreeSet::from(["float"]);
    }
    if kinds.len() > 1 {
        let names: Vec<&str> = kinds.into_iter().collect();
        return Err(diag::err_result_column_type(
            column,
            format!(
                "{where_}mixes {}; use one kind per column",
                names.join(", ")
            ),
        ));
    }
    let Some(kind) = kinds.into_iter().next() else {
        return Err(diag::err_internal(format!("{path} has cells but no kind")));
    };
    match kind {
        "list" => {
            let items: Vec<Cell> = values
                .iter()
                .flat_map(|(v, node)| {
                    let nodes = node.and_then(|n| match &n.kind {
                        Kind::Seq(nodes) => Some(nodes),
                        _ => None,
                    });
                    v.as_array()
                        .into_iter()
                        .flatten()
                        .enumerate()
                        .map(move |(i, item)| (item, nodes.and_then(|nodes| nodes.get(i))))
                })
                .filter(|(i, _)| !i.is_null())
                .collect();
            let element = check(&format!("{path}[]"), &items)?;
            Ok(json!({"kind": kind, "element": element, "fields": null}))
        }
        "struct" => {
            let mut names: Vec<&String> = Vec::new();
            for (v, _) in values {
                for k in v.as_object().into_iter().flat_map(|o| o.keys()) {
                    if !names.contains(&k) {
                        names.push(k);
                    }
                }
            }
            let mut fields = Vec::new();
            for name in names {
                let inner: Vec<Cell> = values
                    .iter()
                    .filter_map(|(v, node)| {
                        let child = node.and_then(|n| match &n.kind {
                            Kind::Map(map) => map.get(name.as_str()).map(|e| &e.value),
                            _ => None,
                        });
                        Some((v.get(name).filter(|c| !c.is_null())?, child))
                    })
                    .collect();
                fields.push(json!([name, check(&format!("{path}.{name}"), &inner)?]));
            }
            Ok(json!({"kind": kind, "element": null, "fields": fields}))
        }
        _ => Ok(json!({"kind": kind, "element": null, "fields": null})),
    }
}

/// `ValuesQuery.declared_schema`: each column's declared type, read off its
/// literals, in the `ColumnType` wire form. With no rows there is no literal to
/// read: the authored columns are declared as strings.
pub fn values_schema(q: &ValuesQueryAuthored) -> Result<Map<String, Value>, Diagnostic> {
    let rows = q.rows.as_deref().unwrap_or_default();
    let mut names: Vec<&String> = Vec::new();
    match &q.columns {
        Tri::Value(columns) => names.extend(columns),
        _ => {
            for row in rows {
                for k in row.0.keys() {
                    if !names.contains(&k) {
                        names.push(k);
                    }
                }
            }
        }
    }
    names
        .into_iter()
        .map(|name| {
            if rows.is_empty() {
                return Ok((name.clone(), json!({"kind": "string", "element": null, "fields": null})));
            }
            let cells: Vec<Cell> = rows
                .iter()
                .filter_map(|r| r.0.get(name).filter(|c| !c.0.is_null()))
                .map(|c| (&c.0, c.1.as_ref()))
                .collect();
            Ok((name.clone(), check(name, &cells)?))
        })
        .collect()
}

/// The first values query, in board order, with a column that mixes kinds or holds only nulls.
pub fn values_kind_error(patch: &Patch) -> Option<Diagnostic> {
    patch.body.queries.values().flatten().find_map(|query| match query {
        QueryAuthored::Values(q) => values_schema(q).err(),
        _ => None,
    })
}
