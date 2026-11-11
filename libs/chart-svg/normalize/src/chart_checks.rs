//! Python's per-chart checks from `compile_authored_board`, read off the hoisted patch: a cropped
//! zero baseline (`validate/zero_baseline.py`) and a partial channel set
//! (`resolve/chart/positional.py::channel_error`, with no columns to bind). The values, link and label-anchor
//! checks live in their own modules and are sequenced here in Python's order.

use crate::compile::CompileError;
use crate::label_anchors::label_anchor_error;
use board_model::chart::ChartAuthored;
use diagnostic::{diag, Diagnostic};
use model_core::Tri;

pub(crate) type Patch = <board_model::board::Board as model_core::Leaf>::Patch;

/// What the checks found, each list in chart order.
#[derive(Debug, Default)]
pub struct ChartFindings {
    /// What Python's `compile` refuses: a cropped bar baseline.
    pub errors: Vec<Diagnostic>,
    /// What Python's `compile` warns of: a cropped area baseline.
    pub warnings: Vec<Diagnostic>,
    /// What only `dct validate` reports (`check_channel_sets`): compile accepts the board and
    /// leaves it to the render, where the one chart's tile errors and the rest draws.
    pub validate_only: Vec<Diagnostic>,
}

/// One family's row of `positional.py`'s tables, over the chart's authored fields.
struct Row {
    kind: &'static str,
    required: &'static [&'static str],
    channels: Vec<(&'static str, bool)>,
    pinned: Vec<(&'static str, bool)>,
}

fn named<T>(slot: &Tri<T>, text: impl Fn(&T) -> &str) -> bool {
    matches!(slot, Tri::Value(v) if !text(v).is_empty())
}

fn listed<T>(slot: &Option<Vec<T>>) -> bool {
    slot.as_ref().is_some_and(|v| !v.is_empty())
}

fn row(chart: &ChartAuthored) -> Option<Row> {
    use ChartAuthored as C;
    let cartesian = |c: &board_model::chart::CartesianChartFieldsAuthored| {
        vec![
            ("x", named(&c.x, |v| v.as_str())),
            ("y", matches!(&c.y, Tri::Value(y) if !y.is_blank())),
            ("color", named(&c.color, |v| v.as_str())),
        ]
    };
    let multiples = |c: &board_model::chart::CartesianChartFieldsAuthored| {
        ("multiples", matches!(c.multiples, Tri::Value(_)))
    };
    let with = |mut v: Vec<(&'static str, bool)>, more: Vec<(&'static str, bool)>| {
        v.extend(more);
        v
    };
    let row = match chart {
        C::Bar(c) => Row {
            kind: "bar",
            required: &["x", "y"],
            channels: with(
                cartesian(&c.cartesian),
                vec![("pattern", named(&c.pattern.pattern, |v| v.as_str()))],
            ),
            pinned: vec![
                ("y_start", named(&c.y_start, |v| v.as_str())),
                ("layers", listed(&c.layers)),
                multiples(&c.cartesian),
                ("tooltip", listed(&c.tooltip.tooltip)),
            ],
        },
        C::Histogram(c) => Row {
            kind: "histogram",
            required: &["x"],
            channels: vec![("x", named(&c.cartesian.x, |v| v.as_str()))],
            pinned: vec![],
        },
        C::Line(c) => Row {
            kind: "line",
            required: &["x", "y"],
            channels: cartesian(&c.cartesian),
            pinned: vec![
                ("connect", named(&c.connect, |v| v.as_str())),
                ("layers", listed(&c.layers)),
                multiples(&c.cartesian),
                ("tooltip", listed(&c.tooltip.tooltip)),
            ],
        },
        C::Area(c) => Row {
            kind: "area",
            required: &["x", "y"],
            channels: with(
                cartesian(&c.cartesian),
                vec![("pattern", named(&c.pattern.pattern, |v| v.as_str()))],
            ),
            pinned: vec![
                ("layers", listed(&c.layers)),
                multiples(&c.cartesian),
                ("tooltip", listed(&c.tooltip.tooltip)),
            ],
        },
        C::Scatter(c) => Row {
            kind: "scatter",
            required: &["x", "y"],
            channels: with(
                cartesian(&c.cartesian),
                vec![
                    ("size", named(&c.size, |v| v.as_str())),
                    ("shape", named(&c.shape, |v| v.as_str())),
                ],
            ),
            pinned: vec![
                ("layers", listed(&c.layers)),
                multiples(&c.cartesian),
                ("tooltip", listed(&c.tooltip.tooltip)),
            ],
        },
        C::Heatmap(c) => Row {
            kind: "heatmap",
            required: &["x", "y"],
            channels: with(
                cartesian(&c.cartesian),
                vec![("pattern", named(&c.pattern.pattern, |v| v.as_str()))],
            ),
            pinned: vec![multiples(&c.cartesian)],
        },
        C::Pie(c) | C::Donut(c) => Row {
            kind: if matches!(chart, C::Pie(_)) {
                "pie"
            } else {
                "donut"
            },
            required: &["theta"],
            channels: vec![
                ("theta", named(&c.radial.theta, |v| v.as_str())),
                ("color", named(&c.radial.color, |v| v.as_str())),
                ("pattern", named(&c.pattern.pattern, |v| v.as_str())),
            ],
            pinned: vec![("tooltip", listed(&c.tooltip.tooltip))],
        },
        C::Kpi(c) => Row {
            kind: "kpi",
            required: &["value"],
            channels: vec![("value", named(&c.value, |v| v.as_str()))],
            pinned: vec![],
        },
        _ => return None,
    };
    Some(row)
}

/// ``_names``: backticked, joined with commas and a last `and`.
fn quoted(names: &[&str]) -> String {
    let q: Vec<String> = names.iter().map(|n| format!("`{n}`")).collect();
    match q.as_slice() {
        [one] => one.clone(),
        [init @ .., last] => format!("{} and {last}", init.join(", ")),
        [] => String::new(),
    }
}

/// `channel_error(chart, ())`: why the chart's named channels cannot bind, whatever its query
/// returns. An unnamed chart with a query binds by position, which needs the columns.
fn channel_error(id: &str, chart: &ChartAuthored) -> Option<Diagnostic> {
    let row = row(chart)?;
    let named: Vec<&str> = row.channels.iter().filter(|c| c.1).map(|c| c.0).collect();
    let pinned: Vec<&str> = row.pinned.iter().filter(|c| c.1).map(|c| c.0).collect();
    if !named.is_empty() || !pinned.is_empty() {
        let missing: Vec<&str> = row
            .required
            .iter()
            .copied()
            .filter(|r| !named.contains(r))
            .collect();
        if missing.is_empty() {
            return None;
        }
        let all: Vec<&str> = named.iter().chain(&pinned).copied().collect();
        return Some(diag::err_chart_channels_incomplete(
            id,
            row.kind,
            quoted(&all),
            quoted(&missing),
            quoted(row.required),
        ));
    }
    let has_query = chart
        .base()
        .is_some_and(|b| matches!(b.query, Tri::Value(_)));
    (!has_query).then(|| diag::err_chart_channels_no_query(id, row.kind, quoted(row.required)))
}

/// The path `axis_y.scale.continuous.zero` names, `false` when the chart's own style authors it so.
macro_rules! zero_false {
    ($axis_y:expr) => {
        matches!(
            $axis_y,
            Tri::Value(a) if matches!(&a.scale, Tri::Value(s) if matches!(&s.continuous, Tri::Value(c) if matches!(&c.zero, Tri::Value(model_core::Engine::Value(false)))))
        )
    };
}

/// `validate_board_zero_baselines`: `axis_y.scale.continuous.zero: false` on a bar (refused, unless
/// it floats from a `y_start`) or an area (a warning), read from the chart's own `style:`.
fn zero_baseline(id: &str, chart: &ChartAuthored) -> Option<Diagnostic> {
    let text = format!("charts.{id}.style.axis_y.scale.continuous.zero");
    let at = || {
        crate::board_warnings::path(&[
            "charts",
            id,
            "style",
            "axis_y",
            "scale",
            "continuous",
            "zero",
        ])
    };
    match chart {
        ChartAuthored::Area(c) if zero_false!(&c.style.cartesian.axis_y) => {
            Some(diag::warn_area_zero_baseline_cropped(id).at_path(at()))
        }
        ChartAuthored::Bar(c) | ChartAuthored::Histogram(c)
            if zero_false!(&c.style.cartesian.axis_y) && !matches!(c.y_start, Tri::Value(_)) =>
        {
            Some(diag::err_bar_zero_baseline_cropped(id, text).at_path(at()))
        }
        _ => None,
    }
}

/// Every finding, each list in Python's order. `public` names a chart as users see it.
pub fn chart_checks(
    patch: &Patch,
    public: &dyn Fn(&str) -> Result<String, CompileError>,
) -> Result<ChartFindings, CompileError> {
    let mut out = ChartFindings::default();
    // Python checks the inline literals, then links, and raises at the first bad one.
    out.errors
        .extend(crate::values_kinds::values_kind_error(patch));
    if out.errors.is_empty() {
        out.errors.extend(crate::links::link_error(patch, public)?);
    }
    for (key, chart) in &patch.body.charts {
        let Some(chart) = chart else { continue };
        out.validate_only
            .extend(channel_error(&public(key)?, chart));
    }
    for (key, chart) in &patch.body.charts {
        let Some(chart) = chart else { continue };
        let id = public(key)?;
        match (zero_baseline(&id, chart), chart) {
            (Some(d), ChartAuthored::Area(_)) => out.warnings.push(d),
            // Python raises at the first cropped bar.
            (Some(d), _) if out.errors.is_empty() => out.errors.push(d),
            (Some(_), _) => {}
            (None, _) => {}
        }
    }
    // Python raises at the first anchored label, after the baselines.
    if out.errors.is_empty() {
        for (key, chart) in &patch.body.charts {
            let Some(chart) = chart else { continue };
            if let Some(d) = label_anchor_error(&public(key)?, chart) {
                out.errors.push(d);
                break;
            }
        }
    }
    Ok(out)
}
