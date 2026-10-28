//! Authored annotations: rules, bands, notes and trends. A family builds an
//! `AnnotationCtx` from the scales it draws with and calls `bounds` in its
//! fit and `paint` twice in its paint body; every placement is computed in
//! `resolve`, so what the fit reserves is what paint draws.

pub mod axis_map;
mod emit;
pub mod model;
mod place;
mod text;

use serde::Deserialize;

pub use axis_map::{AxisMap, Site};
pub use model::{Annotation, YAxis};

use crate::diagnostic::Diagnostic;
use measure::FontBook;
use crate::vega_fit::Bounds;
use place::{Mark, Placed};

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Plane {
    Background,
    Foreground,
}

/// Which screen axis the x CHANNEL runs along. Horizontal bars swap.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Orientation {
    Vertical,
    Horizontal,
}

/// How far the chart's own ink (axes, their titles, marks) reaches past each
/// plot edge, px. Annotation text past an edge clears it.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Reach {
    pub left: f64,
    pub top: f64,
    pub right: f64,
    pub bottom: f64,
}

impl Reach {
    /// What `ink`, in the plot-relative frame of a `pw` x `ph` plot, reaches past it.
    pub fn past(ink: &Bounds, pw: f64, ph: f64) -> Self {
        Reach {
            left: (-ink.x1).max(0.0),
            top: (-ink.y1).max(0.0),
            right: (ink.x2 - pw).max(0.0),
            bottom: (ink.y2 - ph).max(0.0),
        }
    }
}

/// Absolute plot origin and size in chart px.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PlotRect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

pub struct AnnotationCtx<'a> {
    pub annotations: &'a [Annotation],
    /// The chart's id, the `chart_id` param of every annotation diagnostic.
    pub chart_id: &'a str,
    /// Canonical panel key; entries whose `panel` is Some and differs are skipped.
    pub panel: Option<&'a str>,
    pub plot: PlotRect,
    /// The chart's own ink past the plot at this size; zero on an arm that
    /// reserves no margin for annotation text (it refuses text there).
    pub reach: Reach,
    /// Map of the x CHANNEL, in plot-relative px along the screen axis it occupies.
    pub x: AxisMap,
    /// Map of the base y channel.
    pub y: AxisMap,
    /// One per `layers:` entry, same index as the authored list. A layer that
    /// shares the base scale carries a clone of `y`.
    pub layer_y: Vec<AxisMap>,
    pub orientation: Orientation,
    pub fonts: &'a FontBook,
    /// Canvas color behind the plot; the halo stroke.
    pub canvas: &'a str,
    /// `format!("{}-ann{}", contract::safe_svg_id(chart_id), panel_ordinal)`.
    pub clip_id: String,
    /// The chart's own ink inside and around the plot, plot-relative: what
    /// text moves off. Set at paint before the `Foreground` call; empty in the fit.
    pub occupied: Vec<DataMark>,
}

/// Chart ink annotation text may sit on, plot-relative: a bar's, point's or
/// label's box, an area fill between its top and bottom outlines, or a
/// stroked line; outlines and lines sorted by x.
#[derive(Debug, Clone)]
pub enum DataMark {
    Box(Bounds),
    Area { hi: Vec<(f64, f64)>, lo: Vec<(f64, f64)> },
    Line { points: Vec<(f64, f64)>, width: f64 },
}

/// Column width, px, a fill or line is cut into for placement.
const COLUMN: f64 = 4.0;

impl DataMark {
    pub fn rect(x: f64, y: f64, w: f64, h: f64) -> Self {
        DataMark::Box(Bounds::new(x, y, x + w, y + h))
    }

    /// Whether a halo `auto` draws on text over `b`: a line is no mark to it.
    fn overlaps(&self, b: &Bounds) -> bool {
        match self {
            DataMark::Box(m) => m.x1 < b.x2 && m.x2 > b.x1 && m.y1 < b.y2 && m.y2 > b.y1,
            DataMark::Line { .. } => false,
            // Sampled every pixel across the text: the fill's column there
            // must reach into the text's rows.
            DataMark::Area { hi, lo } => {
                let (Some(a), Some(z)) = (hi.first(), hi.last()) else { return false };
                let (x1, x2) = (b.x1.max(a.0.min(z.0)), b.x2.min(a.0.max(z.0)));
                let steps = (x2 - x1).ceil().max(0.0) as usize;
                (0..=steps).map(|i| (x1 + i as f64).min(x2)).filter(|_| x1 <= x2).any(|x| {
                    let (top, bottom) = (y_at(hi, x), y_at(lo, x));
                    top.min(bottom) < b.y2 && top.max(bottom) > b.y1
                })
            }
        }
    }

    /// The mark as boxes text placement scores against: a fill or line in
    /// `COLUMN`-wide slices, each spanning what the mark covers there.
    fn boxes(&self) -> Vec<Bounds> {
        let columns = |points: &[(f64, f64)], span: &dyn Fn(f64, f64) -> (f64, f64)| -> Vec<Bounds> {
            let (Some(a), Some(z)) = (points.first(), points.last()) else { return Vec::new() };
            let (x0, x1) = (a.0.min(z.0), a.0.max(z.0));
            let steps = ((x1 - x0) / COLUMN).ceil().max(1.0) as usize;
            (0..steps)
                .map(|i| {
                    let (c0, c1) = (x0 + i as f64 * COLUMN, (x0 + (i + 1) as f64 * COLUMN).min(x1));
                    let (y0, y1) = span(c0, c1);
                    Bounds::new(c0, y0, c1, y1)
                })
                .collect()
        };
        match self {
            DataMark::Box(b) => vec![*b],
            DataMark::Area { hi, lo } => columns(hi, &|c0, c1| {
                let ys = [y_at(hi, c0), y_at(hi, c1), y_at(lo, c0), y_at(lo, c1)];
                (ys.iter().copied().fold(f64::INFINITY, f64::min), ys.iter().copied().fold(f64::NEG_INFINITY, f64::max))
            }),
            DataMark::Line { points, width } => columns(points, &|c0, c1| {
                let inner = points.iter().filter(|p| p.0 > c0 && p.0 < c1).map(|p| p.1);
                let ys: Vec<f64> = [y_at(points, c0), y_at(points, c1)].into_iter().chain(inner).collect();
                let (y0, y1) = (ys.iter().copied().fold(f64::INFINITY, f64::min), ys.iter().copied().fold(f64::NEG_INFINITY, f64::max));
                (y0 - width / 2.0, y1 + width / 2.0)
            }),
        }
    }
}

/// The outline's y at x, straight between its points; flat past its ends.
fn y_at(points: &[(f64, f64)], x: f64) -> f64 {
    let after = points.partition_point(|p| p.0 < x).clamp(1, points.len().max(1));
    let (a, z) = (points[after - 1], points[after.min(points.len() - 1)]);
    if z.0 == a.0 {
        return if x < a.0 { a.1 } else { z.1 };
    }
    a.1 + (z.1 - a.1) * ((x - a.0) / (z.0 - a.0)).clamp(0.0, 1.0)
}

/// Every annotation placed at this plot size, in list order.
pub(crate) fn resolve<'a>(ctx: &'a AnnotationCtx) -> Result<Vec<Placed<'a>>, Diagnostic> {
    let mut items = Vec::new();
    for ann in ctx.annotations {
        if let Some(p) = place::place(ann, ctx)? {
            items.push(p);
        }
    }
    items.sort_by_key(|p| p.index);
    place::separate(&mut items, ctx);
    if !ctx.occupied.is_empty() {
        let occupied: Vec<Bounds> = ctx.occupied.iter().flat_map(DataMark::boxes).collect();
        place::arrange(&mut items, ctx, &occupied);
    }
    Ok(items)
}

fn mark_bounds(m: &Mark) -> Bounds {
    let fold = |points: &mut dyn Iterator<Item = (f64, f64)>| {
        points.fold(Bounds::new(f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY), |b, (x, y)| {
            Bounds::new(b.x1.min(x), b.y1.min(y), b.x2.max(x), b.y2.max(y))
        })
    };
    match m {
        Mark::Segment { a, b } => fold(&mut [*a, *b].into_iter()),
        Mark::Rect { x, y, w, h } => Bounds::new(*x, *y, x + w, y + h),
        Mark::Polyline(points) => fold(&mut points.iter().copied()),
        Mark::Area { hi, lo } => fold(&mut hi.iter().chain(lo).copied()),
        Mark::Circle { c, r } => Bounds::new(c.0 - r, c.1 - r, c.0 + r, c.1 + r),
    }
}

/// Plot-relative ink union of every placed item (plot top-left is 0,0), the
/// same frame `vega_fit::fit_plot`'s ink closure works in. Marks are clipped
/// to the plot, so only text reaches past it. `None` when nothing is placed
/// or the annotations are refused: the refusal surfaces from `paint`, which
/// resolves the same annotations.
pub fn bounds(ctx: &AnnotationCtx) -> Option<Bounds> {
    let items = resolve(ctx).ok()?;
    let mut union: Option<Bounds> = None;
    let mut add = |b: Bounds| match &mut union {
        Some(u) => u.union(&b),
        None => union = Some(b),
    };
    for p in &items {
        let marks = [&p.mark, &p.interval, &p.enclosure].into_iter().flatten().map(mark_bounds);
        let connector = p.connector.iter().map(|c| {
            mark_bounds(&Mark::Polyline([Some(c.from), c.control, Some(c.to)].into_iter().flatten().collect()))
        });
        for b in marks.chain(connector) {
            add(Bounds::new(
                b.x1.clamp(0.0, ctx.plot.w),
                b.y1.clamp(0.0, ctx.plot.h),
                b.x2.clamp(0.0, ctx.plot.w),
                b.y2.clamp(0.0, ctx.plot.h),
            ));
        }
        if let Some(t) = &p.text {
            add(Bounds::new(t.x, t.y, t.x + t.w, t.y + t.h));
        }
    }
    union
}

/// The right edge of annotation text past the plot, else `0.0`: a side legend
/// anchors beyond it.
pub fn text_x2(ctx: &AnnotationCtx) -> f64 {
    bounds(ctx).map_or(0.0, |b| b.x2)
}

/// One plane as nodes. Call with `Background` after the grid and before any
/// data mark; with `Foreground` after the value labels and before the axes.
/// `Background` must come before `Foreground` (it emits the clipPath).
/// Set `ctx.occupied` before the `Foreground` call: halo `auto` tests it and
/// text moves off it.
pub fn nodes(ctx: &AnnotationCtx, plane: Plane) -> Result<Vec<scene::Node>, Diagnostic> {
    emit::nodes(ctx, &resolve(ctx)?, plane)
}

/// The whole days that bound every date the annotations place on x.
pub fn x_day_bounds(annotations: &[Annotation]) -> impl Iterator<Item = i64> + '_ {
    annotations.iter().flat_map(Annotation::x_days).flat_map(|d| [d.floor() as i64, d.ceil() as i64])
}

/// ISO dates of the earliest and latest annotation day on x that fall before
/// or after every day in `data` (the days of the axis's dates): they extend a bucket scaffold as data
/// does. A range's exclusive end adds none by itself. A day inside the data maps onto its existing bucket and adds none.
pub fn x_dates_outside(data: &[f64], annotations: &[Annotation]) -> Vec<String> {
    let (Some(lo), Some(hi)) = (data.iter().copied().reduce(f64::min), data.iter().copied().reduce(f64::max)) else {
        return Vec::new();
    };
    let days: Vec<i64> = annotations
        .iter()
        .flat_map(Annotation::x_covered_days)
        .flat_map(|d| [d.floor() as i64, d.ceil() as i64])
        .collect();
    let before = days.iter().copied().filter(|&d| (d as f64) < lo.floor()).min();
    let after = days.iter().copied().filter(|&d| d as f64 > hi).max();
    [before, after]
        .into_iter()
        .flatten()
        .map(|d| {
            let (y, m, d) = crate::families::temporal::civil_from_days(d);
            format!("{y:04}-{m:02}-{d:02}")
        })
        .collect()
}

/// A temporal x extent in whole days, widened to every date the annotations
/// place on x: anchors join the axis domain as data does.
pub fn widen_extent(extent: (f64, f64), annotations: &[Annotation]) -> (f64, f64) {
    x_day_bounds(annotations).map(|d| d as f64).fold(extent, |(lo, hi), d| (lo.min(d), hi.max(d)))
}

/// The `axis` y extent widened to every number the annotations on that scale
/// place on y: those drawn on `panel` when an independent scale fits one, else
/// every annotation's. A log extent takes only the positive ones; the map
/// refuses the rest by name when it draws them.
pub fn widen_y(extent: (f64, f64), annotations: &[Annotation], axis: YAxis, panel: Option<&str>, log: bool) -> (f64, f64) {
    annotations
        .iter()
        .filter(|a| panel.is_none_or(|p| a.panel.as_deref().is_none_or(|own| own == p)))
        .flat_map(|a| a.y_numbers(axis))
        .filter(|&n| !log || n > 0.0)
        .fold(extent, |(lo, hi), n| (lo.min(n), hi.max(n)))
}

/// A quantitative x extent widened to every number the annotations place on x.
pub fn widen_numbers(extent: (f64, f64), annotations: &[Annotation]) -> (f64, f64) {
    annotations.iter().flat_map(Annotation::x_numbers).fold(extent, |(lo, hi), n| (lo.min(n), hi.max(n)))
}

/// Entries a numeric point scale gains for annotations past its ends: stepping
/// from each end by the data's median gap, then the anchor itself, so spacing
/// stays proportional. Ascending, unique, as the text a domain entry carries.
/// Numbers inside the span interpolate and add none; more than `MAX_STEPS` in
/// one direction is an error.
pub fn x_numbers_outside(domain: &[String], annotations: &[Annotation]) -> Result<Vec<String>, String> {
    const MAX_STEPS: usize = 500;
    let mut numbers: Vec<f64> = domain.iter().filter_map(|v| v.parse().ok()).collect();
    numbers.sort_by(f64::total_cmp);
    numbers.dedup();
    let (Some(&lo), Some(&hi)) = (numbers.first(), numbers.last()) else { return Ok(Vec::new()) };
    let mut gaps: Vec<f64> = numbers.windows(2).map(|w| w[1] - w[0]).collect();
    gaps.sort_by(f64::total_cmp);
    let step = gaps.get(gaps.len() / 2).copied();
    let anchors: Vec<f64> = annotations.iter().flat_map(Annotation::x_numbers).collect();
    let reach = |edge: f64, far: f64, sign: f64| -> Result<Vec<f64>, String> {
        let Some(step) = step else { return Ok(vec![far]) };
        let count = ((far - edge).abs() / step).ceil() as usize;
        if count > MAX_STEPS {
            return Err(format!("an annotation at x {far} is {count} steps past the data; at most {MAX_STEPS}"));
        }
        let at = |k: usize| ((edge + sign * step * k as f64) * 1e9).round() / 1e9;
        Ok((1..count).map(at).chain([far]).collect())
    };
    let mut out = Vec::new();
    if let Some(far) = anchors.iter().copied().filter(|&n| n < lo).reduce(f64::min) {
        out.extend(reach(lo, far, -1.0)?);
    }
    if let Some(far) = anchors.iter().copied().filter(|&n| n > hi).reduce(f64::max) {
        out.extend(reach(hi, far, 1.0)?);
    }
    out.sort_by(f64::total_cmp);
    Ok(out.into_iter().map(|n| model::Scalar::Number(n).text()).collect())
}

/// The plot edges an arm reserved room past for annotation text.
#[derive(Clone, Copy, Default)]
pub struct Reserved {
    pub top: bool,
    pub right: bool,
}

/// Refuses when a fixed-margin arm would draw ink outside the plot, past an
/// edge it did not reserve.
pub fn refuse_margin_ink(ctx: &AnnotationCtx, reserved: Reserved) -> Result<(), Diagnostic> {
    const EPS: f64 = 1e-9;
    for p in resolve(ctx)? {
        let Some(t) = &p.text else { continue };
        if t.x < -EPS
            || (!reserved.top && t.y < -EPS)
            || (!reserved.right && t.x + t.w > ctx.plot.w + EPS)
            || t.y + t.h > ctx.plot.h + EPS
        {
            return Err(Diagnostic::new(
                &crate::codes::ERR_ANNOTATION_MARGIN_UNSUPPORTED,
                [
                    ("chart_id", serde_json::Value::String(ctx.chart_id.to_string())),
                    ("index", serde_json::Value::from(p.index)),
                ],
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::model::tests::contract;
    use super::model::{Anchor, Scalar};
    use super::text::tests::fonts;
    use super::*;
    use crate::diagnostic::collect;
    use crate::families::cartesian::BandGeometry;
    use crate::time_axis::date_text_day;

    fn day(s: &str) -> f64 {
        date_text_day(s).unwrap()
    }

    /// A 200x100 plot at (30, 20) on a dated x and a 0..40M y.
    fn ctx<'a>(anns: &'a [Annotation], fonts: &'a FontBook) -> AnnotationCtx<'a> {
        AnnotationCtx {
            annotations: anns,
            chart_id: "rev",
            panel: None,
            plot: PlotRect { x: 30.0, y: 20.0, w: 200.0, h: 100.0 },
            reach: Reach::default(),
            x: AxisMap::Time { extent: (day("2024-01-01"), day("2024-12-31")), range: (0.0, 200.0) },
            y: AxisMap::Linear { domain: (0.0, 40_000_000.0), range: (100.0, 0.0) },
            layer_y: vec![AxisMap::Linear { domain: (0.0, 20.0), range: (100.0, 0.0) }],
            orientation: Orientation::Vertical,
            fonts,
            canvas: "#fafafa",
            clip_id: "rev-ann0".into(),
            occupied: Vec::new(),
        }
    }

    /// A rule at y on the base scale or on `layers[0]`'s.
    fn rule_at(y: f64, axis: YAxis) -> Annotation {
        let mut rule = contract().into_iter().find(|a| a.kind == model::Kind::Rule && a.y.is_some()).unwrap();
        rule.y = Some(Anchor::Value { value: Scalar::Number(y) });
        rule.y_axis = axis;
        rule
    }

    #[test]
    fn a_scale_widens_only_to_the_annotations_that_read_it() {
        let anns = [rule_at(90.0, YAxis::Layer(0)), rule_at(50.0, YAxis::Base)];
        assert_eq!(widen_y((0.0, 10.0), &anns, YAxis::Layer(0), None, false), (0.0, 90.0));
        assert_eq!(widen_y((0.0, 10.0), &anns, YAxis::Base, None, false), (0.0, 50.0));
        assert_eq!(widen_y((0.0, 10.0), &anns, YAxis::Layer(1), None, false), (0.0, 10.0));
    }

    /// The contract's annotations that fit the dated test chart.
    fn dated() -> Vec<Annotation> {
        contract().into_iter().filter(|a| [0, 1, 3, 4, 7, 11].contains(&a.index)).collect()
    }

    fn painted(anns: &[Annotation], plane: Plane) -> String {
        painted_over(anns, plane, &[])
    }

    fn painted_over(anns: &[Annotation], plane: Plane, under: &[DataMark]) -> String {
        let fonts = fonts();
        let c = AnnotationCtx { occupied: under.to_vec(), ..ctx(anns, &fonts) };
        print::print(&nodes(&c, plane).unwrap())
    }

    fn rule_x(value: Scalar) -> Annotation {
        let mut a = contract().into_iter().find(|a| a.index == 0).unwrap();
        a.x = Some(Anchor::Value { value });
        a
    }

    #[test]
    fn numbers_past_the_domain_ends_join_it_in_steps_and_inner_ones_do_not() {
        let domain: Vec<String> = ["0", "1", "2"].map(String::from).to_vec();
        let anns = [Scalar::Number(5.0), Scalar::Number(1.5), Scalar::Number(-2.0), Scalar::Number(5.0)].map(rule_x);
        assert_eq!(x_numbers_outside(&domain, &anns).unwrap(), ["-2", "-1", "3", "4", "5"]);
        let far = [Scalar::Number(1e6)].map(rule_x);
        assert!(x_numbers_outside(&domain, &far).unwrap_err().contains("steps past the data"));
    }

    fn days(domain: &[String]) -> Vec<f64> {
        domain.iter().map(|d| date_text_day(d).unwrap()).collect()
    }

    #[test]
    fn only_dates_past_the_data_extend_it() {
        let domain: Vec<String> = ["2024-01-01", "2024-06-01", "2025-12-01"].map(String::from).to_vec();
        let inside = [Scalar::Date("2024-06-01".into()), Scalar::Date("2025-12-01".into())].map(rule_x);
        assert!(x_dates_outside(&days(&domain), &inside).is_empty());
        let anns = [Scalar::Date("2030-01-01".into()), Scalar::Date("2020-03-05".into()), Scalar::Date("2024-01-01".into())]
            .map(rule_x);
        assert_eq!(x_dates_outside(&days(&domain), &anns), ["2020-03-05", "2030-01-01"]);
        assert!(x_dates_outside(&days(&domain), &[]).is_empty());
    }

    fn band_x(from: &str, to: &str) -> Annotation {
        let mut a = contract().into_iter().find(|a| a.index == 0).unwrap();
        a.x = Some(Anchor::Range { from: Some(Scalar::Date(from.into())), to: Some(Scalar::Date(to.into())) });
        a
    }

    /// The monthly bucket scaffold the bar and line families build from the data and the annotations.
    fn monthly_scaffold(domain: &[&str], anns: &[Annotation]) -> Vec<String> {
        use crate::families::temporal::{complete_buckets, TimeUnit};
        let domain: Vec<String> = domain.iter().map(|d| d.to_string()).collect();
        let widened: Vec<String> = domain.iter().cloned().chain(x_dates_outside(&days(&domain), anns)).collect();
        complete_buckets(&widened, TimeUnit::YearMonth, 1)
    }

    #[test]
    fn a_range_end_is_exclusive_and_alone_adds_no_bucket() {
        let data = ["2026-09-01", "2026-10-01"];
        let last = |anns: &[Annotation]| monthly_scaffold(&data, anns).pop().unwrap();
        assert_eq!(last(&[band_x("2026-10-01", "2026-11-01")]), "2026-10-01");
        assert_eq!(last(&[band_x("2026-10-01", "2027-02-01")]), "2027-01-01");
        assert_eq!(last(&[band_x("2026-10-01", "2027-02-15")]), "2027-02-01");
        assert_eq!(monthly_scaffold(&data, &[band_x("2026-08-15", "2026-10-01")])[0], "2026-08-01");
    }

    #[test]
    fn a_range_end_still_widens_the_continuous_extent() {
        let anns = [band_x("2026-10-01", "2026-11-01")];
        let hi = day("2026-11-01");
        assert_eq!(widen_extent((day("2026-05-01"), day("2026-10-01")), &anns), (day("2026-05-01"), hi));
    }

    #[test]
    fn a_point_date_past_the_data_still_adds_its_bucket() {
        assert_eq!(monthly_scaffold(&["2026-09-01", "2026-10-01"], &[rule_x(Scalar::Date("2026-11-01".into()))]).pop().unwrap(), "2026-11-01");
    }

    #[test]
    fn background_paints_the_clip_and_its_own_plane_only() {
        let svg = painted(&dated(), Plane::Background);
        assert!(svg.starts_with("<clipPath id=\"rev-ann0\"><rect x=\"0\" y=\"0\" width=\"200\" height=\"100\"/></clipPath>"));
        assert!(svg.contains("<g class=\"annotations annotations-background\" transform=\"translate(30,20)\">"));
        assert!(!svg.contains("annotations-foreground"));
        assert_eq!(svg.matches("<rect x=").count(), 3, "the clip rect and two bands");
    }

    #[test]
    fn every_text_paints_in_the_foreground_whatever_its_plane() {
        let anns = dated();
        let fonts = fonts();
        let c = ctx(&anns, &fonts);
        let placed = resolve(&c).unwrap();
        let texts = placed.iter().filter(|p| p.text.is_some()).count();
        assert!(placed.iter().any(|p| p.plane == Plane::Background && p.text.is_some()), "a band with text");
        assert!(!painted(&anns, Plane::Background).contains("<text"));
        assert_eq!(painted(&anns, Plane::Foreground).matches("<g class=\"annotations-text\">").count(), 1);
        assert_eq!(painted(&anns, Plane::Foreground).matches("role=\"graphics-symbol\"").count(), texts);
    }

    #[test]
    fn foreground_paints_marks_then_text_and_no_second_clip() {
        let svg = painted(&dated(), Plane::Foreground);
        assert!(!svg.contains("<clipPath"));
        let marks = svg.find("<g class=\"annotations-marks\" clip-path=\"url(#rev-ann0)\">").unwrap();
        let text = svg.find("<g class=\"annotations-text\">").unwrap();
        assert!(marks < text);
        assert!(!svg[text..].contains("clip-path"), "text is never clipped");
        assert!(svg[marks..text].contains("<line ") && !svg[marks..text].contains("<text"));
    }

    #[test]
    fn connectors_and_enclosures_paint_outside_the_plot_clip() {
        let svg = painted(&dated(), Plane::Foreground);
        let marks = svg.find("<g class=\"annotations-marks\" clip-path=").unwrap();
        let links = svg.find("<g class=\"annotations-links\">").expect("an unclipped links group");
        let text = svg.find("<g class=\"annotations-text\">").unwrap();
        assert!(marks < links && links < text);
        let arrowhead = "Z\" fill=\"#3b6fd4\"";
        assert!(!svg[marks..links].contains(arrowhead), "no arrowhead inside the clip");
        assert!(svg[links..text].contains(arrowhead), "the arrowhead is in the links group");
    }

    #[test]
    fn a_foreground_only_chart_still_gets_its_clip_from_background() {
        let anns: Vec<Annotation> = contract().into_iter().filter(|a| a.index == 0).collect();
        assert_eq!(painted(&anns, Plane::Background), "<clipPath id=\"rev-ann0\"><rect x=\"0\" y=\"0\" width=\"200\" height=\"100\"/></clipPath>");
    }

    #[test]
    fn one_accessible_node_per_annotation_and_decoration_hidden() {
        let anns = dated();
        let svg = painted(&anns, Plane::Background) + &painted(&anns, Plane::Foreground);
        assert_eq!(svg.matches("role=\"graphics-symbol\"").count(), anns.len());
        assert_eq!(svg.matches("data-dct-role=\"annotation\"").count(), anns.len());
        assert!(svg.contains("aria-label=\"FY24 goal\""));
        assert!(svg.contains("aria-roledescription=\"annotation\""));
        // The connector and its arrow are decoration.
        assert!(svg.contains(" aria-hidden=\"true\"/><path d=\"M"));
    }

    #[test]
    fn halo_is_one_outlined_copy_faded_as_a_group_under_the_text() {
        let svg = painted(&dated(), Plane::Foreground);
        let halo = "<g opacity=\"0.75\" aria-hidden=\"true\">";
        let outlined = "fill=\"#fafafa\" stroke=\"#fafafa\" stroke-width=\"6\" stroke-linejoin=\"round\">FY24 goal</text></g>";
        assert!(svg.contains(halo) && svg.contains(outlined), "{svg}");
        assert!(svg.contains("fill=\"#757575\">FY24 goal</text>") || svg.matches(">FY24 goal</text>").count() == 2);
        assert!(!svg.contains("paint-order"));
        for (halo, opacity) in [(0.0, 0.75), (3.0, 0.0)] {
            let mut anns = dated();
            for a in &mut anns {
                a.style.text.halo = halo;
                a.style.text.halo_opacity = opacity;
            }
            assert!(!painted(&anns, Plane::Foreground).contains("stroke-linejoin"));
        }
    }

    const HALOED: &str = "stroke-width=\"6\" stroke-linejoin=\"round\">FY24 goal</text>";

    /// The dated annotations with halo `auto`, and the box of the one whose text reads `label`.
    fn over_marks(label: &str) -> (Vec<Annotation>, Bounds) {
        let mut anns = dated();
        for a in &mut anns {
            a.style.text.halo_over_marks = true;
        }
        let fonts = fonts();
        let c = ctx(&anns, &fonts);
        let placed = resolve(&c).unwrap();
        let b = placed.iter().find(|p| p.label == label).and_then(|p| p.text.as_ref()).unwrap();
        let bounds = Bounds::new(b.x, b.y, b.x + b.w, b.y + b.h);
        (anns, bounds)
    }

    #[test]
    fn halo_auto_draws_only_on_text_over_a_data_mark() {
        let (anns, b) = over_marks("FY24 goal");
        assert!(!painted_over(&anns, Plane::Foreground, &[]).contains("stroke-linejoin"), "open ground");
        // As wide as the plot, so the text cannot slide off it.
        let bar = DataMark::Box(Bounds::new(0.0, b.y1 + 2.0, 200.0, b.y2 + 40.0));
        let svg = painted_over(&anns, Plane::Foreground, &[bar]);
        assert_eq!(svg.matches("stroke-linejoin").count(), 1, "{svg}");
        assert!(svg.contains(HALOED), "{svg}");
        let beside = DataMark::Box(Bounds::new(b.x2 + 1.0, b.y1, b.x2 + 9.0, b.y2));
        assert!(!painted_over(&anns, Plane::Foreground, &[beside]).contains(HALOED));
    }

    #[test]
    fn paint_warns_for_a_note_that_left_its_side_without_a_connector() {
        let mut anns: Vec<Annotation> = contract().into_iter().filter(|a| a.index == 7).collect();
        anns[0].connector = model::Connector::None;
        anns[0].style.text.position.auto_edge = true;
        anns[0].style.text.dy = 0.0;
        let fonts = fonts();
        // Everything above the anchor (18.4M at y 54) is taken.
        let c = AnnotationCtx { occupied: vec![DataMark::rect(0.0, 0.0, 200.0, 50.0)], ..ctx(&anns, &fonts) };
        let (_, warnings) = collect(|| nodes(&c, Plane::Foreground).unwrap());
        assert_eq!(warnings.iter().map(|w| w.code).collect::<Vec<_>>(), ["WARN-ANNOTATION-NOTE-MOVED"]);
    }

    #[test]
    fn halo_auto_tests_an_area_fill_by_its_outline_not_its_box() {
        let (anns, b) = over_marks("FY24 goal");
        let base = b.y2 + 60.0;
        let area = |hi: Vec<(f64, f64)>| DataMark::Area { lo: hi.iter().map(|p| (p.0, base)).collect(), hi };
        let under = area(vec![(0.0, b.y1 - 10.0), (200.0, b.y1 - 10.0)]);
        assert!(painted_over(&anns, Plane::Foreground, &[under]).contains(HALOED));
        // Its box covers the text, but the fill only climbs past it far to the right.
        let ramp = area(vec![(b.x1 - 10.0, b.y2 + 10.0), (b.x2 + 600.0, b.y1 - 40.0)]);
        assert!(!painted_over(&anns, Plane::Foreground, &[ramp]).contains(HALOED));
    }

    #[test]
    fn a_baked_dash_is_drawn_as_given_and_none_is_solid() {
        let mut anns: Vec<Annotation> = contract().into_iter().filter(|a| a.index == 0).collect();
        anns[0].style.rule.as_mut().unwrap().stroke.dasharray = Some("7 2".to_string());
        assert!(painted(&anns, Plane::Foreground).contains("stroke-dasharray=\"7 2\""));
        anns[0].style.rule.as_mut().unwrap().stroke.dasharray = None;
        assert!(!painted(&anns, Plane::Foreground).contains("stroke-dasharray"));
    }

    #[test]
    fn halo_inside_an_enclosing_rect_uses_the_rect_fill() {
        let mut anns: Vec<Annotation> = contract().into_iter().filter(|a| a.index == 9).collect();
        anns[0].x = Some(Anchor::Value { value: Scalar::Date("2024-06-01".into()) });
        anns[0].connector = model::Connector::None;
        let fill = anns[0].style.rect().fill.clone();
        let svg = painted(&anns, Plane::Foreground);
        assert_ne!(fill, "#fafafa");
        assert!(svg.contains(&format!("fill=\"{fill}\" stroke=\"{fill}\" stroke-width=\"6\"")), "{svg}");
        assert!(!svg.contains("stroke=\"#fafafa\""));
    }

    #[test]
    fn items_follow_list_order_within_a_plane() {
        let mut anns = dated();
        anns.reverse();
        let svg = painted(&anns, Plane::Foreground);
        let goal = svg.find("FY24 goal").unwrap();
        let pricing = svg.find("Pricing change").unwrap();
        assert!(goal < pricing);
    }

    #[test]
    fn a_span_that_names_nothing_paints_no_element() {
        let mut anns: Vec<Annotation> = contract().into_iter().filter(|a| a.index == 5).collect();
        anns[0].x = Some(Anchor::Category { value: "West".into() });
        let fonts = fonts();
        let mut c = ctx(&anns, &fonts);
        c.x = AxisMap::Band {
            domain: vec!["East".into()],
            geometry: BandGeometry::new(1, 0.0, 200.0, 0.0, 0.0),
            days: None,
            range: (0.0, 200.0),
        };
        let (res, warnings) = collect(|| nodes(&c, Plane::Background));
        assert!(res.unwrap().is_empty());
        assert_eq!(warnings[0].code, "WARN-ANNOTATION-NOTHING-DRAWN");
        assert_eq!(warnings[0].params["chart_id"], "rev");
    }

    #[test]
    fn a_trend_area_is_its_hi_edge_then_the_reversed_lo_edge() {
        let anns: Vec<Annotation> = contract().into_iter().filter(|a| a.index == 11).collect();
        let svg = painted(&anns, Plane::Foreground);
        let area = svg.split("<path d=\"").nth(1).unwrap().split('"').next().unwrap();
        assert!(area.starts_with('M') && area.ends_with('Z'));
        assert_eq!(area.matches('M').count(), 1);
        assert!(area.contains("L"));
    }

    fn margin_text() -> Vec<Annotation> {
        contract().into_iter().filter(|a| a.index == 0).collect()
    }

    #[test]
    fn margin_text_widens_the_bounds_by_exactly_its_box() {
        let mut anns = margin_text();
        anns[0].y = Some(Anchor::Value { value: Scalar::Number(40_000_000.0) });
        let fonts = fonts();
        let c = ctx(&anns, &fonts);
        let b = bounds(&c).unwrap();
        let p = &resolve(&c).unwrap()[0];
        let t = p.text.as_ref().unwrap();
        // The rule sits on the plot top; its text above it is the only ink outside.
        assert_eq!(b, Bounds::new(0.0, t.y, 200.0, 0.0));
        assert!((t.y + t.h + place::GAP).abs() < 1e-9);
    }

    #[test]
    fn inside_text_does_not_widen_the_bounds() {
        let mut anns = margin_text();
        anns[0].style.text.position.overlay = true;
        let fonts = fonts();
        let c = ctx(&anns, &fonts);
        let b = bounds(&c).unwrap();
        assert!(b.x1 >= 0.0 && b.y1 >= 0.0 && b.x2 <= 200.0 && b.y2 <= 100.0);
        assert!(bounds(&ctx(&[], &fonts)).is_none());
    }

    #[test]
    fn refuse_margin_ink_names_the_annotation_whose_text_leaves_the_plot() {
        let mut anns = margin_text();
        anns[0].y = Some(Anchor::Value { value: Scalar::Number(40_000_000.0) });
        let fonts = fonts();
        let d = refuse_margin_ink(&ctx(&anns, &fonts), Reserved::default()).unwrap_err();
        assert_eq!(d.code, "ERR-ANNOTATION-MARGIN-UNSUPPORTED");
        assert_eq!((d.params["chart_id"].as_str(), d.params["index"].as_u64()), (Some("rev"), Some(0)));
        let mut inside = margin_text();
        inside[0].style.text.position.overlay = true;
        refuse_margin_ink(&ctx(&inside, &fonts), Reserved::default()).unwrap();
    }

    #[test]
    fn paint_surfaces_a_refusal() {
        let mut anns = margin_text();
        anns[0].y = Some(Anchor::Category { value: "East".into() });
        let fonts = fonts();
        let c = ctx(&anns, &fonts);
        assert!(bounds(&c).is_none());
        assert_eq!(nodes(&c, Plane::Background).unwrap_err().code, "ERR-ANNOTATION-PAYLOAD");
    }
}
