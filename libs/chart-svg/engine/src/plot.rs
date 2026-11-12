//! A drawn plot body's family arms: the mark kind picks the gutter law its frame is laid out under
//! and the painter that draws its marks. One arm per kind; a family's lane adds its own.

use bind::formats::locale::active_number_locale;
use bind::bound::{self, Family, Mark};
use board_model::style::board::EmojiMode;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use layout::geom::Size;
use layout::knockout::safe_scope;
use layout::plot::bars::bars;
use layout::plot::bins::bins;
use layout::plot::lines::{lines, Metrics};
use layout::plot::points::points;
use layout::plot::value_axis::GutterRule;
use layout::plot::{draw, empty, plan, Env, Heading, Painted};
use measure::shaped::FontBook;
use scene::Node;

type Painter<'a> = Box<dyn Fn(&bound::Plane, Size) -> Result<Painted, Diagnostic> + 'a>;

/// The gutter law, the x inset (a line's mark pad) and the painter of a plot whose one layer is `mark`.
fn family<'a>(plot: &bound::Plane, cell: Size, mark: &Mark, canvas: &'a str, scope: &'a str) -> Result<(GutterRule, Option<f64>, Painter<'a>), Diagnostic> {
    match mark {
        Mark::Points { .. } => Ok((GutterRule::BAR_FIT, None, Box::new(|plot, size| {
            let layer = &plot.layers()[0];
            points(plot, 0, layer, &layer.panels[0], size, "marks").map(|marks| Painted { marks, y_axis_at: None })
        }))),
        Mark::Bins => Ok((GutterRule::BAR_FIT, None, Box::new(|plot, size| {
            let layer = &plot.layers()[0];
            bins(plot, 0, layer, &layer.panels[0], size, "marks").map(|marks| Painted { marks, y_axis_at: None })
        }))),
        Mark::Bars => Ok((GutterRule::BAR_FIT, None, Box::new(move |plot, size| {
            let layer = &plot.layers()[0];
            bars(plot, 0, layer, &layer.panels[0], size, canvas, scope).map(|marks| Painted { marks, y_axis_at: None })
        }))),
        Mark::Lines => {
            let metrics = Metrics::of_plot(plot, cell.width)?;
            Ok((GutterRule::LINE_FIT, Some(metrics.pad), Box::new(move |plot, size| lines(plot, metrics, canvas, size))))
        }
    }
}

/// The chart's root `<svg>` for a cell of `cell`: planned by layout's cartesian frame under its
/// family's gutter law, its marks painted by the family. A title is laid out by the frame in the
/// width-tier face of `placed`'s chart title slot (`layout::title::chart_title`), and the warnings its width raised against it come back with the nodes. A chart bound
/// over no rows is its title, its canvas and "No data": a subtitle on it is refused.
pub(crate) fn draw_plot(chart: &bound::Chart, chart_id: &str, cell: Size, book: &FontBook, placed: &Style, emoji: &EmojiMode, card_padding: f64) -> Result<(Vec<Node>, Vec<Diagnostic>), Diagnostic> {
    let Ok(Family::Cartesian(cartesian)) = &chart.family else {
        return Err(diag::err_internal("a plot is drawn from a plot body"));
    };
    let bound::Cartesian::Plane(plot) = cartesian else {
        if chart.subtitle.is_some() {
            return Err(Diagnostic::refused(refuse!("a plot with a subtitle is not supported")));
        }
        let heading = chart.title.as_ref().map(|_| Heading { chart_id, style: placed });
        return empty::draw(chart, heading.as_ref(), cell, book, emoji, card_padding);
    };
    let [layer] = plot.layers() else {
        return Err(Diagnostic::refused(refuse!("drawing a plot of several mark layers is not supported")));
    };
    if layer.mark == Mark::Bars && (plot.series().len() != 1 || !plot.legends().is_empty()) {
        return Err(Diagnostic::refused(refuse!("drawing grouped bars is not supported")));
    }
    let Some(frame) = &chart.frame else {
        return Err(diag::err_internal("a plot carries its frame"));
    };
    let canvas = color::rgb01_to_hex(frame.canvas.0, frame.canvas.1, frame.canvas.2);
    let scope = safe_scope(chart_id);
    let (rule, x_inset, paint) = family(plot, cell, &layer.mark, &canvas, &scope)?;
    let locale = active_number_locale();
    let heading = chart.title.as_ref().map(|_| Heading { chart_id, style: placed });
    let env = Env { book, locale: &locale, emoji, rule, card_padding, x_inset };
    let planned = plan(chart, heading.as_ref(), cell, &env)?;
    Ok((draw(&**plot, &planned, paint)?, planned.diagnostics().to_vec()))
}
