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
//! cells with the table's root `<svg>`. Under `column_layout.overflow: scroll` (the default) a table whose columns
//! pass its slot allocates them the width they need (`scroll_plan`) and `draw` places its header and rows in clipped
//! scroll panes under a scrollbar (`scroll_panes`), with the runtime `table_scroll.js` embedded; frozen columns stay
//! refused. Under `pagination.mode: scroll` (the default) a table with more rows than a page (`visible_rows`) draws
//! them all in a viewport of `page_rows` rows, or as many as its slot holds, with a y scrollbar and a row-range pager
//! (`pager`); a paged table that needs more than one page stays refused. [`TableFaces::of`] reads the body and
//! header faces off the chart's total `TableChartStyle`.

use bind::bound::{CellLink, NumberCell, Table, TableCell, TableColumn};
use board_model::style::style::Style;
use board_model::style::table::{CellAlign, TableMaxLines, ColumnOverflow, PaginationConfig, PaginationMode, TableChartStyle};
use d3rs::py_float::py_float_sum;
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use measure::SAMPLE_ROWS;
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run};

use super::failed::Face;
mod pager;
use crate::board::band::TitleShift;
use crate::board::height::TABLE_PAGINATION_CONTROL_HEIGHT;
use crate::board::prose::has_template_syntax;
use crate::contract;
use crate::knockout::safe_scope;
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
/// table.rs:`SCROLL_MAX_ROWS` (`ROW_POOL_CAP`): the most rows a vertically scrolling table draws; a longer one pages.
const SCROLL_MAX_ROWS: usize = 1000;
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
/// layout.rs:`SHORT_HEADER_CHARS`. A header of at most this many characters stays on one line: it drives the fit
/// cascade, and a scrolling table never wraps it.
const SHORT_HEADER_CHARS: usize = 12;

/// layout.rs:`HEADER_LINE_LIMIT`: header lines past which production warns, which is not modeled.
const HEADER_LINE_LIMIT: usize = 2;

/// layout.rs:`TALL_CELL_MIN_LINES`: fewest lines a wrapped cell takes before it can warn as a tower.
const TALL_CELL_MIN_LINES: usize = 4;
/// layout.rs:`TUKEY_FENCE`: how far past a column's interquartile range a cell may run before it is an outlier.
const TUKEY_FENCE: f64 = 1.5;

/// layout.rs:`nearest_rank`: the nearest-rank `percent`th percentile of `sorted`; an empty column is one line.
fn nearest_rank(sorted: &[usize], percent: f64) -> usize {
    let rank = (percent / 100.0 * sorted.len() as f64).ceil() as usize;
    rank.checked_sub(1).and_then(|at| sorted.get(at)).copied().unwrap_or(1)
}

/// layout.rs:`column_max_lines`: the lines a text column's cells may draw under `setting`, `None` for no limit.
/// `natural` is every body row's line count wrapped without a limit; the square cap is the bound
/// WARN-TABLE-CONTENT-TALLER-THAN-WIDE checks.
fn column_max_lines(setting: TableMaxLines, natural: &[usize], content: f64, line_height: f64) -> Option<usize> {
    let square = ((content / line_height).floor() as usize).max(1);
    let mut sorted = natural.to_vec();
    sorted.sort_unstable();
    match setting {
        TableMaxLines::Limit(limit) => limit.max_lines(),
        TableMaxLines::Square => Some(square),
        TableMaxLines::Percentile(percent) => Some(nearest_rank(&sorted, f64::from(percent)).min(square)),
        TableMaxLines::TrimOutliers => {
            let (q1, q3) = (nearest_rank(&sorted, 25.0), nearest_rank(&sorted, 75.0));
            let fence = (q3 as f64 + TUKEY_FENCE * (q3 - q1) as f64).floor() as usize;
            Some(fence.min(square))
        }
    }
}

/// The two text faces a table paints: its cells' and its header's.
#[derive(Debug, Clone, PartialEq)]
pub struct TableFaces {
    pub body: Face,
    pub header: Face,
    /// The numeral face (`style.fonts.numeral`) a number paints in, at the body's size, weight and ink.
    pub numeral: Face,
    /// The board's font color (`board_style.font_color`): a linked cell's text and a row band's tint.
    pub link_ink: String,
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
            None => Some(style.font.color.as_str()).filter(|ink| !ink.is_empty()).map(str::to_string).ok_or_else(|| Diagnostic::refused(refuse!("a table with an empty font color is not supported")))?,
        };
        let header = &style.header.font;
        let header_ink = sanitize_color(header.color.as_str())?.unwrap_or_else(|| body_ink.clone());
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
            link_ink: placed.font.color.as_str().to_string(),
            numeral: Face::of(numeral.as_str(), style.font.size, &style.font.weight, &body_ink, emoji),
            body: Face::of(style.font.family.as_str(), style.font.size, &style.font.weight, &body_ink, emoji),
            header: Face::of(header.family.as_str(), header.size, &header.weight, &header_ink, emoji),
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
    // `fit_lines` collapses every run of whitespace to one space before it measures and paints.
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
    /// The chart cell the table is drawn in; a table that is not one (a pie's attached table) cannot scroll.
    pub cell: Option<Cell<'a>>,
}

/// The chart cell a table scrolls in: its chart's id, which names the scroll panes and the clip, and the padding the
/// cell's card and the chart take (left, right, top, bottom), which the clip reaches back out over
/// (`svg_utils.card_box`).
#[derive(Debug, Clone, Copy)]
pub struct Cell<'a> {
    pub id: &'a str,
    pub padding: (f64, f64, f64, f64),
}

/// A table that scrolls: the cell it scrolls in, how far its columns pass the slot (`max_x`, 0 when they fit) and, when its
/// rows outnumber a page, their viewport.
#[derive(Debug, Clone, PartialEq)]
struct Scroll {
    max_x: f64,
    id: String,
    padding: (f64, f64, f64, f64),
    rows: Option<RowScroll>,
    /// The row-range pager under the viewport, measured at plan time; `Some` with `rows`.
    pager: Option<Node>,
}

/// The viewport of a body that scrolls its rows (layout.rs:`lay_out`'s `n_shown` and `scroll_half`): `shown` rows and half
/// of the next, which says there is more and lets the top of the viewport rest on a row at every offset.
#[derive(Debug, Clone, PartialEq)]
struct RowScroll {
    shown: usize,
    /// The rows' height the viewport shows, and how far the rows scroll past it.
    viewport: f64,
    max_y: f64,
    /// The rows `page_rows` asked for when the slot holds fewer, which production warns about.
    squeezed: Option<usize>,
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
    /// Each row's height: the (stepped) row height, grown to the tallest wrapped cell.
    row_heights: Vec<f64>,
    /// Each header's lines, empty with the header hidden.
    header_lines: Vec<Vec<String>>,
    /// The line a wrapped header advances by.
    header_line_height: f64,
    /// The line a wrapped cell advances by.
    line_height: f64,
    /// Each column's wrapped cells, one entry per row: `None` for a cell on one line.
    cell_lines: Vec<Vec<Option<Vec<String>>>>,
    content_width: f64,
    /// Set when the columns pass the slot (`column_layout.overflow: scroll`) or the rows outnumber a page: the table scrolls
    /// in its cell. `None` when both fit.
    scroll: Option<Scroll>,
    /// The width the columns share: the table's, less the band a body that scrolls its rows reserves for its scrollbar.
    view_width: f64,
    /// The band under the rows the x scrollbar takes, so the bar never covers a row; 0 when the columns fit.
    bar_band: f64,
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

    /// Whether the table draws in scroll panes: its columns pass the slot or its rows outnumber a page.
    pub fn scrolls(&self) -> bool {
        self.scroll.is_some()
    }

    /// Whether a table over no rows is taller than the slot it is placed in, which production refuses (layout.rs:`lay_out_empty`).
    pub fn empty_overflows(&self, slot: f64) -> bool {
        self.empty.is_some() && slot < self.height
    }

    /// layout.rs:`lay_out`'s WARN-TABLE-PAGE-SQUEEZED: the slot holds fewer rows than `page_rows` asked a viewport to show.
    pub fn warnings(&self, card: &TableCard) -> Vec<Diagnostic> {
        let Some(Scroll { id, rows: Some(RowScroll { shown, squeezed: Some(requested), .. }), .. }) = &self.scroll else { return Vec::new() };
        vec![diag::warn_table_page_squeezed(id.as_str(), *shown, *requested, card.table.rows())]
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
fn sanitize_color(color: &str) -> Result<Option<String>, Diagnostic> {
    if color.is_empty() {
        return Ok(None);
    }
    bind::table::sanitized_color(color).map(Some).ok_or_else(|| Diagnostic::refused(refuse!("a table color that is not a hex color, a CSS color name, transparent or none is not supported")))
}

/// Every leaf of the table's style that this slice draws another way than production does.
fn refuse_unmodeled(style: &TableChartStyle) -> Result<(), Diagnostic> {
    let unmodeled = [
        (style.row_numbers.visible, "row numbers"),
        (!style.max_lines.wraps(), "`style.max_lines: 1`"),
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

/// One auto column's widths: its demand raised to its word floor, the least width that shows it in full, and
/// whether it is a text column (past the compact ceiling).
struct Natural {
    base: f64,
    width: f64,
    text: bool,
}

/// layout.rs:`auto_column_demand` and `natural_auto_width`. The least width that shows an auto column in full: its
/// demand raised to the word floor (a floor never passes a declared `max_width`), a compact column inflated by
/// `content_headroom` when text columns exist, at least the one-line header plus both paddings, and a text column
/// no wider than its `max_width`.
fn natural_widths(columns: &[Column], headroom: f64, pad: f64) -> Vec<Natural> {
    let base: Vec<f64> = columns.iter().map(|c| c.demand.max(c.max_width.map_or(c.word_floor, |cap| c.word_floor.min(cap))).max(1.0)).collect();
    let text: Vec<bool> = base.iter().map(|&d| d > COMPACT_DEMAND_CEILING).collect();
    let factor = if text.iter().any(|&t| t) { 1.0 + headroom } else { 1.0 };
    columns
        .iter()
        .zip(base.iter().zip(&text))
        .map(|(c, (&d, &is_text))| {
            let width = (if is_text { d } else { d * factor }).max(c.header_demand + 2.0 * pad);
            Natural { base: d, width: if is_text { c.max_width.map_or(width, |cap| width.min(cap)) } else { width }, text: is_text }
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

/// The theme's column laws a width is allocated under: `content_headroom`, the cell padding and
/// `width_similarity_threshold`.
#[derive(Clone, Copy)]
struct Widths {
    headroom: f64,
    pad: f64,
    threshold: f64,
}

/// layout.rs:`calculate_column_layout_port` for the auto columns (the pins are out) in what `table_width` leaves
/// after `pinned`: with room for every column's natural width each compact column takes it and the text columns
/// share the rest; without, the demands stand, the compact columns take a headroom the budget allows and the
/// text columns share what is left.
fn allocate(columns: &[Column], law: Widths, (table_width, pinned): (f64, f64)) -> Vec<f64> {
    let budget = (table_width - pinned).max(0.0);
    if columns.len() == 1 {
        return vec![budget];
    }
    let natural = natural_widths(columns, law.headroom, law.pad);
    let room = has_room(table_width, pinned, py_float_sum(&natural.iter().map(|n| n.width).collect::<Vec<_>>()));
    let demand = |i: usize| if room { natural[i].width } else { natural[i].base };
    let compact: Vec<usize> = (0..columns.len()).filter(|&i| !natural[i].text).collect();
    let text: Vec<usize> = (0..columns.len()).filter(|&i| natural[i].text).collect();
    let raw_compact_total = py_float_sum(&compact.iter().map(|&i| demand(i)).collect::<Vec<_>>());
    let factor = if room || text.is_empty() || raw_compact_total <= 0.0 { 1.0 } else { (1.0 + law.headroom).min((budget / raw_compact_total).max(1.0)) };
    let mut compact_widths: Vec<f64> = compact.iter().map(|&i| demand(i) * factor).collect();
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
        let demands: Vec<f64> = text.iter().map(|&i| demand(i)).collect();
        let floors: Vec<f64> = text.iter().map(|&i| columns[i].word_floor).collect();
        let caps: Vec<Option<f64>> = text.iter().map(|&i| columns[i].max_width).collect();
        let allocated = allocate_text(&demands, &floors, &caps, text_budget, law.threshold);
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

/// Every column's width in `table_width`: a pinned column as pinned, the rest allocated what the pins leave (a
/// single one takes it all).
fn column_widths(pins: &[Option<f64>], inputs: &[Column], law: Widths, table_width: f64) -> Vec<f64> {
    let auto_inputs: Vec<Column> = inputs.iter().zip(pins).filter(|(_, pin)| pin.is_none()).map(|(column, _)| column.clone()).collect();
    let pinned = py_float_sum(&pins.iter().flatten().copied().collect::<Vec<_>>());
    let mut auto_widths = if auto_inputs.is_empty() { Vec::new() } else { allocate(&auto_inputs, law, (table_width, pinned)) }.into_iter();
    pins.iter().map(|pin| pin.unwrap_or_else(|| auto_widths.next().expect("an allocated width per auto column"))).collect()
}

/// layout.rs:`two_line_width`. The narrowest content width that holds `header` on two lines, as `fit_lines` sums
/// a line: its words' widths plus a space between each. Infinite for a one-word header, which has no second line.
/// `wrapped_headers` truncates the content area, so a fraction would wrap the header onto a third line.
fn two_line_width(header: &str, measure: &Measurer, size: f64) -> Result<f64, Diagnostic> {
    let space = measure.width(" ", true, size)?;
    let words: Vec<f64> = header.split_whitespace().map(|word| measure.width(word, true, size)).collect::<Result<_, _>>()?;
    let line = |words: &[f64]| words.iter().sum::<f64>() + space * (words.len() - 1) as f64;
    Ok((1..words.len()).map(|split| line(&words[..split]).max(line(&words[split..]))).fold(f64::INFINITY, f64::min).ceil())
}

/// What `scroll_plan` decides: the budget the columns are allocated in and each column's pin, the compact ones at
/// their natural width.
struct ScrollWidths {
    budget: f64,
    pins: Vec<Option<f64>>,
}

/// layout.rs:`scroll_plan`. Under `column_layout.overflow: scroll` a slot narrower than the columns allocates them
/// the width they need instead, and the table scrolls inside its slot rather than squeezing. A table that fits
/// keeps the usual allocation. One that does not pins its compact columns at their natural width, a long header
/// on two lines; a wrapping text column stays elastic down to the compact ceiling (or its longest word), so the
/// budget grows past the slot only once the headers have wrapped and the text columns are down to that floor.
/// `pins` are the authored `width:`s; the result adds each compact column's natural width to them, `None` when the
/// table fits or pins no compact column.
fn scroll_plan(card: &TableCard, measure: &Measurer, inputs: &[Column], pins: &[Option<f64>], law: Widths, (slot, header_size): (f64, f64)) -> Result<Option<ScrollWidths>, Diagnostic> {
    let style = card.style;
    let pinned = py_float_sum(&pins.iter().flatten().copied().collect::<Vec<_>>());
    let auto: Vec<usize> = (0..pins.len()).filter(|&i| pins[i].is_none()).collect();
    let mut columns: Vec<Column> = auto.iter().map(|&i| inputs[i].clone()).collect();
    let needed = |columns: &[Column]| pinned + py_float_sum(&natural_widths(columns, law.headroom, law.pad).iter().map(|n| n.width).collect::<Vec<_>>());
    let rest = needed(&columns);
    if rest <= 0.0 || rest <= slot {
        return Ok(None);
    }
    let one_line: Vec<f64> = columns.iter().map(|c| c.header_demand + 2.0 * law.pad).collect();
    let header_limit = style.header.max_lines;
    if style.header.visible && !header_limit.is_one_line() {
        for (column, &i) in columns.iter_mut().zip(&auto) {
            let header = &card.table.columns[i].header;
            if header.chars().count() > SHORT_HEADER_CHARS {
                column.header_demand = column.header_demand.min(two_line_width(header, measure, header_size.trunc())?);
            }
        }
    }
    let natural = natural_widths(&columns, law.headroom, law.pad);
    let compact: Vec<usize> = (0..auto.len()).filter(|&a| !natural[a].text).collect();
    let elastic = compact.len() < auto.len();
    // `wrap` is the only mode this slice draws (`refuse_unmodeled`): a text column shrinks no further than the
    // compact ceiling or its longest word.
    for (column, n) in columns.iter_mut().zip(&natural) {
        if n.text {
            let ceiling = COMPACT_DEMAND_CEILING.max(column.word_floor);
            column.max_width = Some(column.max_width.map_or(ceiling, |cap| cap.min(ceiling)));
        }
    }
    let needed = needed(&columns);
    let mut widths: Vec<f64> = compact.iter().map(|&a| natural[a].width).collect();
    // Wrapped headers that leave the slot room to spare get it back, unless a text column is there to take it.
    let spare = slot - needed;
    if spare > 0.0 && !elastic {
        grow_toward_header(&mut widths, &compact.iter().map(|&a| one_line[a]).collect::<Vec<_>>(), spare);
    }
    if compact.is_empty() {
        return Ok(None);
    }
    let mut scroll_pins = pins.to_vec();
    for (&a, width) in compact.iter().zip(widths) {
        scroll_pins[auto[a]] = Some(width);
    }
    Ok(Some(ScrollWidths { budget: needed.max(slot), pins: scroll_pins }))
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
            measure::fit::truncate_label_by(line, width, |text| book.raw_advance(text, &measured).map_err(diag::err_internal))
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
        row_heights: Vec::new(),
        header_lines: Vec::new(),
        header_line_height: 0.0,
        line_height: 0.0,
        cell_lines: Vec::new(),
        content_width: width,
        scroll: None,
        view_width: width,
        bar_band: 0.0,
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

/// table.rs:`PaginationCfg::scrolls_rows`: whether rows past a page scroll instead of paging. A table longer than
/// `SCROLL_MAX_ROWS` pages, so every row stays reachable.
fn scrolls_rows(pagination: &PaginationConfig, n_rows: usize) -> bool {
    pagination.enabled && pagination.mode == PaginationMode::Scroll && n_rows <= SCROLL_MAX_ROWS
}

/// table.rs:`row_scrollbar_band`: the band a table that scrolls its rows (more than `page_rows`) reserves at its right
/// edge for the scrollbar, so the bar never sits against the last column's text. It is reserved from `page_rows + 1`
/// rows, though the grow-by-2 short-circuit draws up to `page_rows + 2` unscrolled.
fn row_scrollbar_band(style: &TableChartStyle, n_rows: usize) -> f64 {
    let pagination = &style.pagination;
    if scrolls_rows(pagination, n_rows) && pagination.page_rows.is_none_or(|page| n_rows > page as usize) {
        style.scrollbar.size + 2.0 * style.scrollbar.inset
    } else {
        0.0
    }
}

/// layout.rs:`largest_safe_page_rows`: the largest page size whose every contiguous window of `row_heights` fits
/// `available`. Checking only the leading rows would let a short first page pick a size that overflows a later, taller one.
fn largest_safe_page_rows(row_heights: &[f64], available: f64, max_page_rows: usize) -> usize {
    let n = row_heights.len();
    (1..=max_page_rows.min(n)).rev().find(|&size| (0..n).step_by(size).all(|start| row_heights[start..n.min(start + size)].iter().sum::<f64>() <= available)).unwrap_or(1)
}

/// What the visible-rows resolution decides for a table whose rows may outnumber a page.
struct Visible {
    /// The slot sizes the table: its height is the slot's, whatever its rows add up to.
    pinned: bool,
    /// The rows a scrolling viewport shows and, when the slot holds fewer than `page_rows` asked, the rows asked for.
    scroll: Option<(usize, Option<usize>)>,
}

/// layout.rs:`lay_out`'s visible-rows resolution for a static export (no page runtime, so no row pool). With a slot
/// (`available` is what it leaves the rows) and pagination on, the grow-by-2 short-circuit draws every row at its height
/// when they all fit within `page_rows + PAGINATION_GROW_CAP`; otherwise a scrolling body's viewport is the rows that fit
/// the slot under the pager's band, the half of the next included, and the slot sizes the table. With no slot the
/// viewport is `page_rows` rows. A paged table that needs more than one page, or whose slot cannot hold its rows,
/// stays refused.
fn visible_rows(pagination: &PaginationConfig, row_heights: &[f64], available: Option<f64>) -> Result<Visible, Diagnostic> {
    let n_rows = row_heights.len();
    let page_rows = pagination.page_rows.map_or(n_rows.max(1), |rows| rows as usize);
    let grows = n_rows <= page_rows + PAGINATION_GROW_CAP;
    let scrolls = scrolls_rows(pagination, n_rows);
    let one_page = Visible { pinned: false, scroll: None };
    if !pagination.enabled {
        return Ok(one_page);
    }
    let fits = grows && available.is_none_or(|available| row_heights.iter().sum::<f64>() <= available);
    if fits {
        return Ok(one_page);
    }
    if !scrolls {
        return Err(Diagnostic::refused(refuse!("a table that needs more than one page is not supported")));
    }
    let Some(available) = available else {
        return Ok(Visible { pinned: false, scroll: Some((page_rows, None)) });
    };
    if n_rows <= largest_safe_page_rows(row_heights, available, page_rows) {
        return Ok(Visible { pinned: true, scroll: None });
    }
    // The viewport is `shown` rows and half the next, under the pager's band.
    let rows_available = available - TABLE_PAGINATION_CONTROL_HEIGHT;
    let half_next = |shown: usize| if shown < n_rows { row_heights[shown] / 2.0 } else { 0.0 };
    let requested = page_rows.min(n_rows);
    let mut shown = requested;
    while shown > 1 && row_heights[..shown].iter().sum::<f64>() + half_next(shown) > rows_available {
        shown -= 1;
    }
    Ok(Visible { pinned: true, scroll: Some((shown, (pagination.page_rows.is_some() && shown < requested).then_some(requested))) })
}

/// The table at `width`: its columns sized, its rows and header placed, and every leaf this slice
/// does not draw refused. `book` measures as production does (`measure::stand_in_family`, raw
/// advances). `slot` is the height the table's cell leaves it, which sizes a table whose rows outnumber a page.
pub fn plan(card: &TableCard, book: &FontBook, width: f64, slot: Option<f64>, shift: TitleShift) -> Result<Plan, Diagnostic> {
    let style = card.style;
    refuse_unmodeled(style)?;
    let n_rows = card.table.rows();
    let pad = style.column_layout.cell_padding.trunc();
    // `outer_padding` is refused above, so the table spans its whole width less the scrollbar's band.
    let table_width = width - row_scrollbar_band(style, n_rows);
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
    let law = Widths { headroom: style.column_layout.content_headroom.get(), pad, threshold: style.column_layout.width_similarity_threshold.get() };
    // layout.rs:`calculate_column_layout_port`: a `width:` pins its column, and the rest share what the
    // pins leave (a single one takes it all).
    let pins: Vec<Option<f64>> = card.table.columns.iter().map(|column| column.width).collect();
    let scrolls_sideways = style.column_layout.overflow == ColumnOverflow::Scroll;
    // `overflow: fit` squeezes a table that needs more than its width, which is not modeled.
    if !scrolls_sideways {
        let auto_inputs: Vec<Column> = inputs.iter().zip(&pins).filter(|(_, pin)| pin.is_none()).map(|(column, _)| column.clone()).collect();
        let pinned = py_float_sum(&pins.iter().flatten().copied().collect::<Vec<_>>());
        let natural = natural_widths(&auto_inputs, law.headroom, pad);
        if !has_room(table_width, pinned, py_float_sum(&natural.iter().map(|n| n.width).collect::<Vec<_>>())) || table_width < pinned {
            return Err(Diagnostic::refused(refuse!("a table whose columns need more than its width is not supported")));
        }
    }
    let mut widths = column_widths(&pins, &inputs, law, table_width);
    if scrolls_sideways {
        if let Some(scrolled) = scroll_plan(card, &measure, &inputs, &pins, law, (table_width, faces.header.size))? {
            widths = column_widths(&scrolled.pins, &inputs, law, scrolled.budget);
        }
    }
    let content_width = py_float_sum(&widths);
    let overflows_width = content_width > table_width + OVERFLOW_EPSILON;
    if overflows_width && !scrolls_sideways {
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
            if style.header.visible && card.table.columns.len() > 1 && column.header.chars().count() <= SHORT_HEADER_CHARS && measure.width(&column.header, false, header_size)? > content {
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
    let header_measurer = book.production_measurer(&measure.header);
    let body_measurer = book.production_measurer(&measure.body);
    let wrap = |measurer: &measure::fonts::FontMeasurer, text: &str, width: f64, size: f64| measure::fit::fit_with(text, width, size, measurer, None).lines;
    let header_limit = style.header.max_lines;
    let mut header_lines: Vec<Vec<String>> = Vec::new();
    if style.header.visible {
        for (column, &w) in card.table.columns.iter().zip(&widths) {
            let content = (w - 2.0 * pad).trunc().max(1.0);
            if measure.width(&column.header, true, header_size.trunc())? <= content {
                header_lines.push(vec![column.header.clone()]);
                continue;
            }
            // layout.rs:`wrapped_headers` caps a header by its line limit (`fit_lines`): a
            // one-line limit cuts it with a tooltip, and a header past two lines raises
            // WARN-TABLE-HEADER-EXTRA-WRAP, neither of which is modeled.
            if header_limit.is_one_line() {
                return Err(Diagnostic::refused(refuse!("a table header cut to one line is not supported")));
            }
            let lines = wrap(&header_measurer, &column.header, content, header_size.trunc());
            if lines.len() > HEADER_LINE_LIMIT {
                return Err(Diagnostic::refused(refuse!("a table header that needs more than two lines is not supported")));
            }
            header_lines.push(if lines.is_empty() { vec![column.header.clone()] } else { lines });
        }
    }
    // layout.rs:`lay_out`'s text wrapping: a text cell wider than the column's content area word-wraps
    // (`fit_lines`) and its row grows to the tallest wrapped cell. A number, a date or a swatch never wraps.
    let line_height = font_size + style.text_baseline_offset;
    let mut row_heights = vec![row_height; n_rows];
    let mut cell_lines: Vec<Vec<Option<Vec<String>>>> = Vec::new();
    for (column, &w) in card.table.columns.iter().zip(&widths) {
        let content = w - 2.0 * pad;
        let mut lines_of = vec![None; n_rows];
        if !column.swatch && !column.has_lane() && content > 0.0 {
            for (row, cell) in column.cells.iter().enumerate() {
                let text = cell.text();
                if text.is_empty() || measure.cell_advance(column, cell, &text, font_size).map_err(diag::err_internal)? <= content {
                    continue;
                }
                let lines = wrap(&body_measurer, &text, content, font_size);
                if lines.len() > 1 {
                    lines_of[row] = Some(lines);
                }
            }
            // layout.rs caps a cell by `style.max_lines`: a cell past the limit is cut with a tooltip
            // (and counted into WARN-TABLE-TEXT-TRUNCATED when the limit is authored), none of which is modeled.
            let natural: Vec<usize> = lines_of.iter().map(|lines| lines.as_ref().map_or(1, Vec::len)).collect();
            let limit = column_max_lines(style.max_lines, &natural, content, line_height);
            for (row, lines) in lines_of.iter().enumerate() {
                let Some(lines) = lines else { continue };
                if limit.is_some_and(|limit| lines.len() > limit) {
                    return Err(Diagnostic::refused(refuse!("a table cell cut to a line limit is not supported")));
                }
                // WARN-TABLE-CONTENT-TALLER-THAN-WIDE is not modeled.
                if lines.len() >= TALL_CELL_MIN_LINES && lines.len() as f64 * line_height > content {
                    return Err(Diagnostic::refused(refuse!("a table text block taller than its column is wide is not supported")));
                }
                row_heights[row] = row_heights[row].max((lines.len() as f64 * line_height - style.text_baseline_offset + 2.0 * pad).round_ties_even());
            }
        }
        cell_lines.push(lines_of);
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
    let header_line_height = header_size.trunc() + style.text_baseline_offset;
    let header_floor = header_lines.iter().map(Vec::len).max().unwrap_or(1) as f64 * header_line_height;
    let header_height = if style.header.visible { style.header.height.trunc().max(header_floor).trunc() } else { 0.0 };
    let header_gap = if style.header.visible { (row_height * 0.25).floor() } else { 0.0 };
    let rows_top = title_height + header_height + header_gap;
    let columns = plan_columns(&widths);
    // layout.rs:`lay_out`'s `hbar_band`: a table that scrolls sideways reserves a band under its rows for the scrollbar.
    let bar_band = if overflows_width { style.scrollbar.size + 2.0 * style.scrollbar.inset } else { 0.0 };
    let available = slot.map(|slot| slot - title_height - header_height - header_gap - style.bottom_padding - bar_band);
    let visible = visible_rows(&style.pagination, &row_heights, available)?;
    let rows_total = row_heights.iter().sum::<f64>();
    let rows_scroll = visible.scroll.map(|(shown, squeezed)| {
        let half = if shown < n_rows { row_heights[shown] / 2.0 } else { 0.0 };
        let viewport = row_heights[..shown].iter().sum::<f64>() + half;
        RowScroll { shown, viewport, max_y: (rows_total + half - viewport).max(0.0), squeezed }
    });
    // layout.rs:`lay_out`'s canvas: a pinned table is its slot, any other its content, the pager's band added when it scrolls its rows.
    let height = match slot {
        Some(slot) if visible.pinned => slot,
        _ => rows_top + rows_scroll.as_ref().map_or(rows_total, |rows| rows.viewport) + style.bottom_padding + bar_band + if rows_scroll.is_some() { TABLE_PAGINATION_CONTROL_HEIGHT } else { 0.0 },
    };
    let scroll = match (overflows_width, rows_scroll, card.cell) {
        (false, None, _) => None,
        (true, _, None) => return Err(Diagnostic::refused(refuse!("a table outside a chart cell that needs more than its width is not supported"))),
        // A pie's attached table is the one drawn outside a cell, and the pie turns its pagination off.
        (false, Some(_), None) => return Err(diag::err_internal("rows scrolled outside a chart cell")),
        (_, rows, Some(cell)) => {
            let y = rows_top + rows.as_ref().map_or(rows_total, |rows| rows.viewport) + style.bottom_padding + bar_band;
            let pager = rows.as_ref().map(|rows| scroll_pager(card, book, cell.id, rows, &row_heights, (width, y))).transpose()?;
            Some(Scroll { max_x: if overflows_width { content_width - table_width } else { 0.0 }, id: cell.id.to_string(), padding: cell.padding, rows, pager })
        }
    };
    Ok(Plan {
        width,
        columns,
        pad,
        font_size,
        header_size,
        height,
        row_heights,
        header_lines,
        header_line_height,
        line_height,
        cell_lines,
        content_width,
        scroll,
        view_width: table_width,
        bar_band,
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
    })
}

/// layout.rs:`emit_divider_segment`. A solid line is a `<rect>` of whole-pixel coverage.
fn divider(x1: f64, x2: f64, y: f64, thickness: f64, color: &str, class: Option<&str>) -> Result<Node, Diagnostic> {
    // Production paints a divider's color as written; only the ones it would reject are refused.
    sanitize_color(color)?;
    let attrs = Attrs::new().quick("fill", color).quick_some("class", class).quick("shape-rendering", "crispEdges");
    Ok(Node::rect(Role::Grid, (x1, y, x2 - x1, thickness), attrs))
}

/// layout.rs:`paint_data_rows`' lane branch: one `<text>` in the numeral face whose prefix, digits and
/// suffix are tspans each anchored at its lane; a null is the dash centered on the digits' lane, and a date
/// one run end-anchored at the lane's number edge.
fn lane_cell(cell: &TableCell, lane: &Lane, pad_units: Option<&(String, String)>, (y, col, font_size): (f64, usize, f64), faces: &TableFaces, (ink, weight, class): (&str, Option<&str>, Option<&str>)) -> Result<Node, Diagnostic> {
    let tspan = |x: f64, anchor: &str, text: &str| Run::Tspan { x: Some(x), y: None, dx: None, dy: None, attrs: Attrs::new().quick("text-anchor", anchor), runs: vec![Run::Chars(Chars::new(text, Escape::Quick))] };
    let attrs = Attrs::new()
        .quick("data-dct-role", "table-cell")
        .quick_some("class", class)
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
                .quick_some("class", class)
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

/// The board's font color as a linked cell and a row band paint it: production requires one and refuses a color that
/// is no hex.
fn link_ink(color: &str) -> Result<String, Diagnostic> {
    sanitize_color(color)?.ok_or_else(|| diag::err_internal("the board's root font color is empty after the theme cascade"))
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
    let mut header = Vec::new();
    if let Some(fill) = style.header.background.as_ref().filter(|_| style.header.visible).map(|c| sanitize_color(c.as_str())).transpose()?.flatten().filter(|c| !c.eq_ignore_ascii_case("transparent")) {
        header.push(Node::rect(Role::Chrome, (0.0, plan.title_height, plan.content_width, plan.header_height), Attrs::new().quick("fill", fill)));
    }
    let header_rule = &style.header.divider.divider;
    if header_rule.visible && style.header.visible {
        header.push(divider(pad, plan.content_width - pad, plan.header_rule_y, header_rule.width, header_rule.color.as_str(), None)?);
    }
    let (body_family, header_family) = (paint_family(&faces.body.family), paint_family(&faces.header.family));
    let (body_ink, header_ink) = (faces.body.color.as_str(), faces.header.color.as_str());
    let header_weight = faces.header.weight.attr();
    for ((column, &(x, w)), lines) in card.table.columns.iter().zip(&plan.columns).zip(&plan.header_lines) {
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
        // layout.rs:`emit_header_labels`: a wrapped header's last line sits on the baseline.
        let [line] = lines.as_slice() else {
            let start = plan.header_baseline - (lines.len() - 1) as f64 * plan.header_line_height;
            header.push(stacked_lines(attrs, (tx, start), plan.header_line_height, lines));
            continue;
        };
        header.push(Node::text(Role::Label, (tx, plan.header_baseline), attrs, line.as_str()));
    }
    let stripe = style.row.stripe.as_ref().and_then(|s| s.color.as_ref()).map(|c| c.as_str());
    // Production validates the stripe's color and paints it as written (bind.rs:`bind_drawn`).
    stripe.map(sanitize_color).transpose()?;
    let row_rule = &style.row.divider.divider;
    let n_rows = card.table.rows();
    let links = card.table.links.as_ref();
    // A linked table's dividers must not catch the pointer a row band is waiting for.
    let rule_class = links.is_some_and(|links| links.rows.is_some()).then_some("dbt-pointer-inert");
    let mut rows = Vec::new();
    let mut row_y = plan.rows_top;
    for row in 0..n_rows {
        let row_height = plan.row_heights[row];
        row_y += if row == 0 { 0.0 } else { plan.row_heights[row - 1] };
        // layout.rs:`paint_data_rows`: the rule's height comes out of the row's own fill, and the
        // last row has none.
        let rule_height = if row_rule.visible && row + 1 < n_rows { row_rule.width.round_ties_even().max(1.0) } else { 0.0 };
        let mut cells = Vec::new();
        if let (Some(fill), true) = (stripe, row % 2 == 1) {
            cells.push(Node::rect(Role::Chrome, (0.0, row_y, plan.content_width, row_height - rule_height), Attrs::new().quick("fill", fill)));
        }
        // layout.rs:`paint_data_rows`: the row's link is one transparent band over the whole row, drawn before its
        // cells, which then stay out of its pointer's way.
        let band = links.and_then(|links| links.rows.as_ref()).and_then(|rows| rows[row].as_ref());
        if let Some(band) = band {
            let ink = link_ink(&faces.link_ink)?;
            let rect = Node::rect(Role::Chrome, (0.0, row_y, plan.content_width, row_height), Attrs::new().quick("class", "dbt-table-row-link").quick("fill", "transparent").quick("style", format!("--dbt-link: {ink}")));
            let attrs = Attrs::new().quick("href", band.link.href.as_str()).quick("aria-label", band.label.as_str());
            cells.push(contract::link(attrs, band.link.open, vec![rect]));
        }
        let inert = band.map(|_| "dbt-pointer-inert");
        let text_y = row_y + row_height / 2.0 + style.text_baseline_offset;
        for (col, (column, &(x, w))) in card.table.columns.iter().zip(&plan.columns).enumerate() {
            if column.swatch {
                // layout.rs:`paint_data_rows`: the square sits 4px in from the column, centered in the row.
                let square = Node::new(Role::Mark { layer: 0, series: None, datum: Some(row) }, Attrs::new().quick("fill", column.cells[row].text()), scene::Shape::Rect { x: 0.0, y: 0.0, w: SWATCH_SIZE, h: SWATCH_SIZE, rx: 3.0 });
                cells.push(Node::group(Role::Chrome, Attrs::new().quick_some("class", inert), vec![square]).translate((x + 4.0).round_ties_even(), row_y + (row_height - SWATCH_SIZE) / 2.0));
                continue;
            }
            // layout.rs:`paint_data_rows`: the fill rect is drawn before the text, over the row's stripe, and a swatch
            // column (above) never reaches it; the column's `font` color and weight replace the body's.
            if let Some(fill) = &column.background {
                cells.push(Node::rect(Role::Chrome, (x, row_y, w, row_height - rule_height), Attrs::new().quick("fill", fill.as_str()).quick_some("class", inert)));
            }
            // layout.rs:`paint_data_rows`: a cell's own link wraps its text in an anchor and a `color` group; the link
            // color replaces the body's unless the column authors an ink, and a linked cell weighs 500 unless the column does.
            let cell_link: Option<&CellLink> = links.and_then(|links| links.cells[col][row].as_ref());
            let linked;
            let ink = match (&column.ink, cell_link) {
                (Some(ink), _) => ink.as_str(),
                (None, Some(_)) => {
                    linked = link_ink(&faces.link_ink)?;
                    linked.as_str()
                }
                (None, None) => body_ink,
            };
            let weight = column.weight.as_deref().or(cell_link.map(|_| "500"));
            let class = if cell_link.is_some() { Some("dbt-table-link-text") } else { band.map(|_| "dbt-table-cell-inert") };
            let wrap = |text: Node| match cell_link {
                None => text,
                Some(link) => {
                    let group = Node::group(Role::Chrome, Attrs::new().quick("class", "dbt-table-link").quick("style", format!("color: {ink}")), vec![text]);
                    contract::link(Attrs::new().quick("href", link.href.as_str()), link.open, vec![group])
                }
            };
            // A null in a date column has no lane: it paints in the body face at the column's midpoint.
            let lane = plan.lanes[col].as_ref().filter(|_| !(column.is_date_lane() && column.cells[row] == TableCell::Null));
            if let Some(lane) = lane {
                cells.push(wrap(lane_cell(&column.cells[row], lane, plan.pad_units.as_ref(), (text_y, col, plan.font_size), faces, (ink, weight, class))?));
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
                .quick_some("class", class)
                .quick("font-size", plan.font_size.to_string())
                .quick("fill", ink)
                .quick("text-anchor", anchor)
                .quick("font-family", body_family.as_str())
                .quick_some("font-weight", weight)
                .quick("data-col", col.to_string());
            // layout.rs:`paint_data_rows`: a wrapped block is centered in its grown row, its first baseline
            // a font size under the block's top.
            let Some(lines) = &plan.cell_lines[col][row] else {
                cells.push(wrap(Node::text(Role::Label, (tx, text_y), attrs, cell.text())));
                continue;
            };
            let first_y = row_y + (row_height - lines.len() as f64 * plan.line_height) / 2.0 + plan.font_size;
            cells.push(wrap(stacked_lines(attrs, (tx, first_y), plan.line_height, lines)));
        }
        if rule_height > 0.0 {
            cells.push(divider(pad, plan.content_width - pad, row_y + row_height - rule_height, rule_height, row_rule.color.as_str(), rule_class)?);
        }
        let attrs = Attrs::new().quick("class", "dbt-table-row").quick("data-dbt-row", row.to_string());
        rows.push(Node::group(Role::Chrome, attrs, cells));
    }
    let children = match &plan.scroll {
        Some(scroll) => {
            let content = nodes.into_iter().chain(scroll_panes(card, plan, scroll, header, rows)).chain(scroll.pager.clone()).chain([scroll_script()]).collect();
            // layout.rs:`lay_out`'s clip: only columns past the slot paint outside it.
            if scroll.max_x > 0.0 {
                clipped(plan, scroll, content)
            } else {
                content
            }
        }
        None => nodes.into_iter().chain(header).chain(rows).collect(),
    };
    Ok(vec![Node::new(Role::Chrome, Attrs::new(), scene::Shape::Svg { extent: Extent { width: plan.width, height: plan.height }, children })])
}

/// layout.rs:`lay_out`'s overflow clip. Hosts inline a table into the board without its root `<svg>`'s viewport, so
/// a table whose columns pass its slot clips itself to its card box: unclipped, its columns paint over whatever sits
/// beside it in a `cols` row. The box reaches back out over the cell's padding (`svg_utils.card_box`).
fn clipped(plan: &Plan, scroll: &Scroll, content: Vec<Node>) -> Vec<Node> {
    let id = format!("table-clip-{}", safe_scope(&scroll.id));
    let (left, right, top, bottom) = scroll.padding;
    let rect = Node::rect(Role::Chrome, (-left, -top, plan.width + left + right, plan.height + top + bottom), Attrs::new());
    let clip = Node::new(Role::Chrome, Attrs::new().quick("id", id.as_str()), scene::Shape::ClipPath(vec![rect]));
    vec![Node::new(Role::Chrome, Attrs::new(), scene::Shape::Defs(vec![clip])), Node::group(Role::Chrome, Attrs::new().quick("clip-path", format!("url(#{id})")), content)]
}

/// table.rs:`SCROLL_MIN_THUMB`: the shortest a scrollbar thumb is drawn, so a long table's thumb stays grabbable.
const SCROLL_MIN_THUMB: f64 = 24.0;
/// table.rs:`SCROLL_SHADOW`: the shadow under the header once rows scroll beneath it.
const SCROLL_SHADOW: f64 = 6.0;

/// layout.rs:`emit_scroll_panes` (the frozen columns out): each pane clips one region and moves on the axes it scrolls.
/// The header pane moves on x, the rows pane on x and y as the table needs, and `table_scroll.js` translates the
/// `data-dbt-scroll` groups. A transparent rect over the viewport takes the wheel and the drag. The scrollbars are drawn
/// in place, so a static export shows how much is out of view: the x bar in the band `Plan::bar_band` reserves under
/// the rows, the y bar in the band the rows' width gave up (`row_scrollbar_band`), and a shadow under the header once rows
/// scroll beneath it.
fn scroll_panes(card: &TableCard, plan: &Plan, scroll: &Scroll, header: Vec<Node>, rows: Vec<Node>) -> Vec<Node> {
    let (id, max_x) = (scroll.id.as_str(), scroll.max_x);
    let max_y = scroll.rows.as_ref().map_or(0.0, |rows| rows.max_y);
    let (scroll_x, scroll_y) = (max_x > 0.0, max_y > 0.0);
    let bar = &card.style.scrollbar;
    let (header_top, body_top, rows_top) = (plan.title_height, plan.title_height + plan.header_height, plan.rows_top);
    let body_bottom = rows_top + scroll.rows.as_ref().map_or_else(|| plan.row_heights.iter().sum::<f64>(), |rows| rows.viewport);
    let view_right = plan.view_width;
    let geometry = format!("{}-{}-{}-{}", num_full(0.0), num_full(header_top), num_full(view_right), num_full(body_bottom));
    let pane_id = |pane: &str| format!("table-scroll-{id}-{pane}-{geometry}");
    // An axis that does not scroll clips nothing on it.
    let rows_right = if scroll_x { view_right } else { plan.width };
    let clips = [("rows", body_top, body_bottom, rows_right, true), ("header", header_top, body_top, view_right, scroll_x)].into_iter().filter(|clip| clip.4).map(|(pane, top, bottom, right, _)| {
        let rect = Node::rect(Role::Chrome, (0.0, top, right, (bottom - top).max(0.0)), Attrs::new());
        Node::new(Role::Chrome, Attrs::new().quick("id", pane_id(pane)), scene::Shape::ClipPath(vec![rect]))
    });
    let pane = |name: &str, attrs: Attrs, content: Vec<Node>| {
        let inner = Node::group(Role::Chrome, attrs, content);
        Node::group(Role::Chrome, Attrs::new().quick("clip-path", format!("url(#{})", pane_id(name))), vec![inner])
    };
    let thumb = |axis: &str, (start, len, cross): (f64, f64, f64), (visible, max): (f64, f64)| {
        let thumb_len = (len * visible / (visible + max)).max(SCROLL_MIN_THUMB).min(len);
        let (x, y, w, h, thumb_w, thumb_h) = if axis == "x" { (start, cross, len, bar.size, thumb_len, bar.size) } else { (cross, start, bar.size, len, bar.size, thumb_len) };
        let track = bar.track_color.as_ref().map(|color| Node::new(Role::Chrome, Attrs::new().quick("fill", color.as_str()), scene::Shape::Rect { x, y, w, h, rx: bar.size / 2.0 }));
        let attrs = Attrs::new().quick("class", "dbt-table-scroll-thumb").quick("data-dbt-scroll-thumb", axis).quick("data-track-start", num_full(start)).quick("data-track-len", num_full(len)).quick("fill", bar.thumb_color.as_str());
        track.into_iter().chain([Node::new(Role::Chrome, attrs, scene::Shape::Rect { x, y, w: thumb_w, h: thumb_h, rx: bar.size / 2.0 })]).collect::<Vec<_>>()
    };
    let rows_h = body_bottom - rows_top;
    let hit = Node::rect(Role::Chrome, (0.0, header_top, view_right, body_bottom - header_top), Attrs::new().quick("class", "dbt-table-scroll-hit").quick("fill", "transparent"));
    let axes = match (scroll_x, scroll_y) {
        (true, true) => "xy",
        (true, false) => "x",
        _ => "y",
    };
    let attrs = Attrs::new()
        .quick("class", "dbt-table-scroll")
        .quick("data-dbt-table-scroll", id)
        .quick("data-max-x", num_full(max_x))
        .quick("data-max-y", num_full(max_y))
        .quick("data-rows-top", num_full(rows_top))
        .quick("data-rows-bottom", num_full(body_bottom));
    let mut children = vec![Node::new(Role::Chrome, Attrs::new(), scene::Shape::Defs(clips.collect())), hit];
    // Header before rows, the document order an unscrolled table has.
    if scroll_x {
        children.push(pane("header", Attrs::new().quick("data-dbt-scroll", "x"), header));
    } else {
        children.extend(header);
    }
    children.push(pane("rows", Attrs::new().quick("data-dbt-scroll", axes).quick("data-dbt-rows", "true"), rows));
    if scroll_y {
        let attrs = Attrs::new().quick("data-dbt-scroll-shadow", "y").quick("fill", "#000000").quick("fill-opacity", "0.06").quick("opacity", "0").quick("pointer-events", "none");
        children.push(Node::rect(Role::Chrome, (0.0, body_top, view_right, SCROLL_SHADOW), attrs));
    }
    if scroll_x {
        // Under the rows, in the band the layout reserved for it.
        children.extend(thumb("x", (bar.inset, view_right - 2.0 * bar.inset, body_bottom + bar.inset), (view_right, max_x)));
    }
    if scroll_y {
        // Right of the rows, in the band the layout reserved for it.
        children.extend(thumb("y", (rows_top + bar.inset, rows_h - 2.0 * bar.inset, view_right + bar.inset), (rows_h, max_y)));
    }
    vec![Node::group(Role::Chrome, attrs, children)]
}

/// layout.rs:`lay_out`'s call to `emit_scroll_pagers`: the pager at `y` under the viewport. A page is a viewport of
/// rows, and `row_ends` are the rows' bottoms from the first row's top.
fn scroll_pager(card: &TableCard, book: &FontBook, id: &str, rows: &RowScroll, row_heights: &[f64], (width, y): (f64, f64)) -> Result<Node, Diagnostic> {
    let mut bottom = 0.0;
    let ends: Vec<f64> = row_heights
        .iter()
        .map(|height| {
            bottom += height;
            bottom
        })
        .collect();
    pager::scroll_pagers(&card.style.paginator, &paint_family(&card.faces.body.family), book, &pager::Viewport { id, width, y, height: rows.viewport, page_rows: rows.shown, ends: &ends })
}

/// layout.rs:`embed_table_scroll_script`. The scroll runtime a static export carries, which no host binds, last so
/// every node it binds as it mounts is already parsed. `table_scroll.js` is pinned byte-equal to production's copy
/// by `tests/test_table_pagination_asset.py`; the engine draws the board a host that ships no page runtime
/// serves, so it always embeds it.
fn scroll_script() -> Node {
    Node::new(Role::Chrome, Attrs::new().quick("type", "text/javascript"), scene::Shape::Script(table_scroll_script()))
}

/// The scroll runtime's source with its fenced engineering notes removed (layout.rs:`embed_table_scroll_script`).
/// `chart-renderer` embeds the same text.
pub fn table_scroll_script() -> String {
    strip_js_comments(include_str!("../../assets/table_scroll.js"))
}

/// Remove every `/*{# ... #}*/`-fenced comment from JavaScript source, port of `comment_stripping.strip_js_comments`.
/// Replaces each fenced span with a single space so adjacent tokens never merge. A plain scan (find the literal
/// `/*{#` marker, then its matching `#}*/` close) is exactly as correct as production's own non-greedy regex here:
/// the 4-character marker is not something real code contains by coincidence, so no string or regex-literal
/// awareness is needed either way.
pub fn strip_js_comments(source: &str) -> String {
    let mut out = String::with_capacity(source.len());
    let mut rest = source;
    while let Some(start) = rest.find("/*{#") {
        out.push_str(&rest[..start]);
        let after_marker = &rest[start + 4..];
        match after_marker.find("#}*/") {
            Some(end) => {
                out.push(' ');
                rest = &after_marker[end + 4..];
            }
            None => {
                // An unterminated fence: production's DOTALL regex would never match without a close either, so the
                // marker (and everything after) passes through untouched.
                out.push_str(&rest[start..]);
                rest = "";
                break;
            }
        }
    }
    out.push_str(rest);
    out
}

/// A wrapped text block: one tspan per line at `at`'s x, each `step` under the last.
fn stacked_lines(attrs: Attrs, at: (f64, f64), step: f64, lines: &[String]) -> Node {
    let runs = lines.iter().enumerate().map(|(i, line)| Run::Tspan { x: Some(at.0), y: Some(at.1 + i as f64 * step), dx: None, dy: None, attrs: Attrs::new(), runs: vec![Run::Chars(Chars::new(line, Escape::Quick))] }).collect();
    Node::new(Role::Label, attrs, scene::Shape::Text { at, bounds: None, runs })
}

/// svg.rs:`num_full`: an integer bare, else Rust's shortest float, as production writes a scroll pane's numbers
/// (never Python's repr, which turns small values to exponents).
fn num_full(v: f64) -> String {
    if v == v.trunc() && v.abs() < 1e12 {
        format!("{}", v as i64)
    } else {
        format!("{v}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use board_model::primitives::LineLimit;

    #[test]
    fn scroll_numbers_print_as_production_svg_writes_them() {
        assert_eq!(num_full(372.0), "372");
        assert_eq!(num_full(0.00005), "0.00005");
        assert_eq!(num_full(0.06657894736842104), "0.06657894736842104");
    }

    #[test]
    fn strip_js_comments_removes_the_fenced_engineering_note() {
        let source = "before/*{# a note\nspanning lines #}*/after";
        assert_eq!(strip_js_comments(source), "before after");
    }

    #[test]
    fn strip_js_comments_leaves_plain_comments_untouched() {
        let source = "// a plain comment\nconst x = 1; /* also plain */";
        assert_eq!(strip_js_comments(source), source);
    }

    /// The same cases as chart-renderer's `column_max_lines` tests: the port must give the renderer's caps.
    #[test]
    fn the_line_limit_is_the_renderers_law() {
        let cap = |setting, lines: &[usize], content| column_max_lines(setting, lines, content, 15.0);
        let lines = [1, 1, 1, 1, 1, 1, 1, 1, 12];
        assert_eq!(cap(TableMaxLines::TrimOutliers, &lines, 1500.0), Some(1));
        assert_eq!(cap(TableMaxLines::Percentile(75), &lines, 1500.0), Some(1));
        let even = [1, 2, 2, 3, 3, 4, 5, 6];
        assert_eq!(cap(TableMaxLines::TrimOutliers, &even, 1500.0), Some(7));
        assert_eq!(cap(TableMaxLines::Percentile(75), &even, 1500.0), Some(4));
        let spread = [8, 9, 10, 10, 11, 12, 40];
        assert_eq!(cap(TableMaxLines::TrimOutliers, &spread, 1500.0), Some(16));
        assert_eq!(cap(TableMaxLines::Percentile(75), &spread, 1500.0), Some(12));
        assert_eq!(cap(TableMaxLines::TrimOutliers, &spread, 45.0), Some(3));
        assert_eq!(cap(TableMaxLines::Percentile(100), &spread, 1500.0), Some(40));
        assert_eq!(cap(TableMaxLines::Percentile(1), &spread, 1500.0), Some(8));
        assert_eq!(cap(TableMaxLines::TrimOutliers, &[], 1500.0), Some(1));
        assert_eq!(cap(TableMaxLines::Limit(LineLimit::lines(2).unwrap()), &spread, 1500.0), Some(2));
        assert_eq!(cap(TableMaxLines::Limit(LineLimit::All), &spread, 1500.0), None);
        assert_eq!(cap(TableMaxLines::Square, &spread, 45.0), Some(3));
    }

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
