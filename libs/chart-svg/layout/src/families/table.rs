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

use bind::bound::{NumberCell, Table, TableCell, TableColumn};
use board_model::style::style::Style;
use board_model::style::table::{CellAlign, PaginationMode, TableChartStyle};
use d3rs::py_float::py_float_sum;
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use measure::SAMPLE_ROWS;
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run};

use super::failed::Face;
use crate::board::band::TitleShift;
use crate::board::prose::has_template_syntax;
use crate::title::chart_title_spec;

/// table.rs:`SWATCH_SIZE`: the square a swatch cell paints; `SWATCH_CELL_DEMAND` adds 4px of breathing room.
const SWATCH_SIZE: f64 = 14.0;
const SWATCH_CELL_DEMAND: f64 = 18.0;
/// common.rs:`SUFFIX_GAP`: the gap between a number and its magnitude word, and between a prefix and the number.
const SUFFIX_GAP: f64 = 3.0;
/// `_table.py`'s `numeric_cell_font` on a table whose font is not Source Serif: the face the decimal pad's units compose from.
const PAD_FAMILY: &str = "dbt Sans Tabular";

/// common.rs:`TAB_STYLE`: the numeral face's tabular, lining figures.
const TAB_STYLE: &str = "font-variant-numeric: tabular-nums lining-nums; font-feature-settings: 'tnum' 1, 'lnum' 1;";
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
/// layout.rs:`lay_out`'s fit cascade: the type a table that still overflows steps down to, how much shorter its
/// rows get, and the shortest they go.
const STEPPED_FONT_SIZE: f64 = 11.0;
const STEPPED_ROW_SHRINK: f64 = 4.0;
const STEPPED_ROW_FLOOR: f64 = 16.0;
/// layout.rs:`overflows`. Only a header of at most this many characters drives the fit cascade.
const CASCADE_HEADER_CHARS: usize = 12;

/// The two text faces a table paints: its cells' and its header's.
#[derive(Debug, Clone, PartialEq)]
pub struct TableFaces {
    pub body: Face,
    pub header: Face,
    /// The numeral face (`style.fonts.numeral`) a number paints in, at the body's size, weight and ink.
    pub numeral: Face,
    pub title: Option<TableTitle>,
    pub subtitle: Option<TableSubtitle>,
    /// The "No results" headline of a table over no rows: `style.empty_state`'s size and ink in the body's family.
    pub empty: Face,
    /// The filters line under that headline: the chart title's subtitle face.
    pub filters: Face,
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

/// A table's subtitle as it draws: not cased, in the table's own font family at the subtitle's size and ink.
#[derive(Debug, Clone, PartialEq)]
pub struct TableSubtitle {
    /// The drawn line: the authored subtitle with every run of whitespace collapsed to one space.
    pub text: String,
    /// The subtitle as authored when the drawn line differs from it: it rides along as `<title>`.
    pub full: Option<String>,
    pub face: Face,
    pub slant: String,
}

impl TableFaces {
    /// The faces `style` paints in, each stack drawn under the board's emoji mode. The body's ink is
    /// the table's font color, or the column defaults' (bind.rs:`bind_drawn`); the header's weight is the header font's.
    ///
    /// `title` and `subtitle` are the authored texts and `width` the chart's outer width, which picks the title's
    /// tier (table.py:`title_band`'s `style.title_font`, typography.py:`chart_title_spec`).
    pub fn of(style: &TableChartStyle, placed: &Style, title: Option<&str>, subtitle: Option<&str>, width: f64) -> Result<Self, Diagnostic> {
        let emoji = &placed.font.emoji;
        // bind.rs:`bind_drawn`: the font's own color passes as written, and production paints an
        // empty ink as `fill=""`, which is no color.
        // bind.rs:`bind_drawn`: `column_defaults.font.color` replaces the font's, and a header with no color of its own
        // takes it (bind refuses every color that names a query column, which is per-row paint rather than an ink).
        let default_ink = style.column_defaults.font.as_ref().and_then(|font| font.color.as_ref()).map(|color| sanitize_color(color.as_str())).transpose()?.flatten();
        let body_ink = match default_ink {
            Some(ink) => ink,
            None => Some(style.font.color.as_str()).filter(|ink| !ink.is_empty()).ok_or_else(|| Diagnostic::refused(refuse!("a table with an empty font color is not supported")))?,
        };
        let header = &style.header.font;
        let header_ink = sanitize_color(header.color.as_str())?.unwrap_or(body_ink);
        let title = title.filter(|text| !text.is_empty()).map(|text| table_title(text, style, placed, width)).transpose()?;
        let subtitle = subtitle.filter(|text| !text.is_empty()).map(|text| table_subtitle(text, style, placed)).transpose()?;
        let numeral = placed.fonts.get("numeral").and_then(|font| font.family.as_ref()).ok_or_else(|| diag::err_internal("style.fonts.numeral.family is unset after the theme cascade"))?;
        // The serif numeral face's advances and pad are not modeled.
        if numeral.as_str().contains("Source Serif") {
            return Err(Diagnostic::refused(refuse!("a table whose numeral font is Source Serif is not supported")));
        }
        // `_table.py`: the pad units compose from dbt Sans Tabular, or from Source Serif 4 on a table whose
        // font is Source Serif; the serif units are not modeled. The paint face stays `style.fonts.numeral`.
        if style.font.family.as_str().contains("Source Serif") {
            return Err(Diagnostic::refused(refuse!("a table whose font is Source Serif is not supported")));
        }
        let (empty, filters) = (&style.empty_state.font, &placed.charts.title.base.subtitle.font);
        Ok(Self {
            empty: Face::of(style.font.family.as_str(), empty.size, &empty.weight, empty.color.as_str(), emoji),
            filters: Face::of(filters.family.as_str(), filters.size, &filters.weight, filters.color.as_str(), emoji),
            numeral: Face::of(numeral.as_str(), style.font.size, &style.font.weight, body_ink, emoji),
            body: Face::of(style.font.family.as_str(), style.font.size, &style.font.weight, body_ink, emoji),
            header: Face::of(header.family.as_str(), header.size, &header.weight, header_ink, emoji),
            title,
            subtitle,
        })
    }
}

fn table_title(text: &str, style: &TableChartStyle, placed: &Style, width: f64) -> Result<TableTitle, Diagnostic> {
    if style.titled.title.is_some() {
        return Err(Diagnostic::refused(refuse!("a table title styled on `charts.table.title` is not supported")));
    }
    if has_template_syntax(text) {
        return Err(Diagnostic::refused(refuse!("a table title with template syntax is not supported")));
    }
    let font = &placed.charts.title.font;
    let (size, weight, family) = chart_title_spec(placed, width)?;
    let cased = display_text::case::apply_case(text, font.case.as_str()).map_err(diag::err_internal)?;
    // `wrap_text_with` collapses every run of whitespace to one space before it measures and paints.
    let drawn = cased.split_whitespace().collect::<Vec<_>>().join(" ");
    Ok(TableTitle { full: (drawn != cased).then(|| text.to_string()), text: drawn, face: Face::of(&family, size, &weight, font.color.as_str(), &placed.font.emoji), slant: font.style.as_str().to_string() })
}

/// layout.rs:`title_band`'s subtitle: `charts.title.subtitle`'s size, weight, ink and slant in the table's own
/// font family (`style.table.font.family`), never cased.
fn table_subtitle(text: &str, style: &TableChartStyle, placed: &Style) -> Result<TableSubtitle, Diagnostic> {
    if has_template_syntax(text) {
        return Err(Diagnostic::refused(refuse!("a table subtitle with template syntax is not supported")));
    }
    let font = &placed.charts.title.base.subtitle.font;
    // layout.rs:`wrap_title`: a text with no words has no lines, and falls back to the text itself (`splitlines() or [text]`).
    let words = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let drawn = if words.is_empty() { text.to_string() } else { words };
    Ok(TableSubtitle { full: (drawn != text).then(|| text.to_string()), text: drawn, face: Face::of(style.font.family.as_str(), font.size, &font.weight, font.color.as_str(), &placed.font.emoji), slant: font.style.as_str().to_string() })
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
    /// The cells' padding, truncated as the theme's is (bind.rs:`bind_drawn`), after the fit cascade.
    pad: f64,
    /// The body and header type and the row height after the fit cascade, which steps them down to 11px and 4px
    /// shorter when the cells still overflow at the stepped padding.
    font_size: f64,
    header_size: f64,
    row_height: f64,
    content_width: f64,
    /// The title band's height above the header; 0 with no title.
    title_height: f64,
    /// Whether the table has a title, which a `cols` row aligns by.
    titled: bool,
    /// The title's baseline, drawn when the table has a title.
    title_baseline: f64,
    /// The subtitle's baseline, drawn when the table has a subtitle.
    subtitle_baseline: f64,
    header_height: f64,
    header_baseline: f64,
    header_rule_y: f64,
    rows_top: f64,
    /// Each number column's lane (`None` for any other column).
    lanes: Vec<Option<Lane>>,
    /// The units a trimmed number's pad is composed from; `None` when no cell is padded.
    pad_units: Option<(String, String)>,
    /// The "No results" body of a table over no rows, which has no columns, header or rows to place.
    empty: Option<EmptyBody>,
    /// The table's height: the `<svg>`'s.
    pub height: f64,
}

/// Where a zero-row table's text sits (`layout.rs:lay_out_empty`): the headline's baseline, and under it the filters
/// line cut to the table's width.
#[derive(Debug, Clone, PartialEq)]
struct EmptyBody {
    headline_y: f64,
    filters: Option<String>,
}

/// layout.rs:`EMPTY_LINE_GAP`: between the headline and the filters line under it.
const EMPTY_LINE_GAP: f64 = 6.0;

/// Where a number column's three lanes sit: the x each is anchored at and the digits' width.
#[derive(Debug, Clone, PartialEq)]
struct Lane {
    prefix_x: f64,
    number_x: f64,
    number_width: f64,
    suffix_x: f64,
}

/// layout.rs:`numeric_lane_positions`: the number lane's right edge puts its widest cell's center on the
/// content's midpoint, the prefix sits `SUFFIX_GAP` left of the digits when any row shows one, and a
/// magnitude word `SUFFIX_GAP` right (a unit touches). A band that pokes past one side of the content while
/// the other has room shifts by the smaller of the overflow and that room.
fn lane_positions(x: f64, width: f64, pad: f64, (digits, prefix, suffix): (f64, f64, f64), magnitude: bool) -> Lane {
    let (left, right) = (x + pad, x + width - pad);
    let number_x = (left + right) / 2.0 + digits / 2.0;
    let suffix_x = number_x + if magnitude { SUFFIX_GAP } else { 0.0 };
    let prefix_x = number_x - digits - if prefix > 0.0 { SUFFIX_GAP } else { 0.0 };
    let overflow_left = left - (prefix_x - prefix);
    let overflow_right = (suffix_x + suffix) - right;
    let shift = if overflow_left > 0.0 && overflow_right < 0.0 {
        overflow_left.min(-overflow_right)
    } else if overflow_right > 0.0 && overflow_left < 0.0 {
        -overflow_right.min(-overflow_left)
    } else {
        0.0
    };
    Lane { prefix_x: prefix_x + shift, number_x: number_x + shift, number_width: digits, suffix_x: suffix_x + shift }
}

impl Plan {
    /// A titled table's (ascent, tail) in its `cols` row's title band (layout.rs:`title_geometry`): the
    /// baseline below the table's top, and the body's top below the baseline. A table with only a subtitle has
    /// none: it takes no shift and adds nothing to the row (`title_band.py:106`, title_shift.rs:`chart_title_metrics`).
    pub fn title_metrics(&self) -> Option<(f64, f64)> {
        self.titled.then_some((self.title_baseline, self.title_height - self.title_baseline))
    }

    /// Whether production draws every row at its height in a slot `slot_height` tall, rather than
    /// paging or squeezing them (layout.rs:`lay_out`, the grow-by-2 short-circuit): the rows must fit
    /// under the header, its gap and the bottom padding.
    pub fn fits(&self, card: &TableCard, slot_height: f64) -> bool {
        let style = card.style;
        if self.empty.is_some() {
            return slot_height >= self.height;
        }
        !style.pagination.enabled || slot_height - self.rows_top - style.bottom_padding >= card.table.rows() as f64 * self.row_height
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

/// colors.py:`sanitize_color` (table.rs:`sanitize_color`): empty is unset, a color `bind::table::sanitized_color` accepts
/// stands, anything else is production's `ERR-CSS-COLOR-INVALID-AT-RENDER`.
fn sanitize_color(color: &str) -> Result<Option<&str>, Diagnostic> {
    if color.is_empty() {
        return Ok(None);
    }
    bind::table::sanitized_color(color).map(Some).ok_or_else(|| Diagnostic::refused(refuse!("a table color that is not a hex color, transparent or none is not supported")))
}

/// Every leaf of the table's style that this slice draws another way than production does.
fn refuse_unmodeled(style: &TableChartStyle) -> Result<(), Diagnostic> {
    let unmodeled = [
        (style.row_numbers.visible, "row numbers"),
        (!style.wrap, "`style.wrap: false`"),
        (style.background.is_some(), "a `style.background`"),
        (style.header.font_compact.is_some(), "a compact header font"),
        (!style.header.divider.continuous && style.header.divider.divider.visible, "a header divider under each column"),
        (style.header.divider.divider.dasharray.is_some() || style.row.divider.divider.dasharray.is_some(), "a dashed divider"),
        (style.column_layout.freeze > 0, "`column_layout.freeze`"),
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
    /// The numeral stack as production measures it: its first entry's own face (layout.rs:`tab_measure_family`), not Inter's stand-in.
    numeral: String,
    /// The decimal reservation's digit and mark units, composed from the numeral face when a number cell is padded.
    pad_units: Option<(String, String)>,
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

    /// A number's digits with its pad (`Cell.text`): the string the number lane paints and measures.
    fn digits(&self, number: &NumberCell) -> String {
        padded_digits(number, self.pad_units.as_ref())
    }

    /// The string a cell's width demand reads (`measured_cell_text`): a number's with its pad. The fit
    /// cascade reads the cell without it (`unpadded_cell_text`, [`TableCell::text`]).
    fn demand_text<'c>(&self, cell: &'c TableCell) -> std::borrow::Cow<'c, str> {
        match cell {
            TableCell::Number(number) => number.measured_around(&self.digits(number)).into(),
            _ => cell.text(),
        }
    }

    /// A cell's advance in the face it paints in (layout.rs:`cell_measure_family`): a number column's
    /// numbers and nulls in the numeral face, a date in the numeral face, any other cell in the body face.
    fn cell_advance(&self, column: &TableColumn, cell: &TableCell, text: &str, size: f64) -> Result<f64, String> {
        if column.is_number_lane() || matches!(cell, TableCell::Date(_)) {
            return self.book.raw_advance(text, &Font { family: &self.numeral, size, weight: 400.0, italic: false });
        }
        self.advance(text, false, size)
    }
}

/// A number's digits followed by its decimal reservation (`decimal_pad_for`'s pad).
fn padded_digits(number: &NumberCell, units: Option<&(String, String)>) -> String {
    match units {
        Some((digit, mark)) => format!("{}{}", number.number, d3rs::numeral_pad::decimal_reservation_pad(number.pad.missing, number.pad.dot_kept, digit, mark)),
        None => number.number.clone(),
    }
}

/// One column's inputs to the allocator.
#[derive(Clone)]
struct Column {
    /// The p95 of the cells' widths plus both paddings (`measure::column_demand_of`).
    demand: f64,
    /// The header label's one-line width.
    header_demand: f64,
    /// The widest unbreakable token of the header and the cells, plus both paddings.
    word_floor: f64,
    /// `max_width:`, the hard bound a text column's share and word floor never pass.
    max_width: Option<f64>,
}

fn column_inputs(card: &TableCard, measure: &Measurer, pad: f64) -> Result<Vec<Column>, Diagnostic> {
    let (font_size, header_size) = (card.faces.body.size, card.faces.header.size);
    card.table
        .columns
        .iter()
        .map(|column| {
            // layout.rs:`column_inputs`: a swatch cell holds a color, not text: the painted square sets the width.
            if column.swatch {
                return Ok(Column { demand: SWATCH_CELL_DEMAND + 2.0 * pad, header_demand: SWATCH_CELL_DEMAND, word_floor: SWATCH_CELL_DEMAND + 2.0 * pad, max_width: column.max_width });
            }
            let cell_demand = measure::column_demand_of(column.cells.iter().map(|cell| measure.cell_advance(column, cell, &measure.demand_text(cell), font_size)), pad).map_err(diag::err_internal)?;
            // layout.rs:`column_inputs`: a hidden header paints no label, so it demands no width and sets no floor.
            let header_visible = card.style.header.visible;
            let mut token = if header_visible { measure::widest_word_with(&column.header, |t| measure.advance(t, true, header_size)).map_err(diag::err_internal)?.0 } else { 0.0 };
            for cell in column.cells.iter().take(SAMPLE_ROWS) {
                token = token.max(measure::widest_word_with(&measure.demand_text(cell), |t| measure.cell_advance(column, cell, t, font_size)).map_err(diag::err_internal)?.0);
            }
            Ok(Column { demand: cell_demand, header_demand: if header_visible { measure.width(&column.header, true, header_size)? } else { 0.0 }, word_floor: token + 2.0 * pad, max_width: column.max_width })
        })
        .collect()
}

/// layout.rs:`auto_column_demand` and `natural_auto_width`. The least width that shows an auto column in full: its
/// demand raised to the word floor (a floor never passes a declared `max_width`), a compact column inflated by
/// `content_headroom` when text columns exist, at least the one-line header plus both paddings, and a text column
/// no wider than its `max_width`.
fn natural_widths(columns: &[Column], headroom: f64, pad: f64) -> Vec<(f64, bool)> {
    let base: Vec<f64> = columns.iter().map(|c| c.demand.max(c.max_width.map_or(c.word_floor, |cap| c.word_floor.min(cap))).max(1.0)).collect();
    let text: Vec<bool> = base.iter().map(|&d| d > COMPACT_DEMAND_CEILING).collect();
    let factor = if text.iter().any(|&t| t) { 1.0 + headroom } else { 1.0 };
    columns
        .iter()
        .zip(base.iter().zip(&text))
        .map(|(c, (&d, &is_text))| {
            let width = (if is_text { d } else { d * factor }).max(c.header_demand + 2.0 * pad);
            (if is_text { c.max_width.map_or(width, |cap| width.min(cap)) } else { width }, is_text)
        })
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

/// layout.rs:`allocate_text_columns`. Proportional shares, a share past its `max_width` pinned there with the excess
/// redistributed among the rest, the uncapped cluster-equalized, each raised to its word floor last (a floor
/// never passes the cap).
fn allocate_text(demands: &[f64], floors: &[f64], caps: &[Option<f64>], budget: f64, threshold: f64) -> Vec<f64> {
    let floor_of = |i: usize| caps[i].map_or(floors[i], |cap| floors[i].min(cap));
    if demands.len() == 1 {
        return vec![caps[0].map_or(budget, |cap| budget.min(cap)).max(floor_of(0))];
    }
    let mut to_allocate: Vec<(usize, f64)> = proportional_allocate(demands, budget).into_iter().enumerate().collect();
    let mut widths: Vec<f64> = vec![0.0; demands.len()];
    loop {
        let mut free: Vec<(usize, f64)> = Vec::new();
        let mut leftover = 0.0_f64;
        for &(i, w) in &to_allocate {
            match caps[i] {
                Some(cap) if w > cap => {
                    widths[i] = cap;
                    leftover += w - cap;
                }
                _ => free.push((i, w)),
            }
        }
        if leftover == 0.0 || free.is_empty() {
            free.iter().for_each(|&(i, w)| widths[i] = w);
            break;
        }
        let total_free = py_float_sum(&free.iter().map(|&(_, w)| w).collect::<Vec<_>>());
        if total_free > 0.0 {
            let new_total = total_free + leftover;
            free.iter_mut().for_each(|(_, w)| *w = *w / total_free * new_total);
        }
        to_allocate = free;
    }
    let uncapped: Vec<usize> = (0..demands.len()).filter(|&i| caps[i].is_none()).collect();
    if uncapped.len() > 1 {
        let shares: Vec<f64> = uncapped.iter().map(|&i| widths[i]).collect();
        let equalized = cluster_and_equalize(&shares, py_float_sum(&shares), threshold);
        uncapped.iter().zip(equalized).for_each(|(&i, w)| widths[i] = w);
    }
    for (i, w) in widths.iter_mut().enumerate() {
        let floor = floor_of(i);
        if floor > 0.0 && *w < floor {
            *w = floor;
        }
    }
    widths
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

/// layout.rs:`calculate_column_layout_port` for the auto columns (the `width:` pins are out), in a
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
        let caps: Vec<Option<f64>> = text.iter().map(|&i| columns[i].max_width).collect();
        let allocated = allocate_text(&demands, &floors, &caps, text_budget, threshold);
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

/// layout.rs:`calculate_column_layout_port`'s `has_room`: what the pins leave of the budget, never negative, covers
/// every auto column's natural width. It is compared as production compares it, not as a sum against the width,
/// which differs by one float rounding step at the boundary. The sums run in authored order where production's
/// run in column-name order, so they can still differ in the last bit between the two orders.
fn has_room(table_width: f64, pinned: f64, natural_total: f64) -> bool {
    (table_width - pinned).max(0.0) >= natural_total
}

/// Where each column starts, given the widths.
fn plan_columns(widths: &[f64]) -> Vec<(f64, f64)> {
    let mut x = 0.0;
    widths
        .iter()
        .map(|&w| {
            let placed = (x, w);
            x += w;
            placed
        })
        .collect()
}

/// The title band above the header: the baseline a `cols` row aligns by, the subtitle's, and the band's height.
struct TitleBand {
    baseline: f64,
    subtitle_baseline: f64,
    height: f64,
}

/// Whether `text` is wider than `compute_title_limit`'s `int(width - 2 * outer_padding)`; `outer_padding` is refused,
/// so the limit is the width. Production wraps or cuts a line past it, which is not modeled.
fn overflows(text: &str, face: &Face, width: f64, book: &FontBook) -> Result<bool, Diagnostic> {
    let family = measure::stand_in_family(&paint_family(&face.family)).to_string();
    let advance = book.raw_advance(text, &Font { family: &family, size: face.size, weight: 400.0, italic: false }).map_err(diag::err_internal)?;
    Ok(advance > width.trunc())
}

/// table.py:`compute_table_title_block_layout` (layout.rs:`title_band`) for a one-line title and subtitle. The
/// title's baseline is `title_ascent` of the tiered title size and its area runs a half em under it plus 8,
/// floored. A subtitle leads when there is no title (its baseline is `title_ascent` of its own size); under a title
/// it sits `title_subtitle_gap` plus its own ascent below the title's descent (`_subtitle_baseline_below_title`).
/// Its area runs a half em under its baseline plus 8; the band is that area, floored, when it leads, else the tallest
/// of the title's area, `title_row.height` and the subtitle's, each floored. Zero without either.
/// A `cols` row's shift moves the title and its subtitle down `title_dy`, the subtitle's rounded afresh from the
/// moved title baseline, and grows the band by `body_dy`. A subtitle with no title is never shifted
/// (`title_band.py:106`), so only a titled band reads `shift`.
fn title_band(card: &TableCard, book: &FontBook, width: f64, shift: TitleShift) -> Result<TitleBand, Diagnostic> {
    let faces = card.faces;
    let title_size = faces.title.as_ref().map(|title| title.face.size);
    if title_size.is_none() && faces.subtitle.is_none() {
        return Ok(TitleBand { baseline: 0.0, subtitle_baseline: 0.0, height: 0.0 });
    }
    if let Some(title) = &faces.title {
        if overflows(&title.text, &title.face, width, book)? {
            return Err(Diagnostic::refused(refuse!("a table title that needs more than one line is not supported")));
        }
    }
    let baseline = title_size.map_or(0.0, |size| (size * 0.8).round_ties_even());
    let title_area = title_size.map_or(0.0, |size| (baseline + size * 0.5 + 8.0).floor());
    let band_height = |area: f64| area + shift.body_dy;
    let Some(subtitle) = &faces.subtitle else { return Ok(TitleBand { baseline: baseline + shift.title_dy, subtitle_baseline: 0.0, height: band_height(title_area) }) };
    if overflows(&subtitle.text, &subtitle.face, width, book)? {
        return Err(Diagnostic::refused(refuse!("a table subtitle that needs more than one line is not supported")));
    }
    let size = subtitle.face.size;
    let Some(title_size) = title_size else {
        let subtitle_baseline = (size * 0.8).round_ties_even();
        return Ok(TitleBand { baseline, subtitle_baseline, height: (subtitle_baseline + size * 0.5 + 8.0).floor() });
    };
    let below = |title_baseline: f64| (title_baseline + title_size * 0.2 + card.style.title_subtitle_gap + size * 0.8).round_ties_even();
    let subtitle_area = (below(baseline) + size * 0.5 + 8.0).floor();
    let height = title_area.max(card.style.title_row.height.floor()).max(subtitle_area);
    Ok(TitleBand { baseline: baseline + shift.title_dy, subtitle_baseline: below(baseline + shift.title_dy), height: band_height(height) })
}

/// layout.rs:`lay_out_empty`: a table over no rows is its title band, its header band and gap when the header
/// shows, and a body of one headline with the filters line under it centered between them and the bottom
/// padding. The canvas grows by the line and its gap.
fn plan_empty(card: &TableCard, filters: Option<&str>, book: &FontBook, width: f64, shift: TitleShift) -> Result<Plan, Diagnostic> {
    let (style, faces) = (card.style, card.faces);
    let TitleBand { baseline: title_baseline, subtitle_baseline, height: title_height } = title_band(card, book, width, shift)?;
    // `outer_padding` is refused by `refuse_unmodeled`, so the table spans its whole width.
    let (header_height, header_gap) = if style.header.visible { (style.header.height.trunc(), (style.row.height * 0.25).trunc()) } else { (0.0, 0.0) };
    let filters = filters
        .map(|line| {
            let family = measure::stand_in_family(&paint_family(&faces.filters.family)).to_string();
            let measured = Font { family: &family, size: faces.filters.size, weight: 400.0, italic: false };
            crate::plot::empty::truncate_label(line, width, |text| book.raw_advance(text, &measured).map_err(diag::err_internal))
        })
        .transpose()?;
    let line_height = filters.as_ref().map_or(0.0, |_| faces.filters.size + EMPTY_LINE_GAP);
    let height = title_height + header_height + header_gap + style.bottom_padding.trunc() + line_height;
    Ok(Plan {
        width,
        columns: Vec::new(),
        pad: 0.0,
        font_size: faces.empty.size,
        header_size: faces.header.size,
        row_height: style.row.height,
        content_width: width,
        title_height,
        titled: card.faces.title.is_some(),
        title_baseline,
        subtitle_baseline,
        header_height,
        header_baseline: 0.0,
        header_rule_y: 0.0,
        rows_top: title_height + header_height + header_gap,
        lanes: Vec::new(),
        pad_units: None,
        empty: Some(EmptyBody { headline_y: (height - line_height) / 2.0, filters }),
        height,
    })
}

/// The table at `width`: its columns sized, its rows and header placed, and every leaf this slice
/// does not draw refused. `book` measures as production does (`measure::stand_in_family`, raw
/// advances).
pub fn plan(card: &TableCard, book: &FontBook, width: f64, shift: TitleShift) -> Result<Plan, Diagnostic> {
    let style = card.style;
    refuse_unmodeled(style)?;
    let n_rows = card.table.rows();
    let pagination = &style.pagination;
    if pagination.enabled && n_rows > pagination.page_rows.map_or(n_rows, |rows| rows as usize + PAGINATION_GROW_CAP) {
        return Err(Diagnostic::refused(refuse!("a table with more rows than one page is not supported")));
    }
    // table.rs:`row_scrollbar_band`: a scrolling table longer than `page_rows` reserves the bar's band.
    if pagination.enabled && pagination.mode == PaginationMode::Scroll && pagination.page_rows.is_none_or(|rows| n_rows > rows as usize) {
        return Err(Diagnostic::refused(refuse!("a table with scrolling rows is not supported")));
    }
    let pad = style.column_layout.cell_padding.trunc();
    // `outer_padding` is refused above, so the table spans its whole width.
    let table_width = width;
    if table_width <= 0.0 {
        return Err(Diagnostic::refused(refuse!("a table with no width is not supported")));
    }
    if let Some(empty) = &card.table.empty {
        return plan_empty(card, empty.filters.as_deref(), book, width, shift);
    }
    let faces = card.faces;
    let padded = card.table.columns.iter().flat_map(|column| &column.cells).any(|cell| matches!(cell, TableCell::Number(number) if number.pad.missing > 0));
    let pad_units = if padded {
        Some(measure::reservation::compose_decimal_units(&book.production_measurer(PAD_FAMILY), PAD_FAMILY).map_err(diag::err_internal)?)
    } else {
        None
    };
    let measure = Measurer { book, body: measure::stand_in_family(&paint_family(&faces.body.family)).to_string(), header: measure::stand_in_family(&paint_family(&faces.header.family)).to_string(), numeral: faces.numeral.family.clone(), pad_units };
    let inputs = column_inputs(card, &measure, pad)?;
    // layout.rs:`calculate_column_layout_port`: a `width:` pins its column, and the rest share what the
    // pins leave (a single one takes it all).
    let pins: Vec<Option<f64>> = card.table.columns.iter().map(|column| column.width).collect();
    let auto: Vec<usize> = (0..pins.len()).filter(|&i| pins[i].is_none()).collect();
    let auto_inputs: Vec<Column> = auto.iter().map(|&i| inputs[i].clone()).collect();
    let pinned = py_float_sum(&pins.iter().flatten().copied().collect::<Vec<_>>());
    let natural = natural_widths(&auto_inputs, style.column_layout.content_headroom.get(), pad);
    // A table that needs more than its width scrolls its columns or squeezes its words
    // (layout.rs:`scroll_plan`).
    if !has_room(table_width, pinned, py_float_sum(&natural.iter().map(|&(w, _)| w).collect::<Vec<_>>())) || table_width < pinned {
        return Err(Diagnostic::refused(refuse!("a table whose columns need more than its width is not supported")));
    }
    let mut auto_widths = if auto.is_empty() { Vec::new() } else { allocate(&auto_inputs, &natural, (table_width - pinned).max(0.0), style.column_layout.width_similarity_threshold.get()) }.into_iter();
    let widths: Vec<f64> = pins.iter().map(|pin| pin.unwrap_or_else(|| auto_widths.next().expect("an allocated width per auto column"))).collect();
    let content_width = py_float_sum(&widths);
    if content_width > table_width + OVERFLOW_EPSILON {
        // The word floors are applied after the 60% cap (`allocate_text_columns`), so a floored column
        // can take back more than the cap released; production draws it with an overflow warning.
        return Err(Diagnostic::refused(refuse!("a table whose word floors push its columns past its width is not supported")));
    }
    let (mut font_size, mut header_size, mut row_height) = (faces.body.size, faces.header.size, style.row.height);
    // The fit cascade (layout.rs:`overflows`): a short header or a number wider than its column's content area
    // steps the cell padding down to half, at least 6; one still too wide steps the type down to 11px, the
    // row 4px shorter (at least 16), and the header to 11px whether or not it inherits the body's size. The
    // columns keep the widths allocated at the theme's type.
    let overflows = |pad: f64, font_size: f64, header_size: f64| -> Result<bool, Diagnostic> {
        for (column, &w) in card.table.columns.iter().zip(&widths) {
            if column.swatch {
                continue;
            }
            let content = w - 2.0 * pad - CASCADE_BREATHING;
            if style.header.visible && card.table.columns.len() > 1 && column.header.chars().count() <= CASCADE_HEADER_CHARS && measure.width(&column.header, false, header_size)? > content {
                return Ok(true);
            }
            // A number or a date never wraps, so one wider than its column steps the cascade too.
            if column.has_lane() {
                for cell in column.cells.iter().take(SAMPLE_ROWS) {
                    if measure.cell_advance(column, cell, &cell.text(), font_size).map_err(diag::err_internal)? > content {
                        return Ok(true);
                    }
                }
            }
        }
        Ok(false)
    };
    let mut pad = pad;
    if overflows(pad, font_size, header_size)? {
        pad = (pad / 2.0).floor().max(6.0);
        if overflows(pad, font_size, header_size)? {
            font_size = STEPPED_FONT_SIZE;
            row_height = (row_height - STEPPED_ROW_SHRINK).max(STEPPED_ROW_FLOOR);
            header_size = STEPPED_FONT_SIZE;
        }
    }
    // layout.rs:`wrapped_headers` and the wrap pass: a header or cell wider than the column's content
    // area takes another line, and the row grows.
    for (column, &w) in card.table.columns.iter().zip(&widths) {
        let header_fits = !style.header.visible || measure.width(&column.header, true, header_size.trunc())? <= (w - 2.0 * pad).trunc().max(1.0);
        let content = w - 2.0 * pad;
        let mut cells_fit = true;
        // A swatch cell is a square and a number or a date never wraps: only their headers can.
        for cell in column.cells.iter().filter(|_| !column.swatch && !column.has_lane()) {
            cells_fit &= content <= 0.0 || measure.cell_advance(column, cell, &cell.text(), font_size).map_err(diag::err_internal)? <= content;
        }
        if !header_fits || !cells_fit {
            return Err(Diagnostic::refused(refuse!("a table whose cells or headers need more than one line is not supported")));
        }
    }
    // layout.rs:`lay_out`'s lane pass: a number column's prefix, digits and suffix share one rhythm, each
    // lane as wide as its widest cell (a null sits outside the lane and widens none). A date column's lane is
    // its dates alone (`numeric_lane_positions` with no affix).
    let lanes = card
        .table
        .columns
        .iter()
        .zip(&plan_columns(&widths))
        .map(|(column, &(x, w))| {
            if !column.has_lane() {
                return Ok(None);
            }
            let advance = |cell: &TableCell, text: &str| measure.cell_advance(column, cell, text, font_size).map_err(diag::err_internal);
            let (mut digits, mut prefix, mut suffix) = (0.0_f64, 0.0_f64, 0.0_f64);
            for cell in &column.cells {
                match cell {
                    TableCell::Number(number) => {
                        digits = digits.max(advance(cell, &measure.digits(number))?);
                        prefix = prefix.max(number.prefix.as_deref().map_or(Ok(0.0), |text| advance(cell, text))?);
                        suffix = suffix.max(number.suffix.as_deref().map_or(Ok(0.0), |text| advance(cell, text))?);
                    }
                    TableCell::Date(text) => digits = digits.max(advance(cell, text)?),
                    TableCell::Text(_) | TableCell::Null => {}
                }
            }
            // A date wider than its column is cut with an ellipsis, which is not modeled.
            if column.is_date_lane() && digits > w - 2.0 * pad {
                return Err(Diagnostic::refused(refuse!("a table date wider than its column is not supported")));
            }
            let magnitude = column.cells.iter().find_map(|cell| match cell {
                TableCell::Number(number) => number.suffix.as_ref().map(|_| number.magnitude_suffix),
                _ => None,
            });
            Ok(Some(lane_positions(x, w, pad, (digits, prefix, suffix), magnitude.unwrap_or(false))))
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    let TitleBand { baseline: title_baseline, subtitle_baseline, height: title_height } = title_band(card, book, width, shift)?;
    let line = header_size.trunc() + style.text_baseline_offset;
    let header_height = if style.header.visible { style.header.height.trunc().max(line).trunc() } else { 0.0 };
    let header_gap = if style.header.visible { (row_height * 0.25).floor() } else { 0.0 };
    let rows_top = title_height + header_height + header_gap;
    let columns = plan_columns(&widths);
    Ok(Plan {
        width,
        columns,
        pad,
        font_size,
        header_size,
        row_height,
        content_width,
        title_height,
        titled: card.faces.title.is_some(),
        title_baseline,
        subtitle_baseline,
        header_height,
        header_baseline: title_height + header_height - style.text_baseline_offset,
        header_rule_y: (title_height + header_height - style.header.divider.divider.width).trunc(),
        rows_top,
        lanes,
        pad_units: measure.pad_units,
        empty: None,
        height: rows_top + n_rows as f64 * row_height + style.bottom_padding,
    })
}

/// layout.rs:`emit_divider_segment`. A solid line is a `<rect>` of whole-pixel coverage.
fn divider(x1: f64, x2: f64, y: f64, thickness: f64, color: &str) -> Result<Node, Diagnostic> {
    // Production paints a divider's color as written; only the ones it would reject are refused.
    sanitize_color(color)?;
    let attrs = Attrs::new().quick("fill", color).quick("shape-rendering", "crispEdges");
    Ok(Node::rect(Role::Grid, (x1, y, x2 - x1, thickness), attrs))
}

/// layout.rs:`paint_data_rows`' lane branch: one `<text>` in the numeral face whose prefix, digits and
/// suffix are tspans each anchored at its lane; a null is the dash centered on the digits' lane, and a date
/// one run end-anchored at the lane's number edge.
fn lane_cell(cell: &TableCell, lane: &Lane, pad_units: Option<&(String, String)>, (y, col, font_size): (f64, usize, f64), faces: &TableFaces, (ink, weight): (&str, Option<&str>)) -> Result<Node, Diagnostic> {
    let tspan = |x: f64, anchor: &str, text: &str| Run::Tspan { x: Some(x), y: None, dx: None, dy: None, attrs: Attrs::new().quick("text-anchor", anchor), runs: vec![Run::Chars(Chars::new(text, Escape::Quick))] };
    let attrs = Attrs::new()
        .quick("data-dct-role", "table-cell")
        .quick("font-size", font_size.to_string())
        .quick("fill", ink)
        .quick("font-family", faces.numeral.family.as_str())
        .quick("style", TAB_STYLE)
        .quick_some("font-weight", weight)
        .quick("data-col", col.to_string());
    let (x, attrs, runs) = match cell {
        TableCell::Number(number) => {
            let runs: Vec<Run> = number
                .prefix
                .iter()
                .map(|prefix| tspan(lane.prefix_x, "end", prefix))
                .chain([tspan(lane.number_x, "end", &padded_digits(number, pad_units))])
                .chain(number.suffix.iter().map(|suffix| tspan(lane.suffix_x, "start", suffix)))
                .collect();
            let x = number.prefix.as_ref().map_or(lane.number_x, |_| lane.prefix_x);
            (x, attrs.quick("data-value", number.value.as_str()), runs)
        }
        TableCell::Null => {
            let x = lane.number_x - lane.number_width / 2.0;
            (x, attrs, vec![tspan(x, "middle", d3rs::native::NULL_DISPLAY)])
        }
        TableCell::Date(text) => {
            // layout.rs:`paint_data_rows`: a date is a plain run end-anchored at the lane's number edge.
            let attrs = Attrs::new()
                .quick("data-dct-role", "table-cell")
                .quick("font-size", font_size.to_string())
                .quick("fill", ink)
                .quick("text-anchor", "end")
                .quick("font-family", faces.numeral.family.as_str())
                .quick("style", TAB_STYLE)
                .quick_some("font-weight", weight)
                .quick("data-col", col.to_string());
            return Ok(Node::text(Role::Label, (lane.number_x, y), attrs, text));
        }
        TableCell::Text(_) => return Err(diag::err_internal("a text cell in a number lane")),
    };
    Ok(Node::new(Role::Label, attrs, scene::Shape::Text { at: (x, y), bounds: None, runs }))
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
            .quick("class", "role-title-text")
            .quick("data-authored-kind", "title");
        let drawn = Run::Tspan { x: Some(0.0), y: Some(plan.title_baseline), dx: None, dy: None, attrs: Attrs::new(), runs: vec![Run::Chars(Chars::new(title.text.as_str(), Escape::Quick))] };
        nodes.push(Node::new(Role::Title, attrs, scene::Shape::Text { at: (0.0, plan.title_baseline), bounds: None, runs: title.full.iter().map(|full| Run::Title(Chars::new(full.as_str(), Escape::Quick))).chain([drawn]).collect() }));
    }
    if let Some(subtitle) = &faces.subtitle {
        // layout.rs:`emit_title_band`: no `font-weight`, and the full text rides along when the drawn line differs.
        let attrs = Attrs::new()
            .num("font-size", subtitle.face.size)
            .quick("font-style", subtitle.slant.as_str())
            .quick("fill", subtitle.face.color.as_str())
            .quick("font-family", paint_family(&subtitle.face.family))
            .quick("class", "role-title-subtitle")
            .quick("data-authored-kind", "subtitle");
        let drawn = Run::Tspan { x: Some(0.0), y: Some(plan.subtitle_baseline), dx: None, dy: None, attrs: Attrs::new(), runs: vec![Run::Chars(Chars::new(subtitle.text.as_str(), Escape::Quick))] };
        nodes.push(Node::new(Role::Title, attrs, scene::Shape::Text { at: (0.0, plan.subtitle_baseline), bounds: None, runs: subtitle.full.iter().map(|full| Run::Title(Chars::new(full.as_str(), Escape::Quick))).chain([drawn]).collect() }));
    }
    if let Some(empty) = &plan.empty {
        // layout.rs:`lay_out_empty`: the headline's baseline is where the body's text block starts; the filters line
        // sits `EMPTY_LINE_GAP` plus its own size under it.
        let center = plan.width / 2.0;
        let text = |face: &Face, y: f64, text: &str| {
            let attrs = Attrs::new().quick("font-size", face.size.to_string()).quick("fill", face.color.as_str()).quick("text-anchor", "middle").quick("font-family", paint_family(&face.family));
            Node::text(Role::Label, (center, y), attrs, text)
        };
        nodes.push(text(&faces.empty, empty.headline_y, display_text::language::tr("chart.no_results", None, &[])?.as_str()));
        if let Some(filters) = &empty.filters {
            nodes.push(text(&faces.filters, empty.headline_y + EMPTY_LINE_GAP + faces.filters.size, filters));
        }
        return Ok(vec![Node::new(Role::Chrome, Attrs::new(), scene::Shape::Svg { extent: Extent { width: plan.width, height: plan.height }, children: nodes })]);
    }
    if let Some(fill) = style.header.background.as_ref().filter(|_| style.header.visible).map(|c| sanitize_color(c.as_str())).transpose()?.flatten().filter(|c| !c.eq_ignore_ascii_case("transparent")) {
        nodes.push(Node::rect(Role::Chrome, (0.0, plan.title_height, plan.content_width, plan.header_height), Attrs::new().quick("fill", fill)));
    }
    let header_rule = &style.header.divider.divider;
    if header_rule.visible && style.header.visible {
        nodes.push(divider(pad, plan.content_width - pad, plan.header_rule_y, header_rule.width, header_rule.color.as_str())?);
    }
    let (body_family, header_family) = (paint_family(&faces.body.family), paint_family(&faces.header.family));
    let (body_ink, header_ink) = (faces.body.color.as_str(), faces.header.color.as_str());
    let header_weight = faces.header.weight.attr();
    for (column, &(x, w)) in card.table.columns.iter().zip(&plan.columns).filter(|_| style.header.visible) {
        // layout.rs:`lay_out`'s header labels: a right-aligned text column's header stays at the start.
        // A number or date column and an `align: center` column center their header on the cell midpoint.
        let (tx, anchor) = if column.align == CellAlign::Center || column.has_lane() { (x + w / 2.0, "middle") } else { (x + pad, "start") };
        let attrs = Attrs::new()
            .quick("data-dct-role", "table-header")
            .quick("font-size", plan.header_size.to_string())
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
    let row_height = plan.row_height;
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
            if column.swatch {
                // layout.rs:`paint_data_rows`: the square sits 4px in from the column, centered in the row.
                let square = Node::new(Role::Mark { layer: 0, series: None, datum: Some(row) }, Attrs::new().quick("fill", column.cells[row].text()), scene::Shape::Rect { x: 0.0, y: 0.0, w: SWATCH_SIZE, h: SWATCH_SIZE, rx: 3.0 });
                cells.push(Node::group(Role::Chrome, Attrs::new(), vec![square]).translate((x + 4.0).round_ties_even(), row_y + (row_height - SWATCH_SIZE) / 2.0));
                continue;
            }
            // layout.rs:`paint_data_rows`: the fill rect is drawn before the text, over the row's stripe, and a swatch
            // column (above) never reaches it; the column's `font` color and weight replace the body's.
            if let Some(fill) = &column.background {
                cells.push(Node::rect(Role::Chrome, (x, row_y, w, row_height - rule_height), Attrs::new().quick("fill", fill.as_str())));
            }
            let (ink, weight) = (column.ink.as_deref().unwrap_or(body_ink), column.weight.as_deref());
            // A null in a date column has no lane: it paints in the body face at the column's midpoint.
            let lane = plan.lanes[col].as_ref().filter(|_| !(column.is_date_lane() && column.cells[row] == TableCell::Null));
            if let Some(lane) = lane {
                cells.push(lane_cell(&column.cells[row], lane, plan.pad_units.as_ref(), (text_y, col, plan.font_size), faces, (ink, weight))?);
                continue;
            }
            // layout.rs:`paint_data_rows`: a text cell has no lane, so `align` alone moves it.
            // A null paints its dash centered whatever the column's alignment (`NULL_DISPLAY`'s lane).
            let cell = &column.cells[row];
            let (tx, anchor) = match (cell, column.align) {
                (TableCell::Null, _) | (_, CellAlign::Center) => (x + w / 2.0, "middle"),
                (_, CellAlign::Right) => (x + w - pad, "end"),
                (_, CellAlign::Left) => (x + pad, "start"),
            };
            let attrs = Attrs::new()
                .quick("data-dct-role", "table-cell")
                .quick("font-size", plan.font_size.to_string())
                .quick("fill", ink)
                .quick("text-anchor", anchor)
                .quick("font-family", body_family.as_str())
                .quick_some("font-weight", weight)
                .quick("data-col", col.to_string());
            cells.push(Node::text(Role::Label, (tx, text_y), attrs, cell.text()));
        }
        if rule_height > 0.0 {
            cells.push(divider(pad, plan.content_width - pad, row_y + row_height - rule_height, rule_height, row_rule.color.as_str())?);
        }
        let attrs = Attrs::new().quick("class", "dbt-table-row").quick("data-dbt-row", row.to_string());
        nodes.push(Node::group(Role::Chrome, attrs, cells));
    }
    Ok(vec![Node::new(Role::Chrome, Attrs::new(), scene::Shape::Svg { extent: Extent { width: plan.width, height: plan.height }, children: nodes })])
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `has_room` compares what the pins leave with the natural widths. The width, one pin and a natural total one
    /// float step above what is left: summed, they land back on the width and would pass; compared, they do not fit.
    #[test]
    fn room_is_compared_as_production_compares_it() {
        let (width, pinned, natural) = (91.3618075613993_f64, 29.0_f64, 62.36180756139931_f64);
        assert!(width >= pinned + natural, "the summed form agrees with the width here");
        assert!(!has_room(width, pinned, natural));
        assert!(has_room(width, pinned, 62.3618075613993));
    }
}
