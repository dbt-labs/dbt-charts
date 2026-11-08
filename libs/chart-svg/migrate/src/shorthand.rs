//! Python's validation-time shorthands (`read_board` is the entry a Rust reader of a board calls;
//! Python's own loader calls `migrate` alone and applies them itself at validation): rewrites a pydantic `BeforeValidator` makes before a
//! model reads a value, so every reader of a board sees them and the emitted schema does not list
//! them. They change the node, never the text: a rewrite here owes no `Edit`.
//!
//! - A bare string under a board's `queries:` is `{sql: <text>, type: sql}`
//!   (`normalize_query_value`), at the root board and every nested board. A string shaped like a
//!   query ref (`<file>.queries.<name>`) is left as written: Python reads it as `{ref: ..}`,
//!   which the model has no arm for yet, and the reader refuses it as it did before.

use fancy_regex::Regex;
use std::sync::OnceLock;
use yaml::{Entry, Kind, Node, Value};

/// `QUERY_REF_RE` in `models/refs.py`.
const QUERY_REF: &str = r"^(?:\.\./)*[A-Za-z_][\w/\-.]*\.queries\.[A-Za-z_]\w*$";

fn is_query_ref(text: &str) -> bool {
    static RE: OnceLock<Option<Regex>> = OnceLock::new();
    RE.get_or_init(|| Regex::new(QUERY_REF).ok())
        .as_ref()
        .is_some_and(|re| re.is_match(text.trim()).unwrap_or(false))
}

/// `migrate` followed by every shorthand: the board node as a model reads it.
pub fn read_board(node: Node, today: crate::catalog::Date) -> crate::engine::Migrated {
    let mut migrated = crate::engine::migrate(node, today);
    expand(&mut migrated.node);
    migrated
}

/// Applies every shorthand to a board node and the boards nested in its layout.
pub fn expand(board: &mut Node) {
    let Kind::Map(m) = &mut board.kind else {
        return;
    };
    if let Some(Kind::Map(queries)) = m.get_mut("queries").map(|e| &mut e.value.kind) {
        for entry in queries.values_mut() {
            bare_sql(&mut entry.value);
        }
    }
    for key in ["rows", "cols"] {
        if let Some(Kind::Seq(items)) = m.get_mut(key).map(|e| &mut e.value.kind) {
            items.iter_mut().for_each(expand);
        }
    }
    if let Some(Kind::Seq(cells)) = m
        .get_mut("grid")
        .and_then(|e| match &mut e.value.kind {
            Kind::Map(g) => g.get_mut("items"),
            _ => None,
        })
        .map(|e| &mut e.value.kind)
    {
        for cell in cells {
            if let Kind::Map(c) = &mut cell.kind {
                if let Some(item) = c.get_mut("item") {
                    expand(&mut item.value);
                }
            }
        }
    }
    if let Some(Kind::Seq(tabs)) = m
        .get_mut("tabs")
        .and_then(|e| match &mut e.value.kind {
            Kind::Map(t) => t.get_mut("items"),
            _ => None,
        })
        .map(|e| &mut e.value.kind)
    {
        tabs.iter_mut().for_each(expand);
    }
}

fn bare_sql(query: &mut Node) {
    let Kind::Scalar(Value::Str(text)) = &query.kind else {
        return;
    };
    if is_query_ref(text) {
        return;
    }
    let span = query.span;
    let text = text.clone();
    let leaf = |s: &str| Node {
        span,
        kind: Kind::Scalar(Value::Str(s.to_string())),
    };
    let mut map = indexmap::IndexMap::new();
    map.insert(
        "sql".to_string(),
        Entry::string_key(span, "sql", leaf(&text)),
    );
    map.insert(
        "type".to_string(),
        Entry::string_key(span, "type", leaf("sql")),
    );
    query.kind = Kind::Map(map);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn expanded(text: &str) -> serde_json::Value {
        let mut node = yaml::parse_node(text).expect("parses");
        expand(&mut node);
        node.to_json()
    }

    #[test]
    fn a_bare_query_is_a_sql_query_at_every_board() {
        let got = expanded(
            "queries:\n  a: SELECT 1\nrows:\n  - queries:\n      b: SELECT 2\n    rows:\n      - x\ntabs:\n  items:\n    - title: T\n      rows:\n        - queries:\n            c: SELECT 3\n          rows:\n            - x\n",
        );
        assert_eq!(
            got["queries"]["a"],
            serde_json::json!({"sql": "SELECT 1", "type": "sql"})
        );
        assert_eq!(
            got["rows"][0]["queries"]["b"],
            serde_json::json!({"sql": "SELECT 2", "type": "sql"})
        );
        assert_eq!(
            got["tabs"]["items"][0]["rows"][0]["queries"]["c"],
            serde_json::json!({"sql": "SELECT 3", "type": "sql"})
        );
    }

    #[test]
    fn a_ref_string_a_mapping_and_other_keys_are_left_alone() {
        let text = "queries:\n  r: shared.yml.queries.revenue\n  m:\n    sql: SELECT 1\nvariables:\n  queries: not a query\n";
        let got = expanded(text);
        let want = yaml::parse_node(text).expect("parses").to_json();
        assert_eq!(got, want);
    }

    #[test]
    fn only_read_board_expands_the_loader_keeps_the_bare_string() {
        let text = "queries:\n  a: SELECT 1\nrows:\n  - x\n";
        let node = yaml::parse_node(text).expect("parses");
        let loader = crate::engine::migrate(node.clone(), (2026, 11, 6))
            .node
            .to_json();
        assert_eq!(loader["queries"]["a"], "SELECT 1");
        let reader = read_board(node, (2026, 11, 6)).node.to_json();
        assert_eq!(reader["queries"]["a"]["type"], "sql");
    }
}
