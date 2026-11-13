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
//! title) and what each chart needs come from [`Facts`]. [`Facts::root_band`] is the root's title band
//! at the width the hug decided.

use bind::bound::DrawnSet;
use board_model::board::layout::{GridItem, Layout, LayoutItem, Placement, TabItem, TabLayout};
use board_model::board::value::Length;
use board_model::board::{items, Board, BoardBody};
use board_model::ids::{ChartId, ItemId};
use board_model::primitives::SpacingValuesPatch;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use model_core::{Leaf, Tri};

use crate::board::band::TitleShift;
use crate::board::height::{boxes, measure, Boxed, NaturalPlan};
use crate::board::tree::{Chart, Content, Details, Dim, Item, Kind, Node, Place, Title};
use crate::board::width::IntrinsicWidth as Width;
use crate::board::width::{container_width, intrinsic, item_widths, Frame, IntrinsicWidth};

/// A body's effective style patch: the theme, its `extends:` and its own `style:`.
pub type StylePatch = <Style as Leaf>::Patch;

/// What a body's text measures to: its title and prose, and at the root the footer row (`Node::own`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Own {
    pub width: IntrinsicWidth,
}

/// What a nested board's own title, prose and variables take above its layout at the content width
/// its box gives it: their height with the gap below them, and what the board contributes to the
/// title band of its `cols` row.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NestedBand {
    pub band: f64,
    pub title: Title,
}

impl NestedBand {
    /// A board with no title, prose or variables of its own.
    pub const NONE: Self = Self { band: 0.0, title: Title::None };
}

/// The measured and data-derived facts the tree holds but the board does not.
pub trait Facts {
    /// The effective style patch `body` lays out with.
    fn style(&self, body: &BoardBody) -> Result<&StylePatch, Diagnostic>;
    /// `body`'s own text, measured. `root` is the board's own body.
    fn own(&mut self, at: &ItemId, body: &BoardBody, root: bool) -> Result<Own, Diagnostic>;
    /// The root's own title and text above its layout, as sizing reserves them: the height of the
    /// bands at the root's content width, 0 when it has none. Only the root's depends on a width
    /// the tree decides (the hug), so it is asked after the container width is known.
    fn root_band(&mut self, body: &BoardBody, content_width: f64) -> Result<f64, Diagnostic>;
    /// The own band of the nested board at `at` (a tab's pane has none) at its content width, known
    /// once the container's widths are placed.
    fn nested_band(&mut self, at: &ItemId, content_width: f64, has_items: bool) -> Result<NestedBand, Diagnostic>;
    /// The width facts of the chart `id` drawn at `at`.
    fn chart(&mut self, at: &ItemId, id: &ChartId) -> Result<Chart, Diagnostic>;
    /// A drawn chart's natural plan at a width.
    fn natural(&mut self, chart: &Chart, width: f64) -> Result<NaturalPlan, Diagnostic>;
    /// The effective style patch tab `tab` of the tabs board `body` lays out with.
    fn pane_style(&self, body: &BoardBody, tab: usize) -> Result<&StylePatch, Diagnostic>;
    /// Where the pane of `body`'s tabs layout starts below its tab strip.
    fn pane_top(&mut self, body: &BoardBody, tabs: &TabLayout) -> Result<f64, Diagnostic>;
    /// The height of `body`'s details summary bar.
    fn summary_height(&mut self, body: &BoardBody) -> Result<f64, Diagnostic>;
    /// How far below its bar a details section's content sits (`layout.details.content_y_offset`).
    fn details_offset(&mut self, body: &BoardBody) -> Result<f64, Diagnostic>;
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
    /// Whether a board other than the root holds it.
    pub nested: bool,
    /// Where its `cols` row moves its title and body.
    pub shift: TitleShift,
}

/// One board's drawn items in layout order, each in the box its layout gives it.
#[derive(Debug, Clone, PartialEq)]
pub struct LaidBoard {
    /// Where the board that owns this layout sits (a tab pane's position is its tab's).
    pub at: ItemId,
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
    /// Where its `cols` row moves its title and body.
    pub shift: TitleShift,
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

/// A closed tab is built so the board hugs its width, but the run records nothing about a board inside
/// it, however deep (`bind::board::hide` does not enter a closed tab): a tabs or details board there
/// has no open tab or section to read, so it is left out, and the engine refuses it by name when the
/// hug would need it.
pub fn opens_a_closed_tab_cannot_hold(item: &LayoutItem) -> bool {
    matches!(item, LayoutItem::Board(nested) if matches!(nested.body.layout, Layout::Tabs(_)) || nested.body.details.is_some())
}

/// `opens_with_text`: whether the first drawn item of a `rows` board is a text block of its own (text,
/// no title, not a details section).
pub fn opens_with_text(body: &BoardBody, at: &ItemId, drawn: &DrawnSet) -> bool {
    if !matches!(body.layout, Layout::Rows(_)) {
        return false;
    }
    let first = items(body, at).find(|placement| !drawn.is_hidden(&placement.id));
    match first.map(|placement| placement.item) {
        Some(LayoutItem::Board(nested)) => nested.body.text.is_some() && nested.body.title.as_ref().is_none_or(|title| title.as_str().is_empty()) && nested.body.details.is_none(),
        _ => false,
    }
}

/// Lays `board` out: its tree, the container width (its `width:` or the hug in
/// the page frame), every board's height, then every drawn chart's box.
pub fn lay_out(board: &Board, drawn: &DrawnSet, facts: &mut impl Facts) -> Result<Laid, Diagnostic> {
    let body = &board.body;
    let geometry = board_geometry(facts.style(body)?)?;
    let frame = Frame { max_width: geometry.max_width, margin: geometry.frame_margin };
    let card_gap = if body.card_gap { geometry.card_gap } else { 0.0 };
    let (mut root, ids) = node(body, &geometry, &ItemId::root(), true, false, drawn, facts)?;
    let max_content = (frame.max_width - 2.0 * frame.margin).max(0.0);
    let authored = board.width.map(|w| w.px());
    let width = container_width(authored, intrinsic(&root, true, max_content, card_gap), &frame);
    let available = (width - 2.0 * frame.margin).max(0.0);
    // `calculate_data_aware_layout`: a titled board's height also carries its title and the gaps below it.
    let band = facts.root_band(body, available)?;
    if band > 0.0 {
        root.band = band + root.hug_gap + card_gap;
    }
    fill_nested_bands(&mut root, &ids, available, card_gap, facts)?;
    let mut natural = |chart: &Chart, at: f64| facts.natural(chart, at);
    let measured = measure(&root, available, card_gap, &mut natural)?;
    let laid = boxes(&root, &measured, measured.height, card_gap);
    let root = collect(&root, &ItemId::root(), &ids, &laid.items, card_gap, false);
    Ok(Laid { width, height: measured.height, root, diagnostics: laid.diagnostics })
}

/// Gives every nested board its own band at the content width its box gets: the widths are placed
/// before any height, and a band moves none of them.
fn fill_nested_bands(board: &mut Node, ids: &Ids, available: f64, card_gap: f64, facts: &mut impl Facts) -> Result<(), Diagnostic> {
    let placed = item_widths(board, available, card_gap);
    for ((item, (at, held)), placed) in board.items.iter_mut().zip(&ids.items).zip(placed) {
        let (Content::Board(inner), Held::Board(inner_ids), Some(placed)) = (&mut item.content, held, placed) else { continue };
        let width = placed.width - inner.chrome;
        let own = facts.nested_band(at, width, !inner.items.is_empty())?;
        inner.band = own.band;
        inner.title = own.title;
        fill_nested_bands(inner, inner_ids, width, card_gap, facts)?;
    }
    Ok(())
}

fn collect(node: &Node, at: &ItemId, ids: &Ids, boxes: &[Option<Boxed>], card_gap: f64, nested: bool) -> LaidBoard {
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
                nested,
                shift: boxed.shift,
            })),
            (Content::Board(inner_node), Held::Board(inner_ids), Some(inner_boxes)) => {
                items.push(LaidItem::Board(PlacedBoard {
                    item: id.clone(),
                    x: boxed.x,
                    y: boxed.y,
                    width: boxed.width,
                    height: boxed.height,
                    shift: boxed.shift,
                    layout: collect(inner_node, id, inner_ids, inner_boxes, card_gap, true),
                }))
            }
            // A collapsed details board is placed with nothing inside it: only its bar is drawn.
            (Content::Board(inner_node), Held::Board(_), None) if inner_node.details.is_some_and(|d| d.collapsed) => {
                items.push(LaidItem::Board(PlacedBoard {
                    item: id.clone(),
                    x: boxed.x,
                    y: boxed.y,
                    width: boxed.width,
                    height: boxed.height,
                    shift: boxed.shift,
                    layout: LaidBoard { at: id.clone(), kind: inner_node.kind, pitch: inner_node.gap + card_gap, items: Vec::new() },
                }))
            }
            _ => {}
        }
    }
    LaidBoard { at: at.clone(), kind: node.kind, pitch: node.gap + card_gap, items }
}

/// `body` as a tree node: the root takes the page frame's minimum height and
/// keeps its chrome outside (the frame's margin is the container's); a nested
/// body adds its padding, border and margin and fills the box it is given.
fn node(
    body: &BoardBody,
    geometry: &BoardGeometry,
    at: &ItemId,
    root: bool,
    closed: bool,
    drawn: &DrawnSet,
    facts: &mut impl Facts,
) -> Result<(Node, Ids), Diagnostic> {
    let details = match &body.details {
        None => None,
        Some(_) if root => return Err(Diagnostic::refused(refuse!("a details root board is not supported"))),
        Some(_) => Some(Details { summary_height: facts.summary_height(body)?, content_offset: facts.details_offset(body)?, collapsed: drawn.details_open(&body.id) != Some(true) }),
    };
    let (kind, layout_gap) = match &body.layout {
        Layout::Tabs(tabs) => (Kind::Tabs { pane_offset: 0.0, pane_top: facts.pane_top(body, tabs)?, rail: 0.0 }, geometry.rows_gap),
        layout => kind_of(layout, geometry),
    };
    // `get_board_gap`: card gap owns the spacing when it is on.
    let hug_gap = if body.card_gap { 0.0 } else { layout_gap };
    let own_gap = geometry.gap.unwrap_or(0.0);
    let (chrome, chrome_height, chrome_top) = if root { (0.0, 0.0, 0.0) } else { geometry.chrome() };
    let min_height = if root { geometry.min_height } else { 0.0 };
    let own = facts.own(at, body, root)?;
    let mut node_items = Vec::new();
    let mut ids = Vec::new();
    // A collapsed details board draws its summary bar alone.
    if details.is_none_or(|d| !d.collapsed) {
        match &body.layout {
            Layout::Tabs(tabs) => {
                let open = drawn.active_tab(&body.id).ok_or_else(|| diag::err_internal(format!("no open tab for the tabs board {}", body.id)))?;
                for (index, tab) in tabs.items.iter().enumerate() {
                    let id = at.key("tabs").key("items").index(index);
                    let (pane, inner) = pane(body, tab, index, closed || index != open, Some(items(body, at).filter(|p| p.pane == [index]).collect::<Vec<_>>()), drawn, facts)?;
                    node_items.push(Item { width: None, height: None, place: None, drawn: index == open, drawn_without_data: true, content: Content::Board(pane) });
                    ids.push((id, Held::Board(inner)));
                }
            }
            layout => {
                // A hidden item takes no space, and nothing of it is measured.
                for placement in items(body, at).filter(|p| !drawn.is_hidden(&p.id) && !(closed && opens_a_closed_tab_cannot_hold(p.item))) {
                    let (item, inner) = item(layout, &placement, closed, drawn, facts)?;
                    node_items.push(item);
                    ids.push((placement.id, inner));
                }
            }
        }
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
        title: Title::None,
        band: 0.0,
        min_height,
        fill_height: !root,
        details,
    };
    Ok((node, Ids { items: ids }))
}

fn kind_of(layout: &Layout, geometry: &BoardGeometry) -> (Kind, f64) {
    match layout {
        Layout::Rows(_) => (Kind::Rows, geometry.rows_gap),
        Layout::Cols(_) => (Kind::Cols, geometry.cols_gap),
        Layout::Grid(g) => (Kind::Grid { columns: g.columns as usize }, geometry.grid_gap),
        Layout::Tabs(_) => unreachable!("a tabs layout is not a pane's layout: `pane` refuses it"),
    }
}

/// One tab's pane: a nested board of the tab's own layout, in the tab's scope (`pane_style`), holding
/// `placements` when it is the open tab. A tab with `text`, an icon or notes, and a
/// tabs layout written inside a pane, are refused by name.
fn pane(
    body: &BoardBody,
    tab: &TabItem,
    index: usize,
    closed: bool,
    placements: Option<Vec<Placement>>,
    drawn: &DrawnSet,
    facts: &mut impl Facts,
) -> Result<(Node, Ids), Diagnostic> {
    for (set, what) in [(tab.text.is_some(), "text"), (tab.icon.is_some(), "icon"), (tab.notes.is_some(), "notes")] {
        if set {
            return Err(Diagnostic::refused(refuse!("a tab with its own `{what}` is not supported")));
        }
    }
    if matches!(tab.layout, Layout::Tabs(_)) {
        return Err(Diagnostic::refused(refuse!("a tabs layout inside a tab is not supported")));
    }
    let geometry = board_geometry(facts.pane_style(body, index)?)?;
    let (kind, layout_gap) = kind_of(&tab.layout, &geometry);
    let (chrome, chrome_height, chrome_top) = geometry.chrome();
    let mut node_items = Vec::new();
    let mut ids = Vec::new();
    for placement in placements.into_iter().flatten().filter(|p| !drawn.is_hidden(&p.id) && !(closed && opens_a_closed_tab_cannot_hold(p.item))) {
        let (item, inner) = item(&tab.layout, &placement, closed, drawn, facts)?;
        node_items.push(item);
        ids.push((placement.id, inner));
    }
    let node = Node {
        kind,
        items: node_items,
        hug_gap: if body.card_gap { 0.0 } else { layout_gap },
        gap: geometry.gap.unwrap_or(0.0),
        own: Width::NONE,
        chrome,
        chrome_height,
        chrome_top,
        title: Title::None,
        band: 0.0,
        min_height: 0.0,
        fill_height: true,
        details: None,
    };
    Ok((node, Ids { items: ids }))
}

fn item(layout: &Layout, placement: &Placement, closed: bool, drawn: &DrawnSet, facts: &mut impl Facts) -> Result<(Item, Held), Diagnostic> {
    let place = match (placement.cell, layout) {
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
            let (node, ids) = node(&nested.body, &geometry, &placement.id, false, closed, drawn, facts)?;
            (nested.width.map(dim), nested.height.map(dim), Content::Board(node), Held::Board(ids))
        }
    };
    let item = Item { width, height, place, drawn: true, drawn_without_data: true, content };
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
    pub fn chrome(&self) -> (f64, f64, f64) {
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
            Tri::Value(gap) => Some(gap.px()),
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
