//! A drawn plot body's family arms: the mark kind picks the gutter law its frame is laid out under
//! and the painter that draws its marks. One arm per kind; a family's lane adds its own.

use bind::formats::locale::active_number_locale;
use bind::bound::{self, Family, Mark};
use board_model::style::board::EmojiMode;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use layout::geom::Size;
use layout::plot::bins::bins;
use layout::plot::points::points;
use layout::plot::value_axis::GutterRule;
use layout::plot::{draw, empty, plan, Env, Heading};
use measure::shaped::FontBook;
use scene::Node;

type Painter = fn(&bound::Plane, Size) -> Result<Vec<Node>, Diagnostic>;

fn family(mark: &Mark) -> Result<(GutterRule, Painter), Diagnostic> {
    match mark {
        Mark::Points { .. } => Ok((GutterRule::BAR_FIT, |plot, size| {
            let layer = &plot.layers()[0];
            points(plot, 0, layer, &layer.panels[0], size, "marks")
        })),
        Mark::Bins => Ok((GutterRule::BAR_FIT, |plot, size| {
            let layer = &plot.layers()[0];
            bins(plot, 0, layer, &layer.panels[0], size, "marks")
        })),
        Mark::Bars => Err(Diagnostic::refused(refuse!("drawing a plot of bars is not supported"))),
        Mark::Lines => Err(Diagnostic::refused(refuse!("drawing a plot of lines is not supported"))),
    }
}

/// The chart's root `<svg>` for a cell of `cell`: planned by layout's cartesian frame under its
/// family's gutter law, its marks painted by the family. A title is laid out by the frame in the
/// width-tier face of `placed`'s chart title slot (`layout::title::chart_title`). A chart bound
/// over no rows is its title, its canvas and "No data": a subtitle on it is refused.
pub(crate) fn draw_plot(chart: &bound::Chart, chart_id: &str, cell: Size, book: &FontBook, placed: &Style, emoji: &EmojiMode, card_padding: f64) -> Result<Vec<Node>, Diagnostic> {
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
    let (rule, paint) = family(&layer.mark)?;
    let locale = active_number_locale();
    let env = Env { book, locale: &locale, emoji, rule, card_padding };
    let heading = chart.title.as_ref().map(|_| Heading { chart_id, style: placed });
    draw(&**plot, &plan(chart, heading.as_ref(), cell, &env)?, paint)
}
