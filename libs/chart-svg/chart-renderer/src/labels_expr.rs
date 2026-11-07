//! chart-renderer's side of an authored `axis.labels.expr`: `layout::labels_expr` evaluates it; this module reads
//! the payload for the expressions it holds, maps their failures to the diagnostics production raises, and
//! settles the chart's layout one autosize-fit padding pass at a time for an expression that reads the view.

use std::cell::RefCell;

use ::layout::labels_expr::{describe, view, EvalError, Expr, COMPILER_MARK, THROWS_MARK};

use crate::common::AxisLabels;
use crate::vega_fit::FitMargins;

/// What each band of a discrete axis paints: the band itself, or an authored `labels.expr`'s rewrite of it
/// (`labelExpr` runs on `datum.value`, the raw band) at the scale `axis` names.
pub fn band_texts(
    axis: &'static str,
    domain: &[String],
    labels: &AxisLabels,
) -> Result<Vec<String>, String> {
    let Some(src) = labels.expr.as_deref() else {
        return domain.iter().map(|band| labels.cased(band)).collect();
    };
    let expr = Expr::parse(src)
        .map(|e| e.on(axis))
        .map_err(|e| describe(&e, "the axis"))?;
    domain
        .iter()
        .enumerate()
        .map(|(i, band)| labels.cased(&expr.rewrite_band(band, i, domain.len())?))
        .collect()
}

/// vl-convert's failure to convert: `what` is the message of a plain `Error`, or of the named class (`RangeError: ...`).
fn conversion_failed(what: &str) -> diagnostic::Diagnostic {
    let named = what.split_once(": ").is_some_and(|(class, _)| {
        class.ends_with("Error") && class.chars().all(|c| c.is_ascii_alphabetic())
    });
    let error = if named {
        what.to_string()
    } else {
        format!("Error: {what}")
    };
    diagnostic::Diagnostic::new(
        &diagnostic::codes::ERR_INTERNAL,
        [(
            "message",
            serde_json::Value::String(format!("Vega-Lite to SVG conversion failed: {error}")),
        )],
    )
}

/// What a chart becomes when its label expression throws: a bar paints no marks (`ERR-CHART-PAINTED-NO-MARKS`),
/// while Vega draws a line, area or scatter chart half-built (or fails inside its scenegraph), which is not drawn here.
pub(crate) fn throw_diagnostic(payload: &crate::Payload) -> diagnostic::Diagnostic {
    match payload
        .resolved
        .get("chart_type")
        .and_then(serde_json::Value::as_str)
    {
        Some(family @ ("line" | "area" | "scatter")) => {
            diagnostic::Diagnostic::refused(diagnostic::refuse!(
                "{family}: a labels.expr that throws on an axis label is not supported"
            ))
        }
        _ => payload.painted_no_marks(),
    }
}

/// The diagnostic production raises for a refusal that carries a [`describe`]d failure: a throw while painting
/// leaves the chart with no marks, a rejected expression fails vl-convert's conversion.
pub(crate) fn recode(
    d: diagnostic::Diagnostic,
    payload: &crate::Payload,
) -> diagnostic::Diagnostic {
    let text = d.to_string();
    if text.contains(THROWS_MARK) {
        throw_diagnostic(payload)
    } else if let Some(at) = text.find(COMPILER_MARK) {
        conversion_failed(&text[at + COMPILER_MARK.len()..])
    } else {
        d
    }
}

/// Every axis `labels.expr` the chart authors.
fn axis_exprs(resolved: &serde_json::Value) -> Vec<String> {
    use serde_json::Value;
    fn walk(v: &Value, in_axis: bool, found: &mut Vec<String>) {
        match v {
            Value::Object(map) => {
                for (key, child) in map {
                    if in_axis && key == "labels" {
                        if let Some(Value::String(src)) = child.get("expr") {
                            found.push(src.clone());
                        }
                    }
                    walk(child, matches!(key.as_str(), "axis_x" | "axis_y"), found);
                }
            }
            Value::Array(items) => items.iter().for_each(|c| walk(c, in_axis, found)),
            _ => {}
        }
    }
    let mut sources = Vec::new();
    walk(resolved, false, &mut sources);
    sources
}

/// Compiles every axis `labels.expr` the chart authors, the way Vega's compiler does before it draws anything: an
/// expression it rejects fails the conversion whether or not this engine would have evaluated it.
pub(crate) fn validate(resolved: &serde_json::Value) -> Result<(), diagnostic::Diagnostic> {
    for src in axis_exprs(resolved) {
        if let Err(EvalError::Unknown(what)) = Expr::parse(&src) {
            return Err(conversion_failed(&what));
        }
    }
    Ok(())
}

/// What an authored axis `labels.expr` reads of the chart it labels.
#[derive(Debug, Default, Clone, Copy)]
pub(crate) struct Needs {
    /// The plot's scales or label geometry, which only the final layout knows: such a chart is painted pass by
    /// pass ([`render_passes`]).
    pub scales: bool,
    /// The chart's datasets.
    pub data: bool,
}

pub(crate) fn needs(resolved: &serde_json::Value) -> Needs {
    let mut found = Needs::default();
    for src in axis_exprs(resolved) {
        if let Ok(expr) = Expr::parse(&src) {
            found.scales |= expr.reads_view();
            found.data |= expr.reads_data();
        }
    }
    found
}

/// What the render in progress is doing to settle the layout the scales' ranges come from.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
enum Mode {
    /// Collecting the scales' domains; expressions that read the view see nothing.
    Discover,
    /// One padding pass of Vega's autosize-fit loop, at the plot size the previous pass negotiated.
    Measure,
    /// The final layout: the plot rectangle is pinned to what the last pass negotiated.
    Draw,
    /// A layout with no passes to replay (`autosize: pad`).
    #[default]
    Settled,
}

/// A fit the render asked `vega_fit` for.
#[derive(Debug, Clone)]
struct FitCall {
    /// The outer size it was asked to fit.
    view: (f64, f64),
    /// How many padding passes it runs.
    passes: usize,
    /// What it negotiated; `None` for an `autosize: pad` layout, whose plot is its argument.
    margins: Option<FitMargins>,
    plot: (f64, f64),
}

/// What `vega_fit` does with a fit it is asked for.
pub enum FitPlan {
    Normal,
    /// Run one padding pass starting at this plot size.
    From((f64, f64)),
    /// The margins the last padding pass negotiated.
    Pinned(Box<FitMargins>),
}

#[derive(Default)]
struct Passes {
    mode: Mode,
    start: Option<(f64, f64)>,
    calls: Vec<FitCall>,
    pins: Vec<FitCall>,
}

thread_local! {
    static PASSES: RefCell<Passes> = RefCell::new(Passes::default());
}

/// Holds the layout view open for one render; closing it drops every registration and pass.
pub struct Guard {
    _view: view::Guard,
}

/// Opens the view for a render whose axis expressions read it.
pub fn enter() -> Guard {
    PASSES.with(|p| *p.borrow_mut() = Passes::default());
    Guard {
        _view: view::enter(),
    }
}

impl Drop for Guard {
    fn drop(&mut self) {
        PASSES.with(|p| *p.borrow_mut() = Passes::default());
    }
}

/// Asks for the plan of the next fit the render makes.
pub fn fit_plan() -> FitPlan {
    PASSES.with(|p| {
        let p = p.borrow();
        match p.mode {
            Mode::Measure => p.start.map_or(FitPlan::Normal, FitPlan::From),
            Mode::Draw => match p.pins.get(p.calls.len()).and_then(|c| c.margins.clone()) {
                Some(m) => FitPlan::Pinned(Box::new(m)),
                None => FitPlan::Normal,
            },
            Mode::Discover | Mode::Settled => FitPlan::Normal,
        }
    })
}

/// Records a fit.
pub fn fit_done(view: (f64, f64), passes: usize, m: &FitMargins) {
    if view::active() {
        PASSES.with(|p| {
            p.borrow_mut().calls.push(FitCall {
                view,
                passes,
                margins: Some(m.clone()),
                plot: (m.plot_w, m.plot_h),
            })
        });
    }
}

/// Records an `autosize: pad` layout, whose plot rectangle is given.
pub fn pad_done(plot: (f64, f64)) {
    if view::active() {
        PASSES.with(|p| {
            p.borrow_mut().calls.push(FitCall {
                view: plot,
                passes: 0,
                margins: None,
                plot,
            })
        });
    }
}

fn begin(mode: Mode, start: Option<(f64, f64)>, plot: (f64, f64)) {
    PASSES.with(|p| {
        let mut p = p.borrow_mut();
        p.mode = mode;
        p.start = start;
        p.calls.clear();
    });
    view::set_discovering(mode == Mode::Discover);
    view::set_plot(plot.0, plot.1);
}

fn take_calls() -> Vec<FitCall> {
    PASSES.with(|p| std::mem::take(&mut p.borrow_mut().calls))
}

/// Renders a chart whose axis expressions read the view, the way Vega paints it. The first render collects the
/// scales. Then each padding pass of the autosize-fit loop is rendered on its own at the plot size the pass before
/// negotiated, so its expressions see the scales at that size; the last render pins the plot to what the final
/// pass negotiated and paints the labels at that size.
pub fn render_passes<T, E: From<String>>(
    rows: &[serde_json::Map<String, serde_json::Value>],
    mut render: impl FnMut() -> Result<T, E>,
) -> Result<T, E> {
    let _open = enter();
    view::set_rows(rows)?;
    begin(Mode::Discover, None, (0.0, 0.0));
    let discovered = render();
    let calls = take_calls();
    let Some(last) = calls.last() else {
        discovered?;
        return Err(E::from(
            "labels.expr reads the chart's scales, but this chart negotiated no plot rectangle"
                .to_string(),
        ));
    };
    if last.margins.is_none() {
        begin(Mode::Settled, None, last.plot);
        return render();
    }
    let (first, passes) = (calls[0].view, calls[0].passes);
    let mut plot = first;
    let mut negotiated = Vec::new();
    for _ in 0..passes {
        begin(Mode::Measure, Some(plot), plot);
        render()?;
        negotiated = take_calls();
        plot = negotiated.last().map_or(plot, |c| c.plot);
    }
    begin(Mode::Draw, None, plot);
    PASSES.with(|p| p.borrow_mut().pins = negotiated);
    render()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_chart_with_a_rejected_expression_fails_the_conversion() {
        let resolved = serde_json::json!({"style": {"axis_x": {"labels": {"expr": "nope(1)"}}}});
        let err = validate(&resolved).unwrap_err();
        assert!(err.to_string().contains("ERR-INTERNAL"), "{err}");
        let fine = serde_json::json!({"style": {"axis_x": {"labels": {"expr": "upper(datum.label)"}}, "legend": {"labels": {"expr": "nope(1)"}}}});
        assert!(validate(&fine).is_ok());
    }

    #[test]
    fn the_scan_pass_reports_the_error_a_family_raised_before_its_fit() {
        let scanned =
            render_passes::<(), String>(&[], || Err("bar: the chart is too small".to_string()));
        assert_eq!(scanned, Err("bar: the chart is too small".to_string()));
    }
}
