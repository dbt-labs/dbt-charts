//! Board heights and boxes: each board's height bottom-up from its charts'
//! natural heights, then every drawn item's box top-down in the height its
//! board is given (SL L7–L14, N2–N4, S4, S5).

use std::collections::{BTreeMap, BTreeSet, HashSet};

use diagnostic::{diag, Diagnostic};

use crate::board::band::{row_shifts, TitleShift};
use crate::board::tree::{Cell, Chart, Content, Details, Item, Kind, Node, Spare, Title};
use crate::board::width::{item_widths, Placed};
use crate::title::TitleMetrics;

/// How much taller than its authored `height:` a lone table's slot may grow, to
/// hold every row rather than paginate: one pagination control bar.
const TABLE_PAGINATION_CONTROL_HEIGHT: f64 = 28.0;

/// What a chart needs at a width: its natural height there, its floors and card
/// padding included but not the shift its `cols` row gives it, and where its
/// title sits from its top, `None` for a chart that takes no part in a row's
/// title band (a kpi, a callout, an untitled chart, one that failed).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NaturalPlan {
    pub height: f64,
    pub title: Option<TitleMetrics>,
}

pub type Natural<'a> = dyn FnMut(&Chart, f64) -> Result<NaturalPlan, Diagnostic> + 'a;

/// A board measured: its height, and what each drawn item needs. Heights are
/// measured once; assignment reads them back, so the two cannot disagree.
#[derive(Debug, Clone, PartialEq)]
pub struct Measured {
    /// The board's height: its band, its layout and its vertical chrome.
    pub height: f64,
    /// The width of the board's content, which its columns and tracks divide.
    width: f64,
    /// Per item, `None` when not drawn.
    items: Vec<Option<Slot>>,
    /// Per item, the grid cell it occupies, an auto-placed item's included.
    cells: Vec<Option<Cell>>,
}

#[derive(Debug, Clone, PartialEq)]
struct Slot {
    width: f64,
    /// What the item needs: a chart's natural height at `width`, a board's
    /// height with any details bar above it, and the body shift its `cols` row
    /// gives it.
    content: f64,
    /// A chart's title metrics; a board's are on its node.
    title: Option<TitleMetrics>,
    /// Where the item's title and body move in its `cols` row.
    shift: TitleShift,
    /// A nested board's measure; `None` for a chart, or while collapsed.
    board: Option<Measured>,
}

/// A drawn item's box, in its board's layout area: below the board's band,
/// inside its chrome. A nested board's items are in `board`, likewise relative
/// to its own layout area.
#[derive(Debug, Clone, PartialEq)]
pub struct Boxed {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    /// The shift its `cols` row gives the item. A board's band and layout area
    /// sit `body_dy` below where its chrome puts them.
    pub shift: TitleShift,
    pub board: Option<Vec<Option<Boxed>>>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Boxes {
    /// Per item, `None` when not drawn.
    pub items: Vec<Option<Boxed>>,
    pub diagnostics: Vec<Diagnostic>,
}

/// A board's drawn items with what was measured for each.
struct Drawn<'a> {
    item: &'a Item,
    slot: &'a Slot,
    cell: Option<Cell>,
    /// Its index among all the board's items.
    at: usize,
}

/// Measures `board` in `available` px of content width. `natural` is asked once
/// for each drawn chart, at the width its box will have. Every `cols` row's
/// title band is resolved before any height is, so each cell's height holds its
/// own shift. A chart whose natural height cannot be found is that error.
pub fn measure(board: &Node, available: f64, card_gap: f64, natural: &mut Natural) -> Result<Measured, Diagnostic> {
    let placed = item_widths(board, available, card_gap);
    let mut measured = plan(board, available, &placed, natural)?;
    shift_rows(board, &mut measured);
    finish(board, &mut measured, card_gap);
    Ok(measured)
}

/// The slots of `board`'s drawn items, each chart asked, no height folded yet.
fn plan(
    board: &Node,
    available: f64,
    placed: &[Option<Placed>],
    natural: &mut Natural,
) -> Result<Measured, Diagnostic> {
    let items: Vec<Option<Slot>> = board
        .items
        .iter()
        .zip(placed)
        .map(|(item, placed)| {
            let Some(placed) = placed.as_ref() else { return Ok(None) };
            let (content, title, nested) = match &item.content {
                Content::Chart(chart) => {
                    let plan = natural(chart, placed.width)?;
                    (plan.height, plan.title, None)
                }
                Content::Board(Node {
                    details:
                        Some(Details {
                            collapsed: true,
                            summary_height,
                        }),
                    ..
                }) => (*summary_height, None, None),
                Content::Board(inner) => {
                    let inner_placed = placed
                        .board
                        .as_deref()
                        .expect("item_widths places a nested board's items");
                    let planned = plan(inner, placed.width - inner.chrome, inner_placed, natural)?;
                    (0.0, None, Some(planned))
                }
            };
            Ok(Some(Slot {
                width: placed.width,
                content,
                title,
                shift: TitleShift::default(),
                board: nested,
            }))
        })
        .collect::<Result<_, Diagnostic>>()?;
    let cells = match board.kind {
        Kind::Grid { columns } => place_grid(columns, board, &items),
        _ => vec![None; items.len()],
    };
    Ok(Measured {
        height: 0.0,
        width: available,
        items,
        cells,
    })
}

/// Gives every `cols` row's titled cells their shifts, inner boards first: a
/// row inside an untitled `cols` wrapper shares its parent's band, so the
/// parent's shift is the one that stands.
fn shift_rows(board: &Node, measured: &mut Measured) {
    for (item, slot) in board.items.iter().zip(measured.items.iter_mut()) {
        if let (Content::Board(inner), Some(slot)) = (&item.content, slot) {
            if let Some(planned) = slot.board.as_mut() {
                shift_rows(inner, planned);
            }
        }
    }
    if board.kind != Kind::Cols {
        return;
    }
    let mut targets = Vec::new();
    for (at, (item, slot)) in board.items.iter().zip(&measured.items).enumerate() {
        if let Some(slot) = slot {
            title_targets(item, slot, &mut vec![at], 0.0, &mut targets);
        }
    }
    let shifts = row_shifts(&targets.iter().map(|(_, m)| *m).collect::<Vec<_>>());
    for ((path, _), shift) in targets.iter().zip(shifts) {
        slot_at(measured, path).shift = shift;
    }
}

/// The items that draw a `cols` cell's titles, as paths from the row, with the
/// title metrics each has from the cell's top. A titled chart or board draws its
/// own. An untitled board that only wraps a `cols` draws one title per item, and
/// one that only wraps a `rows` draws its first item's; those items are the
/// targets, `above` px below the cell's top. A details section draws a summary
/// bar, not a title.
fn title_targets(
    item: &Item,
    slot: &Slot,
    path: &mut Vec<usize>,
    above: f64,
    targets: &mut Vec<(Vec<usize>, TitleMetrics)>,
) {
    let shifted = |m: TitleMetrics| TitleMetrics {
        ascent: m.ascent + above,
        tail: m.tail,
    };
    match &item.content {
        Content::Chart(_) => targets.extend(slot.title.map(|m| (path.clone(), shifted(m)))),
        Content::Board(inner) if inner.details.is_some() => {}
        Content::Board(Node {
            title: Title::Own(m),
            ..
        }) => targets.push((path.clone(), shifted(*m))),
        Content::Board(inner) if inner.title == Title::Wraps => {
            let planned = slot.board.as_ref().expect("a drawn board is planned");
            let wrapped = inner
                .items
                .iter()
                .zip(&planned.items)
                .enumerate()
                .filter_map(|(at, (item, slot))| Some((at, item, slot.as_ref()?)));
            let count = if inner.kind == Kind::Cols {
                usize::MAX
            } else {
                1
            };
            for (at, item, slot) in wrapped.take(count) {
                path.push(at);
                title_targets(item, slot, path, above + inner.chrome_top, targets);
                path.pop();
            }
        }
        Content::Board(_) => {}
    }
}

fn slot_at<'a>(measured: &'a mut Measured, path: &[usize]) -> &'a mut Slot {
    let slot = measured.items[path[0]].as_mut().expect("a target is drawn");
    match path[1..] {
        [] => slot,
        _ => slot_at(
            slot.board.as_mut().expect("a wrapper is planned"),
            &path[1..],
        ),
    }
}

/// Folds heights bottom-up: a chart is its natural height and its shift; a
/// board its band, layout and chrome, and its own shift when a `cols` row
/// gave it one.
fn finish(board: &Node, measured: &mut Measured, card_gap: f64) {
    let gap = board.gap + card_gap;
    for (item, slot) in board.items.iter().zip(measured.items.iter_mut()) {
        let Some(slot) = slot else { continue };
        if let (Content::Board(inner), Some(planned)) = (&item.content, slot.board.as_mut()) {
            finish(inner, planned, card_gap);
            slot.content = planned.height + details_chrome(inner, gap);
        }
        slot.content += slot.shift.body_dy;
    }
    measured.height = board.band + layout_height(board, measured, gap) + board.chrome_height;
}

/// The summary bar a details section adds above its content, with the
/// container's gap; a plain board adds nothing.
fn details_chrome(board: &Node, container_gap: f64) -> f64 {
    board
        .details
        .map_or(0.0, |d| d.summary_height + container_gap)
}

fn drawn<'a>(board: &'a Node, measured: &'a Measured) -> Vec<Drawn<'a>> {
    board
        .items
        .iter()
        .zip(&measured.items)
        .zip(&measured.cells)
        .enumerate()
        .filter_map(|(at, ((item, slot), cell))| {
            Some(Drawn {
                item,
                slot: slot.as_ref()?,
                cell: *cell,
                at,
            })
        })
        .collect()
}

/// A layout's height: its drawn items folded per layout type, never under
/// `min_height`; with nothing drawn, `min_height`.
fn layout_height(board: &Node, measured: &Measured, gap: f64) -> f64 {
    let items = drawn(board, measured);
    if items.is_empty() {
        return board.min_height;
    }
    let height = match board.kind {
        Kind::Rows => rows_measure(&items, gap),
        Kind::Cols => {
            let demand = cols_demand(&items, 0.0);
            demand.specified.max(demand.auto)
        }
        Kind::Grid { .. } => {
            let (rows, _) = grid_rows(&items, 0.0);
            rows.values().sum::<f64>() + gap * (rows.len() - 1) as f64
        }
        Kind::Tabs { pane_top, rail, .. } => (pane_top + items[0].slot.content).max(rail),
    };
    height.max(board.min_height)
}

/// An item's authored height in px, `None` when auto. A percentage resolves
/// against `against`; the measure has no room yet and passes 0, so it falls
/// through to the content, and assignment resolves it.
fn authored(item: &Item, against: f64) -> Option<f64> {
    item.height
        .map(|height| height.resolve(against))
        .filter(|&px| px > 0.0)
}

fn rows_measure(items: &[Drawn], gap: f64) -> f64 {
    let heights: f64 = items
        .iter()
        .zip(rows_specified(items, 0.0))
        .map(|(d, specified)| specified.unwrap_or(d.slot.content))
        .sum();
    heights + gap * (items.len() - 1) as f64
}

/// A rows item's authored height as a ceiling over its content, `None` when auto.
fn rows_specified(items: &[Drawn], against: f64) -> Vec<Option<f64>> {
    items
        .iter()
        .map(|d| {
            let authored = authored(d.item, against)?;
            let content = d.slot.content;
            Some(
                if is_single_table(d.item)
                    && content > authored
                    && content - authored <= TABLE_PAGINATION_CONTROL_HEIGHT
                {
                    content
                } else if content > 0.0 {
                    authored.min(content)
                } else {
                    // Nothing inside measures (a color swatch): the box is all there is.
                    authored
                },
            )
        })
        .collect()
}

/// A `height:` wrapper around exactly one table.
fn is_single_table(item: &Item) -> bool {
    matches!(&item.content, Content::Board(inner)
        if matches!(inner.items.as_slice(),
            [Item { content: Content::Chart(chart), .. }] if chart.table))
}

/// What a cols row's cells ask for, in px: the tallest authored height, the
/// tallest content of a cell with none, and the tallest a cell draws.
struct ColsDemand {
    specified: f64,
    auto: f64,
    drawn: f64,
}

/// A percentage height resolves against `available`. A content-sized cell
/// (every chart in it draws to its content) with an authored height reserves
/// the row at that height but draws only its content.
fn cols_demand(items: &[Drawn], available: f64) -> ColsDemand {
    let mut demand = ColsDemand {
        specified: 0.0,
        auto: 0.0,
        drawn: 0.0,
    };
    for d in items {
        match authored(d.item, available) {
            Some(px) if !holds_only_content_sized(d.item) => {
                demand.specified = demand.specified.max(px);
                demand.drawn = demand.drawn.max(px);
            }
            Some(px) => {
                demand.specified = demand.specified.max(px);
                demand.drawn = demand.drawn.max(d.slot.content.min(px));
            }
            None => {
                demand.auto = demand.auto.max(d.slot.content);
                demand.drawn = demand.drawn.max(d.slot.content);
            }
        }
    }
    demand
}

/// Whether every chart in the item, at any depth and drawn or not, draws to its
/// content, and there is at least one.
fn holds_only_content_sized(item: &Item) -> bool {
    fn collect(item: &Item, sized: &mut Vec<bool>) {
        match &item.content {
            Content::Chart(chart) => sized.push(chart.content_sized),
            Content::Board(inner) => inner.items.iter().for_each(|item| collect(item, sized)),
        }
    }
    let mut sized = Vec::new();
    collect(item, &mut sized);
    !sized.is_empty() && sized.iter().all(|&s| s)
}

/// Each occupied grid row's height, and the rows an authored height pins. An
/// item costs each row it spans an equal share of its height; a row costs its
/// costliest item.
fn grid_rows(items: &[Drawn], available: f64) -> (BTreeMap<usize, f64>, BTreeSet<usize>) {
    let mut rows = BTreeMap::new();
    let mut pinned = BTreeSet::new();
    for d in items {
        let cell = d.cell.expect("a grid places every drawn item");
        let height = match authored(d.item, available) {
            Some(px) => {
                pinned.extend(cell.row..cell.row + cell.row_span);
                px
            }
            None => d.slot.content,
        };
        for row in cell.row..cell.row + cell.row_span {
            let share = height / cell.row_span as f64;
            let current: &mut f64 = rows.entry(row).or_insert(0.0);
            *current = current.max(share);
        }
    }
    (rows, pinned)
}

/// Every drawn item's cell (`_calculate_grid_dimensions`): an item with both a row
/// and a column sits there; every other item, in order, takes the first position
/// from the cursor on where all its tracks are free, a row ending where its span
/// would pass the last column, and the cursor moves past it.
fn place_grid(columns: usize, board: &Node, slots: &[Option<Slot>]) -> Vec<Option<Cell>> {
    assert!(columns > 0, "a grid has at least one column");
    let spans = |item: &Item| item.place.map_or((1, 1), |p| (p.row_span, p.col_span));
    let mut taken = HashSet::new();
    let mut cells: Vec<Option<Cell>> = board
        .items
        .iter()
        .zip(slots)
        .map(|(item, slot)| {
            slot.as_ref()?;
            let place = item.place?;
            let (row_span, col_span) = spans(item);
            Some(Cell { row: place.row?, col: place.col?, row_span, col_span })
        })
        .collect();
    for cell in cells.iter().flatten() {
        for row in cell.row..cell.row + cell.row_span {
            taken.extend((cell.col..cell.col + cell.col_span).map(|col| (row, col)));
        }
    }
    let (mut row, mut col) = (0, 0);
    for ((cell, slot), item) in cells.iter_mut().zip(slots).zip(&board.items) {
        if cell.is_some() || slot.is_none() {
            continue;
        }
        let (row_span, col_span) = spans(item);
        assert!(col_span <= columns, "a flowed item fits the grid's columns");
        loop {
            if col + col_span > columns {
                (row, col) = (row + 1, 0);
                continue;
            }
            let free = (row..row + row_span).all(|r| (col..col + col_span).all(|c| !taken.contains(&(r, c))));
            if free {
                break;
            }
            col += 1;
        }
        for r in row..row + row_span {
            taken.extend((col..col + col_span).map(|c| (r, c)));
        }
        *cell = Some(Cell { row, col, row_span, col_span });
        col += col_span;
    }
    cells
}

/// Whether an item can use more height than it needs: a nested board when
/// anything in it can, a chart by its kind, and anything that is `alone` in its
/// layout, a pie excepted.
fn takes_spare(item: &Item, alone: bool) -> bool {
    match &item.content {
        Content::Board(inner) => {
            if inner.details.is_some_and(|d| d.collapsed) {
                return false;
            }
            let drawn: Vec<&Item> = inner.items.iter().filter(|item| item.drawn).collect();
            alone || drawn.iter().any(|sub| takes_spare(sub, drawn.len() == 1))
        }
        Content::Chart(chart) => match chart.spare {
            Spare::Never => false,
            Spare::Always => true,
            Spare::WhenAlone => alone,
        },
    }
}

/// Every drawn item's box when `board`, measured, is `height` tall, and the
/// warnings its charts' floors raise against the slots they got. A chart under
/// an authored `height:`, its own or an ancestor's, whose category floor is
/// taller than its slot warns.
pub fn boxes(board: &Node, measured: &Measured, height: f64, card_gap: f64) -> Boxes {
    let mut diagnostics = Vec::new();
    let items = place(
        board,
        measured,
        (height - board.band - board.chrome_height).max(0.0),
        card_gap,
        false,
        &mut diagnostics,
    );
    Boxes { items, diagnostics }
}

/// The boxes of `board`'s items in a layout area `room` tall.
fn place(
    board: &Node,
    measured: &Measured,
    room: f64,
    card_gap: f64,
    authored_above: bool,
    diagnostics: &mut Vec<Diagnostic>,
) -> Vec<Option<Boxed>> {
    let gap = board.gap + card_gap;
    let items = drawn(board, measured);
    if items.is_empty() {
        return vec![None; board.items.len()];
    }
    let rects = match board.kind {
        Kind::Rows => rows_boxes(board, &items, room, gap),
        Kind::Cols => cols_boxes(board, &items, room, gap),
        Kind::Grid { columns } => grid_boxes(board, measured, &items, columns, room, gap),
        Kind::Tabs {
            pane_offset,
            pane_top,
            ..
        } => {
            let available = room - pane_top;
            let height = if board.fill_height {
                available
            } else {
                items[0].slot.content.min(available)
            };
            items
                .iter()
                .map(|d| Rect::new(pane_offset, pane_top, d.slot.width, height))
                .collect()
        }
    };
    let mut boxed: Vec<Option<Boxed>> = vec![None; board.items.len()];
    for (d, rect) in items.iter().zip(rects) {
        let authored_here = authored_above || d.item.height.is_some();
        let nested = match (&d.item.content, &d.slot.board) {
            (Content::Board(inner), Some(inner_measured)) => {
                let inner_room = (rect.height
                    - details_chrome(inner, gap)
                    - inner.chrome_height
                    - inner.band
                    - d.slot.shift.body_dy)
                    .max(0.0);
                Some(place(
                    inner,
                    inner_measured,
                    inner_room,
                    card_gap,
                    authored_here,
                    diagnostics,
                ))
            }
            (Content::Chart(chart), _) => {
                if let Some(floor) = chart.floor.filter(|floor| {
                    authored_here
                        && rect.height > 0.0
                        && floor.categories > 0
                        && floor.min_height > rect.height
                }) {
                    diagnostics.push(diag::warn_layout_min_exceeds_height(
                        chart.id.as_str(),
                        floor.categories,
                        floor.min_height,
                        rect.height,
                    ));
                }
                None
            }
            _ => None,
        };
        boxed[d.at] = Some(Boxed {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
            shift: d.slot.shift,
            board: nested,
        });
    }
    boxed
}

#[derive(Clone, Copy)]
struct Rect {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

impl Rect {
    fn new(x: f64, y: f64, width: f64, height: f64) -> Self {
        Self {
            x,
            y,
            width,
            height,
        }
    }
}

/// Rows stack. A pinned item keeps its ceiling height; the auto items share
/// what is left, scaling down in proportion when it is short, and, when the
/// board fills its box, sharing what is spare among those that can use it in
/// proportion to their content.
fn rows_boxes(board: &Node, items: &[Drawn], room: f64, gap: f64) -> Vec<Rect> {
    let n = items.len();
    let room = room - gap * (n - 1) as f64;
    let specified = rows_specified(items, room);
    let pinned: f64 = specified.iter().flatten().sum();
    let remaining = (room - pinned).max(0.0);
    let auto_total: f64 = items
        .iter()
        .zip(&specified)
        .filter(|(_, s)| s.is_none())
        .map(|(d, _)| d.slot.content)
        .sum();
    let mut heights: Vec<f64> = items
        .iter()
        .zip(&specified)
        .map(|(d, s)| match s {
            Some(pinned) => *pinned,
            None if auto_total > 0.0 && remaining < auto_total => {
                d.slot.content * (remaining / auto_total)
            }
            None => d.slot.content,
        })
        .collect();
    let spare = remaining - auto_total;
    if board.fill_height && spare > 0.0 {
        let growing: Vec<usize> = (0..n)
            .filter(|&i| specified[i].is_none() && takes_spare(items[i].item, n == 1))
            .collect();
        let growing_total: f64 = growing.iter().map(|&i| items[i].slot.content).sum();
        if growing_total > 0.0 {
            for i in growing {
                heights[i] += spare * items[i].slot.content / growing_total;
            }
        }
    }
    let mut y = 0.0;
    items
        .iter()
        .zip(heights)
        .map(|(d, height)| {
            let rect = Rect::new(0.0, y, d.slot.width, height);
            y += height + gap;
            rect
        })
        .collect()
}

/// Cols share one row height: the tallest authored height wins and overflows
/// the room; otherwise the tallest content, capped at the room, or all of it
/// when the board fills.
fn cols_boxes(board: &Node, items: &[Drawn], room: f64, gap: f64) -> Vec<Rect> {
    let demand = cols_demand(items, room);
    let (row, cell) = if demand.specified >= demand.auto {
        (demand.specified, demand.drawn.min(demand.specified))
    } else if board.fill_height {
        (room, room)
    } else {
        let row = demand.auto.min(room);
        (row, row)
    };
    let mut x = 0.0;
    items
        .iter()
        .map(|d| {
            let height = if holds_only_content_sized(d.item) {
                row
            } else {
                cell
            };
            let rect = Rect::new(x, 0.0, d.slot.width, height);
            x += d.slot.width + gap;
            rect
        })
        .collect()
}

/// Grid rows cost their tallest item; over the room, only rows no authored
/// height pins scale down; spare goes to the rows that can use it.
fn grid_boxes(
    board: &Node,
    measured: &Measured,
    items: &[Drawn],
    columns: usize,
    room: f64,
    gap: f64,
) -> Vec<Rect> {
    let (mut rows, pinned) = grid_rows(items, room);
    let gaps = gap * (rows.len() - 1) as f64;
    let total: f64 = rows.values().sum();
    if total + gaps > room {
        let pinned_total: f64 = pinned.iter().filter_map(|r| rows.get(r)).sum();
        let auto_total = total - pinned_total;
        let budget = (room - gaps - pinned_total).max(0.0);
        let scale = if auto_total > 0.0 {
            budget / auto_total
        } else {
            1.0
        };
        for (row, height) in rows.iter_mut() {
            if !pinned.contains(row) {
                *height *= scale;
            }
        }
    }
    let spare = room - gaps - rows.values().sum::<f64>();
    if board.fill_height && spare > 0.0 {
        let growing: BTreeSet<usize> = items
            .iter()
            .filter(|d| takes_spare(d.item, items.len() == 1))
            .flat_map(|d| {
                let cell = d.cell.expect("a grid places every drawn item");
                cell.row..cell.row + cell.row_span
            })
            .filter(|row| !pinned.contains(row))
            .collect();
        let growing_total: f64 = growing.iter().map(|r| rows[r]).sum();
        if growing_total > 0.0 {
            for row in growing {
                let height = rows[&row];
                rows.insert(row, height + spare * height / growing_total);
            }
        }
    }
    let mut tops = BTreeMap::new();
    let mut y = 0.0;
    for (row, height) in &rows {
        tops.insert(*row, y);
        y += height + gap;
    }
    let track = (measured.width - gap * (columns - 1) as f64) / columns as f64;
    items
        .iter()
        .map(|d| {
            let cell = d.cell.expect("a grid places every drawn item");
            let spanned: f64 = (cell.row..cell.row + cell.row_span).map(|r| rows[&r]).sum();
            Rect::new(
                cell.col as f64 * (track + gap),
                tops[&cell.row],
                d.slot.width,
                spanned + gap * (cell.row_span - 1) as f64,
            )
        })
        .collect()
}
