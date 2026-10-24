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

use super::bind::bind_chart;
use super::layout::auto_column_demand;
use super::layout::column_inputs;
use super::layout::is_compact_column;
use super::layout::natural_auto_width;
use super::layout::paginator_window;
use super::layout::parse_column_width;
use super::svg_font_family;
use super::Chart;
use super::ColumnInputs;
use super::MeasureFonts;
use super::PageItem;
use super::WidthHint;
use super::PAGINATION_GROW_CAP;
use super::PAGINATOR_AUX_SLOT_RATIO;
use crate::layout::IntrinsicWidth;
use d3rs::py_float::py_float_sum;
use measure::FontBook;
use measure::Measure;

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
        payload.cell_text =
            crate::common::decode_cells(&mut payload.rows).map_err(serde::de::Error::custom)?;
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
    hug_width(columns, |auto| {
        let base: Vec<f64> = auto
            .iter()
            .map(|c| auto_column_demand(c.demand, c.word_floor, c.max_width))
            .collect();
        let is_text: Vec<bool> = auto
            .iter()
            .zip(&base)
            .map(|(c, &d)| !is_compact_column(d, c.spark))
            .collect();
        let text_columns_exist = is_text.iter().any(|&t| t);
        auto.iter()
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
            .collect()
    })
}

/// The data-column budget below which a column would have to break a word: px
/// pins as written, each auto column at its word floor (capped by its
/// `max_width`; a spark column at its demand), `%` pins solving against the
/// rest. `None` where [`natural_content_width`] has nothing to hug.
fn minimum_content_width(columns: &[NaturalColumn]) -> Option<f64> {
    hug_width(columns, |auto| {
        auto.iter()
            .map(|c| {
                // The same 1px floor `auto_column_demand` gives the preferred
                // width, so a minimum exists wherever a preferred width does.
                if c.spark {
                    c.demand.max(1.0)
                } else {
                    auto_column_demand(0.0, c.word_floor, c.max_width)
                }
            })
            .collect()
    })
}

/// The one hug solve: px pins as written, the auto columns at the widths
/// `auto_widths` gives them, `%` pins solving against the rest
/// (`S / (1 - p)`). `None` when `%` pins total 100% or more, or no column
/// carries a width of its own.
fn hug_width(
    columns: &[NaturalColumn],
    auto_widths: impl FnOnce(&[&NaturalColumn]) -> Vec<f64>,
) -> Option<f64> {
    let mut pinned_px = 0.0_f64;
    let mut percent = 0.0_f64;
    let mut auto: Vec<&NaturalColumn> = Vec::new();
    for column in columns {
        match column.pin {
            Some(Pin::Px(width)) => pinned_px += width,
            Some(Pin::Percent(fraction)) => percent += fraction,
            None => auto.push(column),
        }
    }
    if percent >= 1.0 {
        return None;
    }
    let rest = pinned_px + py_float_sum(&auto_widths(&auto));
    (rest > 0.0).then(|| rest / (1.0 - percent))
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
    let label_w = fonts.measure(
        &label,
        measure::stand_in_family(font_family),
        font_size as f64,
    )?;
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
                        let w = Measure { fonts }.page_slot(
                            &p.to_string(),
                            measure::stand_in_family(font_family),
                            font_size as f64,
                            item_width,
                        )?;
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
fn natural_column(col: &super::ColMeta, inputs: &ColumnInputs) -> Result<NaturalColumn, String> {
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
    let name = col.name.as_str();
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
    Ok(measure_in_scope(payload, fonts)?.map(|width| width.preferred))
}

fn measure_in_scope(
    payload: &MeasurePayload,
    fonts: &FontBook,
) -> Result<Option<IntrinsicWidth>, String> {
    crate::number_locale::with_number_locale(&payload.format_locale, || {
        crate::time_locale::with_time_locale(&payload.time_locale, || {
            display_text::language::with_language(payload.language, || {
                natural_width_in_scope(payload, fonts)
            })
        })
    })
}

fn natural_width_in_scope(
    payload: &MeasurePayload,
    fonts: &FontBook,
) -> Result<Option<IntrinsicWidth>, String> {
    let chart = bind_chart(super::Source {
        resolved: &payload.resolved,
        rows: &payload.rows,
        cell_text: &payload.cell_text,
        formats: payload.formats.as_ref(),
        sub_unit_specs: payload.sub_unit_specs.as_ref(),
    })
    .map_err(|d| d.to_string())?;
    natural_width(&chart, fonts)
}

/// What a board can hug the table to: the narrowest it breaks no word at, and
/// the width at which every column shows (`None` with nothing to hug).
pub(super) fn natural_width(
    chart: &Chart,
    fonts: &FontBook,
) -> Result<Option<IntrinsicWidth>, String> {
    let _formats =
        super::BoardFormats::enter(chart.formats.as_ref(), chart.sub_unit_specs.as_ref());
    let th = &chart.resolved.style.table;
    let (rows, cols, cell_data) = (&chart.rows[..], &chart.cols, &chart.cell_data);
    // Integer sizes, SVG-safe families.
    let body_family = svg_font_family(
        th.font
            .family
            .as_deref()
            .ok_or("table payload: style.table.font.family missing")?,
    );
    let header_family = svg_font_family(
        th.header
            .font
            .family
            .as_deref()
            .ok_or("table payload: style.table.header.font.family missing")?,
    );
    let font_size = th
        .font
        .size
        .ok_or("table payload: style.table.font.size missing")?
        .trunc();
    let header_size = th.header.font.size.map_or(font_size, f64::trunc);
    let cell_pad = th.column_layout.cell_padding.trunc();
    let outer_padding = th.outer_padding.trunc();

    let inputs = column_inputs(
        cols,
        cell_data,
        rows,
        fonts,
        &MeasureFonts {
            body: measure::stand_in_family(&body_family),
            header: measure::stand_in_family(&header_family),
        },
        font_size,
        header_size,
        th.header.visible,
        cell_pad,
    )?;
    let natural: Vec<NaturalColumn> = cols
        .iter()
        .map(|c| natural_column(c, &inputs))
        .collect::<Result<_, _>>()?;
    let Some(content_width) =
        natural_content_width(&natural, th.column_layout.content_headroom, cell_pad)
    else {
        return Ok(None);
    };
    let min_content_width =
        minimum_content_width(&natural).ok_or("table: a width to hug has no minimum width")?;
    let row_number_width = if th.row_numbers.visible {
        Measure { fonts }.row_number_column(
            &th.row_numbers.header,
            rows.len(),
            measure::stand_in_family(&body_family),
            measure::stand_in_family(&header_family),
            font_size,
            th.column_layout.cell_padding,
        )?
    } else {
        0.0
    };
    let columns_width = content_width + row_number_width + 2.0 * outer_padding;
    let min_width = min_content_width + row_number_width + 2.0 * outer_padding;

    // Only the page size can paginate an unbounded table; a slot short enough
    // to paginate is not known here.
    let page_rows = if th.pagination.enabled {
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
            Ok(Some(IntrinsicWidth {
                min: min_width,
                preferred: columns_width.max(band),
            }))
        }
        _ => Ok(Some(IntrinsicWidth {
            min: min_width,
            preferred: columns_width,
        })),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn auto(demand: f64, word_floor: f64, max_width: Option<f64>, spark: bool) -> NaturalColumn {
        NaturalColumn {
            pin: None,
            max_width,
            spark,
            demand,
            header_demand: 0.0,
            word_floor,
        }
    }

    fn pinned(pin: Pin) -> NaturalColumn {
        NaturalColumn {
            pin: Some(pin),
            ..auto(0.0, 0.0, None, false)
        }
    }

    #[test]
    fn a_px_pin_counts_as_written() {
        assert_eq!(
            minimum_content_width(&[pinned(Pin::Px(100.0))]),
            Some(100.0)
        );
    }

    #[test]
    fn a_percent_pin_solves_against_the_rest() {
        let columns = [pinned(Pin::Px(100.0)), pinned(Pin::Percent(0.5))];
        assert_eq!(minimum_content_width(&columns), Some(200.0));
    }

    #[test]
    fn an_auto_column_counts_at_its_word_floor_not_its_demand() {
        assert_eq!(
            minimum_content_width(&[auto(200.0, 80.0, None, false)]),
            Some(80.0)
        );
    }

    #[test]
    fn a_max_width_caps_the_word_floor() {
        assert_eq!(
            minimum_content_width(&[auto(200.0, 80.0, Some(50.0), false)]),
            Some(50.0)
        );
    }

    #[test]
    fn a_spark_column_counts_at_its_demand() {
        assert_eq!(
            minimum_content_width(&[auto(60.0, 20.0, None, true)]),
            Some(60.0)
        );
    }

    #[test]
    fn the_columns_add_up() {
        let columns = [
            pinned(Pin::Px(100.0)),
            auto(200.0, 80.0, Some(50.0), false),
            auto(200.0, 80.0, None, false),
            auto(60.0, 20.0, None, true),
        ];
        assert_eq!(minimum_content_width(&columns), Some(290.0));
    }

    /// Whatever `natural_content_width` can hug, the minimum can too: an auto
    /// column with no word floor still takes the 1px `auto_column_demand` floors it at.
    #[test]
    fn a_floorless_auto_column_still_has_a_minimum() {
        let columns = [auto(0.0, 0.0, None, false)];
        assert!(natural_content_width(&columns, 0.0, 0.0).is_some());
        assert_eq!(minimum_content_width(&columns), Some(1.0));
    }
}
