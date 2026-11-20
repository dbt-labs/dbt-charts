//! The 0.11.0 -> 0.12.0 boundary: `legend` and the endpoint-label leaves become `style.key`.
//!
//! - **`style.key` replaces `legend` and the endpoint-label leaves.** `legend.*` becomes
//!   `key.legend.*` at every scope; the rest is per-leaf in `key_moves`.
//! - **`style.color.static` becomes the paint of the chart's primary mark.** The
//!   constant paint a chart has with no `color:` channel is authored on its mark:
//!   `bar` and `histogram` to `style.marks.bar.color`, `area` to
//!   `style.marks.area.color`, `line` to `style.marks.line.stroke.color`, `scatter`,
//!   `point_map` and `bubble_map` to `style.marks.point.color`, `pie` and `donut` to
//!   `style.marks.slice.color`, `heatmap` to `style.marks.rect.color`, `map` and
//!   `geoshape` to `style.marks.geoshape.fill`, and a `table` to the cell text color,
//!   `style.column_defaults.font.color`. One `Move` per chart `type:`, and the same
//!   move under `style.charts.<family>` on the theme and board tiers. A retired
//!   color token is respelled on the way. A destination the author already set
//!   refuses the move.
//! - **A table's conditional formatting is on the column it paints.** The
//!   chart-root `conditional_formatting: {col: {when: [rule]}}` becomes
//!   `style.columns.<col>.conditional: [rule]`, merged into the column's
//!   `style.columns.<col>` entry when it has one; a leaf both hold refuses. A
//!   rule's cell style keeps its keys (`background`, `font`, `glyph`,
//!   `glyph_color`, `tone`): they are the column's own `style.columns.<col>`
//!   fields. Rule order and semantics are unchanged: every matching rule applies
//!   and the later one wins per key, so a board that lists a general rule after
//!   a specific one needs the two swapped by hand.
//! - **A KPI's conditional formatting is `style.conditional`, and its background is
//!   `style.background`.** `conditional_formatting: {col: {when: [rule]}}` becomes
//!   `style.conditional: {col: [rule]}`. A rule on the `support.value` column styles the
//!   support row (`font.color` becomes `support.font.color`); a rule on any other column styles
//!   the headline (`font.color` becomes `value.font.color`, `background` stays). Evaluation is
//!   one ordered list per column: every matching rule applies and the later one wins per key,
//!   and `default` applies only when no rule in its column matched, so a `default` that relied
//!   on the old per-key split repaints. A support rule's `tone` becomes `support.tone`. A rule
//!   that set `glyph` or `glyph_color`, or a headline `tone`, refuses: draw those with `good`.
//!   The chart-root `background:` becomes `style.background:`, and refuses when
//!   `style.background` is also authored.
//! - **A board's `style.title.font.size` is removed**: one `Deletion`, on the root
//!   and every nested board. A board, section or prose title sizes from the heading
//!   ramp (`style.fonts.heading.sizes`, `style.fonts.section.sizes`); the key set
//!   no painted size. A chart's own `style.title.font.size` is a live override and
//!   stays.
//! - **A variable's `options.label_column` is removed**: one `Deletion`. Nothing read it;
//!   a select shows each option's value.


use std::sync::Arc;

use serde_json::Value as Json;

use crate::decl::{path, Declarations, Deletion, DocumentRewrite, Grammars, Move, Rewritten, Rewrote};
use crate::pyrepr::repr_json;
use crate::v0_11_0::{respell_secondary_ink, retired_scaffold_token_map};

const TITLE_SIZE_REASON: &str = "A board title's size comes from the heading ramp, so this key set none. Set `style.fonts.heading.sizes` (or `style.fonts.section.sizes`) to size board and section titles, or a chart's own `style.title.font.size`.";
const LABEL_COLUMN_REASON: &str = "Nothing read it: a select shows the values of `options.column`.";
/// A chart `type:` to the mark path (under `marks`) holding its constant paint.
const STATIC_COLOR_MARK_PAINT: [(&str, &[&str]); 12] = [
    ("bar", &["bar", "color"]),
    ("histogram", &["bar", "color"]),
    ("area", &["area", "color"]),
    ("line", &["line", "stroke", "color"]),
    ("scatter", &["point", "color"]),
    ("point_map", &["point", "color"]),
    ("bubble_map", &["point", "color"]),
    ("pie", &["slice", "color"]),
    ("donut", &["slice", "color"]),
    ("heatmap", &["rect", "color"]),
    ("map", &["geoshape", "fill"]),
    ("geoshape", &["geoshape", "fill"]),
];
/// The `style.charts.<family>` keys; `donut`, `bubble_map` and `map` share
/// `pie`, `point_map` and `geoshape`.
const STATIC_COLOR_THEME_FAMILIES: [&str; 9] = [
    "bar", "histogram", "area", "line", "scatter", "point_map", "pie", "heatmap", "geoshape",
];
/// A table's constant paint is its cell text color: the column default font.
const TABLE_INK: [&str; 3] = ["column_defaults", "font", "color"];
const STATIC_COLOR: [&str; 2] = ["color", "static"];

const RAIL_FAMILIES: [&str; 3] = ["line", "area", "bar"];
/// The families whose key can only be a legend and whose 0.11 base theme left `legend.visible`
/// unset, so a board-wide hidden legend reached them. Scatter, heatmap and geoshape pinned it `true`.
const BOARD_HIDDEN_LEGEND_FAMILIES: [&str; 2] = ["histogram", "point_map"];
/// The legend leaves a pie's key never read: its legend is the attached table, placed by `position`.
const PIE_LEGEND_LEAVES: [&str; 10] =
    ["direction", "columns", "compact_columns", "row", "label", "title", "symbol_limit", "values", "symbol_shape", "symbol_fill"];
const NOT_CHART_HOLDERS: [&str; 5] = ["style", "layers", "annotations", "support_table", "queries"];
const ENDPOINT_VISIBLE: [&str; 2] = ["endpoint_labels", "visible"];
const STRIP_REASON: &str = "A line or area prints its series names beside the plot, with no label strip to size.";
const TWICE: &str = "names the series twice: a legend beside endpoint labels. Set `key.type` to `legend` or `direct`.";

type Path = Vec<String>;

fn value_at<'a>(doc: &'a Json, p: &[String]) -> Option<&'a Json> {
    p.iter().try_fold(doc, |node, part| match node {
        Json::Object(m) => m.get(part),
        Json::Array(items) => part.parse::<usize>().ok().and_then(|i| items.get(i)),
        _ => None,
    })
}

/// A toggle as the old `bool | auto` field read it.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Visible {
    On,
    Off,
    Auto,
}

fn coerce(value: &Json) -> Option<Visible> {
    let flag = |b: bool| if b { Visible::On } else { Visible::Off };
    match value {
        Json::Bool(b) => Some(flag(*b)),
        Json::String(s) if s == "auto" => Some(Visible::Auto),
        Json::String(s) => match s.to_lowercase().as_str() {
            "1" | "on" | "t" | "true" | "y" | "yes" => Some(Visible::On),
            "0" | "off" | "f" | "false" | "n" | "no" => Some(Visible::Off),
            _ => None,
        },
        Json::Number(n) => match n.as_f64() {
            Some(1.0) => Some(Visible::On),
            Some(0.0) => Some(Visible::Off),
            _ => None,
        },
        _ => None,
    }
}

fn visible(source: &[String], value: &Json) -> Result<Visible, String> {
    coerce(value).ok_or_else(|| {
        format!("Cannot migrate `{}: {}`. Migrate this field manually.", source.join("."), repr_json(value))
    })
}

/// The mapping holding the key a path ends `depth` keys below.
fn holder(source: &[String], depth: usize) -> Path {
    prefix(source, source.len().saturating_sub(depth))
}

fn prefix(path: &[String], n: usize) -> Path {
    path.iter().take(n).cloned().collect()
}

fn at(parent: &[String], tail: &[&str]) -> Path {
    [parent, &path(tail)[..]].concat()
}

fn endpoint_visible(doc: &Json, parent: &[String]) -> Result<Option<Visible>, String> {
    let at = at(parent, &ENDPOINT_VISIBLE);
    value_at(doc, &at).filter(|v| !v.is_null()).map(|v| visible(&at, v)).transpose()
}

fn endpoints_edge(doc: &Json, parent: &[String]) -> bool {
    cascaded(doc, parent, &["legend", "position", "edge"]).and_then(Json::as_str) == Some("endpoints")
}

fn shown_legend(doc: &Json, parent: &[String]) -> Result<bool, String> {
    Ok(effective(doc, parent, legend_flag)?.is_some_and(|(v, _)| v == Visible::On))
}

fn legend_flag(doc: &Json, parent: &[String]) -> Result<Option<Visible>, String> {
    let at = at(parent, &["legend", "visible"]);
    value_at(doc, &at).filter(|v| !v.is_null()).map(|v| visible(&at, v)).transpose()
}

/// The mappings a `parent` inherits from, nearest first. The cascade resolves a family path
/// before any board-wide one, whichever board sets it: a chart's `style` reads every enclosing
/// board's `charts.<family>`, then every enclosing board's `charts`; a family reads the same
/// family on the boards around its own, then `charts` on its own board and the boards around it.
fn inherited(doc: &Json, parent: &[String]) -> Vec<Path> {
    let Some((last, above)) = parent.split_last() else {
        return Vec::new();
    };
    let charts_of = |board: &[String], boards: std::ops::Range<usize>| -> Vec<Path> {
        boards.rev().map(|n| at(&prefix(board, n), &["style", "charts"])).collect()
    };
    let with_family = |family: &str, charts: Vec<Path>| -> Vec<Path> {
        let families = charts.iter().map(|c| at(c, &[family])).collect::<Vec<_>>();
        families.into_iter().chain(charts).collect()
    };
    match (last.as_str(), above) {
        ("style", chart) => {
            let charts = charts_of(chart, 0..chart.len());
            match value_at(doc, &at(chart, &["type"])).and_then(Json::as_str) {
                Some(family) => with_family(family, charts),
                None => charts,
            }
        }
        ("charts", _) => Vec::new(),
        (family, [rest @ .., style, charts]) if style == "style" && charts == "charts" => {
            let outer = charts_of(rest, 0..rest.len())
                .into_iter()
                .map(|c| at(&c, &[family]))
                .collect::<Vec<_>>();
            outer.into_iter().chain(charts_of(rest, 0..rest.len() + 1)).collect()
        }
        _ => vec![above.to_vec()],
    }
}

/// The flag as the cascade resolves it at `parent`, and the mapping that sets it.
fn effective(
    doc: &Json,
    parent: &[String],
    flag: fn(&Json, &[String]) -> Result<Option<Visible>, String>,
) -> Result<Option<(Visible, Path)>, String> {
    for holder in std::iter::once(parent.to_vec()).chain(inherited(doc, parent)) {
        if let Some(found) = flag(doc, &holder)? {
            return Ok(Some((found, holder)));
        }
    }
    Ok(None)
}

/// The nearest value of `tail` the cascade sets at `parent`.
fn cascaded<'a>(doc: &'a Json, parent: &[String], tail: &[&str]) -> Option<&'a Json> {
    std::iter::once(parent.to_vec())
        .chain(inherited(doc, parent))
        .find_map(|holder| value_at(doc, &at(&holder, tail)).filter(|v| !v.is_null()))
}

/// Mappings under `node` that are charts of one of `families`. A chart never holds another, and
/// `style`, `layers`, `annotations`, `support_table` and `queries` hold typed mappings that are not charts.
fn charts_of_type(node: &Json, families: &[&str], at_path: &mut Path, out: &mut Vec<Path>) {
    match node {
        Json::Object(m) if m.get("type").and_then(Json::as_str).is_some_and(|t| families.contains(&t)) => out.push(at_path.clone()),
        Json::Object(m) => {
            for (key, child) in m.iter().filter(|(k, _)| !NOT_CHART_HOLDERS.contains(&k.as_str())) {
                at_path.push(key.clone());
                charts_of_type(child, families, at_path, out);
                at_path.pop();
            }
        }
        Json::Array(items) => {
            for (i, child) in items.iter().enumerate() {
                at_path.push(i.to_string());
                charts_of_type(child, families, at_path, out);
                at_path.pop();
            }
        }
        _ => {}
    }
}

/// Whether a line, area or bar's endpoint rail, asked for as `auto`, could not fire on the old
/// grammar whatever the data: a placed legend edge, `layers`, `multiples`, and on a bar a
/// per-series support table, `y_start` or an unstacked `stack`. Data-dependent exits (a negative
/// measure, a sort, a tiny card, a series-less color) are not decided here.
///
/// A bar's `stack` defaults to `none` in every built-in theme; a custom theme that stacks it is
/// not visible in the document.
fn rail_never_fires(doc: &Json, chart: &[String]) -> bool {
    let style = at(chart, &["style"]);
    match cascaded(doc, &style, &["legend", "position", "edge"]).and_then(Json::as_str) {
        Some("endpoints") => return false,
        Some("top" | "right" | "bottom" | "left") => return true,
        _ => {}
    }
    let set = |key: &str| value_at(doc, &at(chart, &[key])).is_some_and(|v| !v.is_null());
    if value_at(doc, &at(chart, &["layers"])).and_then(Json::as_array).is_some_and(|l| !l.is_empty()) || set("multiples") {
        return true;
    }
    if value_at(doc, &at(chart, &["type"])).and_then(Json::as_str) != Some("bar") {
        return false;
    }
    set("y_start") || per_series_support_table(doc, chart) || cascaded(doc, &style, &["stack"]).is_none_or(|v| v.as_str() == Some("none"))
}

fn per_series_support_table(doc: &Json, chart: &[String]) -> bool {
    let table = value_at(doc, &at(chart, &["support_table"]));
    let entries = table.and_then(|t| t.get("entries")).or(table).and_then(Json::as_array);
    entries.is_some_and(|entries| {
        entries.iter().any(|e| {
            e.get("per_series").is_some_and(|v| !v.is_null()) && e.get("by_measure").and_then(coerce) != Some(Visible::On)
        })
    })
}

/// The `key.<leaf>` write for `source`, refused when a hand-mixed document already authors one.
fn key_write(doc: &Json, source: &[String], parent: &[String], leaf: &str, value: Json) -> Result<(Path, Option<Json>), String> {
    let at = at(parent, &["key", leaf]);
    if value_at(doc, &at).is_some_and(|v| !v.is_null()) {
        return Err(format!("Cannot migrate `{}`: `key.{leaf}` is already set beside it. Keep one.", source.join(".")));
    }
    Ok((at, Some(value)))
}

fn type_write(doc: &Json, source: &[String], parent: &[String], name: &str) -> Result<(Path, Option<Json>), String> {
    key_write(doc, source, parent, "type", Json::from(name))
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Scope {
    Rail,
    Pie,
    /// The board-wide `charts` scope.
    Board,
    Other,
}

/// What the `legend` at `source` (a path ending `legend`, then `depth - 1` keys) styles: a chart
/// (its `type:` says), `charts.<family>`, or every family at `charts`.
fn scope(doc: &Json, source: &[String], depth: usize) -> Scope {
    let family = |name: &str| match name {
        "pie" | "donut" => Scope::Pie,
        _ if RAIL_FAMILIES.contains(&name) => Scope::Rail,
        _ => Scope::Other,
    };
    let at_holder = source.len().saturating_sub(depth + 1);
    match source.get(at_holder).map(String::as_str) {
        Some("charts") => Scope::Board,
        Some("style") => value_at(doc, &at(&prefix(source, at_holder), &["type"])).and_then(Json::as_str).map_or(Scope::Other, family),
        Some(name) => family(name),
        None => Scope::Other,
    }
}

/// `legend.visible` as the key's `visible`, and the `legend` type a shown one asks for.
///
/// A hidden legend named nothing only where the family has no other way to name its series: a
/// line, area or bar still printed its rail (`auto` again), a pie ignored it. A rail hidden too
/// names nothing, and so does one that could not fire (`rail_never_fires`): that chart's own
/// key is hidden. A hidden board-wide legend also hides `histogram` and `point_map`; scatter,
/// heatmap and geoshape pinned `visible: true` in their 0.11 base theme, so it never reached them. A shown pie
/// legend is dropped, since the pie keeps `auto`; a shown board-wide one pins `pie` to `auto`.
fn legend_visible(doc: &Json, source: &[String], value: &Json) -> Result<Rewrote, String> {
    let parent = holder(source, 2);
    let hide = Rewrote { value: Some(Json::Bool(false)), ..Rewrote::default() };
    match visible(source, value)? {
        Visible::Auto => Ok(Rewrote::default()),
        Visible::Off => match scope(doc, source, 2) {
            Scope::Other => Ok(hide),
            Scope::Pie => Ok(Rewrote::default()),
            Scope::Rail if effective(doc, &parent, endpoint_visible)?.is_some_and(|(v, _)| v == Visible::Off) => Ok(hide),
            railed => {
                let (scope_key, chart) =
                    parent.split_last().ok_or_else(|| format!("Cannot migrate `{}`: it sits under no scope.", source.join(".")))?;
                if scope_key == "style" {
                    return Ok(if rail_never_fires(doc, chart) { hide } else { Rewrote::default() });
                }
                let families = if railed == Scope::Board { RAIL_FAMILIES.to_vec() } else { vec![scope_key.as_str()] };
                let mut writes = railless_chart_hides(doc, source, &parent, &families)?;
                if railed == Scope::Board {
                    for family in BOARD_HIDDEN_LEGEND_FAMILIES {
                        let own = at(&parent, &[family]);
                        if legend_flag(doc, &own)?.is_none() {
                            writes.push(key_write(doc, source, &own, "visible", Json::Bool(false))?);
                        }
                    }
                }
                Ok(Rewrote { writes, ..Rewrote::default() })
            }
        },
        Visible::On => {
            let scope = scope(doc, source, 2);
            if scope == Scope::Pie {
                return Ok(Rewrote::default());
            }
            let endpoints = endpoint_visible(doc, &parent)?;
            if scope != Scope::Other && (endpoints_edge(doc, &parent) || endpoints == Some(Visible::On)) {
                return Err(format!("Cannot migrate `{}: true`, which {TWICE}", source.join(".")));
            }
            let mut writes = Vec::new();
            if endpoints != Some(Visible::Off) {
                writes.push(type_write(doc, source, &parent, "legend")?);
                if scope == Scope::Board {
                    writes.push(key_write(doc, source, &at(&parent, &["pie"]), "type", Json::from("auto"))?);
                }
            }
            Ok(Rewrote { value: Some(Json::Bool(true)), writes, ..Rewrote::default() })
        }
    }
}

/// `key.visible: false` on each chart whose hidden legend is the one at `parent` and whose rail
/// could not fire.
fn railless_chart_hides(doc: &Json, source: &[String], parent: &[String], families: &[&str]) -> Result<Vec<(Path, Option<Json>)>, String> {
    let mut charts = Vec::new();
    charts_of_type(doc, families, &mut Vec::new(), &mut charts);
    let mut writes = Vec::new();
    for chart in charts {
        let style = at(&chart, &["style"]);
        let hidden_here = effective(doc, &style, legend_flag)?.is_some_and(|(v, holder)| v == Visible::Off && holder == parent);
        let labels_hidden = effective(doc, &style, endpoint_visible)?.is_some_and(|(v, _)| v == Visible::Off);
        if hidden_here && !labels_hidden && rail_never_fires(doc, &chart) {
            writes.push(key_write(doc, source, &style, "visible", Json::Bool(false))?);
        }
    }
    Ok(writes)
}

/// A pie draws no legend of its own: every leaf but its `position` is dropped.
fn pie_legend_leaf(doc: &Json, source: &[String], value: &Json) -> Result<Rewrote, String> {
    if scope(doc, source, 2) != Scope::Pie {
        return Ok(Rewrote { value: Some(value.clone()), ..Rewrote::default() });
    }
    let notice = format!("`{}` no longer applies on a pie, whose key legend keeps only its `position`. Dropped.", source.join("."));
    Ok(Rewrote { notice: Some(notice), ..Rewrote::default() })
}

/// An `endpoints` edge is the `direct` type, anything else stays the legend's edge. Board-wide it
/// is `direct` on the line, area and bar families, not on `charts`, which would reach a pie.
fn legend_edge(doc: &Json, source: &[String], value: &Json) -> Result<Rewrote, String> {
    if value.as_str() != Some("endpoints") {
        return Ok(Rewrote { value: Some(value.clone()), ..Rewrote::default() });
    }
    let parent = holder(source, 3);
    if shown_legend(doc, &parent)? {
        return Err(format!("Cannot migrate `{}: endpoints`, which {TWICE}", source.join(".")));
    }
    if endpoint_visible(doc, &parent)? == Some(Visible::Off) {
        return Err(format!(
            "Cannot migrate `{}: endpoints` beside `endpoint_labels.visible: false`. Set `key.type` to `legend` or `direct`.",
            source.join(".")
        ));
    }
    if scope(doc, source, 3) == Scope::Board {
        // Only a line, area or bar ever drew endpoint labels; a family that sets its own edge or
        // endpoint labels is migrated by its own leaf.
        let mut writes = Vec::new();
        for family in RAIL_FAMILIES {
            let own = at(&parent, &[family]);
            let set = |tail: &[&str]| value_at(doc, &at(&own, tail)).is_some_and(|v| !v.is_null());
            if !set(&["legend", "position", "edge"]) && !set(&ENDPOINT_VISIBLE) {
                if shown_legend(doc, &own)? {
                    return Err(format!("Cannot migrate `{}: endpoints`, whose `{family}` {TWICE}", source.join(".")));
                }
                writes.push(type_write(doc, source, &own, "direct")?);
            }
        }
        return Ok(Rewrote { writes, ..Rewrote::default() });
    }
    let direct = (endpoint_visible(doc, &parent)? != Some(Visible::On)).then(|| type_write(doc, source, &parent, "direct")).transpose()?;
    Ok(Rewrote { writes: direct.into_iter().collect(), ..Rewrote::default() })
}

/// `endpoint_labels.visible` as the key's type: `true` names series on the chart, `false` in a
/// legend, unless the legend is hidden too (`legend_visible` hides the key).
fn endpoint_type(doc: &Json, source: &[String], value: &Json) -> Result<Rewrote, String> {
    let parent = holder(source, 2);
    let named = |name: &str| type_write(doc, source, &parent, name).map(|_| Rewrote { value: Some(Json::from(name)), ..Rewrote::default() });
    match visible(source, value)? {
        Visible::On => named("direct"),
        Visible::Off => match effective(doc, &parent, legend_flag)? {
            Some((Visible::Off, holder)) if holder == parent => Ok(Rewrote::default()),
            Some((Visible::Off, _)) => {
                Ok(Rewrote { writes: vec![key_write(doc, source, &parent, "visible", Json::Bool(false))?], ..Rewrote::default() })
            }
            _ => named("legend"),
        },
        Visible::Auto => Ok(Rewrote::default()),
    }
}

fn key_moves(g: &Grammars) -> Vec<Move> {
    let rewritten = |renames: &[(&[&str], &[&str])], rewrite: DocumentRewrite| {
        g.suffix_rename_moves(renames, None, None)
            .into_iter()
            .map(|mut m| {
                m.rewrite_in_document = Some(rewrite.clone());
                m
            })
            .collect::<Vec<_>>()
    };
    // Leaves before the containers that hold them, so each finds its container still whole.
    let mut moves = rewritten(&[(&["key", "visible"], &["legend", "visible"])], Arc::new(legend_visible));
    moves.extend(rewritten(&[(&["key", "legend", "position", "edge"], &["legend", "position", "edge"])], Arc::new(legend_edge)));
    moves.extend(rewritten(&[(&["key", "type"], &ENDPOINT_VISIBLE)], Arc::new(endpoint_type)));
    for leaf in PIE_LEGEND_LEAVES {
        moves.extend(rewritten(&[(&["key", "legend", leaf], &["legend", leaf])], Arc::new(pie_legend_leaf)));
    }
    moves.extend(g.suffix_rename_moves(
        &[
            (&["key", "direct", "offset"], &["endpoint_labels", "label_offset"]),
            (&["layers", "*", "key"], &["layers", "*", "legend"]),
        ],
        None,
        None,
    ));
    moves.extend(g.suffix_rename_moves(&[(&["key", "direct"], &["series_label"])], None, None));
    // Only a bar has a label strip to size; a line or area's `height` is struck.
    moves.extend(g.suffix_rename_moves(
        &[(&["style", "key", "direct", "height"], &["style", "endpoint_labels", "height"])],
        None,
        Some("bar"),
    ));
    moves.extend(g.suffix_rename_moves(
        &[(&["charts", "bar", "key", "direct", "height"], &["charts", "bar", "endpoint_labels", "height"])],
        None,
        None,
    ));
    let legend = g.suffix_rename_moves(&[(&["key", "legend"], &["legend"])], None, None).into_iter().map(|mut m| {
        m.merge = true;
        m
    });
    moves.extend(legend);
    moves
}

fn tail<'a>(head: &[&'a str], rest: &[&'a str]) -> Vec<&'a str> {
    [head, rest].concat()
}

fn static_color_moves(g: &Grammars) -> Vec<Move> {
    let rename = |new: &[&str], old: &[&str], chart_type: Option<&str>| {
        g.suffix_rename_moves(&[(new, old)], None, chart_type)
    };
    let mut moves = Vec::new();
    for (chart_type, paint) in STATIC_COLOR_MARK_PAINT {
        moves.extend(rename(
            &tail(&["style", "marks"], paint),
            &tail(&["style"], &STATIC_COLOR),
            Some(chart_type),
        ));
    }
    for family in STATIC_COLOR_THEME_FAMILIES {
        let Some((_, paint)) = STATIC_COLOR_MARK_PAINT.iter().find(|(t, _)| *t == family) else {
            continue;
        };
        moves.extend(rename(
            &tail(&["charts", family, "marks"], paint),
            &tail(&["charts", family], &STATIC_COLOR),
            None,
        ));
    }
    moves.extend(rename(
        &tail(&["style"], &TABLE_INK),
        &tail(&["style"], &STATIC_COLOR),
        Some("table"),
    ));
    moves.extend(rename(
        &tail(&["charts", "table"], &TABLE_INK),
        &tail(&["charts", "table"], &STATIC_COLOR),
        None,
    ));
    let value_map = Arc::new(retired_scaffold_token_map());
    for m in &mut moves {
        let map = value_map.clone();
        m.rewrite = Some(Arc::new(move |v| respell_secondary_ink(&map, v)));
    }
    moves
}

/// `{col: {when: [rule]}}` as `{col: {conditional: [rule]}}`; a rule keeps its keys.
/// A value that is not that shape is left for the grammar to refuse.
fn table_conditional_rules(value: &Json) -> Rewritten {
    let Json::Object(columns) = value else {
        return Rewritten::Value(value.clone());
    };
    let mut moved = serde_json::Map::new();
    for (column, entry) in columns {
        let Some(when) = entry.get("when").filter(|w| w.is_array()) else {
            return Rewritten::Value(value.clone());
        };
        moved.insert(column.clone(), serde_json::json!({ "conditional": when }));
    }
    Rewritten::Value(Json::Object(moved))
}

fn table_rule_moves(g: &Grammars) -> Vec<Move> {
    g.suffix_rename_moves(
        &[(&["style", "columns"], &["conditional_formatting"])],
        None,
        Some("table"),
    )
    .into_iter()
    .map(|mut m| {
        m.rewrite = Some(Arc::new(table_conditional_rules));
        m.merge = true;
        m
    })
    .collect()
}

const RULE_PREDICATES: [&str; 10] = [
    "eq", "ne", "lt", "lte", "gt", "gte", "between", "in", "is_null", "default",
];

/// A KPI's `{col: {when: [rule]}}` as `{col: [rule]}`, each rule's paint under the row it styles.
///
/// A rule on the `support.value` column styles the support row; a rule on any other column styles
/// the headline. Paint the old grammar never drew (a headline rule's `font.weight`) is dropped. A
/// support rule's `tone` moves to `support.tone`; a glyph, or a headline tone, has no successor in
/// a rule, so it refuses. A value that is not that shape is left for the grammar to refuse.
fn kpi_conditional_rules(doc: &Json, source: &[String], value: &Json) -> Result<Rewrote, String> {
    let kept = || Ok(Rewrote { value: Some(value.clone()), ..Rewrote::default() });
    let Json::Object(columns) = value else {
        return kept();
    };
    let chart = holder(source, 1);
    let chart_value = value_at(doc, &chart).and_then(Json::as_object);
    let field = |key: &str| chart_value.and_then(|c| c.get(key));
    let support_column = field("support")
        .and_then(|s| s.get("value"))
        .filter(|support| Some(*support) != field("value"))
        .and_then(Json::as_str);
    let mut moved = serde_json::Map::new();
    for (column, entry) in columns {
        let Some(when) = entry.get("when").and_then(Json::as_array) else {
            return kept();
        };
        let row = if Some(column.as_str()) == support_column { "support" } else { "value" };
        let mut rules = Vec::new();
        for rule in when {
            let Json::Object(rule) = rule else {
                return kept();
            };
            if rule.contains_key("glyph") || rule.contains_key("glyph_color") {
                return Err(format!(
                    "conditional_formatting.{column} sets glyph or glyph_color, which a style.conditional rule cannot. Draw the glyph from the sign with `good` (`support.good` for the support row)."
                ));
            }
            if rule.contains_key("tone") && row != "support" {
                return Err(format!(
                    "conditional_formatting.{column} sets tone on the headline, which a style.conditional rule cannot. Color the headline from the sign with `good`."
                ));
            }
            let mut moved_rule: serde_json::Map<String, Json> = rule
                .iter()
                .filter(|(k, _)| RULE_PREDICATES.contains(&k.as_str()))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            let predicates = moved_rule.len();
            if let Some(background) = rule.get("background") {
                moved_rule.insert("background".into(), background.clone());
            }
            let mut row_style = serde_json::Map::new();
            if let Some(color) = rule.get("font").and_then(|f| f.get("color")) {
                row_style.insert("font".into(), serde_json::json!({ "color": color }));
            }
            if let Some(tone) = rule.get("tone") {
                row_style.insert("tone".into(), tone.clone());
            }
            if !row_style.is_empty() {
                moved_rule.insert(row.into(), Json::Object(row_style));
            }
            if moved_rule.len() > predicates {
                rules.push(Json::Object(moved_rule));
            }
        }
        if !rules.is_empty() {
            moved.insert(column.clone(), Json::Array(rules));
        }
    }
    Ok(Rewrote {
        value: (!moved.is_empty()).then(|| Json::Object(moved)),
        ..Rewrote::default()
    })
}

fn kpi_moves(g: &Grammars) -> Vec<Move> {
    let conditional = g
        .suffix_rename_moves(&[(&["style", "conditional"], &["conditional_formatting"])], None, Some("kpi"))
        .into_iter()
        .map(|mut m| {
            m.rewrite_in_document = Some(Arc::new(kpi_conditional_rules));
            m.conflicts_with = vec![path(&["style", "conditional"])];
            m
        });
    let background = g
        .suffix_rename_moves(&[(&["style", "background"], &["background"])], None, Some("kpi"))
        .into_iter()
        .map(|mut m| {
            m.conflicts_with = vec![path(&["style", "background"])];
            m
        });
    conditional.chain(background).collect()
}

pub fn declarations(g: &Grammars) -> Declarations {
    let tabs = path(&["tabs", "items", "*"]);
    let moves = key_moves(g)
        .into_iter()
        .chain(table_rule_moves(g))
        .chain(kpi_moves(g))
        .chain(static_color_moves(g))
        .map(|mut m| {
            // A tab item is its own model, not a board, so the board nesting
            // walk never steps into one; following it as a prefix reaches tabs
            // nested to any depth.
            let mut prefixes = (*m.sub_board_prefixes).clone();
            prefixes.push(tabs.clone());
            m.sub_board_prefixes = Arc::new(prefixes);
            m
        })
        .collect();
    let deletion = |parts: &[&str], reason: &str, chart_type: Option<&str>| {
        let mut d = Deletion::new(g.source, g.target, parts);
        d.reason = Some(reason.into());
        d.chart_type = chart_type.map(str::to_string);
        d
    };
    let mut deletions = vec![deletion(&["style", "title", "font", "size"], TITLE_SIZE_REASON, None)];
    for family in ["line", "area"] {
        deletions.push(deletion(&["style", "charts", family, "endpoint_labels", "height"], STRIP_REASON, None));
        deletions.push(deletion(&["style", "endpoint_labels", "height"], STRIP_REASON, Some(family)));
    }
    deletions.push(deletion(&["options", "label_column"], LABEL_COLUMN_REASON, None));
    Declarations {
        moves,
        deletions,
        ..Declarations::default()
    }
}
