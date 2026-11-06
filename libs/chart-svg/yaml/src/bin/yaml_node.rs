//! Sweep driver: reads a JSON array of YAML texts on stdin, writes one result
//! per text: `{"node": <json>, "keys": [[start, end, line, column, kind, text], ...]}`
//! or `{"errors": [{"code", "params"}, ...]}`. Not part of the engine.

use std::io::Read;

use serde_json::{json, Value};
use yaml::{parse_node, Kind, Node};

fn keys(node: &Node, out: &mut Vec<Value>) {
    match &node.kind {
        Kind::Scalar(v) => {
            let s = node.span;
            let (kind, value) = match v {
                yaml::Value::Str(t) => ("Str", json!(t)),
                _ => ("Other", Value::Null),
            };
            out.push(json!([s.start, s.end, s.line, s.column, kind, value]));
        }
        Kind::Seq(items) => items.iter().for_each(|n| keys(n, out)),
        Kind::Map(map) => {
            for (k, e) in map {
                let s = e.key_span;
                let text = if e.key_kind == yaml::KeyKind::Str {
                    k
                } else {
                    &e.key_text
                };
                out.push(json!([s.start, s.end, s.line, s.column, "Key", text]));
                keys(&e.value, out);
            }
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input)?;
    let texts: Vec<String> = serde_json::from_str(&input)?;
    let results: Vec<Value> = texts
        .iter()
        .map(|text| match parse_node(text) {
            Ok(node) => {
                let mut k = Vec::new();
                keys(&node, &mut k);
                json!({"node": node.to_json(), "keys": k})
            }
            Err(errors) => json!({"errors": errors}),
        })
        .collect();
    println!("{}", serde_json::to_string(&results)?);
    Ok(())
}
