//! The 0.11.0 -> 0.12.0 boundary: `endpoint_labels.visible` becomes the legend
//! position's `endpoints` edge. `label_offset` and `height` stay under
//! `endpoint_labels`; `successor_edges` holds the scope rules.
//!
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

use std::sync::Arc;

use indexmap::{IndexMap, IndexSet};
use serde_json::Value as Json;

use crate::decl::{path, Declarations, Deletion, DocumentRewrite, Grammars, KeyPath, Move, Rewritten, Rewrote};
use crate::pyrepr::repr_json;
use crate::v0_11_0::{respell_secondary_ink, retired_scaffold_token_map};

const TITLE_SIZE_REASON: &str = "A board title's size comes from the heading ramp, so this key set none. Set `style.fonts.heading.sizes` (or `style.fonts.section.sizes`) to size board and section titles, or a chart's own `style.title.font.size`.";
const RAIL_FAMILIES: [&str; 3] = ["line", "area", "bar"];
const CONCRETE_EDGES: [&str; 4] = ["top", "bottom", "left", "right"];
const LEGEND_EDGE: [&str; 3] = ["legend", "position", "edge"];
const ENDPOINT_VISIBLE: [&str; 2] = ["endpoint_labels", "visible"];
/// Beside `endpoints` these mean nothing, and the successor refuses them.
const POSITION_PLACEMENT: [[&str; 3]; 2] = [
    ["legend", "position", "align"],
    ["legend", "position", "overlay"],
];

const CAVEAT: &str = "Legend settings in other files (meta.yml, `extends:`, a board this file styles) can change how this combines, so check the charts it styles.";
const LAYERED_LEAVES: &str =
    "a legend shown beside the labels (as on a layered chart) no longer stays at";

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

type Path = Vec<String>;

/// The field's old type, `bool | Literal["auto"]`: an authored `"false"` or `1`
/// meant what it coerced to.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Visible {
    On,
    Off,
    Auto,
}

impl Visible {
    fn text(self) -> &'static str {
        match self {
            Visible::On => "true",
            Visible::Off => "false",
            Visible::Auto => "auto",
        }
    }
}

/// Where a chart is placed, its definition's path, and the boards enclosing the
/// placement (outermost first).
struct Placement {
    at: Path,
    chart: Path,
    enclosing: Vec<Path>,
}

/// The scopes one placed chart reads, from `scopes[0]` out to the broadest.
#[derive(Clone)]
struct Chain {
    placement: Path,
    /// The chart's own scope; `None` for a scope that styles no chart in the file.
    chart: Option<Path>,
    scopes: Vec<Path>,
}

/// The edge a scope holds after the boundary (`None`: struck, it inherits),
/// the notice it owes, and the values it owes other positions.
#[derive(Clone)]
struct Successor {
    edge: Option<Json>,
    notice: Option<String>,
    writes: Vec<(KeyPath, Option<Json>)>,
}

fn join(parts: &[&[String]]) -> Path {
    parts.concat()
}

fn strs(parts: &[&str]) -> Path {
    path(parts)
}

fn dotted(p: &[String]) -> String {
    p.join(".")
}

/// The value at `p`; a digit segment indexes a list.
fn value_at<'a>(doc: &'a Json, p: &[String]) -> Option<&'a Json> {
    p.iter().try_fold(doc, |node, part| match node {
        Json::Object(m) => m.get(part),
        Json::Array(items) => part.parse::<usize>().ok().and_then(|i| items.get(i)),
        _ => None,
    })
}

/// An authored, non-null value at `scope` + `tail`.
fn authored<'a>(doc: &'a Json, scope: &[String], tail: &[&str]) -> Option<&'a Json> {
    value_at(doc, &join(&[scope, &strs(tail)])).filter(|v| !v.is_null())
}

/// A value as a Python f-string prints it.
fn shown(v: &Json) -> String {
    match v {
        Json::String(s) => s.clone(),
        other => repr_json(other),
    }
}

fn is_concrete(v: Option<&Json>) -> bool {
    v.and_then(Json::as_str)
        .is_some_and(|s| CONCRETE_EDGES.contains(&s))
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

fn visible_at(doc: &Json, scope: &[String]) -> Result<Option<Visible>, String> {
    let Some(value) = authored(doc, scope, &ENDPOINT_VISIBLE) else {
        return Ok(None);
    };
    coerce(value).map(Some).ok_or_else(|| {
        format!(
            "Cannot migrate `{}: {}`. Migrate this field manually.",
            dotted(&join(&[scope, &strs(&ENDPOINT_VISIBLE)])),
            repr_json(value)
        )
    })
}

fn authors(doc: &Json, scope: &[String]) -> Result<bool, String> {
    Ok(visible_at(doc, scope)?.is_some() || authored(doc, scope, &LEGEND_EDGE).is_some())
}

fn family(doc: &Json, chart: &[String]) -> Option<&'static str> {
    let t = authored(doc, chart, &["type"])?.as_str()?;
    RAIL_FAMILIES.iter().copied().find(|f| *f == t)
}

/// Each board's enclosing boards (ending with itself), and each placed chart.
///
/// The boards of a file share one set of chart names, so a name places the
/// chart whichever board defines it. An unplaced chart is read from the board
/// defining it.
fn layout(doc: &Json) -> (Vec<Vec<Path>>, Vec<Placement>) {
    enum Placed {
        Name(String),
        At(Path),
    }
    struct Walk<'a> {
        boards: Vec<(Vec<Path>, &'a Json)>,
        entries: Vec<(Path, Placed, Vec<Path>)>,
    }
    fn walk<'a>(board: &'a Json, chain: Vec<Path>, w: &mut Walk<'a>) {
        let here = chain.last().cloned().unwrap_or_default();
        w.boards.push((chain.clone(), board));
        let mut items: Vec<(Path, &Json)> = Vec::new();
        for key in ["rows", "cols"] {
            if let Some(Json::Array(listed)) = board.get(key) {
                for (i, item) in listed.iter().enumerate() {
                    items.push((join(&[&here, &strs(&[key, &i.to_string()])]), item));
                }
            }
        }
        if let Some(Json::Array(cells)) = board.get("grid").and_then(|g| g.get("items")) {
            for (i, cell) in cells.iter().enumerate() {
                if let Some(item) = cell.as_object().and_then(|c| c.get("item")) {
                    let at = join(&[&here, &strs(&["grid", "items", &i.to_string(), "item"])]);
                    items.push((at, item));
                }
            }
        }
        for (at, item) in items {
            match item {
                Json::String(name) => {
                    w.entries
                        .push((at, Placed::Name(name.clone()), chain.clone()))
                }
                Json::Object(m) if m.contains_key("type") => {
                    w.entries.push((at.clone(), Placed::At(at), chain.clone()))
                }
                Json::Object(m) if m.len() == 1 => {
                    let inline = m.iter().next().filter(|(_, chart)| {
                        chart.get("type").is_some_and(Json::is_string) && chart.is_object()
                    });
                    match inline {
                        Some((name, _)) => {
                            let chart = join(&[&at, std::slice::from_ref(name)]);
                            w.entries.push((at, Placed::At(chart), chain.clone()));
                        }
                        None => walk(item, [&chain[..], &[at]].concat(), w),
                    }
                }
                Json::Object(_) => walk(item, [&chain[..], &[at]].concat(), w),
                _ => {}
            }
        }
        if let Some(Json::Array(tabs)) = board.get("tabs").and_then(|t| t.get("items")) {
            for (i, tab) in tabs.iter().enumerate() {
                if tab.is_object() {
                    let at = join(&[&here, &strs(&["tabs", "items", &i.to_string()])]);
                    walk(tab, [&chain[..], &[at]].concat(), w);
                }
            }
        }
    }

    let mut w = Walk {
        boards: Vec::new(),
        entries: Vec::new(),
    };
    walk(doc, vec![Vec::new()], &mut w);
    let mut defined: IndexMap<String, (Path, Vec<Path>)> = IndexMap::new();
    for (chain, board) in &w.boards {
        if let Some(Json::Object(charts)) = board.get("charts") {
            let here = chain.last().cloned().unwrap_or_default();
            for name in charts.keys() {
                let chart = join(&[&here, &strs(&["charts", name])]);
                defined.insert(name.clone(), (chart, chain.clone()));
            }
        }
    }
    let mut placements = Vec::new();
    for (at, chart, enclosing) in w.entries {
        match chart {
            // A name no board here defines is another file's chart.
            Placed::Name(name) => {
                if let Some((chart, _)) = defined.get(&name) {
                    placements.push(Placement {
                        at,
                        chart: chart.clone(),
                        enclosing,
                    });
                }
            }
            Placed::At(chart) => placements.push(Placement {
                at,
                chart,
                enclosing,
            }),
        }
    }
    let placed: IndexSet<Path> = placements.iter().map(|p| p.chart.clone()).collect();
    for (chart, chain) in defined.into_values() {
        if !placed.contains(&chart) {
            placements.push(Placement {
                at: chart.clone(),
                chart,
                enclosing: chain,
            });
        }
    }
    (
        w.boards.into_iter().map(|(chain, _)| chain).collect(),
        placements,
    )
}

/// The board scopes a `family` chart reads, narrowest first.
///
/// The style trees merge board by board before a family inherits from its
/// block, so a family at any depth outranks a block at any depth.
fn scopes(boards: &[Path], family: &str) -> Vec<Path> {
    let families = boards
        .iter()
        .rev()
        .map(|b| join(&[b, &strs(&["style", "charts", family])]));
    let blocks = boards
        .iter()
        .rev()
        .map(|b| join(&[b, &strs(&["style", "charts"])]));
    families.chain(blocks).collect()
}

/// Each scope that authors either key, with the chains it heads.
///
/// A chart's old result came from the narrowest scope it reads that authors
/// `endpoint_labels.visible` or `legend.position.edge`, so that scope's chain
/// holds the chart's whole pair. A scope that heads no chart in this file is
/// read from its own board.
fn chains(
    doc: &Json,
    boards: &[Vec<Path>],
    placements: &[Placement],
) -> Result<IndexMap<Path, Vec<Chain>>, String> {
    let mut heads: IndexMap<Path, Vec<Chain>> = IndexMap::new();
    for p in placements {
        let Some(fam) = family(doc, &p.chart) else {
            continue;
        };
        let read: Vec<Path> = std::iter::once(join(&[&p.chart, &strs(&["style"])]))
            .chain(scopes(&p.enclosing, fam))
            .collect();
        for (i, scope) in read.iter().enumerate() {
            if authors(doc, scope)? {
                heads.entry(scope.clone()).or_default().push(Chain {
                    placement: p.at.clone(),
                    chart: read.first().cloned(),
                    scopes: read.get(i..).unwrap_or_default().to_vec(),
                });
                break;
            }
        }
    }
    for enclosing in boards {
        for fam in RAIL_FAMILIES {
            let read = scopes(enclosing, fam);
            for i in [0, enclosing.len()] {
                let Some(scope) = read.get(i) else { continue };
                if !heads.contains_key(scope) && authors(doc, scope)? {
                    heads.insert(
                        scope.clone(),
                        vec![Chain {
                            placement: enclosing.last().cloned().unwrap_or_default(),
                            chart: None,
                            scopes: read.get(i..).unwrap_or_default().to_vec(),
                        }],
                    );
                }
            }
        }
    }
    Ok(heads)
}

/// Blocks, then families, then charts, each outermost first.
fn rank(scope: &[String]) -> (u8, usize) {
    let n = scope.len();
    let ends = |k: usize, tail: &[&str]| {
        n >= k
            && scope
                .get(n - k..n - k + tail.len())
                .is_some_and(|s| s == tail)
    };
    if ends(2, &["style", "charts"]) {
        (0, n)
    } else if ends(3, &["style", "charts"]) {
        (1, n)
    } else {
        (2, n)
    }
}

/// A chain's nearest `visible` and nearest edge in this file.
struct Pair {
    visible: Option<Visible>,
    edge: Option<Json>,
}

impl Pair {
    fn successor(&self) -> Json {
        match self.visible {
            Some(Visible::On) => Json::from("endpoints"),
            Some(Visible::Off) if is_concrete(self.edge.as_ref()) => {
                self.edge.clone().unwrap_or(Json::Null)
            }
            Some(Visible::Off) => Json::from("top"),
            _ => self.edge.clone().unwrap_or_else(|| Json::from("auto")),
        }
    }
}

fn pair(doc: &Json, read: &[Path]) -> Result<Pair, String> {
    let mut visible = None;
    for s in read {
        if let Some(v) = visible_at(doc, s)? {
            visible = Some(v);
            break;
        }
    }
    let edge = read
        .iter()
        .find_map(|s| authored(doc, s, &LEGEND_EDGE))
        .cloned();
    Ok(Pair { visible, edge })
}

/// The one notice a `visible` authored at `head` owes; `None` where it has none.
fn notice(doc: &Json, head: &[String], became: &str) -> Result<Option<String>, String> {
    Ok(visible_at(doc, head)?.map(|v| {
        format!(
            "`{}: {}` {became}. {CAVEAT}",
            dotted(&join(&[head, &strs(&ENDPOINT_VISIBLE)])),
            v.text()
        )
    }))
}

fn edge_text(edge: &Json) -> String {
    format!("`legend.position.edge: {}`", shown(edge))
}

/// The `legend.position.edge` each authoring scope holds after the boundary:
/// struck where the scope already inherits it, with chart-level writes where
/// one scope's charts mapped to different edges.
///
/// A placed chart reads the nearest `visible` and the nearest edge, each through
/// the chart, then `style.charts.<family>`, then `style.charts`, innermost board
/// first. `true` maps to `endpoints`, `false` to the edge if it names a side and
/// else `top`, `auto` or unset to the edge as it stood.
///
/// The narrowest scope a chart reads that authors either key holds the chart's
/// pair. It takes the edge most of its charts' pairs map to, or nothing where
/// they inherit it from a scope in this file no other file outranks (another
/// file's family outranks a block here), and loses `visible`; a chart it styles
/// that mapped elsewhere gets its own edge, with the `align` and `overlay` it
/// authors struck if that edge is `endpoints`. A chart placed in two boards whose
/// pairs map apart raises: one chart holds one edge.
///
/// Every migrated `visible` gets a notice naming what it became, and so does an
/// authored side a broader `true` turns into `endpoints`. A concrete edge owns
/// the rail whatever `legend.visible` says, so a hidden legend under an edge now
/// names nothing; `WARN-LEGEND-HIDDEN-UNDER-EDGE` reports it.
fn successor_edges(doc: &Json) -> Result<IndexMap<Path, Successor>, String> {
    let (boards, placements) = layout(doc);
    let mut pins: IndexMap<Path, Json> = IndexMap::new();
    let mut successors: IndexMap<Path, Successor> = IndexMap::new();
    let mut heads: Vec<(Path, Vec<Chain>)> =
        chains(doc, &boards, &placements)?.into_iter().collect();
    heads.sort_by_key(|(head, _)| rank(head));
    for (head, heads_chains) in heads {
        let mut live: Vec<(Chain, Pair)> = Vec::new();
        for chain in heads_chains {
            if chain.chart.as_ref().is_some_and(|c| pins.contains_key(c)) {
                continue;
            }
            let p = pair(doc, &chain.scopes)?;
            live.push((chain, p));
        }
        let authored_edge = authored(doc, &head, &LEGEND_EDGE).cloned();
        if live.is_empty() {
            let found = match &authored_edge {
                None => Successor {
                    edge: None,
                    notice: notice(
                        doc,
                        &head,
                        "was struck; the charts it styles hold their own `legend.position.edge`",
                    )?,
                    writes: Vec::new(),
                },
                Some(edge) => Successor {
                    edge: Some(edge.clone()),
                    notice: notice(doc, &head, &format!("is now {}", edge_text(edge)))?,
                    writes: Vec::new(),
                },
            };
            successors.insert(head, found);
            continue;
        }
        let inherited: Vec<Option<&Path>> = live
            .iter()
            .map(|(chain, _)| {
                chain
                    .scopes
                    .iter()
                    .skip(1)
                    .find(|s| successors.get(*s).is_some_and(|found| found.edge.is_some()))
            })
            .collect();
        let leans = authored_edge.is_none()
            && live.iter().zip(&inherited).all(|((_, p), found)| {
                found.is_some_and(|s| {
                    successors.get(s).and_then(|f| f.edge.as_ref()) == Some(&p.successor())
                })
            });
        let edges: Vec<Json> = live.iter().map(|(_, p)| p.successor()).collect();
        // Another file's family outranks any block here, so a chart or family
        // drops its edge only onto a chart or family in this file.
        if leans
            && (rank(&head).0 == 0
                || inherited
                    .iter()
                    .all(|found| found.is_some_and(|s| rank(s).0 > 0)))
        {
            let mut kept: Vec<String> = edges.iter().map(edge_text).collect();
            kept.sort();
            kept.dedup();
            let became = format!(
                "was struck; the charts it styles inherit {}",
                kept.join(" or ")
            );
            let found = Successor {
                edge: None,
                notice: notice(doc, &head, &became)?,
                writes: Vec::new(),
            };
            successors.insert(head, found);
            continue;
        }
        let mut chosen = Json::Null;
        let mut best = 0;
        for edge in &edges {
            let count = edges.iter().filter(|e| *e == edge).count();
            if count > best {
                best = count;
                chosen = edge.clone();
            }
        }
        let mut writes = Vec::new();
        let mut struck_notes: Vec<String> = Vec::new();
        for (chain, p) in &live {
            let edge = p.successor();
            if edge == chosen {
                continue;
            }
            let pinned = chain
                .chart
                .as_ref()
                .filter(|c| **c != head && edge.is_string());
            let Some(chart) = pinned else {
                let first = live.first().map(|(c, _)| c).unwrap_or(chain);
                return Err(apart(&head, first, chain, &chosen, &edge));
            };
            pins.insert(chart.clone(), edge.clone());
            writes.push((join(&[chart, &strs(&LEGEND_EDGE)]), Some(edge.clone())));
            if edge == "endpoints" {
                let struck = striking_placement(
                    doc,
                    chart,
                    Successor {
                        edge: Some(edge),
                        notice: None,
                        writes: Vec::new(),
                    },
                );
                writes.extend(struck.writes);
                struck_notes.extend(struck.notice);
            }
        }
        let mut became = format!("is now {}", edge_text(&chosen));
        let side = live
            .iter()
            .find(|(_, p)| p.visible == Some(Visible::On) && is_concrete(p.edge.as_ref()))
            .and_then(|(_, p)| p.edge.clone());
        if let Some(side) = side {
            became.push_str(&format!("; {LAYERED_LEAVES} `{}`", shown(&side)));
        }
        let mut said = notice(doc, &head, &became)?;
        if said.is_none() && chosen == "endpoints" && is_concrete(authored_edge.as_ref()) {
            let mut shown_at = None;
            'find: for (chain, p) in &live {
                if p.successor() != chosen {
                    continue;
                }
                for scope in &chain.scopes {
                    if visible_at(doc, scope)?.is_some() {
                        shown_at = Some(scope.clone());
                        break 'find;
                    }
                }
            }
            let Some(at) = shown_at else {
                return Err(format!(
                    "Cannot migrate `{}`: no `endpoint_labels.visible` maps it to `endpoints`.",
                    dotted(&head)
                ));
            };
            let side = authored_edge.as_ref().map(shown).unwrap_or_default();
            said = Some(format!(
                "`{}: {side}` is now `legend.position.edge: endpoints`, as `{}: true` applies; {LAYERED_LEAVES} `{side}`. {CAVEAT}",
                dotted(&join(&[&head, &strs(&LEGEND_EDGE)])),
                dotted(&join(&[&at, &strs(&ENDPOINT_VISIBLE)])),
            ));
        }
        if !struck_notes.is_empty() {
            said = Some(
                said.into_iter()
                    .chain(struck_notes)
                    .collect::<Vec<_>>()
                    .join(" "),
            );
        }
        successors.insert(
            head,
            Successor {
                edge: Some(chosen),
                notice: said,
                writes,
            },
        );
    }
    for p in &placements {
        let scope = join(&[&p.chart, &strs(&["style"])]);
        let (Some(pinned), Some(fam)) = (pins.get(&scope), family(doc, &p.chart)) else {
            continue;
        };
        let read: Vec<Path> = std::iter::once(scope.clone())
            .chain(scopes(&p.enclosing, fam))
            .collect();
        let edge = pair(doc, &read)?.successor();
        if edge != *pinned {
            return Err(format!(
                "Cannot migrate `{}`: placed at `{}` its legend sat at `{}`, and elsewhere at `{}`, which one chart's `legend.position.edge` cannot keep. Give each placement its own chart with its own `legend.position.edge`, then delete `endpoint_labels.visible`.",
                dotted(&p.chart),
                dotted(&p.at),
                shown(&edge),
                shown(pinned)
            ));
        }
    }
    Ok(successors)
}

fn apart(head: &[String], first: &Chain, other: &Chain, edge: &Json, other_edge: &Json) -> String {
    format!(
        "Cannot migrate `{}`: the charts it styles at `{}` and `{}` had different legend positions (`{}` and `{}`), which one chart's `legend.position.edge` cannot keep. Give each placement its own chart with its own `legend.position.edge`, then delete `endpoint_labels.visible`.",
        dotted(head),
        dotted(&first.placement),
        dotted(&other.placement),
        shown(edge),
        shown(other_edge)
    )
}

/// `found`, striking the `align` and `overlay` its `endpoints` would sit beside.
///
/// Only those in the same mapping: one a broader scope sets does not apply to
/// endpoint labels, so the successor ignores it.
fn striking_placement(doc: &Json, holder: &[String], found: Successor) -> Successor {
    if found.edge.as_ref().and_then(Json::as_str) != Some("endpoints") {
        return found;
    }
    let struck: Vec<&[&str; 3]> = POSITION_PLACEMENT
        .iter()
        .filter(|tail| authored(doc, holder, &tail[..]).is_some_and(|v| v != "auto"))
        .collect();
    if struck.is_empty() {
        return found;
    }
    let keys: Vec<String> = struck.iter().map(|t| format!("`{}`", t[2])).collect();
    let said = format!(
        "{} beside `legend.position.edge: endpoints` at `{}` was struck: endpoint labels name each series on the chart, not in a legend placed along an edge.",
        keys.join(", "),
        dotted(holder)
    );
    let mut writes = found.writes;
    writes.extend(
        struck
            .iter()
            .map(|t| (join(&[holder, &strs(&t[..])]), None)),
    );
    Successor {
        edge: found.edge,
        notice: Some(match found.notice {
            None => said,
            Some(n) => format!("{n} {said}"),
        }),
        writes,
    }
}

fn holder(source: &[String], depth: usize) -> Path {
    source
        .get(..source.len().saturating_sub(depth))
        .unwrap_or_default()
        .to_vec()
}

fn rewrote(found: Successor) -> Rewrote {
    Rewrote {
        value: found.edge,
        notice: found.notice,
        writes: found.writes,
    }
}

/// An authored `legend.position.edge` as the edge its scope's pair maps to.
fn edge_in_document(doc: &Json, source: &[String], value: &Json) -> Result<Rewrote, String> {
    let holder = holder(source, LEGEND_EDGE.len());
    let mut successors = successor_edges(doc)?;
    let Some(found) = successors
        .shift_remove(&holder)
        .filter(|_| !value.is_null())
    else {
        return Ok(Rewrote {
            value: Some(value.clone()),
            ..Rewrote::default()
        });
    };
    Ok(rewrote(striking_placement(doc, &holder, found)))
}

/// `endpoint_labels.visible` as the `legend.position.edge` it became.
///
/// Struck where its mapping also authors an edge: `edge_in_document` rewrites
/// that one and carries this key's notice. A `null` never reaches it: the
/// 0.10.0 style-null rule drops it first.
fn visible_to_edge(doc: &Json, source: &[String], _value: &Json) -> Result<Rewrote, String> {
    let holder = holder(source, ENDPOINT_VISIBLE.len());
    let struck = |notice: Option<String>| Rewrote {
        value: None,
        notice,
        writes: Vec::new(),
    };
    if authored(
        doc,
        holder
            .get(..holder.len().saturating_sub(1))
            .unwrap_or_default(),
        &["type"],
    )
    .is_some_and(|t| t == "histogram")
    {
        visible_at(doc, &holder)?;
        return Ok(struck(Some(format!(
            "`{}` was struck: it had no effect on a histogram.",
            dotted(source)
        ))));
    }
    let mut successors = successor_edges(doc)?;
    let Some(found) = successors.shift_remove(&holder) else {
        return Err(format!(
            "Cannot migrate `{}`: it is not on a line, area or bar chart or family. Migrate this field manually.",
            dotted(source)
        ));
    };
    if authored(doc, &holder, &LEGEND_EDGE).is_some() {
        return Ok(struck(None));
    }
    Ok(rewrote(striking_placement(doc, &holder, found)))
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
    let edge_rewrite: DocumentRewrite = Arc::new(edge_in_document);
    let visible_rewrite: DocumentRewrite = Arc::new(visible_to_edge);
    let tabs = path(&["tabs", "items", "*"]);
    let moves = g
        .suffix_rename_moves(
            &[
                (&LEGEND_EDGE, &LEGEND_EDGE),
                (&LEGEND_EDGE, &ENDPOINT_VISIBLE),
            ],
            None,
            None,
        )
        .into_iter()
        .map(|mut m| {
            m.rewrite_in_document = Some(if m.old_path == m.new_path {
                edge_rewrite.clone()
            } else {
                visible_rewrite.clone()
            });
            m
        })
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
    let mut title_size = Deletion::new(g.source, g.target, &["style", "title", "font", "size"]);
    title_size.reason = Some(TITLE_SIZE_REASON.into());
    Declarations {
        moves,
        deletions: vec![title_size],
        ..Declarations::default()
    }
}
