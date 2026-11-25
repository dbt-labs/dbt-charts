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
//! - **`style.outliers.reveal: none` becomes `chart`**, on a chart's own style,
//!   `style.charts.<type>` and every nested board. `chart` redraws every torn bar on
//!   hover and `bar` only the hovered one. The move is live, so a board at any version
//!   that still says `none` migrates with a notice.
//! - **`style.stack_order` becomes `style.series_order`** on bar and area, at every
//!   cascade level: it now orders a grouped bar's series too, so the name no longer
//!   says stack.
//! - **`tabs` maps each tab's label to its board.** The `items` list becomes the
//!   mapping itself, keyed by each item's `title`; the rest of the item is the
//!   board. `icon`, never drawn, goes. `position` moves to
//!   `style.layout.tabs.position`; `id` and `default` go with a notice, so the
//!   first tab opens. Two tabs with one title refuse. One `Move` of every
//!   board's `tabs.items` to `tabs`, run last, flattening the tabs in its panes too.
//! - **A color scale's bounds move under `domain`**: a gradient or table-column scale's
//!   `min` and `max` become `domain.min` and `domain.max` (`[min, max]` is the
//!   shorthand), one tail rename per scale target. Its old `domain` only ever said
//!   `data`, which an unset `domain` means: a live identity move strikes `data`, or a
//!   null beside `min`/`max`, with a notice; a lone null still clears an inherited range.

use std::collections::BTreeSet;
use std::sync::Arc;

use serde_json::Value as Json;

use crate::decl::{path, Declarations, Deletion, Grammars, Move, Rewrote};
use crate::fields::Fields;
use crate::pyrepr::repr_json;
use crate::v0_12_0::{holder, value_at};

const VARIABLES_VISIBLE_REASON: &str = "No renderer read it, so the variables strip drew either way. Hide one control with that variable's own `visible: false`.";
const REVEAL_NONE_NOTICE: &str = "`style.outliers.reveal: none` became `chart`: hover now redraws the torn bars. `bar` redraws only the hovered one.";
const SPARK_BAR_FONT_REASON: &str = "A spark bar cell draws no text, so this font styled nothing.";

const TABS: [&str; 1] = ["tabs"];
const TAB_ITEMS: [&str; 2] = ["tabs", "items"];
const TAB_POSITION: [&str; 4] = ["style", "layout", "tabs", "position"];
/// What a board must hold one of (AuthoredBoard's content rule).
const BOARD_CONTENT: [&str; 7] = ["rows", "cols", "grid", "tabs", "text", "charts", "title"];
const ICON_NOTE: &str =
    "A tab's `icon` is gone: tabs never drew it, so write an emoji into the label instead.";

/// A board's `tabs.items` as its `tabs`: the mapping from each tab's label to its board, every
/// tabs layout in its panes flattened too. A `position` beside it other than `top` goes to the
/// board's `style.layout.tabs.position`; `id` and `default` go, each with a notice.
fn flat_tabs(doc: &Json, source: &[String], value: &Json) -> Result<Rewrote, String> {
    let tabs_at = holder(source, 1);
    let mut block = value_at(doc, &tabs_at).cloned().unwrap_or(Json::Null);
    if let Json::Object(fields) = &mut block {
        fields.insert("items".into(), value.clone());
    }
    let mut notices = Vec::new();
    let (tabs, position) = flattened(&block, &mut notices)?;
    let board = holder(source, TAB_ITEMS.len());
    Ok(Rewrote {
        value: Some(tabs),
        notice: (!notices.is_empty()).then(|| notices.join(" ")),
        writes: position
            .map(|p| ([board, path(&TAB_POSITION)].concat(), Some(p)))
            .into_iter()
            .collect(),
        ..Rewrote::default()
    })
}

fn note(notices: &mut Vec<String>, said: String) {
    if !notices.contains(&said) {
        notices.push(said);
    }
}

/// One `tabs:` block as its label mapping, and the `position` it set other than `top`.
fn flattened(value: &Json, notices: &mut Vec<String>) -> Result<(Json, Option<Json>), String> {
    let Json::Object(fields) = value else {
        return Err(format!("`tabs` {} is not a mapping", repr_json(value)));
    };
    let Some(items) = fields.get("items") else {
        return Err("`tabs` has no `items`".into());
    };
    let tabs = tabs_as_mapping(items, notices)?;
    if let Some(id) = fields.get("id").filter(|v| !v.is_null()) {
        note(notices, format!("`tabs.id: {}` is gone: the variable that selects a tab is named after the board, so a link that set `{}` opens the first tab.", repr_json(id), id.as_str().unwrap_or_default()));
    }
    if let Some(default) = fields.get("default").filter(|v| !v.is_null()) {
        note(notices, format!("`tabs.default: {}` is gone: the first tab opens on load. Put the tab you want opened first.", repr_json(default)));
    }
    let position = fields
        .get("position")
        .filter(|p| p.as_str() != Some("top") && !p.is_null())
        .cloned();
    Ok((tabs, position))
}

fn tabs_as_mapping(v: &Json, notices: &mut Vec<String>) -> Result<Json, String> {
    let Json::Array(tabs) = v else {
        return Err(format!("`tabs.items` {} is not a list", repr_json(v)));
    };
    let mut out = serde_json::Map::new();
    for tab in tabs {
        let Json::Object(fields) = tab else {
            return Err(format!("the tab {} is not a mapping", repr_json(tab)));
        };
        let Some(Json::String(title)) = fields.get("title") else {
            return Err("a tab has no `title`: give each tab one, it becomes the tab's key".into());
        };
        if out.contains_key(title) {
            return Err(format!(
                "two tabs are titled {}: a tab is keyed by its label, so give each its own",
                repr_json(&Json::String(title.clone()))
            ));
        }
        if fields.contains_key("icon") {
            note(notices, ICON_NOTE.into());
        }
        let mut board = serde_json::Map::new();
        for (k, v) in fields {
            if !matches!(k.as_str(), "title" | "icon") {
                board.insert(k.clone(), v.clone());
            }
        }
        // The title was the tab's whole content: the pane stays, empty, as a board must hold one.
        if !BOARD_CONTENT.iter().any(|k| board.contains_key(*k)) {
            board.insert("rows".into(), Json::Array(Vec::new()));
        }
        out.insert(
            title.clone(),
            board_tabs_flattened(Json::Object(board), notices)?,
        );
    }
    Ok(Json::Object(out))
}

/// `board` with the tabs layouts of it and of every board in its layout flattened.
fn board_tabs_flattened(mut board: Json, notices: &mut Vec<String>) -> Result<Json, String> {
    let Json::Object(fields) = &mut board else {
        return Ok(board);
    };
    for key in ["rows", "cols"] {
        if let Some(Json::Array(items)) = fields.get_mut(key) {
            for item in items.iter_mut() {
                *item = board_tabs_flattened(item.take(), notices)?;
            }
        }
    }
    if let Some(Json::Array(cells)) = fields.get_mut("grid").and_then(|g| g.get_mut("items")) {
        for cell in cells.iter_mut() {
            if let Some(item) = cell.get_mut("item") {
                *item = board_tabs_flattened(item.take(), notices)?;
            }
        }
    }
    if let Some(tabs) = fields.get_mut("tabs") {
        let (flat, position) = flattened(tabs, notices)?;
        *tabs = flat;
        if let Some(position) = position {
            let [holders @ .., leaf] = TAB_POSITION;
            let mut at = &mut *fields;
            for key in holders {
                let next = at
                    .entry(key)
                    .or_insert_with(|| Json::Object(serde_json::Map::new()));
                let Json::Object(next) = next else {
                    return Err(format!("`{key}` beside `tabs.position` is not a mapping"));
                };
                at = next;
            }
            at.insert(leaf.into(), position);
        }
    }
    Ok(board)
}

/// One `tabs` move per board, run last: every move into a pane finds it at its old index.
fn tab_moves(g: &Grammars) -> Vec<Move> {
    let mut m = Move::new(g.source, g.target, path(&TAB_ITEMS), path(&TABS));
    m.sub_board_prefixes = g.nesting_prefixes();
    m.rewrite_in_document = Some(Arc::new(flat_tabs));
    m.reshapes = true;
    vec![m]
}

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

fn reveal_none(_doc: &Json, _source: &[String], value: &Json) -> Result<Rewrote, String> {
    if value.as_str() != Some("none") {
        return Ok(Rewrote {
            leave: true,
            ..Rewrote::default()
        });
    }
    Ok(Rewrote {
        value: Some(Json::from("chart")),
        notice: Some(REVEAL_NONE_NOTICE.into()),
        ..Rewrote::default()
    })
}

/// One identity move per place the grammar holds `outliers.reveal`.
fn reveal_none_moves(g: &Grammars) -> Vec<Move> {
    let mut fields = Fields::new(g.target_schema);
    let nesting = g.nesting_prefixes();
    let tail = path(&["outliers", "reveal"]);
    fields
        .paths(&BTreeSet::from([tail.clone()]))
        .into_iter()
        .filter(|p| p.ends_with(&tail))
        .filter(|p| g.source_schema.path_exists(p, false))
        .map(|p| {
            let mut m = Move::new(g.source, g.target, p.clone(), p);
            m.rewrite_in_document = Some(Arc::new(reveal_none));
            m.live = true;
            m.sub_board_prefixes = nesting.clone();
            m
        })
        .collect()
}

/// A scale target's retired `domain: data`, struck; so is a `domain: null` beside the
/// 0.12.0 `min`/`max` the boundary moves under it. Any other domain stays: a lone null is
/// current grammar, clearing an inherited range.
fn data_domain(doc: &Json, source: &[String], value: &Json) -> Result<Rewrote, String> {
    let scale = holder(source, 1);
    let beside_bounds = || ["min", "max"].iter().any(|k| text_at(doc, &scale, &[k]).is_some());
    let said = match value.as_str() {
        Some("data") => "data",
        _ if value.is_null() && beside_bounds() => "null",
        _ => {
            return Ok(Rewrote {
                leave: true,
                ..Rewrote::default()
            })
        }
    };
    Ok(Rewrote {
        notice: Some(format!(
            "`{}: {said}` is gone: a scale with no `domain` already fits the data.",
            source.join(".")
        )),
        ..Rewrote::default()
    })
}

/// One live identity move per place a scale target (the model with `hinge` beside
/// `domain`) holds `domain`.
fn data_domain_moves(g: &Grammars) -> Vec<Move> {
    let tail = path(&["domain"]);
    let nesting = g.nesting_prefixes();
    Fields::new(g.source_schema)
        .paths(&BTreeSet::from([tail.clone()]))
        .into_iter()
        .filter(|p| p.ends_with(&tail) && g.target_schema.path_exists(p, false))
        .filter(|p| {
            let hinge = [&holder(p, 1)[..], &path(&["hinge"])[..]].concat();
            g.source_schema.path_exists(&hinge, false)
        })
        .map(|p| {
            let mut m = Move::new(g.source, g.target, p.clone(), p);
            m.rewrite_in_document = Some(Arc::new(data_domain));
            m.live = true;
            m.sub_board_prefixes = nesting.clone();
            m
        })
        .collect()
}

pub fn declarations(g: &Grammars) -> Declarations {
    let moves: Vec<Move> = g
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
        .chain(reveal_none_moves(g))
        .chain(data_domain_moves(g))
        .chain(g.suffix_rename_moves(
            &[
                (&["domain", "min"], &["min"]),
                (&["domain", "max"], &["max"]),
            ],
            None,
            None,
        ))
        .chain(g.suffix_rename_moves(&[(&["series_order"], &["stack_order"])], None, None))
        .chain(tab_moves(g))
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
