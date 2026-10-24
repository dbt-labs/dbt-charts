//! Attached `chart.support_table` on a horizontal bar: a block of value
//! columns beside the plot, one cell per category band, a header row above.
//!
//! Ports `attach_support_table_columns` and its helpers
//! (`support_table_attachment.py`): the transpose of the row strip in
//! `support_table_strip.rs`. Where the strip paints one row per entry at a
//! pixel-literal y over the category x band, the block paints one column per
//! entry at a pixel-literal x over the category y band. Entry resolution
//! (values, declare-once numerals, `per_series` expansion, cell formatting) is
//! the strip's own `entry_lines`/`build_cells`; only the geometry lives here.
//!
//! Everything about a column's width is baked into the spec by production's
//! Python measurer, so it is measured RAW (`FontBook::measure`); the shaped
//! width only feeds the view bounds Vega's autosize reads
//! (`docs/laws-shared.md#font-measurement-policy`).

use std::collections::HashMap;
use std::fmt::Write as _;

use serde_json::Value;

use crate::common::{slug_to_display, TAB_FONT};
use crate::diagnostic::Diagnostic;
use crate::families::support_table_strip::{
    build_cells, divider_line, entry_lines, label_font_resolved, link_cells, row_height, validate_against_data,
    CellAria, ComposedCell, DecimalPad, EntryLines, SupportTableConfig, SupportTableStyle,
};
use crate::svg::{py_float_str};
use print::{vega_escape_attr, xml_escape, xml_escape_attr};
use measure::FontBook;
use crate::vega_fit::{self, Bounds};

/// Which side of the plot the block sits on
/// (`compile.support_table.resolve_support_table_position`, baked onto the
/// resolved chart's `effective_support_table_style.position`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Left,
    Right,
}

impl Side {
    pub fn parse(position: &str) -> Result<Side, String> {
        match position {
            "left" => Ok(Side::Left),
            "right" => Ok(Side::Right),
            other => Err(format!(
                "support_table: position {other:?} on a vertical category axis — the \
                 resolver bakes only \"left\"/\"right\" there"
            )),
        }
    }
}

/// One visual column. A `per_series:` entry expands into one column per
/// series; the columns of one entry share `width`.
#[derive(Debug)]
pub struct Column {
    pub header: String,
    /// Shaped header width — the ink Vega's view bounds read.
    pub header_w: f64,
    /// The footprint reserved for the column (`_entry_column_widths`).
    pub width: f64,
    /// A `per_series:` column's series ink; every other column paints the
    /// style's own cell ink.
    pub ink: Option<String>,
    /// The series a `per_series:` column prints; `None` on every other column.
    pub series: Option<String>,
    /// The entry's hung-suffix width (`_hung_suffix_width`): the cells' digits
    /// share an edge this far inside the header's, and the anchor cell
    /// (`ComposedCell::hang` carries the same width) hangs its suffix out to
    /// it. 0 when the entry has no trailing affix to hang.
    pub hung_w: f64,
    /// One cell per category, `x` the band center along the category axis.
    /// A grouped bar's single per_series column holds one cell per
    /// (series, category), series-major, `x` the sub-band center.
    pub cells: Vec<ComposedCell>,
    /// That column's series, ink and category index for each of `cells`;
    /// empty elsewhere. A missing (category, series) pair has no cell.
    pub cell_series: Vec<(String, Option<String>, usize)>,
    /// How many times each cell paints: once per measure on a wide chart.
    pub copies: usize,
}

#[derive(Debug)]
pub struct ColumnBlock {
    pub columns: Vec<Column>,
    pub side: Side,
    /// Standoff between the block and the plot edge
    /// ([`plot_gutter`]).
    pub gutter: f64,
    pub font_size: f64,
    pub cell_ink: String,
    pub label_ink: String,
}

/// `_column_block_plot_gutter`: the column's own trailing padding plus the
/// axis's label padding — a solid field of digits runs the full plot height, so
/// it earns the same standoff a neighboring axis label gets.
pub fn plot_gutter(style: &SupportTableStyle, axis_label_padding: f64) -> f64 {
    style.row.padding.horizontal + axis_label_padding
}

/// `WARN-PLOT-WIDTH-BELOW-MINIMUM`: the column block claims more than
/// `share_warn_ratio` of the card it shares with the plot, before any axis or
/// legend chrome is subtracted. `plot_width` is the slot width the plot is
/// laid out in.
pub fn warn_block_share(chart_id: &str, block_width: f64, plot_width: f64, share_warn_ratio: f64) {
    let card_width = plot_width + block_width;
    if block_width > 0.0 && plot_width > 0.0 && block_width > share_warn_ratio * card_width {
        crate::diagnostic::warn(crate::diagnostic::Diagnostic::new(
            &crate::codes::WARN_PLOT_WIDTH_BELOW_MINIMUM,
            [
                ("block_width", serde_json::json!(block_width)),
                ("card_width", serde_json::json!(card_width)),
                ("chart_id", serde_json::json!(chart_id)),
                ("plot_width", serde_json::json!(plot_width)),
            ],
        ));
    }
}

/// The numeric cell typeface: Source
/// Serif keeps its own typeface, every other family paints `dbt Sans Tabular`.
fn cell_family(style: &SupportTableStyle) -> &'static str {
    if style.font.family_or("").contains("Source Serif") {
        "Source Serif 4"
    } else {
        "dbt Sans Tabular"
    }
}

/// The font file production's `get_font_measurer(family, numeric=True)` measures a
/// cell with: Source Serif itself, every other family as `dbt Sans Tabular`.
fn cell_measure_family(style: &SupportTableStyle) -> &str {
    if style.font.family_or("").contains("Source Serif") {
        style.font.family_or("")
    } else {
        TAB_FONT
    }
}

/// Build the block: one [`Column`] per visual column, widths per
/// `_entry_column_widths`. `centers[i]` is category `domain[i]`'s band center
/// along the category axis. `anchored` and `series_order` carry the same
/// meaning as `build_rows_composed`'s; `series_ink` is `None` when the style
/// authors one uniform cell ink. The numeral hang is always `"left"` on the
/// column path (`_entry_numerals(..., "left")`). `sub_offsets` is set for a
/// grouped bar: its `per_series` entry paints ONE column, a cell per series
/// on that series' sub-band, `sub_offsets[i]` past the category's band center.
#[allow(clippy::too_many_arguments)]
pub fn build_block(
    chart_id: &str,
    config: &SupportTableConfig,
    rows: &[serde_json::Map<String, Value>],
    cat_field: &str,
    domain: &[String],
    centers: &[f64],
    fonts: &FontBook,
    style: &SupportTableStyle,
    side: Side,
    axis_label_padding: f64,
    anchored: bool,
    color_field: Option<&str>,
    series_order: &[String],
    series_ink: Option<&[String]>,
    sub_offsets: Option<&[f64]>,
    links: Option<&HashMap<(String, String), String>>,
) -> Result<ColumnBlock, Diagnostic> {
    if domain.len() != centers.len() {
        return Err("support_table: domain/centers length mismatch".into());
    }
    let rows = config.source_rows(rows);
    validate_against_data(config, rows, cat_field, domain, false)?;
    let font_size = style.font.size.ok_or("support_table: font.size absent (theme required)")?;
    let cell_ink =
        style.font.color.clone().ok_or("support_table: font.color absent (theme required)")?;
    let label_ink = style
        .label
        .font
        .color
        .clone()
        .ok_or("support_table: label.font.color absent (theme required)")?;
    let cell_measure = cell_measure_family(style);
    let cell_paint = cell_family(style);
    let (label_family, label_size) = label_font_resolved(style);
    let header_measure = measure::stand_in_family(label_family);
    let pad = 2.0 * style.row.padding.horizontal;

    let decimals = DecimalPad::new(fonts, style)?;
    let mut columns: Vec<Column> = Vec::new();
    for (entry_index, entry) in config.entries.iter().enumerate() {
        // `axis_on_right = true, vertical = true`: the hang is always "left"
        // and the magnitude suffix speaks the column register.
        let EntryLines { spec, lines, numerals, nums } =
            entry_lines(entry, rows, cat_field, domain, anchored, true, true, color_field, series_order, &decimals, chart_id, entry_index)?;
        if entry.per_series.is_some() && series_ink.is_some_and(|ink| ink.len() != series_order.len()) {
            return Err("support_table: internal error — series_ink/series_order length mismatch".into());
        }
        let label_of = |entry_label: &Option<String>, source: &Option<String>| -> Result<String, String> {
            match entry_label {
                Some(l) => Ok(l.clone()),
                None => slug_to_display(
                    source.as_deref().expect("entry_values already required a source"),
                    "none",
                ),
            }
        };
        let single_column = entry.per_series.is_some() && sub_offsets.is_some();
        let headers: Vec<String> = if single_column {
            vec![match &entry.label {
                Some(l) => l.clone(),
                None => slug_to_display(entry.per_series.as_deref().expect("single_column implies per_series"), "none")?,
            }]
        } else if entry.per_series.is_some() {
            series_order.to_vec()
        } else {
            vec![label_of(&entry.label, &entry.source)?]
        };

        // The widest cell any of the entry's columns could paint. A column has
        // no free gutter for an anchor's affix to overhang, so every cell is
        // measured as if it were the anchor (`conservative=True`).
        let mut value_w = 0.0_f64;
        match (&numerals, spec) {
            (Some(n), Some(_)) => {
                let painted: Vec<String> = nums
                    .iter()
                    .filter(|v| v.is_finite())
                    .map(|&v| n.cell_text(v, true))
                    .collect::<Result<_, _>>()?;
                for text in &painted {
                    value_w = value_w.max(fonts.measure(text, cell_measure, font_size)?);
                }
            }
            _ => {
                // `_entry_cell_widths` measures `str(float)` though the cell paints the bare JSON number.
                for &v in nums.iter().filter(|v| v.is_finite()) {
                    value_w = value_w.max(fonts.measure(&py_float_str(v), cell_measure, font_size)?);
                }
            }
        }
        // From the numerals, not from an anchor cell: production insets the
        // digits by the hung suffix even when no cell of the column carries.
        let hung_w = match &numerals {
            Some(n) => fonts.measure(n.hung_suffix(), cell_measure, font_size)?,
            None => 0.0,
        };
        let mut header_w = 0.0_f64;
        for h in &headers {
            header_w = header_w.max(fonts.measure(h, header_measure, label_size)?);
        }
        let width = value_w.max(header_w) + pad;

        if let (true, Some(offsets)) = (single_column, sub_offsets) {
            let mut cells = Vec::new();
            let mut cell_series = Vec::new();
            for (si, series) in series_order.iter().enumerate() {
                let series_centers: Vec<f64> = centers.iter().map(|c| c + offsets[si]).collect();
                let (series_cells, _bare_w) =
                    build_cells(&lines[si], &series_centers, &numerals, spec, fonts, cell_paint, font_size, false)?;
                for (ci, mut cell) in series_cells.into_iter().enumerate() {
                    if lines[si][ci].is_none() {
                        continue;
                    }
                    link_cells(std::slice::from_mut(&mut cell), entry, &domain[ci..=ci], links);
                    cell_series.push((series.clone(), series_ink.map(|ink| ink[si].clone()), ci));
                    cells.push(cell);
                }
            }
            let header = headers.into_iter().next().expect("single_column has one header");
            columns.push(Column {
                header_w: fonts.measure_shaped(&header, label_family, label_size)?,
                header,
                width,
                ink: None,
                series: None,
                hung_w,
                cells,
                cell_series,
                copies: 1,
            });
            continue;
        }
        for (i, header) in headers.into_iter().enumerate() {
            let (mut cells, _bare_w) =
                build_cells(&lines[i], centers, &numerals, spec, fonts, cell_paint, font_size, false)?;
            link_cells(&mut cells, entry, domain, links);
            let header_shaped = fonts.measure_shaped(&header, label_family, label_size)?;
            columns.push(Column {
                header,
                header_w: header_shaped,
                width,
                ink: series_ink.filter(|_| entry.per_series.is_some()).map(|ink| ink[i].clone()),
                series: entry.per_series.as_ref().map(|_| series_order[i].clone()),
                hung_w,
                cells,
                cell_series: Vec::new(),
                copies: if entry.per_series.is_some() { 1 } else { config.cell_copies() },
            });
        }
    }
    Ok(ColumnBlock {
        columns,
        side,
        gutter: plot_gutter(style, axis_label_padding),
        font_size,
        cell_ink,
        label_ink,
    })
}

impl Column {
    /// A cell's right edge for a column whose header sits at `edge`: the digits
    /// share `edge - hung_w` and the anchor's suffix hangs out to `edge`
    /// (`_column_cell_layers`, `_hung_anchor_layers`).
    fn cell_x(&self, edge: f64, cell: &ComposedCell) -> f64 {
        edge - self.hung_w + cell.hang
    }
}

impl ColumnBlock {
    pub fn describe(&mut self, aria: &CellAria) {
        for col in &mut self.columns {
            for (i, cell) in col.cells.iter_mut().enumerate() {
                let (series, x_index) = match col.cell_series.get(i) {
                    Some((s, _, ci)) => (Some(s.as_str()), *ci),
                    None => (col.series.as_deref(), i),
                };
                cell.aria = aria.label(x_index, series);
            }
        }
    }

    /// Move every cell to its band center at a plot `y_scale` times the height
    /// the block was built against.
    pub fn rescale(&mut self, y_scale: f64) {
        for cell in self.columns.iter_mut().flat_map(|c| c.cells.iter_mut()) {
            cell.x *= y_scale;
        }
    }

    /// Footprint beside the plot: every column plus the standoff.
    pub fn footprint(&self) -> f64 {
        self.columns.iter().map(|c| c.width).sum::<f64>() + self.gutter
    }

    /// `_column_edges`: each column's RIGHT edge — where its header and cells
    /// right-align — in plot coordinates, columns in authored order left to
    /// right on both sides. Left: the block spans `[-footprint, -gutter]`.
    /// Right: it starts `gutter` past the plot's right edge.
    pub fn right_edges(&self, plot_w: f64) -> Vec<f64> {
        let mut cursor = match self.side {
            Side::Left => -self.footprint(),
            Side::Right => plot_w + self.gutter,
        };
        self.columns
            .iter()
            .map(|c| {
                cursor += c.width;
                cursor
            })
            .collect()
    }

    /// The ink the block adds to Vega's autosize-pad view bounds, in plot
    /// coordinates: each header at `-row_height/2` and each cell on its band,
    /// right-aligned at its column's edge (`cell_x`).
    ///
    /// `y_scale` is `plot_h / h` for the height the cells were centered
    /// against: a band center is linear in the plot height, so a fit that
    /// settles on another height moves every cell with it.
    pub fn ink(&self, b: &mut Bounds, style: &SupportTableStyle, plot_w: f64, y_scale: f64) {
        let font_size = self.font_size;
        let (_, label_size) = label_font_resolved(style);
        let header_y = -row_height(style) / 2.0;
        for (col, edge) in self.columns.iter().zip(self.right_edges(plot_w)) {
            b.union(&vega_fit::text_ink(edge, header_y, col.header_w, label_size, "right", "middle"));
            for cell in &col.cells {
                b.union(&vega_fit::text_ink(col.cell_x(edge, cell), cell.x * y_scale, cell.w, font_size, "right", "middle"));
            }
        }
    }

    /// Paint the block with the plot's top-left at `(origin_x, origin_y)`.
    pub fn render(
        &self,
        out: &mut String,
        style: &SupportTableStyle,
        plot_w: f64,
        plot_h: f64,
        origin_x: f64,
        origin_y: f64,
        link_open: Option<crate::href::LinkOpen>,
    ) -> Result<(), String> {
        // Headers and rules read no datum: on a linked chart each carries an undefined link.
        let undefined = link_open.and_then(|open| open.anchor(Some(crate::href::UNDEFINED_HREF)));
        let font_size = self.font_size;
        let cell_ink = self.cell_ink.as_str();
        let cell_weight = style.font.weight_str();
        let (label_family, label_size) = label_font_resolved(style);
        let label_ink = self.label_ink.as_str();
        let label_weight = style.label.font.weight_str();
        let cell_shift = measure::baseline_shift("middle", font_size);
        let label_shift = measure::baseline_shift("middle", label_size);
        let header_y = origin_y - row_height(style) / 2.0 + label_shift;
        let paint = cell_family(style);
        let last = self.columns.len() - 1;
        for (i, (col, edge)) in self.columns.iter().zip(self.right_edges(plot_w)).enumerate() {
            let x = origin_x + edge;
            crate::contract::mark_group_open(out, "text", "strip")?;
            out.push_str(&crate::href::anchor_open(undefined));
            let _ = write!(
                out,
                "<text x=\"{x:.2}\" y=\"{header_y:.2}\" text-anchor=\"end\" font-family=\"{}\" \
                 font-size=\"{label_size}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                vega_escape_attr(label_family),
                xml_escape_attr(&label_weight),
                xml_escape_attr(label_ink),
                xml_escape(&col.header),
            );
            out.push_str(crate::href::anchor_close(undefined));
            crate::contract::mark_group_close(out);
            crate::contract::mark_group_open(out, "text", "strip")?;
            for (k, cell) in col.cells.iter().enumerate() {
                let ink = col.cell_series.get(k).and_then(|(_, ink, _)| ink.as_deref()).or(col.ink.as_deref()).unwrap_or(cell_ink);
                let cell_x = origin_x + col.cell_x(edge, cell);
                let y = origin_y + cell.x + cell_shift;
                let anchor = link_open.and_then(|open| open.anchor(cell.href.as_deref()));
                for _ in 0..col.copies {
                    out.push_str(&crate::href::anchor_open(anchor));
                    let _ = write!(
                        out,
                        "<text{} x=\"{cell_x:.2}\" y=\"{y:.2}\" text-anchor=\"end\" font-family=\"{}\" \
                         font-size=\"{font_size}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                        print::attributes(&crate::contract::cell_label_attrs(&cell.aria)?),
                        vega_escape_attr(paint),
                        xml_escape_attr(&cell_weight),
                        xml_escape_attr(ink),
                        xml_escape(&cell.text),
                    );
                    out.push_str(crate::href::anchor_close(anchor));
                }
            }
            crate::contract::mark_group_close(out);
            if style.row.divider.visible && i < last {
                // `_column_rule_layer`: every column right-aligns AT its edge,
                // so the line clears the left column's glyphs by one padding
                // and still short of the next column's leftmost glyph.
                let rule_x = x + style.row.padding.horizontal;
                out.push_str(&crate::href::anchor_open(undefined));
                out.push_str(&print::print(&[divider_line(rule_x, origin_y, rule_x, origin_y + plot_h, &style.row.divider)]));
                out.push_str(crate::href::anchor_close(undefined));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::families::support_table_strip::parse_config;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn style() -> SupportTableStyle {
        serde_json::from_value(style_json()).unwrap()
    }

    fn style_json() -> Value {
        serde_json::json!({
            "font": {"family": "'dbt Sans Tabular', Inter, sans-serif", "size": 11.0,
                     "color": "#626366", "weight": "400"},
            "divider": {"gap": 4.0, "visible": false, "color": "#626366", "width": 1.0, "dasharray": null},
            "row": {"padding": {"vertical": 2.0, "horizontal": 6.0},
                    "divider": {"visible": false, "color": "#626366", "width": 1.0, "dasharray": null}},
            "label": {"font": {"family": "'dbt Sans Tabular', Inter, sans-serif", "size": 11.0,
                               "weight": "500", "color": "#626366"}},
            "padding_top": 8.0,
            "padding_bottom": 4.0,
            "label_max_lines": 2,
            "position": "left",
        })
    }

    fn domain() -> Vec<String> {
        ["A", "B", "C"].iter().map(|s| s.to_string()).collect()
    }

    fn rows() -> Vec<serde_json::Map<String, Value>> {
        [("A", 5.0, 1200000.0), ("B", 60.0, 30.0), ("C", 7.0, 8.0)]
            .iter()
            .map(|(c, u, r)| {
                serde_json::from_value(serde_json::json!({"cat": c, "units": u, "revenue": r}))
                    .unwrap()
            })
            .collect()
    }

    fn ratio_rows() -> Vec<serde_json::Map<String, Value>> {
        [("A", 0.05), ("B", 0.6), ("C", 0.07)]
            .iter()
            .map(|(c, u)| serde_json::from_value(serde_json::json!({"cat": c, "units": u})).unwrap())
            .collect()
    }

    fn block_of(entries: serde_json::Value, anchored: bool) -> Result<ColumnBlock, Diagnostic> {
        block_over(&rows(), entries, anchored)
    }

    fn block_over(
        rows: &[serde_json::Map<String, Value>],
        entries: serde_json::Value,
        anchored: bool,
    ) -> Result<ColumnBlock, Diagnostic> {
        let config = parse_config(&serde_json::json!({ "entries": entries }), "bar").unwrap();
        let dom = domain();
        let centers = [10.0, 30.0, 50.0];
        build_block("c",
            &config,
            rows,
            "cat",
            &dom,
            &centers,
            &test_fonts(),
            &style(),
            Side::Left,
            6.0,
            anchored,
            None,
            &[],
            None, None, None)
    }

    /// `_column_block_plot_gutter`: the column's own trailing padding plus the
    /// axis's label padding.
    #[test]
    fn gutter_is_row_padding_plus_axis_label_padding() {
        assert_eq!(plot_gutter(&style(), 6.0), 12.0);
        assert_eq!(plot_gutter(&style(), 9.5), 15.5);
    }

    /// `_column_edges`: left block spans `[-(total + gutter), -gutter]` in
    /// authored order; right block starts `gutter` past the plot.
    #[test]
    fn right_edges_run_left_to_right_on_both_sides() {
        let col = |width: f64| Column { header: String::new(), header_w: 0.0, width, ink: None, series: None, hung_w: 0.0, cells: vec![], cell_series: vec![], copies: 1 };
        let mut block = ColumnBlock {
            columns: vec![col(50.0), col(30.0)],
            side: Side::Left,
            gutter: 12.0,
            font_size: 11.0,
            cell_ink: String::new(),
            label_ink: String::new(),
        };
        assert_eq!(block.footprint(), 92.0);
        assert_eq!(block.right_edges(400.0), vec![-42.0, -12.0]);
        block.side = Side::Right;
        assert_eq!(block.right_edges(400.0), vec![462.0, 492.0]);
    }

    /// `_entry_column_widths`: max(widest cell, header) + 2 * padding.horizontal,
    /// cells measured with the tabular typeface, the header with the Inter stand-in.
    #[test]
    fn column_width_is_the_widest_cell_or_header_plus_two_paddings() {
        let fonts = test_fonts();
        let block = block_of(serde_json::json!([{"source": "units", "format": ",d", "label": "U"}]), false)
            .unwrap();
        let cell_w = fonts.measure("60", TAB_FONT, 11.0).unwrap();
        let header_w = fonts.measure("U", "Inter", 11.0).unwrap();
        assert!(cell_w > header_w);
        assert_eq!(block.columns[0].width, cell_w + 12.0);
    }

    /// 26F: a header wider than every value sizes its own column.
    #[test]
    fn a_long_header_widens_its_column_past_the_values() {
        let fonts = test_fonts();
        let label = "A Very Long Column Header Name";
        let block =
            block_of(serde_json::json!([{"source": "units", "format": ",d", "label": label}]), false)
                .unwrap();
        assert_eq!(block.columns[0].width, fonts.measure(label, "Inter", 11.0).unwrap() + 12.0);
    }

    /// `conservative=True`: no free gutter for an anchor's affix, so a column
    /// reserves the anchor-decorated width even for cells that drop the `$`.
    #[test]
    fn a_column_reserves_the_anchor_decorated_width() {
        let fonts = test_fonts();
        let block =
            block_of(serde_json::json!([{"source": "revenue", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "R"}]), true)
                .unwrap();
        let widest = fonts.measure("$1,200,000", TAB_FONT, 11.0).unwrap();
        assert_eq!(block.columns[0].width, widest + 12.0);
        // The anchor is the first carrying cell; the rest drop the `$`.
        let texts: Vec<&str> = block.columns[0].cells.iter().map(|c| c.text.as_str()).collect();
        assert_eq!(texts, vec!["$1,200,000", "30", "8"]);
    }

    /// The `x` of `<text ...>{text}</text>` in `svg`.
    fn text_x(svg: &str, text: &str) -> f64 {
        let end = svg.find(&format!(">{text}</text>")).unwrap_or_else(|| panic!("{text}: {svg}"));
        let tag = &svg[svg[..end].rfind("<text x=\"").unwrap() + 9..end];
        tag[..tag.find('"').unwrap()].parse().unwrap()
    }

    /// A column cell's `x` stays its band center along the category axis; the
    /// anchor carries its suffix width as `hang`, and the column paints every
    /// digit `hung_w` inside its header edge while the anchor's suffix hangs
    /// out to that edge (`_column_cell_layers`).
    #[test]
    fn an_anchored_percent_column_hangs_its_anchor_suffix_past_the_digit_edge() {
        let pct_w = test_fonts().measure("%", TAB_FONT, 11.0).unwrap();
        let block = block_over(
            &ratio_rows(),
            serde_json::json!([{"source": "units", "format": {"spec": ".0%", "raw": "percent_whole"}, "label": "U"}]),
            true,
        )
        .unwrap();
        let col = &block.columns[0];
        let texts: Vec<&str> = col.cells.iter().map(|c| c.text.as_str()).collect();
        assert_eq!(texts, vec!["5%", "60", "7"]);
        let bands: Vec<f64> = col.cells.iter().map(|c| c.x).collect();
        assert_eq!(bands, vec![10.0, 30.0, 50.0]);
        let hangs: Vec<f64> = col.cells.iter().map(|c| c.hang).collect();
        assert_eq!(hangs, vec![pct_w, 0.0, 0.0]);
        assert_eq!(col.hung_w, pct_w);

        let mut out = String::new();
        block.render(&mut out, &style(), 400.0, 300.0, 100.0, 50.0, None).unwrap();
        let edge = 100.0 + block.right_edges(400.0)[0];
        assert!((text_x(&out, "5%") - edge).abs() < 0.005, "anchor at the header edge");
        assert!((text_x(&out, "60") - (edge - pct_w)).abs() < 0.005, "bare digits one suffix in");
        assert!((text_x(&out, "U") - edge).abs() < 0.005, "header stays at the edge");

        let mut b = Bounds::empty_new();
        block.ink(&mut b, &style(), 400.0, 1.0);
        let mut want = Bounds::empty_new();
        let edge = block.right_edges(400.0)[0];
        for cell in &col.cells {
            let x = if cell.hang > 0.0 { edge } else { edge - pct_w };
            want.union(&vega_fit::text_ink(x, cell.x, cell.w, 11.0, "right", "middle"));
        }
        assert_eq!((b.x1, b.x2), (want.x1, want.x2));
    }

    /// `_hung_suffix_width` reads the numerals, not an anchor cell: a column
    /// whose entry declares a suffix insets its digits even when it paints
    /// no anchor.
    #[test]
    fn an_unanchored_percent_column_hangs_nothing() {
        let block = block_over(
            &ratio_rows(),
            serde_json::json!([{"source": "units", "format": {"spec": ".0%", "raw": "percent_whole"}, "label": "U"}]),
            false,
        )
        .unwrap();
        assert_eq!(block.columns[0].hung_w, 0.0);
        assert!(block.columns[0].cells.iter().all(|c| c.hang == 0.0));
    }

    /// A column speaks the analytic register while its magnitude suffix
    /// declares once: the whole word is the hung suffix, measured in the
    /// tabular typeface. Production's
    /// `get_font_measurer("'dbt Sans Tabular', Inter, sans-serif",
    /// numeric=True).measure("M", 11)` is 9.9365234375.
    #[test]
    fn a_compacting_column_hangs_the_analytic_word() {
        let rows: Vec<serde_json::Map<String, Value>> = [("A", 120e6), ("B", 340e6), ("C", 560e6)]
            .iter()
            .map(|(c, r)| serde_json::from_value(serde_json::json!({"cat": c, "revenue": r})).unwrap())
            .collect();
        let block = block_over(
            &rows,
            serde_json::json!([{"source": "revenue", "format": {"spec": ".3~s", "raw": "number"}, "label": "R"}]),
            true,
        )
        .unwrap();
        let col = &block.columns[0];
        let texts: Vec<&str> = col.cells.iter().map(|c| c.text.as_str()).collect();
        assert_eq!(texts, vec!["120M", "340", "560"]);
        assert!((col.hung_w - 9.9365234375).abs() < 1e-6, "{}", col.hung_w);
        assert_eq!(col.cells[0].hang, col.hung_w);
    }

    /// An unformatted entry paints the bare number but reserves the width of
    /// Python's `str(float)` (`320` paints, `320.0` is measured).
    #[test]
    fn an_unformatted_entry_reserves_python_float_repr_width() {
        let fonts = test_fonts();
        let block = block_of(serde_json::json!([{"source": "units", "label": "U"}]), false).unwrap();
        assert_eq!(block.columns[0].width, fonts.measure("60.0", TAB_FONT, 11.0).unwrap() + 12.0);
        assert_eq!(block.columns[0].cells[1].text, "60");
    }

    /// No `label:`: the header is the title-cased source name
    /// (`_default_support_table_label`).
    #[test]
    fn the_default_header_is_the_title_cased_source() {
        let block = block_of(serde_json::json!([{"source": "units", "format": ",d"}]), false).unwrap();
        assert_eq!(block.columns[0].header, "Units");
    }

    /// A per_series entry paints one column per series, all sharing a width
    /// sized to the widest series name or value; headers are the series names.
    #[test]
    fn per_series_columns_share_one_width_and_take_series_names() {
        let fonts = test_fonts();
        let config = parse_config(&serde_json::json!({"entries": [{"per_series": "revenue", "format": ",d"}]}), "bar").unwrap();
        let rows: Vec<serde_json::Map<String, Value>> = [
            ("A", "Online", 9.0), ("A", "Retail Stores", 4.0),
            ("B", "Online", 3.0), ("B", "Retail Stores", 2.0),
        ]
        .iter()
        .map(|(c, s, v)| serde_json::from_value(serde_json::json!({"cat": c, "series": s, "revenue": v})).unwrap())
        .collect();
        let dom: Vec<String> = vec!["A".into(), "B".into()];
        let series: Vec<String> = vec!["Online".into(), "Retail Stores".into()];
        let ink: Vec<String> = vec!["#111111".into(), "#222222".into()];
        let block = build_block("c",
            &config, &rows, "cat", &dom, &[10.0, 30.0], &fonts, &style(), Side::Left, 6.0, false,
            Some("series"), &series, Some(&ink), None, None)
        .unwrap();
        assert_eq!(block.columns.len(), 2);
        assert_eq!(block.columns[0].header, "Online");
        assert_eq!(block.columns[1].header, "Retail Stores");
        let want = fonts.measure("Retail Stores", "Inter", 11.0).unwrap() + 12.0;
        assert_eq!(block.columns[0].width, want);
        assert_eq!(block.columns[1].width, want);
        assert_eq!(block.columns[1].ink.as_deref(), Some("#222222"));
    }

    /// A trim fixed-point spec pads each cell to the width of its untrimmed
    /// form (`decimal_pad_table_for`), so `60` reserves `60.00`.
    #[test]
    fn a_trim_fixed_point_spec_pads_cells_to_the_untrimmed_width() {
        let fonts = test_fonts();
        let block = block_of(serde_json::json!([{"source": "units", "format": ",.2~f", "label": "U"}]), false)
            .unwrap();
        for (cell, untrimmed) in block.columns[0].cells.iter().zip(["5.00", "60.00", "7.00"]) {
            let w = fonts.measure(&cell.text, TAB_FONT, 11.0).unwrap();
            let want = fonts.measure(untrimmed, TAB_FONT, 11.0).unwrap();
            assert!((w - want).abs() < 0.6, "{:?} measures {w}, {untrimmed} measures {want}", cell.text);
        }
        assert!(block.columns[0].cells[1].text.starts_with("60"));
        assert_ne!(block.columns[0].cells[1].text, "60");
    }

    /// An SI row whose cells share one unit but print different fractional
    /// depths (`15k`, `3.5k`) aligns on the deepest (`_unscaled_decimal_pad`).
    #[test]
    fn an_si_row_with_mixed_fractional_depths_pads_to_the_deepest() {
        let fonts = test_fonts();
        let rows: Vec<serde_json::Map<String, Value>> = [("A", 15000.0), ("B", 5000.0), ("C", 3500.0)]
            .iter()
            .map(|(c, r)| serde_json::from_value(serde_json::json!({"cat": c, "revenue": r})).unwrap())
            .collect();
        let block = block_over(
            &rows,
            serde_json::json!([{"source": "revenue", "format": {"spec": ".3~s", "raw": "number"}, "label": "R"}]),
            false,
        )
        .unwrap();
        let cells = &block.columns[0].cells;
        assert!(cells[2].text.starts_with("3.5k"), "{:?}", cells[2].text);
        assert_ne!(cells[0].text, "15k");
        let deepest = fonts.measure(&cells[2].text, TAB_FONT, 11.0).unwrap();
        let shallow = fonts.measure(&cells[0].text, TAB_FONT, 11.0).unwrap();
        assert!((deepest - shallow).abs() > 1.0, "15k and 3.5k are different glyph runs");
    }

    /// Cells that print different units (`1.2M`, `30`) share no decimal
    /// position, so nothing pads.
    #[test]
    fn an_si_row_printing_different_units_pads_nothing() {
        let block = block_of(serde_json::json!([{"source": "revenue", "format": {"spec": ".3~s", "raw": "number"}, "label": "R"}]), false)
            .unwrap();
        assert_eq!(block.columns[0].cells[1].text, "30");
    }

    /// `_entry_cell_widths` measures floats: a bare text source paints its
    /// strings verbatim and reserves no cell width (the header still does),
    /// while a numeric-looking string counts as the float Python parses.
    #[test]
    fn a_text_cell_paints_verbatim_and_reserves_width_only_for_a_parsed_number() {
        let config = parse_config(&serde_json::json!({"entries": [{"source": "note"}]}), "bar").unwrap();
        let block = |notes: [&str; 2]| {
            let rows: Vec<serde_json::Map<String, Value>> = [("A", notes[0]), ("B", notes[1])]
                .iter()
                .map(|(c, n)| serde_json::from_value(serde_json::json!({"cat": c, "note": n})).unwrap())
                .collect();
            let dom: Vec<String> = vec!["A".into(), "B".into()];
            build_block("c",
                &config, &rows, "cat", &dom, &[10.0, 30.0], &test_fonts(), &style(), Side::Left, 6.0,
                false, None, &[], None, None, None)
            .unwrap()
        };
        let text = block(["n/a", "ok"]);
        assert_eq!(text.columns[0].cells[0].text, "n/a");
        assert_eq!(text.columns[0].cells[1].text, "ok");
        let parsed = block([" 1e20 ", "ok"]);
        assert!(parsed.columns[0].width > text.columns[0].width, "a parsed float reserves its `str(float)` width");
    }

    /// The strip's data guards run on the block too: a bare `source` over a
    /// repeated category is ambiguous (`validate_support_table_against_data`).
    #[test]
    fn a_source_over_a_repeated_category_refuses() {
        let config = parse_config(&serde_json::json!({"entries": [{"source": "units", "format": ",d"}]}), "bar").unwrap();
        let mut rows = rows();
        rows.push(rows[0].clone());
        let err = build_block("c",
            &config, &rows, "cat", &domain(), &[10.0, 30.0, 50.0], &test_fonts(), &style(),
            Side::Left, 6.0, false, None, &[], None, None, None)
        .unwrap_err();
        assert!(err.contains("ambiguous"), "{err}");
    }

    /// The resolver bakes only `left`/`right` on a vertical category axis.
    #[test]
    fn a_strip_position_on_a_vertical_axis_refuses() {
        assert_eq!(Side::parse("right"), Ok(Side::Right));
        assert!(Side::parse("top").unwrap_err().contains("\"top\""));
    }

    fn divider_style(visible: bool, width: f64, color: &str, dasharray: Option<&str>) -> SupportTableStyle {
        let mut value = style_json();
        value["row"]["divider"] =
            serde_json::json!({"visible": visible, "width": width, "color": color, "dasharray": dasharray});
        serde_json::from_value(value).unwrap()
    }

    fn rendered(style: &SupportTableStyle) -> String {
        let block = block_of(
            serde_json::json!([
                {"source": "units", "format": ",d", "label": "U"},
                {"source": "revenue", "format": ",d", "label": "R"}
            ]),
            false,
        )
        .unwrap();
        let mut out = String::new();
        block.render(&mut out, style, 400.0, 300.0, 100.0, 50.0, None).unwrap();
        out
    }

    /// `_column_rule_layer`: every column right-aligns AT its edge, so the
    /// rule between two columns sits one row padding past the left column's
    /// edge and spans the plot's full height.
    #[test]
    fn a_rule_sits_one_padding_past_the_left_columns_edge() {
        let out = rendered(&divider_style(true, 1.0, "#8A8C8F", None));
        let block = block_of(
            serde_json::json!([
                {"source": "units", "format": ",d", "label": "U"},
                {"source": "revenue", "format": ",d", "label": "R"}
            ]),
            false,
        )
        .unwrap();
        let x = 100.0 + block.right_edges(400.0)[0] + 6.0;
        let want = format!(
            "<line x1=\"{x:.2}\" y1=\"50.00\" x2=\"{x:.2}\" y2=\"350.00\" \
             stroke=\"#8A8C8F\" stroke-width=\"1\"/>"
        );
        assert!(out.contains(&want), "{want}\n{out}");
        assert_eq!(out.matches("<line").count(), 1, "none after the last column");
    }

    /// `_divider_mark`: the divider's own width and color, its pattern as the
    /// comma-joined `strokeDash` Vega writes.
    #[test]
    fn a_dashed_divider_paints_its_own_width_color_and_dash() {
        let out = rendered(&divider_style(true, 2.0, "#cc7700", Some("6 3")));
        assert!(
            out.contains("stroke=\"#cc7700\" stroke-width=\"2\" stroke-dasharray=\"6,3\"/>"),
            "{out}"
        );
    }

    /// `visible: false` switches the line off whatever width or color it carries.
    #[test]
    fn a_hidden_divider_paints_nothing() {
        assert!(!rendered(&divider_style(false, 3.0, "#8A8C8F", Some("6 3"))).contains("<line"));
    }

    /// The theme cascade always bakes the cell font's size and ink and the
    /// header's ink; production raises on a missing size rather than guess one.
    #[test]
    fn a_style_missing_its_font_size_or_ink_refuses_by_name() {
        let config = parse_config(&serde_json::json!({"entries": [{"source": "units", "format": ",d"}]}), "bar").unwrap();
        for (path, needle) in [
            (["font", "size"], "font.size"),
            (["font", "color"], "font.color"),
        ] {
            let mut value = style_json();
            value[path[0]].as_object_mut().unwrap().remove(path[1]);
            let style: SupportTableStyle = serde_json::from_value(value).unwrap();
            let err = build_block("c",
                &config, &rows(), "cat", &domain(), &[10.0, 30.0, 50.0], &test_fonts(), &style,
                Side::Left, 6.0, false, None, &[], None, None, None)
            .unwrap_err();
            assert!(err.contains(needle), "{err}");
        }
        let mut value = style_json();
        value["label"]["font"].as_object_mut().unwrap().remove("color");
        let style: SupportTableStyle = serde_json::from_value(value).unwrap();
        let err = build_block("c",
            &config, &rows(), "cat", &domain(), &[10.0, 30.0, 50.0], &test_fonts(), &style,
            Side::Left, 6.0, false, None, &[], None, None, None)
        .unwrap_err();
        assert!(err.contains("label.font.color"), "{err}");
    }

    #[test]
    fn an_integer_format_carries_no_pad() {
        block_of(serde_json::json!([{"source": "units", "format": ",d", "label": "U"}]), false).unwrap();
    }
}
