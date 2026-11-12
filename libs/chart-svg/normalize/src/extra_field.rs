//! The hints Python gives an unknown key that has a better answer than the generic "allowed keys"
//! text: a key that belongs on another chart type, under another block, or nowhere. Each reads the
//! path of the key and, for the chart-type cases, the type the board file authored for the chart.

use board_model::board::Board;
use diagnostic::Diagnostic;
use model_core::variant_keys;
use std::collections::BTreeMap;
use std::sync::OnceLock;
use yaml::{Kind, Node, Value};

/// How many of a chart type's keys a hint lists before it elides the rest.
const MAX_LISTED_KEYS: usize = 40;
const SIZING: [&str; 3] = ["aspect_ratio", "min_height", "max_height"];
const AXIS_SLOTS: [&str; 5] = ["axis", "axis_x", "axis_y", "axis_quantitative", "axis_band"];
const LAYOUT_KEYS: [&str; 4] = ["rows", "cols", "grid", "tabs"];
const CARTESIAN: [&str; 6] = ["bar", "line", "area", "scatter", "heatmap", "histogram"];
/// Families with no per-chart card to paint `style.font` or `style.border` onto.
const NO_CARD: [&str; 8] = [
    "bar",
    "line",
    "area",
    "scatter",
    "heatmap",
    "histogram",
    "pie",
    "donut",
];

/// The root keys each chart family takes, by `type:` tag: the keys of its model and the union's
/// `type` (Python's `_authored_chart_fields_by_type`).
fn families() -> &'static BTreeMap<String, Vec<String>> {
    static FAMILIES: OnceLock<BTreeMap<String, Vec<String>>> = OnceLock::new();
    FAMILIES.get_or_init(|| {
        variant_keys::<Board>("charts.*")
            .into_iter()
            .map(|(tag, mut keys)| {
                keys.push("type".into());
                keys.sort();
                (tag, keys)
            })
            .collect()
    })
}

fn child<'a>(node: &'a Node, key: &str) -> Option<&'a Node> {
    let Kind::Map(map) = &node.kind else {
        return None;
    };
    map.get(key).map(|e| &e.value)
}

fn at_path<'a>(root: &'a Node, path: &[String]) -> Option<&'a Node> {
    path.iter().try_fold(root, |node, step| match &node.kind {
        Kind::Seq(items) => items.get(step.parse::<usize>().ok()?),
        _ => child(node, step),
    })
}

fn chart<'a>(root: &'a Node, name: &str) -> Option<&'a Node> {
    child(child(root, "charts")?, name)
}

fn table_height(field: &str, pagination_at: &str, chart: &str) -> String {
    format!(
        "A table is as tall as its rows: delete `{field}:`. To show fewer rows, set \
         `style.pagination.page_rows: 10` {pagination_at}; to cap its slot, give the layout row \
         around it a height:\nrows:\n  - height: 300\n    rows:\n      - {chart}"
    )
}

/// The board layout's `grid:` block, at the root or in a nested board; `grid` also names the
/// axis gridline blocks, which sit under `style` or a chart.
fn is_layout_grid(parent: &[String]) -> bool {
    match parent {
        [last] => last == "grid",
        [key, _, rest @ ..] if key == "rows" || key == "cols" => is_layout_grid(rest),
        _ => false,
    }
}

fn is_axis_title(parent: &[String]) -> bool {
    matches!(parent, [.., slot, title] if title == "title" && AXIS_SLOTS.contains(&slot.as_str()))
}

fn is_total_label(parent: &[String]) -> bool {
    matches!(parent, [.., total, label] if total == "total" && label == "label")
        && parent.iter().any(|s| s == "style")
}

/// What Python's `str()` writes of an authored scalar.
fn python_text(node: &Node) -> Option<String> {
    match &node.kind {
        Kind::Scalar(Value::Str(s)) => Some(s.clone()),
        Kind::Scalar(Value::Bool(b)) => Some(if *b { "True" } else { "False" }.to_string()),
        Kind::Scalar(Value::Null) => Some("None".to_string()),
        Kind::Scalar(Value::Number(n)) => Some(n.to_string()),
        _ => None,
    }
}

fn json_string(s: &str) -> String {
    serde_json::Value::String(s.to_string()).to_string()
}

/// The hint for the unknown key at `path` (the key last), or `None` when it has no special case.
/// `allowed` is what the block takes; `board` is the file as authored.
pub(crate) fn hint(path: &[String], allowed: &[&str], board: &Node) -> Option<String> {
    let (field, parent) = path.split_last()?;
    let field = field.as_str();
    let shown = if parent.is_empty() {
        "root".to_string()
    } else {
        parent.join(".")
    };
    if field == "gap" && is_layout_grid(parent) {
        return Some(
            "Grid spacing is set by `style.layout.grid.gap` (pixels) — the `grid.gap` key never \
             had any effect and should be removed."
                .into(),
        );
    }
    let in_theme = path.starts_with(&["style".into(), "charts".into()]);
    let family = || path.get(2).map(String::as_str);

    if in_theme
        && path.len() >= 4
        && matches!(family(), Some("kpi" | "table"))
        && SIZING.contains(&field)
    {
        let family = family()?;
        return Some(format!(
            "`style.charts.{family}` uses a fixed sizing contract — `{field}` has no effect on \
             {family} charts and should be removed."
        ));
    }
    if in_theme && family() == Some("table") && path.len() == 4 && field.contains("height") {
        return Some(table_height(field, "on the table chart", "<table chart>"));
    }
    if in_theme && path.len() >= 4 && family() == Some("heatmap") && field == "axis_quantitative" {
        return Some(
            "`style.charts.heatmap.axis_quantitative` is not supported — heatmap's axes are both \
             nominal, so it has no quantitative axis to style. Use \
             `style.charts.heatmap.axis_band` instead."
                .into(),
        );
    }
    if field == "text" && is_axis_title(parent) {
        return Some(
            "Axis title text is authored on the chart, as `x_label:` / `y_label:` — `title:` \
             here styles the title's typography only."
                .into(),
        );
    }
    if field == "category_colors" && parent.last().is_some_and(|p| p == "style") {
        return Some(
            "`category_colors` pins a category to one color across the whole board, so it is set \
             board-wide at `style.charts.category_colors`, keyed by data field."
                .into(),
        );
    }
    if field == "fonts" && allowed.contains(&"font") && !is_axis_title(parent) {
        return Some(format!(
            "There are no font roles at {shown}; `font` is a mapping (family, size, weight, case, \
             color):\nfont:\n  family: Georgia\n  size: 14"
        ));
    }
    if field == "subtitle"
        && parent
            .first()
            .is_none_or(|p| LAYOUT_KEYS.contains(&p.as_str()))
    {
        let mut written = parent.to_vec();
        written.push("subtitle".into());
        let value = at_path(board, &written)
            .and_then(python_text)
            .map_or("<your subtitle>".to_string(), |s| json_string(&s));
        return Some(format!(
            "A board has `title` and `text`, no `subtitle`. Put it first in `text:` — `text: \
             {value}` — or on a chart, which does take `subtitle`."
        ));
    }
    if field == "visible" && parent.last().is_some_and(|p| p == "tooltip") {
        return Some(
            "`style.charts.tooltip` has no `visible` switch: delete the line. It styles the \
             tooltip (background, border, font, ...)."
                .into(),
        );
    }
    if field == "board" && parent.is_empty() {
        return Some(
            "Board properties (title, rows, queries, ...) go at the top level of the file, not \
             nested under 'board:'."
                .into(),
        );
    }
    if field == "field_labels" && parent.last().is_some_and(|p| p == "table") {
        return Some(
            "A table's header text is authored per column, as `style.columns.<col>.label:`.".into(),
        );
    }
    if field == "text" && is_total_label(parent) {
        return Some(
            "Donut center label text is authored on the chart, as `total.label:` — \
             `style.total.label` here styles the label's typography only."
                .into(),
        );
    }

    let charts = path.first().is_some_and(|p| p == "charts");
    let name = path.get(1)?;
    // The innermost layer's `type` when it is a cartesian one, else the chart's.
    let kind = || {
        if !charts {
            return None;
        }
        let type_at =
            |at: Option<&[String]>| match &child(at.and_then(|a| at_path(board, a))?, "type")?.kind
            {
                Kind::Scalar(Value::Str(t)) => Some(t.as_str()),
                _ => None,
            };
        let layer = path
            .iter()
            .rposition(|p| p == "layers")
            .and_then(|i| type_at(path.get(..i + 2)))
            .filter(|t| CARTESIAN.contains(t));
        layer.or_else(|| type_at(path.get(..2)))
    };

    if charts
        && path.len() == 4
        && path.get(2).is_some_and(|p| p == "style")
        && kind() == Some(field)
    {
        let nested = chart(board, name)
            .and_then(|c| child(c, "style"))
            .and_then(|s| child(s, field));
        let example = match nested.map(|n| &n.kind) {
            Some(Kind::Map(m)) if !m.is_empty() => {
                format!(", e.g. `charts.{name}.style.{}`", m.keys().next()?)
            }
            _ => String::new(),
        };
        return Some(format!(
            "Inside one chart's `style`, write the {field} keys directly: delete the `{field}:` \
             line and move its keys up one level{example}. `style.charts.{field}` is the \
             board-wide form."
        ));
    }

    if field == "font" || field == "border" {
        let no_card = if in_theme && path.len() == 4 {
            family().filter(|f| NO_CARD.contains(f))
        } else if charts && parent.len() >= 2 && parent.last().is_some_and(|p| p == "style") {
            kind().filter(|t| NO_CARD.contains(t))
        } else {
            None
        };
        if let Some(t) = no_card {
            let written = path.join(".");
            return Some(if field == "border" {
                format!(
                    "Delete `{written}`: a {t} chart has no border of its own. A border around \
                     every chart card is `style.charts.border`; omit it for no border."
                )
            } else {
                format!(
                    "`style.{field}` is not supported on `type: {t}` — {t} has no per-chart card \
                     to paint it onto (it renders via Vega-Lite with no card surface separate \
                     from the board frame). The field has no effect and should be removed."
                )
            });
        }
    }

    if charts && path.len() >= 3 {
        if parent.last().is_some_and(|p| p == "style") && (field == "height" || field == "width") {
            return Some(format!(
                "chart.{field} must be set at the chart root, not under style: — move it up one \
                 level."
            ));
        }
        let t = kind()?;
        if field == "legend" && path.iter().any(|p| p == "style") && matches!(t, "kpi" | "table") {
            return Some(format!(
                "`type: {t}` charts never paint a legend — the field has no effect and should \
                 be removed."
            ));
        }
        let cartesian = CARTESIAN.contains(&t);
        if (field == "format" || field == "formatter") && (cartesian || t == "table") {
            if t == "table" {
                return Some(format!(
                    "`{field}:` is not supported on `type: table`. Use \
                     `style.columns.<column_name>.format:` per column instead."
                ));
            }
            let x = path
                .iter()
                .skip(2)
                .rev()
                .find(|p| *p == "axis_x" || *p == "axis_y")
                .is_some_and(|p| p == "axis_x");
            return Some(if x {
                format!(
                    "`{field}:` is not supported on `type: {t}`. Use `style.axis_x.labels.format:` \
                     for x-axis tick labels — it is never used for tooltip formatting."
                )
            } else {
                format!(
                    "`{field}:` is not supported on `type: {t}`. Use `style.axis_y.labels.format:` \
                     for axis and tooltip formatting, or `formats:` on the query to format the \
                     column everywhere."
                )
            });
        }
        if t == "table" && field == "height" {
            return Some(table_height(field, &format!("on `charts.{name}`"), name));
        }
        if t == "table" && field == "sort" {
            return Some(
                "`sort:` is not supported on `type: table`. A table shows rows in query order: \
                 sort in the query with `ORDER BY`."
                    .into(),
            );
        }
        if t == "heatmap" && field == "axis_quantitative" {
            return Some(
                "`axis_quantitative:` is not supported on `type: heatmap`. Heatmap's axes are \
                 both nominal, so it has no quantitative axis to style. Use `axis_band:` instead."
                    .into(),
            );
        }
        return supported_types(t, field);
    }
    None
}

/// A key the chart's own type lacks but others take: those types, and what this one takes.
fn supported_types(chart_type: &str, field: &str) -> Option<String> {
    let families = families();
    let own = families.get(chart_type)?;
    if own.iter().any(|k| k == field) {
        return None;
    }
    let supporting: Vec<&str> = families
        .iter()
        .filter(|(_, keys)| keys.iter().any(|k| k == field))
        .map(|(tag, _)| tag.as_str())
        .collect();
    if supporting.is_empty() {
        return None;
    }
    let more = if own.len() > MAX_LISTED_KEYS { ", ..." } else { "" };
    let accepted = own
        .iter()
        .take(MAX_LISTED_KEYS)
        .map(String::as_str)
        .collect::<Vec<_>>()
        .join(", ");
    let mut hint = format!(
        "`{field}:` is not supported on `type: {chart_type}`. Supported chart types for \
         `{field}:`: {}. Fields accepted on `type: {chart_type}`: {accepted}{more}.",
        supporting.join(", ")
    );
    if field == "height" || field == "width" {
        hint.push_str(&format!(
            " Use the chart family's sizing surface or the surrounding layout instead of \
             chart-root `{field}:`."
        ));
    }
    Some(hint)
}

/// `d` with the special hint of its unknown key, when it has one.
pub(crate) fn special(mut d: Diagnostic, board: &Node) -> Diagnostic {
    if d.code != "ERR-EXTRA-FIELD" {
        return d;
    }
    let Some(path) = d.path.clone() else {
        return d;
    };
    let allowed: Vec<&str> = d
        .params
        .get("allowed_keys")
        .and_then(|a| a.as_array())
        .map(|a| a.iter().filter_map(|k| k.as_str()).collect())
        .unwrap_or_default();
    match hint(&path.0, &allowed, board) {
        Some(h) => {
            // Python names a nearest key only in the generic hint.
            d.params.remove("suggestion");
            d.with_hint(h)
        }
        None => d,
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    fn at(path: &[&str]) -> Vec<String> {
        path.iter().map(|s| s.to_string()).collect()
    }

    // The board model accepts `font` and `border` where these hints apply, so no board reaches
    // them through a compile yet.
    #[test]
    fn a_card_less_family_has_no_font_or_border_to_paint() {
        let board = yaml::parse_node("charts:\n  c:\n    type: bar\n").unwrap();
        let theme = hint(&at(&["style", "charts", "bar", "border"]), &[], &board).unwrap();
        assert!(
            theme.contains("a bar chart has no border of its own"),
            "{theme}"
        );
        let chart = hint(&at(&["charts", "c", "style", "font"]), &[], &board).unwrap();
        assert!(
            chart.contains("`style.font` is not supported on `type: bar`"),
            "{chart}"
        );
    }
}
