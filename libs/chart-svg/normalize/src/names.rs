//! The names an author gives: every chart, query and variable name is an identifier.
//!
//! Python's rule, `str.isidentifier()`: XID_Start or `_`, then XID_Continue. An identifier
//! never starts with `@`, the mark of a generated position id (`board_model::ids`), so an
//! authored name and a position cannot collide. The walk is Python's `normalize_board`: a
//! board's variables, queries and charts in that order, then its layout in item order, a
//! nested board entered where it sits.

use diagnostic::diag::err_name_invalid;
use diagnostic::Diagnostic;
use yaml::{Kind, Node, Value};

/// Python's `str.isidentifier()`.
pub fn is_identifier(name: &str) -> bool {
    let mut chars = name.chars();
    chars
        .next()
        .is_some_and(|c| c == '_' || unicode_ident::is_xid_start(c))
        && chars.all(unicode_ident::is_xid_continue)
}

/// `Err` is the first non-identifier name of the board file's tree, as Python reports it.
/// `rooted` is a file whose boards have authored paths from its own root; an imported board file
/// has none, and Python names its keys by section alone.
pub(crate) fn refuse(node: &Node, rooted: bool) -> Result<(), Diagnostic> {
    board(node, rooted.then(String::new))
}

/// `segment` under the board's authored path, `None` where the board has none.
fn under(prefix: &Option<String>, segment: &str) -> Option<String> {
    prefix.as_ref().map(|p| match p.is_empty() {
        true => segment.to_string(),
        false => format!("{p}.{segment}"),
    })
}

fn check(kind: &str, name: &str, path: Option<String>, fallback: &str) -> Result<(), Diagnostic> {
    match is_identifier(name) {
        true => Ok(()),
        false => Err(err_name_invalid(
            kind,
            name,
            path.unwrap_or_else(|| fallback.to_string()),
        )),
    }
}

fn board(node: &Node, prefix: Option<String>) -> Result<(), Diagnostic> {
    let Kind::Map(m) = &node.kind else {
        return Ok(());
    };
    for (kind, section) in [
        ("Variable", "variables"),
        ("Query", "queries"),
        ("Chart", "charts"),
    ] {
        if let Some(Kind::Map(names)) = m.get(section).map(|e| &e.value.kind) {
            for name in names.keys() {
                let at = format!("{section}.{name}");
                check(kind, name, under(&prefix, &at), &at)?;
            }
        }
    }
    for (key, id) in [("rows", "row"), ("cols", "col")] {
        if let Some(Kind::Seq(items)) = m.get(key).map(|e| &e.value.kind) {
            for (i, item) in items.iter().enumerate() {
                let at = under(&prefix, &format!("{key}.{i}"));
                layout_item(item, at, &format!("{id}{i}"))?;
            }
        }
    }
    if let Some(Kind::Map(grid)) = m.get("grid").map(|e| &e.value.kind) {
        if let Some(Kind::Seq(cells)) = grid.get("items").map(|e| &e.value.kind) {
            for (i, cell) in cells.iter().enumerate() {
                let item = match &cell.kind {
                    Kind::Map(c) => c.get("item").map(|e| &e.value),
                    _ => None,
                };
                if let Some(item) = item {
                    let at = under(&prefix, &format!("grid.items.{i}"));
                    layout_item(item, at, &format!("grid{i}"))?;
                }
            }
        }
    }
    if let Some(Kind::Map(tabs)) = m.get("tabs").map(|e| &e.value.kind) {
        if let Some(Kind::Seq(panes)) = tabs.get("items").map(|e| &e.value.kind) {
            for (i, pane) in panes.iter().enumerate() {
                board(pane, under(&prefix, &format!("tabs.items.{i}")))?;
            }
        }
    }
    Ok(())
}

/// A layout item: a name or a file (nothing to judge here), a chart written in place, or a
/// nested board. `at` is the item's authored path; an item with none is named by `item_id`.
fn layout_item(item: &Node, at: Option<String>, item_id: &str) -> Result<(), Diagnostic> {
    let Kind::Map(m) = &item.kind else {
        return Ok(());
    };
    if m.contains_key("type") {
        return Ok(());
    }
    let typed = |v: &Node| {
        matches!(&v.kind, Kind::Map(c)
            if matches!(c.get("type").map(|t| &t.value.kind), Some(Kind::Scalar(Value::Str(_)))))
    };
    match m.iter().next() {
        Some((name, entry)) if m.len() == 1 && typed(&entry.value) => {
            check("Chart", name, at.clone(), item_id)
        }
        _ => board(item, at),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn refused(text: &str, rooted: bool) -> String {
        let node = yaml::parse_node(text).unwrap();
        refuse(&node, rooted).unwrap_err().message().unwrap()
    }

    #[test]
    fn identifiers_are_python_s() {
        for ok in ["a", "_a", "a1", "région", "a_b", "Ünï"] {
            assert!(is_identifier(ok), "{ok}");
        }
        for bad in ["", "a b", "a-b", "1a", "a.b", "@a", "a ", "a²", "a\u{200b}"] {
            assert!(!is_identifier(bad), "{bad:?}");
        }
    }

    #[test]
    fn each_name_kind_is_refused_with_its_path() {
        assert_eq!(
            refused("variables:\n  a-b:\n    input: text\n", true),
            "Variable 'a-b' at variables.a-b must be a valid identifier: letters, digits and underscores, not starting with a digit."
        );
        assert!(refused("queries:\n  '1a': {}\n", true).starts_with("Query '1a' at queries.1a "));
        assert!(refused("charts:\n  '@x': {}\n", true).starts_with("Chart '@x' at charts.@x "));
    }

    #[test]
    fn a_nested_board_names_its_place_and_an_imported_one_none() {
        let nested = "rows:\n  - variables:\n      a b: {}\n    rows:\n      - text: x\n";
        assert!(refused(nested, true).contains("at rows.0.variables.a b "));
        assert!(refused(nested, false).contains("at variables.a b "));
        let grid = "grid:\n  items:\n    - item:\n        charts:\n          c.d: {}\n";
        assert!(refused(grid, true).contains("at grid.items.0.charts.c.d "));
        let tab = "tabs:\n  items:\n    - title: T\n      rows:\n        - queries:\n            q-1: {}\n";
        assert!(refused(tab, true).contains("at tabs.items.0.rows.0.queries.q-1 "));
    }

    #[test]
    fn a_chart_written_in_place_is_named_by_its_item() {
        let item = "rows:\n  - 'a-b':\n      type: kpi\n";
        assert!(refused(item, true).starts_with("Chart 'a-b' at rows.0 "));
        assert!(refused(item, false).starts_with("Chart 'a-b' at row0 "));
        assert!(refuse(
            &yaml::parse_node("rows:\n  - kpi_a:\n      type: kpi\n").unwrap(),
            true
        )
        .is_ok());
    }

    #[test]
    fn a_board_file_or_a_chart_name_in_a_layout_is_not_judged_here() {
        let node = yaml::parse_node("rows:\n  - _nav.yml\n  - 'a-b'\n").unwrap();
        assert!(refuse(&node, true).is_ok());
    }
}
