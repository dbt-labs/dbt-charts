//! Value labels: where they sit, the ink they take and whether they fit.

use crate::common::{AxisStyle, MarkLabels, VALUE_AXIS_BREATHING_ROOM, format_mark_label, row_num, row_str};
use crate::constants::bar::LABEL_FIT_LINE_HEIGHT_MULTIPLIER;
use crate::families::cartesian::{BandScale, LinearScale};
use crate::families::span::RangedBars;
use crate::vega_fit;

use measure::FontBook;
use scene::{Attrs, Node};
use serde_json::Value;
use std::collections::{HashMap, HashSet};

use super::BarResolved;
use super::domain::SegmentSpans;

/// The position `_build_bar_text_layer` resolves: unset is `above`, and a
/// stacked `above` folds to `top` — the label sits inside its own segment, not
/// past the whole stack.
pub(super) fn label_position(ml: &MarkLabels, stacked: bool) -> &str {
    match ml.position.as_deref().unwrap_or("above") {
        "above" if stacked => "top",
        position => position,
    }
}

/// A horizontal-bar value label's own offset/alignment for its position
/// (`BAR_POS_MAP_HORIZONTAL`) — shared so a value label's measured ink (the
/// fit closure, which needs these numbers ahead of paint) and its painted
/// position (`mark_labels`) cannot diverge.
/// Returns `(dx, dy, align)`: `dx`/`dy` are an authored `ml.dx`/`ml.dy`
/// override over the position's own default; `align` is
/// `vega_fit::text_ink`'s alignment ("left"/"right"/"center"), which also
/// selects `mark_labels`'s SVG `text-anchor` ("left" → "start", "right"
/// → "end", "center" → "middle").
pub(super) fn horizontal_mark_label_offset(
    ml: &MarkLabels,
    stacked: bool,
) -> Result<(f64, f64, &'static str), String> {
    let (default_dx, align) = match label_position(ml, stacked) {
        "above" | "bottom" => (4.0, "left"),
        "top" => (-4.0, "right"),
        "middle" | "middle_aligned" => (0.0, "center"),
        other => return Err(format!("bar: mark labels position {other:?} is not a position")),
    };
    Ok((ml.dx.unwrap_or(default_dx), ml.dy.unwrap_or(0.0), align))
}

/// One value label's anchor and the tooltip description its datum carries.
pub(super) struct LabelPoint {
    /// The band-axis coordinate the label centers on.
    pub(super) center: f64,
    /// Where the label's baseline anchors along the measure axis: the bar's
    /// own end pixel for "above"/"top", HALF the value's pixel for "middle"
    /// (`_build_bar_text_layer`'s non-stacked `middle` branch:
    /// `datum[y_field] / 2`), and a stacked segment's own end or midpoint
    /// (`stacked_label_points`).
    pub(super) extent: f64,
    /// The datum's aria-label, byte-identical to its bar's
    /// (`bar_datum_aria`); `None` when the bar wires none.
    pub(super) aria: Option<String>,
    /// The fill of the bar this label names, set only where the text mark
    /// inherits the outer color channel (a grouped bar's outside label): an
    /// unpinned text color is then the series color.
    pub(super) series_fill: Option<String>,
    /// `_fit_hide_test` blanked the text: the mark stays, empty (Vega writes
    /// a self-closed `<text/>`), so its aria-label still twins the datum.
    pub(super) blank: bool,
    pub(super) side: Option<LabelSide>,
    /// How a ranged bar's label reads its span; `None` on any other bar.
    pub(super) span: Option<SpanLabel>,
    /// The printed text (`LabelSpec::text`).
    pub(super) text: String,
    /// The datum's link, which wraps its label.
    pub(super) href: Option<String>,
    /// The bar this label names recedes, so the label does with it.
    pub(super) recede: Option<crate::selected::Recede>,
}

/// A ranged bar's label under `top` (`_build_bar_span_text_layer`). A row that
/// starts at zero is a plain bar (a waterfall's totals): its value at the tip,
/// which sits inside the bar for a rise and below the baseline for a fall. Any
/// other row prints its signed change, centered on the span, since the label
/// measures the whole bar (`from_zero ? end : (end + start) / 2`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(super) enum SpanLabel {
    FromZero { rises: bool },
    Change { rises: bool },
}

impl SpanLabel {
    pub(super) fn rises(self) -> bool {
        let (Self::FromZero { rises } | Self::Change { rises }) = self;
        rises
    }

    /// The value a span label anchors at (`_build_bar_span_text_layer`): `above`
    /// at the tip, `bottom` at the start, `middle` halfway, and `top` at the tip of a
    /// row from zero, else halfway.
    pub(super) fn anchor(self, position: &str, start: f64, end: f64) -> f64 {
        match (position, self) {
            ("above", _) | ("top", Self::FromZero { .. }) => end,
            ("bottom", _) => start,
            _ => (start + end) / 2.0,
        }
    }

    /// `(magnitude, label)` of a bar from `start` to `end`: the number it
    /// prints (the change's size, or the end itself from zero) and how it
    /// reads it.
    pub(super) fn of(start: f64, end: f64) -> (f64, Self) {
        let rises = end >= start;
        if start == 0.0 {
            (end, Self::FromZero { rises })
        } else {
            ((end - start).abs(), Self::Change { rises })
        }
    }
}

/// The text a value label prints for `value` (a ranged bar's `span` reading
/// leads a change with its sign).
pub(super) fn label_text(ml: &MarkLabels, is_house: bool, value: f64, span: Option<SpanLabel>) -> Result<String, String> {
    let magnitude = format_mark_label(ml.format.as_ref(), value, is_house).map_err(|e| format!("bar: {e}"))?;
    Ok(match span {
        Some(SpanLabel::Change { rises }) => format!("{}{magnitude}", if rises { '+' } else { '\u{2212}' }),
        _ => magnitude,
    })
}

/// What turns a row into a value label (`_build_bar_text_layer`): the
/// position it resolves to, where that anchors on the measure axis, and the
/// text it prints.
pub(super) struct LabelSpec {
    pub(super) ml: MarkLabels,
    pub(super) position: String,
    pub(super) is_house: bool,
    /// `labels_draw_text`: `labels.field` names a column with a non-number
    /// value, so every value prints verbatim.
    pub(super) field_is_text: bool,
    /// The layer's mean measure, which `middle_aligned` halves (a
    /// `joinaggregate` over every row of the layer).
    pub(super) mean: f64,
}

impl LabelSpec {
    pub(super) fn new(
        ml: &MarkLabels,
        stacked: bool,
        is_house: bool,
        rows: &[serde_json::Map<String, Value>],
        y_field: &str,
    ) -> Self {
        let field_is_text = ml.field_is_text(rows);
        let measures: Vec<f64> = rows.iter().filter_map(|row| row_num(row, y_field)).collect();
        let mean = if measures.is_empty() { 0.0 } else { measures.iter().sum::<f64>() / measures.len() as f64 };
        LabelSpec { ml: ml.clone(), position: label_position(ml, stacked).to_string(), is_house, field_is_text, mean }
    }

    /// The measure value a non-stacked label anchors at: the bar end for
    /// `above`/`top`, half of it for `middle`, the baseline for `bottom`, and
    /// half the layer's mean for `middle_aligned`.
    pub(super) fn anchor(&self, value: f64) -> f64 {
        match self.position.as_str() {
            "middle" => value / 2.0,
            "bottom" => 0.0,
            "middle_aligned" => self.mean / 2.0,
            _ => value,
        }
    }

    /// The measure-axis pixel of a non-stacked label, clamped to the plot.
    pub(super) fn extent(&self, value: f64, position: impl Fn(f64) -> f64, plot_extent: f64) -> f64 {
        position(self.anchor(value)).clamp(0.0, plot_extent)
    }

    /// `(text, blank)` of `row`'s label. `labels.field` prints that column —
    /// formatted when every value is a number, verbatim when any is text,
    /// blank where it is null (`_house_register_text_encoding`'s `condition`);
    /// otherwise the formatted measure `value`.
    pub(super) fn text(
        &self,
        row: Option<&serde_json::Map<String, Value>>,
        value: f64,
        span: Option<SpanLabel>,
    ) -> Result<(String, bool), String> {
        if self.ml.field.is_none() {
            return Ok((label_text(&self.ml, self.is_house, value, span)?, false));
        }
        self.ml.field_text(self.is_house, self.field_is_text, row, value).map_err(|e| format!("bar: {e}"))
    }
}

/// What only the drawing of a row's bar knows about its label: the datum's
/// description and, where the text mark inherits the color channel, the fill.
#[derive(Default)]
pub(super) struct LabelDecor {
    pub(super) aria: Option<String>,
    pub(super) fill: Option<String>,
}

pub(super) type LabelDecors = HashMap<(String, String), LabelDecor>;

/// One value label before any geometry: where it anchors on the measure axis
/// and what it prints.
pub(super) struct LabelSite {
    pub(super) cat: String,
    /// The measure value that anchors the label (`LabelSpec::anchor`, or the
    /// segment's midpoint, or a span's anchor).
    pub(super) at: f64,
    pub(super) slot: Option<usize>,
    /// The measure the label prints and the fit test reads: the row's, a
    /// span's change, or the stack total.
    pub(super) value: f64,
    /// The row's own measure, which the datum's description names.
    pub(super) raw: f64,
    pub(super) span: Option<SpanLabel>,
    pub(super) text: String,
    /// The mark exists but prints nothing: `labels.field` is null here, or a
    /// genuine-zero label on a nonzero row.
    pub(super) blank: bool,
    /// `(category, series)` of the bar this label names, for its decor.
    pub(super) key: (String, String),
    /// The datum's link, which wraps its label like its bar.
    pub(super) href: Option<String>,
}

/// `label_points`'s row walk: `place(row, category, value)` returns the row's
/// site, or `None` for a row that owns none. Built from the rows directly,
/// never a category domain: a bar-type layer's union domain can carry bands
/// `rows` has no entry for, and a plain lookup keyed on the domain can't tell
/// "no row" from "a genuine 0".
pub(super) fn label_sites<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    (cat_field, y_field): (&str, &str),
    link: Option<&crate::href::DatumLink>,
    mut place: impl FnMut(&serde_json::Map<String, Value>, &str, f64) -> Result<Option<LabelSite>, String>,
) -> Result<Vec<LabelSite>, String> {
    let mut sites = Vec::new();
    for (row, val) in crate::common::valued_rows(rows, y_field) {
        if let Some(mut site) = place(row, &row_str(row, cat_field), val)? {
            site.href = link.map(|link| link.href(row)).transpose()?;
            sites.push(site);
        }
    }
    Ok(sites)
}

/// The sites of the single-series shapes: one label per row at its band's
/// center.
pub(super) fn plain_sites<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    fields: (&str, &str),
    link: Option<&crate::href::DatumLink>,
    spec: &LabelSpec,
    ranged: Option<&RangedBars>,
    // A colorless centered bar's `middle` label sits mid-span, not mid-value.
    centered: Option<&SegmentSpans>,
) -> Result<Vec<LabelSite>, String> {
    label_sites(rows, fields, link, |row, cat, val| {
        let (at, value, span) = match ranged {
            None => match centered.and_then(|spans| spans.get(&(cat.to_string(), String::new()))) {
                Some(seg) if spec.position == "middle" => ((seg.start + seg.end) / 2.0, val, None),
                _ => (spec.anchor(val), val, None),
            },
            Some(rb) => {
                let start = rb.starts.get(&(cat.to_string(), String::new())).copied();
                let start = start.ok_or_else(|| format!("bar: no start for category {cat:?}"))?;
                // A date span never starts at zero: its start is a date, and it prints its length.
                let (magnitude, numeric) = SpanLabel::of(start, val);
                let span = if rb.is_date() { SpanLabel::Change { rises: val >= start } } else { numeric };
                (span.anchor(&spec.position, start, val), magnitude, Some(span))
            }
        };
        let (mut text, blank) = spec.text(Some(row), value, span)?;
        if let (Some(rb), None) = (ranged.filter(|rb| rb.is_date()), spec.ml.field.as_ref()) {
            let start = rb.starts[&(cat.to_string(), String::new())];
            text = rb.duration_text(start, val)?;
        }
        Ok(Some(LabelSite {
            cat: cat.to_string(),
            at,
            slot: None,
            value,
            raw: val,
            span,
            text,
            blank,
            key: (cat.to_string(), String::new()),
            href: None,
        }))
    })
}

/// The sites of a stacked or grouped bar's labels, one per row that owns a
/// span: `top`/`above` at the segment end, `middle` at its midpoint.
/// Production's mark order follows `rows` — except a stacked `middle`, whose
/// layer carries no stack of its own, so the outer `order` channel sorts its
/// marks by series rank (`rank_order`).
#[allow(clippy::too_many_arguments)]
pub(super) fn segment_sites(
    spans: &SegmentSpans,
    rows: &[serde_json::Map<String, Value>],
    fields: (&str, &str),
    link: Option<&crate::href::DatumLink>,
    color_field: &str,
    spec: &LabelSpec,
    rank_order: Option<&[String]>,
    ranged: bool,
) -> Result<Vec<LabelSite>, String> {
    let ordered: Vec<&serde_json::Map<String, Value>> = match rank_order {
        Some(order) => order
            .iter()
            .flat_map(|series| rows.iter().filter(move |row| row_str(row, color_field) == *series))
            .collect(),
        None => rows.iter().collect(),
    };
    label_sites(ordered, fields, link, |row, cat, row_value| {
        let key = (cat.to_string(), row_str(row, color_field));
        let Some(span) = spans.get(&key) else { return Ok(None) };
        let value = row_value;
        let (at, value, label_span) = if ranged {
            let (magnitude, label_span) = SpanLabel::of(span.start, span.end);
            (label_span.anchor(&spec.position, span.start, span.end), magnitude, Some(label_span))
        } else if spec.position == "middle" {
            ((span.start + span.end) / 2.0, value, None)
        } else {
            (spec.anchor(span.end), value, None)
        };
        let (text, blank) = spec.text(Some(row), value, label_span)?;
        Ok(Some(LabelSite {
            cat: cat.to_string(),
            at,
            slot: span.slot,
            value,
            raw: row_value,
            span: label_span,
            text,
            blank,
            key,
            href: None,
        }))
    })
}

/// The stack-total label's sites (`_build_bar_total_label_layer`): a
/// `joinaggregate` sum prints the category's total at the stack's top. One mark
/// per category, taken from the stack's first row by series rank: every row of
/// a stack shares that position, so more marks would overprint.
pub(super) fn total_sites(
    spans: &SegmentSpans,
    totals: &HashMap<String, f64>,
    rows: &[serde_json::Map<String, Value>],
    fields: (&str, &str),
    link: Option<&crate::href::DatumLink>,
    color_field: &str,
    rank_order: &[String],
    spec: &LabelSpec,
) -> Result<Vec<LabelSite>, String> {
    let ranked = rank_order
        .iter()
        .flat_map(|series| rows.iter().filter(move |row| row_str(row, color_field) == *series));
    let mut stacks_marked = HashSet::new();
    label_sites(ranked, fields, link, |row, cat, _| {
        let key = (cat.to_string(), row_str(row, color_field));
        let Some(span) = spans.get(&key) else { return Ok(None) };
        if !stacks_marked.insert(cat.to_string()) {
            return Ok(None);
        }
        let total = totals
            .get(cat)
            .copied()
            .ok_or_else(|| format!("bar: internal error — no stacked total for category {cat:?}"))?;
        let (text, blank) = spec.text(Some(row), total, None)?;
        Ok(Some(LabelSite { cat: cat.to_string(), at: total, slot: span.slot, value: total, raw: total, span: None, text, blank, key, href: None }))
    })
}

/// The geometry a value label's site resolves against: the band axis, the
/// measure scale, and the plot extent that clamps a label to its rect.
pub(super) struct LabelGeometry<'a> {
    pub(super) band: &'a BandScale,
    /// A site's category, its band's own start and its slot to the label's band-axis center.
    pub(super) center: &'a dyn Fn(&str, f64, Option<usize>) -> f64,
    pub(super) linear: &'a LinearScale,
}

/// The fit test (`_fit_hide_test`) of a vertical `top`/`middle` label: its
/// pass's plot height, the measure span and the label's font size.
#[derive(Clone, Copy)]
pub(super) struct FitHide {
    pub(super) plot_h: f64,
    pub(super) span: f64,
    pub(super) font_size: f64,
}

/// What `value_label_layers` reads: the chart's rows, spans and label slots.
pub(super) struct LabelBuild<'a> {
    pub(super) r: &'a BarResolved,
    pub(super) rows: &'a [serde_json::Map<String, Value>],
    /// The rows a colorless bar labels, in the order the arm paints them.
    pub(super) plain_rows: &'a [&'a serde_json::Map<String, Value>],
    pub(super) cat_field: &'a str,
    /// The drawn category axis, which `visible: last` ranks the rows on.
    pub(super) cat_domain: &'a [String],
    pub(super) val_field: &'a str,
    pub(super) color_field: Option<&'a str>,
    pub(super) is_grouped: bool,
    pub(super) is_stacked: bool,
    pub(super) horizontal: bool,
    pub(super) spans: &'a SegmentSpans,
    /// Where a centered stack's labels sit, which is not where its bars are.
    pub(super) label_spans: Option<&'a SegmentSpans>,
    pub(super) stack_order: &'a [String],
    pub(super) totals: &'a HashMap<String, f64>,
    /// The genuine-zero label's slot, used when no value label is authored.
    pub(super) zero_text: Option<&'a MarkLabels>,
    /// A date-ranged bar's spans, when its authored labels print them.
    pub(super) span: Option<&'a RangedBars>,
    pub(super) fonts: &'a FontBook,
    pub(super) board_style: Option<&'a crate::BoardStyle>,
    pub(super) link: Option<&'a crate::href::DatumLink<'a>>,
}

/// `labels.visible: last`: each series' sites at its most recent date, the rank
/// `x_ranked_rows` gives its rows over the dates in time order (not the drawn
/// order, which a horizontal bar sorts by value). Only a time axis has one; the
/// compile stage refuses the rest. A stacked bar keeps every segment of its last
/// stack (each series ends there); the total label has its own switch.
fn last_sites(sites: Vec<LabelSite>, b: &LabelBuild) -> Result<Vec<LabelSite>, String> {
    let dates: Vec<&str> = b.cat_domain.iter().map(String::as_str).collect();
    let mut chronological = b.cat_domain.to_vec();
    if crate::families::temporal::x_values_are_standard_dates(&dates) {
        let mut axis = crate::time_axis::TimeAxis::new(Default::default(), HashMap::new());
        for date in &dates {
            axis.add(date).map_err(|e| e.diagnostic(&b.r.id, b.cat_field, "bar").to_string())?;
        }
        axis.sort_by_instant(&mut chronological);
    } else if crate::common::is_year_shaped(b.rows, b.cat_field) {
        // A year-shaped integer axis is a time axis: its years order numerically.
        let mut years = chronological
            .iter()
            .map(|c| c.parse::<i32>().map(|y| (y, c.clone())).map_err(|_| format!("bar: category {c:?} is no year on a year axis")))
            .collect::<Result<Vec<_>, _>>()?;
        years.sort();
        chronological = years.into_iter().map(|(_, c)| c).collect();
    } else {
        return Err("bar: labels.visible \"last\" needs a time axis on a bar chart; a categorical axis has no most-recent bar".into());
    }
    let series_of = |row: &serde_json::Map<String, Value>| b.color_field.map(|cf| row_str(row, cf)).unwrap_or_default();
    let mut last: HashMap<String, u64> = HashMap::new();
    for row in crate::common::x_ranked_rows(b.rows, b.cat_field, &chronological)? {
        let (Some(at), Some(_)) = (row.get(b.cat_field).and_then(Value::as_u64), row_num(&row, b.val_field)) else { continue };
        let slot = last.entry(series_of(&row)).or_insert(at);
        *slot = (*slot).max(at);
    }
    let rank: HashMap<&str, u64> = chronological.iter().enumerate().map(|(at, c)| (c.as_str(), at as u64)).collect();
    Ok(sites.into_iter().filter(|s| last.get(&s.key.1) == rank.get(s.cat.as_str())).collect())
}

/// A bar's value-label layers, one law for both orientations: the authored
/// (or genuine-zero) label layer, then the stack-total layer.
pub(super) fn value_label_layers(b: &LabelBuild) -> Result<Vec<LabelLayer>, String> {
    let r = b.r;
    let authored = r.style.mark.labels.as_ref().filter(|ml| ml.visible && r.wide_measures.is_empty());
    let mut layers = Vec::new();
    if let Some(ml) = authored.or(b.zero_text) {
        let zero_only = authored.is_none();
        let spec = LabelSpec::new(ml, b.is_stacked, r.style.label_is_house && !zero_only, b.rows, b.val_field);
        let sites = match b.color_field.filter(|_| b.is_grouped) {
            Some(color) => segment_sites(
                b.label_spans.unwrap_or(b.spans),
                b.rows,
                (b.cat_field, b.val_field),
                b.link,
                color,
                &spec,
                (b.is_stacked && spec.position == "middle").then_some(b.stack_order),
                b.span.is_some(),
            )?,
            None => plain_sites(
                b.plain_rows.iter().copied(),
                (b.cat_field, b.val_field),
                b.link,
                &spec,
                b.span,
                b.label_spans,
            )?,
        };
        let sites = if ml.last_only { last_sites(sites, b)? } else { sites };
        layers.push(LabelLayer::new(
            spec,
            sites,
            b.horizontal,
            b.is_stacked,
            b.span.is_some(),
            zero_only,
            false,
            b.fonts,
            b.board_style,
        )?);
    }
    if b.is_stacked && r.style.mark.total_label.as_ref().is_some_and(|t| t.visible == Some(true)) {
        let total_label = r.style.mark.total_label.as_ref().expect("checked above").as_mark_labels()?;
        let spec = LabelSpec::new(&total_label, false, r.style.total_label_is_house, b.rows, b.val_field);
        let sites = total_sites(
            b.spans,
            b.totals,
            b.rows,
            (b.cat_field, b.val_field),
            b.link,
            b.color_field.expect("is_stacked implies color"),
            b.stack_order,
            &spec,
        )?;
        layers.push(LabelLayer::new(spec, sites, b.horizontal, false, false, false, true, b.fonts, b.board_style)?);
    }
    Ok(layers)
}

/// One value-label layer: its sites, how they print, and where they sit.
pub(super) struct LabelLayer {
    pub(super) spec: LabelSpec,
    pub(super) font: LabelFont,
    pub(super) style: LabelStyle,
    pub(super) sites: Vec<LabelSite>,
    /// The printed text's measured width, per site.
    pub(super) widths: Vec<f64>,
    pub(super) stacked: bool,
    pub(super) ranged: bool,
    /// A genuine-zero label: its blank marks keep the bars' ink.
    pub(super) zero_only: bool,
    /// The stack-total layer, which no `data-dbt-value-label` group stamps.
    pub(super) total: bool,
    pub(super) horizontal: bool,
}

/// Where a plain bar's label sits relative to its anchor and which ink it takes.
/// `Out` and `In` carry a pixel direction (`1.0` or `-1.0`) along the measure
/// axis that follows the bar's own direction, so a negative bar's label never
/// lands on the wrong side of it; `Bare` stays at its position's own spot.
#[derive(Clone, Copy)]
pub(super) enum LabelSide {
    /// Just past the anchor, in ink.
    Out(f64),
    /// Just inside the anchor, over the bar, in the canvas color.
    In(f64),
    /// At the position's own spot, with no bar under the text: in ink.
    Bare,
}

impl LabelSide {
    /// The direction the text runs from its anchor.
    pub(super) fn run(self) -> f64 {
        match self {
            LabelSide::Out(dir) => dir,
            LabelSide::In(dir) => -dir,
            LabelSide::Bare => 0.0,
        }
    }
}

/// Where `LabelLayer::place` puts one site. `side` is `None` or `Some(Bare)`
/// where the label sits at its position's own spot; `Bare` paints in ink.
pub(super) struct LabelPlace {
    pub(super) center: f64,
    pub(super) extent: f64,
    pub(super) blank: bool,
    pub(super) side: Option<LabelSide>,
}

/// The axis group's own half-pixel translate, which moves the category labels
/// toward the plot.
pub(super) const AXIS_CRISPNESS_SHIFT: f64 = 0.5;

/// The gap between a bar's end and a label sitting just past it (or just
/// inside it), and each side of the room a horizontal label needs.
pub(super) const LABEL_FIT_PADDING: f64 = 4.0;

/// A bar painted shorter than this many drawn units (the board's own SVG
/// units, not device pixels) draws no visible mark; its label stands in for a
/// genuine-zero label and treats it like a zero. 1.5 is where a bar edge's
/// anti-aliasing stops reading as a mark at the board's natural width: a 1px
/// bar still shows as a faint hairline, a 1.5px one is lost beside the
/// baseline. A heuristic parameter, not a theme value.
pub(super) const INVISIBLE_BAR_PX: f64 = 1.5;

/// The least of the canvas a plot spans along the measure axis, which the
/// invisible-bar pre-test (`zero_label`) assumes before the plot is fitted.
/// A plot narrower than this drops a sliver's label. A heuristic parameter.
pub(super) const MIN_PLOT_SHARE: f64 = 0.25;

/// The room a label needs inside its bar along the measure axis: its measured
/// width plus padding on a horizontal bar, one line on a vertical one.
pub(super) fn label_need(horizontal: bool, width: f64, font_size: f64) -> f64 {
    if horizontal {
        width + 2.0 * LABEL_FIT_PADDING
    } else {
        LABEL_FIT_LINE_HEIGHT_MULTIPLIER * font_size
    }
}

/// A plain bar's painted length along the measure axis, in pixels: the zero
/// baseline (clamped to the plot) to the value's end (clamped likewise).
pub(super) fn painted_extent(px: &dyn Fn(f64) -> f64, (start, end): (f64, f64), value: f64) -> f64 {
    let (lo, hi) = (start.min(end), start.max(end));
    (px(value).clamp(lo, hi) - px(0.0).clamp(lo, hi)).abs()
}

/// How far the category labels move off the plot so a value label hanging
/// `overhang` past the plot edge keeps `VALUE_AXIS_BREATHING_ROOM` from them,
/// when they already sit `base` from that edge. The one clearance law of the
/// vertical and horizontal arms.
pub(super) fn category_clearance(overhang: f64, base: f64) -> f64 {
    (overhang + VALUE_AXIS_BREATHING_ROOM + AXIS_CRISPNESS_SHIFT - base).max(0.0)
}

/// How far the plain value labels of `layers` hang past the plot edge the
/// category axis sits on (`edge`), the plot being `ph` tall.
pub(super) fn label_hang(layers: &[LabelLayer], geom: &LabelGeometry, ph: f64, edge: crate::common::XEdge) -> f64 {
    layers
        .iter()
        .filter(|layer| !layer.total)
        .flat_map(|layer| layer.site_inks(geom, None))
        .map(|(_, ink)| match edge {
            crate::common::XEdge::Bottom => ink.y2 - ph,
            crate::common::XEdge::Top => -ink.y1,
        })
        .fold(0.0_f64, f64::max)
}

/// The genuine-zero label's one law: a bar painted at least `INVISIBLE_BAR_PX`
/// long draws a visible mark, so its zero-label slot stays blank.
pub(super) fn zero_label_blank(px: &dyn Fn(f64) -> f64, range: (f64, f64), value: f64) -> bool {
    painted_extent(px, range, value) >= INVISIBLE_BAR_PX
}

/// One genuine-zero label on a vertical bar: `(extent, side, blank)`.
pub(super) fn zero_label_at(spec: &LabelSpec, value: f64, linear: &LinearScale, plot_h: f64, font_size: f64) -> (f64, Option<LabelSide>, bool) {
    let px = |v: f64| linear.position(v);
    let at = spec.extent(value, px, plot_h);
    let (extent, side) = plain_label_place(&spec.position, value, at, &px, (plot_h, 0.0), label_need(false, 0.0, font_size));
    (extent, side, zero_label_blank(&px, (plot_h, 0.0), value))
}

/// The one law of a plain bar's value label: where its measure-axis anchor
/// sits (`at` is the position's own spot, in pixels) and which side of it the
/// text runs. A `top`/`middle`/`bottom` label that does not fit its painted
/// extent flips past the bar's own far end, in ink; a negative bar's `top`,
/// `bottom` and `above` labels follow the bar's direction. `middle_aligned`
/// floats free of its bar and keeps its spot, in ink wherever its bar does not
/// reach under the text. Every path that places a plain
/// bar's label calls this, so one authored layer reads the same on any base.
pub(super) fn plain_label_place(
    position: &str,
    value: f64,
    at: f64,
    px: &dyn Fn(f64) -> f64,
    range: (f64, f64),
    need: f64,
) -> (f64, Option<LabelSide>) {
    let upward = (px(1.0) - px(0.0)).signum();
    let bar_dir = if value < 0.0 { -upward } else { upward };
    let negative = value < 0.0;
    match position {
        "top" | "middle" | "bottom" if painted_extent(px, range, value) < need => {
            let (lo, hi) = (range.0.min(range.1), range.0.max(range.1));
            (px(value).clamp(lo, hi), Some(LabelSide::Out(bar_dir)))
        }
        "top" if negative => (at, Some(LabelSide::In(bar_dir))),
        "bottom" if negative => (at, Some(LabelSide::In(-bar_dir))),
        "above" if negative => (at, Some(LabelSide::Out(bar_dir))),
        "middle_aligned" => {
            let (lo, hi) = (range.0.min(range.1), range.0.max(range.1));
            let (a, b) = (px(0.0).clamp(lo, hi), px(value).clamp(lo, hi));
            let covered = a.min(b) <= at - need / 2.0 && at + need / 2.0 <= a.max(b);
            (at, (!covered).then_some(LabelSide::Bare))
        }
        _ => (at, None),
    }
}

impl LabelLayer {
    pub(super) fn new(
        spec: LabelSpec,
        sites: Vec<LabelSite>,
        horizontal: bool,
        stacked: bool,
        ranged: bool,
        zero_only: bool,
        total: bool,
        fonts: &FontBook,
        board_style: Option<&crate::BoardStyle>,
    ) -> Result<Self, String> {
        let style = LabelStyle::new(&spec.ml, horizontal, stacked, board_style)?;
        let font = label_font(&spec.ml, board_style)?;
        let widths = sites
            .iter()
            .map(|site| if site.blank { Ok(0.0) } else { fonts.measure(&site.text, &font.family, font.size) })
            .collect::<Result<Vec<_>, String>>()?;
        Ok(LabelLayer { spec, font, style, sites, widths, stacked, ranged, zero_only, total, horizontal })
    }

    /// Where a site's label sits, or `None` where its category has no band. A
    /// plain bar follows `plain_label_place`; a stacked or ranged segment keeps
    /// the `hide` fit test (no flip), and a stack total never hides.
    pub(super) fn place(&self, site: &LabelSite, width: f64, geom: &LabelGeometry, hide: Option<FitHide>) -> Option<LabelPlace> {
        let band_x = geom.band.position(&site.cat)?;
        let center = (geom.center)(&site.cat, band_x, site.slot);
        let at = geom.linear.position(site.at);
        let blank_at = |blank| Some(LabelPlace { center, extent: at, blank, side: None });
        if site.blank {
            return blank_at(true);
        }
        if self.stacked || self.ranged || self.total {
            let hidden = hide.is_some_and(|h| {
                fit_hides_label(&self.spec.position, site.value, h.plot_h, h.span, h.font_size, self.ranged)
            });
            return blank_at(hidden);
        }
        let linear = geom.linear;
        let px = |v: f64| linear.position(v);
        let range = (linear.range_start, linear.range_end);
        if self.zero_only && zero_label_blank(&px, range, site.value) {
            return blank_at(true);
        }
        let need = label_need(self.horizontal, width, self.font.size);
        let (extent, side) = plain_label_place(&self.spec.position, site.value, at, &px, range, need);
        Some(LabelPlace { center, extent, blank: false, side })
    }

    /// The points `mark_labels` paints, in site order. `decor` is each
    /// site's description and inherited fill.
    pub(super) fn points(
        &self,
        geom: &LabelGeometry,
        hide: Option<FitHide>,
        decors: &LabelDecors,
        selection: &crate::selected::Selection,
        plain_aria: impl Fn(&LabelSite) -> Result<Option<String>, String>,
    ) -> Result<Vec<LabelPoint>, String> {
        let mut points = Vec::with_capacity(self.sites.len());
        for (site, &width) in self.sites.iter().zip(&self.widths) {
            let Some(LabelPlace { center, extent, blank, side }) = self.place(site, width, geom, hide) else { continue };
            let (aria, series_fill) = match decors.get(&site.key) {
                Some(d) => (d.aria.clone(), d.fill.clone()),
                None => (plain_aria(site)?, None),
            };
            points.push(LabelPoint {
                center,
                extent,
                aria,
                series_fill,
                blank,
                side,
                span: site.span,
                text: site.text.clone(),
                href: site.href.clone(),
                recede: selection.recede_of(site.href.as_deref()),
            });
        }
        Ok(points)
    }

    /// A blanked mark keeps a zero-width box one line tall (`autosize: fit` bounds it), except
    /// in a genuine-zero layer that labels nothing at this size, whose marks are all blank and
    /// move nothing.
    fn placements<'a>(
        &'a self,
        geom: &'a LabelGeometry,
        hide: Option<FitHide>,
    ) -> impl Iterator<Item = (&'a LabelSite, vega_fit::Bounds, Option<LabelSide>)> + 'a {
        let silent = self.zero_only
            && self.sites.iter().zip(&self.widths).all(|(site, &width)| self.place(site, width, geom, hide).is_none_or(|p| p.blank));
        self.sites.iter().zip(&self.widths).filter_map(move |(site, &width)| {
            if silent {
                return None;
            }
            let LabelPlace { center, extent, blank, side } = self.place(site, width, geom, hide)?;
            let spot = label_spot(
                &self.spec.ml,
                &self.style.position,
                self.style.horizontal_offset,
                center,
                extent,
                site.span,
                side,
            );
            let w = if blank { 0.0 } else { width };
            Some((site, vega_fit::text_ink(spot.x, spot.y, w, self.font.size, spot.align(), spot.baseline), side))
        })
    }

    pub(super) fn site_inks<'a>(
        &'a self,
        geom: &'a LabelGeometry,
        hide: Option<FitHide>,
    ) -> impl Iterator<Item = (&'a LabelSite, vega_fit::Bounds)> + 'a {
        self.placements(geom, hide).map(|(site, ink, _)| (site, ink))
    }

    /// `WARN-VALUE-LABELS-CROWD-WIDTH` over the labels this layer paints; `layer` is the authored
    /// layer index. A label drawn over its bar must also fit `bar_width(site)`. A horizontal bar
    /// passes none: its text runs along the bar, and `plain_label_place` flips it out when it
    /// does not fit. The stack-total and auto-zero layers are not the authored `labels` block.
    pub(super) fn warn_if_crowded(
        &self,
        chart_id: &str,
        layer: Option<usize>,
        geom: &LabelGeometry,
        hide: Option<FitHide>,
        bar_width: Option<&dyn Fn(&LabelSite) -> f64>,
    ) {
        if self.total || self.zero_only {
            return;
        }
        crate::common::warn_value_labels_crowd(
            chart_id,
            layer,
            self.placements(geom, hide).map(|(site, ink, side)| {
                let drawn_over_bar = self.style.inside_bar && !matches!(side, Some(LabelSide::Out(_)));
                (ink, bar_width.filter(|_| drawn_over_bar).map(|width| width(site)))
            }),
        );
    }

    pub(super) fn ink(&self, geom: &LabelGeometry, hide: Option<FitHide>, b: &mut vega_fit::Bounds) {
        for (_, ink) in self.site_inks(geom, hide) {
            b.union(&ink);
        }
    }
}

/// `_measure_span` (`features/value_labels.py`): the measure span the EMITTED
/// scale pins — an authored domain pair, else `domainMax - domainMin` — read
/// before Vega niceties move the drawn domain past it. `None` where no bound is pinned (Vega
/// then derives the domain from the data, and the fit test stands down) and
/// under `normalize`, whose segments are shares while the rows carry raw
/// values.
pub(super) fn label_fit_span(
    ax_y: &AxisStyle,
    stack: &str,
    stacked_domain_max: Option<f64>,
) -> Option<f64> {
    if stack == "normalize" {
        return None;
    }
    let span = match ax_y.authored_continuous_domain() {
        Some((lo, hi)) => hi - lo,
        None => {
            let max = if matches!(stack, "zero" | "center") { stacked_domain_max } else { ax_y.domain_max }?;
            let floor = if ax_y.is_zero_false() { ax_y.domain_min.unwrap_or(0.0) } else { 0.0 };
            max - floor
        }
    };
    (span > 0.0).then_some(span)
}

/// `_fit_hide_test` (`features/value_labels.py`) for a stacked or ranged
/// vertical segment: a `top`/`middle` label whose segment is shorter than one
/// label line — `v * plot_h / span` pixels against
/// `label_fit_line_height_multiplier` times the label's font size — is blanked,
/// and Vega writes the empty mark as a self-closed `<text/>`. An exact zero is
/// exempt, and a span's extent is its change whichever way it runs. A plain
/// bar's label never hides: `plain_label_place` flips it outside instead
/// (`middle_aligned`, which floats free of its bar, is the exception).
pub(super) fn fit_hides_label(
    position: &str,
    value: f64,
    plot_h: f64,
    span: f64,
    font_size: f64,
    ranged: bool,
) -> bool {
    let short = |v: f64| v * plot_h / span < LABEL_FIT_LINE_HEIGHT_MULTIPLIER * font_size;
    if ranged {
        // A span's extent is its change, whichever way it runs, and a zero
        // change is no exception (`_fit_hide_test`); `above` sits in open space.
        return position != "above" && short(value.abs());
    }
    match position {
        // `bottom` anchors at the baseline, so a stacked one says nothing
        // about its segment (`_fit_measures_the_label`).
        "top" => value > 0.0 && short(value),
        "middle" => value != 0.0 && short(value.abs()),
        _ => false,
    }
}

/// The band x's step geometry a layer's captions anchor against.
pub(super) fn step_band_of(band: &BandScale, cat_domain: &[String]) -> crate::families::layers::StepBand {
    let center = |cat: Option<&String>| {
        band.position(cat.expect("a painted layer has a category domain")).expect("a domain category has a band")
            + band.bandwidth() / 2.0
    };
    crate::families::layers::StepBand {
        bandwidth: band.bandwidth(),
        first: center(cat_domain.first()),
        last: center(cat_domain.last()),
    }
}

/// One layer's value-label ink in the vertical fit: its style, one item per
/// row, and its authored position (which picks its own axis once layers own one).
pub(super) struct LayerLabelFit<'a> {
    pub(super) style: crate::families::layers::LayerLabelStyle<'a>,
    pub(super) items: Vec<LayerLabelFitItem>,
    pub(super) layer: usize,
}

/// A layer row's measure, caption width, category and caption spot.
pub(super) struct LayerLabelFitItem {
    pub(super) val: f64,
    pub(super) width: f64,
    pub(super) cat: String,
    pub(super) spot: crate::common::CaptionSpot,
}

/// A value label's own font: `ml.font` over the chart's defaults.
pub(crate) struct LabelFont {
    pub family: String,
    pub size: f64,
    pub weight: String,
    /// `None` takes the position's own default ink.
    pub color: Option<String>,
}

pub(crate) fn label_font(ml: &MarkLabels, board_style: Option<&crate::BoardStyle>) -> Result<LabelFont, String> {
    let f = ml.font.as_ref();
    let (family, size) = crate::common::value_label_face(f, board_style)?;
    Ok(LabelFont {
        family,
        size,
        weight: f.map(|f| f.weight_str()).unwrap_or_else(|| "400".into()),
        color: f.and_then(|f| f.color.clone()),
    })
}

/// Where a value label's text mark sits, plot-local: the mark's `(x, y)`
/// before Vega's baseline shift, its baseline, and the SVG `text-anchor`.
pub(super) struct LabelSpot {
    pub(super) x: f64,
    pub(super) y: f64,
    pub(super) baseline: &'static str,
    pub(super) anchor: &'static str,
}

impl LabelSpot {
    /// `vega_fit::text_ink`'s alignment for this anchor.
    pub(super) fn align(&self) -> &'static str {
        match self.anchor {
            "start" => "left",
            "end" => "right",
            _ => "center",
        }
    }
}

/// A span label's spot (`_SPAN_POS_VERTICAL`/`_SPAN_POS_HORIZONTAL`): which way the
/// bar runs picks the side of the anchor the text sits on, `above` past the tip,
/// `top` just inside it (centered on a change row), `bottom` inside the start, and
/// `middle` on the midpoint. An authored `dx`/`dy` replaces the table's offset.
pub(super) fn span_spot(ml: &MarkLabels, position: &str, horizontal: bool, center: f64, extent: f64, span: SpanLabel) -> LabelSpot {
    let (rises, from_zero) = (span.rises(), matches!(span, SpanLabel::FromZero { .. }));
    if horizontal {
        let (dx, anchor) = match (position, from_zero, rises) {
            ("above", _, true) | ("bottom", _, true) | ("top", true, false) => (4.0, "start"),
            ("above", _, false) | ("bottom", _, false) | ("top", true, true) => (-4.0, "end"),
            _ => (0.0, "middle"),
        };
        return LabelSpot { x: extent + ml.dx.unwrap_or(dx), y: center + ml.dy.unwrap_or(0.0), baseline: "middle", anchor };
    }
    let (baseline, dy) = match (position, from_zero, rises) {
        ("above", _, true) | ("bottom", _, true) | ("top", true, false) => ("bottom", -4.0),
        ("above", _, false) | ("bottom", _, false) | ("top", true, true) => ("top", 4.0),
        _ => ("middle", 0.0),
    };
    LabelSpot { x: center + ml.dx.unwrap_or(0.0), y: extent + ml.dy.unwrap_or(dy), baseline, anchor: "middle" }
}

/// The position's offset table applied to a point's `(center, extent)`.
/// `horizontal_offset` is `horizontal_mark_label_offset`'s answer on a
/// horizontal bar, `None` on a vertical one.
pub(super) fn label_spot(
    ml: &MarkLabels,
    position: &str,
    horizontal_offset: Option<(f64, f64, &str)>,
    center: f64,
    extent: f64,
    span: Option<SpanLabel>,
    side: Option<LabelSide>,
) -> LabelSpot {
    if let Some(side @ (LabelSide::Out(_) | LabelSide::In(_))) = side {
        let dir = side.run();
        let off = dir * LABEL_FIT_PADDING;
        return if horizontal_offset.is_some() {
            LabelSpot { x: extent + ml.dx.unwrap_or(off), y: center + ml.dy.unwrap_or(0.0), baseline: "middle", anchor: if dir > 0.0 { "start" } else { "end" } }
        } else {
            LabelSpot { x: center + ml.dx.unwrap_or(0.0), y: extent + ml.dy.unwrap_or(off), baseline: if dir < 0.0 { "bottom" } else { "top" }, anchor: "middle" }
        };
    }
    if let Some(span) = span {
        return span_spot(ml, position, horizontal_offset.is_some(), center, extent, span);
    }
    if let Some((dx, dy, align)) = horizontal_offset {
        let anchor = match align {
            "left" => "start",
            "right" => "end",
            _ => "middle",
        };
        return LabelSpot { x: extent + dx, y: center + dy, baseline: "middle", anchor };
    }
    // Vertical arm's own measure-axis default (`ml.dy`'s fallback): labels sit
    // above/below the column's own extent, never side-offset.
    // `BAR_POS_MAP_VERTICAL`: above and bottom → baseline bottom, dy −4; top →
    // baseline top, dy 4; middle and middle_aligned → baseline middle, no
    // default dy (the point is already positioned at its own pixel).
    // A ranged bar's `top` label reads its row (`_SPAN_POS_VERTICAL`): from
    // zero it keeps the tip, inside a rise and past a fall; a change centers.
    let (baseline, map_measure_off) = match position {
        "above" | "bottom" => ("bottom", -4.0),
        "top" => ("top", 4.0),
        _ => ("middle", 0.0),
    };
    LabelSpot {
        x: center + ml.dx.unwrap_or(0.0),
        y: extent + ml.dy.unwrap_or(map_measure_off),
        baseline,
        anchor: "middle",
    }
}

/// The resolved look of one value-label layer: everything but the points.
pub(super) struct LabelStyle {
    pub(super) position: String,
    /// Its `color` is the authored `labels.font.color`; unset takes the
    /// position's default.
    pub(super) font: LabelFont,
    pub(super) font_style: Option<String>,
    /// `_build_bar_text_layer`'s `_INSIDE_BAR_POSITIONS` gate: a label painted
    /// OVER the bar's own ink (top/middle) defaults to the chart canvas color
    /// for legibility, unlike "above" (painted on the plot's own background,
    /// default `config.text.color`, or — where the text mark inherits the
    /// outer color channel — the series color).
    pub(super) inside_bar: bool,
    pub(super) horizontal_offset: Option<(f64, f64, &'static str)>,
}

impl LabelStyle {
    /// The horizontal branch's own offset/alignment table lives in
    /// `horizontal_mark_label_offset` (shared with the fit closure's ink).
    pub(super) fn new(
        ml: &MarkLabels,
        horizontal: bool,
        stacked: bool,
        board_style: Option<&crate::BoardStyle>,
    ) -> Result<Self, String> {
        let position = label_position(ml, stacked);
        if !matches!(position, "above" | "top" | "middle" | "bottom" | "middle_aligned") {
            return Err(format!("bar: mark labels position {position:?} is not a position"));
        }
        let horizontal_offset =
            if horizontal { Some(horizontal_mark_label_offset(ml, stacked)?) } else { None };
        Ok(LabelStyle {
            position: position.to_string(),
            font: label_font(ml, board_style)?,
            font_style: ml.font.as_ref().and_then(|f| f.style.clone()),
            inside_bar: matches!(position, "top" | "middle" | "bottom" | "middle_aligned"),
            horizontal_offset,
        })
    }
}

/// Draw authored bar value labels (`style.mark.labels`) — production's
/// value_labels feature as a text layer per BAR_POS_MAP_HORIZONTAL/_VERTICAL,
/// with VL text-mark defaults (baseline middle, align center from the config
/// text style). Points come from `label_points`; `stacked` is
/// `_build_bar_text_layer`'s `is_stacked` (an `above` folds to `top`, and
/// every stacked label defaults to the canvas ink).
///
/// Labels whose points carry an aria-label form ONE text mark set named
/// `layer_{layer}_marks`, `layer` counting the layers painted before it —
/// stamped `data-dbt-value-label` (`contract::value_label_group_open`) for a
/// segment/value layer, plain for the stack-total layer; a point with no
/// aria-label prints a bare `<text>`, the same `tooltip_labels` gate the bars
/// use.
///
/// A blank label prints as a self-closing `<text/>`.
#[allow(clippy::too_many_arguments)]
pub(super) fn mark_labels(
    ml: &MarkLabels,
    horizontal: bool,
    stacked: bool,
    stamp_value_label: bool,
    points: &[LabelPoint],
    layer: usize,
    board_style: Option<&crate::BoardStyle>,
    plot_left: f64,
    plot_top: f64,
    canvas: &str,
    link_open: crate::href::LinkOpen,
) -> Result<Vec<Node>, String> {
    let style = LabelStyle::new(ml, horizontal, stacked, board_style)?;
    let stamped = points.iter().any(|p| p.aria.is_some());
    let mut labels = Vec::new();
    for point in points {
        let LabelPoint { center, extent, ref aria, ref series_fill, blank, side, span, ref text, ref href, recede } = *point;
        let anchor = link_open.anchor(href.as_deref()).map(|anchor| crate::href::Anchor { recede, ..anchor });
        let color = style.font.color.as_deref().unwrap_or(if style.inside_bar && !matches!(side, Some(LabelSide::Out(_) | LabelSide::Bare)) {
            canvas
        } else {
            series_fill.as_deref().unwrap_or("#313233")
        });
                let spot = label_spot(ml, &style.position, style.horizontal_offset, center, extent, span, side);
        let attrs = Attrs::new()
            .set("x", format!("{:.2}", plot_left + spot.x))
            .set("y", format!("{:.2}", plot_top + spot.y + measure::baseline_shift(spot.baseline, style.font.size)))
            .set("text-anchor", spot.anchor)
            .vega("font-family", style.font.family.as_str())
            .set("font-size", style.font.size.to_string())
            .set("font-weight", style.font.weight.as_str())
            .set_if(style.font_style.is_some(), "font-style", style.font_style.clone().unwrap_or_default())
            .set("fill", color)
            .extend(crate::contract::aria_attrs(aria.as_deref(), crate::contract::ItemRole::TextMark)?);
        let label = if blank {
            Node::EmptyText(attrs)
        } else {
            Node::text(attrs, text.as_str())
        };
        labels.push(crate::href::linked(anchor, label));
    }
    if !stamped {
        return Ok(labels);
    }
    let name = format!("layer_{layer}_marks");
    Ok(vec![if stamp_value_label {
        crate::contract::value_label_group(&name, labels)?
    } else {
        crate::contract::mark_group("text", &name, labels)?
    }])
}

#[cfg(test)]
mod span_label_tests {
    use super::{SpanLabel, fit_hides_label, label_spot, label_text};
    use crate::common::MarkLabels;

    fn labels() -> MarkLabels {
        serde_json::from_value(serde_json::json!({
            "visible": true, "field": null, "format": "$,.0f", "dx": null, "dy": null,
            "font": null, "position": "top",
        }))
        .unwrap()
    }

    /// `_build_bar_span_text_layer`, `top`: a row from zero keeps the tip label,
    /// the change of any other row centers on its bar.
    #[test]
    fn a_span_label_anchors_at_the_tip_from_zero_and_the_midpoint_otherwise() {
        let anchored = |start, end| {
            let (magnitude, span) = SpanLabel::of(start, end);
            (span.anchor("top", start, end), magnitude, span)
        };
        assert_eq!(anchored(0.0, 120.0), (120.0, 120.0, SpanLabel::FromZero { rises: true }));
        assert_eq!(anchored(120.0, 148.0), (134.0, 28.0, SpanLabel::Change { rises: true }));
        assert_eq!(anchored(161.0, 139.0), (150.0, 22.0, SpanLabel::Change { rises: false }));
        // A bar from zero that falls (a negative total) is a fall at its tip.
        assert_eq!(anchored(0.0, -40.0), (-40.0, -40.0, SpanLabel::FromZero { rises: false }));
    }

    /// `_SPAN_POS_VERTICAL["top"]` is `(rise, fall) = (top/+4, bottom/-4)`, and a
    /// change row's `_SPAN_CENTERED_VERTICAL` is `middle/0`.
    #[test]
    fn a_span_label_takes_its_baseline_and_offset_from_its_row() {
        let ml = labels();
        let spot = |span| label_spot(&ml, "top", None, 10.0, 50.0, span, None);
        let (rise, fall, change) = (
            spot(Some(SpanLabel::FromZero { rises: true })),
            spot(Some(SpanLabel::FromZero { rises: false })),
            spot(Some(SpanLabel::Change { rises: true })),
        );
        assert_eq!((rise.baseline, rise.y), ("top", 54.0));
        assert_eq!((fall.baseline, fall.y), ("bottom", 46.0));
        assert_eq!((change.baseline, change.y), ("middle", 50.0));
        assert_eq!(spot(None).baseline, "top", "a plain bar's `top` label is unchanged");
    }

    #[test]
    fn a_change_leads_its_size_with_its_sign() {
        let ml = labels();
        let text = |value, span| label_text(&ml, false, value, span).unwrap();
        assert_eq!(text(28.0, Some(SpanLabel::Change { rises: true })), "+$28");
        assert_eq!(text(22.0, Some(SpanLabel::Change { rises: false })), "\u{2212}$22");
        assert_eq!(text(120.0, Some(SpanLabel::FromZero { rises: true })), "$120");
        assert_eq!(text(120.0, None), "$120");
    }

    /// `_fit_hide_test` on a span measures `abs(end - start)` whichever way it
    /// runs, and an empty change is no exception: unlike a plain `top` label
    /// (positive values only, a zero exempt), a span's zero and negative
    /// extents both blank.
    #[test]
    fn a_span_is_fit_tested_by_the_size_of_its_change() {
        // 340px over a 173.88 span: 12.65px is 6.5 units.
        let hides = |value, ranged| fit_hides_label("top", value, 340.0, 173.88, 11.0, ranged);
        assert!(hides(6.0, true) && !hides(7.0, true));
        assert!(hides(0.0, true), "a zero change has no room");
        assert!(hides(-6.0, true), "the size of a fall");
        assert!(!hides(0.0, false), "a segment's zero keeps its label");
        assert!(!hides(-6.0, false), "a negative segment's `top` label sits outside the bar");
    }
}
