//! The 0.9.0 -> 0.10.0 boundary.
//!
//! Which fields accept a `null` is read from the frozen 0.10.0 grammar, never a model.
//!
//! Reach: a board nested in a board is opaque to the field walk past the first
//! level, so the heading-ramp moves compose the one-level steps up to
//! [`BOARD_NESTING_DEPTH`]; past it a ramp stays and the board fails to parse.

use std::collections::{BTreeSet, HashMap};
use std::sync::Arc;

use serde_json::{Map, Value as Json};

use crate::decl::{
    path, Declarations, Deletion, Expansion, Grammars, KeyPath, Mapped, Move, Rewrite, Rewritten,
};
use crate::fields::Fields;
use crate::pyrepr::{py_repr, repr_json};
use crate::schema::{without_composition, Pos, Schema};

const BOARD_NESTING_DEPTH: usize = 3;
const BOARD_STEP_SEGMENTS: &[&str] = &["rows", "cols", "grid", "items", "item", "tabs", "*"];

const RAMP_TAIL: &[&str] = &["style", "title", "sizes"];
const RAMP_DELETION_REASON: &str = "The H1-H6 heading ramp is one board-wide font, so a chart-level title.sizes was dropped. Set style.fonts.heading.sizes to resize headings.";

const MEASURE_FAMILIES: &[&str] = &["bar", "line", "area", "histogram"];
const THEME_FAMILIES: &[&str] = &["bar", "line", "area", "histogram", "scatter", "heatmap"];
const FORMAT_KEY_DELETION_REASON: &str = "{key} was never read here, so it formatted nothing. Set a slot's labels.format (style.axis_y.labels.format) or formats: on the query.";
const HEATMAP_FORMAT_REASON: &str =
    "A heatmap's axes are both grid dimensions, so {key} never applied. Set formats: on the query.";

const KPI_LINE_HEIGHT_NOTE: &str =
    "line_height has no effect on KPI text slots (each renders a single line); removed.";
const KPI_NO_TITLE: &str = "A KPI has no title: its overflow moved to label.overflow, and any other title setting was dropped. Style the label under label.";
const KPI_CHARACTER: &str = "character only drew on the KPI glyph slot; it was ignored here.";
const STYLE_NULL_NOTE: &str = "style null is accepted only where it means no value (no link, solid line, no stroke). Elsewhere it was dropped; where it had cleared a value from the theme or a parent, write the value you want instead.";
const COLUMN_DEFAULTS_LABEL_REASON: &str = "One header text for every column has no use. Set label on each column under style.columns instead.";

/// A null here left the choice to the engine, as `auto` does.
const NULL_IS_AUTO: &[&str] = &["legend", "title", "visible"];

type Tail = &'static [&'static str];
type Renames = &'static [(Tail, Tail)];

const KPI_RENAMES: Renames = &[
    (&["style", "variant"], &["variant"]),
    (&["style", "support", "format"], &["support", "format"]),
    (&["style", "support", "tone"], &["support", "tone"]),
    (&["glyph"], &["style", "glyph", "character"]),
    (
        &["charts", "kpi", "label", "overflow"],
        &["charts", "kpi", "title", "overflow"],
    ),
];
const KPI_LABEL_OVERFLOW_RENAME: Renames = &[(
    &["style", "label", "overflow"],
    &["style", "title", "overflow"],
)];
const KPI_FONT_RENAMES: Renames = &[
    (
        &["style", "value", "glyph", "font"],
        &["style", "glyph", "font"],
    ),
    (
        &["style", "value", "affix", "font"],
        &["style", "affix", "font"],
    ),
    (
        &["charts", "kpi", "value", "glyph", "font"],
        &["charts", "kpi", "glyph", "font"],
    ),
    (
        &["charts", "kpi", "value", "affix", "font"],
        &["charts", "kpi", "affix", "font"],
    ),
];

/// (tail, reason, chart_type)
const KPI_DELETIONS: &[(Tail, Option<&str>, Option<&str>)] = &[
    (
        &["charts", "kpi", "glyph", "character"],
        Some("A glyph is per-chart content: set glyph on each KPI."),
        None,
    ),
    (&["charts", "kpi", "title"], Some(KPI_NO_TITLE), None),
    (
        &["charts", "kpi", "label", "character"],
        Some(KPI_CHARACTER),
        None,
    ),
    (
        &["charts", "kpi", "affix", "character"],
        Some(KPI_CHARACTER),
        None,
    ),
    (
        &["charts", "kpi", "font", "line_height"],
        Some(KPI_LINE_HEIGHT_NOTE),
        None,
    ),
    (
        &["charts", "kpi", "value", "font", "line_height"],
        Some(KPI_LINE_HEIGHT_NOTE),
        None,
    ),
    (
        &["charts", "kpi", "label", "font", "line_height"],
        Some(KPI_LINE_HEIGHT_NOTE),
        None,
    ),
    (&["style", "title"], Some(KPI_NO_TITLE), Some("kpi")),
    (
        &["style", "label", "character"],
        Some(KPI_CHARACTER),
        Some("kpi"),
    ),
    (
        &["style", "affix", "character"],
        Some(KPI_CHARACTER),
        Some("kpi"),
    ),
    (
        &["style", "font", "line_height"],
        Some(KPI_LINE_HEIGHT_NOTE),
        Some("kpi"),
    ),
    (
        &["style", "value", "font", "line_height"],
        Some(KPI_LINE_HEIGHT_NOTE),
        Some("kpi"),
    ),
    (
        &["style", "label", "font", "line_height"],
        Some(KPI_LINE_HEIGHT_NOTE),
        Some("kpi"),
    ),
    // What the font and character moves leave behind.
    (&["charts", "kpi", "glyph"], None, None),
    (&["charts", "kpi", "affix"], None, None),
    (&["style", "glyph"], None, Some("kpi")),
    (&["style", "affix"], None, Some("kpi")),
];

const COLUMN_DEFAULTS_LABEL_TAILS: &[(Tail, Option<&str>)] = &[
    (&["style", "column_defaults", "label"], Some("table")),
    (
        &["style", "charts", "table", "column_defaults", "label"],
        None,
    ),
];

fn legend_position_expansion() -> Expansion {
    let edge = |e: &'static str, overlay: bool| {
        vec![
            ("edge", Mapped::Str(e.into())),
            ("overlay", Mapped::Bool(overlay)),
        ]
    };
    let corner = |e: &'static str, a: &'static str| {
        vec![
            ("edge", Mapped::Str(e.into())),
            ("align", Mapped::Str(a.into())),
            ("overlay", Mapped::Bool(true)),
        ]
    };
    HashMap::from([
        ("left".to_string(), edge("left", false)),
        ("right".to_string(), edge("right", false)),
        ("top".to_string(), edge("top", false)),
        ("bottom".to_string(), edge("bottom", false)),
        ("top-left".to_string(), corner("top", "start")),
        ("top-right".to_string(), corner("top", "end")),
        ("bottom-left".to_string(), corner("bottom", "start")),
        ("bottom-right".to_string(), corner("bottom", "end")),
    ])
}

const LEGEND_OVERLAY_NOTE: &str = "A pie legend corner (`top-left`, `top-right`, `bottom-left`, `bottom-right`) floated over the wedges. A pie key never overlays, so the corner became its edge and alignment, and the overlay was dropped.";

// ---- rewrites ----

fn refuse<T>(message: String) -> Result<T, Rewritten> {
    Err(Rewritten::Refuse(message))
}

fn manual(what: &str, value: &Json) -> String {
    format!(
        "Cannot migrate {what}: expected a mapping, got {}. Migrate this field manually.",
        repr_json(value)
    )
}

fn line(value: &Json, key: &str) -> Result<Map<String, Json>, Rewritten> {
    match value {
        Json::Object(m) => Ok(m.clone()),
        other => refuse(manual(&py_repr(key), other)),
    }
}

/// A JSON number; bools are not numbers.
fn number(value: &Json) -> Option<f64> {
    match value {
        Json::Number(n) => n.as_f64(),
        _ => None,
    }
}

fn dasharray(style: &Json) -> Result<Option<&'static str>, Rewritten> {
    match style.as_str() {
        Some("solid") => Ok(None),
        Some("dashed") => Ok(Some("6 3")),
        Some("dotted") => Ok(Some("2 2")),
        _ => refuse(format!(
            "Cannot migrate a rule style of {}. Migrate this field manually.",
            repr_json(style)
        )),
    }
}

/// Runs a fallible rewrite as the engine's infallible-looking `Rewrite`.
fn rewriting(f: fn(&Json) -> Result<Rewritten, Rewritten>) -> Rewrite {
    Arc::new(move |v| f(v).unwrap_or_else(|refusal| refusal))
}

/// A `rule` whose presence drew the line gains `visible: true`; null merged as
/// "inherit", so it said nothing and is dropped.
fn present_means_on(value: &Json) -> Result<Rewritten, Rewritten> {
    if value.is_null() {
        return Ok(Rewritten::Drop);
    }
    let mut rule = line(value, "rule")?;
    if let Some(width) = rule.shift_remove("stroke_width") {
        rule.insert("width".into(), width);
    }
    if let Some(style) = rule.shift_remove("style") {
        if let Some(dashes) = dasharray(&style)? {
            rule.insert("dasharray".into(), dashes.into());
        }
    }
    let mut out = Map::new();
    out.insert("visible".into(), Json::Bool(true));
    out.extend(rule);
    Ok(Rewritten::Value(Json::Object(out)))
}

/// A `rule` that drew whenever its width was above 0: the width becomes
/// `visible`, and a 0 is no width at all.
fn width_means_visibility(value: &Json) -> Result<Rewritten, Rewritten> {
    if value.is_null() {
        return Ok(Rewritten::Drop);
    }
    let mut rule = line(value, "rule")?;
    let width = match rule.get("width") {
        None | Some(Json::Null) => return Ok(Rewritten::Value(Json::Object(rule))),
        Some(_) if rule.contains_key("visible") => return Ok(Rewritten::Value(Json::Object(rule))),
        Some(w) => w.clone(),
    };
    let Some(w) = number(&width) else {
        return refuse(format!(
            "Cannot migrate a rule width of {}. Migrate this field manually.",
            repr_json(&width)
        ));
    };
    if w > 0.0 {
        rule.insert("visible".into(), Json::Bool(true));
    } else {
        rule.shift_remove("width");
        rule.insert("visible".into(), Json::Bool(false));
    }
    Ok(Rewritten::Value(Json::Object(rule)))
}

/// `width_means_visibility` for a support table, whose `continuous` was never read.
fn support_width_means_visibility(value: &Json) -> Result<Rewritten, Rewritten> {
    match value {
        Json::Object(m) => {
            let mut m = m.clone();
            m.shift_remove("continuous");
            width_means_visibility(&Json::Object(m))
        }
        other => width_means_visibility(other),
    }
}

fn without_line_height(value: &Json) -> Result<Rewritten, Rewritten> {
    match value {
        Json::Object(m) if m.contains_key("line_height") => {
            let mut m = m.clone();
            m.shift_remove("line_height");
            Ok(Rewritten::Value(Json::Object(m)))
        }
        other => Ok(Rewritten::Value(other.clone())),
    }
}

/// The mapping at `key`, created when absent.
fn block<'a>(
    parent: &'a mut Map<String, Json>,
    key: &str,
) -> Result<&'a mut Map<String, Json>, Rewritten> {
    match parent
        .entry(key)
        .or_insert_with(|| Json::Object(Map::new()))
    {
        Json::Object(m) => Ok(m),
        other => refuse(manual(&py_repr(key), other)),
    }
}

fn width_of(value: &Json) -> Result<f64, Rewritten> {
    number(value).map_or_else(
        || {
            refuse(format!(
                "Cannot migrate rule width {}. Migrate this field manually.",
                repr_json(value)
            ))
        },
        Ok,
    )
}

fn role_widths(roles: &mut Map<String, Json>, row_rule: Option<&Json>) -> Result<(), Rewritten> {
    if let Some(Json::Object(total)) = roles.get_mut("total") {
        total.shift_remove("rule_width");
    }
    let Some(Json::Object(summary)) = roles.get_mut("summary") else {
        return Ok(());
    };
    let Some(width) = summary.shift_remove("rule_width") else {
        return Ok(());
    };
    if width.is_null() {
        return Ok(());
    }
    // The shipped themes give body rows no line, so an unauthored width is 0.
    let row_width = match row_rule
        .and_then(|r| r.as_object())
        .and_then(|r| r.get("width"))
    {
        Some(w) => {
            width_of(w)?;
            w.clone()
        }
        None => Json::from(0),
    };
    let effective = if width_of(&width)? != 0.0 {
        width
    } else {
        row_width
    };
    let mut divider = Map::new();
    if width_of(&effective)? > 0.0 {
        divider.insert("visible".into(), Json::Bool(true));
        divider.insert("width".into(), effective);
    } else {
        divider.insert("visible".into(), Json::Bool(false));
    }
    summary.insert("divider".into(), Json::Object(divider.clone()));
    match roles.get_mut("total") {
        None | Some(Json::Null) => {
            let mut total = Map::new();
            total.insert("divider".into(), Json::Object(divider));
            roles.insert("total".into(), Json::Object(total));
        }
        Some(Json::Object(total)) => {
            total
                .entry("divider")
                .or_insert_with(|| Json::Object(divider));
        }
        Some(_) => {}
    }
    Ok(())
}

/// Writes the color and width the old table lines shared to each divider,
/// resolved per style block.
fn table_dividers(value: &Json) -> Result<Rewritten, Rewritten> {
    let Json::Object(original) = value else {
        return Ok(Rewritten::Value(value.clone()));
    };
    let mut out = original.clone();
    let row_rule = out.get("row").and_then(|r| r.get("rule")).cloned();
    let table_rule = out.shift_remove("rule");
    let mut color = table_rule
        .as_ref()
        .and_then(|r| r.as_object())
        .and_then(|r| r.get("color"))
        .filter(|c| !c.is_null())
        .cloned();
    if let Some(c) = row_rule
        .as_ref()
        .and_then(|r| r.as_object())
        .and_then(|r| r.get("color"))
        .filter(|c| !c.is_null())
    {
        color = Some(c.clone());
    }
    if let Some(Json::Object(roles)) = out.get_mut("row").and_then(|r| r.get_mut("roles")) {
        role_widths(roles, row_rule.as_ref())?;
    }
    let Some(color) = color else {
        if let Some(Json::Object(rule)) = out.get_mut("header").and_then(|h| h.get_mut("rule")) {
            rule.shift_remove("color");
        }
        return Ok(Rewritten::Value(Json::Object(out)));
    };
    block(block(&mut out, "row")?, "rule")?.insert("color".into(), color.clone());
    block(block(&mut out, "header")?, "rule")?.insert("color".into(), color.clone());
    let roles = block(block(&mut out, "row")?, "roles")?;
    for role in ["summary", "total"] {
        block(block(roles, role)?, "divider")?
            .entry("color")
            .or_insert_with(|| color.clone());
    }
    Ok(Rewritten::Value(Json::Object(out)))
}

// ---- null rewrite, read from the frozen grammar ----

fn node_accepts_null(node: &Json) -> bool {
    match node.get("type") {
        Some(Json::String(t)) if t == "null" => return true,
        Some(Json::Array(ts)) if ts.iter().any(|t| t.as_str() == Some("null")) => return true,
        _ => {}
    }
    node.get("const").is_some_and(Json::is_null)
        || node
            .get("enum")
            .and_then(Json::as_array)
            .is_some_and(|e| e.iter().any(Json::is_null))
}

fn accepts_null(schema: &'static Schema, field: Pos) -> bool {
    schema.branches(field).iter().any(|b| node_accepts_null(b))
}

/// A `dict[str, X]` field: a typed or free-form map and no declared properties.
fn is_open_map(schema: &'static Schema, field: Pos) -> bool {
    schema.branches(field).iter().any(|b| {
        let free = matches!(b.get("additionalProperties"), Some(a) if a.is_object() || a == &Json::Bool(true));
        let declared = b
            .get("properties")
            .and_then(Json::as_object)
            .is_some_and(|p| !p.is_empty());
        free && !declared
    })
}

/// Drops each `null` that is not a value; a legend title one becomes `auto`.
/// `owners` are the style models a mapping at this position can be (one per
/// chart `type:` at a chart position); a `null` stays when any of them accepts it.
fn rewrite_nulls(
    fields: &mut Fields,
    schema: &'static Schema,
    node: &Json,
    owners: &[Pos],
    at: &mut Vec<String>,
) -> Json {
    let Json::Object(map) = node else {
        return node.clone();
    };
    let mut out = Map::new();
    for (key, child) in map {
        let declared: Vec<Pos> = owners
            .iter()
            .filter_map(|o| o.get("properties")?.get(key))
            .filter(|f| f.is_object())
            .collect();
        at.push(key.clone());
        if declared.is_empty() {
            out.insert(key.clone(), child.clone());
        } else if child.is_null() {
            if declared.iter().any(|f| accepts_null(schema, f)) {
                out.insert(key.clone(), Json::Null);
            } else if at.ends_with(&path(NULL_IS_AUTO)) {
                out.insert(key.clone(), "auto".into());
            }
        } else {
            let mut nested: Vec<Pos> = Vec::new();
            for f in &declared {
                for m in fields.models_in(f) {
                    if !nested.iter().any(|n| std::ptr::eq(*n, m)) {
                        nested.push(m);
                    }
                }
            }
            let open = declared.iter().any(|f| is_open_map(schema, f));
            let rewritten = match child {
                Json::Object(entries) if open => {
                    at.push("*".into());
                    let rebuilt = entries
                        .iter()
                        .map(|(entry, value)| {
                            (
                                entry.clone(),
                                rewrite_nulls(fields, schema, value, &nested, at),
                            )
                        })
                        .collect();
                    at.pop();
                    Json::Object(rebuilt)
                }
                _ => rewrite_nulls(fields, schema, child, &nested, at),
            };
            out.insert(key.clone(), rewritten);
        }
        at.pop();
    }
    Json::Object(out)
}

fn null_rewrite(schema: &'static Schema, model: Pos) -> Rewrite {
    Arc::new(move |value| {
        Rewritten::Value(rewrite_nulls(
            &mut Fields::new(schema),
            schema,
            value,
            &[model],
            &mut Vec::new(),
        ))
    })
}

// ---- moves ----

/// Paths of every board-owned `style` block, root included.
fn board_style_paths(g: &Grammars) -> Vec<KeyPath> {
    let tails = BTreeSet::from([path(&["style"])]);
    let prefixes: BTreeSet<KeyPath> = Fields::new(g.target_schema)
        .paths(&tails)
        .into_iter()
        .filter_map(|p| {
            let (last, prefix) = p.split_last()?;
            let steps = prefix
                .iter()
                .all(|s| BOARD_STEP_SEGMENTS.contains(&s.as_str()));
            (last == "style" && !prefix.is_empty() && steps).then(|| prefix.to_vec())
        })
        .collect();
    // A step that is two others end to end adds nothing once steps compose.
    let steps: Vec<&KeyPath> = prefixes
        .iter()
        .filter(|p| {
            !prefixes.iter().any(|q| {
                p.starts_with(q) && p.get(q.len()..).is_some_and(|rest| prefixes.contains(rest))
            })
        })
        .collect();
    let mut levels: Vec<KeyPath> = vec![Vec::new()];
    let mut frontier: Vec<KeyPath> = vec![Vec::new()];
    for _ in 0..BOARD_NESTING_DEPTH {
        frontier = frontier
            .iter()
            .flat_map(|base| steps.iter().map(move |s| [&base[..], &s[..]].concat()))
            .collect();
        for level in &frontier {
            if !levels.contains(level) {
                levels.push(level.clone());
            }
        }
    }
    levels
        .into_iter()
        .map(|mut l| {
            l.push("style".into());
            l
        })
        .collect()
}

fn heading_ramp_moves(g: &Grammars) -> Vec<Move> {
    board_style_paths(g)
        .into_iter()
        .filter_map(|base| {
            let old = [&base[..], &path(&["title", "sizes"])[..]].concat();
            let new = [&base[..], &path(&["fonts", "heading", "sizes"])[..]].concat();
            (g.source_schema.path_exists(&old, false) && g.target_schema.path_exists(&new, true))
                .then(|| Move::new(g.source, g.target, old, new))
        })
        .collect()
}

fn with_rewrite(moves: Vec<Move>, rewrite: Option<Rewrite>) -> Vec<Move> {
    moves
        .into_iter()
        .map(|mut m| {
            m.rewrite.clone_from(&rewrite);
            m
        })
        .collect()
}

fn divider_moves(g: &Grammars) -> Vec<Move> {
    let renamed = |renames: Renames, rewrite: Option<Rewrite>| {
        with_rewrite(g.suffix_rename_moves(renames, None, None), rewrite)
    };
    let mut out = renamed(
        &[
            (&["charts", "table"], &["charts", "table"]),
            (&["style"], &["style"]),
        ],
        Some(rewriting(table_dividers)),
    );
    out.extend(renamed(
        &[
            (&["text", "column", "divider"], &["text", "column", "rule"]),
            (&["footer", "divider"], &["footer", "rule"]),
        ],
        Some(rewriting(present_means_on)),
    ));
    out.extend(renamed(&[(&["text", "divider"], &["text", "rule"])], None));
    out.extend(renamed(
        &[
            (
                &["table", "header", "divider"],
                &["table", "header", "rule"],
            ),
            (&["table", "row", "divider"], &["table", "row", "rule"]),
            (
                &["style", "header", "divider"],
                &["style", "header", "rule"],
            ),
            (&["style", "row", "divider"], &["style", "row", "rule"]),
        ],
        Some(rewriting(width_means_visibility)),
    ));
    out.extend(renamed(
        &[
            (
                &["support_table", "row", "divider"],
                &["support_table", "row", "rule"],
            ),
            (&["support_table", "divider"], &["support_table", "divider"]),
        ],
        Some(rewriting(support_width_means_visibility)),
    ));
    out
}

fn kpi_moves(g: &Grammars) -> Vec<Move> {
    let mut out = g.suffix_rename_moves(KPI_RENAMES, None, None);
    out.extend(g.suffix_rename_moves(KPI_LABEL_OVERFLOW_RENAME, None, Some("kpi")));
    for mut m in g.suffix_rename_moves(KPI_FONT_RENAMES, None, None) {
        m.rewrite = Some(rewriting(without_line_height));
        m.rewrite_note = Some(KPI_LINE_HEIGHT_NOTE);
        out.push(m);
    }
    out
}

fn chart_format_moves(g: &Grammars) -> Vec<Move> {
    let scoped = |old: &str, axis: &str, families: &[&str], conflicts: &[KeyPath]| {
        let new_tail = ["style", axis, "labels", "format"];
        let old_tail = ["style", old];
        let found = g.suffix_rename_moves(&[(&new_tail, &old_tail)], None, None);
        let mut out = Vec::new();
        for m in found {
            for family in families {
                let mut m = m.clone();
                m.chart_type = Some((*family).to_string());
                m.conflicts_with = conflicts.to_vec();
                out.push(m);
            }
        }
        out
    };
    let families: Vec<&str> = MEASURE_FAMILIES
        .iter()
        .copied()
        .chain(["scatter"])
        .collect();
    let competitors = [
        path(&["axis", "labels", "format"]),
        path(&["axis_quantitative", "labels", "format"]),
    ];
    let mut out = scoped("number_format", "axis_y", &families, &competitors);
    out.extend(scoped("time_format", "axis_x", &families, &[]));
    out
}

fn style_null_moves(g: &Grammars) -> Vec<Move> {
    let owners = Fields::new(g.target_schema).style_owners();
    let mut out = Vec::new();
    for m in g.suffix_rename_moves(&[(&["style"], &["style"])], None, None) {
        let Some((_, models)) = owners.iter().find(|(p, _)| *p == m.new_path) else {
            continue;
        };
        for (chart_type, model) in models {
            let mut m = m.clone();
            m.chart_type = Some(chart_type.clone());
            m.rewrite = Some(null_rewrite(g.target_schema, model));
            m.rewrite_note = Some(STYLE_NULL_NOTE);
            out.push(m);
        }
    }
    out
}

fn auto_link_moves(g: &Grammars) -> Vec<Move> {
    g.suffix_rename_moves(&[(&["links", "auto"], &["auto_link"])], None, None)
        .into_iter()
        .map(|mut m| {
            m.conflicts_with = vec![path(&["links", "auto"])];
            m
        })
        .collect()
}

fn legend_position_moves(g: &Grammars) -> Vec<Move> {
    let expansion = Arc::new(legend_position_expansion());
    let notes = Arc::new(HashMap::from([("overlay", LEGEND_OVERLAY_NOTE)]));
    g.suffix_rename_moves(
        &[(&["legend", "position"], &["legend", "position"])],
        None,
        None,
    )
    .into_iter()
    .map(|mut m| {
        m.expansion = Some(expansion.clone());
        m.drop_notes = notes.clone();
        m
    })
    .collect()
}

// ---- deletions ----

/// Chart `type:` literals whose own `style.title` declares `sizes`.
fn chart_types_with_title_ramp(schema: &'static Schema) -> BTreeSet<String> {
    let mut types = BTreeSet::new();
    let defs = schema
        .root()
        .get("$defs")
        .and_then(Json::as_object)
        .into_iter()
        .flatten();
    for variant in defs.map(|(_, v)| v) {
        let literals = variant
            .get("properties")
            .and_then(|p| p.get("type"))
            .and_then(|t| t.get("enum"))
            .and_then(Json::as_array);
        let (Some(literals), Some(own)) = (literals, without_composition(variant)) else {
            continue;
        };
        if schema.declares_tail(&[own], &path(RAMP_TAIL)) {
            types.extend(literals.iter().filter_map(Json::as_str).map(str::to_string));
        }
    }
    types
}

/// `style.charts` families whose `title` declares `sizes`.
fn chart_families_with_title_ramp(schema: &'static Schema) -> Vec<String> {
    let mut charts = vec![schema.root()];
    for key in ["style", "charts"] {
        charts = schema.child_positions(&charts, key);
    }
    schema
        .declared_names(&charts)
        .into_iter()
        .filter(|family| {
            schema.declares_tail(
                &schema.child_positions(&charts, family),
                &path(&["title", "sizes"]),
            )
        })
        .collect()
}

fn deletion_at(g: &Grammars, tail: KeyPath) -> Deletion {
    let mut d = Deletion::new(g.source, g.target, &[]);
    d.path = tail;
    d
}

fn deletions(g: &Grammars) -> Vec<Deletion> {
    let source = g.source_schema;
    let mut out = Vec::new();
    let prefixes = std::iter::once(path(&["style", "charts"])).chain(
        chart_families_with_title_ramp(source)
            .into_iter()
            .map(|f| path(&["style", "charts", &f])),
    );
    for prefix in prefixes {
        let tail = [&prefix[..], &path(&["title", "sizes"])[..]].concat();
        if source.path_exists(&tail, false) {
            let mut d = deletion_at(g, tail);
            d.reason = Some(RAMP_DELETION_REASON.into());
            out.push(d);
        }
    }
    for chart_type in chart_types_with_title_ramp(source) {
        let mut d = Deletion::new(g.source, g.target, RAMP_TAIL);
        d.reason = Some(RAMP_DELETION_REASON.into());
        d.chart_type = Some(chart_type);
        out.push(d);
    }
    for (tail, reason, chart_type) in KPI_DELETIONS {
        let mut d = Deletion::new(g.source, g.target, tail);
        d.reason = reason.map(str::to_string);
        d.chart_type = chart_type.map(str::to_string);
        out.push(d);
    }
    for (tail, chart_type) in COLUMN_DEFAULTS_LABEL_TAILS {
        let mut d = Deletion::new(g.source, g.target, tail);
        d.reason = Some(COLUMN_DEFAULTS_LABEL_REASON.into());
        d.chart_type = chart_type.map(str::to_string);
        out.push(d);
    }
    for key in ["number_format", "time_format"] {
        for family in THEME_FAMILIES {
            let tail = path(&["style", "charts", family, key]);
            if source.path_exists(&tail, false) {
                let mut d = deletion_at(g, tail);
                d.reason = Some(FORMAT_KEY_DELETION_REASON.replace("{key}", key));
                out.push(d);
            }
        }
        let mut d = Deletion::new(g.source, g.target, &["style", key]);
        d.reason = Some(HEATMAP_FORMAT_REASON.replace("{key}", key));
        d.chart_type = Some("heatmap".into());
        out.push(d);
    }
    out
}

pub fn declarations(g: &Grammars) -> Declarations {
    let mut moves = heading_ramp_moves(g);
    moves.extend(divider_moves(g));
    moves.extend(kpi_moves(g));
    moves.extend(chart_format_moves(g));
    moves.extend(style_null_moves(g));
    moves.extend(auto_link_moves(g));
    moves.extend(legend_position_moves(g));
    Declarations {
        moves,
        deletions: deletions(g),
        ..Declarations::default()
    }
}
