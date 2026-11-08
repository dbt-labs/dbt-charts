//! What a bar is drawn as: rects and rounded paths, the layers' bars, the baseline rules and the column geometry.

use crate::common::{AxisStyle, FormatSlot, axis_title_text};
use ::bind::rows::{row_num, row_str};
use crate::contract::Reads;
use diagnostic::Diagnostic;
use crate::families::bar_hover_band::HoverBand;
use crate::families::cartesian::BandScale;
use crate::families::layers::{BarLayerMark, LayerClaim, LayerStack, OverlayBarLayer, OverlayLayer, layer_series_text, row_order_stack};
use ::bind::layers::{layer_color_of, layer_color_promoted, layer_color_text, layer_color_values};
use crate::families::scatter_axis::TemporalXAxis;
use crate::families::span::RangedBars;
use crate::families::{span, support_table_strip};
use crate::vega_fit;

use measure::FontBook;
use scene::{Attrs, Node, PathData, Role};
use serde_json::{Value, json};
use std::collections::HashMap;

use super::domain::row_keys;
use super::labels::{LabelPoint, LabelSide, LabelSpec, LabelStyle, label_font, label_need, label_position, label_spot, mark_labels, plain_label_place};
use super::time_x::TimeX;
use super::{BarResolved, MIN_COLUMN_WIDTH, Overlap};

/// The layers' rows of a ranged bar's combined block (`_span_values`): every layer but the
/// nested ranged bar, each under its own name, and the bar's own swatch. Swatched only when
/// every layer claims one paint, so a grouped layer leaves all unswatched.
pub(super) fn layer_block_rows(layers: &[(&OverlayLayer, &LayerClaim)], bar_fill: &str) -> (Vec<span::BlockRow>, Option<String>) {
    let inner = layers.iter().position(|(l, _)| matches!(l, OverlayLayer::Bar(b) if b.y_start.is_some()));
    let named: Vec<_> = layers.iter().enumerate().filter(|(i, _)| Some(*i) != inner).map(|(_, l)| l).collect();
    let swatched = !named.is_empty() && named.iter().all(|(_, claim)| claim.fill.is_some());
    // Rows key to the shared scale by title, so two layers named alike both read the last one's paint.
    let paint = |title: &str| layers.iter().rev().find(|(_, claim)| claim.label == title).and_then(|(_, claim)| claim.fill.clone());
    let rows = named
        .iter()
        .map(|(l, claim)| span::BlockRow { title: claim.label.clone(), field: l.y().to_string(), swatch: paint(&claim.label).filter(|_| swatched) })
        .collect();
    (rows, swatched.then(|| bar_fill.to_string()))
}

/// Every day number the unpinned layers plot on a date measure's shared axis, y and y_start.
pub(super) fn layer_day_numbers<'a>(layers: impl Iterator<Item = (&'a OverlayLayer, &'a [serde_json::Map<String, Value>])>) -> Vec<f64> {
    layers
        .filter(|(layer, _)| layer.axis_y().position.is_none())
        .flat_map(|(layer, rows)| {
            let start = match layer {
                OverlayLayer::Bar(bl) => bl.y_start.as_deref(),
                _ => None,
            };
            rows.iter().flat_map(move |row| [Some(layer.y()), start].into_iter().flatten().filter_map(|f| row_num(row, f)))
        })
        .collect()
}

/// One mark of a horizontal bar layer in value space: it spans `from..to` along the
/// measure axis; `series` is the layer's own color value, if it groups by color.
pub(super) struct LayerMark<'a> {
    pub(super) cat: String,
    pub(super) series: Option<String>,
    pub(super) value: f64,
    pub(super) from: f64,
    pub(super) to: f64,
    pub(super) row: &'a serde_json::Map<String, Value>,
    pub(super) datum: usize,
}

/// The description of a ranged bar layer's mark over a base with no block of its own
/// (`_layer_tooltip_description`): the layer named for its two ends, its start, end and
/// difference rows, and its color value when the color splits its rows.
pub(super) struct RangedLayerAria {
    pub(super) block: span::SpanBlock,
    pub(super) name: String,
    pub(super) promoted: bool,
}

impl RangedLayerAria {
    pub(super) fn new(
        bl: &OverlayBarLayer,
        start_field: &str,
        rows: &[serde_json::Map<String, Value>],
        field_labels: &crate::common::FieldLabels,
        date: bool,
    ) -> Result<Self, String> {
        let end_title = axis_title_text(&bl.y, bl.label.as_deref(), field_labels)?;
        let start_title = display_text::field_title(start_field, field_labels)?;
        let name = format!("{start_title} \u{2013} {end_title}");
        let ends = span::Ends { end: bl.y.clone(), end_title, start: start_field.to_string(), start_title };
        Ok(Self {
            block: span::SpanBlock::new(rows, &ends, Vec::new(), None, None, date),
            name,
            promoted: bl.color.as_deref().is_some_and(|cf| layer_color_promoted(rows, cf)),
        })
    }

    pub(super) fn label(
        &self,
        bl: &OverlayBarLayer,
        header: &str,
        row: &serde_json::Map<String, Value>,
        format: &FormatSlot,
    ) -> Result<String, String> {
        let color_value = bl.color.as_deref().filter(|_| self.promoted).map(|cf| layer_color_text(row, cf));
        let series = layer_series_text(&self.name, color_value.as_deref());
        self.block.layer_bar_label(header, Some(&series), row, format)
    }
}

/// A horizontal bar layer's marks in paint order. A colorless layer stacks rows
/// sharing a category in row order; one grouped by its own color stacks left to
/// right in domain order (the vertical arm's descending stack read along an axis
/// that runs the other way), on `LayerStack`'s one stack law.
pub(super) fn horizontal_bar_layer_marks<'a>(
    bl: &OverlayBarLayer,
    rows: &'a [serde_json::Map<String, Value>],
    x_field: &str,
    cat_domain: &[String],
    base_domain: &[String],
) -> Result<Vec<LayerMark<'a>>, String> {
    // A ranged layer spans its two columns, each row a mark of its own; a row missing either end draws none.
    if let Some(start) = bl.y_start.as_deref() {
        return Ok(rows
            .iter()
            .enumerate()
            .filter_map(|(datum, row)| {
                let (value, from) = (row_num(row, &bl.y)?, row_num(row, start)?);
                Some(LayerMark {
                    cat: row_str(row, x_field),
                    series: bl.color.as_deref().and_then(|color| layer_color_of(row, color)),
                    value,
                    from,
                    to: value,
                    row,
                    datum,
                })
            })
            .collect());
    }
    let Some(color_field) = bl.color.as_deref() else {
        // A row with no value draws no mark and takes no place in the stack.
        let valued = || rows.iter().enumerate().filter(|(_, row)| row_num(row, &bl.y).is_some());
        return Ok(valued()
            .zip(row_order_stack(valued().map(|(_, row)| row), x_field, &bl.y, &crate::families::layers::never_nan))
            .map(|((datum, row), (cat, from, to))| LayerMark {
                cat,
                series: None,
                value: row_num(row, &bl.y).expect("filtered to valued rows"),
                from,
                to,
                row,
                datum,
            })
            .collect());
    };
    let any_null = rows.iter().any(|row| layer_color_of(row, color_field).is_none());
    // A series the base's color scale has not seen joins it after the base's own values.
    let values = layer_color_values(rows, color_field);
    let (seen, unseen): (Vec<String>, Vec<String>) = values.into_iter().partition(|v| base_domain.contains(v));
    let seen_in_base_order = base_domain.iter().filter(|v| seen.contains(v)).cloned();
    let baseline_first: Vec<Option<String>> = any_null
        .then_some(None)
        .into_iter()
        .chain(seen_in_base_order.chain(unseen).map(Some))
        .collect();
    let spans = LayerStack::new(rows, x_field, color_field, &bl.y, &baseline_first).of_rows(rows);
    let mut rows_of: HashMap<(String, Option<String>), Vec<usize>> = HashMap::new();
    for (index, row) in rows.iter().enumerate() {
        rows_of.entry((row_str(row, x_field), layer_color_of(row, color_field))).or_default().push(index);
    }
    let mut marks = Vec::new();
    for cat in cat_domain {
        for series in &baseline_first {
            for &index in rows_of.get(&(cat.clone(), series.clone())).into_iter().flatten() {
                let Some((from, to)) = spans[index] else { continue };
                let row = &rows[index];
                let value = row_num(row, &bl.y).expect("a stacked row has its value");
                marks.push(LayerMark { cat: cat.clone(), series: series.clone(), value, from, to, row, datum: index });
            }
        }
    }
    Ok(marks)
}

/// A centered stack's non-`middle` value labels are text marks on Vega-Lite's
/// default `zero` stack (`_build_bar_text_layer`), not on the centered bars:
/// they sit at the zero stack's ends, and a negative running sum there joins
/// the shared value scale's extent.
pub(super) fn labels_on_zero_stack(r: &BarResolved) -> bool {
    r.stack == "center"
        && r.wide_measures.is_empty()
        && r.style.mark.labels.as_ref().is_some_and(|l| l.visible && label_position(l, true) != "middle")
}

/// `all_marks_degenerate` (`mark_extents.py`) over a bar chart's rects, and the
/// `ERR-CHART-PAINTED-NO-MARKS` production raises when some measure should have
/// painted (`rendering.py`; an all-zero chart is a faithful blank render). A
/// vertical bar is never thinner than a quarter pixel (Vega-Lite's
/// `max(0.25, bandwidth)`), so only a plot with no height flattens them; a
/// horizontal bar's length is its width, so only a plot with no width does.
/// Stroked layer marks keep a collapsed plot from reading as blank.
pub(super) fn collapsed_bars(
    horizontal: bool,
    (plot_w, plot_h): (f64, f64),
    stroked_layer_marks: bool,
    (chart_id, card_width): (&str, f64),
    rows: &[serde_json::Map<String, Value>],
    val_field: &str,
) -> Result<(), Diagnostic> {
    let flat = if horizontal { plot_w <= 0.0 } else { plot_h <= 0.0 };
    if flat && !stroked_layer_marks && rows.iter().any(|row| row_num(row, val_field).is_some_and(|v| v != 0.0)) {
        return Err(crate::painted_no_marks(card_width, json!(chart_id), rows.len()));
    }
    Ok(())
}

/// `marks.bar.max_size` caps a band's bar thickness (`cap_band_bar_size`):
/// `min(band * bandwidth, max_size)`. The authored ceiling also turns the
/// band-fraction shorthand into an explicit size, which Vega-Lite centers on
/// its band.
pub(super) fn capped_thickness(size: f64, max_size: Option<f64>) -> f64 {
    max_size.map_or(size, |max| size.min(max))
}

/// Where each category's support-table column stands at a plot `pw` wide: in the cell of the axis label naming its
/// period. A time axis reads it off its labels; a band axis whose labels center on a coarser period
/// (`period_centers`, each category's period center in band coordinates) centers it there; any other band label
/// stands on its bar.
pub(super) fn strip_columns(
    band: &BandScale,
    cat_domain: &[String],
    time: Option<(&TimeX, &TemporalXAxis)>,
    period_centers: Option<&[Option<f64>]>,
    pw: f64,
    fonts: &FontBook,
) -> Result<Vec<support_table_strip::Column>, String> {
    if let Some((t, axis)) = time {
        return t.strip_columns(axis, pw, fonts);
    }
    let mut columns = Vec::with_capacity(cat_domain.len());
    for (i, cat) in cat_domain.iter().enumerate() {
        let x = match period_centers.and_then(|centers| centers[i]) {
            Some(at) => super::axes::bucket_x(band, cat_domain, at).ok_or("bar: an interval label sits off the band")?,
            None => match band.position(cat) {
                Some(left) => left + band.bandwidth() / 2.0,
                None => continue,
            },
        };
        columns.push(support_table_strip::Column::centered(x));
    }
    Ok(columns)
}

/// Port of `bar_sort_op` (`core/utils.py`): `Sum` only for a stacked bar
/// sorted by its own measure; every other sort folds `Min`, the sort column's
/// own value. "Stacked" is the resolved `stack` (production's
/// `chart.stack not in (None, "none")`), not the presence of a color channel.
/// A strip cell is described like the bar over the same category: `x_title: raw
/// category`, plus `; Color: series` on a `per_series:` row.
pub(super) fn strip_cell_aria(
    r: &BarResolved,
    x_title: &str,
    cat_domain: &[String],
    time: Option<&TimeX>,
    color_field: Option<&str>,
) -> Result<support_table_strip::CellAria, String> {
    let x_text = |cat: &String| -> Result<String, String> {
        match time {
            Some(t) => t
                .date_of(cat)
                .ok_or_else(|| format!("unparsed date {cat:?}"))
                .and_then(|date| crate::families::temporal::default_time_unit_text(date, t.grain)),
            None => Ok(cat.clone()),
        }
    };
    Ok(support_table_strip::CellAria {
        x: cat_domain.iter().map(|c| Ok(format!("{x_title}: {}", x_text(c)?))).collect::<Result<_, String>>()?,
        color_title: color_field
            .map(|cf| crate::families::legend::color_channel_title(cf, &r.legend, &r.field_labels))
            .transpose()?,
    })
}

/// The baseline rules the value axis paints over the bars: the zero rule and
/// `_apply_unity`'s 100% rule, which share one stroke. Both fire only with a
/// visible grid AND a visible threshold. Production reaches that conjunction
/// two ways — the ordinary zero rule through `_insert_zero_rule`
/// (`threshold_style.visible`) plus `build_zero_rule_if_applicable(
/// grid_visible=...)`, and a normalize stack's 0%/100% rules through
/// `_insert_top_rules`, which gates them identically rather than exempting
/// them from `grid.visible`. A ranged bar (`ranged`: a `y_start`) has no zero
/// baseline unless its axis reaches zero anyway (`baseline.py:375`:
/// `should_fire = y_start is None or axis_style.zero_anchored`); the 100%
/// rule has no such gate.
#[derive(Clone, Copy)]
pub(super) struct BaselineRules {
    pub(super) width: f64,
    pub(super) zero: bool,
    pub(super) unity: bool,
}

/// A domain pinned away from 0 (an authored pair, a floor above it or a ceiling below it) leaves the
/// zero rule no position to paint at (`build_zero_rule_if_applicable`).
pub(super) fn domain_holds_zero(ax_y: &AxisStyle) -> bool {
    !ax_y.domain_min.is_some_and(|floor| floor > 0.0)
        && !ax_y.domain_max.is_some_and(|ceiling| ceiling < 0.0)
        && ax_y.authored_continuous_domain().is_none_or(|(a, b)| a.min(b) <= 0.0 && 0.0 <= a.max(b))
}

/// `BaselineFeature` adds no zero rule to a chart whose own query has no rows (`has_rows`).
pub(super) fn baseline_rules(ax_y: &AxisStyle, ranged: bool, unity: bool, has_rows: bool) -> Option<BaselineRules> {
    let width = ax_y
        .grid
        .threshold
        .as_ref()
        .filter(|_| ax_y.grid.visible.unwrap_or(false) && ax_y.grid.threshold_visible())
        .map(|z| z.width.unwrap_or(2.0))?;
    let zero = has_rows && (!ranged || ax_y.zero_anchored) && domain_holds_zero(ax_y);
    (zero || unity).then_some(BaselineRules { width, zero, unity })
}

/// Whether the measure axis carries `_apply_unity`'s 100% rule: a normalize
/// stack's definitional ceiling always does; every other bar only when the
/// axis is percent-formatted and its rendered domain reaches 1.0 over the
/// measure column's raw extent (`common::unity_rule_fires`, the one copy of
/// the gate).
pub(super) fn unity_rule(r: &BarResolved, rows: &[serde_json::Map<String, Value>]) -> bool {
    let (lo, hi) = rows
        .iter()
        .filter_map(|row| row_num(row, &r.y))
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)));
    r.stack == "normalize" || crate::common::unity_rule_fires(&r.style.axis_y, lo, hi)
}

/// The measure axis's baseline rules over the bars, one `.role-mark` group each, layers
/// `first_layer..`: the zero rule, then the 100% rule when `positions` carries it. `line(pos)`
/// builds the rule's `<line>` at `pos` with the attributes it is given. Every reference rule is hidden from screen
/// readers (`full_rule_at`'s `aria: false`): Vega-Lite would read the zero rule back as `NaN` and
/// a unity rule as a bare `1`.
pub(super) fn baseline_rule_layers(first_layer: usize, positions: &[f64], line: impl Fn(f64, Attrs) -> Node) -> Result<Vec<Node>, String> {
    positions
        .iter()
        .enumerate()
        .map(|(i, &pos)| {
            crate::contract::item_group(
                "rule",
                &format!("layer_{}_marks", first_layer + i),
                ::layout::contract::ItemRole::RuleMark,
                Reads::Hidden,
                |aria| line(pos, aria),
            )
        })
        .collect()
}

/// One bar-layer rect on a value scale, drawn by `bar_passes`: a ranged
/// layer spans its two columns, any other the zero rule (or the stack below) to
/// its value. Vega draws a rect past the plot unclipped, so an unclipped layer
/// keeps its ends on the plot (a zoomed floor puts the 0 baseline below it)
/// where a clipped one paints them where the scale puts them (square, the clip
/// dropping the radius) and the clip trims the rest. `at` is the rect's `(left, width)`; `layer` is
/// the layer's index among the chart's marks (0 is the base).
/// The stroke antialiases twice only under a corner radius, whose clipped group
/// repaints it; any other bar compiles to one plain path.
#[allow(clippy::too_many_arguments)]
pub(crate) fn layer_bar(
    layer: usize,
    position: &dyn Fn(f64) -> f64,
    plot_h: f64,
    clipped: bool,
    bl: &OverlayBarLayer,
    mark: &BarLayerMark,
    at: (f64, f64),
    fill: &str,
    aria: Option<&str>,
    link: Option<&crate::href::DatumLink>,
) -> Result<Vec<Node>, String> {
    let ceiling = if clipped { f64::INFINITY } else { plot_h };
    let (top, h, ends) = if bl.y_start.is_some() {
        ranged_extent(position, mark.to, Some(mark.from))
    } else {
        let (y1, y2) = (position(mark.from).min(ceiling), position(mark.to).min(ceiling));
        (y1.min(y2), (y1 - y2).abs(), Rounded::by_sign(mark.value))
    };
    let border = &bl.bar_mark.border;
    // Clipping a layer drops its corner radius (`_clip_layer_marks`).
    let radius = if clipped { 0.0 } else { border.radius.unwrap_or(0.0) };
    let href = link.map(|link| link.href(mark.row)).transpose()?;
    bar_passes(
        layer_mark(layer, bl, mark.row, mark.datum),
        false, at.0, top, at.1, h, fill, None, bl.bar_mark.opacity, border.color.as_deref().unwrap_or("none"), border.width.unwrap_or(0.0),
        radius, ends, radius > 0.0, None, aria, link.and_then(|link| link.open.anchor(href.as_deref())),
    )
}

/// A bar layer's own value labels, at each row's plain value (a bar layer never
/// stacks its labels), painted right after its bars. `centers` are the rects' x,
/// parallel to `marks`, and `aria` a datum's description.
#[allow(clippy::too_many_arguments)]
pub(crate) fn layer_bar_labels(
    bl: &OverlayBarLayer,
    rows: &[serde_json::Map<String, Value>],
    marks: &[BarLayerMark],
    centers: &[f64],
    position: &dyn Fn(f64) -> f64,
    plot_h: f64,
    aria: impl Fn(&BarLayerMark) -> Option<String>,
    link: Option<&crate::href::DatumLink>,
    (board_style, canvas): (Option<&crate::BoardStyle>, &str),
    (crowd, bar_widths): (crate::families::layers::LabelCrowd, &[f64]),
) -> Result<Vec<Node>, String> {
    let Some(ml) = bl.bar_mark.labels.as_ref().filter(|ml| ml.visible) else { return Ok(Vec::new()) };
    let spec = LabelSpec::new(ml, false, bl.label_is_house, rows, &bl.y);
    let font = label_font(ml, board_style)?;
    let points = marks
        .iter()
        .zip(centers)
        .map(|(mark, &center)| {
            let (extent, side) = layer_label_place(&spec, mark.value, position, plot_h, font.size);
            let (text, blank) = spec.text(Some(mark.row), mark.value, None)?;
            Ok(LabelPoint {
                center,
                extent,
                aria: aria(mark),
                series_fill: None,
                blank,
                side,
                span: None,
                text,
                href: link.map(|link| link.href(mark.row)).transpose()?,
                recede: None,
                ink: None,
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    let boxes = layer_bar_label_boxes(bl, rows, marks, centers, position, plot_h, crowd.fonts, board_style)?;
    crate::common::warn_value_labels_crowd(
        crowd.chart_id,
        Some(crowd.layer),
        boxes.into_iter().zip(bar_widths).map(|((ink, over_bar), &bar_width)| (ink, over_bar.then_some(bar_width))),
    );
    mark_labels(ml, false, false, true, &points, 0, board_style, 0.0, 0.0, canvas, link.map(|link| link.open).unwrap_or_default())
}

/// A bar layer's label anchor and side: `plain_label_place` on the layer's own
/// vertical scale, which `position` maps and `plot_h` bounds.
pub(super) fn layer_label_place(
    spec: &LabelSpec,
    value: f64,
    position: &dyn Fn(f64) -> f64,
    plot_h: f64,
    font_size: f64,
) -> (f64, Option<LabelSide>) {
    let at = spec.extent(value, position, plot_h);
    plain_label_place(&spec.position, value, at, position, (plot_h, 0.0), label_need(false, 0.0, font_size))
}

/// Each bar-layer value label's text box, where `layer_bar_labels` puts it (one per rect at
/// `centers`), and whether it is drawn over its own bar. The fit and the crowding check both
/// read these boxes.
#[allow(clippy::too_many_arguments)]
fn layer_bar_label_boxes(
    bl: &OverlayBarLayer,
    rows: &[serde_json::Map<String, Value>],
    marks: &[BarLayerMark],
    centers: &[f64],
    position: &dyn Fn(f64) -> f64,
    plot_h: f64,
    fonts: &FontBook,
    board_style: Option<&crate::BoardStyle>,
) -> Result<Vec<(vega_fit::Bounds, bool)>, String> {
    let Some(ml) = bl.bar_mark.labels.as_ref().filter(|ml| ml.visible) else { return Ok(Vec::new()) };
    let spec = LabelSpec::new(ml, false, bl.label_is_house, rows, &bl.y);
    let style = LabelStyle::new(ml, false, false, board_style)?;
    marks
        .iter()
        .zip(centers)
        .map(|(mark, &center)| {
            let (extent, side) = layer_label_place(&spec, mark.value, position, plot_h, style.font.size);
            let spot = label_spot(ml, &style.position, None, center, extent, None, side);
            let width = fonts.measure(&spec.text(Some(mark.row), mark.value, None)?.0, &style.font.family, style.font.size)?;
            let over_bar = style.inside_bar && !matches!(side, Some(LabelSide::Out(_)));
            Ok((vega_fit::text_ink(spot.x, spot.y, width, style.font.size, spot.align(), spot.baseline), over_bar))
        })
        .collect()
}

/// The fit ink of a bar layer's value labels, unioned into `bounds`.
#[allow(clippy::too_many_arguments)]
pub(crate) fn layer_bar_label_ink(
    bl: &OverlayBarLayer,
    rows: &[serde_json::Map<String, Value>],
    marks: &[BarLayerMark],
    centers: &[f64],
    position: &dyn Fn(f64) -> f64,
    plot_h: f64,
    fonts: &FontBook,
    board_style: Option<&crate::BoardStyle>,
    bounds: &mut vega_fit::Bounds,
) -> Result<(), String> {
    for (ink, _) in layer_bar_label_boxes(bl, rows, marks, centers, position, plot_h, fonts, board_style)? {
        bounds.union(&ink);
    }
    Ok(())
}

/// Each row's href by the `(category, series)` key its bar is drawn under;
/// empty on a chart with no `link`.
pub(super) fn link_hrefs(
    link: Option<&crate::href::DatumLink>,
    rows: &[serde_json::Map<String, Value>],
    (cat_field, color_field): (&str, Option<&str>),
    tiered: bool,
) -> Result<HashMap<(String, String), String>, String> {
    let Some(link) = link else { return Ok(HashMap::new()) };
    row_keys(rows, cat_field, color_field, tiered)
        .into_iter()
        .zip(rows)
        .map(|(key, row)| Ok((key, link.href(row)?)))
        .collect()
}

/// Each row's index by the `(category, series)` key its bar is drawn under, the first row for a key
/// several rows share.
pub(super) fn datum_by_key(
    rows: &[serde_json::Map<String, Value>],
    (cat_field, color_field): (&str, Option<&str>),
    tiered: bool,
) -> HashMap<(String, String), usize> {
    let mut first = HashMap::new();
    for (row, key) in row_keys(rows, cat_field, color_field, tiered).into_iter().enumerate() {
        first.entry(key).or_insert(row);
    }
    first
}

/// The role of a base-layer bar drawn under `key`: the series it names (none on a single series)
/// and the row it draws.
pub(super) fn base_mark(datum_of: &HashMap<(String, String), usize>, key: &(String, String)) -> Role {
    Role::Mark { layer: 0, series: Some(key.1.clone()).filter(|series| !series.is_empty()), datum: Some(*datum_of.get(key).expect("a drawn bar's key is one of the rows' keys")) }
}

/// The role of a bar layer's rect for the row at `datum` among the layer's rows; `layer` counts the
/// chart's marks (0 is the base).
pub(crate) fn layer_mark(layer: usize, bl: &OverlayBarLayer, row: &serde_json::Map<String, Value>, datum: usize) -> Role {
    Role::Mark { layer, series: bl.color.as_deref().map(|field| row_str(row, field)), datum: Some(datum) }
}

/// Which end of a bar takes the corner radius. A bar grows away from its
/// baseline, so its FAR end rounds: the positive end (top / right) of a
/// positive bar, the negative end (bottom / left) of a negative one. A
/// ranged bar (`y_start`) rounds both ends — both cap a value
/// (`vega-lite/src/compile/mark/encode/corner-radius.ts:27-34`) — unless it
/// falls: a ranged fall keeps explicit tip-pair radii (`bar_corner_props`,
/// `_layers.py::emit_bar_layer`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(super) enum Rounded {
    Positive,
    Negative,
    Both,
    /// A ranged fall: the tip pair only (bottom of a column, left of a bar),
    /// one plain stroked shape (explicit per-corner radii, no
    /// `cornerRadiusEnd` clip group).
    Fall,
}

impl Rounded {
    /// The far end of a bar whose value is `v` (zero counts as non-negative,
    /// as in `bar_data_signs`).
    pub(super) fn by_sign(v: f64) -> Self {
        if v < 0.0 {
            Self::Negative
        } else {
            Self::Positive
        }
    }
}

/// A column's `(top, height, rounded ends)` from its end value and, for a
/// ranged bar, its start: Vega's rect with `y` and `y2` swaps them into order
/// and takes the height between (`adjustSpatial`, `vega.js:35708-35740`), both
/// ends rounding for a rise and the bottom pair only for a fall. Any other bar
/// spans the zero rule to its value, whichever side of the rule that is,
/// rounding the end away from it. Vega's rect does not clip, so an end past
/// the plot edge (an authored domain narrower than the data) paints past it.
pub(super) fn ranged_extent(position: impl Fn(f64) -> f64, end: f64, start: Option<f64>) -> (f64, f64, Rounded) {
    let end_y = position(end);
    let (from_y, ends) = match start {
        None => (position(0.0), Rounded::by_sign(end)),
        Some(start) => (
            position(start),
            if end >= start { Rounded::Both } else { Rounded::Fall },
        ),
    };
    (end_y.min(from_y), (end_y - from_y).abs(), ends)
}

/// A ranged bar's paint order and layer count. With a corner radius configured
/// (`radius`), a chart holding both rises (`end >= start`) and falls splits into
/// a rise layer and then a fall layer so each rounds its own tip
/// (`_layers.py::emit_bar_layer`); otherwise it stays one layer in row order.
pub(super) fn direction_layers<T>(bars: Vec<T>, rises: impl Fn(&T) -> bool, radius: Option<f64>) -> (Vec<T>, usize) {
    let flags: Vec<bool> = bars.iter().map(rises).collect();
    if radius.is_none() || flags.iter().all(|&f| f) || !flags.iter().any(|&f| f) {
        return (bars, 1);
    }
    let (rising, falling): (Vec<_>, Vec<_>) = bars.into_iter().zip(flags).partition(|(_, rises)| *rises);
    (rising.into_iter().chain(falling).map(|(bar, _)| bar).collect(), 2)
}

/// A color-split bar's paint order and layer count: series by series with a
/// sign split, or — a ranged bar — row order with its rises before its falls
/// (`emit_bar_layer`'s sign split). Each bar is `(series index, series, category)`.
pub(super) fn painted_series_bars(
    color_domain: &[String],
    cat_domain: &[String],
    data_map: &HashMap<(String, String), f64>,
    ranged: Option<&RangedBars>,
    rows: &[serde_json::Map<String, Value>],
    (cat_field, color_field): (&str, &str),
    radius: Option<f64>,
    data_order: bool,
) -> (Vec<(usize, String, String)>, usize) {
    match ranged {
        None => {
            let series_major: Vec<(usize, String, String)> = color_domain
                .iter()
                .enumerate()
                .flat_map(|(si, series)| cat_domain.iter().map(move |cat| (si, series.clone(), cat.clone())))
                .collect();
            // Vega paints marks in data order, which only shows where bars overlap;
            // a pair with no row has no mark and keeps its place after the others.
            // A wide fold's rows are not in the order Vega-Lite's own fold emits,
            // so it keeps the series order.
            let mut first_row: HashMap<(String, String), usize> = HashMap::new();
            for (i, row) in rows.iter().enumerate() {
                first_row.entry((row_str(row, color_field), row_str(row, cat_field))).or_insert(i);
            }
            let row_index = |(_, series, cat): &(usize, String, String)| {
                first_row.get(&(series.clone(), cat.clone())).copied().unwrap_or(rows.len())
            };
            let mut series_major = series_major;
            if data_order {
                series_major.sort_by_key(row_index);
            }
            sign_layers(
                series_major,
                |(_, series, cat)| data_map.get(&(cat.clone(), series.clone())).copied().unwrap_or(0.0),
                radius,
            )
        }
        Some(rb) => {
            let in_row_order = rows
                .iter()
                .map(|row| {
                    let series = row_str(row, color_field);
                    let si = color_domain.iter().position(|s| *s == series).expect("the domain holds every row's series");
                    (si, series, row_str(row, cat_field))
                })
                .collect();
            let rises = |(_, series, cat): &(usize, String, String)| {
                let key = (cat.clone(), series.clone());
                rb.starts.get(&key).zip(data_map.get(&key)).is_some_and(|(start, end)| end >= start)
            };
            direction_layers(in_row_order, rises, radius)
        }
    }
}

/// A bar chart's paint order and layer count when its measure holds both
/// signs: with a corner radius configured, production splits each bar into a
/// `>= 0` sub-layer and then a `< 0` one so each rounds its own tip
/// (`_layers.py::emit_bar_layer`), and only a positive and a negative value
/// together trigger it (`bar_data_signs`; a zero never does).
pub(super) fn sign_layers<T>(bars: Vec<T>, value: impl Fn(&T) -> f64, radius: Option<f64>) -> (Vec<T>, usize) {
    let mixed = bars.iter().any(|b| value(b) > 0.0) && bars.iter().any(|b| value(b) < 0.0);
    direction_layers(bars, |b| value(b) >= 0.0, radius.filter(|_| mixed))
}

/// A rectangle with all four corners rounded — a ranged bar's outline, for
/// either orientation.
pub(super) fn all_corners_path(x: f64, y: f64, w: f64, h: f64, r: f64) -> PathData {
    let (x2, y2) = (x + w, y + h);
    PathData::new()
        .move_to(x + r, y)
        .line_to(x2 - r, y)
        .arc_to(r, true, x2, y + r)
        .line_to(x2, y2 - r)
        .arc_to(r, true, x2 - r, y2)
        .line_to(x + r, y2)
        .arc_to(r, true, x, y2 - r)
        .line_to(x, y + r)
        .arc_to(r, true, x + r, y)
        .close()
}

/// The panel's `BarHoverBandFeature` layer, `None` where production declares
/// none: a facet panel, a layered, wide-measure or histogram bar, a `normalize`
/// stack, or a stack with no series to carry the band.
pub(super) fn hover_band_for(r: &BarResolved, rows: &[serde_json::Map<String, Value>], in_facet: bool) -> Option<HoverBand> {
    if in_facet || !r.layers.is_empty() || !r.wide_measures.is_empty() {
        return None;
    }
    match (r.stack.as_str(), r.color.as_deref()) {
        ("none", _) => HoverBand::plain(rows, &r.y),
        ("zero", Some(series)) => HoverBand::stacked(rows, &r.x, &r.y, series, r.stacked_domain_max),
        _ => None,
    }
}

/// A bar as nodes: a `<path>` with rounded corners at its FAR end (production's `cornerRadiusEnd`
/// on a positive bar, the opposite pair on a negative one, which `ends` selects), or a `<rect>`
/// when the radius is zero. `horizontal` bars grow rightward from the zero rule, columns upward;
/// the near end, against the zero rule, is always square.
///
/// A stroked bar paints its stroke TWICE when `double_stroke` — a fill+stroke pass, then the same
/// outline stroke-only. Production's compiled bar is a clipped rounded group
/// whose inner rect carries a stroke (clipped to the shape) AND whose
/// foreground paints the rounded outline again, so edge pixels get two
/// antialias contributions; a single stroke leaves every bar edge one
/// blend-level short of the reference raster (bc0: 1.20% → 0.37% diff).
/// That group is `cornerRadiusEnd`'s; a ranged bar's `Rounded::Both` is VL's
/// plain `cornerRadius`, one stroked shape, and painting it twice darkens a
/// thin dumbbell stem's white outline past the reference (0.51% → 0.02%). A
/// `Rounded::Fall` (a ranged bar running left or down) is the tip pair with explicit
/// corner radii, also one stroked shape. The continuous-x bar compiles to one plain fill+stroke
/// path, so it passes `double_stroke: false`: a second pass over-brightens every edge at the
/// fractional positions a linear scale produces.
///
/// A patterned bar strokes once: the second pass only matches a reference raster solid bars have, and
/// as a node of its own it would stay bright when the hover recedes the bar.
///
/// `ink`, a patterned bar's outline color, rides on the filled shape as `data-dbt-ink`: the hover
/// runtime reads it for the tooltip key and rings, since a stacked segment's own `stroke` is the
/// gap between neighbors.
///
/// `aria_label`, when `Some`, wraps the datum in Vega's own `.role-mark` group and stamps the
/// label on the FIRST drawn element only — the second (stroke-only) pass is a pure antialiasing
/// overlay of the same datum, never a second mark.
#[allow(clippy::too_many_arguments)]
pub(super) fn bar_passes(
    mark: Role,
    horizontal: bool,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    fill: &str,
    ink: Option<&str>,
    fill_opacity: Option<f64>,
    stroke: &str,
    stroke_w: f64,
    r: f64,
    ends: Rounded,
    double_stroke: bool,
    clip: Option<&str>,
    aria_label: Option<&str>,
    href: Option<crate::href::Anchor<'_>>,
) -> Result<Vec<Node>, String> {
    let r = if horizontal {
        crate::common::bar_corner_radius(r, h, w)
    } else {
        crate::common::bar_corner_radius(r, w, h)
    };
    let stroked = stroke_w > 0.0 && stroke != "none" && !stroke.is_empty();
    // The second stroke pass sits outside the datum's `<a>`, so `linked` never reaches it.
    let overlay = |attrs: Attrs| match href.and_then(|anchor| anchor.recede) {
        Some(recede) => recede.recede_attrs(attrs),
        None => attrs,
    };
    let stroke_attrs = |attrs: Attrs| {
        if stroked { attrs.set("stroke", stroke).num("stroke-width", stroke_w) } else { attrs }
    };
    // `bar_passes`'s doc: only `cornerRadiusEnd`'s clipped group strokes twice.
    let fill_attrs = |attrs: Attrs| attrs.set("fill", fill).num_some("fill-opacity", fill_opacity).quick_some("data-dbt-ink", ink);
    let stroke_repeat = double_stroke && stroked && ink.is_none() && !matches!(ends, Rounded::Both | Rounded::Fall);
    let label_attrs = ::layout::contract::aria_attrs(aria_label, ::layout::contract::ItemRole::Bar)?;
    let grouped = |nodes: Vec<Node>| -> Result<Vec<Node>, String> {
        match aria_label {
            Some(_) => Ok(vec![::layout::contract::mark_group("rect", "layer_0_marks", nodes)?]),
            None => Ok(nodes),
        }
    };
    if r <= 0.0 {
        // A zero-width rect paints nothing; Vega's path of the same box still
        // strokes its edge (a step layer over grouped bars zeroes the columns).
        let shape = |role: Role, attrs: Attrs| {
            if w == 0.0 && !horizontal {
                let edge = PathData::new().move_to(x, y).line_to(x, y).line_to(x, y + h).line_to(x, y + h).close();
                Node::path(role, edge, attrs)
            } else {
                Node::rect(role, (x, y, w, h), attrs)
            }
        };
        let mut nodes = vec![crate::href::linked(href, shape(mark, stroke_attrs(fill_attrs(Attrs::new())).extend(label_attrs)))];
        if stroke_repeat {
        nodes.push(shape(Role::Chrome, overlay(stroke_attrs(Attrs::new().set("fill", "none")))));
        }
        return grouped(nodes);
    }
    let x2 = x + w;
    let y2 = y + h; // bottom (baseline)
    // Stroke painted twice — see bar_passes's doc comment.
    let d = if ends == Rounded::Both {
        all_corners_path(x, y, w, h, r)
    } else if horizontal {
        if ends == Rounded::Positive {
            PathData::new()
                .move_to(x, y)
                .line_to(x2 - r, y)
                .arc_to(r, true, x2, y + r)
                .line_to(x2, y2 - r)
                .arc_to(r, true, x2 - r, y2)
                .line_to(x, y2)
                .close()
        } else {
            PathData::new()
                .move_to(x, y + r)
                .arc_to(r, true, x + r, y)
                .line_to(x2, y)
                .line_to(x2, y2)
                .line_to(x + r, y2)
                .arc_to(r, true, x, y2 - r)
                .close()
        }
    } else if matches!(ends, Rounded::Negative | Rounded::Fall) {
        // Rounded bottom corners; square top. Path: start at top-left, down
        // the left edge, arc bottom-left, cross bottom, arc bottom-right,
        // up the right edge, close at top-right. The vertical mirror of the
        // round-top path below flips the arc sweep flag (0, not 1) — a
        // convex rounded corner needs the opposite winding once the near/far
        // ends swap (probed: `1 0 0,1` renders a REFLEX corner when
        // mirrored this way).
        PathData::new()
            .move_to(x, y)
            .line_to(x, y2 - r)
            .arc_to(r, false, x + r, y2)
            .line_to(x2 - r, y2)
            .arc_to(r, false, x2, y2 - r)
            .line_to(x2, y)
            .close()
    } else {
        // Rounded top corners; square bottom corners.
        // Path: start at bottom-left, go up left edge, arc top-left, cross top, arc top-right,
        // go down right edge, close at bottom-right.
        PathData::new()
            .move_to(x, y2)
            .line_to(x, y + r)
            .arc_to(r, true, x + r, y)
            .line_to(x2 - r, y)
            .arc_to(r, true, x2, y + r)
            .line_to(x2, y2)
            .close()
    };
    // The compiled group clips the rect's own stroke to the rounded shape and strokes the outline on top, so
    // an overlapping neighbor's edge is crossed by the outline alone.
    let clip = clip.filter(|_| stroke_repeat);
    let filled = stroke_attrs(fill_attrs(Attrs::new()));
    let filled = match clip {
        Some(id) => filled.set("clip-path", format!("url(#{id})")),
        None => filled,
    };
    let mut nodes = vec![crate::href::linked(href, Node::path(mark, d.clone(), filled.extend(label_attrs)))];
    if stroke_repeat {
        if let Some(id) = clip {
            nodes.push(Node::new(
                Role::Chrome,
                Attrs::new().set("id", id),
                scene::Shape::ClipPath(vec![Node::path(Role::Chrome, d.clone(), Attrs::new())]),
            ));
        }
        nodes.push(Node::path(Role::Chrome, d, overlay(stroke_attrs(Attrs::new().set("fill", "none")))));
    }
    grouped(nodes)
}

/// The keyword table of `_resolve_overlap_fraction`: `auto` (or no overlap)
/// is the series-count-dependent `auto`, keywords map through the table and a
/// number passes through.
pub(super) fn overlap_fraction(overlap: Option<&Overlap>, auto: f64) -> Result<f64, String> {
    Ok(match overlap {
        None => auto,
        Some(Overlap::Fraction(f)) => *f,
        Some(Overlap::Keyword(k)) => match k.as_str() {
            "auto" => auto,
            "none" => -0.1,
            "flush" => 0.0,
            "partial" => 0.25,
            "full" => 1.0,
            other => return Err(format!("bar: style.overlap {other:?} not a known keyword")),
        },
    })
}

/// Production's grouped-bar overlap fraction (`_resolve_overlap_fraction`):
/// unauthored (or `auto`) is −0.1 — a gap — for any series count but
/// two, and +0.25 for exactly two; keywords map through its table and a
/// number passes through. A positive overlap off two series and a full overlap
/// are raised ahead of `render` by `production_errors`.
pub(super) fn grouped_overlap_fraction(overlap: Option<&Overlap>, n_series: usize) -> Result<f64, String> {
    overlap_fraction(overlap, if n_series == 2 { 0.25 } else { -0.1 })
}

/// A color split that lays a band's columns side by side: `width_steps` is
/// each column's width in sub-steps (`1 + overlap`, floored), and `layered`
/// whether the chart is a layered spec.
pub(super) struct ColumnSplit {
    pub(super) n_series: usize,
    /// Sub-bands ahead of the first series: the strip cells' missing series value.
    pub(super) lead: usize,
    pub(super) width_steps: f64,
    pub(super) layered: bool,
    /// Patterned columns on the chart's own ground close the gaps between them: they share one ink edge.
    pub(super) touching: bool,
}

/// The width of each of `n` bars that touch and fill the extent of `n` bars `width` wide, a `step`
/// apart: the same footprint with the gaps taken up by the bars.
pub(super) fn touching_width(n: usize, step: f64, width: f64) -> f64 {
    ((n as f64 - 1.0) * step + width) / n as f64
}

/// Where a vertical bar chart's columns sit inside one category band. An
/// unsplit band holds one column of `mark.band_width` of the band; a
/// [`ColumnSplit`] band holds one per series, a sub-step apart.
#[derive(Clone)]
pub(super) struct BandColumns {
    /// Distance between a split band's columns; the band itself unsplit.
    pub(super) step: f64,
    /// `ColumnSplit::lead`: the sub-band the first series sits after.
    pub(super) lead: usize,
    pub(super) width: f64,
    /// The first column's left edge from the band's left edge.
    pub(super) pad: f64,
    /// Where a value label centers, from the band's left edge on an unsplit band: the band's middle, except
    /// on a time axis, where Vega-Lite's text mark sits at the bucket's start or the instant itself.
    pub(super) anchor: f64,
    pub(super) split: bool,
    /// Every series' column sits on the same extent, though each value label keeps its series' sub-band.
    pub(super) coincident: bool,
}

impl BandColumns {
    pub(super) fn new(band_w: f64, mark_band_width: f64, max_size: Option<f64>, split: Option<&ColumnSplit>) -> Self {
        let Some(split) = split else {
            let width = capped_thickness((band_w * mark_band_width).min(band_w), max_size);
            return Self { step: band_w, lead: 0, width, pad: (band_w - width) / 2.0, anchor: band_w / 2.0, split: false, coincident: false };
        };
        let step = support_table_strip::sub_band_step(band_w, split.n_series + split.lead, false);
        // `_fix_bar_band_width` (`_overlay.py:778`) runs ONLY inside a
        // LAYERED spec: it rewrites the base bar's `{"band": f}` width
        // shorthand to an explicit `f * bandwidth('xOffset')`, so both the
        // mark width AND its centering offset resolve against the OFFSET
        // scale's own bandwidth (`step`, since `xOffset.paddingInner` is
        // hardcoded 0) there — a non-layered grouped bar keeps VL's
        // un-rewritten shorthand, which resolves against the OUTER category
        // bandwidth instead (probed: `two_series_take_the_partial_overlap`,
        // no layer, vs. `grouped-bar-with-layer`'s reference Jan/Enterprise
        // bar at x=26.671875 — `(1-1.25)/2 * step(71.125)`, not `* band_w`).
        let pad_band_w = if split.layered { step } else { band_w };
        let width = capped_thickness(split.width_steps * step, max_size);
        // An authored ceiling makes the width explicit, so it centers on the
        // sub-band instead of resolving the shorthand against the outer band.
        let pad = if max_size.is_some() { (step - width) / 2.0 } else { (1.0 - split.width_steps) / 2.0 * pad_band_w };
        Self { step, lead: split.lead, width, pad, anchor: step / 2.0, split: true, coincident: false }.touched(split)
    }

    /// The grouped columns of a time axis under value labels: Vega-Lite sizes them against the offset scale
    /// and leaves each at its sub-band's own leading edge.
    pub(super) fn left_aligned(band_w: f64, split: &ColumnSplit) -> Self {
        let step = support_table_strip::sub_band_step(band_w, split.n_series + split.lead, false);
        let width = (split.width_steps * step).max(MIN_COLUMN_WIDTH);
        Self { step, lead: split.lead, width, pad: 0.0, anchor: step / 2.0, split: true, coincident: false }.touched(split)
    }

    /// The grouped columns of a point-dated time axis under value labels: every series' column is `width` wide
    /// at the band's center, and each value label sits in its series' sub-band.
    pub(super) fn centered_in_band(band_w: f64, width: f64, split: &ColumnSplit) -> Self {
        let step = support_table_strip::sub_band_step(band_w, split.n_series + split.lead, false);
        Self { step, lead: split.lead, width, pad: (band_w - width) / 2.0, anchor: step / 2.0, split: true, coincident: true }
    }

    /// The same columns of a date with no band: on one extent at the plot's left edge, the label there too.
    pub(super) fn unplaced(&self) -> Self {
        Self { step: 0.0, pad: 0.0, anchor: 0.0, coincident: false, ..self.clone() }
    }

    /// A time-axis column `width` wide, `pad` in from its bucket's leading edge, its value label `anchor` in.
    pub(super) fn unsplit(span: f64, pad: f64, width: f64, anchor: f64) -> Self {
        Self { step: span, lead: 0, width, pad, anchor, split: false, coincident: false }
    }

    /// A time-axis column of an authored `size`, centered on its `span`.
    pub(super) fn centered(span: f64, size: f64) -> Self {
        Self::unsplit(span, (span - size) / 2.0, size, 0.0)
    }

    /// The grouped columns of an instant: every series on the same extent.
    pub(super) fn coincident(span: f64) -> Self {
        Self { step: 0.0, lead: 0, width: span, pad: 0.0, anchor: 0.0, split: true, coincident: false }
    }

    /// The grouped columns of a time bucket: Vega-Lite divides `0.8` of the grain's `reference` bucket
    /// among the series, ignoring the mark's `band_width`, and widens each by the overlap.
    pub(super) fn offset_bucket(reference: f64, split: &ColumnSplit) -> Self {
        let step = 0.8 * reference / (split.n_series + split.lead) as f64;
        let width = split.width_steps * step;
        Self { step, lead: split.lead, width, pad: 0.1 * reference + (step - width) / 2.0, anchor: step / 2.0, split: true, coincident: false }.touched(split)
    }

    /// The columns of a grouped base whose xOffset a step layer turned into a
    /// point scale over `n_series` series and the layer's two edges: zero-width
    /// columns on the first slots, `band_w / (n_series + 1)` apart.
    pub(super) fn point_slots(band_w: f64, n_series: usize) -> Self {
        let step = band_w / (n_series as f64 + 1.0);
        Self { step, lead: 0, width: 0.0, pad: 0.0, anchor: step / 2.0, split: true, coincident: false }
    }

    /// These columns closed up to touch: each as wide as the spacing, the group keeping its footprint
    /// and its first column's edge. A split with overlap or no gap to close is left as it is.
    fn touched(self, split: &ColumnSplit) -> Self {
        if !split.touching || split.lead != 0 || self.width >= self.step {
            return self;
        }
        let width = touching_width(split.n_series, self.step, self.width);
        Self { step: width, width, anchor: self.pad + width / 2.0, ..self }
    }

    /// Where a value label of series `slot` centers.
    pub(super) fn label_center(&self, band_x: f64, slot: usize) -> f64 {
        self.sub_start(band_x, slot) + self.anchor
    }

    /// Left edge of the band's column at series `slot` (ignored unsplit).
    pub(super) fn sub_start(&self, band_x: f64, slot: usize) -> f64 {
        band_x + if self.split { support_table_strip::sub_band_start(self.step, slot + self.lead, false) } else { 0.0 }
    }

    pub(super) fn left(&self, band_x: f64, slot: usize) -> f64 {
        if self.coincident {
            band_x + self.pad
        } else {
            self.sub_start(band_x, slot) + self.pad
        }
    }
}

/// The columns of every band of a chart: one law across a band scale, each bucket's own on a time axis.
pub(super) enum Columns {
    Banded(BandColumns),
    Timed(HashMap<String, BandColumns>),
}

impl Columns {
    pub(super) fn of(&self, cat: &str) -> &BandColumns {
        match self {
            Self::Banded(columns) => columns,
            Self::Timed(by_cat) => by_cat.get(cat).expect("a time axis lays out every category it plots"),
        }
    }

    /// `[left, right]` of the drawn bars across `band`, given the first and
    /// last drawn `(category index, series slot)`. A later slot never ends
    /// before an earlier one, so these two bound every column.
    pub(super) fn x_extent(
        &self,
        band: &BandScale,
        cats: &[String],
        (first, last): ((usize, usize), (usize, usize)),
    ) -> (f64, f64) {
        let edge = |(ci, slot): (usize, usize)| {
            let band_x = band.position(&cats[ci]).expect("a drawn category is in its own band scale's domain");
            let columns = self.of(&cats[ci]);
            (columns.left(band_x, slot), columns.width)
        };
        (edge(first).0, edge(last).0 + edge(last).1)
    }
}
