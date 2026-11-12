//! The layout tree board layout reads: what each board arranges, how, and what
//! each item asks for. The facts that come from style, text and charts are
//! resolved by the caller; this tree holds only what the layout arithmetic needs.

use board_model::ids::ItemId;

use crate::board::width::IntrinsicWidth;
use crate::title::TitleMetrics;

/// An authored item dimension.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Dim {
    Px(f64),
    /// Percent of the space it is resolved against.
    Percent(f64),
}

impl Dim {
    pub fn resolve(self, total: f64) -> f64 {
        match self {
            Dim::Px(px) => px,
            Dim::Percent(percent) => percent / 100.0 * total,
        }
    }
}

/// How a board arranges its items.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Kind {
    Rows,
    Cols,
    Grid {
        columns: usize,
    },
    /// `pane_offset` is the tab rail's width beside the pane (0 for a top bar);
    /// `pane_top` the bar's height above it (0 beside a rail); `rail` the height
    /// a left rail needs, which the board is never shorter than (0 for a top bar).
    Tabs {
        pane_offset: f64,
        pane_top: f64,
        rail: f64,
    },
}

/// A board: its layout, its items, the gaps between them (before any card gap),
/// what its own title, prose and footer need, and, nested, the padding, border
/// and margin around its content (`chrome` across, `chrome_height` down). The
/// hug folds its items with `hug_gap`, its layout type's gap, while assignment
/// pitches them with `gap`, which for a nested board is its own `style.gap` (0
/// when unset).
///
/// Heights read `band`, the height of the board's own title, prose and
/// variables above its layout with the gap that separates them from it, measured
/// at the board's content width (the root's band counts like any board's);
/// `min_height`, which its layout is never shorter than (the page frame's at the
/// root, 0 nested); `chrome_top`, the part of `chrome_height` above the content, production's
/// `margin.top + effective_padding.top`;
/// `title`, what the board contributes to the title band of the `cols` row it
/// stands in; `fill_height`, whether its layout takes all the height its
/// box gives it (nested boards do, the root does not); and `details`, when the
/// board is a collapsible section.
#[derive(Debug, Clone, PartialEq)]
pub struct Node {
    pub kind: Kind,
    pub items: Vec<Item>,
    pub hug_gap: f64,
    pub gap: f64,
    pub own: IntrinsicWidth,
    pub chrome: f64,
    pub chrome_height: f64,
    pub chrome_top: f64,
    pub title: Title,
    pub band: f64,
    pub min_height: f64,
    pub fill_height: bool,
    pub details: Option<Details>,
}

/// What a board contributes to a `cols` row's title band.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Title {
    /// A titled board's first baseline and body, or a heading's when prose opens
    /// it, from the board's own top with its chrome.
    Own(TitleMetrics),
    /// An untitled board that only wraps a stack: no title, no text, no visible
    /// variables, and a `rows` or `cols` layout (the caller's predicate). It
    /// draws its first item's title (`rows`) or every item's (`cols`), so those
    /// items are the ones that shift.
    Wraps,
    /// Takes no part: a board whose title shares a band with its controls, one
    /// with text or variables and no title of its own, any other layout.
    None,
}

/// A board drawn as a collapsible section: the summary bar the container draws
/// above it, and whether it is closed, in which case only the bar is drawn.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Details {
    pub summary_height: f64,
    /// How far below the bar the content sits: the section's border ends with it.
    pub content_offset: f64,
    pub collapsed: bool,
}

/// Where a grid item asks to sit: its row and column, if written, and how many
/// of each it spans. Heights place it at both when both are written and flow it
/// otherwise; widths read its column alone (production's two readings). An item
/// with no place is flowed and spans one track.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Place {
    pub row: Option<usize>,
    pub col: Option<usize>,
    pub row_span: usize,
    pub col_span: usize,
}

/// A grid item's placed cell: its first row and column and how many it spans.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Cell {
    pub row: usize,
    pub col: usize,
    pub row_span: usize,
    pub col_span: usize,
}

/// A layout item: its authored width and height (a tab carries neither), its grid place,
/// whether it is drawn (now, and with no query data, which is how tabs are
/// hugged), and what it holds. In a tabs layout, `drawn` is the open tab.
#[derive(Debug, Clone, PartialEq)]
pub struct Item {
    pub width: Option<Dim>,
    pub height: Option<Dim>,
    pub place: Option<Place>,
    pub drawn: bool,
    pub drawn_without_data: bool,
    pub content: Content,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Content {
    Chart(Chart),
    Board(Node),
}

/// A chart's width facts: its own authored `width:`, and its intrinsic width
/// with its data and without (a table measures its columns; with no data it
/// answers its configured width); and the facts the heights read: its placement and id, which
/// charts draw to their content (table, kpi, spark_bar, callout, custom), which
/// is a table, whether it can use spare height, and, for a horizontal bar, the
/// height its categories need. Its natural height at a width is the caller's.
#[derive(Debug, Clone, PartialEq)]
pub struct Chart {
    /// The placement it is drawn at: a chart placed twice is two `Chart`s.
    pub at: ItemId,
    pub id: String,
    pub width: Option<f64>,
    pub intrinsic: IntrinsicWidth,
    pub without_data: IntrinsicWidth,
    pub content_sized: bool,
    pub table: bool,
    pub spare: Spare,
    pub floor: Option<CategoryFloor>,
}

/// Whether a chart can use more height than its natural height.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Spare {
    /// A pie keeps its aspect.
    Never,
    /// A custom chart, or a cartesian or geo chart with no chart-root `height`.
    Always,
    /// Anything else, which takes the slot only when it is the layout's one item.
    WhenAlone,
}

/// The height a horizontal bar's categories need to stay readable.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CategoryFloor {
    pub categories: usize,
    pub min_height: f64,
}
