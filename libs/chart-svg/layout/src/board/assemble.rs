//! The layout tree from board-model's board: each body's layout, items, cells,
//! drawn flags, gaps and chrome, read from the board, bind's drawn set and each
//! body's style, then the board's widths, heights and boxes over it (spec § 6.5
//! steps 1, 2, 5 and 6).
//!
//! Style is read once per body, through [`board_geometry`], from the body's
//! effective style patch (theme, `extends:` and its own `style:` as the spine
//! folds them) until the spine hands layout each body's total style, when that
//! function alone changes: a leaf the patch leaves unset is an error naming its
//! path, never a default. What text measures to (a body's own width, band and
//! title) and what each chart needs come from [`Facts`].

use bind::model::DrawnSet;
use board_model::board::layout::{GridItem, Layout, LayoutItem, Placement};
use board_model::board::value::Length;
use board_model::board::{items, Board, BoardBody};
use board_model::ids::{ChartId, ItemId};
use board_model::primitives::SpacingValuesPatch;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use model_core::{Leaf, Tri};

use crate::board::height::{boxes, measure, Boxed, NaturalPlan};
use crate::board::tree::{Chart, Content, Dim, Item, Kind, Node, Place, Title};
use crate::board::width::{container_width, intrinsic, Frame, IntrinsicWidth};

/// A body's effective style patch: the theme, its `extends:` and its own `style:`.
pub type StylePatch = <Style as Leaf>::Patch;

/// What a body's text measures to, at the content width its box gives it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Own {
    /// Its title and prose, and at the root the footer row (`Node::own`).
    pub width: IntrinsicWidth,
    /// Its title, prose and variables above its layout, with the gap below them.
    pub band: f64,
    pub title: Title,
}

/// The measured and data-derived facts the tree holds but the board does not.
pub trait Facts {
    /// The effective style patch `body` lays out with.
    fn style(&self, body: &BoardBody) -> Result<&StylePatch, Diagnostic>;
    /// `body`'s own text, measured. `root` is the board's own body.
    fn own(&mut self, body: &BoardBody, root: bool) -> Result<Own, Diagnostic>;
    /// The root's own title and text above its layout, as sizing reserves them: the height of the
    /// bands at the root's content width, 0 when it has none. Only the root's depends on a width
    /// the tree decides (the hug), so it is asked after the container width is known.
    fn root_band(&mut self, body: &BoardBody, content_width: f64) -> Result<f64, Diagnostic>;
    /// The width facts of the chart `id` drawn at `at`.
    fn chart(&mut self, at: &ItemId, id: &ChartId) -> Result<Chart, Diagnostic>;
    /// A drawn chart's natural plan at a width.
    fn natural(&mut self, chart: &Chart, width: f64) -> Result<NaturalPlan, Diagnostic>;
}

/// The tree's item ids beside its items, nested as the tree nests.
#[derive(Debug, Clone, PartialEq)]
pub struct Ids {
    pub items: Vec<(ItemId, Held)>,
}

/// What an item of the tree holds, by its board-model id.
#[derive(Debug, Clone, PartialEq)]
pub enum Held {
    Chart(ChartId),
    Board(Ids),
}

/// One drawn chart's box, in its board's layout area (`Boxed`).
#[derive(Debug, Clone, PartialEq)]
pub struct Placed {
    pub item: ItemId,
    pub chart: ChartId,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// One board's drawn items in layout order, each in the box its layout gives it.
#[derive(Debug, Clone, PartialEq)]
pub struct LaidBoard {
    pub kind: Kind,
    /// What separates one item's edge from the next: the board's gap and the card gap.
    pub pitch: f64,
    pub items: Vec<LaidItem>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum LaidItem {
    Chart(Placed),
    Board(PlacedBoard),
}

/// A nested board's box in its parent's layout area, and its own items inside its content area.
#[derive(Debug, Clone, PartialEq)]
pub struct PlacedBoard {
    pub item: ItemId,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub layout: LaidBoard,
}

/// The board laid out: its container width and height and every drawn item's box.
#[derive(Debug, Clone, PartialEq)]
pub struct Laid {
    pub width: f64,
    pub height: f64,
    pub root: LaidBoard,
    pub diagnostics: Vec<Diagnostic>,
}

impl Laid {
    /// Every drawn chart's box, in reading order, each in its own board's layout area.
    pub fn charts(&self) -> Vec<&Placed> {
        fn collect<'a>(board: &'a LaidBoard, out: &mut Vec<&'a Placed>) {
            for item in &board.items {
                match item {
                    LaidItem::Chart(chart) => out.push(chart),
                    LaidItem::Board(nested) => collect(&nested.layout, out),
                }
            }
        }
        let mut out = Vec::new();
        collect(&self.root, &mut out);
        out
    }
}

/// Lays `board` out: its tree, the container width (its `width:` or the hug in
/// the page frame), every board's height, then every drawn chart's box.
pub fn lay_out(board: &Board, drawn: &DrawnSet, facts: &mut impl Facts) -> Result<Laid, Diagnostic> {
    let body = &board.body;
    let geometry = board_geometry(facts.style(body)?)?;
    let frame = Frame { max_width: geometry.max_width, margin: geometry.frame_margin };
    let card_gap = if body.card_gap { geometry.card_gap } else { 0.0 };
    let (mut root, ids) = node(body, &geometry, &ItemId::root(), true, drawn, facts)?;
    let max_content = (frame.max_width - 2.0 * frame.margin).max(0.0);
    let authored = board.width.map(|w| w.px());
    let width = container_width(authored, intrinsic(&root, true, max_content, card_gap), &frame);
    let available = (width - 2.0 * frame.margin).max(0.0);
    // `calculate_data_aware_layout`: a titled board's height also carries its title and the gaps below it.
    let band = facts.root_band(body, available)?;
    if band > 0.0 {
        root.band = band + root.hug_gap + card_gap;
    }
    let mut natural = |chart: &Chart, at: f64| facts.natural(chart, at);
    let measured = measure(&root, available, card_gap, &mut natural)?;
    let laid = boxes(&root, &measured, measured.height, card_gap);
    let root = collect(&root, &ids, &laid.items, card_gap);
    Ok(Laid { width, height: measured.height, root, diagnostics: laid.diagnostics })
}

fn collect(node: &Node, ids: &Ids, boxes: &[Option<Boxed>], card_gap: f64) -> LaidBoard {
    let mut items = Vec::new();
    for ((item, (id, held)), boxed) in node.items.iter().zip(&ids.items).zip(boxes) {
        let Some(boxed) = boxed else { continue };
        match (&item.content, held, &boxed.board) {
            (Content::Chart(_), Held::Chart(chart), _) => items.push(LaidItem::Chart(Placed {
                item: id.clone(),
                chart: chart.clone(),
                x: boxed.x,
                y: boxed.y,
                width: boxed.width,
                height: boxed.height,
            })),
            (Content::Board(inner_node), Held::Board(inner_ids), Some(inner_boxes)) => {
                items.push(LaidItem::Board(PlacedBoard {
                    item: id.clone(),
                    x: boxed.x,
                    y: boxed.y,
                    width: boxed.width,
                    height: boxed.height,
                    layout: collect(inner_node, inner_ids, inner_boxes, card_gap),
                }))
            }
            _ => {}
        }
    }
    LaidBoard { kind: node.kind, pitch: node.gap + card_gap, items }
}

/// `body` as a tree node: the root takes the page frame's minimum height and
/// keeps its chrome outside (the frame's margin is the container's); a nested
/// body adds its padding, border and margin and fills the box it is given.
fn node(
    body: &BoardBody,
    geometry: &BoardGeometry,
    at: &ItemId,
    root: bool,
    drawn: &DrawnSet,
    facts: &mut impl Facts,
) -> Result<(Node, Ids), Diagnostic> {
    if body.details.is_some() {
        return Err(Diagnostic::refused(refuse!("laying out a details board is not supported")));
    }
    let (kind, layout_gap) = match &body.layout {
        Layout::Rows(_) => (Kind::Rows, geometry.rows_gap),
        Layout::Cols(_) => (Kind::Cols, geometry.cols_gap),
        Layout::Grid(g) => (Kind::Grid { columns: g.columns as usize }, geometry.grid_gap),
        Layout::Tabs(_) => return Err(Diagnostic::refused(refuse!("laying out a tabs board is not supported"))),
    };
    // `get_board_gap`: card gap owns the spacing when it is on.
    let hug_gap = if body.card_gap { 0.0 } else { layout_gap };
    let own_gap = geometry.gap.unwrap_or(0.0);
    let (chrome, chrome_height, chrome_top) = if root { (0.0, 0.0, 0.0) } else { geometry.chrome() };
    let min_height = if root { geometry.min_height } else { 0.0 };
    let own = facts.own(body, root)?;
    let mut node_items = Vec::new();
    let mut ids = Vec::new();
    for placement in items(body, at) {
        let (item, inner) = item(body, &placement, drawn, facts)?;
        node_items.push(item);
        ids.push((placement.id, inner));
    }
    let node = Node {
        kind,
        items: node_items,
        hug_gap,
        gap: if root { hug_gap } else { own_gap },
        own: own.width,
        chrome,
        chrome_height,
        chrome_top,
        title: own.title,
        band: own.band,
        min_height,
        fill_height: !root,
        details: None,
    };
    Ok((node, Ids { items: ids }))
}

fn item(parent: &BoardBody, placement: &Placement, drawn: &DrawnSet, facts: &mut impl Facts) -> Result<(Item, Held), Diagnostic> {
    let is_drawn = !drawn.is_hidden(&placement.id);
    let place = match (placement.cell, &parent.layout) {
        (Some(grid), Layout::Grid(layout)) => Some(grid_place(grid, layout.columns as usize)?),
        _ => None,
    };
    let (width, height, content, ids) = match placement.item {
        LayoutItem::Chart { id, .. } => {
            let chart = facts.chart(&placement.id, id)?;
            (None, None, Content::Chart(chart), Held::Chart(id.clone()))
        }
        LayoutItem::Board(nested) => {
            let geometry = board_geometry(facts.style(&nested.body)?)?;
            let (node, ids) = node(&nested.body, &geometry, &placement.id, false, drawn, facts)?;
            (nested.width.map(dim), nested.height.map(dim), Content::Board(node), Held::Board(ids))
        }
    };
    let item = Item { width, height, place, drawn: is_drawn, drawn_without_data: is_drawn, content };
    Ok((item, ids))
}

/// A grid item's place: its row and column as written, spans falling back from
/// `col_span` to `width` to one (a 0 is unset). A span wider than the grid has no
/// position production can flow it to, and is refused by name.
fn grid_place(grid: &GridItem, columns: usize) -> Result<Place, Diagnostic> {
    let span = |span: Option<u32>, alias: Option<u32>| span.filter(|s| *s > 0).or(alias.filter(|s| *s > 0)).unwrap_or(1) as usize;
    let index = |v: Option<i64>, name: &str| {
        v.map(|v| usize::try_from(v).map_err(|_| diag::err_internal(format!("grid {name} {v} is negative")))).transpose()
    };
    let place = Place {
        row: index(grid.row, "row")?,
        col: index(grid.col, "col")?,
        row_span: span(grid.row_span, grid.height),
        col_span: span(grid.col_span, grid.width),
    };
    if place.col_span > columns {
        return Err(Diagnostic::refused(refuse!("a grid item spanning more columns than its grid is not supported")));
    }
    Ok(place)
}

fn dim(length: Length) -> Dim {
    match length {
        Length::Px(px) => Dim::Px(px.get()),
        Length::Percent(percent) => Dim::Percent(percent),
    }
}

/// Spacing on each side of a box.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Sides {
    pub top: f64,
    pub right: f64,
    pub bottom: f64,
    pub left: f64,
}

/// Everything board layout reads from a body's style.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BoardGeometry {
    /// `style.frame`: the page frame, which only the root reads.
    pub max_width: f64,
    pub frame_margin: f64,
    pub min_height: f64,
    pub card_gap: f64,
    /// `style.layout.<type>.gap`.
    pub rows_gap: f64,
    pub cols_gap: f64,
    pub grid_gap: f64,
    /// `style.gap`, which pitches a nested body's items; `None` when unset.
    pub gap: Option<f64>,
    /// `style.padding` and `style.margin`, a side zero when unset, and `style.border.width`.
    pub padding: Sides,
    pub margin: Sides,
    pub border_width: f64,
}

impl BoardGeometry {
    /// A nested body's chrome across, down, and above its content:
    /// `effective_padding` (padding plus the border's width on each side) plus its margin.
    fn chrome(&self) -> (f64, f64, f64) {
        let (p, m, b) = (self.padding, self.margin, self.border_width);
        (
            p.left + p.right + 2.0 * b + m.left + m.right,
            p.top + p.bottom + 2.0 * b + m.top + m.bottom,
            m.top + p.top + b,
        )
    }
}

/// The one read of a body's style for board layout. A leaf the style leaves
/// unset is an error naming its path, never a default.
pub fn board_geometry(style: &StylePatch) -> Result<BoardGeometry, Diagnostic> {
    // `SpacingValues`: a side the block does not write is 0, as is an unwritten block.
    let sides = |spacing: &Tri<SpacingValuesPatch>| match spacing {
        Tri::Value(s) => Sides {
            top: s.top.unwrap_or(0.0),
            right: s.right.unwrap_or(0.0),
            bottom: s.bottom.unwrap_or(0.0),
            left: s.left.unwrap_or(0.0),
        },
        Tri::Unset | Tri::Null => Sides::default(),
    };
    Ok(BoardGeometry {
        max_width: leaf(style.frame.max_width, "style.frame.max_width")?,
        frame_margin: leaf(style.frame.margin, "style.frame.margin")?,
        min_height: leaf(style.frame.min_height, "style.frame.min_height")?,
        card_gap: leaf(style.frame.card_gap, "style.frame.card_gap")?,
        rows_gap: leaf(style.layout.rows.gap, "style.layout.rows.gap")?,
        cols_gap: leaf(style.layout.cols.gap, "style.layout.cols.gap")?,
        grid_gap: leaf(style.layout.grid.gap, "style.layout.grid.gap")?,
        gap: match style.gap {
            Tri::Value(gap) => Some(gap),
            Tri::Unset | Tri::Null => None,
        },
        padding: sides(&style.padding),
        margin: sides(&style.margin),
        border_width: leaf(style.border.width, "style.border.width")?,
    })
}

/// A style leaf the body sets, or an error naming its path.
pub fn leaf<T>(slot: Option<T>, path: &str) -> Result<T, Diagnostic> {
    slot.ok_or_else(|| diag::err_internal(format!("{path} is unset in the board's style")))
}
