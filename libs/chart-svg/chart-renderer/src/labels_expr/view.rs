//! The view an authored `labels.expr` runs in: the scales and datasets Vega's dataflow holds when it paints an axis
//! label. The chart family that builds a scale registers it here; an expression reads it by name.
//!
//! A scale's range is the plot rectangle of the pass being painted, so a registration carries the scale without
//! its pixels ([`Span`]) and the pass supplies them ([`enter`] / [`set_plot`]).

use std::cell::RefCell;
use std::collections::BTreeMap;

use super::scales::{Kind, Scale};
use super::val::{EvalError, Res, Text, Val};
use crate::vega_fit::FitMargins;

/// Where a scale's range lies on the plot rectangle.
#[derive(Debug, Clone, PartialEq)]
pub enum Span {
    /// `[0, width]`.
    Width,
    /// `[inset, width - inset]`.
    WidthInset(f64),
    /// `[height, 0]`.
    Height,
    /// `[0, height]`.
    HeightDown,
    /// A range the plot's size does not set (a color palette).
    Fixed(Vec<Val>),
}

/// A scale as a family builds it: its type and domain, and where its range lies.
#[derive(Debug, Clone)]
pub struct ScaleSpec {
    pub kind: Kind,
    pub domain: Vec<Val>,
    pub span: Span,
}

/// What the render in progress is doing to settle the layout the scales' ranges come from.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
enum Mode {
    /// Collecting the scales' domains; expressions that read the view see nothing.
    #[default]
    Discover,
    /// One padding pass of Vega's autosize-fit loop, at the plot size the previous pass negotiated.
    Measure,
    /// The final layout: the plot rectangle is pinned to what the last pass negotiated.
    Draw,
    /// A layout with no passes to replay (`autosize: pad`).
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

/// Which side of the plot an axis sits on.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Orient {
    Bottom,
    Top,
    Left,
    Right,
}

impl Orient {
    fn horizontal(self) -> bool {
        matches!(self, Orient::Bottom | Orient::Top)
    }

    fn outward(self) -> f64 {
        if matches!(self, Orient::Left | Orient::Top) { -1.0 } else { 1.0 }
    }
}

/// What the label mark's encoder fixes for an axis: the props `item` carries besides its position.
#[derive(Debug, Clone)]
pub struct ItemStyle {
    pub font: Text,
    pub size: f64,
    pub fill: Text,
    pub weight: Val,
    pub limit: f64,
}

/// How an axis lays out its upright labels: the geometry `item` carries.
#[derive(Debug, Clone)]
pub struct AxisSpec {
    pub orient: Orient,
    /// Tick length plus label padding.
    pub offset: f64,
    /// Added to the scale's position (a band's midpoint is added on top).
    pub shift: f64,
    /// The label anchor, where it is not the side's default (`left` on a right axis, `right` on a left one).
    pub align: Option<&'static str>,
    /// The label baseline, where it is not the side's default (`top` below the plot, `middle` beside it).
    pub baseline: Option<&'static str>,
    /// `labelAngle`.
    pub angle: f64,
    /// `labelFlush`'s threshold, if the axis flushes its end labels.
    pub flush: Option<f64>,
    pub style: ItemStyle,
}

/// Where a label item stands after an evaluation.
#[derive(Debug, Clone)]
struct Geom {
    x: f64,
    y: f64,
    align: Val,
    baseline: Val,
    text: Val,
}

/// A label item across evaluations: Vega evaluates the encoder on the item it keeps from the evaluation before.
#[derive(Debug, Clone)]
struct ItemState {
    /// The scale's domain and range at the evaluation that last ran.
    sig: String,
    /// What `item` read during that evaluation.
    visible: Geom,
    /// What `item` holds after it.
    after: Geom,
}

#[derive(Default)]
struct View {
    axes: BTreeMap<String, AxisSpec>,
    item: Option<Val>,
    items: BTreeMap<(String, String), ItemState>,
    on: bool,
    rows: Vec<Val>,
    mode: Mode,
    plot: (f64, f64),
    specs: BTreeMap<String, ScaleSpec>,
    scales: BTreeMap<String, Scale>,
    start: Option<(f64, f64)>,
    calls: Vec<FitCall>,
    pins: Vec<FitCall>,
}

thread_local! {
    static VIEW: RefCell<View> = RefCell::new(View::default());
}

/// Holds the view open for one render; closing it drops every registration.
pub struct Guard;

/// Opens the view for a render whose axis expressions read it.
pub fn enter() -> Guard {
    super::funcs::reseed_random();
    VIEW.with(|v| *v.borrow_mut() = View { on: true, mode: Mode::Settled, ..View::default() });
    Guard
}

impl Drop for Guard {
    fn drop(&mut self) {
        VIEW.with(|v| *v.borrow_mut() = View::default());
    }
}

/// Whether a render is collecting scales: families skip building a [`ScaleSpec`] when it is not.
pub fn active() -> bool {
    VIEW.with(|v| v.borrow().on)
}

/// The plot rectangle the scales' ranges lie on in the pass being painted.
pub fn set_plot(width: f64, height: f64) {
    VIEW.with(|v| {
        let mut v = v.borrow_mut();
        v.plot = (width, height);
        v.scales.clear();
    });
}

/// Registers the scale `name` as the family built it.
pub fn register(name: &str, spec: ScaleSpec) {
    VIEW.with(|v| {
        let mut v = v.borrow_mut();
        if v.on {
            v.specs.insert(name.to_string(), spec);
            v.scales.remove(name);
        }
    });
}

/// Registers how the axis `name` lays out its labels, for the expressions that read `item`.
pub fn register_axis(name: &str, spec: AxisSpec) {
    VIEW.with(|v| {
        let mut v = v.borrow_mut();
        if v.on {
            v.axes.insert(name.to_string(), spec);
        }
    });
}

/// Registers a linear scale over `[lo, hi]`.
pub fn register_linear(name: &str, lo: f64, hi: f64, span: Span) {
    register(name, ScaleSpec { kind: Kind::Linear, domain: vec![Val::Num(lo), Val::Num(hi)], span });
}

/// The categories of a band or point scale, as the strings Vega's scale holds.
fn category_domain(domain: &[String]) -> Result<Vec<Val>, String> {
    domain.iter().map(|c| Val::string(c.clone()).map_err(|e| super::describe(&e, "a category"))).collect()
}

/// Registers a point scale over the categories `domain`.
pub fn register_point(name: &str, domain: &[String], span: Span) -> Result<(), String> {
    let kind = Kind::Point { padding_outer: 0.0 };
    register(name, ScaleSpec { kind, domain: category_domain(domain)?, span });
    Ok(())
}

/// Registers a time scale over `[lo, hi]` epoch milliseconds, which the render pins to UTC.
pub fn register_utc(name: &str, lo_ms: f64, hi_ms: f64, span: Span) {
    register(name, ScaleSpec { kind: Kind::Utc, domain: vec![Val::Date(lo_ms), Val::Date(hi_ms)], span });
}

/// Registers the continuous scale `bend` names over `[lo, hi]`.
pub fn register_bent(name: &str, bend: crate::vega_fit::Bend, lo: f64, hi: f64, span: Span) {
    use crate::vega_fit::Bend;
    let kind = match bend {
        Bend::Linear => Kind::Linear,
        Bend::Log => Kind::Log,
        Bend::Pow(exponent) => Kind::Pow(exponent),
        Bend::Symlog(constant) => Kind::Symlog(constant),
    };
    register(name, ScaleSpec { kind, domain: vec![Val::Num(lo), Val::Num(hi)], span });
}

/// Registers a point scale over `domain` with outer padding `padding`.
pub fn register_point_padded(name: &str, domain: &[String], padding: f64, span: Span) -> Result<(), String> {
    let kind = Kind::Point { padding_outer: padding };
    register(name, ScaleSpec { kind, domain: category_domain(domain)?, span });
    Ok(())
}

/// Registers a log scale over `[lo, hi]`.
pub fn register_log(name: &str, lo: f64, hi: f64, span: Span) {
    register(name, ScaleSpec { kind: Kind::Log, domain: vec![Val::Num(lo), Val::Num(hi)], span });
}

/// Registers a band scale over the categories `domain`, with d3's padding fractions.
pub fn register_band(name: &str, domain: &[String], padding_inner: f64, padding_outer: f64, span: Span) -> Result<(), String> {
    let kind = Kind::Band { padding_inner, padding_outer };
    register(name, ScaleSpec { kind, domain: category_domain(domain)?, span });
    Ok(())
}

/// Reads the scale `name` at the current plot rectangle, if a family registered it.
pub fn with_scale<R>(name: &str, read: impl FnOnce(&mut Scale) -> R) -> Option<R> {
    VIEW.with(|v| {
        let mut v = v.borrow_mut();
        if !v.scales.contains_key(name) {
            let (w, h) = v.plot;
            let spec = v.specs.get(name)?;
            let range = match &spec.span {
                Span::Width => vec![Val::Num(0.0), Val::Num(w)],
                Span::WidthInset(inset) => vec![Val::Num(*inset), Val::Num(w - inset)],
                Span::Height => vec![Val::Num(h), Val::Num(0.0)],
                Span::HeightDown => vec![Val::Num(0.0), Val::Num(h)],
                Span::Fixed(range) => range.clone(),
            };
            let scale = Scale::new(spec.kind, spec.domain.clone(), range);
            v.scales.insert(name.to_string(), scale);
        }
        v.scales.get_mut(name).map(read)
    })
}

/// The rows of the chart's one source dataset, as the tuples Vega-Lite's `source_0` holds.
pub fn set_rows(rows: &[serde_json::Map<String, serde_json::Value>]) -> Result<(), String> {
    fn lift(v: &serde_json::Value) -> Result<Val, String> {
        Ok(match v {
            serde_json::Value::Null => Val::Null,
            serde_json::Value::Bool(b) => Val::Bool(*b),
            serde_json::Value::Number(n) => Val::Num(n.as_f64().unwrap_or(f64::NAN)),
            serde_json::Value::String(s) => Val::string(s.clone()).map_err(|e| super::describe(&e, "a data value"))?,
            serde_json::Value::Array(items) => Val::Array(items.iter().map(lift).collect::<Result<_, _>>()?),
            serde_json::Value::Object(map) => Val::Object(map.iter().map(|(k, v)| Ok((k.clone(), lift(v)?))).collect::<Result<_, String>>()?),
        })
    }
    let tuples: Vec<Val> = rows
        .iter()
        .map(|row| Ok(Val::tuple(row.iter().map(|(k, v)| Ok((k.clone(), lift(v)?))).collect::<Result<_, String>>()?)))
        .collect::<Result<_, String>>()?;
    VIEW.with(|v| v.borrow_mut().rows = tuples);
    Ok(())
}

/// The tuples of the dataset `name`, if the chart has it: Vega-Lite names its source `source_0`.
pub fn dataset(name: &str) -> Option<Vec<Val>> {
    VIEW.with(|v| {
        let v = v.borrow();
        (v.on && name == "source_0").then(|| v.rows.clone())
    })
}

/// Whether `name` is one Vega-Lite's compiler mints (`source_1`, `data_0`, ...): datasets whose contents depend
/// on how it compiled the chart.
pub fn is_compiled_name(name: &str) -> bool {
    let digits = |rest: &str| !rest.is_empty() && rest.bytes().all(|b| b.is_ascii_digit());
    name.strip_prefix("source_").is_some_and(digits) || name.strip_prefix("data_").is_some_and(digits)
}

/// Whether a failing expression is held back: the render is only collecting scales, and the expression ran on a
/// view that has none yet.
pub fn discovering() -> bool {
    VIEW.with(|v| {
        let v = v.borrow();
        v.on && v.mode == Mode::Discover
    })
}

/// Asks for the plan of the next fit the render makes.
pub fn fit_plan() -> FitPlan {
    VIEW.with(|v| {
        let v = v.borrow();
        match v.mode {
            Mode::Measure => v.start.map_or(FitPlan::Normal, FitPlan::From),
            Mode::Draw => match v.pins.get(v.calls.len()).and_then(|c| c.margins.clone()) {
                Some(m) => FitPlan::Pinned(Box::new(m)),
                None => FitPlan::Normal,
            },
            Mode::Discover | Mode::Settled => FitPlan::Normal,
        }
    })
}

/// Records a fit.
pub fn fit_done(view: (f64, f64), passes: usize, m: &FitMargins) {
    VIEW.with(|v| {
        let mut v = v.borrow_mut();
        if v.on {
            v.calls.push(FitCall { view, passes, margins: Some(m.clone()), plot: (m.plot_w, m.plot_h) });
        }
    });
}

/// Records an `autosize: pad` layout, whose plot rectangle is given.
pub fn pad_done(plot: (f64, f64)) {
    VIEW.with(|v| {
        let mut v = v.borrow_mut();
        if v.on {
            v.calls.push(FitCall { view: plot, passes: 0, margins: None, plot });
        }
    });
}

fn begin(mode: Mode, start: Option<(f64, f64)>, plot: (f64, f64)) {
    VIEW.with(|v| {
        let mut v = v.borrow_mut();
        v.mode = mode;
        v.start = start;
        v.plot = plot;
        v.scales.clear();
        v.calls.clear();
    });
}

fn take_calls() -> Vec<FitCall> {
    VIEW.with(|v| std::mem::take(&mut v.borrow_mut().calls))
}

/// Renders a chart whose axis expressions read the view, the way Vega paints it. The first render collects the
/// scales. Then each padding pass of the autosize-fit loop is rendered on its own at the plot size the pass before
/// negotiated, so its expressions see the scales at that size; the last render pins the plot to what the final
/// pass negotiated and paints the labels at that size.
pub fn render_passes<T, E: From<String>>(rows: &[serde_json::Map<String, serde_json::Value>], mut render: impl FnMut() -> Result<T, E>) -> Result<T, E> {
    let _open = enter();
    set_rows(rows)?;
    begin(Mode::Discover, None, (0.0, 0.0));
    let discovered = render();
    let calls = take_calls();
    let Some(last) = calls.last() else {
        discovered?;
        return Err(E::from("labels.expr reads the chart's scales, but this chart negotiated no plot rectangle".to_string()));
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
    VIEW.with(|v| v.borrow_mut().pins = negotiated);
    render()
}

fn geometry(spec: &AxisSpec, scale: &Scale, value: &Val) -> Res<Geom> {
    let at = scale.call(value).to_number();
    let along = at + scale.bandwidth() * 0.5 + spec.shift;
    let across = spec.orient.outward() * spec.offset;
    let flushed = |left: &'static str, right: &'static str, center: &'static str| match spec.flush {
        Some(threshold) => {
            let args = [scale.range(), Val::Num(at), Val::Num(threshold), Val::Str(left.into()), Val::Str(right.into()), Val::Str(center.into())];
            super::funcs::call("flush", &args, &Val::Null)
        }
        None => Ok(Val::Str(center.into())),
    };
    let text = |s: &'static str| Val::Str(s.into());
    Ok(if spec.orient.horizontal() {
        let baseline = text(spec.baseline.unwrap_or(if spec.orient == Orient::Top { "bottom" } else { "top" }));
        let align = match spec.align {
            Some(a) => text(a),
            None => flushed("left", "right", "center")?,
        };
        Geom { x: along, y: across, align, baseline, text: Val::Undefined }
    } else {
        let align = text(spec.align.unwrap_or(if spec.orient == Orient::Right { "left" } else { "right" }));
        let baseline = match spec.baseline {
            Some(b) => text(b),
            None => flushed("top", "bottom", "middle")?,
        };
        Geom { x: across, y: along, align, baseline, text: Val::Undefined }
    })
}

/// The `item` an axis label's encoder runs on for the tick `value`: the label item the evaluation before left,
/// or a new one, which has only the position its `enter` set. An evaluation at the scale the last one saw is not
/// a new evaluation: Vega does not re-run the encoder.
fn item_before(axis: &str, value: &Val) -> Res<Val> {
    let unmodeled = || EvalError::Unmodeled(crate::refuse!("labels.expr reading the item of the {axis:?} axis's labels on a layout this renderer does not lay out for it is not supported"));
    let spec = VIEW.with(|v| v.borrow().axes.get(axis).cloned()).ok_or_else(unmodeled)?;
    let scale = with_scale(axis, |s| s.clone()).ok_or_else(unmodeled)?;
    let now = geometry(&spec, &scale, value)?;
    let sig = format!("{:?}{:?}", scale.domain(), scale.range());
    let key = (axis.to_string(), format!("{value:?}"));
    VIEW.with(|v| {
        let mut v = v.borrow_mut();
        let visible = match v.items.get(&key) {
            Some(state) if state.sig == sig => state.visible.clone(),
            Some(state) => Geom { x: now.x, y: now.y, ..state.after.clone() },
            None => Geom { align: Val::Undefined, baseline: Val::Undefined, ..now.clone() },
        };
        if v.mode != Mode::Discover {
            v.items.insert(key, ItemState { sig, visible: visible.clone(), after: now });
        }
        Ok(item_of(&spec, &visible))
    })
}

/// Opens an evaluation of the label of the tick `value`: `item` reads what the evaluation before left.
pub(super) fn item_begin(axis: &str, value: &Val) -> Res<()> {
    let item = item_before(axis, value)?;
    VIEW.with(|v| v.borrow_mut().item = Some(item));
    Ok(())
}

/// Closes it, recording the text it set on the item.
pub(super) fn item_end(axis: &str, value: &Val, text: Option<&Val>) {
    VIEW.with(|v| {
        let mut v = v.borrow_mut();
        v.item = None;
        if let (Some(text), Some(state)) = (text, v.items.get_mut(&(axis.to_string(), format!("{value:?}")))) {
            state.after.text = text.clone();
        }
    });
}

/// The props of the label item being evaluated, if its axis lays them out.
pub(super) fn item_props() -> Vec<(String, Val)> {
    VIEW.with(|v| match &v.borrow().item {
        Some(Val::Object(fields)) => fields.clone(),
        _ => Vec::new(),
    })
}

fn item_of(spec: &AxisSpec, g: &Geom) -> Val {
    let style = &spec.style;
    let num = |n: f64| Val::Num(n);
    let fields = [
        ("opacity", num(1.0)),
        ("text", g.text.clone()),
        ("x", num(g.x)),
        ("y", num(g.y)),
        ("align", g.align.clone()),
        ("baseline", g.baseline.clone()),
        ("angle", num(spec.angle)),
        ("fill", Val::Str(style.fill.clone())),
        ("font", Val::Str(style.font.clone())),
        ("fontSize", num(style.size)),
        ("fontWeight", style.weight.clone()),
        ("limit", num(style.limit)),
    ];
    Val::Object(fields.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

/// An anchor name a family paints with, as the `&'static str` an [`AxisSpec`] holds.
pub fn anchor(name: &str) -> Result<&'static str, String> {
    ["left", "right", "center", "top", "middle", "bottom"]
        .into_iter()
        .find(|known| *known == name)
        .ok_or_else(|| format!("label anchor {name:?} is not a Vega align or baseline"))
}
