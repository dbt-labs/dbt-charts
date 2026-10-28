//! Bar layout: the card, plot, axes, marks, legends and annotations laid out in the cell and
//! returned as nodes, with the WARNs the layout raises. Every pixel decision is made by the panel
//! the chart's orientation and x scale pick.

use crate::common::{distinct_values, row_num};
use crate::diagnostic::Diagnostic;
use crate::layout::{IntrinsicWidth, Layout, Rect};
use crate::{Payload, codes};

use measure::{FontBook, Measure};
use scene::Node;
use serde_json::{Value, json};
use std::borrow::Cow;
use std::cell::RefCell;
use std::collections::HashSet;

use super::bind::Bound;
use super::continuous::{XLayers, render_vertical_continuous_x};
use super::horizontal::render_horizontal;
use super::vertical::render_vertical;
use super::{BarResolved, ContinuousWidth, MarkStyle, continuous_max_size, x_resolution};

/// A bar chart fills the width it is given: nothing to hug.
pub fn intrinsic(_bound: &Bound, _measure: &Measure) -> Result<Option<IntrinsicWidth>, Diagnostic> {
    Ok(None)
}

/// A bar chart draws in the slot sizing hands it: that slot's height is its natural height at any
/// width, and [`layout`] draws the cell it is given.
pub fn height_for_width(bound: &Bound, _width: f64, _measure: &Measure) -> Result<f64, Diagnostic> {
    Ok(bound.payload.height)
}

/// One plot in `cell`. A `multiples` chart is composed panel by panel at `render`'s string boundary.
pub fn layout(bound: &Bound, cell: Rect, measure: &Measure) -> Result<Layout, Diagnostic> {
    if bound.r.multiples.is_some() {
        return Err("bar: layout draws one plot; a multiples chart is composed by render".into());
    }
    let payload: Cow<Payload> = if (cell.width, cell.height) == (bound.payload.width, bound.payload.height) {
        Cow::Borrowed(&*bound.payload)
    } else {
        Cow::Owned(Payload { width: cell.width, height: cell.height, ..Payload::clone(&bound.payload) })
    };
    let (drawn, diagnostics) = crate::diagnostic::collect(|| draw(bound, &payload, measure.fonts));
    // A layout that fails has no `Layout` to carry what it found: those WARNs reach the sink, as
    // they did when the render wrote to it directly.
    let drawn = drawn.inspect_err(|_| diagnostics.iter().cloned().for_each(crate::diagnostic::warn))?;
    Ok(Layout {
        insets: drawn.canvas.insets_of(&drawn.data_box),
        data_box: drawn.data_box,
        nodes: vec![drawn.svg],
        diagnostics,
        title_pos: drawn.title_pos,
    })
}

/// The one plot of `bound` in the card `payload` describes.
fn draw(bound: &Bound, payload: &Payload, fonts: &FontBook) -> Result<Drawn, Diagnostic> {
    let r = &bound.r;
    match r.orientation.as_str() {
        "horizontal" => {
            Ok(render_horizontal(payload, fonts, r, &bound.wide_series, &bound.layers, bound.support_table.as_ref())?)
        }
        "vertical" => {
            // Quantitative-x bars compile to a LINEAR x scale (type_inference: an all-numeric x
            // field), not a band scale — production sizes the bars with
            // `continuous_bar_size_prop`'s clamp expression and pads the scale by the half-bar
            // reservation. That shape has its own closed-form arm; `None` means a sub-shape
            // outside its gate (see `render_vertical_continuous_x`) and falls through to the band
            // arm.
            if bound.support_table.is_none() {
                let x_layers = XLayers { all: &bound.layers, payload };
                if let Some(drawn) = render_vertical_continuous_x(payload, fonts, r, &x_layers)? {
                    warn_vertical_density(
                        payload.chart_id()?,
                        r,
                        &payload.rows,
                        payload.width,
                        1,
                        XScale::of(&x_resolution(r, &payload.rows)?.resolution),
                    )?;
                    return Ok(drawn);
                }
            }
            let (drawn, _, composed_done) =
                render_vertical(payload, fonts, r, bound.support_table.as_ref(), &bound.wide_series, &bound.layers)?;
            // Every strip shape takes a pad arm that draws its own rows.
            if bound.support_table.is_some() && !composed_done {
                return Err("bar: a support_table strip reached an arm that draws no rows".into());
            }
            Ok(drawn)
        }
        other => Err(format!("bar: orientation {other:?} is neither vertical nor horizontal").into()),
    }
}

/// `bar_band_width_too_narrow._MIN_BAND_WIDTH_PX`: below it a bar's fill is
/// swallowed by the border strokes bracketing it.
pub(super) const MIN_BAND_WIDTH_PX: f64 = 4.0;

/// The scale a vertical bar's x draws on, as the density warnings read it.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum XScale {
    Band,
    /// A linear scale over numeric x: no bands to subdivide or count.
    Numeric,
    /// A time axis that kept its calendar grain: production emits a `timeUnit`, so its
    /// offset channel still has bands to divide, though no band is counted.
    GrainedTemporal,
    /// A time axis with no grain: a continuous scale.
    ContinuousTemporal,
}

impl XScale {
    /// The scale the one x-type law resolved for the chart.
    pub(super) fn of(law: &crate::families::x_type::XResolution) -> Self {
        use crate::families::temporal::VegaType;
        use crate::families::x_type::Grain;
        match law.vl_type {
            VegaType::Quantitative => Self::Numeric,
            VegaType::Temporal => match law.grain {
                Grain::None => Self::ContinuousTemporal,
                Grain::Calendar(_) | Grain::Part(_) => Self::GrainedTemporal,
            },
            VegaType::Ordinal | VegaType::Nominal => Self::Band,
        }
    }
}

/// `WARN-BAR-BAND-WIDTH-TOO-NARROW` over a band scale: `extent` px split into
/// `distinct` bands, each split again across `series` grouped columns.
pub(super) fn warn_band_too_narrow(chart_id: &str, extent: f64, distinct: usize, series: usize) {
    if distinct == 0 {
        return;
    }
    let bar_width = extent / distinct as f64 / series as f64;
    if bar_width >= MIN_BAND_WIDTH_PX {
        return;
    }
    crate::diagnostic::warn(Diagnostic::new(
        &codes::WARN_BAR_BAND_WIDTH_TOO_NARROW,
        [
            ("bar_width", json!(bar_width)),
            ("chart_id", json!(chart_id)),
            ("distinct", json!(distinct)),
            ("min_band_width", json!(MIN_BAND_WIDTH_PX)),
            ("render_width", json!(extent)),
            ("series", json!(series)),
        ],
    ));
}

/// `WARN-BAR-BAND-WIDTH-TOO-NARROW` over a numeric x: the bar is wider than
/// the closest two x values are apart, so neighbors overlap. `min_band_width`
/// carries that step, and the `continuous` sink param tells the Python sink to
/// word it as production's continuous branch does.
pub(super) fn warn_continuous_bars_overlap(
    chart_id: &str,
    mark: &MarkStyle,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    render_width: f64,
) -> Result<(), String> {
    let mut xs: Vec<f64> = rows.iter().filter_map(|row| row_num(row, x_field)).collect();
    xs.sort_by(f64::total_cmp);
    xs.dedup();
    let (Some(&first), Some(&last)) = (xs.first(), xs.last()) else { return Ok(()) };
    if xs.len() < 2 || last - first <= 0.0 {
        return Ok(());
    }
    let reserved = mark.size.unwrap_or_else(|| continuous_max_size(mark));
    let usable = render_width - reserved;
    if usable <= 0.0 {
        return Ok(());
    }
    let step_px = xs.windows(2).map(|w| w[1] - w[0]).fold(f64::INFINITY, f64::min) * (usable / (last - first));
    let bar_width = ContinuousWidth::of(mark)?.at(step_px);
    if bar_width > step_px {
        crate::diagnostic::warn(
            Diagnostic::new(
                &codes::WARN_BAR_BAND_WIDTH_TOO_NARROW,
                [
                    ("bar_width", json!(bar_width)),
                    ("chart_id", json!(chart_id)),
                    ("distinct", json!(xs.len())),
                    ("min_band_width", json!(step_px)),
                    ("render_width", json!(render_width)),
                    ("series", json!(1)),
                ],
            )
            .with_sink_param("continuous", json!(true)),
        );
    }
    Ok(())
}

/// The density warnings of a vertical bar chart's x axis over `width` px:
/// production's detectors read the spec's own `width`, which is the card
/// width (or a facet's naive panel width), never the fitted plot.
pub(super) fn warn_vertical_density(
    chart_id: &str,
    r: &BarResolved,
    rows: &[serde_json::Map<String, Value>],
    width: f64,
    series: usize,
    x: XScale,
) -> Result<(), String> {
    match x {
        XScale::GrainedTemporal | XScale::ContinuousTemporal => Ok(()),
        XScale::Numeric => warn_continuous_bars_overlap(chart_id, &r.style.mark, rows, &r.x, width),
        XScale::Band => {
            warn_band_too_narrow(chart_id, width, distinct_values(rows, &r.x), series);
            Ok(())
        }
    }
}

/// A drawn panel: the card as a node, the plot box in it and where the title sits in that box.
pub(crate) struct Drawn {
    pub(crate) svg: Node,
    pub(crate) canvas: Rect,
    pub(crate) data_box: Rect,
    pub(crate) title_pos: Option<(f64, f64)>,
}

/// `nodes` under the plot-sized layer clip when `clipped`, the clip defined the first time the
/// chart asks for it.
pub(super) fn clipped_if(
    clipped: bool,
    defined: &RefCell<HashSet<String>>,
    plot: (f64, f64),
    chart_id: &str,
    nodes: Vec<Node>,
) -> Vec<Node> {
    if clipped {
        crate::families::layers::clipped_layer(&mut defined.borrow_mut(), plot, chart_id, nodes)
    } else {
        nodes
    }
}
