//! The render engine (rust-render-bind-size-print spec § 2): a board, one run of its queries and the
//! caller's facts to an SVG and its report. bind decides what the data says, layout every box and
//! node, print the bytes; this crate sequences them and composes the board's frame.
//!
//! The text a board draws is measured in a later slice, so a board that needs it is refused by name.
//! What the spine's total `Style` and the host will own is a caller fact for now ([`EngineFacts`]),
//! each the one place that changes when it lands.
//!
//! `render_board(&Board, &ChartStyles, &RunResult, &FontBook, &mut impl EngineFacts)` returns a `Rendered`
//! (`svg` and `report`): bind, `layout::board::assemble::lay_out`, the board's frame composed on
//! nodes (`compose`), and one `print`. The board's language and time locale are open for all of it
//! (`scope`), and the nodes end in `layout::board::styles`' dedupe. Nothing in production calls it.
//!
//! The engine draws only what it models, and anything else is refused by name rather than
//! approximated: failed charts, KPI cards, callout cards, plain tables of text columns and
//! direct-mode pies and donuts (`slot`). `Slots` and layout's `Facts` are keyed by placement
//! `ItemId`, and each chart is drawn at every placement in the style of the body that places it.
//! A failed chart reports its error at each placement, as production does.

mod compose;
mod facts;
pub mod host;
pub mod plan;
mod plot;
mod scope;
mod slot;
mod styles;
mod texts;

pub use facts::EngineFacts;
pub use plan::plan;
pub use styles::chart_styles;

use bind::board::ChartStyles;
use bind::run::RunResult;
use board_model::board::Board;
use board_model::ids::{ChartId, ItemId};
use diagnostic::Diagnostic;
use layout::geom::Rect;
use measure::shaped::FontBook;

/// A rendered board: its SVG and what it reports about the render.
#[derive(Debug, Clone, PartialEq)]
pub struct Rendered {
    pub svg: String,
    /// Each drawn chart's own `<svg>`, keyed as `Report.boxes` is: see [`host::chart_svgs`].
    pub charts: Vec<(String, String)>,
    pub report: Report,
}

/// What a render says beside its picture.
#[derive(Debug, Clone, PartialEq)]
pub struct Report {
    /// Each failed placement's error, the execute error or the one bind found in the data (a chart
    /// placed twice fails, and is reported, at both, as production reports it), then the layout's
    /// own findings, then the data warnings bind raised (production adds its restated-key warnings
    /// last too).
    pub diagnostics: Vec<Diagnostic>,
    /// Every drawn chart's cell, in the frame sizing lays the board out in: its placement in its
    /// board's layout area plus each enclosing nested board's own, with none of the chrome the
    /// composer adds when it draws.
    pub boxes: Vec<(ItemId, ChartId, Rect)>,
    /// The board's sized width and height (the drawn SVG is taller when a card outgrows its slot).
    pub width: f64,
    pub height: f64,
}

/// Renders `board` over `run`: bind, layout, then the board's frame, printed once. `book` is the
/// host's font book, built once from the font bytes at init: the engine builds no font book of its own.
pub fn render_board(board: &Board, styles: &ChartStyles, run: &RunResult, book: &FontBook, facts: &mut impl EngineFacts) -> Result<Rendered, Diagnostic> {
    scope::of(board.regional.as_ref())?.within(|| render_scoped(board, styles, run, book, facts))
}

fn render_scoped(board: &Board, styles: &ChartStyles, run: &RunResult, book: &FontBook, facts: &mut impl EngineFacts) -> Result<Rendered, Diagnostic> {
    let filled = texts::filled(board, styles, run, &*facts)?;
    let board = &filled;
    let mut bound = bind::bind(board, styles, run)?;
    // A chart the slots cannot place is that chart's refusal, as one the bind cannot read is: the
    // rest of the board is placed without it.
    while let Err(refusal) = slot::plan(board, styles, &bound, &*facts).map(|_| ()) {
        refuse_chart(board, styles, &mut bound, refusal)?;
    }
    let slots = slot::plan(board, styles, &bound, &*facts)?;
    let laid = layout::board::assemble::lay_out(board, bound.drawn(), &mut slots.facts(&*facts, book))?;
    let mut diagnostics: Vec<Diagnostic> = bound
        .charts()
        .values()
        .filter_map(|chart| match &chart.family {
            Err(failure) => Some(failure.diagnostic.clone()),
            _ => None,
        })
        .collect();
    diagnostics.extend(laid.diagnostics.iter().cloned());
    diagnostics.extend(bound.diagnostics.iter().cloned());
    let boxes = compose::boxes(board, &laid, &slots, bound.drawn());
    let (mut nodes, drawn) = compose::board(board, &bound, &slots, &laid, run, &*facts, book)?;
    diagnostics.extend(drawn);
    layout::board::styles::dedupe_repeated_style_rules(&mut nodes)?;
    Ok(Rendered {
        charts: host::chart_svgs(&nodes),
        svg: print::print(&nodes),
        report: Report { diagnostics, boxes, width: laid.width, height: laid.height },
    })
}

/// The refusal `plan` raised for one placement, made that chart's failure; any other error is the
/// board's.
fn refuse_chart(board: &Board, styles: &ChartStyles, bound: &mut bind::bound::Board, refusal: Diagnostic) -> Result<(), Diagnostic> {
    let placement = match refusal.params.get("placement") {
        Some(serde_json::Value::String(placement)) if refusal.code == diagnostic::codes::ERR_ENGINE_REFUSED.id => placement.clone(),
        _ => return Err(refusal),
    };
    let drawn = bound.drawn().charts(board).map_err(|e| diagnostic::diag::err_internal(format!("{e:?}")))?;
    let (at, id) = drawn.into_iter().find(|(at, _)| at.to_string() == placement).ok_or_else(|| diagnostic::diag::err_internal(format!("no chart is placed at {placement}")))?;
    let diagnostic = refusal.with_sink_param("chart_id", styles.public_id(id)?.into());
    bound.fail(&at, bind::bound::Failure { chart: id.clone(), diagnostic }).map_err(|e| diagnostic::diag::err_internal(format!("{e:?}")))
}
