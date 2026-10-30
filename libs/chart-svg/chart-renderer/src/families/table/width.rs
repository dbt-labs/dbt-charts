//! A table's natural width: what a board with no authored width hugs.
//!
//! Board sizing asks for it (`layout_sizing.py::_make_chart_width_measurer`), so
//! the board hugs the width the table's columns need instead of the 800px theme
//! estimate. The laws under it are `natural_content_width` and the pager band
//! width.
//!
//! The measure is a function of the table's resolved chart and its rows —
//! the formatted cell text and the table theme, both already in the payload —
//! so it takes exactly that and nothing else: no slot width, no board.

use std::collections::HashMap;

use serde::Deserialize;
use serde_json::Value;

use super::{
    auto_column_demand, build_cell_data, column_inputs, is_compact_column, natural_auto_width,
    paginator_window, parse_column_width, pivot_for, resolve_row_roles, svg_font_family,
    table_rows, visible_columns, ColumnInputs, page_slot_width, MeasureFonts, PageItem, Resolved, WidthHint,
    PAGINATION_GROW_CAP, PAGINATOR_AUX_SLOT_RATIO,
};
use crate::common::measure_family_stand_in;
use crate::text::FontBook;
use d3rs::py_float::py_float_sum;

/// What the measure reads: the table's resolved chart, its rows, and the
/// fonts to measure in.
#[derive(Deserialize)]
#[serde(remote = "Self")]
pub struct MeasurePayload {
    pub resolved: Value,
    pub rows: Vec<serde_json::Map<String, Value>>,
    pub fonts_dir: Option<String>,
    /// The board's number locale: the formatted cell text is what is measured.
    pub format_locale: crate::number_locale::NumberLocale,
    /// The board's time locale: a date cell's month and day names.
    pub time_locale: crate::time_locale::TimeLocale,
    /// The board's language: a column header derived from its name speaks it.
    #[serde(default)]
    pub language: display_text::language::Language,
    /// The board's `style.formats` alias table.
    #[serde(default)]
    pub formats: Option<serde_json::Map<String, Value>>,
    /// The board's plain-digit siblings of the adaptive formats.
    #[serde(default)]
    pub sub_unit_specs: Option<std::collections::BTreeMap<String, String>>,
    /// The `str()` text of each tagged cell in `rows`; filled when parsed.
    #[serde(skip)]
    pub cell_text: crate::common::CellText,
}

impl<'de> Deserialize<'de> for MeasurePayload {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let mut payload = MeasurePayload::deserialize(deserializer)?;
        payload.cell_text = crate::common::decode_cells(&mut payload.rows).map_err(serde::de::Error::custom)?;
        Ok(payload)
    }
}

/// An authored column `width:` — `parse_column_width(hint, 1.0)`: a px
/// number, or a fraction of the final budget.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Pin {
    Px(f64),
    Percent(f64),
}

/// One column's inputs to `natural_content_width`.
#[derive(Clone, Copy, Debug)]
pub struct NaturalColumn {
    pub pin: Option<Pin>,
    /// A px `max_width`; a percentage one is relative to the final width,
    /// unknown while hugging, so the caller passes none.
    pub max_width: Option<f64>,
    pub spark: bool,
    pub demand: f64,
    pub header_demand: f64,
    pub word_floor: f64,
}

/// `natural_content_width`: the data-column budget at
/// which the allocator never overflows. Px pins as written, each auto column
/// at its natural width (`natural_auto_width`), `%` pins solving against the
/// rest (`S / (1 - p)`). `None` when there is nothing to hug: `%` pins total
/// 100% or more, or no column carries a width of its own.
pub fn natural_content_width(
    columns: &[NaturalColumn],
    content_headroom: f64,
    cell_pad: f64,
) -> Option<f64> {
    let mut pinned_px = 0.0_f64;
    let mut percent = 0.0_f64;
    for column in columns {
        match column.pin {
            Some(Pin::Px(width)) => pinned_px += width,
            Some(Pin::Percent(fraction)) => percent += fraction,
            None => {}
        }
    }
    if percent >= 1.0 {
        return None;
    }
    let widths: Vec<f64> = natural_auto_layout(columns, content_headroom, cell_pad)
        .into_iter()
        .flatten()
        .map(|(width, _)| width)
        .collect();
    let rest = pinned_px + py_float_sum(&widths);
    if rest <= 0.0 {
        None
    } else {
        Some(rest / (1.0 - percent))
    }
}

/// Each auto column's natural width (`natural_auto_width`) and whether it is
/// compact, in column order; `None` for a pinned column.
pub fn natural_auto_layout(
    columns: &[NaturalColumn],
    content_headroom: f64,
    cell_pad: f64,
) -> Vec<Option<(f64, bool)>> {
    let auto: Vec<&NaturalColumn> = columns.iter().filter(|c| c.pin.is_none()).collect();
    let base: Vec<f64> = auto
        .iter()
        .map(|c| auto_column_demand(c.demand, c.word_floor, c.max_width))
        .collect();
    let is_text: Vec<bool> =
        auto.iter().zip(&base).map(|(c, &d)| !is_compact_column(d, c.spark)).collect();
    let text_columns_exist = is_text.iter().any(|&t| t);
    let widths: Vec<f64> = auto
        .iter()
        .zip(base.iter().zip(&is_text))
        .map(|(c, (&d, &text))| {
            natural_auto_width(
                d,
                text,
                text_columns_exist,
                c.header_demand,
                c.max_width,
                content_headroom,
                cell_pad,
            )
        })
        .collect();
    let mut auto_widths = widths.into_iter().zip(is_text);
    columns
        .iter()
        .map(|c| if c.pin.is_none() { auto_widths.next().map(|(w, text)| (w, !text)) } else { None })
        .collect()
}

/// The table width at which the row
/// label and the pager both fit on every page — the widest label (the widest
/// page window) plus the padding the label sits in. The renderer drops the
/// label when it would touch the right-anchored pager.
pub fn paginator_band_width(
    fonts: &FontBook,
    font_family: &str,
    font_size: i64,
    item_width: f64,
    total_rows: usize,
    page_rows: usize,
    padding: f64,
) -> Result<f64, String> {
    if page_rows == 0 {
        return Err("table: pagination.page_rows must be at least 1".into());
    }
    let total_pages = total_rows.div_ceil(page_rows) as i64;
    let label = format!("{total_rows}\u{2013}{total_rows} / {total_rows}");
    // `get_font_measurer(font_family)`: every non-serif family
    // measures as Inter, whatever the table paints in.
    let label_w =
        fonts.measure(&label, measure_family_stand_in(font_family), font_size as f64)?;
    let aux_slot_w = item_width * PAGINATOR_AUX_SLOT_RATIO;
    // Windows differ in their ellipses and in the digit width of their page
    // labels, and the widest is not always at either end, so every page's
    // window is measured.
    let mut slot_widths: HashMap<i64, f64> = HashMap::new();
    let mut pager_w = 0.0_f64;
    for page in 1..=total_pages {
        let mut slots = vec![aux_slot_w];
        for item in paginator_window(page, total_pages) {
            slots.push(match item {
                PageItem::Page(p) => match slot_widths.get(&p) {
                    Some(w) => *w,
                    None => {
                        let w = page_slot_width(fonts, font_family, font_size as f64, item_width, &p.to_string())?;
                        slot_widths.insert(p, w);
                        w
                    }
                },
                PageItem::Ellipsis => aux_slot_w,
            });
        }
        slots.push(aux_slot_w);
        pager_w = pager_w.max(py_float_sum(&slots));
    }
    Ok(padding + label_w + pager_w)
}

/// A column's `width:`/`max_width:` as `natural_content_width` reads them.
pub(super) fn natural_column(
    col: &super::ColMeta,
    inputs: &ColumnInputs,
) -> Result<NaturalColumn, String> {
    let width = col.cfg.width.as_ref();
    let pin = match parse_column_width(width, 1.0)? {
        Some(fraction) if is_percent(width) => Some(Pin::Percent(fraction)),
        Some(px) => Some(Pin::Px(px)),
        None => None,
    };
    let max_width = if is_percent(col.cfg.max_width.as_ref()) {
        None
    } else {
        parse_column_width(col.cfg.max_width.as_ref(), 0.0)?
    };
    let name = col.name;
    Ok(NaturalColumn {
        pin,
        max_width,
        spark: col.cfg.spark.is_some(),
        demand: inputs.demands[name],
        header_demand: inputs.header_demands[name],
        word_floor: inputs.word_floors[name],
    })
}

fn is_percent(hint: Option<&WidthHint>) -> bool {
    matches!(hint, Some(WidthHint::Text(s)) if s.trim().ends_with('%'))
}

/// `measure_table_natural_width`: the width the table needs
/// to show every column — columns and outer padding, before any card padding —
/// or `None` with nothing to hug. A paginated table is at least as wide as its
/// pager band, so the "a–b / N" label is never dropped.
///
pub fn measure_natural_width(
    payload: &MeasurePayload,
    fonts: &FontBook,
) -> Result<Option<f64>, String> {
    crate::number_locale::with_number_locale(&payload.format_locale, || {
        crate::time_locale::with_time_locale(&payload.time_locale, || {
            display_text::language::with_language(payload.language, || {
                natural_width_in_scope(payload, fonts)
            })
        })
    })
}

fn natural_width_in_scope(payload: &MeasurePayload, fonts: &FontBook) -> Result<Option<f64>, String> {
    let _formats = super::BoardFormats::enter(payload.formats.as_ref(), payload.sub_unit_specs.as_ref());
    let query_rows = &payload.rows;
    if let Some((resolved, rows)) =
        super::text_format::lower(&payload.resolved, query_rows)
    {
        let lowered = MeasurePayload {
            resolved,
            rows,
            fonts_dir: payload.fonts_dir.clone(),
            format_locale: payload.format_locale.clone(),
            time_locale: payload.time_locale.clone(),
            language: payload.language,
            formats: payload.formats.clone(),
            sub_unit_specs: payload.sub_unit_specs.clone(),
            cell_text: payload.cell_text.clone(),
        };
        return measure_natural_width(&lowered, fonts);
    }
    let r: Resolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("table payload: {e}"))?;
    let th = &r.style.table;
    if th.transpose && !query_rows.is_empty() {
        let (resolved, rows) =
            super::transposed_inputs(&payload.resolved, query_rows)
                .map_err(|d| d.to_string())?;
        let transposed = MeasurePayload {
            resolved,
            rows,
            fonts_dir: payload.fonts_dir.clone(),
            format_locale: payload.format_locale.clone(),
            time_locale: payload.time_locale.clone(),
            language: payload.language,
            formats: payload.formats.clone(),
            sub_unit_specs: payload.sub_unit_specs.clone(),
            cell_text: Vec::new(),
        };
        return measure_natural_width(&transposed, fonts);
    }
    let pivot = pivot_for(&r, query_rows, &payload.cell_text).map_err(|d| d.to_string())?;
    let rows = table_rows(query_rows, &pivot);
    let (cols, _, _) = visible_columns(&r, rows, &pivot).map_err(|e| e.to_string())?;

    // Integer sizes, SVG-safe families.
    let body_family = svg_font_family(
        th.font.family.as_deref().ok_or("table payload: style.table.font.family missing")?,
    );
    let header_family = svg_font_family(
        th.header
            .font
            .family
            .as_deref()
            .ok_or("table payload: style.table.header.font.family missing")?,
    );
    let font_size = th.font.size.ok_or("table payload: style.table.font.size missing")?.trunc();
    let header_size = th.header.font.size.map_or(font_size, f64::trunc);
    let cell_pad = th.column_layout.cell_padding.trunc();
    let outer_padding = th.outer_padding.trunc();

    let row_role = th.row.role.as_deref();
    let row_roles = resolve_row_roles(rows, row_role);
    let cell_data = build_cell_data(rows, &cols, &th.symbol_mode, &row_roles)
        .map_err(|e| e.to_string())?;
    let inputs = column_inputs(
        &cols,
        &cell_data,
        rows,
        fonts,
        &MeasureFonts {
            body: measure_family_stand_in(&body_family),
            header: measure_family_stand_in(&header_family),
        },
        font_size,
        header_size,
        th.header.visible,
        cell_pad,
    )?;
    let natural: Vec<NaturalColumn> =
        cols.iter().map(|c| natural_column(c, &inputs)).collect::<Result<_, _>>()?;
    let Some(content_width) =
        natural_content_width(&natural, th.column_layout.content_headroom, cell_pad)
    else {
        return Ok(None);
    };
    let row_number_width = if th.row_numbers.visible {
        super::row_number_column_width(
            &th.row_numbers,
            rows.len(),
            fonts,
            measure_family_stand_in(&body_family),
            measure_family_stand_in(&header_family),
            font_size,
            th.column_layout.cell_padding,
        )?
    } else {
        0.0
    };
    let columns_width = content_width + row_number_width + 2.0 * outer_padding;

    // Only the page size can paginate an unbounded table; a slot short enough
    // to paginate is not known here.
    let page_rows = if th.pagination.enabled && th.pagination.mode == super::PaginationMode::Pages {
        th.pagination.page_rows
    } else {
        None
    };
    match page_rows {
        Some(page_rows) if rows.len() > page_rows + PAGINATION_GROW_CAP => {
            // The size, else 11.
            let font_size = th.paginator.font.size.map_or(11, |s| s.trunc() as i64);
            let band = paginator_band_width(
                fonts,
                &body_family,
                font_size,
                th.paginator.item_width,
                rows.len(),
                page_rows,
                th.outer_padding,
            )?;
            Ok(Some(columns_width.max(band)))
        }
        _ => Ok(Some(columns_width)),
    }
}
