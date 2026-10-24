//! Histogram bind: what the rows and the authored style decide before any pixel is placed.
//! The bins and their stacks, each series' fill and tooltip text, every tick label and the
//! legend's entries. It measures nothing and holds no size but the slot height the payload
//! carries.

use serde::Deserialize;
use serde_json::Value;

use super::{histogram_bins_by, Bin, Resolved};
use crate::common::{axis_title_text, format_y_label, row_num, MirrorGhost};
use crate::diagnostic::Diagnostic;
use crate::families::cartesian::d3_nice_domain;
use crate::families::color_scale::ContinuousColor;
use crate::families::legend;
use crate::families::temporal::{infer_vega_type, VegaType};
use crate::{BoardStyle, Padding, Payload};

/// A bound histogram: the resolved chart as deserialized, with what the data derives from it.
pub struct Bound {
    pub(crate) r: Resolved,
    /// The card's chrome, which `ChartFrame` reads at layout.
    pub(crate) padding: Option<Padding>,
    pub(crate) board_style: Option<BoardStyle>,
    /// The height the payload's slot gives the chart. A histogram has no height of its own: it
    /// fills what sizing hands it.
    pub(crate) height: f64,
    /// `axis_y.position`: the count axis sits on the right edge.
    pub(crate) val_on_right: bool,
    /// `axis_y.mirror`: the far edge repeats the count labels.
    pub(crate) ghost: Option<MirrorGhost>,
    /// An own-side `labels.align` over an authored numeric domain: production bakes a measured
    /// `labelPadding`.
    pub(crate) own_side_measured: bool,
    /// Every bin, empty ones included: an empty bin draws nothing but still lies on the x scale.
    pub(crate) bins: Vec<BoundBin>,
    /// The x scale's domain, `NaN` for both when no row has a numeric x.
    pub(crate) x_extent: (f64, f64),
    /// Each bin edge, which the binned scale ticks, and its label.
    pub(crate) x_ticks: Vec<f64>,
    pub(crate) x_labels: Vec<String>,
    /// The tallest bin, or the group of rows with no numeric x if that is taller.
    pub(crate) max_count: f64,
    /// The count scale's top: `[0, nice(max_count)]`.
    pub(crate) y_domain_max: f64,
    /// The axis titles before they wrap.
    pub(crate) x_title: String,
    pub(crate) y_title: String,
    /// Whether a color channel stacks and paints the bins.
    pub(crate) colored: bool,
    pub(crate) legend: Option<LegendPaint>,
    pub(crate) legend_title: String,
    /// The id of a gradient legend's bar, namespaced to the chart.
    pub(crate) gradient_id: String,
}

/// One bin of the x scale with its stack.
pub(crate) struct BoundBin {
    pub(crate) start: f64,
    pub(crate) end: f64,
    pub(crate) count: usize,
    /// Bottom to top.
    pub(crate) segments: Vec<Segment>,
}

/// One run of a bin's rows in a stack.
pub(crate) struct Segment {
    pub(crate) n: usize,
    /// `None` for a series Vega-Lite's invalid filter drops: its rows stack but draw nothing.
    pub(crate) mark: Option<Mark>,
}

pub(crate) struct Mark {
    /// Vega-Lite's description of the bar: every channel as `title: value`.
    pub(crate) label: String,
    /// `None` is a null color's mark, which Vega leaves unfilled.
    pub(crate) fill: Option<String>,
    pub(crate) href: Option<String>,
}

pub fn bind(payload: &Payload) -> Result<Bound, Diagnostic> {
    let mut r: Resolved = Deserialize::deserialize(&payload.resolved)
        .map_err(|e| format!("histogram: payload.resolved: {e}"))?;
    r.series_palette = payload.series_palette(&r.palette);
    r.link_context = payload.link_context.clone();
    let ax_x = &r.style.axis_x;
    let ax_y = &r.style.axis_y;
    // `_emit_histogram` bakes `orient` from the resolved `axis_y.position`
    // via `measure_axis_to_vl` — this arm models
    // both left and right; any other position (the theme resolves an
    // unauthored one to the literal string `"left"`) would silently render
    // on the wrong side.
    let val_on_right = match ax_y.position.as_deref() {
        Some("left") => false,
        Some("right") => true,
        Some(other) => return Err(format!("histogram: axis_y.position {other:?} is neither left nor right").into()),
        None => return Err("histogram: axis_y.position absent (theme required)".into()),
    };
    // `axis_y.mirror: true` repeats the labels and ticks on the opposite edge at
    // the primary's own offset (`mirror_axis`), relabeled by the ghost's own `format`.
    let ghost = ax_y.mirror_ghost("histogram")?;
    // `measure_axis_to_vl` bakes a measured `labelPadding` only for an own-side
    // `labelAlign` on an axis whose tick labels it can reconstruct: no
    // `labelExpr` (an authored `labels.expr`), no upper/lower `font.case`, a
    // concrete `labels.format`, and, since histogram's `tick_values` and
    // `y_fields` are both empty, an authored numeric `scale.continuous.domain`
    // to estimate them from. Anything less falls back to Vega-Lite's un-flipped
    // default (`_fallback_reversed_label_align`).
    let own_side = if val_on_right { "right" } else { "left" };
    let own_side_measured = ax_y.labels.align.as_deref() == Some(own_side)
        && ax_y.labels.format.is_some()
        && ax_y.labels.expr.is_none()
        && !matches!(ax_y.labels.font.case.as_deref(), Some("upper") | Some("lower"))
        && ax_y.authored_continuous_domain().is_some();

    let binned_rows: Vec<&serde_json::Map<String, Value>> =
        payload.rows.iter().filter(|row| row_num(row, &r.x).is_some()).collect();
    let (stack_color, row_series) = match stack_color(&r, &payload.rows, &binned_rows)? {
        Some((color, row_series)) => (Some(color), row_series),
        None => (None, vec![Some(0); binned_rows.len()]),
    };
    let series_count = stack_color.as_ref().map_or(1, |c| c.series.len());
    let binned: Vec<(f64, Option<usize>)> = binned_rows
        .iter()
        .zip(&row_series)
        .map(|(row, series)| (row_num(row, &r.x).expect("filtered to numeric x"), *series))
        .collect();
    let link = r.link.as_deref().map(|template| crate::href::DatumLink {
        template,
        open: r.link_open,
        x: crate::href::LinkField::raw(&r.x),
        y: None,
        color: r.color.as_deref().map(|name| crate::href::LinkField {
            name,
            text: if stack_color.as_ref().is_some_and(|c| c.dated) { crate::href::LinkText::Date } else { crate::href::LinkText::Raw },
        }),
        context: r.link_context.as_ref(),
    });
    let row_hrefs: Vec<Option<String>> = match &link {
        Some(link) => binned_rows.iter().map(|row| link.href(row).map(Some)).collect::<Result<_, _>>()?,
        None => Vec::new(),
    };
    let bins = histogram_bins_by(&binned, series_count.max(1), &row_hrefs)?;
    // No numeric x leaves the x scale with a NaN domain: no bin, tick or label.
    let (bin_start, bin_stop) =
        bins.first().zip(bins.last()).map_or((f64::NAN, f64::NAN), |(first, last)| (first.start, last.end));
    if !bins.is_empty() && bin_stop <= bin_start {
        return Err("histogram: x domain is degenerate (max ≤ min)".into());
    }
    // Vega-Lite's invalid filter can leave no mark at all, which production
    // raises as a chart that painted nothing. A plain chart with no bin has
    // an empty mark group, which is no mark to measure; a color channel
    // splits it into per-series groups that each hold an empty mark.
    let paints_a_mark = bins.iter().any(|bin| {
        bin.counts.iter().enumerate().any(|(i, &n)| n > 0 && stack_color.as_ref().is_none_or(|c| c.series[i].drawn))
    });
    if !paints_a_mark && (!bins.is_empty() || stack_color.is_some()) {
        return Err(payload.painted_no_marks());
    }
    // Every row with no numeric x aggregates into a group of its own, whatever its
    // color: it draws no bar but still sets the count domain's top.
    let null_x_group = payload.rows.iter().filter(|row| row_num(row, &r.x).is_none()).count();
    let max_count = bins.iter().map(Bin::count).max().unwrap_or(0).max(null_x_group) as f64;

    // ── X: a binned scale ticks every bin edge. Vega's `tickCount` lifts any
    // count to `scale.bins.length` (`vega-scale` `tickCount`), so the
    // authored or themed `ticks.count` thins nothing.
    // With no bin there is no label, so the format a populated axis bakes may be absent.
    let x_slot = ax_x.labels.format.as_ref();
    let x_ticks: Vec<f64> =
        if bins.is_empty() { Vec::new() } else { bins.iter().map(|bin| bin.start).chain([bin_stop]).collect() };
    let x_labels: Vec<String> = if x_ticks.is_empty() {
        Vec::new()
    } else {
        let slot = x_slot.ok_or("histogram: axis_x.labels.format missing (theme required)")?;
        x_ticks
            .iter()
            .enumerate()
            .map(|(i, &tv)| {
                let label = format_y_label(tv, ax_x.ruler.as_ref(), ax_x.tick_label.as_ref(), Some(slot), i, x_ticks.len())
                    .map_err(|e| format!("histogram x label format: {e}"))?;
                ax_x.labels.apply_expr("x", tv, label, i, x_ticks.len())
            })
            .collect::<Result<_, _>>()?
    };

    // ── Y: never baked (a count aggregate has no static column); the ticks are
    // re-derived at the fitted extent at layout.
    let y_domain_max = d3_nice_domain(0.0, max_count, 10).1;

    let x_title = axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels)?;
    // `chart.y_label or COUNT_TITLE` — an authored
    // `y_label` (or an empty-string one, Python's `or` is falsy-checked)
    // overrides the literal "#" fallback every histogram's y aggregate
    // otherwise carries.
    let y_title = r
        .y_label
        .as_deref()
        .filter(|s| !s.is_empty())
        .map_or_else(|| "#".to_string(), crate::common::collapse_title_newlines);

    // A symbol legend's box does not depend on the plot; a gradient's follows
    // the plot's height and is laid out per fit pass.
    let legend_title = match r.color.as_deref() {
        Some(cf) if stack_color.is_some() => legend::legend_title_text(cf, &r.legend, &r.field_labels)?,
        _ => String::new(),
    };

    // Vega-Lite's default description of a bar: every encoded channel as
    // `title: value`, the bin as its formatted range, both through the axes'
    // own label formats.
    let y_fmt_spec = ax_y.labels.spec();
    let affixed_card = r.style.tooltip_format.has_affix();
    let mut bound_bins = Vec::with_capacity(bins.len());
    for bin in &bins {
        let mut segments = Vec::new();
        // An empty bin (count == 0) paints nothing — Vega's own `aggregate: count` groups the
        // binned field, so a bin no ROW ever lands in produces no group and no mark.
        if bin.count() > 0 {
            let fmt = |spec: &str, v: f64| {
                let plain_below_one = affixed_card
                    && v.abs() < 1.0
                    && d3rs::format::parse_parts(spec).is_ok_and(|p| p.type_ == 's');
                let spec = if plain_below_one { d3rs::format::sub_unit_specs(spec)?.0 } else { spec.to_string() };
                crate::number_locale::d3_format(&spec, v).map_err(|e| format!("histogram tooltip format: {e}"))
            };
            let x_spec = x_slot.expect("a populated axis has its label format (checked above)").spec.as_str();
            let span = format!("{} \u{2013} {}", fmt(x_spec, bin.start)?, fmt(x_spec, bin.end)?);
            // A link splits a series' rows into one segment per distinct link: a colored stack keeps
            // them in the order they appear, a colorless one stacks its links in descending order.
            let runs: Vec<(usize, usize, Option<&str>)> = bin
                .counts
                .iter()
                .enumerate()
                .filter(|(_, n)| **n > 0)
                .flat_map(|(series_idx, &n)| {
                    let groups = &bin.hrefs[series_idx];
                    if groups.is_empty() {
                        return vec![(series_idx, n, None)];
                    }
                    let mut groups: Vec<(usize, Option<&str>)> = groups.iter().map(|(href, n)| (*n, Some(href.as_str()))).collect();
                    if stack_color.is_none() {
                        groups.sort_by(|a, b| b.1.cmp(&a.1));
                    }
                    groups.into_iter().map(|(n, href)| (series_idx, n, href)).collect()
                })
                .collect();
            for (series_idx, n, href) in runs {
                // A count axis with no label format prints the bare number.
                let count = match y_fmt_spec {
                    _ if affixed_card => r.style.tooltip_format.paint(n as f64)?,
                    Some(spec) => fmt(spec, n as f64)?,
                    None => n.to_string(),
                };
                let series = stack_color.as_ref().map(|c| &c.series[series_idx]);
                if series.is_some_and(|s| !s.drawn) {
                    segments.push(Segment { n, mark: None });
                    continue;
                }
                let mut rows = vec![(x_title.as_str(), span.clone()), (y_title.as_str(), count)];
                if let (Some(color), Some(series)) = (&stack_color, series) {
                    rows.push((color.title.as_str(), series.text.clone()));
                }
                let fill = match series {
                    Some(series) => series.fill.clone(),
                    None => Some(r.style.single_series_fill.clone()),
                };
                let mark = Mark { label: crate::contract::native_label(&rows), fill, href: href.map(str::to_string) };
                segments.push(Segment { n, mark: Some(mark) });
            }
        }
        bound_bins.push(BoundBin { start: bin.start, end: bin.end, count: bin.count(), segments });
    }

    Ok(Bound {
        padding: payload.padding,
        board_style: payload.board_style.clone(),
        height: payload.height,
        val_on_right,
        ghost,
        own_side_measured,
        bins: bound_bins,
        x_extent: (bin_start, bin_stop),
        x_ticks,
        x_labels,
        max_count,
        y_domain_max,
        x_title,
        y_title,
        colored: stack_color.is_some(),
        legend: stack_color.map(|c| c.legend),
        legend_title,
        gradient_id: format!("hist_grad-{}", crate::contract::safe_svg_id(&r.id)),
        r,
    })
}

/// One stack segment's identity: how it paints and what its tooltip row reads.
pub(crate) struct Series {
    /// `None` for a null color, whose mark Vega leaves unfilled.
    fill: Option<String>,
    text: String,
    /// False for a null the invalid filter drops from the marks while its
    /// rows still stack, and so still count toward the bins' height.
    drawn: bool,
}

/// What a visible legend draws.
pub(crate) enum LegendPaint {
    /// One swatch per entry, `(label, fill)` in legend order.
    Symbols(Vec<(String, String)>),
    Gradient(ContinuousColor),
}

/// How a histogram's color channel stacks and paints each bin.
struct StackColor {
    /// The color field's title in a mark's description.
    title: String,
    /// Series in stack order, base first.
    series: Vec<Series>,
    legend: LegendPaint,
    /// The color field holds dates, which a link prints through `timeFormat`.
    dated: bool,
}

/// Production's `_emit_histogram` color encoding over `binned_rows` (every
/// row with a numeric x): the stack's series, base first, and each row's
/// series. A row with none is one Vega-Lite's invalid filter drops, which
/// leaves the quantitative and temporal scales' own nulls out of every bin.
///
/// The column types the way `infer_vega_type_from_data` reads it. A nominal
/// one ranks its series by row count and paints the palette in that order. A
/// numeric or date one is a continuous scale over its extent, one segment per
/// distinct value; an ordinal (date-like text) one a `blues` ramp over its
/// sorted values. Vega-Lite stacks those last three in descending value order.
fn stack_color(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
    binned_rows: &[&serde_json::Map<String, Value>],
) -> Result<Option<(StackColor, Vec<Option<usize>>)>, String> {
    let Some(cf) = r.color.as_deref() else { return Ok(None) };
    // `_flag_quantitative_color` stamps every resolved color channel, so a
    // color field without one is a payload no resolver produced.
    let channel = r
        .resolved_channels
        .get("color")
        .ok_or("histogram: a color field carries no resolved color channel (resolver required)")?;
    if !matches!(channel.mode.as_str(), "series" | "gradient") {
        return Err(format!(
            "histogram: a {} color channel is not a histogram payload (resolve produces only series and gradient)",
            channel.mode
        ));
    }
    let title = crate::families::legend::color_channel_title(cf, &r.legend, &r.field_labels)?;
    let continuous = |values: Vec<Option<f64>>,
                      scale: ContinuousColor,
                      text: &dyn Fn(f64) -> Result<String, String>|
     -> Result<(StackColor, Vec<Option<usize>>), String> {
        // A cell that reads as `NaN` is no value: it stacks with the nulls.
        let values: Vec<Option<f64>> = values.into_iter().map(|v| v.filter(|v| v.is_finite())).collect();
        let mut distinct: Vec<f64> = values.iter().flatten().copied().collect();
        distinct.sort_by(|a, b| b.total_cmp(a));
        distinct.dedup();
        let mut series = distinct
            .iter()
            .map(|&v| Ok(Series { fill: Some(scale.fill(v)), text: text(v)?, drawn: scale.paints() }))
            .collect::<Result<Vec<_>, String>>()?;
        // A null sorts below every value, so it stacks last and draws nothing.
        let null_at = series.len();
        if values.iter().any(Option::is_none) {
            series.push(Series { fill: None, text: "null".into(), drawn: false });
        }
        let row_series = values
            .iter()
            .map(|v| match v {
                Some(v) => Some(distinct.iter().position(|&d| d == *v).expect("every kept value is distinct")),
                None => Some(null_at),
            })
            .collect();
        Ok((StackColor { title: title.clone(), series, legend: LegendPaint::Gradient(scale), dated: false }, row_series))
    };
    // A numeric color's tooltip prints through the query's format when the
    // visible legend carries it as its own `format`, else a gradient scale's
    // value through d3's default and a plain series' as the bare number.
    let gradient_mode = channel.mode == "gradient";
    let quantitative_text = |v: f64| -> Result<String, String> {
        let spec = match r.color_format.as_ref().filter(|_| r.legend.visible).and_then(crate::families::color_scale::legend_format_spec) {
            Some(spec) => spec,
            None if gradient_mode => "",
            None => return Ok(crate::families::color_scale::js_number_string(v)),
        };
        crate::number_locale::d3_format(spec, v).map_err(|e| format!("histogram color tooltip format: {e}"))
    };
    let extent = |values: &[Option<f64>]| {
        values.iter().flatten().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| (lo.min(v), hi.max(v)))
    };
    let vega_type = if channel.mode == "gradient" { VegaType::Quantitative } else { infer_vega_type(rows, cf) };
    let color = match vega_type {
        VegaType::Quantitative if channel.mode == "gradient" => {
            let scale = channel.scale.as_ref().ok_or("histogram: a gradient color channel carries no scale (resolver required)")?;
            let values: Vec<Option<f64>> = binned_rows.iter().map(|row| row_num(row, cf)).collect();
            let all = extent(&rows.iter().filter_map(|row| row.get(cf)?.as_f64()).map(Some).collect::<Vec<_>>());
            let all = (all.0 <= all.1).then_some(all);
            continuous(values.clone(), ContinuousColor::gradient(scale, all, extent(&values))?, &quantitative_text)?
        }
        VegaType::Quantitative => {
            let values: Vec<Option<f64>> = binned_rows.iter().map(|row| row_num(row, cf)).collect();
            let (lo, hi) = extent(&values);
            continuous(values, ContinuousColor::blues(lo, hi), &quantitative_text)?
        }
        VegaType::Temporal => {
            let values = binned_rows
                .iter()
                .map(|row| match row.get(cf) {
                    None | Some(Value::Null) => None,
                    Some(cell) => crate::families::heatmap::date_cell_day(cell),
                })
                .collect::<Vec<Option<f64>>>();
            let (lo, hi) = extent(&values);
            let (dated, row_series) =
                continuous(values, ContinuousColor::dates(&d3rs::vega_schemes::BLUES, lo, hi), &crate::families::heatmap::legend_date_label)?;
            (StackColor { dated: true, ..dated }, row_series)
        }
        VegaType::Ordinal => ordinal_color(cf, title, &r.legend, binned_rows),
        VegaType::Nominal => nominal_color(r, cf, title, rows, binned_rows)?,
    };
    Ok(Some(color))
}

/// A nominal color: series ranked by row count, highest first, name breaking
/// ties, counted over every row with a color. The same order puts the first
/// series at each stack's base and hands out palette slots
/// (`spatial_color_scale` without a board scale, so `category_colors` never
/// applies). A null color is a series of its own at the base with no paint
/// (`series_order_expression` ranks it `-1`) that the legend leaves out.
fn nominal_color(
    r: &Resolved,
    cf: &str,
    title: String,
    rows: &[serde_json::Map<String, Value>],
    binned_rows: &[&serde_json::Map<String, Value>],
) -> Result<(StackColor, Vec<Option<usize>>), String> {
    let mut counts: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();
    for row in rows.iter().filter(|row| row.get(cf).is_some_and(|v| !v.is_null())) {
        *counts.entry(crate::common::row_str(row, cf)).or_default() += 1;
    }
    let mut ranked: Vec<(String, usize)> = counts.into_iter().collect();
    ranked.sort_by(|(a, na), (b, nb)| nb.cmp(na).then_with(|| a.cmp(b)));
    let has_null = binned_rows.iter().any(|row| row.get(cf).is_none_or(Value::is_null));
    let base = usize::from(has_null);
    // A chart with no palette of its own sets no color domain: Vega-Lite's default sorts it alphabetically,
    // where an authored palette's slots follow the ranked order.
    let mut color_domain: Vec<String> = ranked.iter().map(|(name, _)| name.clone()).collect();
    if r.palette.is_empty() {
        color_domain.sort_unstable();
    }
    let fills: Vec<String> = ranked
        .iter()
        .map(|(name, _)| crate::common::series_color(name, &color_domain, &r.series_palette, None))
        .collect::<Result<_, _>>()?;
    let mut series: Vec<Series> = Vec::new();
    if has_null {
        series.push(Series { fill: None, text: "null".into(), drawn: true });
    }
    series.extend(
        ranked.iter().zip(&fills).map(|((name, _), fill)| Series { fill: Some(fill.clone()), text: name.clone(), drawn: true }),
    );
    let row_series = binned_rows
        .iter()
        .map(|row| {
            if row.get(cf).is_none_or(Value::is_null) {
                return Some(0);
            }
            let value = crate::common::row_str(row, cf);
            Some(base + ranked.iter().position(|(name, _)| *name == value).expect("every colored row is counted"))
        })
        .collect();
    // The legend lists the series top of the stack first.
    let names: Vec<&str> = ranked.iter().rev().map(|(name, _)| name.as_str()).collect();
    let entries = legend_entries(&r.legend, &names, |name| {
        let rank = ranked.iter().position(|(n, _)| n == name).expect("a legend name is a series");
        fills[rank].clone()
    });
    Ok((StackColor { title, series, legend: LegendPaint::Symbols(entries), dated: false }, row_series))
}

/// An ordinal (date-like text) color: a `blues` ramp over the sorted values,
/// one segment per value, stacked descending. The ramp samples `blues` at
/// `(i + 1) / (n + 1)` for the `i`-th of `n` values, a null among them as the
/// lowest.
fn ordinal_color(
    cf: &str,
    title: String,
    legend: &crate::families::legend::LegendConfig,
    binned_rows: &[&serde_json::Map<String, Value>],
) -> (StackColor, Vec<Option<usize>>) {
    let value_of = |row: &serde_json::Map<String, Value>| {
        row.get(cf).filter(|v| !v.is_null()).map(|_| crate::common::row_str(row, cf))
    };
    let mut ascending: Vec<Option<String>> = binned_rows.iter().map(|row| value_of(row)).collect();
    ascending.sort();
    ascending.dedup();
    let fill_of = |i: usize| {
        d3rs::color::rgb_text(d3rs::color::quantize_sample(&d3rs::vega_schemes::BLUES, ascending.len(), i))
    };
    let text_of = |v: &Option<String>| v.clone().unwrap_or_else(|| "null".into());
    let series: Vec<Series> = ascending
        .iter()
        .enumerate()
        .rev()
        .map(|(i, v)| Series { fill: Some(fill_of(i)), text: text_of(v), drawn: true })
        .collect();
    let row_series = binned_rows
        .iter()
        .map(|row| {
            let value = value_of(row);
            ascending.iter().position(|v| *v == value).map(|i| ascending.len() - 1 - i)
        })
        .collect();
    let names: Vec<String> = ascending.iter().map(text_of).collect();
    let name_refs: Vec<&str> = names.iter().map(String::as_str).collect();
    let entries = legend_entries(legend, &name_refs, |name| {
        fill_of(names.iter().position(|v| v == name).expect("a legend name is a value"))
    });
    (StackColor { title, series, legend: LegendPaint::Symbols(entries), dated: false }, row_series)
}

/// The legend entries a symbol legend lists: `names` in display order, or the
/// authored `legend.values` resolved against them (an empty list draws a
/// title-only legend; one that matches nothing falls back to every name).
fn legend_entries(
    legend: &crate::families::legend::LegendConfig,
    names: &[&str],
    fill: impl Fn(&str) -> String,
) -> Vec<(String, String)> {
    let order: Vec<usize> = match &legend.values {
        Some(authored) if authored.is_empty() => Vec::new(),
        Some(authored) => crate::families::legend::resolve_legend_values(authored, names, None),
        None => (0..names.len()).collect(),
    };
    order.into_iter().map(|i| (names[i].to_string(), fill(names[i]))).collect()
}
