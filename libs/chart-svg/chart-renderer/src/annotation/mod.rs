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
pub use model::Annotation;

use crate::diagnostic::Diagnostic;
use crate::text::FontBook;
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

/// Paints one plane. Call with `Background` after the grid and before any
/// data mark; with `Foreground` after the value labels and before the axes.
/// `Background` must be called before `Foreground` (it emits the clipPath).
pub fn paint(ctx: &AnnotationCtx, plane: Plane, svg: &mut String) -> Result<(), Diagnostic> {
    emit::paint(ctx, &resolve(ctx)?, plane, svg)
}

/// Refuses when a fixed-margin arm would draw ink outside the plot.
pub fn refuse_margin_ink(ctx: &AnnotationCtx) -> Result<(), Diagnostic> {
    const EPS: f64 = 1e-9;
    for p in resolve(ctx)? {
        let Some(t) = &p.text else { continue };
        if t.x < -EPS || t.y < -EPS || t.x + t.w > ctx.plot.w + EPS || t.y + t.h > ctx.plot.h + EPS {
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
    use crate::families::temporal::date_text_day;

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
            x: AxisMap::Time { extent: (day("2024-01-01"), day("2024-12-31")), range: (0.0, 200.0) },
            y: AxisMap::Linear { domain: (0.0, 40_000_000.0), range: (100.0, 0.0) },
            layer_y: vec![AxisMap::Linear { domain: (0.0, 20.0), range: (100.0, 0.0) }],
            orientation: Orientation::Vertical,
            fonts,
            canvas: "#fafafa",
            clip_id: "rev-ann0".into(),
        }
    }

    /// The contract's annotations that fit the dated test chart.
    fn dated() -> Vec<Annotation> {
        contract().into_iter().filter(|a| [0, 1, 3, 4, 7, 11].contains(&a.index)).collect()
    }

    fn painted(anns: &[Annotation], plane: Plane) -> String {
        let fonts = fonts();
        let c = ctx(anns, &fonts);
        let mut svg = String::new();
        paint(&c, plane, &mut svg).unwrap();
        svg
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
    fn halo_text_strokes_in_the_canvas_color_under_the_fill() {
        let svg = painted(&dated(), Plane::Foreground);
        assert!(svg.contains("stroke=\"#fafafa\" stroke-width=\"6\" stroke-linejoin=\"round\" paint-order=\"stroke\">FY24 goal</text>"));
        let mut anns = dated();
        for a in &mut anns {
            a.style.text.halo = 0.0;
        }
        assert!(!painted(&anns, Plane::Foreground).contains("paint-order"));
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
        let mut svg = String::new();
        let (res, warnings) = collect(|| paint(&c, Plane::Background, &mut svg));
        res.unwrap();
        assert_eq!(svg, "");
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
        let d = refuse_margin_ink(&ctx(&anns, &fonts)).unwrap_err();
        assert_eq!(d.code, "ERR-ANNOTATION-MARGIN-UNSUPPORTED");
        assert_eq!((d.params["chart_id"].as_str(), d.params["index"].as_u64()), (Some("rev"), Some(0)));
        let mut inside = margin_text();
        inside[0].style.text.position.overlay = true;
        refuse_margin_ink(&ctx(&inside, &fonts)).unwrap();
    }

    #[test]
    fn paint_surfaces_a_refusal() {
        let mut anns = margin_text();
        anns[0].y = Some(Anchor::Category { value: "East".into() });
        let fonts = fonts();
        let c = ctx(&anns, &fonts);
        assert!(bounds(&c).is_none());
        assert_eq!(paint(&c, Plane::Background, &mut String::new()).unwrap_err().code, "ERR-ANNOTATION-PAYLOAD");
    }
}
