//! The board's drawn frame: production's root `<svg>`, layout rectangle, rows/cols/grid stacking,
//! nested boards and chart cards (`boards.py`, `layouts.py`, `chart/rendering.py`), written on nodes.
//!
//! A card draws at the larger of its slot and its own height, and production stacks the next row by
//! what was drawn, not by the slot: the drawn board grows past its sized extent, and the report keeps
//! the sized boxes.
//!
//! The root closes with the footer and timestamp row (`layout::board::bands`, measured in the host's font
//! book). The timestamp prints from `RunResult.env.rendered_at` in the board's zone.
//!
//! Above the layout the root draws its title, text and variables bands (`Header`,
//! `_build_board_content_items` with its authoring blocks), and the page title. They are sized
//! through `Facts::root_band`, which carries the title alone, as production's root sizing does. A
//! variables band that fits beside the title shares its row, and the title's content is unwrapped
//! from its `<svg>` so its markdown rules dedupe with the text's. A board that does not author its
//! `width:` takes the width its title and text ask for (`Facts::own`). A nested board's variables are refused
//! by name; its own title and text draw under one `header` block, inside its border. A `cols` row draws a pie no taller than it sizes.

use std::cell::RefCell;
use bind::bound::{self, Family};
use bind::run::RunResult;
use board_model::board::layout::LayoutItem;
use board_model::board::{items, Board};
use board_model::chart::Chart as ModelChart;
use board_model::ids::{ChartId, ItemId};
use d3rs::py_float::float_repr;
use diagnostic::{diag, refuse, Diagnostic};
use layout::board::assemble::{board_geometry, Laid, LaidBoard, LaidItem, Placed, PlacedBoard};
use board_model::board::BoardBody;
use board_model::style::style::{Gap, Style};
use layout::board::{chrome, frame, prose, variables};
use board_model::board::Layout;
use board_model::variable_value::VariableValue;
use bind::variables::Controls;
use layout::board::stack::row_advance;
use layout::board::tree::Kind;
use layout::families::callout::{CalloutCard, CalloutStyle};
use layout::family::{Env, Family as _};
use layout::families::kpi::{KpiCard, KpiFaces};
use layout::families::spark_bar::{self, SparkBarCard};
use layout::families::pie::{self, PieCard};
use layout::families::table::{self, TableCard, TableFaces};
use layout::geom::{Rect, Size};
use layout::board::band::TitleShift;
use layout::board::bands::{self, Page};
use measure::shaped::FontBook;
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Shape};

use crate::plot;
use crate::slot::{total_style, Slots};
use layout::board::assemble::leaf;
use crate::EngineFacts;

/// An item's authored path as production writes it: `@tabs.items[1]` is `tabs.items.1`.
fn authored_path(at: &ItemId) -> String {
    at.as_str().trim_start_matches('@').replace('[', ".").replace(']', "")
}

/// `svg_utils.px`: Python's `round`, half to even.
pub(crate) fn px(v: f64) -> f64 {
    v.round_ties_even()
}

/// `translate_group`: no wrapper for a translate that does nothing.
fn translated(nodes: Vec<Node>, x: f64, y: f64) -> Vec<Node> {
    if x == 0.0 && y == 0.0 {
        return nodes;
    }
    vec![Node::group(Role::Chrome, Attrs::new(), nodes).translate(x, y)]
}

/// Every drawn chart's cell in sizing's frame: its place in its board's layout area plus each
/// enclosing nested board's own.
pub(crate) fn boxes(board: &Board, laid: &Laid, slots: &Slots, drawn: &bind::bound::DrawnSet) -> Vec<(ItemId, ChartId, Rect)> {
    /// A collapsed details board sizes its charts at nothing, where its content would start.
    fn collapsed(body: &BoardBody, at: &ItemId, (x, y): (f64, f64), out: &mut Vec<(ItemId, ChartId, Rect)>) {
        for placement in items(body, at) {
            sized_at_nothing(placement.item, placement.id, (x, y), out);
        }
    }
    fn sized_at_nothing(item: &LayoutItem, at: ItemId, (x, y): (f64, f64), out: &mut Vec<(ItemId, ChartId, Rect)>) {
        match item {
            LayoutItem::Chart { id, .. } => out.push((at, id.clone(), Rect { x, y, width: 0.0, height: 0.0 })),
            LayoutItem::Board(nested) => collapsed(&nested.body, &at, (x, y), out),
        }
    }
    fn walk(board: &LaidBoard, (x0, y0): (f64, f64), slots: &Slots, drawn: &bind::bound::DrawnSet, out: &mut Vec<(ItemId, ChartId, Rect)>) {
        for item in &board.items {
            match item {
                LaidItem::Chart(p) => out.push((p.item.clone(), p.chart.clone(), Rect { x: x0 + p.x, y: y0 + p.y, width: p.width, height: p.height })),
                LaidItem::Board(b) => match slots.boards.get(&b.item) {
                    Some(body) if body.details.is_some() && drawn.details_open(&body.id) != Some(true) => collapsed(body, &b.item, (x0 + b.x, y0 + b.y), out),
                    _ => walk(&b.layout, (x0 + b.x, y0 + b.y), slots, drawn, out),
                },
            }
        }
    }
    /// A hidden item is sized at nothing, at the origin, with every chart beneath it.
    fn hidden(body: &BoardBody, at: &ItemId, drawn: &bind::bound::DrawnSet, out: &mut Vec<(ItemId, ChartId, Rect)>) {
        if body.details.is_some() && drawn.details_open(&body.id) != Some(true) {
            return;
        }
        for placement in items(body, at) {
            if drawn.active_tab(&body.id).is_some_and(|open| placement.pane.first() != Some(&open)) {
                continue;
            }
            if drawn.is_hidden(&placement.id) {
                sized_at_nothing(placement.item, placement.id, (0.0, 0.0), out);
            } else if let LayoutItem::Board(nested) = placement.item {
                hidden(&nested.body, &placement.id, drawn, out);
            }
        }
    }
    let mut out = Vec::new();
    walk(&laid.root, (0.0, 0.0), slots, drawn, &mut out);
    hidden(&board.body, &ItemId::root(), drawn, &mut out);
    out
}

/// The selection boxes of a card with no padding (`selection_boxes`, the outer and inner boxes are
/// one) and its content: the children of a chart wrapper whose card fills it.
fn unpadded(width: f64, height: f64, content: Vec<Node>) -> Vec<Node> {
    let selection = |class: &str| Node::rect(Role::Chrome, (0.0, 0.0, px(width), px(height)), Attrs::new().set("class", class).set("fill", "transparent"));
    [vec![selection("dbt-box-outer"), selection("dbt-box-inner")], content].concat()
}

/// A card moved inside its padding (left, right, top, bottom; `padded_authoring_content`): the outer
/// and inner selection boxes, then the content.
fn padded(extent: Extent, content: Vec<Node>, (left, right, top, bottom): (f64, f64, f64, f64)) -> Vec<Node> {
    let boxes = [
        ("dbt-box-outer", (0.0, 0.0, px(extent.width + left + right), px(extent.height + top + bottom))),
        ("dbt-box-inner", (px(left), px(top), px(extent.width), px(extent.height))),
    ]
    .map(|(class, rect)| Node::rect(Role::Chrome, rect, Attrs::new().set("class", class).set("fill", "transparent")));
    [boxes.to_vec(), translated(content, px(left), px(top))].concat()
}

/// A KPI card drawn in `inner` (width, height): the cell inside `pad`, the padding the host shrank it by.
/// Its `<svg>` extent, its content with the card's background reaching back out over the padding, and the
/// warnings production raises, in its order. The board's slot and a host's cell both draw a KPI through this.
pub(crate) fn kpi_card(placed: &Style, body: &BoardBody, kpi: &bind::bound::Kpi, id: &str, pad: (f64, f64, f64, f64), inner: (f64, f64), book: &FontBook) -> Result<(Extent, Vec<Node>, Vec<Diagnostic>), Diagnostic> {
    let chart_style = &placed.charts.kpi;
    let faces = KpiFaces::of(chart_style, &total_style(body)?.font.emoji).ruled_by(kpi);
    let card = KpiCard { kpi, style: chart_style, faces: &faces };
    let planned = layout::families::kpi::plan(&card, book, inner.0)?;
    let mut warnings = Vec::new();
    if let (true, Some(label)) = (planned.label_truncated, &kpi.label) {
        warnings.push(diag::warn_kpi_label_truncated(id, label.as_str()));
    }
    if planned.inline_fell_back {
        warnings.push(diag::warn_kpi_inline_variant_fallback_to_stacked(id, "inline"));
    }
    if let (true, Some(support)) = (planned.support_truncated(), &kpi.support) {
        warnings.push(diag::warn_kpi_support_truncated(id, support.explainer.as_str()));
    }
    if planned.align_overflowed {
        warnings.push(diag::warn_kpi_align_overflow(id));
    }
    let (extent, mut content) = card_svg(layout::families::kpi::draw(&card, &planned, inner.1))?;
    // The card's own background reaches back out over the padding the card was shrunk by.
    if let Some(fill) = &kpi.fill {
        let (left, right, top, bottom) = pad;
        content.insert(0, Node::rect(Role::Chrome, (-left, -top, extent.width + left + right, extent.height + top + bottom), Attrs::new().set("fill", fill.as_str())));
    }
    Ok((extent, content, warnings))
}

/// A card or title drawn as one `<svg>`: its extent and its children, which the wrapper takes in its place.
fn card_svg(nodes: Vec<Node>) -> Result<(Extent, Vec<Node>), Diagnostic> {
    let [card] = <[Node; 1]>::try_from(nodes).map_err(|_| diag::err_internal("a card or title is one node".to_string()))?;
    match card.shape {
        Shape::Svg { extent, children } => Ok((extent, children)),
        _ => Err(diag::err_internal("a card or title is an <svg>".to_string())),
    }
}

/// The board's title when it has text: production tests `not title`, so `title: ""` is untitled.
pub(crate) fn title_text(body: &BoardBody) -> Option<&str> {
    body.title.as_ref().map(|title| title.as_str()).filter(|text| !text.is_empty())
}

/// The board's own title and text above its layout (`_build_board_content_items`): their nodes, in
/// the page's frame, and the height they stack to.
pub(crate) struct Header {
    pub nodes: Vec<Node>,
    pub non_layout_height: f64,
    pub gap_before_layout: f64,
}

/// `_tagged_authoring_block`: a block's selection boxes and content in a group that starts one
/// padding before the content (`selection_boxes`, `padded_authoring_content`), so the ink lands
/// where it would untagged and the tagged box is the padded outer one. `mark` is the
/// `(top, height)` of the words inside a block that reserved more height than they fill.
fn tagged(path: &str, kind: &str, content: Vec<Node>, (x, y): (f64, f64), (width, height): (f64, f64), (pad_left, pad_right, pad_top): (f64, f64, f64), mark: Option<(f64, f64)>) -> Node {
    let (outer_width, outer_height) = (width + pad_left + pad_right, height + pad_top);
    let (mark_y, mark_height) = mark.map_or((0.0, outer_height), |(top, h)| (top + pad_top, h));
    let rect = |class: &str, rect: (f64, f64, f64, f64)| Node::rect(Role::Chrome, rect, Attrs::new().set("class", class).set("fill", "transparent"));
    let boxes = vec![
        rect("dbt-box-outer", (0.0, px(mark_y), px(outer_width), px(mark_height))),
        rect("dbt-box-inner", (px(pad_left), px(pad_top), px(width), px(height))),
    ];
    let attrs = Attrs::new().set("data-authored-path", path).set("data-authored-kind", kind);
    let children = [boxes, translated(content, px(pad_left), px(pad_top))].concat();
    Node::group(Role::Chrome, attrs, children).translate(px(x - pad_left), px(y - pad_top))
}

/// Where a board's own title and text open and what separates their blocks: the top left of the
/// frame they sit in, the gap between blocks, and whether a layout follows.
pub(crate) struct HeaderFrame {
    pub origin: (f64, f64),
    /// What separates one block from the next.
    pub pitch: f64,
    /// The gap prose columns divide a board's width by (`card_row_gap`).
    pub gap: f64,
    pub has_items: bool,
    /// Whether the first drawn item is a text block of its own (`opens_with_text`).
    pub opens_with_text: bool,
    /// Which board's title this is, and where its `cols` row moves it.
    pub title_kind: prose::TitleKind,
    pub shift: TitleShift,
    /// The authored path of a nested board, whose title and text are one `header` block under it;
    /// the root has none and tags each.
    pub header_path: Option<String>,
}

impl HeaderFrame {
    /// A nested board's frame: its blocks open at its padding and are separated by its own `style.gap`, and
    /// the board and what it measures read the same one.
    pub(crate) fn nested(geometry: &layout::board::assemble::BoardGeometry, has_items: bool, opens_with_text: bool, title_kind: prose::TitleKind, shift: TitleShift, header_path: Option<String>) -> Self {
        let gap = geometry.gap.unwrap_or(0.0);
        Self { origin: (geometry.padding.left + geometry.border_width, geometry.padding.top + geometry.border_width), pitch: gap, gap, has_items, opens_with_text, title_kind, shift, header_path }
    }
}

impl Header {
    /// What the title and text take above the layout, with the gap below them.
    pub(crate) fn band(&self) -> f64 {
        self.non_layout_height + self.gap_before_layout
    }

    pub(crate) fn of(body: &BoardBody, total: &Style, frame: &HeaderFrame, book: &FontBook, controls: &Controls, content_width: f64) -> Result<Self, Diagnostic> {
        let (page_padding, card_padding) = (frame.origin.0, total.frame.card_padding);
        let HeaderFrame { pitch, gap, has_items, .. } = frame;
        let (pitch, gap, has_items) = (*pitch, *gap, *has_items);
        let header_path = frame.header_path.as_deref();
        let prose_width = content_width - 2.0 * card_padding;
        // Production measures the inline title at `max(inner, 1)` and the stacked one at
        // `title_text_width or layout_content_width`; the engine draws neither at such a width.
        if title_text(body).is_some() && prose_width < 1.0 {
            return Err(Diagnostic::refused(refuse!("a board's title narrower than its card padding is not supported")));
        }
        let prose_width = prose_width.max(0.0);
        let title = title_text(body).map(|text| prose::title(total, text, book, prose_width, frame.title_kind)).transpose()?;
        let title_height = title.as_ref().map_or(0.0, |t| t.height.max(total.title.base.min_height));
        let text = body
            .text
            .as_ref()
            .map(|t| prose::text(total, t.as_str(), book, prose::Slot { width: prose_width, board_width: content_width, gap }))
            .transpose()?;
        let text_height = text.as_ref().map_or(0.0, |t| t.height);
        let heading = title.as_ref().map(|t| variables::Headline { height: title_height, baseline: t.baseline, one_line_width: t.one_line_width });
        // `should_use_title_inline_band`: the controls share the title's row when they fit beside it.
        let inline = match &heading {
            Some(heading) if !controls.list.is_empty() => variables::inline(controls, heading, content_width - 2.0 * card_padding, card_padding, total, book)?,
            _ => None,
        };
        let strip = if controls.list.is_empty() || inline.is_some() {
            None
        } else {
            let (width, align) = variables::standalone(title_text(body).is_some(), content_width, card_padding, total);
            Some(variables::band(controls, width, align, total, book)?)
        };
        let variables_height = strip.as_ref().map_or(0.0, |(_, height)| *height);
        let inline_height = inline.as_ref().map_or(0.0, |band| band.height);
        let shift = frame.shift;
        let stacked_title_height = if inline.is_some() { 0.0 } else if title.is_some() { title_height + shift.body_dy } else { title_height };
        let band = prose::content_box(stacked_title_height, inline_height, text_height, variables_height, pitch, has_items, frame.opens_with_text, card_padding);
        let (x, mut y) = (page_padding + card_padding, frame.origin.1 + band.content_top);
        let header_start = y;
        let line_box = title.as_ref().map(|t| t.line_box);
        // A nested board's title and text are one `header` block: each piece sits at its offset from the
        // block's own origin (`header_piece`).
        let mut pieces: Vec<Node> = Vec::new();
        let piece = |kind: &str, content: Vec<Node>, at: f64| Node::group(Role::Chrome, Attrs::new().set("data-authored-kind", kind), content).translate(0.0, px(at) - px(header_start));
        let mut nodes = Vec::new();
        match (title, inline) {
            (Some(title), Some(inline)) => {
                // `_render_title_variables_inline_band`: the title's block claims no more on its right than
                // the gap to the strip, and the band claims the top inset once, where it is placed.
                let pad = (card_padding, card_padding.min(total.variables.gap), 0.0);
                // `extract_svg_inner_content`: the band holds the title's content, not its `<svg>`, so
                // its markdown rules dedupe against the text's in one scope.
                let (_, content) = card_svg(vec![title.svg])?;
                let block = tagged("title", "title", content, (card_padding, inline.title_dy), (inline.title_width, title_height), pad, Some(title.line_box));
                let strip = translated(vec![inline.strip], px(inline.strip_dx), px(inline.strip_dy));
                nodes.extend(translated([vec![block], strip].concat(), px(page_padding), px(y)));
                y += inline.height;
                if body.text.is_some() {
                    y += pitch;
                }
            }
            (Some(title), None) if header_path.is_some() => {
                pieces.push(piece("title", vec![title.svg], y + shift.title_dy));
                y += title_height + shift.body_dy;
            }
            (Some(title), None) => {
                let block = tagged("title", "title", vec![title.svg], (x, y), (prose_width, title_height), (card_padding, card_padding, band.content_top), Some(title.line_box));
                nodes.push(block);
                y += title_height;
            }
            _ => {}
        }
        if let Some(text) = text {
            // The band's top inset is claimed once, by whichever block opens it.
            let top = if title_text(body).is_some() { 0.0 } else { band.content_top };
            if header_path.is_some() {
                pieces.push(piece("text", text.nodes, y));
            } else {
                nodes.push(tagged("text", "text", text.nodes, (x, y), (prose_width, text_height), (card_padding, card_padding, top), None));
            }
            y += text_height;
        }
        if let Some(path) = header_path.filter(|_| !pieces.is_empty()) {
            // A combined header opening with a title is seated the way the standalone title is, and ends
            // where the header ends: the title's trailing margin is the rhythm to the text under it.
            let height = y - header_start;
            let mark = line_box.map(|(top, line_height)| (top, if body.text.is_some() { height - top } else { line_height }));
            nodes.push(tagged(path, "header", pieces, (x, header_start), (prose_width, height), (card_padding, card_padding, band.content_top), mark));
        }
        if let Some((strip, _)) = strip {
            // Title or text above it: one gap (`compute_board_content_box`).
            if title_text(body).is_some() || body.text.is_some() {
                y += pitch;
            }
            nodes.extend(translated(vec![strip], px(x), px(y)));
        }
        Ok(Header { nodes, non_layout_height: band.non_layout_height, gap_before_layout: band.gap_before_layout })
    }
}

struct Composer<'a, F> {
    run: &'a RunResult,
    bound: &'a bound::Board,
    slots: &'a Slots<'a>,
    facts: &'a F,
    book: &'a FontBook,
    /// The warnings the drawn charts raised against their widths, in the order they draw.
    warnings: RefCell<Vec<Diagnostic>>,
}

pub(crate) fn board<F: EngineFacts>(
    board: &Board,
    bound: &bound::Board,
    slots: &Slots,
    laid: &Laid,
    run: &RunResult,
    facts: &F,
    book: &FontBook,
) -> Result<(Vec<Node>, Vec<Diagnostic>), Diagnostic> {
    let composer = Composer { bound, slots, facts, book, run, warnings: RefCell::new(Vec::new()) };
    let style = facts.style(&board.body)?;
    let body = &board.body;
    let total = total_style(body)?;
    let page_padding = board_geometry(style)?.frame_margin;
    let background = leaf(style.background.as_ref(), "style.background")?.as_str();
    let content_width = laid.width - 2.0 * page_padding;
    let controls = bind::variables::controls(body, run, total, &slots.styles.root_queries)?;
    if laid.root.items.is_empty() && title_text(body).is_none() && body.text.is_none() && controls.list.is_empty() {
        return Err(Diagnostic::refused(refuse!("drawing a board with no drawn items is not supported")));
    }
    let card_gap = if body.card_gap { total.frame.card_gap } else { 0.0 };
    // `card_row_gap`: a root cols or grid pitches its cards by its own gap, any other by `style.gap`.
    let gap = match laid.root.kind {
        Kind::Cols | Kind::Grid { .. } => laid.root.pitch,
        Kind::Rows | Kind::Tabs { .. } => total.gap.map_or(0.0, Gap::px) + card_gap,
    };
    let frame = HeaderFrame { origin: (page_padding, page_padding), pitch: laid.root.pitch, gap, has_items: !laid.root.items.is_empty(), opens_with_text: layout::board::assemble::opens_with_text(body, &ItemId::root(), bound.drawn()), title_kind: prose::TitleKind::ROOT, shift: TitleShift::default(), header_path: None };
    let header = Header::of(body, total, &frame, book, &controls, content_width)?;
    let (items, layout_height) = composer.layout(&laid.root)?;
    let actual_height = header.non_layout_height + header.gap_before_layout + layout_height;
    let mut layout = header.nodes;
    // `_bg_rect`: the layout's own rectangle, under its items.
    if !laid.root.items.is_empty() {
        let layout_bg = Node::new(
            Role::Chrome,
            Attrs::new().set("fill", background),
            Shape::Rect { x: 0.0, y: 0.0, w: content_width, h: layout_height, rx: 4.0 },
        );
        layout.extend(translated([vec![layout_bg], items].concat(), px(page_padding), px(page_padding + header.non_layout_height + header.gap_before_layout)));
    }
    let total_width = laid.width.max(content_width + 2.0 * page_padding);
    let total_height = laid.height.max(actual_height + 2.0 * page_padding) + bands::footer_height(total);
    let mut children = vec![frame::defs(facts.stylesheet())];
    if background != "transparent" {
        children.push(Node::new(
            Role::Chrome,
            Attrs::new().set("fill", background),
            Shape::Rect { x: 0.0, y: 0.0, w: total_width, h: total_height, rx: 0.0 },
        ));
    }
    children.extend(layout);
    // `{layout}\n\n{timestamp}\n{footer}\n`: the row closes the root.
    let stamp = bands::stamp_text(total, run.env.rendered_at)?;
    let row = bands::draw(total, book, Page { width: total_width, height: total_height }, &stamp)?;
    let newline = || Node::chars(Chars::new("\n", Escape::Attr));
    children.push(Node::chars(Chars::new("\n\n", Escape::Attr)));
    children.extend(row.timestamp);
    children.push(newline());
    children.extend(row.footer);
    children.push(newline());
    let family = bind::font_stack::with_emoji(total.font.family.as_str(), &total.font.emoji);
    let page_title = title_text(body).unwrap_or("dbt Charts");
    let attrs = frame::root_attrs(style, &family, page_title, &run.env.rendered_at)?;
    let svg = Node::new(Role::Chrome, attrs, Shape::Svg { extent: Extent { width: total_width, height: total_height }, children });
    Ok((vec![svg], composer.warnings.into_inner()))
}

impl<'a, F: EngineFacts> Composer<'a, F> {
    /// `render_rows_layout`, `render_cols_layout` and `render_grid_layout`: the items placed, and the
    /// height they actually drew.
    fn layout(&self, board: &LaidBoard) -> Result<(Vec<Node>, f64), Diagnostic> {
        if let Kind::Tabs { pane_top, .. } = board.kind {
            return self.tabs(board, pane_top);
        }
        let mut nodes = Vec::new();
        let (mut y, mut drawn, mut cells) = (0.0, 0.0_f64, 0.0_f64);
        for item in &board.items {
            let (x_box, y_box, content, height, cell) = match item {
                LaidItem::Chart(p) => {
                    let (content, height) = self.chart(p)?;
                    (p.x, p.y, content, height, p.height)
                }
                LaidItem::Board(b) => {
                    let (content, height) = self.board_item(board, b)?;
                    (b.x, b.y, content, height, b.height)
                }
            };
            match board.kind {
                Kind::Rows => {
                    let advance = row_advance(cell, height)?;
                    nodes.extend(translated(content, 0.0, px(y)));
                    drawn = y + advance;
                    y += advance + board.pitch;
                }
                // A cell in a row draws to its content when it is shorter than the row; the row is
                // the tallest cell, and it must be the tallest drawn.
                Kind::Cols => {
                    nodes.extend(translated(content, px(x_box), 0.0));
                    drawn = drawn.max(height);
                    cells = cells.max(cell);
                }
                Kind::Grid { .. } => {
                    nodes.extend(translated(content, px(x_box), px(y_box)));
                    drawn = drawn.max(y_box + height);
                    cells = cells.max(y_box + cell);
                }
                Kind::Tabs { .. } => return Err(diag::err_internal("a tabs layout composes through `tabs`".to_string())),
            }
        }
        if !matches!(board.kind, Kind::Rows) {
            row_advance(cells, drawn)?;
        }
        Ok((nodes, drawn))
    }

    /// A chart's card in its wrapper (`_wrap_rendered_chart_svg`). A failed chart's is the error
    /// card on the error path (`_render_chart_resolve_failure`: no padding inset, no card border or
    /// shadow); a KPI's is its card inset by the card padding and its own.
    fn chart(&self, placed: &Placed) -> Result<(Vec<Node>, f64), Diagnostic> {
        let id = placed.chart.as_str();
        let slot = self.slots.get(&placed.item)?;
        let style = self.facts.style(slot.body)?;
        let shadow = !matches!(style.box_shadow, model_core::Tri::Unset | model_core::Tri::Null);
        let border = &style.charts.border;
        let drawn_border = leaf(border.width, "style.charts.border.width")? > 0.0 || leaf(border.radius, "style.charts.border.radius")? > 0.0;
        if drawn_border || shadow {
            return Err(Diagnostic::refused(refuse!("drawing a chart card's border or shadow is not supported")));
        }
        let drawn = self.bound.charts().get(&placed.item);
        let body = drawn.map(|c| &c.family);
        let (class, title, extent_height, children) = match body {
            Some(Err(failure)) => {
                let card = crate::slot::error_card(self.facts, slot, failure, placed.width, placed.shift)?;
                let (extent, children) = card_svg(card.render(&Env { book: self.book }, placed.width, placed.height)?)?;
                let children = unpadded(placed.width, extent.height, children);
                ("dbt-chart dbt-chart-callout", id.to_string(), extent.height, children)
            }
            Some(Ok(Family::Kpi(kpi))) => {
                let chart_style = &slot.placed.charts.kpi;
                if chart_style.border.width > 0.0 || chart_style.border.radius > 0.0 {
                    return Err(Diagnostic::refused(refuse!("drawing a chart card's border or shadow is not supported")));
                }
                let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
                let pad = crate::slot::additive_padding(card_padding, &chart_style.padding);
                let (inner_width, inner_height) = ((placed.width - pad.0 - pad.1).max(0.0), (placed.height - px(pad.2) - px(pad.3)).max(0.0));
                let (extent, content, warnings) = kpi_card(slot.placed, slot.body, kpi, id, pad, (inner_width, inner_height), self.book)?;
                self.warnings.borrow_mut().extend(warnings);
                let children = padded(extent, content, pad);
                // The wrapper names the chart by its authored label, not the one the slot's case transform drew.
                let authored = kpi.title.clone();
                let title = authored.unwrap_or_else(|| id.to_string());
                ("dbt-chart", title, extent.height + px(pad.2) + px(pad.3), children)
            }
            Some(Ok(Family::Table(bound_table))) => {
                let chart_style = &slot.placed.charts.table;
                if chart_style.border.width > 0.0 || chart_style.border.radius > 0.0 {
                    return Err(Diagnostic::refused(refuse!("drawing a chart card's border or shadow is not supported")));
                }
                let faces = TableFaces::of(chart_style, slot.placed, crate::slot::table_title(slot.chart), placed.width)?;
                // A nested board wrapping a table moves its title to share a baseline with its `cols`
                // row's other titles; the layout does not yet name which boards wrap.
                if faces.title.is_some() && placed.nested {
                    return Err(Diagnostic::refused(refuse!("a table title inside a nested board is not supported")));
                }
                let card = TableCard { table: bound_table, style: chart_style, faces: &faces };
                let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
                let pad = crate::slot::additive_padding(card_padding, &chart_style.padding);
                let planned = table::plan(&card, self.book, (placed.width - pad.0 - pad.1).max(0.0))?;
                if !planned.fits(&card, placed.height - px(pad.2) - px(pad.3)) {
                    return Err(Diagnostic::refused(refuse!("a table in a slot shorter than its rows is not supported")));
                }
                let (extent, content) = card_svg(table::draw(&card, &planned)?)?;
                let children = padded(extent, content, pad);
                ("dbt-chart", crate::slot::table_title(slot.chart).filter(|title| !title.is_empty()).map_or_else(|| id.to_string(), str::to_string), extent.height + px(pad.2) + px(pad.3), children)
            }
            Some(Ok(Family::Pie(bound_pie))) => {
                let card = PieCard { pie: bound_pie, style: &slot.placed.charts.pie, placed: slot.placed, chart_id: id, emoji: &total_style(slot.body)?.font.emoji, ground: leaf(style.background.as_ref(), "style.background")?.as_str() };
                let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
                let pad = crate::slot::additive_padding(card_padding, &slot.placed.charts.pie.base.padding);
                // The size stage decides the mode in the slot's own box; a cols row aligns a pie no
                // taller than it sizes (`_effective_alignment_target_for_chart`).
                let natural = crate::slot::pie_height(slot, style, placed.width)?;
                let height = placed.height.min(natural);
                let planned = pie::plan(&card, self.book, (placed.width, natural), (placed.width, height), pad)?;
                self.warnings.borrow_mut().extend(planned.diagnostics().iter().cloned());
                let svg = pie::draw(&card, &planned, (placed.width, height))?;
                let selection = |class: &str, rect: (f64, f64, f64, f64)| Node::rect(Role::Chrome, rect, Attrs::new().set("class", class).set("fill", "transparent"));
                let inner = (px(pad.0), px(pad.2), px(placed.width - pad.0 - pad.1), px(height - pad.2 - pad.3));
                let children = vec![selection("dbt-box-outer", (0.0, 0.0, px(placed.width), px(height))), selection("dbt-box-inner", inner), svg];
                ("dbt-chart", bound_pie.title.clone().unwrap_or_else(|| id.to_string()), height, children)
            }
            Some(Ok(Family::SparkBar(bound_spark))) => {
                let chart_style = &slot.placed.charts.spark_bar;
                let face = spark_bar::face_of(chart_style, &total_style(slot.body)?.font.emoji);
                let fill = spark_bar::fill_of(slot.placed)?;
                let card = SparkBarCard { spark: bound_spark, style: chart_style, face: &face, fill: &fill };
                let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
                let pad = crate::slot::additive_padding(card_padding, &chart_style.padding);
                let (extent, content) = card_svg(card.render(&Env { book: self.book }, (placed.width - pad.0 - pad.1).max(0.0), (placed.height - px(pad.2) - px(pad.3)).max(0.0))?)?;
                ("dbt-chart", id.to_string(), extent.height + px(pad.2) + px(pad.3), padded(extent, content, pad))
            }
            Some(Ok(Family::Callout(callout))) => {
                // A callout paints its own border and background, and takes neither the card padding
                // nor the chart's own (`_uses_svg_family_layout_padding`): its card fills the wrapper.
                let style = CalloutStyle::of(&slot.placed.charts.callout, total_style(slot.body)?)?;
                let card = CalloutCard { callout, style: &style };
                let (extent, content) = card_svg(card.render(&Env { book: self.book }, placed.width, placed.height)?)?;
                let title = callout.title.clone().unwrap_or_else(|| id.to_string());
                ("dbt-chart dbt-chart-callout", title, extent.height, unpadded(placed.width, extent.height, content))
            }
            Some(Ok(Family::Cartesian(_))) => {
                let drawn = drawn.ok_or_else(|| diag::err_internal(format!("chart {id} is not bound")))?;
                let frame = drawn.frame.as_ref().ok_or_else(|| diag::err_internal("a plot carries its frame"))?;
                let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
                let cell = Size { width: placed.width, height: placed.height };
                let (nodes, warnings) = plot::draw_plot(drawn, id, cell, self.book, slot.placed, &total_style(slot.body)?.font.emoji, card_padding)?;
                self.warnings.borrow_mut().extend(warnings);
                // The selection boxes: the slot, and the slot inside the card and chart padding.
                let own = &frame.padding;
                let (left, top) = (card_padding + own.left, card_padding + own.top);
                let inner = ((placed.width - left - card_padding - own.right).max(0.0), (placed.height - top - card_padding - own.bottom).max(0.0));
                let boxes = [("dbt-box-outer", (0.0, 0.0, px(placed.width), px(placed.height))), ("dbt-box-inner", (px(left), px(top), px(inner.0), px(inner.1)))]
                    .map(|(class, rect)| Node::rect(Role::Chrome, rect, Attrs::new().set("class", class).set("fill", "transparent")));
                ("dbt-chart", drawn.title.clone().unwrap_or_else(|| id.to_string()), placed.height, [boxes.to_vec(), nodes].concat())
            }
            _ => return Err(diag::err_internal(format!("chart {id} is not drawn by the engine"))),
        };
        let family = match slot.chart {
            ModelChart::Kpi(_) => "kpi",
            ModelChart::Table(_) => "table",
            ModelChart::Callout(_) => "callout",
            ModelChart::SparkBar(_) => "spark_bar",
            ModelChart::Pie(_) => "pie",
            ModelChart::Donut(_) => "pie",
            ModelChart::Scatter(_) => "scatter",
            ModelChart::Line(_) => "line",
            ModelChart::Bar(_) | ModelChart::Funnel(_) => "bar",
            ModelChart::Histogram(_) => "histogram",
            ModelChart::Area(_) => "area",
            ModelChart::Heatmap(_) => "heatmap",
            ModelChart::Geoshape(_) => "geoshape",
            ModelChart::Map(_) => "map",
            ModelChart::PointMap(_) => "point_map",
            ModelChart::BubbleMap(_) => "bubble_map",
            ModelChart::Custom(_) => "custom",
        };
        // Where the chart was authored: the `charts:` of the nested board that declares it, else the root's.
        let chart_path = match &slot.authored_in {
            Some(board) => format!("{}.charts.{id}", authored_path(board)),
            None => format!("charts.{id}"),
        };
        let attrs = Attrs::new()
            .set("class", class)
            .set("id", format!("chart-{id}"))
            .set("data-chart-id", id)
            .set("data-chart-type", family)
            .set("data-chart-title", title.as_str())
            .set("data-chart-width", float_repr(placed.width))
            .set("data-chart-height", float_repr(extent_height))
            .set("role", "group")
            .set("aria-roledescription", display_text::language::tr("aria.chart", None, &[])?)
            .set("aria-label", title.as_str())
            .set("data-authored-path", chart_path)
            .set("data-authored-kind", "chart");
        let canvas = match (body, drawn.and_then(|chart| chart.frame.as_ref())) {
            (Some(Ok(Family::Pie(pie))), _) => Some(pie.canvas.clone()),
            // The ground label ink is judged on, which the chart's runtime reads off its wrapper.
            (Some(Ok(Family::Cartesian(_))), Some(frame)) => {
                let (r, g, b, _) = frame.canvas;
                Some(color::rgb01_to_hex(r, g, b))
            }
            _ => slot.canvas.clone(),
        };
        let attrs = match canvas {
            Some(canvas) => attrs.set("data-dbt-chart-canvas", canvas.as_str()),
            None => attrs,
        };
        // The authored subtitle rides in a leading `<desc>`, written through `escape_attr` (the
        // `Mdsvg` escape) even as element text; never a `<title>`, which browsers paint as a hover tooltip.
        let subtitle = match body {
            Some(Ok(Family::Pie(pie))) => pie.subtitle.as_deref(),
            _ => drawn.and_then(|chart| chart.subtitle.as_deref()),
        };
        let description = subtitle.map(|text| {
            let desc = scene::Element { tag: "desc".to_string(), attrs: Attrs::new(), children: vec![scene::Child::Text(Chars::new(text, Escape::Mdsvg))] };
            Node::element(desc)
        });
        Ok((vec![Node::group(Role::Chrome, attrs, description.into_iter().chain(children).collect())], extent_height))
    }

    fn body(&self, at: &ItemId) -> Result<&'a BoardBody, Diagnostic> {
        self.slots.boards.get(at).copied().ok_or_else(|| diag::err_internal(format!("board {at} has no body")))
    }

    /// A nested board in the layout of `parent`: a details section draws its bar and, when open, the board
    /// beneath it (`render_details_item`), any other board is itself.
    fn board_item(&self, parent: &LaidBoard, placed: &PlacedBoard) -> Result<(Vec<Node>, f64), Diagnostic> {
        let body = self.body(&placed.item)?;
        let Some(details) = &body.details else { return self.nested(placed, self.facts.style(body)?) };
        // The bar is drawn in the style of the layout that holds the section.
        let style = self.slots.style_of(&parent.at)?;
        let facts = self.slots.styles.details.get(&placed.item).ok_or_else(|| diag::err_internal(format!("no details facts for the board at {}", placed.item)))?;
        let open = self.bound.drawn().details_open(&body.id) == Some(true);
        let summary = chrome::plain(&details.summary, "a details summary")?;
        let label = match (&details.expanded_title, open) {
            (Some(title), true) => chrome::plain(title, "a details summary")?,
            _ => summary,
        };
        let href = chrome::toggle_url(&self.variables()?, &facts.variable, if open { "false" } else { "true" });
        let bar = chrome::draw_details_bar(style, label, open, &href, placed.width);
        let summary_height = chrome::summary_height(style)?;
        if !open {
            return Ok((vec![bar], summary_height));
        }
        // The bar and the content's offset below it come off the section's cell.
        let content_offset = style.layout.details.content_y_offset;
        let content = PlacedBoard { height: (placed.height - summary_height - content_offset).max(0.0), ..placed.clone() };
        let (nested, height) = self.nested(&content, self.facts.style(body)?)?;
        let content_y = px(summary_height + content_offset);
        let actual = content_y + height;
        let mut nodes = vec![chrome::draw_details_border(style, placed.width, actual), bar];
        nodes.extend(translated(nested, 0.0, content_y));
        Ok((nodes, actual))
    }

    /// The board's variables as `_build_toggle_url` reads them: each set one, in order, as `str(value)`.
    fn variables(&self) -> Result<Vec<(String, String)>, Diagnostic> {
        let mut out = Vec::new();
        for (name, value) in &self.run.variables {
            let text = match value {
                VariableValue::Unset => continue,
                VariableValue::One(atom) => bind::variables::text_of(atom),
                _ => return Err(Diagnostic::refused(refuse!("a link that carries a list or date range variable is not supported"))),
            };
            out.push((name.clone(), text));
        }
        Ok(out)
    }

    /// `render_tabs_layout`: the tab strip and, `pane_top` below it, the open tab's pane.
    fn tabs(&self, board: &LaidBoard, pane_top: f64) -> Result<(Vec<Node>, f64), Diagnostic> {
        let body = self.body(&board.at)?;
        let Layout::Tabs(tabs) = &body.layout else { return Err(diag::err_internal(format!("board {} has no tabs", board.at))) };
        let style = total_style(body)?;
        let titles = tabs.items.iter().map(|tab| chrome::plain(&tab.title, "a tab title")).collect::<Result<Vec<_>, _>>()?;
        let strip = chrome::tab_strip(style, tabs.position, &titles, self.book)?;
        let facts = self.slots.styles.tabs.get(&board.at).ok_or_else(|| diag::err_internal(format!("no tabs facts for the board at {}", board.at)))?;
        let active = self.bound.drawn().active_tab(&body.id).ok_or_else(|| diag::err_internal(format!("no open tab for the board {}", body.id)))?;
        let variables = self.variables()?;
        let hrefs: Vec<String> = facts.slugs.iter().map(|slug| chrome::toggle_url(&variables, &facts.variable, slug)).collect();
        let authored: Vec<String> = (0..tabs.items.len()).map(|i| format!("{}.title", authored_path(&board.at.key("tabs").key("items").index(i)))).collect();
        let [LaidItem::Board(pane)] = board.items.as_slice() else { return Err(diag::err_internal("a tabs layout draws its open tab alone".to_string())) };
        let (content, height) = self.nested(pane, self.facts.pane_style(body, active)?)?;
        let mut nodes = vec![chrome::draw_tab_strip(style, &strip, active, &hrefs, &authored, pane.width)?];
        nodes.extend(translated(content, 0.0, px(pane_top)));
        Ok((nodes, pane_top + height))
    }

    /// `render_nested_board` for a board without variables, in `style`: its border, its own title and text, then its layout.
    fn nested(&self, placed: &PlacedBoard, style: &layout::board::assemble::StylePatch) -> Result<(Vec<Node>, f64), Diagnostic> {
        let geometry = board_geometry(style)?;
        if matches!(style.border.dash_array, model_core::Tri::Value(_)) {
            return Err(Diagnostic::refused(refuse!("a dashed nested board border is not supported")));
        }
        // `_effective_padding`: the border's width on each side is padding too.
        let stroke = geometry.border_width;
        let (padding, margin) = (geometry.padding, geometry.margin);
        let (inset_left, inset_top, inset_bottom) = (padding.left + stroke, padding.top + stroke, padding.bottom + stroke);
        // A tab's pane has no body of its own, and so no text.
        let body = self.slots.boards.get(&placed.item).copied().filter(|body| body.text.is_some() || title_text(body).is_some());
        let (layout, layout_height) = self.layout(&placed.layout)?;
        // The board's own title and text above its layout, in this board's frame (`render_nested_board`).
        let header = if let Some(body) = body {
            let kind = prose::TitleKind { level: *self.slots.levels.get(&placed.item).ok_or_else(|| diag::err_internal("a nested board carries its heading level"))?, root: false };
            let opens = layout::board::assemble::opens_with_text(body, &placed.item, self.slots.drawn);
            let frame = HeaderFrame::nested(&geometry, !placed.layout.items.is_empty(), opens, kind, placed.shift, Some(authored_path(&placed.item)));
            let content_width = placed.width - geometry.chrome().0;
            Some(Header::of(body, total_style(body)?, &frame, self.book, &Controls { list: Vec::new(), reset_enabled: false }, content_width)?)
        } else {
            None
        };
        let band = header.as_ref().map_or(0.0, Header::band);
        let natural = inset_top + band + layout_height + inset_bottom;
        let margin_vertical = margin.top + margin.bottom;
        let board_height = natural.max((placed.height - margin_vertical).max(0.0));
        let board_width = placed.width - (margin.left + margin.right);
        let background = leaf(style.background.as_ref(), "style.background")?.as_str();
        let radius = leaf(style.border.radius, "style.border.radius")?;
        let mut group = Vec::new();
        if !background.is_empty() {
            group.push(Node::new(
                Role::Chrome,
                Attrs::new().set("fill", background),
                Shape::Rect { x: 0.0, y: 0.0, w: board_width, h: board_height, rx: radius },
            ));
        }
        // `border_rect`: a stroke-only rectangle inset by half the stroke, so the border stays inside the box.
        let color = if stroke > 0.0 { leaf(style.border.color.as_ref(), "style.border.color")?.as_str() } else { "" };
        if stroke > 0.0 && !color.is_empty() {
            let half = stroke / 2.0;
            group.push(Node::new(
                Role::Chrome,
                Attrs::new().set("fill", "none").set("stroke", color).num("stroke-width", stroke),
                Shape::Rect { x: half, y: half, w: (board_width - stroke).max(0.0), h: (board_height - stroke).max(0.0), rx: (radius - half).max(0.0) },
            ));
        }
        if let Some(header) = header {
            group.extend(header.nodes);
        }
        if !placed.layout.items.is_empty() {
            group.extend(translated(layout, px(inset_left), px(inset_top + band)));
        }
        let total_height = board_height + margin_vertical;
        let children = translated(group, px(margin.left), px(margin.top));
        let svg = Node::new(Role::Chrome, Attrs::new(), Shape::Svg { extent: Extent { width: placed.width, height: total_height }, children });
        Ok((vec![svg], total_height))
    }
}
