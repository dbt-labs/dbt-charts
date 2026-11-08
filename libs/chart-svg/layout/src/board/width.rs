//! Board widths: each board's intrinsic width bottom-up, the root container's
//! width, and every drawn item's width top-down (SL W1–W16, L1–L6).

use crate::board::tree::{Content, Dim, Item, Kind, Node};

/// What a contributor needs horizontally, as CSS intrinsic sizing: `min` the
/// width it must have, `preferred` the width it asks for; `None` is absent.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct IntrinsicWidth {
    pub min: Option<f64>,
    pub preferred: Option<f64>,
}

impl IntrinsicWidth {
    pub const NONE: IntrinsicWidth = IntrinsicWidth {
        min: None,
        preferred: None,
    };

    /// This width inside `chrome` px of padding and margin; absent stays absent.
    pub fn inset(self, chrome: f64) -> Self {
        Self {
            min: self.min.map(|w| w + chrome),
            preferred: self.preferred.map(|w| w + chrome),
        }
    }

    /// Contributors sharing one box: it fits the widest of each field.
    pub fn combine_max(self, other: Self) -> Self {
        let max = |a: Option<f64>, b: Option<f64>| match (a, b) {
            (Some(a), Some(b)) => Some(a.max(b)),
            (a, b) => a.or(b),
        };
        Self {
            min: max(self.min, other.min),
            preferred: max(self.preferred, other.preferred),
        }
    }
}

/// The page frame a hugged root is bounded by.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Frame {
    pub max_width: f64,
    pub margin: f64,
}

/// A board's intrinsic width: its own content combined with its layout's items
/// folded per layout type; a nested board adds its chrome, the root does not
/// (its page margin is the container's). `max_content_width` resolves a
/// percentage item width; `card_gap` joins every gap.
pub fn intrinsic(
    board: &Node,
    is_root: bool,
    max_content_width: f64,
    card_gap: f64,
) -> IntrinsicWidth {
    let width = content_intrinsic(board, max_content_width, card_gap, false);
    if is_root {
        width
    } else {
        width.inset(board.chrome)
    }
}

/// A board's own content combined with its layout, before its chrome.
fn content_intrinsic(
    board: &Node,
    max_content_width: f64,
    card_gap: f64,
    without_data: bool,
) -> IntrinsicWidth {
    board.own.combine_max(layout_intrinsic(
        board,
        max_content_width,
        card_gap,
        without_data,
    ))
}

/// The items' widths folded by the layout type. Inside tabs, and below them,
/// every item is measured as it is without query data, so the board's width
/// does not depend on which tab is open.
fn layout_intrinsic(
    board: &Node,
    max_content_width: f64,
    card_gap: f64,
    without_data: bool,
) -> IntrinsicWidth {
    let without_data = without_data || matches!(board.kind, Kind::Tabs { .. });
    let items: Vec<&Item> = board
        .items
        .iter()
        .filter(|item| {
            if without_data {
                item.drawn_without_data
            } else {
                item.drawn
            }
        })
        .collect();
    let widths: Vec<IntrinsicWidth> = items
        .iter()
        .map(|item| item_intrinsic(board.kind, item, max_content_width, card_gap, without_data))
        .collect();
    let gap = board.hug_gap + card_gap;
    IntrinsicWidth {
        min: fold(
            board.kind,
            &items,
            &widths.iter().map(|w| w.min).collect::<Vec<_>>(),
            gap,
        ),
        preferred: fold(
            board.kind,
            &items,
            &widths.iter().map(|w| w.preferred).collect::<Vec<_>>(),
            gap,
        ),
    }
}

/// An item's own answer. An authored width pins it, except a percentage in
/// `rows`, which is a fraction of the row at assignment and would compound
/// with it here.
fn item_intrinsic(
    kind: Kind,
    item: &Item,
    max_content_width: f64,
    card_gap: f64,
    without_data: bool,
) -> IntrinsicWidth {
    if let Some(width) = item.width {
        let percent_in_rows = kind == Kind::Rows && matches!(width, Dim::Percent(_));
        if !percent_in_rows {
            return IntrinsicWidth {
                min: None,
                preferred: Some(width.resolve(max_content_width)),
            };
        }
    }
    match &item.content {
        Content::Chart(chart) if without_data => chart.without_data,
        Content::Chart(chart) => chart.intrinsic,
        Content::Board(board) => {
            content_intrinsic(board, max_content_width, card_gap, without_data).inset(board.chrome)
        }
    }
}

/// One field of the layout from its items'. `rows` and `tabs` take the widest;
/// `cols` the sum, with gaps only between present values; `grid` the widest
/// per-track demand spread over every column.
fn fold(kind: Kind, items: &[&Item], values: &[Option<f64>], gap: f64) -> Option<f64> {
    let present: Vec<f64> = values.iter().flatten().copied().collect();
    if present.is_empty() {
        return None;
    }
    match kind {
        Kind::Cols => Some(present.iter().sum::<f64>() + gap * (present.len() - 1) as f64),
        Kind::Grid { columns } => {
            let demands: Vec<f64> = items
                .iter()
                .zip(values)
                .filter_map(|(item, value)| {
                    let width = (*value)?;
                    let (start, span) = item.place.map_or((0, 1), |place| (place.col.unwrap_or(0), place.col_span));
                    let covered = (start + span)
                        .min(columns)
                        .checked_sub(start)
                        .filter(|&n| n > 0)?;
                    Some((width - gap * (covered - 1) as f64).max(0.0) / covered as f64)
                })
                .collect();
            let track = demands.into_iter().reduce(f64::max)?;
            Some(span_width(track, columns, gap))
        }
        Kind::Rows | Kind::Tabs { .. } => present.into_iter().reduce(f64::max),
    }
}

/// The width `span` equal tracks of `track` take with `gap` between them.
fn span_width(track: f64, span: usize, gap: f64) -> f64 {
    track * span as f64 + gap * span.saturating_sub(1) as f64
}

/// The root container's width: the authored root `width:` exactly, even past the
/// frame; else the hug, `max(min, preferred) + 2·margin` capped at the frame's
/// `max_width`, a board preferring nothing taking the whole frame.
pub fn container_width(authored: Option<f64>, root: IntrinsicWidth, frame: &Frame) -> f64 {
    if let Some(width) = authored {
        return width;
    }
    let max_content = (frame.max_width - 2.0 * frame.margin).max(0.0);
    let preferred = root.preferred.unwrap_or(max_content);
    let minimum = root.min.unwrap_or(0.0);
    frame
        .max_width
        .min(preferred.max(minimum) + 2.0 * frame.margin)
}

/// A drawn item's width, and for a nested board its own items' widths inside
/// its chrome.
#[derive(Debug, Clone, PartialEq)]
pub struct Placed {
    pub width: f64,
    pub board: Option<Vec<Option<Placed>>>,
}

/// Every item's width in a board `available` wide (its content width), `None`
/// for an item not drawn. `rows` give an item its authored width, capped at the
/// row, else the row; `cols` keep explicit widths and split the rest; `grid`
/// spans equal tracks; `tabs` give every pane the width beside the tab rail.
pub fn item_widths(board: &Node, available: f64, card_gap: f64) -> Vec<Option<Placed>> {
    let drawn: Vec<&Item> = board.items.iter().filter(|item| item.drawn).collect();
    let gap = board.gap + card_gap;
    let widths: Vec<f64> = match board.kind {
        Kind::Rows => drawn
            .iter()
            .map(|item| rows_item_width(item, available))
            .collect(),
        Kind::Cols => {
            let content = available - gap * drawn.len().saturating_sub(1) as f64;
            let specified: Vec<Option<f64>> = drawn
                .iter()
                .map(|item| item.width.map(|w| w.resolve(content)))
                .collect();
            cols_widths(&specified, content)
        }
        Kind::Grid { columns } => {
            let track = (available - gap * columns.saturating_sub(1) as f64) / columns as f64;
            drawn
                .iter()
                .map(|item| span_width(track, item.place.map_or(1, |place| place.col_span), gap))
                .collect()
        }
        Kind::Tabs { pane_offset, .. } => vec![available - pane_offset; drawn.len()],
    };
    let mut placed = drawn.iter().zip(widths).map(|(item, width)| {
        let nested = match &item.content {
            Content::Board(inner) => Some(item_widths(inner, width - inner.chrome, card_gap)),
            Content::Chart(_) => None,
        };
        Placed {
            width,
            board: nested,
        }
    });
    board
        .items
        .iter()
        .map(|item| if item.drawn { placed.next() } else { None })
        .collect()
}

/// A rows item's authored width (the item's, else its chart's own `width:`),
/// capped at the row; else the row.
fn rows_item_width(item: &Item, available: f64) -> f64 {
    let authored = item
        .width
        .map(|w| w.resolve(available))
        .or(match &item.content {
            Content::Chart(chart) => chart.width,
            Content::Board(_) => None,
        });
    match authored {
        Some(width) if width > 0.0 => width.min(available),
        _ => available,
    }
}

/// Explicit column widths kept, auto columns sharing what is left. When the
/// explicit widths leave nothing, each auto column takes an equal share of the
/// row and the explicit ones scale into the rest, so no auto column draws 0 px.
fn cols_widths(specified: &[Option<f64>], content: f64) -> Vec<f64> {
    let total: f64 = specified.iter().flatten().sum();
    let autos = specified.iter().filter(|w| w.is_none()).count();
    if autos == 0 {
        return specified.iter().flatten().copied().collect();
    }
    let remaining = content - total;
    if remaining > 0.0 {
        let auto = remaining / autos as f64;
        return specified.iter().map(|w| w.unwrap_or(auto)).collect();
    }
    let share = content / specified.len() as f64;
    let budget = (content - share * autos as f64).max(0.0);
    let scale = if total > 0.0 { budget / total } else { 0.0 };
    specified
        .iter()
        .map(|w| w.map_or(share, |w| w * scale))
        .collect()
}
