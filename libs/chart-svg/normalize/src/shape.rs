//! A board file's own `tabs:` and `grid:` as authored, before any fold. Python validates each
//! file on its own, so a layout missing a required key is refused even when a template would
//! supply it; the patch parse here fills later and would not notice.

use diagnostic::diag::err_validation_field;
use diagnostic::Diagnostic;
use yaml::{Kind, Node, Value};

const TABS_KEYS: [&str; 4] = ["id", "position", "default", "items"];

/// `Err` is Python's `ERR-VALIDATION-FIELD` for the first shape it refuses. `own` is a file's
/// root board, the only one whose null leaves Python rejects (a nested or imported board's pass).
pub(crate) fn refuse(node: &Node, own: bool) -> Result<(), Diagnostic> {
    walk(node, &mut Vec::new(), own)
}

fn walk(node: &Node, at: &mut Vec<String>, own: bool) -> Result<(), Diagnostic> {
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
    if let Some(Kind::Map(tabs)) = m.get("tabs").map(|e| &e.value.kind) {
        if own && at.is_empty() {
            if let Some(key) = TABS_KEYS.into_iter().find(|k| {
                matches!(
                    tabs.get(*k).map(|e| &e.value.kind),
                    Some(Kind::Scalar(Value::Null))
                )
            }) {
                return Err(err_validation_field(
                    "tabs",
                    format!("Value error, {key}: null is not accepted here. Omit the key to leave the field unset."),
                ));
            }
        }
        let Some(items) = tabs.get("items") else {
            return Err(required(at, &["tabs", "items"]));
        };
        if let Kind::Seq(panes) = &items.value.kind {
            for (i, pane) in panes.iter().enumerate() {
                nested(pane, at, &["tabs", "items", &i.to_string()])?;
            }
        }
    }
    Ok(())
}

/// A board (or tab pane) nested at `steps` below `at`: its own layout is read the same way.
fn nested(node: &Node, at: &mut Vec<String>, steps: &[&str]) -> Result<(), Diagnostic> {
    at.extend(steps.iter().map(|s| s.to_string()));
    let r = walk(node, at, false);
    at.truncate(at.len() - steps.len());
    r
}

fn required(at: &[String], steps: &[&str]) -> Diagnostic {
    let path: Vec<&str> = at
        .iter()
        .map(String::as_str)
        .chain(steps.iter().copied())
        .collect();
    err_validation_field(path.join("."), "Field required")
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn message(yaml_text: &str, own: bool) -> String {
        let node = yaml::parse_node(yaml_text).unwrap();
        refuse(&node, own).unwrap_err().message().unwrap()
    }

    #[test]
    fn a_layout_is_refused_as_the_file_wrote_it() {
        let items = "  items:\n    - title: T\n      rows:\n        - text: x\n";
        assert_eq!(
            message(&format!("tabs:\n  id: null\n{items}"), true),
            "Field 'tabs': Value error, id: null is not accepted here. Omit the key to leave the field unset."
        );
        assert!(refuse(
            &yaml::parse_node(&format!("tabs:\n  id: null\n{items}")).unwrap(),
            false
        )
        .is_ok());
        assert_eq!(
            message("tabs:\n  id: dupv\n", true),
            "Field 'tabs.items': Field required"
        );
        assert_eq!(
            message("grid:\n  columns: 2\n", true),
            "Field 'grid.items': Field required"
        );
        assert_eq!(
            message("rows:\n  - grid:\n      items:\n        - {}\n", false),
            "Field 'rows.0.grid.items.0.item': Field required"
        );
        assert_eq!(
            message(
                "tabs:\n  items:\n    - title: T\n      grid:\n        items:\n          - {}\n",
                true
            ),
            "Field 'tabs.items.0.grid.items.0.item': Field required"
        );
    }
}
