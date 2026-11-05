//! Legend view: what a chart's legend entries do when clicked, beyond what its series are.
//!
//! A chart with no color-keyed `link:` shows only the clicked series: the runtime writes the
//! per-chart param `_dct_legend_<chart-id>` (one series name, or a repeated key for several) and
//! the board re-renders; the chart then draws marks for the shown series only. Colors, legend
//! entries and stack order stay those of the whole series set, so a shown series keeps its color
//! and every entry stays drawn, a hidden one receded. The value axis fits the shown rows (a view
//! that shows none keeps the whole chart's axis) while the plot box stays where the chart with no
//! view puts it (`pin_plot_box`); category axes and the shape scale stay the whole chart's.
//!
//! A chart whose `link:` sets a declared select, radio or multiselect variable from its color
//! has its rows filtered by the query itself: its legend lists every option in option order, then
//! any series the rows hold that is no option, and recedes the ones the variable does not hold. A
//! click sets the variable. A faceted chart takes no view param: its legend only previews.
//!
//! A legend entry, and a direct label that names a series, carries the param it sets and the value a
//! click sets, so the runtime applies it as it stands.

use scene::{Attrs, Node, Role, Shape};
use serde_json::{json, Value};

use crate::families::legend::{LegendAction, LegendConfig, LegendEntry, VegaLegend};
use measure::FontBook;
use crate::href::js_string;
use crate::selected::{setter, Recede};
use crate::layout::Rect;
use crate::{Padding, Payload};

pub(crate) const PARAM_PREFIX: &str = "_dct_legend_";

#[derive(Clone, Debug)]
pub struct LegendView {
    /// The view param, or the variable the entries set.
    param: String,
    source: Source,
    recede: Recede,
}

#[derive(Clone, Debug)]
enum Source {
    /// The series a click left shown; `None` shows every series. `series` are all the chart's, in row order.
    Param { shown: Option<Vec<String>>, series: Vec<String> },
    /// A declared variable the chart's link sets from its color.
    Variable {
        multiple: bool,
        /// The variable's options in option order, then the rows' series that are none.
        domain: Vec<String>,
        /// The options the variable holds; `None` holds nothing, which recedes no entry.
        held: Option<Vec<String>>,
        /// The series the chart's rows hold.
        present: Vec<String>,
    },
}

/// The family a legend view applies to: only bar, line and pie legends set a linked variable.
#[derive(Clone, Copy, PartialEq)]
pub(crate) enum Family {
    Bar,
    Line,
    Scatter,
    Pie,
}

/// What a chart's bind knows that decides whether its legend can hide series.
pub(crate) struct ViewFacts<'a> {
    pub family: Family,
    pub color: Option<&'a str>,
    /// The fields the chart is split into panels by.
    pub panel_fields: Vec<&'a str>,
    /// A wide chart's series are measures; its legend labels are not the values a link keys by.
    pub wide: bool,
    /// A normalized stack makes the shown series fill the whole column.
    pub normalized: bool,
    /// A layer or a strip names series of its own.
    pub layered: bool,
    /// The color is a scale over a magnitude, not a set of series.
    pub continuous_color: bool,
}

/// A list of series names a variable or param holds: a scalar is one, an array lists several, and
/// nothing (absent, null, `""`, `[]`) is none. A number or boolean prints as a link writes it.
fn names(param: &str, value: Option<&Value>) -> Result<Option<Vec<String>>, String> {
    let scalar = |name: &Value| match name {
        Value::String(_) | Value::Number(_) | Value::Bool(_) => js_string(Some(name)),
        _ => Err(format!("`{param}` lists series names; {name} is not one")),
    };
    let held = match value {
        None | Some(Value::Null) => return Ok(None),
        Some(Value::Array(series)) => series.iter().map(scalar).collect::<Result<Vec<_>, _>>()?,
        Some(one @ (Value::String(_) | Value::Number(_) | Value::Bool(_))) => vec![scalar(one)?],
        Some(other) => return Err(format!("`{param}` is a series name or a list of them, not {other}")),
    };
    Ok(Some(held.into_iter().filter(|name| !name.is_empty()).collect::<Vec<_>>()).filter(|held| !held.is_empty()))
}

impl LegendView {
    /// The view `payload` holds, `None` when the chart's legend does not hide series: `facts`
    /// rules the shape out, a color-keyed link sets its variable instead (unless the variable
    /// declares its options, which the legend then lists whole), a magnitude color has no
    /// series, and a chart without a visible hover emphasis has no way to show an entry hidden.
    /// A null param is a cleared one.
    pub(crate) fn of(payload: &Payload, facts: &ViewFacts) -> Result<Option<Self>, String> {
        let Some(color) = facts.color else { return Ok(None) };
        let panel_axis = facts.panel_fields.contains(&color);
        if facts.wide || facts.layered || facts.continuous_color || panel_axis {
            return Ok(None);
        }
        let link = payload.resolved.get("link").and_then(Value::as_str).and_then(setter).filter(|(_, channel)| *channel == "color");
        let declared = link
            .and_then(|(name, _)| payload.variable_options.get(name).map(|options| (name, options)))
            .filter(|(_, declared)| seats_every_option(payload, color, &declared.options));
        let variable = declared.is_some();
        let unhideable = match facts.family {
            Family::Pie => !variable,
            Family::Scatter => variable,
            Family::Bar | Family::Line => false,
        };
        if unhideable || (facts.normalized && !variable) || (link.is_some() && !variable) || crate::board::is_magnitude_colored(&payload.resolved) {
            return Ok(None);
        }
        let Some(recede) = Recede::of(payload)? else { return Ok(None) };
        let (param, source) = match declared {
            Some((name, declared)) => {
                let present = present_series(&payload.rows, color);
                let mut domain: Vec<String> = declared.options.iter().map(|option| crate::common::cell_str(Some(option))).collect();
                let strays: Vec<String> = present.iter().filter(|series| !domain.contains(series)).cloned().collect();
                domain.extend(strays);
                let held = names(name, payload.variables.get(name))?;
                (name.to_string(), Source::Variable { multiple: declared.multiple, domain, held, present })
            }
            // A faceted chart's panels would reflow or vanish as series hide: its legend only previews.
            None if !facts.panel_fields.is_empty() => return Ok(None),
            None => {
                let param = format!("{PARAM_PREFIX}{}", payload.chart_id()?);
                let shown = names(&param, payload.variables.get(&param))?;
                (param, Source::Param { shown, series: present_series(&payload.rows, color) })
            }
        };
        Ok(Some(LegendView { param, source, recede }))
    }

    /// The series whose marks the chart draws: the shown ones of a view param, the ones its rows
    /// hold under a variable (the query already filtered them).
    pub(crate) fn shows(&self, series: &str) -> bool {
        match &self.source {
            Source::Param { shown, .. } => shown.as_ref().is_none_or(|shown| shown.iter().any(|name| name == series)),
            Source::Variable { present, .. } => present.iter().any(|name| name == series),
        }
    }

    /// Whether the entry of `series` is at full ink: shown, or held by the variable (or nothing is).
    fn holds(&self, series: &str) -> bool {
        match &self.source {
            Source::Param { .. } => self.shows(series),
            Source::Variable { held, .. } => held.as_ref().is_none_or(|held| held.iter().any(|name| name == series)),
        }
    }

    /// The series a linked legend lists whole, in legend order: the variable's options, then the
    /// rows' series that are none. `None` for a view param.
    pub(crate) fn series(&self) -> Option<&[String]> {
        match &self.source {
            Source::Variable { domain, .. } => Some(domain),
            Source::Param { .. } => None,
        }
    }

    /// `series`' position in the legend, the palette slot an unbound scale gives it.
    pub(crate) fn slot(&self, series: &str) -> Option<usize> {
        self.series()?.iter().position(|name| name == series)
    }

    /// `payload` over the rows of the shown series, with the whole rows beside it; `None` when this
    /// view hides no row. `color` names each row's series.
    pub(crate) fn narrowed(&self, payload: &Payload, color: &str) -> Option<Narrowed> {
        let Source::Param { shown: Some(_), .. } = &self.source else { return None };
        let shown: Vec<_> = payload.rows.iter().filter(|row| self.shows(&crate::common::row_str(row, color))).cloned().collect();
        (shown.len() < payload.rows.len()).then(|| Narrowed { payload: payload.with_inputs(payload.resolved.clone(), shown), whole: payload.rows.clone() })
    }

    /// `entries` with each series' entry toggling this view, and a hidden series' receded. `keys`
    /// pairs each entry with the series it names; an entry with none (a layer, a y title) is no series.
    pub(crate) fn entries<'a>(&'a self, entries: Vec<LegendEntry<'a>>, keys: &[Option<&'a str>]) -> Vec<LegendEntry<'a>> {
        assert_eq!(entries.len(), keys.len(), "one key per legend entry");
        entries
            .into_iter()
            .zip(keys)
            .map(|(entry, key)| LegendEntry {
                recede: key.and_then(|series| self.recede_of(series)),
                action: key.map(|series| LegendAction::Toggle { view: self, series }),
                ..entry
            })
            .collect()
    }

    /// How far `series`' entry or label recedes: it is not shown.
    pub(crate) fn recede_of(&self, series: &str) -> Option<Recede> {
        (!self.holds(series)).then_some(self.recede)
    }

    /// Every series the click rules choose among, in the order a click lists them.
    fn domain(&self) -> &[String] {
        match &self.source {
            Source::Param { series, .. } => series,
            Source::Variable { domain, .. } => domain,
        }
    }

    /// The value a click on `series` sets, as JSON: from all shown it narrows to the one series, then
    /// each click takes a shown series out or puts a hidden one back; nothing shown, or every
    /// series shown, is the cleared value. A single-value variable switches to the series, and
    /// clears when it already holds it.
    fn next(&self, series: &str) -> Value {
        let domain = self.domain();
        let cleared = match &self.source {
            Source::Param { .. } => Value::Null,
            Source::Variable { multiple: true, .. } => json!([]),
            Source::Variable { multiple: false, .. } => json!(""),
        };
        let shown: Vec<&String> = domain.iter().filter(|name| self.holds(name)).collect();
        let next: Vec<&String> = match &self.source {
            Source::Variable { multiple: false, .. } if shown.len() == 1 && shown[0] == series => Vec::new(),
            Source::Variable { multiple: false, .. } => domain.iter().filter(|name| *name == series).collect(),
            _ if shown.len() == domain.len() => domain.iter().filter(|name| *name == series).collect(),
            _ => domain.iter().filter(|name| (*name == series) != self.holds(name)).collect(),
        };
        match (&self.source, next.as_slice()) {
            (_, []) => cleared,
            (Source::Variable { multiple: false, .. }, [one]) => json!(one),
            (_, next) if next.len() == domain.len() => cleared,
            (_, next) => json!(next),
        }
    }

    /// `attrs` of the direct label naming `series`: receded when the series is hidden, and a button.
    pub(crate) fn direct_label(&self, attrs: Attrs, series: &str) -> Attrs {
        let attrs = match self.recede_of(series) {
            Some(recede) => recede.recede_attrs(attrs),
            None => attrs,
        };
        self.clickable(attrs, series)
    }

    /// `attrs` of the legend entry or direct label naming `series`, made a button: the param it
    /// sets, the value a click sets, and the keyboard and assistive-technology attributes.
    pub(crate) fn clickable(&self, attrs: Attrs, series: &str) -> Attrs {
        // A lone series has nothing to narrow to or add back: its click would change nothing.
        if self.domain().len() < 2 {
            return attrs;
        }
        let shown = self.holds(series);
        attrs
            .set("data-dbt-legend-var", &self.param)
            .set("data-dbt-legend-next", self.next(series).to_string())
            .set_if(!shown, "data-dbt-legend-hidden", "true")
    }
}

/// Whether the board's scale for `color`, if it binds one, has a slot for every option. The
/// planner leaves options out when they outrun the palette, and a legend cannot swatch those.
fn seats_every_option(payload: &Payload, color: &str, options: &[Value]) -> bool {
    let scales = payload.resolved.get("category_colors").and_then(Value::as_array);
    let scale = scales.and_then(|scales| scales.iter().find(|scale| scale["field"] == color));
    scale.is_none_or(|scale| options.iter().all(|option| scale["slots"].get(crate::common::cell_str(Some(option))).is_some()))
}

/// The distinct series the rows hold, in row order; a null cell is no series.
fn present_series(rows: &[serde_json::Map<String, Value>], color: &str) -> Vec<String> {
    let mut present: Vec<String> = Vec::new();
    for row in rows.iter().filter(|row| row.get(color).is_some_and(|cell| !cell.is_null())) {
        let series = crate::common::row_str(row, color);
        if !present.contains(&series) {
            present.push(series);
        }
    }
    present
}

/// A chart's payload over the rows a legend view leaves, and every row of the chart: the series,
/// their colors and the legend come from the whole rows, the marks from the shown ones.
pub(crate) struct Narrowed {
    pub(crate) payload: Payload,
    pub(crate) whole: Vec<serde_json::Map<String, Value>>,
}

/// `attrs` of the direct label naming `series`, made a button when the chart has a legend view.
pub(crate) fn direct_label(view: Option<&LegendView>, attrs: Attrs, series: &str) -> Attrs {
    match view {
        Some(view) => view.direct_label(attrs, series),
        None => attrs,
    }
}

impl Narrowed {
    /// Whether the value axes refit to the shown rows. A view that shows no row leaves the axes
    /// of the whole chart: there is nothing to fit.
    pub(crate) fn refits_axes(&self) -> bool {
        !self.payload.rows.is_empty()
    }
}

impl LegendView {
    /// `payload` as the chart would be drawn with no view: the plot box a view must keep.
    pub(crate) fn unviewed(&self, payload: &Payload) -> Box<Payload> {
        let mut whole = payload.clone();
        whole.variables.remove(&self.param);
        Box::new(whole)
    }
}

/// How many times the padding is renegotiated before the plot box is declared unsettled.
const PIN_PASSES: usize = 6;

/// How close two plot boxes must be, in px, to be the same box.
const PIN_TOLERANCE: f64 = 1e-6;

fn same_box(a: Rect, b: Rect) -> bool {
    [a.x - b.x, a.y - b.y, a.width - b.width, a.height - b.height].iter().all(|delta| delta.abs() < PIN_TOLERANCE)
}

/// `lay` (a layout under the given padding, giving the plot box it settled on and whatever else the
/// caller keeps) run until that box is `whole`'s: a value axis fitted to the shown rows has other
/// tick labels, whose ink would move the plot, so the card's padding gives up what the labels
/// gain. The unviewed chart's own layout is never touched. The left and right padding never go
/// below zero, since a value axis's labels reach toward those edges: ink wider than the whole
/// chart's gutter and the authored padding together stays on the card and moves the plot by what
/// it exceeds instead. The top and bottom give way freely: the ink there is a half-line of the
/// end tick labels, over the title band and the category labels.
pub(crate) fn pin_plot_box<T>(
    whole: Rect,
    authored: Option<Padding>,
    mut lay: impl FnMut(Padding) -> Result<(Rect, T), diagnostic::Diagnostic>,
) -> Result<T, diagnostic::Diagnostic> {
    let mut pad = authored.unwrap_or(Padding { left: 0.0, right: 0.0, top: 0.0, bottom: 0.0 });
    let (mut at, mut laid) = lay(pad)?;
    let mut spent = false;
    for _ in 0..PIN_PASSES {
        if same_box(at, whole) {
            return Ok(laid);
        }
        let (dx, dy, dw, dh) = (whole.x - at.x, whole.y - at.y, whole.width - at.width, whole.height - at.height);
        let wanted = Padding { left: pad.left + dx, right: pad.right - dx - dw, top: pad.top + dy, bottom: pad.bottom - dy - dh };
        let next = Padding { left: wanted.left.max(0.0), right: wanted.right.max(0.0), ..wanted };
        spent = wanted.left < 0.0 || wanted.right < 0.0;
        if [next.left, next.right, next.top, next.bottom] == [pad.left, pad.right, pad.top, pad.bottom] {
            break;
        }
        pad = next;
        (at, laid) = lay(pad)?;
    }
    if same_box(at, whole) || spent {
        return Ok(laid);
    }
    Err(format!("legend view: the plot box {at:?} does not settle on the unviewed chart's {whole:?}").into())
}

/// The rows a value axis fits: those the view draws, or the whole chart's when it draws none.
pub(crate) fn axis_rows<'a>(
    whole: Option<&'a [serde_json::Map<String, Value>]>,
    shown: &'a [serde_json::Map<String, Value>],
) -> &'a [serde_json::Map<String, Value>] {
    whole.filter(|_| shown.is_empty()).unwrap_or(shown)
}

/// Whether the legend view of a chart (if it has one) leaves `series` shown.
pub(crate) fn shown(view: Option<&LegendView>, series: &str) -> bool {
    view.is_none_or(|view| view.shows(series))
}

const CLEAR_FULL: &str = "\u{d7} CLEAR";
const CLEAR_GLYPH: &str = "\u{d7}";
const CLEAR_GAP: f64 = 8.0;
const CLEAR_TRACKING_EM: f64 = 0.08;

/// The control sits one size step below the labels it clears.
fn clear_size(label_size: f64) -> f64 {
    label_size - 2.0
}

/// The width `text` paints at as the control, tracking included, beside labels of `label_size`.
fn clear_width(fonts: &FontBook, text: &str, family: &str, label_size: f64, weight: &str) -> Result<f64, String> {
    let size = clear_size(label_size);
    let text_w = crate::common::measure_at_css_weight(fonts, text, family, size, weight)?;
    Ok(text_w + CLEAR_TRACKING_EM * size * text.chars().count() as f64)
}

/// The words that fit `available`: the full label, else the glyph alone.
fn clear_text_fitting(fonts: &FontBook, available: f64, family: &str, label_size: f64, weight: &str) -> Result<&'static str, String> {
    Ok(if clear_width(fonts, CLEAR_FULL, family, label_size, weight)? <= available { CLEAR_FULL } else { CLEAR_GLYPH })
}

impl LegendView {
    /// Whether the chart hides anything a clear would show again: a variable holds a value, or a
    /// view param shows fewer than all the series.
    fn clearable(&self) -> bool {
        match &self.source {
            Source::Variable { held, .. } => held.is_some(),
            Source::Param { shown, series } => shown.as_ref().is_some_and(|shown| series.iter().any(|name| !shown.contains(name))),
        }
    }

    /// The JSON the runtime sends to clear, as a last click off sets it.
    fn cleared(&self) -> &'static str {
        match &self.source {
            Source::Param { .. } => "null",
            Source::Variable { multiple: true, .. } => "[]",
            Source::Variable { multiple: false, .. } => "\"\"",
        }
    }
}

/// The control, hidden: a static picture states the held values in the filter bar and the runtime
/// reveals it. `font` is the labels' (family, size, weight, style); `ink` is receded halfway and
/// hover restores it (`data-dbt-clear-ink`).
fn clear_node(view: &LegendView, text: &str, ink: &str, x: f64, top: f64, font: (&str, f64, &str, &str)) -> Node {
    let (family, label_size, weight, style) = font;
    let size = clear_size(label_size);
    let attrs = Attrs::new()
        .set("text-anchor", "start")
        .vega("font-family", family)
        .num("font-size", size)
        .set("font-weight", weight)
        .set("font-style", style)
        .set("letter-spacing", format!("{CLEAR_TRACKING_EM}em"))
        .set("fill", view.recede.halfway(ink))
        .set("visibility", "hidden")
        .set("pointer-events", "visiblePainted")
        .set("data-dbt-clear-var", &view.param)
        .set("data-dbt-clear-next", view.cleared())
        .set("data-dbt-clear-ink", ink);
    Node::text(Role::Chrome, (x, top + measure::baseline_shift("top", size)), attrs, text)
}

/// The control of a direct-label rail `pane_w` wide: left-aligned at `x`, one gap below the lowest
/// of `labels` (their `(top, bottom)`); one gap above the highest when below leaves `bounds` (the
/// column's `(top, bottom)`); at the column's bottom edge when neither fits. It says "× CLEAR"
/// when that fits the pane and "×" when it does not.
pub(crate) fn clear_control(
    view: Option<&LegendView>,
    ink: Option<&str>,
    x: f64,
    labels: &[(f64, f64)],
    bounds: (f64, f64),
    pane_w: f64,
    font: (&str, f64, &str, &str),
    fonts: &FontBook,
) -> Result<Option<Node>, String> {
    let Some(view) = view.filter(|view| view.clearable()) else { return Ok(None) };
    let ink = ink.ok_or("legend.label.font.color absent (theme required)")?;
    let size = clear_size(font.1);
    let lowest = labels.iter().map(|&(_, bottom)| bottom).fold(f64::NEG_INFINITY, f64::max);
    let highest = labels.iter().map(|&(top, _)| top).fold(f64::INFINITY, f64::min);
    let (below, above) = (lowest + CLEAR_GAP, highest - CLEAR_GAP - size);
    let top = if below + size <= bounds.1 {
        below
    } else if above >= bounds.0 {
        above
    } else {
        bounds.1 - size
    };
    let text = clear_text_fitting(fonts, pane_w, font.0, font.1, font.2)?;
    Ok(Some(clear_node(view, text, ink, x, top, font)))
}

/// The view `entries` toggle, if any.
pub(crate) fn toggled<'a>(entries: &[LegendEntry<'a>]) -> Option<&'a LegendView> {
    entries.iter().find_map(|entry| match entry.action {
        Some(LegendAction::Toggle { view, .. }) => Some(view),
        _ => None,
    })
}

/// The canvas inside its padding and below its title band, in absolute coordinates: where chrome
/// may draw.
#[derive(Clone, Copy, Debug)]
pub(crate) struct Inset {
    top: f64,
    right: f64,
    bottom: f64,
}

impl Inset {
    /// The inset whose top is the lower of `pad_top` and the bottom of the title and subtitle
    /// text among `head`.
    pub(crate) fn below_titles(head: &[Node], pad_top: f64, right: f64, bottom: f64) -> Self {
        let titles = head.iter().filter(|node| node.role == Role::Title).filter_map(|node| match node.shape {
            Shape::Text { at, .. } => Some(at.1 + 0.25 * node.attrs.get_num("font-size").expect("title text carries a font-size")),
            _ => None,
        });
        Inset { top: titles.fold(pad_top, f64::max), right, bottom }
    }
}

/// The control of a boxed legend drawn at `origin`, in absolute coordinates, or `None` when the view
/// hides nothing or no candidate stays inside `inset`. Candidates in order: after the last entry
/// of a horizontal legend with no numeric legends stacked beside it, below the box, above the
/// box. "× CLEAR" wherever it fits, else "×" at the first candidate it fits. The box keeps its
/// size.
pub(crate) fn legend_clear(
    legend: &VegaLegend,
    entries: &[LegendEntry],
    cfg: &LegendConfig,
    origin: (f64, f64),
    inset: Inset,
    fonts: &FontBook,
) -> Result<Option<Node>, String> {
    let Some(view) = toggled(entries).filter(|view| view.clearable()) else { return Ok(None) };
    let ink = cfg.label.font.color.as_deref().ok_or("legend.label.font.color absent (theme required)")?;
    let (family, size, weight) = (cfg.label.font.family_or("sans-serif"), cfg.label.font.size_or(11.0), cfg.label.font.weight_str());
    let font = (family, size, weight.as_str(), cfg.label.font.style.as_deref().unwrap_or("normal"));
    let (ox, oy) = origin;
    let ink_size = clear_size(size);
    let last_row = legend.grid.row_y.last().copied().unwrap_or(0.0);
    let mut spots = Vec::new();
    // A stack of numeric legends along x starts just past the base legend's box.
    let numeric_beside = legend.stack.as_ref().is_some_and(|stack| stack.offsets.len() > usize::from(stack.base_block));
    if cfg.direction != "vertical" && !numeric_beside {
        let row_top = oy + legend.title_band + last_row + (legend.grid.size - ink_size) / 2.0;
        spots.push((ox + legend.grid.width + 2.0 * CLEAR_GAP, row_top));
    }
    spots.push((ox, oy + legend.box_h() + CLEAR_GAP));
    spots.push((ox, oy - CLEAR_GAP - ink_size));
    for text in [CLEAR_FULL, CLEAR_GLYPH] {
        let width = clear_width(fonts, text, family, size, &weight)?;
        let spot = spots.iter().find(|&&(x, top)| x + width <= inset.right && top >= inset.top && top + ink_size <= inset.bottom);
        if let Some(&(x, top)) = spot {
            return Ok(Some(clear_node(view, text, ink, x, top, font)));
        }
    }
    Ok(None)
}

/// The series of `domain` a chart's legend view (if it has one) leaves shown: what a grouped band splits among.
pub(crate) fn shown_among(view: Option<&LegendView>, domain: &[String]) -> Vec<String> {
    domain.iter().filter(|series| shown(view, series)).cloned().collect()
}

/// The view param of chart `chart_id` and its value as JSON, when the board holds one: the chart
/// carries it whether or not it draws a way to change it.
pub(crate) fn held_view(chart_id: &str, variables: &serde_json::Map<String, Value>) -> Option<(String, String)> {
    let param = format!("{PARAM_PREFIX}{chart_id}");
    let value = variables.get(&param)?;
    let cleared = match value {
        Value::Null => true,
        Value::String(text) => text.is_empty(),
        Value::Array(list) => list.is_empty(),
        _ => false,
    };
    (!cleared).then(|| (param, value.to_string()))
}

/// `axis` without the ladder resolve baked off the whole data, for an axis whose ladder the layout
/// derives (a stacked total, a nominal axis's absent one): the shown rows fit it. An authored
/// `scale.values` ladder stays, and so does an authored `scale.continuous.domain`: both are the
/// author's, not the data's.
pub(crate) fn unbake(axis: &mut crate::common::AxisStyle, column_forming: bool) {
    if axis.scale.get("values").is_none_or(Value::is_null) {
        axis.tick_values.clear();
        axis.refit = true;
        axis.column_forming = column_forming;
    }
    axis.domain_min = None;
    axis.domain_max = None;
}

/// `axis` baked again from the values the shown rows plot, by the law resolve bakes the whole
/// chart's axis with (`d3rs::measure_axis::bake_ticks`): the same headroom, the same nice ladder,
/// the same domain bounds a chart holding only those rows would get. The zero anchor resolve decided
/// over every row stays, so a view of one series far from zero does not float the axis.
pub(crate) fn refit(axis: &mut crate::common::AxisStyle, shown: &[f64], column_forming: bool) {
    if shown.is_empty() {
        return;
    }
    let law = d3rs::measure_axis::MeasureAxis {
        headroom: axis.scale.get("headroom").and_then(Value::as_f64),
        authored_domain: axis.authored_continuous_domain(),
        authored_ladder: axis.authored_scale_values(),
        target_count: axis.ticks.count.map(|count| count as usize),
        min_step: axis
            .is_quantitative
            .then(|| axis.labels.format.as_ref().and_then(|slot| d3rs::numeral_scale::tick_min_step_for_format(&slot.spec)))
            .flatten(),
    };
    let baked = d3rs::measure_axis::bake_ticks(&law, shown, axis.zero_anchored);
    axis.tick_values = baked.ticks;
    axis.domain_max = baked.domain_max;
    axis.domain_min = baked.domain_min;
    axis.refit = true;
    axis.column_forming = column_forming;
}

#[cfg(test)]
mod tests {
    use super::*;

    const WHOLE: Rect = Rect { x: 40.0, y: 10.0, width: 300.0, height: 200.0 };
    const AUTHORED: Padding = Padding { left: 4.0, right: 4.0, top: 4.0, bottom: 4.0 };

    /// A chart whose value-axis labels reach `ink` px left of its plot: the plot starts at
    /// `padding.left + ink` and gives up that much width.
    fn lay(ink: f64) -> impl FnMut(Padding) -> Result<(Rect, Padding), diagnostic::Diagnostic> {
        move |padding| {
            let x = padding.left + ink;
            Ok((Rect { x, y: padding.top + 6.0, width: 400.0 - x - padding.right - 10.0, height: 220.0 - padding.top - padding.bottom - 6.0 - 10.0 }, padding))
        }
    }

    #[test]
    fn padding_gives_up_what_narrower_labels_gain() {
        let padding = pin_plot_box(WHOLE, Some(AUTHORED), lay(30.0)).expect("settles");
        assert_eq!((padding.left, padding.top), (10.0, 4.0));
    }

    #[test]
    fn labels_wider_than_the_pinned_gutter_spend_the_padding_and_stop_at_the_card_edge() {
        let padding = pin_plot_box(WHOLE, Some(AUTHORED), lay(44.0)).expect("settles");
        assert_eq!(padding.left, 0.0, "the authored 4px of padding is spent, and no more than that");
    }

    #[test]
    fn a_plot_that_cannot_reach_the_box_is_an_error() {
        let stuck = |padding: Padding| Ok((Rect { x: 40.1, ..WHOLE }, padding));
        assert!(pin_plot_box(WHOLE, Some(AUTHORED), stuck).is_err());
    }

    /// A horizontal legend of one chart's series, holding a view that hides all but `EMEA`, with
    /// numeric legends stacked beside it when `stacked`.
    fn horizontal_legend_clear(stacked: bool) -> (Node, VegaLegend) {
        let payload: Payload = {
            let mut json: Value = serde_json::from_str(include_str!("../../fixtures/line_multi.json")).unwrap();
            json["board_style"]["hover_emphasis"] = serde_json::json!({"visible": true, "recede_contrast": 1.5});
            json["variables"] = serde_json::json!({"_dct_legend_line_multi": "EMEA"});
            json["resolved"]["legend"]["direction"] = serde_json::json!("horizontal");
            serde_json::from_value(json).unwrap()
        };
        let facts = ViewFacts { family: Family::Line, color: Some("region"), panel_fields: vec![], wide: false, normalized: false, layered: false, continuous_color: false };
        let view = LegendView::of(&payload, &facts).unwrap().expect("a view");
        let cfg: LegendConfig = serde_json::from_value(payload.resolved["legend"].clone()).unwrap();
        let fonts = FontBook::load_dir(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../src/dbt_charts/core/render/fonts")).unwrap();
        let entries: Vec<LegendEntry> = ["EMEA", "AMER", "APAC"]
            .iter()
            .map(|series| LegendEntry { label: series, fill: "#336699", symbol: None, series_key: None, recede: None, action: Some(LegendAction::Toggle { view: &view, series }) })
            .collect();
        let mut legend = crate::families::legend::vega_legend(&entries, false, "", &cfg, &fonts).unwrap();
        if stacked {
            let beside = legend.box_w + 8.0;
            legend.stack = Some(crate::families::color_scale::LegendStack { offsets: vec![(0.0, 0.0), (beside, 0.0)], size: (beside + 120.0, legend.box_h()), base_block: true });
            legend.box_w = beside + 120.0;
        }
        let inset = Inset { top: 0.0, right: 600.0, bottom: 300.0 };
        let node = legend_clear(&legend, &entries, &cfg, (20.0, 100.0), inset, &fonts).unwrap().expect("a control fits");
        (node, legend)
    }

    #[test]
    fn clear_follows_a_horizontal_legends_last_entry_unless_numeric_legends_stand_beside_it() {
        let x = |node: &Node| match node.shape {
            Shape::Text { at, .. } => at.0,
            _ => unreachable!("the control is text"),
        };
        let (alone, legend) = horizontal_legend_clear(false);
        assert!(x(&alone) >= 20.0 + legend.grid.width, "after the entries: {}", x(&alone));
        let (stacked, _) = horizontal_legend_clear(true);
        assert_eq!(x(&stacked), 20.0, "below the stacked legends, not over them");
    }
}
