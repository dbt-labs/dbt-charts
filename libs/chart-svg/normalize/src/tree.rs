//! The boards nested in a layout, seen through what the passes over the merged root read of them.
//! A tab is a nested board of its own in Python; here it is one with a text and nothing else.

use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::board::value::VisibleAuthored;
use board_model::variable::VariableAuthored;
use indexmap::IndexMap;
use model_core::Tri;

/// One nested board or tab.
pub struct Nested<'a> {
    pub title: Option<&'a str>,
    pub text: Option<&'a str>,
    pub visible: Option<&'a VisibleAuthored>,
    pub variables: Option<&'a IndexMap<String, VariableAuthored>>,
}

/// Every board and tab below `layout`, outermost first.
pub fn nested(layout: &LayoutAuthored) -> Vec<Nested<'_>> {
    let mut out = Vec::new();
    walk(layout, &mut out);
    out
}

fn walk<'a>(layout: &'a LayoutAuthored, out: &mut Vec<Nested<'a>>) {
    let item = |item: &'a LayoutItemAuthored, out: &mut Vec<Nested<'a>>| {
        if let LayoutItemAuthored::Board(b) = item {
            out.push(Nested {
                title: match &b.body.title {
                    Tri::Value(t) => Some(t.as_str()),
                    _ => None,
                },
                text: match &b.body.text {
                    Tri::Value(t) => Some(t.as_str()),
                    _ => None,
                },
                visible: match &b.visible {
                    Tri::Value(v) => Some(v),
                    _ => None,
                },
                variables: Some(&b.body.variables),
            });
            if let Some(inner) = &b.body.layout {
                walk(inner, out);
            }
        }
    };
    match layout {
        LayoutAuthored::Rows(items) | LayoutAuthored::Cols(items) => {
            items.iter().for_each(|i| item(i, out))
        }
        LayoutAuthored::Grid(g) => g
            .items
            .iter()
            .flatten()
            .filter_map(|cell| cell.item.as_ref())
            .for_each(|i| item(i, out)),
        LayoutAuthored::Tabs(t) => {
            for tab in t.items.iter().flatten() {
                out.push(Nested {
                    title: None,
                    text: match &tab.text {
                        Tri::Value(t) => Some(t.as_str()),
                        _ => None,
                    },
                    visible: None,
                    variables: None,
                });
                if let Some(inner) = &tab.layout {
                    walk(inner, out);
                }
            }
        }
    }
}
