//! The 0.5.0 -> 0.6.0 boundary: `data_table` -> `support_table`, `description`
//! -> `notes`, `charts.kpi.tones` -> board-level `tones`, a layer's
//! `axis_y.label` -> `labels`, retired built-in `theme:` names, and a long list
//! of dead keys and per-family `font`/`border` slots removed.
//!
//! `theme:` is authoring sugar with no schema field, so its move is declared by
//! hand at the document root only. Its value map must be total over every legal
//! `theme:` value, not just the retired names: a board naming a current theme
//! beside some other retired construct is routed through this boundary, and an
//! unmapped value would fail it. The identity entries therefore come from the
//! target grammar's `theme` enum.

use std::sync::Arc;

use crate::decl::{path, Declarations, Deletion, Grammars, Mapped, Move, ScalarKey, ValueMap};

/// Retired built-in theme names, whichever key holds them (`theme:` or an
/// `extends:` entry). Retired names only: an identity entry for a live theme
/// would make `extends: clarity` read as retired.
pub const THEME_RENAMES: &[(&str, &str)] = &[
    ("editorial", "clarity"),
    ("plain", "clarity"),
    ("solid", "clarity"),
    ("cream", "paper"),
];

/// `(new_tail, old_tail)`.
const RENAMES: &[(&[&str], &[&str])] = &[
    (&["tones"], &["charts", "kpi", "tones"]),
    (&["support_table"], &["data_table"]),
    (&["notes"], &["description"]),
    (&["axis_y", "labels"], &["axis_y", "label"]),
];

const DELETED_TAILS: &[&[&str]] = &[
    &["model"],
    &["dimension"],
    &["measure"],
    &["grid", "row_height"],
    &["grid", "default_width"],
    &["grid", "default_height"],
    &["grid", "gap"],
    &["input", "border", "width"],
    &["input", "border", "color"],
    &["input", "border", "dash_array"],
    &["input", "border", "line_cap"],
    &["input", "border", "dash_offset"],
    &["columns", "border", "width"],
    &["columns", "border", "color"],
    &["columns", "border", "dash_array"],
    &["columns", "border", "line_cap"],
    &["columns", "border", "dash_offset"],
    &["spark", "bar", "border", "width"],
    &["spark", "bar", "border", "color"],
    &["spark", "bar", "border", "dash_array"],
    &["spark", "bar", "border", "line_cap"],
    &["spark", "bar", "border", "dash_offset"],
    &["spark_bar", "border", "width"],
    &["spark_bar", "border", "color"],
    &["spark_bar", "border", "dash_array"],
    &["spark_bar", "border", "line_cap"],
    &["spark_bar", "border", "dash_offset"],
    &["charts", "bar", "font"],
    &["charts", "bar", "border"],
    &["charts", "line", "font"],
    &["charts", "line", "border"],
    &["charts", "area", "font"],
    &["charts", "area", "border"],
    &["charts", "scatter", "font"],
    &["charts", "scatter", "border"],
    &["charts", "histogram", "font"],
    &["charts", "histogram", "border"],
    &["charts", "heatmap", "font"],
    &["charts", "heatmap", "border"],
    &["charts", "pie", "font"],
    &["charts", "pie", "border"],
    &["charts", "heatmap", "axis_quantitative"],
    &["charts", "kpi", "color"],
];

/// Tails retired on one chart family while a same-named sibling stays live on
/// the others.
const NO_CARD_FAMILIES: &[&str] = &[
    "bar",
    "line",
    "area",
    "scatter",
    "histogram",
    "heatmap",
    "pie",
    "donut",
];
const BORDER_FIELDS: &[&str] = &["width", "color", "dash_array", "line_cap", "dash_offset"];

fn no_card_reason(family: &str) -> String {
    format!("{family} has no per-chart card to paint it onto (it renders via Vega-Lite with no card surface separate from the board frame). The field had no effect and has been removed.")
}

fn paint_reason(tail: &[&str]) -> Option<&'static str> {
    Some(match tail {
        ["grid", "gap"] => "grid.gap never had any effect; grid spacing is set by style.layout.grid.gap (pixels).",
        ["style", "color"] => "kpi's style.color is gone; style.value.font.color paints the value text instead. Every other chart family keeps its own style.color, which types as an object rather than a bare color string.",
        ["style", "axis_quantitative"] => "heatmap has no quantitative axis to style: both of its axes are nominal, and the magnitude lives on the color channel. Use style.axis_band for the axes it does have.",
        ["charts", "heatmap", "axis_quantitative"] => "heatmap has no quantitative axis to style: both of its axes are nominal. Use style.charts.heatmap.axis_band for the axes it does have.",
        ["charts", "kpi", "color"] => "style.charts.kpi.color is gone; style.charts.kpi.value.font.color paints the value text instead. This was a real theme override, so the board's kpi text takes its inherited color until you move it.",
        _ => return None,
    })
}

/// The board-level `charts.<family>.font|border` tails name a family with no card.
fn card_style_reason(tail: &[&str]) -> Option<String> {
    match tail {
        ["charts", family, "font" | "border"] if NO_CARD_FAMILIES.contains(family) => {
            Some(no_card_reason(family))
        }
        _ => None,
    }
}

fn theme_value_map(g: &Grammars) -> ValueMap {
    let mut map: ValueMap = THEME_RENAMES
        .iter()
        .map(|(k, v)| (ScalarKey::Str((*k).into()), Mapped::Str((*v).into())))
        .collect();
    let current = g
        .target_schema
        .root()
        .get("properties")
        .and_then(|p| p.get("theme"))
        .and_then(|t| t.get("enum"))
        .and_then(|e| e.as_array());
    for name in current.into_iter().flatten().filter_map(|v| v.as_str()) {
        map.insert(ScalarKey::Str(name.into()), Mapped::Str(name.into()));
    }
    map
}

pub fn declarations(g: &Grammars) -> Declarations {
    let mut moves = g.suffix_rename_moves(RENAMES, None, None);
    let mut theme = Move::new(g.source, g.target, path(&["theme"]), path(&["theme"]));
    theme.value_map = Some(Arc::new(theme_value_map(g)));
    moves.push(theme);

    let mut deletions: Vec<Deletion> = DELETED_TAILS
        .iter()
        .map(|t| {
            let mut d = Deletion::new(g.source, g.target, t);
            d.reason = card_style_reason(t).or_else(|| paint_reason(t).map(String::from));
            d
        })
        .collect();
    let mut scoped = |tail: &[&str], family: &str, reason: Option<String>| {
        let mut d = Deletion::new(g.source, g.target, tail);
        d.reason = reason;
        d.chart_type = Some(family.to_string());
        deletions.push(d);
    };
    scoped(
        &["style", "color"],
        "kpi",
        paint_reason(&["style", "color"]).map(String::from),
    );
    scoped(
        &["style", "axis_quantitative"],
        "heatmap",
        paint_reason(&["style", "axis_quantitative"]).map(String::from),
    );
    for family in NO_CARD_FAMILIES {
        for field in ["font", "border"] {
            scoped(&["style", field], family, Some(no_card_reason(family)));
        }
    }
    for field in BORDER_FIELDS {
        scoped(&["style", "border", field], "spark_bar", None);
    }
    Declarations {
        moves,
        deletions,
        ..Declarations::default()
    }
}
