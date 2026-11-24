//! A board file's own `grid:` as authored, before any fold, in every nested board and tab pane.
//! Python validates each file on its own, so a layout missing a required key is refused even when
//! a template would supply it; the patch parse here fills later and would not notice.
//!
//! Refused as Python refuses them: a missing `grid.items` or cell `item`.

use diagnostic::diag::err_validation_field;
use diagnostic::Diagnostic;
use yaml::{Kind, Node};

/// `Err` is Python's `ERR-VALIDATION-FIELD` for the first shape it refuses.
pub(crate) fn refuse(node: &Node) -> Result<(), Diagnostic> {
    walk(node, &mut Vec::new())
}

fn walk(node: &Node, at: &mut Vec<String>) -> Result<(), Diagnostic> {
    let Kind::Map(m) = &node.kind else {
        return Ok(());
    };
    for key in ["rows", "cols"] {
        let Some(Kind::Seq(items)) = m.get(key).map(|e| &e.value.kind) else {
            continue;
        };
        for (i, item) in items.iter().enumerate() {
            nested(item, at, &[key, &i.to_string()])?;
        }
    }
    if let Some(Kind::Map(grid)) = m.get("grid").map(|e| &e.value.kind) {
        let Some(items) = grid.get("items") else {
            return Err(required(at, &["grid", "items"]));
        };
        if let Kind::Seq(cells) = &items.value.kind {
            for (i, cell) in cells.iter().enumerate() {
                let Kind::Map(cell_map) = &cell.kind else {
                    continue;
                };
                let i = i.to_string();
                match cell_map.get("item") {
                    Some(item) => nested(&item.value, at, &["grid", "items", &i, "item"])?,
                    None => return Err(required(at, &["grid", "items", &i, "item"])),
                }
            }
        }
    }
    if let Some(Kind::Map(panes)) = m.get("tabs").map(|e| &e.value.kind) {
        for (label, pane) in panes.iter() {
            nested(&pane.value, at, &["tabs", label])?;
        }
    }
    Ok(())
}

/// A board (or tab pane) nested at `steps` below `at`: its own layout is read the same way.
fn nested(node: &Node, at: &mut Vec<String>, steps: &[&str]) -> Result<(), Diagnostic> {
    at.extend(steps.iter().map(|s| s.to_string()));
    let r = walk(node, at);
    at.truncate(at.len() - steps.len());
    r
}

fn required(at: &[String], steps: &[&str]) -> Diagnostic {
    let path: Vec<&str> = at
        .iter()
        .map(String::as_str)
        .chain(steps.iter().copied())
        .collect();
    err_validation_field(diagnostic::dotted(&path), "Field required")
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn message(yaml_text: &str) -> String {
        let node = yaml::parse_node(yaml_text).unwrap();
        refuse(&node).unwrap_err().message().unwrap()
    }

    #[test]
    fn a_layout_is_refused_as_the_file_wrote_it() {
        assert_eq!(
            message("grid:\n  columns: 2\n"),
            "Field 'grid.items': Field required"
        );
        assert_eq!(
            message("rows:\n  - grid:\n      items:\n        - {}\n"),
            "Field 'rows.0.grid.items.0.item': Field required"
        );
        assert_eq!(
            message("tabs:\n  T:\n    grid:\n      items:\n        - {}\n"),
            "Field 'tabs.T.grid.items.0.item': Field required"
        );
    }
}
