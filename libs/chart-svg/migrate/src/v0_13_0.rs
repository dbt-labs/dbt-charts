//! The 0.12.0 -> 0.13.0 boundary.
//!
//! - **`style.variables.visible` is removed**: one `Deletion`, on the root and every
//!   nested board. No renderer read it, so the strip drew either way; a variable's
//!   own `visible` hides its control.
//! - **`style.charts.table.spark.bar.font` is removed**, and a table chart's own
//!   `style.spark.bar.font`: two `Deletion`s. A spark bar cell draws no text since
//!   its value labels left, so the key styled nothing.
//! - **`style.axis_y.position: left | right` on a horizontal bar becomes
//!   `style.axis_x.position`.** On a horizontal bar each axis key drives the axis it
//!   names: `axis_x.position` places the category names, `axis_y.position` the value
//!   axis (`top` or `bottom`). Both spellings are valid in both grammars, so this is a
//!   `live` move: the document alone decides, and only where the bar is *known*
//!   horizontal from it. A bar is horizontal when its own `style.orientation` says so;
//!   with none, no `layers` and the nearest enclosing `style.charts.bar.orientation`
//!   saying so; and `style.charts.bar.axis_y.position` moves beside its own
//!   `style.charts.bar.orientation: horizontal`. Anything else (`vertical`, `auto`,
//!   unset, a bar with `layers`) is left alone: when the data then makes the bar
//!   horizontal, resolve refuses the key and names `axis_x.position`. A destination
//!   already authored refuses the move.

use std::sync::Arc;

use serde_json::Value as Json;

use crate::decl::{path, Declarations, Deletion, Grammars, Rewrote};
use crate::v0_12_0::value_at;

const VARIABLES_VISIBLE_REASON: &str = "No renderer read it, so the variables strip drew either way. Hide one control with that variable's own `visible: false`.";
const SPARK_BAR_FONT_REASON: &str = "A spark bar cell draws no text, so this font styled nothing.";

fn text_at<'a>(doc: &'a Json, base: &[String], tail: &[&str]) -> Option<&'a Json> {
    value_at(doc, &[base, &path(tail)[..]].concat())
}

/// Whether the bar chart at `chart` is horizontal by what the document says.
fn bar_is_horizontal(doc: &Json, chart: &[String]) -> bool {
    let Some(node) = value_at(doc, chart) else {
        return false;
    };
    if node.get("type").and_then(Json::as_str) != Some("bar") || node.get("layers").is_some() {
        return false;
    }
    if let Some(own) = text_at(doc, chart, &["style", "orientation"]) {
        return own.as_str() == Some("horizontal");
    }
    (0..chart.len())
        .rev()
        .filter_map(|n| chart.get(..n))
        .find_map(|board| text_at(doc, board, &["style", "charts", "bar", "orientation"]))
        .is_some_and(|inherited| inherited.as_str() == Some("horizontal"))
}

fn category_edge(doc: &Json, source: &[String], value: &Json) -> Result<Rewrote, String> {
    let leave = Rewrote {
        leave: true,
        ..Rewrote::default()
    };
    let (Some(scope), true) = (
        source.get(..source.len().saturating_sub(2)),
        matches!(value.as_str(), Some("left" | "right")),
    ) else {
        return Ok(leave);
    };
    let horizontal = !source.iter().any(|s| s == "layers")
        && match scope {
            [chart @ .., style] if style == "style" => bar_is_horizontal(doc, chart),
            [.., style, charts, bar] if style == "style" && charts == "charts" && bar == "bar" => {
                text_at(doc, scope, &["orientation"]).and_then(Json::as_str) == Some("horizontal")
            }
            _ => false,
        };
    if !horizontal {
        return Ok(leave);
    }
    let at = source.join(".");
    if text_at(doc, scope, &["axis_x", "position"]).is_some() {
        return Err(format!(
            "Cannot move `{at}` to `axis_x.position`: it is already set. On a horizontal bar `axis_x.position` places the category names; choose one value and migrate manually."
        ));
    }
    Ok(Rewrote {
        value: Some(value.clone()),
        notice: Some(format!(
            "`{at}` moved to `axis_x.position`: on a horizontal bar `axis_x.position` places the category names and `axis_y.position` (top or bottom) the value axis."
        )),
        ..Rewrote::default()
    })
}

pub fn declarations(g: &Grammars) -> Declarations {
    let moves = g
        .suffix_rename_moves(
            &[(&["axis_x", "position"], &["axis_y", "position"])],
            None,
            None,
        )
        .into_iter()
        .map(|mut m| {
            m.live = true;
            m.rewrite_in_document = Some(Arc::new(category_edge));
            m
        })
        .collect();
    let deletion = |parts: &[&str], reason: &str, chart_type: Option<&str>| {
        let mut d = Deletion::new(g.source, g.target, parts);
        d.reason = Some(reason.into());
        d.chart_type = chart_type.map(str::to_string);
        d
    };
    Declarations {
        moves,
        deletions: vec![
            deletion(
                &["style", "variables", "visible"],
                VARIABLES_VISIBLE_REASON,
                None,
            ),
            deletion(
                &["style", "charts", "table", "spark", "bar", "font"],
                SPARK_BAR_FONT_REASON,
                None,
            ),
            deletion(
                &["style", "spark", "bar", "font"],
                SPARK_BAR_FONT_REASON,
                Some("table"),
            ),
        ],
        ..Declarations::default()
    }
}
