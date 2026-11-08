//! How a board arranges what it holds: `rows` / `cols` / `grid` / `tabs`, spelled as
//! sibling authored keys, and the items they place.
//!
//! An item is a chart by [`ChartId`] or a nested board. An inline chart is a third arm of the
//! authored form only (`LayoutItemAuthored::InlineChart`): normalize hoists it into
//! `Board.charts` under a tree-position id and the built layout holds that id.

use crate::board::value::{Length, Visible};
use crate::board::BoardBody;
use crate::chart::Chart;
use crate::facet::{DisplayText, Markdown};
use crate::ids::{ChartId, ItemId};
use crate::string_enum;
use crate::style::style::Style;
use model_core::{build_payload, Build, Cx, FromNode, Leaf, Merge, Node, TypeExpr, Unset, Walk};
use model_derive::Model;
use yaml::{Kind, Value};

string_enum!(
    /// Which edge the tab bar sits on.
    TabPosition { Top = "top", Left = "left" }
);

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
    /// Tabbed navigation where each tab contains its own layout.
    Tabs(TabLayout),
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
    /// A chart written in place: a mapping with a `type`, or `{name: {type: ..}}` (Python keeps
    /// the name for nothing: the hoisted id is the item's position). Not buildable until
    /// normalize hoists it.
    InlineChart(Box<<Chart as Leaf>::Whole>),
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
    fn build(slot: Self::Patch, at: &str) -> Result<Self, Unset> {
        Self::build_authored(slot, at)
    }
    fn build_authored(slot: Self::Authored, at: &str) -> Result<Self, Unset> {
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
                // A lone key that is no board key names a chart. Python takes the chart arm whenever the board arm
                // fails, so `{title: {type: ..}}` is a chart there and a refused board here.
                Some((name, entry))
                    if m.len() == 1
                        && matches!(entry.value.kind, Kind::Map(_))
                        && !<NestedBoard as FromNode>::keys()
                            .iter()
                            .any(|k| k == name.as_str()) =>
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

/// One tab: a title and a layout of its own.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TabItem {
    /// Tab label displayed in the tab bar.
    pub title: DisplayText,
    /// Icon shown in the tab (an emoji or icon name).
    #[model(absent = "no icon")]
    pub icon: Option<String>,
    /// Optional metadata for AI search, emitted into the SVG DOM as `data-layout-notes`;
    /// never painted.
    #[model(absent = "no notes")]
    pub notes: Option<DisplayText>,
    /// Markdown text content shown in this tab.
    #[model(absent = "no text")]
    pub text: Option<Markdown>,
    /// Appearance overrides for this tab's content area.
    #[model(absent = "the board's style applies")]
    pub style: Option<Box<Style>>,
    /// The tab's content: one of `rows`, `cols`, `grid`, `tabs`.
    #[model(keys, fill = fill_rows)]
    pub layout: Layout,
}

/// Tab layout configuration. `id` names the variable and URL parameter that select a tab.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TabLayout {
    /// Variable name and URL param base for tab selection.
    #[model(absent = "generated from the board id")]
    pub id: Option<String>,
    /// Which edge the tab bar sits on.
    #[model(fill = "top")]
    pub position: TabPosition,
    /// Title of the tab opened on load.
    #[model(absent = "the first tab opens")]
    pub default: Option<DisplayText>,
    /// Tabs in display order.
    pub items: Vec<TabItem>,
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

/// The items of `body`'s own layout, tab panes' layouts included, in authored order. A nested
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
            for (i, tab) in t.items.iter().enumerate() {
                pane.push(i);
                walk(&tab.layout, at.key("tabs").key("items").index(i), pane, out);
                pane.pop();
            }
        }
    }
}
