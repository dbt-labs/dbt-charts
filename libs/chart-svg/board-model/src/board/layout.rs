//! How a board arranges what it holds: `rows` / `cols` / `grid` / `tabs`, spelled as
//! sibling authored keys, and the items they place.
//!
//! An item is a chart by [`ChartId`] or a nested board. An inline chart is a third arm of the
//! authored form only (`LayoutItemAuthored::InlineChart`): normalize hoists it into
//! `Board.charts` under a tree-position id and the built layout holds that id.

use crate::board::value::{Length, Visible};
use crate::board::BoardBody;
use crate::chart::Chart;
use crate::facet::DisplayText;
use crate::ids::{ChartId, ItemId};
use indexmap::IndexMap;
use model_core::{
    build_payload, Build, Cx, FromNode, Leaf, Merge, Node, Spell, TypeExpr, Unset, Walk,
};
use model_derive::Model;
use yaml::{Kind, Value};

/// A board arranged on one axis, or in a grid, or in tabs. Exactly one is written;
/// a second key is an error, and a board that writes none is arranged by normalize
/// (its charts, in authored order, as rows).
#[derive(Debug, Clone, PartialEq, Model)]
pub enum Layout {
    /// Vertical stack of charts and nested boards.
    Rows(Vec<LayoutItem>),
    /// Horizontal arrangement of charts and nested boards.
    Cols(Vec<LayoutItem>),
    /// CSS-grid style layout with explicit row/column placement.
    Grid(GridLayout),
    /// Each tab's label, in display order, to the board it shows. A board's tabs are what it
    /// wrote: an inherited tab is never merged in by label.
    #[model(merge = override)]
    Tabs(IndexMap<String, NestedBoard>),
}

/// A board nested in a parent's layout: its place in the parent, and its own content.
/// `width` and `height` size the slot in the parent (a root board's `width` is its own,
/// `Board.width`), and `visible` is read at render time.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct NestedBoard {
    /// Width when nested: pixels, or a share of the parent such as `50%`.
    #[model(absent = "the parent shares its width out")]
    pub width: Option<Length>,
    /// Height when nested: pixels, or a share of the parent.
    #[model(absent = "the slot is as tall as its content")]
    pub height: Option<Length>,
    /// Whether this board is drawn: a bool, a variable name, a Jinja expression, or a
    /// `{query, column}` probe.
    #[model(absent = "always drawn")]
    pub visible: Option<Visible>,
    #[flatten]
    pub body: BoardBody,
}

/// One cell of a layout: a chart by id, or a nested board.
// Items live in a `Vec`; a `Box` would only add an indirection to every match in `bind`.
#[allow(clippy::large_enum_variant)]
#[derive(Debug, Clone, PartialEq)]
pub enum LayoutItem {
    /// A chart by id, and whether this placement draws it. A bare name places it always drawn;
    /// normalize moves an authored chart's `visible:` here.
    Chart {
        id: ChartId,
        visible: Option<Visible>,
    },
    Board(NestedBoard),
}

/// A [`LayoutItem`] as parsed: a bare string is a chart name, a mapping a nested board.
#[allow(clippy::large_enum_variant)]
#[derive(Debug, Clone, PartialEq)]
pub enum LayoutItemAuthored {
    Chart(ChartId),
    Board(<NestedBoard as Leaf>::Whole),
    /// A chart written in place: a mapping with a `type`, or `{name: {type: ..}}` with any name
    /// (Python keeps the name for nothing: the hoisted id is the item's position). Not buildable
    /// until normalize hoists it.
    InlineChart(Box<<Chart as Leaf>::Whole>),
}

impl Spell for LayoutItemAuthored {
    fn spell(&self) -> serde_json::Value {
        match self {
            Self::Chart(id) => id.spell(),
            Self::Board(b) => b.spell(),
            Self::InlineChart(c) => c.spell(),
        }
    }
}

impl Leaf for LayoutItem {
    type Whole = LayoutItemAuthored;
    type Authored = Option<LayoutItemAuthored>;
    type Patch = Option<LayoutItemAuthored>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(
        &<NestedBoard as Leaf>::EXPR,
        &TypeExpr::Either(
            &<Chart as Leaf>::EXPR,
            &TypeExpr::Either(
                &<ChartId as Leaf>::EXPR,
                &TypeExpr::Map(&<Chart as Leaf>::EXPR),
            ),
        ),
    );
}

impl Build for LayoutItem {
    fn build(slot: Self::Patch, at: &model_core::At<'_>) -> Result<Self, Unset> {
        Self::build_authored(slot, at)
    }
    fn build_authored(slot: Self::Authored, at: &model_core::At<'_>) -> Result<Self, Unset> {
        match slot {
            None => Err(Unset {
                paths: vec![at.to_string()],
            }),
            Some(LayoutItemAuthored::Chart(id)) => Ok(Self::Chart { id, visible: None }),
            Some(LayoutItemAuthored::Board(b)) => {
                build_payload::<NestedBoard>(b, at).map(Self::Board)
            }
            // The hoisted chart is a `Chart { id, .. }`; an unhoisted one has no id to hold.
            Some(LayoutItemAuthored::InlineChart(_)) => Err(Unset {
                paths: vec![at.to_string()],
            }),
        }
    }
}

impl FromNode for LayoutItem {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::whole(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::whole(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        match &node.kind {
            Kind::Scalar(Value::Str(_)) => {
                <ChartId as FromNode>::whole(node, cx).map(LayoutItemAuthored::Chart)
            }
            Kind::Map(m) if m.contains_key("type") => <Chart as FromNode>::whole(node, cx)
                .map(|c| LayoutItemAuthored::InlineChart(Box::new(c))),
            Kind::Map(m) => match m.iter().next() {
                // A lone key over a typed mapping names a chart, whatever the key: Python takes the
                // chart arm whenever the board arm fails, so `{title: {type: ..}}` is a chart there.
                Some((_, entry))
                    if m.len() == 1
                        && matches!(&entry.value.kind, Kind::Map(v)
                            if matches!(v.get("type").map(|t| &t.value.kind), Some(Kind::Scalar(Value::Str(_))))) =>
                {
                    <Chart as FromNode>::whole(&entry.value, cx)
                        .map(|c| LayoutItemAuthored::InlineChart(Box::new(c)))
                }
                _ => <NestedBoard as FromNode>::whole(node, cx).map(LayoutItemAuthored::Board),
            },
            _ => {
                cx.mismatch(node, "a chart name or a nested board");
                None
            }
        }
    }
    fn walk(w: &mut Walk) {
        <NestedBoard as FromNode>::walk(w);
    }
}

/// A grid cell: what it holds and where it sits, in grid units (not pixels).
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(
    range(col_span, ge = 0),
    range(row_span, ge = 0),
    range(width, ge = 0),
    range(height, ge = 0)
))]
#[allow(clippy::duplicated_attributes)]
pub struct GridItem {
    /// Chart name or nested board to place in this grid cell.
    #[model(arm_pattern)]
    pub item: LayoutItem,
    /// Column position (0-indexed).
    #[model(absent = "auto-placed")]
    pub col: Option<i64>,
    /// Row position (0-indexed).
    #[model(absent = "auto-placed")]
    pub row: Option<i64>,
    /// Number of columns to span. Zero or a positive number; 0 is treated as unset and
    /// falls back to `width`, then to 1.
    #[model(absent = "spans `width` columns, else one")]
    pub col_span: Option<u32>,
    /// Number of rows to span. Zero or a positive number; 0 is treated as unset and
    /// falls back to `height`, then to 1.
    #[model(absent = "spans `height` rows, else one")]
    pub row_span: Option<u32>,
    /// Alias for `col_span`, used only when `col_span` is unset or 0.
    #[model(absent = "falls back to one column")]
    pub width: Option<u32>,
    /// Alias for `row_span`, used only when `row_span` is unset or 0.
    #[model(absent = "falls back to one row")]
    pub height: Option<u32>,
    /// Optional metadata for AI search, emitted into the SVG DOM as `data-layout-notes`;
    /// never painted.
    #[model(absent = "no notes")]
    pub notes: Option<DisplayText>,
}

/// Grid layout configuration.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(columns, gt = 0)))]
pub struct GridLayout {
    /// Grid columns, 24 unless set; write it only when it differs. Positive number only.
    #[model(fill = 24)]
    pub columns: u32,
    /// Cells of this grid, each pairing content with its placement.
    pub items: Vec<GridItem>,
}

/// One layout item of a body, where it sits and how it is placed.
#[derive(Debug, Clone, PartialEq)]
pub struct Placement<'a> {
    pub id: ItemId,
    pub item: &'a LayoutItem,
    /// The tab index at each tabs layout the item sits under, outermost first; empty outside tabs.
    /// A tabs layout written directly in a tab pane adds one more, so the last is the nearest.
    pub pane: Vec<usize>,
    /// The grid cell holding the item, for the grid's own placement fields.
    pub cell: Option<&'a GridItem>,
}

/// The keys of a position id, each with its index (`rows[2]` is `("rows", Some(2))`).
fn position_steps(at: &ItemId) -> Vec<(&str, Option<usize>)> {
    let rest = at.as_str().trim_start_matches('@');
    if rest.is_empty() {
        return Vec::new();
    }
    rest.split('.')
        .map(|part| match part.split_once('[') {
            Some((key, n)) => (key, n.trim_end_matches(']').parse().ok()),
            None => (part, None),
        })
        .collect()
}

/// The authored path of the item at `at` below `body`, segment by segment (`rows`, `3`; `tabs`,
/// `Overview`, `rows`, `2`): a position's indices become segments, and a tab is its label.
pub fn authored_path(body: &BoardBody, at: &ItemId) -> Result<Vec<String>, String> {
    let missing = || format!("the position {at} names no layout item");
    let mut out = Vec::new();
    let mut layout = &body.layout;
    for (key, index) in position_steps(at) {
        out.push(key.to_string());
        let Some(i) = index else { continue };
        let item = match (key, layout) {
            ("rows", Layout::Rows(v)) | ("cols", Layout::Cols(v)) => v.get(i),
            ("items", Layout::Grid(g)) => g.items.get(i).map(|cell| &cell.item),
            ("tabs", Layout::Tabs(t)) => {
                let (label, tab) = t.get_index(i).ok_or_else(missing)?;
                out.push(label.clone());
                layout = &tab.body.layout;
                continue;
            }
            _ => None,
        };
        out.push(i.to_string());
        match item.ok_or_else(missing)? {
            LayoutItem::Board(nested) => layout = &nested.body.layout,
            LayoutItem::Chart { .. } => {}
        }
    }
    Ok(out)
}

/// [`authored_path`] in a parsed root layout, before normalize builds it.
pub fn authored_path_parsed(root: Option<&LayoutAuthored>, at: &ItemId) -> Result<Vec<String>, String> {
    let missing = || format!("the position {at} names no layout item");
    let mut out = Vec::new();
    let mut layout = root;
    for (key, index) in position_steps(at) {
        out.push(key.to_string());
        let Some(i) = index else { continue };
        let item = match (key, layout) {
            ("rows", Some(LayoutAuthored::Rows(v))) | ("cols", Some(LayoutAuthored::Cols(v))) => v.get(i),
            ("items", Some(LayoutAuthored::Grid(g))) => {
                g.items.iter().flatten().nth(i).and_then(|cell| cell.item.as_ref())
            }
            ("tabs", Some(LayoutAuthored::Tabs(t))) => {
                let (label, tab) = t.get_index(i).ok_or_else(missing)?;
                out.push(label.clone());
                layout = tab.body.layout.as_ref();
                continue;
            }
            _ => None,
        };
        out.push(i.to_string());
        match item.ok_or_else(missing)? {
            LayoutItemAuthored::Board(nested) => layout = nested.body.layout.as_ref(),
            LayoutItemAuthored::Chart(_) | LayoutItemAuthored::InlineChart(_) => {}
        }
    }
    Ok(out)
}

/// The items of `body`'s own layout, tab panes' layouts included, in authored order, one level
/// of the body's own layout with each item's tab pane and grid cell. A nested
/// board's items are `items(&nested.body, &placement.id)`; `at` is where `body` sits
/// ([`ItemId::root`] for the root board), so a caller descends only where it means to.
pub fn items<'a>(body: &'a BoardBody, at: &ItemId) -> impl Iterator<Item = Placement<'a>> {
    let mut out = Vec::new();
    walk(&body.layout, at.clone(), &mut Vec::new(), &mut out);
    out.into_iter()
}

fn walk<'a>(layout: &'a Layout, at: ItemId, pane: &mut Vec<usize>, out: &mut Vec<Placement<'a>>) {
    let mut place = |id: ItemId, item: &'a LayoutItem, cell: Option<&'a GridItem>| {
        out.push(Placement {
            id,
            item,
            pane: pane.clone(),
            cell,
        })
    };
    match layout {
        Layout::Rows(v) => v
            .iter()
            .enumerate()
            .for_each(|(i, item)| place(at.key("rows").index(i), item, None)),
        Layout::Cols(v) => v
            .iter()
            .enumerate()
            .for_each(|(i, item)| place(at.key("cols").index(i), item, None)),
        Layout::Grid(g) => g.items.iter().enumerate().for_each(|(i, cell)| {
            let id = at.key("grid").key("items").index(i).key("item");
            place(id, &cell.item, Some(cell));
        }),
        Layout::Tabs(t) => {
            for (i, tab) in t.values().enumerate() {
                pane.push(i);
                walk(&tab.body.layout, at.key("tabs").index(i), pane, out);
                pane.pop();
            }
        }
    }
}

/// Gives every chart placement in `layout`, nested boards and tab panes included, the `visible:`
/// its chart authored. `charts` is the root's map, which holds every chart of the board.
pub fn place_visible(layout: &mut Layout, charts: &IndexMap<String, Chart>) {
    let place = |item: &mut LayoutItem| match item {
        LayoutItem::Chart { id, visible } => {
            *visible = charts
                .get(id.as_str())
                .and_then(Chart::base)
                .and_then(|base| base.visible.clone());
        }
        LayoutItem::Board(b) => place_visible(&mut b.body.layout, charts),
    };
    match layout {
        Layout::Rows(items) | Layout::Cols(items) => items.iter_mut().for_each(place),
        Layout::Grid(g) => g.items.iter_mut().for_each(|cell| place(&mut cell.item)),
        Layout::Tabs(t) => t
            .values_mut()
            .for_each(|tab| place_visible(&mut tab.body.layout, charts)),
    }
}
