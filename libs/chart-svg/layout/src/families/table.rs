//! A flat table of text columns: a header row over body rows, each cell one line of start-aligned
//! text in a column the table sizes itself. `plan` decides what a width and the style decide (the
//! columns' widths, every row's place, the table's height), `draw` places the nodes.
//!
//! The rules are `chart-renderer/src/families/table/layout.rs`'s (`lay_out`,
//! `calculate_column_layout_port`, `column_inputs`, `wrapped_headers`), themselves the port of
//! production's `calculate_column_layout` and `_render_table_svg_core`, cited at each law. The
//! table's root `<svg>` is part of the DOM contract and is emitted here. A table that needs a law
//! this slice does not port is refused by name; `sweep/cartesian_refusals/` lists them.
//!
//! `plan` is the columns' widths at a width (production's `calculate_column_layout`: p95 demand and word
//! floor per column, compact columns at their natural width, text columns sharing the rest,
//! cluster-equalized and floored), the one-step fit cascade (padding to half, at least 6), every row's
//! place and the table's height; the widths are the raw advances of
//! `measure::shaped::FontBook::raw_advance`. `draw` is the header, its divider, the stripes and the
//! cells with the table's root `<svg>`. [`TableFaces::of`] reads the body and header faces off the chart's
//! total `TableChartStyle`; [`Plan::fits`] is the slot check under which production neither pages nor
//! squeezes.

use bind::bound::Table;
use board_model::style::style::Style;
use board_model::style::table::{CellAlign, PaginationMode, TableChartStyle};
use d3rs::py_float::py_float_sum;
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use measure::SAMPLE_ROWS;
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run};

use super::failed::Face;
use crate::title::chart_title_spec;

/// table.rs:`PAGINATION_GROW_CAP` (`_PAGINATION_GROW_CAP`): rows past `page_rows` that still draw on one page.
const PAGINATION_GROW_CAP: usize = 2;
/// layout.rs:`COMPACT_DEMAND_CEILING`. A column whose demand is within it is pinned at its natural
/// width; a wider one shares what is left.
const COMPACT_DEMAND_CEILING: f64 = 220.0;
/// layout.rs:`PROPORTIONAL_CAP`. Share of a budget one text column may take.
const PROPORTIONAL_CAP: f64 = 0.6;
/// layout.rs:`OVERFLOW_EPSILON`. Float noise in a sum of widths that mathematically fits.
const OVERFLOW_EPSILON: f64 = 1e-6;
/// layout.rs:`overflows`. The breathing room the fit cascade leaves a short header.
const CASCADE_BREATHING: f64 = 4.0;
/// layout.rs:`overflows`. Only a header of at most this many characters drives the fit cascade.
const CASCADE_HEADER_CHARS: usize = 12;

/// The two text faces a table paints: its cells' and its header's.
#[derive(Debug, Clone, PartialEq)]
pub struct TableFaces {
    pub body: Face,
    pub header: Face,
    pub title: Option<TableTitle>,
}

/// A table's title as it draws: the authored text cased by the title font, its face and slant.
#[derive(Debug, Clone, PartialEq)]
pub struct TableTitle {
    /// The drawn line: the cased title with every run of whitespace collapsed to one space.
    pub text: String,
    /// The title as authored when the drawn line differs from the cased text: it rides along as `<title>`.
    pub full: Option<String>,
    pub face: Face,
    pub slant: String,
}

impl TableFaces {
    /// The faces `style` paints in, each stack drawn under the board's emoji mode. The body's ink is
    /// the table's static `color` over its font's (bind.rs:`bind_drawn`); the header's weight is the
    /// header font's.
    ///
    /// `title` is the authored title and `width` the chart's outer width, which picks the title's tier
    /// (table.py:`title_band`'s `style.title_font`, typography.py:`chart_title_spec`).
    pub fn of(style: &TableChartStyle, placed: &Style, title: Option<&str>, width: f64) -> Result<Self, Diagnostic> {
        let emoji = &placed.font.emoji;
        // bind.rs:`bind_drawn`: a static `color` is sanitized, the font's own passes as written, and
        // production paints an empty ink as `fill=""`, which is no color.
        let fixed = style.color.r#static.as_ref().map(|color| sanitize_color(color.as_str())).transpose()?.flatten();
        let body_ink = fixed.or(Some(style.font.color.as_str())).filter(|ink| !ink.is_empty()).ok_or_else(|| Diagnostic::refused(refuse!("a table with an empty font color is not supported")))?;
        let header = &style.header.font;
        let header_ink = sanitize_color(header.color.as_str())?.unwrap_or(body_ink);
        let title = title.filter(|text| !text.is_empty()).map(|text| table_title(text, style, placed, width)).transpose()?;
        Ok(Self {
            body: Face::of(style.font.family.as_str(), style.font.size, &style.font.weight, body_ink, emoji),
            header: Face::of(header.family.as_str(), header.size, &header.weight, header_ink, emoji),
            title,
        })
    }
}

fn table_title(text: &str, style: &TableChartStyle, placed: &Style, width: f64) -> Result<TableTitle, Diagnostic> {
    if style.titled.title.is_some() {
        return Err(Diagnostic::refused(refuse!("a table title styled on `charts.table.title` is not supported")));
    }
    if text.contains("{{") || text.contains("{%") {
        return Err(Diagnostic::refused(refuse!("a table title with template syntax is not supported")));
    }
    let font = &placed.charts.title.font;
    let (size, weight, family) = chart_title_spec(placed, width)?;
    let cased = display_text::case::apply_case(text, font.case.as_str()).map_err(diag::err_internal)?;
    // `wrap_text_with` collapses every run of whitespace to one space before it measures and paints.
    let drawn = cased.split_whitespace().collect::<Vec<_>>().join(" ");
    Ok(TableTitle { full: (drawn != cased).then(|| text.to_string()), text: drawn, face: Face::of(&family, size, &weight, font.color.as_str(), &placed.font.emoji), slant: font.style.as_str().to_string() })
}

#[derive(Debug, Clone, Copy)]
pub struct TableCard<'a> {
    pub table: &'a Table,
    pub style: &'a TableChartStyle,
    pub faces: &'a TableFaces,
}

/// What a width decides, shared by the least height and the drawing.
#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    width: f64,
    /// Where each column starts and how wide it is.
    columns: Vec<(f64, f64)>,
    /// The cells' padding, truncated as the theme's is (bind.rs:`bind_drawn`).
    pad: f64,
    content_width: f64,
    /// The title band's height above the header; 0 with no title.
    title_height: f64,
    /// The title's baseline, drawn when the table has a title.
    title_baseline: f64,
    header_height: f64,
    header_baseline: f64,
    header_rule_y: f64,
    rows_top: f64,
    /// The table's height: the `<svg>`'s.
    pub height: f64,
}

impl Plan {
    /// A titled table's (ascent, tail) in its `cols` row's title band (layout.rs:`title_geometry`): the
    /// baseline below the table's top, and the body's top below the baseline.
    pub fn title_metrics(&self) -> Option<(f64, f64)> {
        (self.title_height > 0.0).then_some((self.title_baseline, self.title_height - self.title_baseline))
    }

    /// Whether production draws every row at its height in a slot `slot_height` tall, rather than
    /// paging or squeezing them (layout.rs:`lay_out`, the grow-by-2 short-circuit): the rows must fit
    /// under the header, its gap and the bottom padding.
    pub fn fits(&self, card: &TableCard, slot_height: f64) -> bool {
        let style = card.style;
        !style.pagination.enabled || slot_height - self.rows_top - style.bottom_padding >= card.table.rows() as f64 * style.row.height
    }
}

/// bind.rs:`svg_font_family`'s quote-stripping half: an SVG `font-family` attribute takes a quote
/// as a literal character, so the stack is painted without them, and Source Serif gains its bundled
/// web alias.
fn paint_family(family: &str) -> String {
    let normalized = family.replace(['\'', '"'], "");
    if normalized.contains("Source Serif 4") && !normalized.contains("Source Serif 4 Web") {
        return normalized.replace("Source Serif 4", "'Source Serif 4 Web', 'Source Serif 4'");
    }
    normalized
}

/// colors.py:`sanitize_color` (table.rs:`sanitize_color`): empty is unset, a hex color stands,
/// `transparent` and `none` are `transparent`, anything else is production's
/// `ERR-CSS-COLOR-INVALID-AT-RENDER`.
fn sanitize_color(color: &str) -> Result<Option<&str>, Diagnostic> {
    let hex = color.strip_prefix('#').is_some_and(|h| matches!(h.len(), 3 | 6) && h.bytes().all(|b| b.is_ascii_hexdigit()));
    if color.is_empty() {
        Ok(None)
    } else if hex {
        Ok(Some(color))
    } else if color.eq_ignore_ascii_case("transparent") || color.eq_ignore_ascii_case("none") {
        Ok(Some("transparent"))
    } else {
        Err(Diagnostic::refused(refuse!("a table color that is not a hex color, transparent or none is not supported")))
    }
}

/// Every leaf of the table's style that this slice draws another way than production does.
fn refuse_unmodeled(style: &TableChartStyle) -> Result<(), Diagnostic> {
    let pagination = &style.pagination;
    let unmodeled = [
        (!style.header.visible, "a hidden header"),
        (style.row_numbers.visible, "row numbers"),
        (!style.wrap, "`style.wrap: false`"),
        (style.background.is_some(), "a `style.background`"),
        (style.header.font_compact.is_some(), "a compact header font"),
        (!style.header.divider.continuous && style.header.divider.divider.visible, "a header divider under each column"),
        (style.header.divider.divider.dasharray.is_some() || style.row.divider.divider.dasharray.is_some(), "a dashed divider"),
        (style.column_layout.freeze > 0, "`column_layout.freeze`"),
        (pagination.enabled && pagination.mode == PaginationMode::Scroll, "scrolling rows"),
        (style.outer_padding != 0.0, "an authored `outer_padding`"),
    ];
    match unmodeled.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(Diagnostic::refused(refuse!("a table with {what} is not supported"))),
        None => Ok(()),
    }
}

struct Measurer<'a> {
    book: &'a FontBook,
    /// The family production measures a face in (`measure::stand_in_family`).
    body: String,
    header: String,
}

impl Measurer<'_> {
    /// `FontBook.measure`: the raw advance at the face's default weight.
    fn advance(&self, text: &str, header: bool, size: f64) -> Result<f64, String> {
        let family = if header { &self.header } else { &self.body };
        self.book.raw_advance(text, &Font { family, size, weight: 400.0, italic: false })
    }

    fn width(&self, text: &str, header: bool, size: f64) -> Result<f64, Diagnostic> {
        self.advance(text, header, size).map_err(diag::err_internal)
    }
}

/// One column's inputs to the allocator.
struct Column {
    /// The p95 of the cells' widths plus both paddings (`measure::column_demand_of`).
    demand: f64,
    /// The header label's one-line width.
    header_demand: f64,
    /// The widest unbreakable token of the header and the cells, plus both paddings.
    word_floor: f64,
}

fn column_inputs(card: &TableCard, measure: &Measurer, pad: f64) -> Result<Vec<Column>, Diagnostic> {
    let (font_size, header_size) = (card.faces.body.size, card.faces.header.size);
    card.table
        .columns
        .iter()
        .map(|column| {
            let cell_demand = measure::column_demand_of(column.cells.iter().map(|text| measure.advance(text, false, font_size)), pad).map_err(diag::err_internal)?;
            let mut token = measure::widest_word_with(&column.header, |t| measure.advance(t, true, header_size)).map_err(diag::err_internal)?.0;
            for text in column.cells.iter().take(SAMPLE_ROWS) {
                token = token.max(measure::widest_word_with(text, |t| measure.advance(t, false, font_size)).map_err(diag::err_internal)?.0);
            }
            Ok(Column { demand: cell_demand, header_demand: measure.width(&column.header, true, header_size)?, word_floor: token + 2.0 * pad })
        })
        .collect()
}

/// layout.rs:`natural_auto_width`. The least width that shows an auto column in full: its demand
/// raised to the word floor, a compact column inflated by `content_headroom` when text columns
/// exist, at least the one-line header plus both paddings.
fn natural_widths(columns: &[Column], headroom: f64, pad: f64) -> Vec<(f64, bool)> {
    let base: Vec<f64> = columns.iter().map(|c| c.demand.max(c.word_floor).max(1.0)).collect();
    let text: Vec<bool> = base.iter().map(|&d| d > COMPACT_DEMAND_CEILING).collect();
    let factor = if text.iter().any(|&t| t) { 1.0 + headroom } else { 1.0 };
    columns
        .iter()
        .zip(base.iter().zip(&text))
        .map(|(c, (&d, &is_text))| ((if is_text { d } else { d * factor }).max(c.header_demand + 2.0 * pad), is_text))
        .collect()
}

/// layout.rs:`proportional_allocate`. Proportional shares with an iterative 60%-of-budget cap;
/// the capped excess redistributes among the uncapped.
fn proportional_allocate(demands: &[f64], budget: f64) -> Vec<f64> {
    if demands.len() <= 1 {
        return vec![budget; demands.len()];
    }
    let cap = budget * PROPORTIONAL_CAP;
    let mut fixed: Vec<Option<f64>> = vec![None; demands.len()];
    let mut remaining = budget;
    loop {
        let active: Vec<usize> = (0..demands.len()).filter(|&i| fixed[i].is_none()).collect();
        if active.is_empty() {
            break;
        }
        let total = py_float_sum(&active.iter().map(|&i| demands[i]).collect::<Vec<_>>());
        if total <= 0.0 {
            for &i in &active {
                fixed[i] = Some(remaining / active.len() as f64);
            }
            break;
        }
        let over: Vec<usize> = active.iter().copied().filter(|&i| (demands[i] / total) * remaining > cap).collect();
        if over.is_empty() {
            for &i in &active {
                fixed[i] = Some((demands[i] / total) * remaining);
            }
            break;
        }
        for &i in &over {
            fixed[i] = Some(cap);
        }
        remaining -= py_float_sum(&vec![cap; over.len()]);
    }
    fixed.into_iter().map(|w| w.expect("every column is fixed")).collect()
}

/// layout.rs:`cluster_and_equalize_widths`. A sorted walk clusters widths whose min/new ratio
/// stays at or above `threshold`, snaps each cluster to its max, then rescales once back to budget.
fn cluster_and_equalize(widths: &[f64], budget: f64, threshold: f64) -> Vec<f64> {
    if widths.len() <= 1 {
        return widths.to_vec();
    }
    let mut order: Vec<usize> = (0..widths.len()).collect();
    order.sort_by(|&a, &b| widths[a].partial_cmp(&widths[b]).expect("a width is a number"));
    let mut clusters: Vec<Vec<usize>> = Vec::new();
    let mut cluster: Vec<usize> = Vec::new();
    let mut cluster_min = widths[order[0]];
    for &i in &order {
        if !cluster.is_empty() && cluster_min / (widths[i] + 1e-9) < threshold {
            clusters.push(std::mem::take(&mut cluster));
            cluster_min = widths[i];
        }
        cluster.push(i);
    }
    clusters.push(cluster);
    let mut snapped = vec![0.0; widths.len()];
    for cluster in &clusters {
        let max = cluster.iter().map(|&i| widths[i]).fold(0.0_f64, f64::max);
        for &i in cluster {
            snapped[i] = max;
        }
    }
    let total = py_float_sum(&snapped);
    if total > 0.0 {
        let scale = budget / total;
        snapped.iter_mut().for_each(|w| *w *= scale);
    }
    snapped
}

/// layout.rs:`allocate_text_columns`, with no `max_width`. Proportional shares, the uncapped
/// cluster-equalized, each raised to its word floor last.
fn allocate_text(demands: &[f64], floors: &[f64], budget: f64, threshold: f64) -> Vec<f64> {
    if demands.len() == 1 {
        return vec![budget.max(floors[0])];
    }
    let shares = proportional_allocate(demands, budget);
    let equalized = cluster_and_equalize(&shares, py_float_sum(&shares), threshold);
    equalized.iter().zip(floors).map(|(&w, &floor)| if floor > 0.0 && w < floor { floor } else { w }).collect()
}

/// layout.rs:`grow_toward_header_width`. Covers each column's header gap first (proportionally
/// when the surplus cannot cover them all), then spreads any residual proportionally to width.
fn grow_toward_header(widths: &mut [f64], header_demands: &[f64], surplus: f64) {
    let gaps: Vec<f64> = widths.iter().zip(header_demands).map(|(&w, &target)| if target > w { target - w } else { 0.0 }).collect();
    let total_gap = py_float_sum(&gaps.iter().copied().filter(|&g| g > 0.0).collect::<Vec<_>>());
    if total_gap > surplus {
        for (w, gap) in widths.iter_mut().zip(&gaps) {
            if *gap > 0.0 {
                *w += surplus * (gap / total_gap);
            }
        }
        return;
    }
    for (w, gap) in widths.iter_mut().zip(&gaps) {
        *w += gap;
    }
    let residual = surplus - total_gap;
    if residual <= 0.1 {
        return;
    }
    let total = py_float_sum(widths);
    for w in widths.iter_mut() {
        *w += residual * (*w / total);
    }
}

/// layout.rs:`calculate_column_layout_port` for columns with no `width:` or `max_width:`, in a
/// budget that covers every column's natural width (the caller refuses one that does not): each
/// compact column takes its natural width, the text columns share what is left.
fn allocate(columns: &[Column], natural: &[(f64, bool)], budget: f64, threshold: f64) -> Vec<f64> {
    if columns.len() == 1 {
        return vec![budget];
    }
    let compact: Vec<usize> = (0..columns.len()).filter(|&i| !natural[i].1).collect();
    let text: Vec<usize> = (0..columns.len()).filter(|&i| natural[i].1).collect();
    let mut compact_widths: Vec<f64> = compact.iter().map(|&i| natural[i].0).collect();
    let compact_total = py_float_sum(&compact_widths);
    let text_budget = (budget - compact_total).max(0.0);
    let mut widths = vec![0.0; columns.len()];
    let header_of = |idx: &[usize]| idx.iter().map(|&i| columns[i].header_demand).collect::<Vec<_>>();
    if text.is_empty() {
        if compact_total >= budget {
            let scale = budget / compact_total;
            compact_widths.iter_mut().for_each(|w| *w *= scale);
        } else {
            grow_toward_header(&mut compact_widths, &header_of(&compact), budget - compact_total);
        }
    } else {
        let demands: Vec<f64> = text.iter().map(|&i| natural[i].0).collect();
        let floors: Vec<f64> = text.iter().map(|&i| columns[i].word_floor).collect();
        let allocated = allocate_text(&demands, &floors, text_budget, threshold);
        let remainder = budget - compact_total - py_float_sum(&allocated);
        if remainder > 0.1 && !compact.is_empty() {
            grow_toward_header(&mut compact_widths, &header_of(&compact), remainder);
        }
        for (&i, w) in text.iter().zip(allocated) {
            widths[i] = w;
        }
    }
    for (&i, w) in compact.iter().zip(compact_widths) {
        widths[i] = w;
    }
    widths
}

/// The table at `width`: its columns sized, its rows and header placed, and every leaf this slice
/// does not draw refused. `book` measures as production does (`measure::stand_in_family`, raw
/// advances).
pub fn plan(card: &TableCard, book: &FontBook, width: f64) -> Result<Plan, Diagnostic> {
    let style = card.style;
    refuse_unmodeled(style)?;
    let n_rows = card.table.rows();
    let pagination = &style.pagination;
    if pagination.enabled && n_rows > pagination.page_rows.map_or(n_rows, |rows| rows as usize + PAGINATION_GROW_CAP) {
        return Err(Diagnostic::refused(refuse!("a table with more rows than one page is not supported")));
    }
    let pad = style.column_layout.cell_padding.trunc();
    // `outer_padding` is refused above, so the table spans its whole width.
    let table_width = width;
    if table_width <= 0.0 {
        return Err(Diagnostic::refused(refuse!("a table with no width is not supported")));
    }
    let faces = card.faces;
    let measure = Measurer { book, body: measure::stand_in_family(&paint_family(&faces.body.family)).to_string(), header: measure::stand_in_family(&paint_family(&faces.header.family)).to_string() };
    let inputs = column_inputs(card, &measure, pad)?;
    let natural = natural_widths(&inputs, style.column_layout.content_headroom.get(), pad);
    // A table that needs more than its width scrolls its columns or squeezes its words
    // (layout.rs:`scroll_plan`).
    if table_width < py_float_sum(&natural.iter().map(|&(w, _)| w).collect::<Vec<_>>()) {
        return Err(Diagnostic::refused(refuse!("a table whose columns need more than its width is not supported")));
    }
    let widths = allocate(&inputs, &natural, table_width, style.column_layout.width_similarity_threshold.get());
    let content_width = py_float_sum(&widths);
    if content_width > table_width + OVERFLOW_EPSILON {
        // The word floors are applied after the 60% cap (`allocate_text_columns`), so a floored column
        // can take back more than the cap released; production draws it with an overflow warning.
        return Err(Diagnostic::refused(refuse!("a table whose word floors push its columns past its width is not supported")));
    }
    let (font_size, header_size) = (faces.body.size, faces.header.size);
    // The fit cascade (layout.rs:`overflows`): a short header wider than its column's content area
    // steps the cell padding down to half, at least 6. A header still too wide at the stepped padding
    // takes production's second step (a smaller type and a shorter row), which is refused.
    let overflows = |pad: f64| -> Result<bool, Diagnostic> {
        for (column, &w) in card.table.columns.iter().zip(&widths) {
            if card.table.columns.len() > 1 && column.header.chars().count() <= CASCADE_HEADER_CHARS && measure.width(&column.header, false, header_size)? > w - 2.0 * pad - CASCADE_BREATHING {
                return Ok(true);
            }
        }
        Ok(false)
    };
    let mut pad = pad;
    if overflows(pad)? {
        pad = (pad / 2.0).floor().max(6.0);
        if overflows(pad)? {
            return Err(Diagnostic::refused(refuse!("a table whose type steps down to fit is not supported")));
        }
    }
    // layout.rs:`wrapped_headers` and the wrap pass: a header or cell wider than the column's content
    // area takes another line, and the row grows.
    for (column, &w) in card.table.columns.iter().zip(&widths) {
        let header_fits = measure.width(&column.header, true, header_size.trunc())? <= (w - 2.0 * pad).trunc().max(1.0);
        let content = w - 2.0 * pad;
        let mut cells_fit = true;
        for text in &column.cells {
            cells_fit &= content <= 0.0 || measure.width(text, false, font_size)? <= content;
        }
        if !header_fits || !cells_fit {
            return Err(Diagnostic::refused(refuse!("a table whose cells or headers need more than one line is not supported")));
        }
    }
    // table.py:`compute_table_title_block_layout` (layout.rs:`title_band`) for a one-line title with no
    // subtitle: the baseline at the font's ascent, the band to a half em under it plus 8, floored.
    let (title_baseline, title_height) = match &faces.title {
        Some(title) => {
            let family = measure::stand_in_family(&paint_family(&title.face.family)).to_string();
            let limit = width.trunc();
            let width = book.raw_advance(&title.text, &Font { family: &family, size: title.face.size, weight: 400.0, italic: false }).map_err(diag::err_internal)?;
            if width > limit {
                return Err(Diagnostic::refused(refuse!("a table title that needs more than one line is not supported")));
            }
            let baseline = (title.face.size * 0.8).round_ties_even();
            (baseline, (baseline + title.face.size * 0.5 + 8.0).floor())
        }
        None => (0.0, 0.0),
    };
    let line = header_size.trunc() + style.text_baseline_offset;
    let header_height = style.header.height.trunc().max(line).trunc();
    let rows_top = title_height + header_height + (style.row.height * 0.25).floor();
    let mut x = 0.0;
    let columns = widths
        .iter()
        .map(|&w| {
            let placed = (x, w);
            x += w;
            placed
        })
        .collect();
    Ok(Plan {
        width,
        columns,
        pad,
        content_width,
        title_height,
        title_baseline,
        header_height,
        header_baseline: title_height + header_height - style.text_baseline_offset,
        header_rule_y: (title_height + header_height - style.header.divider.divider.width).trunc(),
        rows_top,
        height: rows_top + n_rows as f64 * style.row.height + style.bottom_padding,
    })
}

/// layout.rs:`emit_divider_segment`. A solid line is a `<rect>` of whole-pixel coverage.
fn divider(x1: f64, x2: f64, y: f64, thickness: f64, color: &str) -> Result<Node, Diagnostic> {
    // Production paints a divider's color as written; only the ones it would reject are refused.
    sanitize_color(color)?;
    let attrs = Attrs::new().quick("fill", color).quick("shape-rendering", "crispEdges");
    Ok(Node::rect(Role::Grid, (x1, y, x2 - x1, thickness), attrs))
}

/// The table as nodes: its root `<svg>`, drawn at the plan's size.
pub fn draw(card: &TableCard, plan: &Plan) -> Result<Vec<Node>, Diagnostic> {
    let (style, faces) = (card.style, card.faces);
    let pad = plan.pad;
    let mut nodes = Vec::new();
    if let Some(title) = &faces.title {
        let attrs = Attrs::new()
            .quick("font-size", title.face.size.to_string())
            .quick("font-style", title.slant.as_str())
            .quick("font-weight", title.face.weight.attr())
            .quick("fill", title.face.color.as_str())
            .quick("font-family", title.face.family.as_str())
            .quick("data-authored-kind", "title");
        let drawn = Run::Tspan { x: Some(0.0), y: Some(plan.title_baseline), dx: None, dy: None, attrs: Attrs::new(), runs: vec![Run::Chars(Chars::new(title.text.as_str(), Escape::Quick))] };
        nodes.push(Node::new(Role::Title, attrs, scene::Shape::Text { at: (0.0, plan.title_baseline), bounds: None, runs: title.full.iter().map(|full| Run::Title(Chars::new(full.as_str(), Escape::Quick))).chain([drawn]).collect() }));
    }
    if let Some(fill) = style.header.background.as_ref().map(|c| sanitize_color(c.as_str())).transpose()?.flatten().filter(|c| !c.eq_ignore_ascii_case("transparent")) {
        nodes.push(Node::rect(Role::Chrome, (0.0, plan.title_height, plan.content_width, plan.header_height), Attrs::new().quick("fill", fill)));
    }
    let header_rule = &style.header.divider.divider;
    if header_rule.visible {
        nodes.push(divider(pad, plan.content_width - pad, plan.header_rule_y, header_rule.width, header_rule.color.as_str())?);
    }
    let (body_family, header_family) = (paint_family(&faces.body.family), paint_family(&faces.header.family));
    let (body_ink, header_ink) = (faces.body.color.as_str(), faces.header.color.as_str());
    let header_weight = faces.header.weight.attr();
    for (column, &(x, w)) in card.table.columns.iter().zip(&plan.columns) {
        // layout.rs:`lay_out`'s header labels: only a centered column centers its header; a
        // right-aligned text column's header stays at the start.
        let (tx, anchor) = if column.align == CellAlign::Center { (x + w / 2.0, "middle") } else { (x + pad, "start") };
        let attrs = Attrs::new()
            .quick("font-size", faces.header.size.to_string())
            .quick("font-weight", header_weight.as_str())
            .quick("fill", header_ink)
            .quick("text-anchor", anchor)
            .quick("font-family", header_family.as_str());
        nodes.push(Node::text(Role::Label, (tx, plan.header_baseline), attrs, column.header.as_str()));
    }
    let stripe = style.row.stripe.as_ref().and_then(|s| s.color.as_ref()).map(|c| c.as_str());
    // Production validates the stripe's color and paints it as written (bind.rs:`bind_drawn`).
    stripe.map(sanitize_color).transpose()?;
    let row_rule = &style.row.divider.divider;
    let n_rows = card.table.rows();
    let row_height = style.row.height;
    for row in 0..n_rows {
        let row_y = plan.rows_top + row as f64 * row_height;
        // layout.rs:`paint_data_rows`: the rule's height comes out of the row's own fill, and the
        // last row has none.
        let rule_height = if row_rule.visible && row + 1 < n_rows { row_rule.width.round_ties_even().max(1.0) } else { 0.0 };
        let mut cells = Vec::new();
        if let (Some(fill), true) = (stripe, row % 2 == 1) {
            cells.push(Node::rect(Role::Chrome, (0.0, row_y, plan.content_width, row_height - rule_height), Attrs::new().quick("fill", fill)));
        }
        let text_y = row_y + row_height / 2.0 + style.text_baseline_offset;
        for (col, (column, &(x, w))) in card.table.columns.iter().zip(&plan.columns).enumerate() {
            // layout.rs:`paint_data_rows`: a text cell has no lane, so `align` alone moves it.
            let (tx, anchor) = match column.align {
                CellAlign::Center => (x + w / 2.0, "middle"),
                CellAlign::Right => (x + w - pad, "end"),
                CellAlign::Left => (x + pad, "start"),
            };
            let attrs = Attrs::new()
                .quick("font-size", faces.body.size.to_string())
                .quick("fill", body_ink)
                .quick("text-anchor", anchor)
                .quick("font-family", body_family.as_str())
                .quick("data-col", col.to_string());
            cells.push(Node::text(Role::Label, (tx, text_y), attrs, column.cells[row].as_str()));
        }
        if rule_height > 0.0 {
            cells.push(divider(pad, plan.content_width - pad, row_y + row_height - rule_height, rule_height, row_rule.color.as_str())?);
        }
        let attrs = Attrs::new().quick("class", "dbt-table-row").quick("data-dbt-row", row.to_string());
        nodes.push(Node::group(Role::Chrome, attrs, cells));
    }
    Ok(vec![Node::new(Role::Chrome, Attrs::new(), scene::Shape::Svg { extent: Extent { width: plan.width, height: plan.height }, children: nodes })])
}
