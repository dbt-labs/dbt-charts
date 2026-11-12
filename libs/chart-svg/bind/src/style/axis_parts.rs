//! An axis's parts as walked: grid, domain line, ticks, labels and title. The rungs of an
//! [`AxisLadder`] are projected onto the shared axis shape and laid, lowest first, over the
//! placing scope's `charts.axis`, so the cascade resolves tokens, named fonts and inherited
//! leaves once; the axis's total is what the scope's `charts.axis` ends as (the scope is
//! `ChartStyles.scope`). Any set leaf the walk does not read is refused by name.

use board_model::board::theme::{built_in_theme, BASE_THEME};
use board_model::style::axis::{
    AxisYLabelPosition, SwitchOrPixels, TimeUnit, XAxisPosition, YAxisPosition,
    AxisTicksStylePatch, BaseAxisStyle, BaseAxisStylePatch,
};
use board_model::style::font::{FontSlant, LetterCase, TextDecoration};
use board_model::style::charts::ChartsStyle;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use display_text::font::family_is_tabular;
use model_core::{DropNulls, Engine, Patch, Resolved, SetLeaves, Tri};

use super::axis::{AxisLadder, AxisName, AxisRung, TicksShown};
use crate::column_kind::ChannelType;
use crate::families::cartesian::unsupported;
use crate::walk::sparse;
use crate::bound::{AxisStyle, Side, Face, Labels, Line, Ticks, TimeLabelStyle, Title};

/// The leaves of the x axis that act only on a time axis. Every theme sets them, so a rung may
/// hold them only at the theme's value; `time_unit` may hold `none`, the one value
/// `AxisLadder::unwalked_leaf` reads rather than refuses.
const TIME_ONLY: [&str; 5] = ["labels.tilt_increments", "labels.clock", "fiscal_year_start_month", "fill", "time_unit"];

/// The leaves of a rung the walk reads, besides the `scale` block, the band position and the
/// leaves `AxisLadder::unwalked_leaf` already refuses; any other set leaf is refused by name.
const WALKED: [&str; 41] = [
    "grid.visible",
    "grid.color",
    "grid.width",
    "grid.opacity",
    "grid.dash",
    "line.visible",
    "line.color",
    "line.width",
    "ticks.visible",
    "ticks.color",
    "ticks.length",
    "ticks.width",
    "ticks.offset",
    "ticks.count",
    "labels.visible",
    "labels.format",
    "labels.padding",
    "labels.min_gap",
    "labels.position",
    "position",
    "labels.max_width",
    "labels.angle",
    "labels.align",
    "labels.font.extends",
    "labels.font.family",
    "labels.font.size",
    "labels.font.weight",
    "labels.font.color",
    "labels.font.style",
    "labels.font.case",
    "title.visible",
    "title.padding",
    "title.angle",
    "title.align",
    "title.font.extends",
    "title.font.family",
    "title.font.size",
    "title.font.weight",
    "title.font.color",
    "title.font.style",
    "title.font.case",
];

/// `labels.flush` is read by a quantitative or time axis, whose labels anchor on it; a band axis
/// never flushes, so there it may hold only the theme's value.
fn walked(path: &str, flushes: bool) -> bool {
    WALKED.contains(&path) || path.starts_with("scale") || path == "band_position" || flushes && path == "labels.flush"
}

/// The built-in base theme every theme folds over: the values the board's slots hold unless the
/// board changed them.
fn theme_base() -> Result<&'static Patch<Style>, Diagnostic> {
    match &built_in_theme(BASE_THEME).map_err(|e| diag::err_internal(e.to_string()))?.style {
        Tri::Value(style) => Ok(style),
        Tri::Null | Tri::Unset => Err(diag::err_internal("the base theme has no style")),
    }
}

/// The first leaf of the board's `charts.axis` the walk does not read and the board changed from
/// the theme's. Every theme sets the leaves matched here, so a board that leaves them alone holds
/// the theme's value; `grid.threshold.*` is the paint of the zero rule, which each theme colors for
/// itself.
fn changed_total_leaf(scope: &Resolved<Style>, base: &Patch<Style>, flushes: bool) -> Option<String> {
    let (set, theme) = (&scope.raw.charts.axis, &base.charts.axis);
    let mut found = None;
    set.set_leaves("", &mut |path| {
        let at_theme_value = match path {
            "labels.bound" => set.labels.bound == theme.labels.bound,
            "labels.flush" => set.labels.flush == theme.labels.flush,
            "labels.offset" => set.labels.offset == theme.labels.offset,
            _ if path.starts_with("labels.overlap.") => set.labels.overlap == theme.labels.overlap,
            _ if path.starts_with("grid.torn_baseline.") => set.grid.torn_baseline == theme.grid.torn_baseline,
            _ => path.starts_with("grid.threshold."),
        };
        if !(walked(path, flushes) || at_theme_value) {
            found.get_or_insert_with(|| path.to_string());
        }
    });
    found
}

/// An `auto` x tick visibility is unset after the cascade, as `unset_autos` leaves it.
fn visible(rule: &Option<Engine<bool, board_model::rule::Auto>>) -> Option<bool> {
    match rule {
        Some(Engine::Value(shown)) => Some(*shown),
        Some(Engine::Rule(_)) | None => None,
    }
}

impl AxisRung<'_> {
    /// The rung on the shared axis shape; `None` for the rungs that are not a style layer here.
    fn shared(&self) -> Option<Patch<BaseAxisStyle>> {
        Some(match self {
            AxisRung::Axis(p) => (*p).clone(),
            AxisRung::Quantitative(p) => p.base.clone(),
            AxisRung::Band(p) => p.base.clone(),
            AxisRung::X(p) => BaseAxisStylePatch {
                grid: p.grid.clone(),
                line: p.line.clone(),
                ticks: AxisTicksStylePatch { visible: visible(&p.ticks.visible), rest: p.ticks.rest.clone() },
                labels: p.labels.label.clone(),
                title: p.title.clone(),
                scale: Tri::Unset,
            },
            AxisRung::Y(p) => BaseAxisStylePatch {
                grid: p.grid.clone(),
                line: p.line.clone(),
                ticks: p.ticks.clone(),
                labels: p.labels.label.clone(),
                title: p.title.clone(),
                scale: Tri::Unset,
            },
            AxisRung::Total(_) | AxisRung::Column(_) => return None,
        })
    }

    /// The first set leaf the walk does not read, by name: one of the rung's own leaves. A time-only
    /// leaf passes at the value `theme` gives the board's `axis_x`, the one the walk assumes.
    fn unwalked_part(&self, theme: &Patch<Style>, flushes: bool) -> Option<String> {
        let mut found = None;
        let at_theme_value = |path: &str| match self {
            AxisRung::X(p) => {
                let Tri::Value(base) = &theme.charts.axis_x else { return false };
                match path {
                    "labels.clock" => p.labels.clock == base.labels.clock,
                    "labels.tilt_increments" => p.labels.tilt_increments == base.labels.tilt_increments,
                    "fill" => p.fill == base.fill,
                    "fiscal_year_start_month" => p.fiscal_year_start_month == base.fiscal_year_start_month,
                    "time_unit" => matches!(&p.time_unit, Tri::Value(Engine::Value(TimeUnit::None))),
                    _ => false,
                }
            }
            _ => false,
        };
        let mut note = |path: &str| {
            let read = walked(path, flushes) || TIME_ONLY.contains(&path) && at_theme_value(path);
            if !read {
                found.get_or_insert_with(|| path.to_string());
            }
        };
        match self {
            AxisRung::Axis(p) => p.set_leaves("", &mut note),
            AxisRung::Quantitative(p) => p.set_leaves("", &mut note),
            AxisRung::Band(p) => p.set_leaves("", &mut note),
            AxisRung::X(p) => p.set_leaves("", &mut note),
            AxisRung::Y(p) => p.set_leaves("", &mut note),
            AxisRung::Total(_) | AxisRung::Column(_) => {}
        }
        found
    }
}

impl AxisLadder<'_> {
    /// Where the axis sits: `position` off the highest rung that sets it. A y axis' `auto` flips
    /// to the right for endpoint labels, which no ported family draws, and where the family has no
    /// rail to yield the right side to (`y_auto_right`, a scatter or a line:
    /// `bake_ay_position_right`).
    fn side(&self, y_auto_right: bool) -> Side {
        match self.axis {
            AxisName::X => {
                let set = self.rungs.walk(|layer| match &layer.rung {
                    AxisRung::X(p) => p.position.as_ref(),
                    _ => Tri::Unset,
                });
                if set == Some(&XAxisPosition::Top) { Side::Top } else { Side::Bottom }
            }
            AxisName::Y => {
                let set = self.rungs.walk(|layer| match &layer.rung {
                    AxisRung::Y(p) => sparse(&p.position),
                    _ => Tri::Unset,
                });
                match set {
                    Some(Engine::Value(YAxisPosition::Right)) => Side::Right,
                    Some(Engine::Value(YAxisPosition::Left)) => Side::Left,
                    _ if y_auto_right => Side::Right,
                    _ => Side::Left,
                }
            }
        }
    }

    /// The ruler layer: the shared axis face's family, when the board's tier hands this axis a
    /// tabular one.
    fn ruler_face(&self, scope: &Resolved<Style>) -> Option<Patch<BaseAxisStyle>> {
        let charts = &scope.total.charts;
        let shared = &charts.axis.labels.font.family;
        let by_axis = match self.axis {
            AxisName::X => charts.axis_x.labels.label.font.family.as_ref(),
            AxisName::Y => charts.axis_y.labels.label.font.family.as_ref(),
        };
        let by_type = (self.channel == ChannelType::Quantitative)
            .then_some(charts.axis_quantitative.base.labels.font.family.as_ref())
            .flatten();
        let handed = by_type.or(by_axis).unwrap_or(shared);
        family_is_tabular(handed.as_ref()).then(|| {
            let mut swap = BaseAxisStylePatch::default();
            swap.labels.font.family = Some(shared.clone());
            swap
        })
    }

    /// The axis's parts, the rungs laid over `scope`. A leaf the walk does not read, set on any
    /// rung, is refused for `family` by name.
    pub fn parts(&self, scope: &Resolved<Style>, family: &str, horizontal_ruler: bool, y_auto_right: bool, caps_bottom: bool, labeled: bool) -> Result<AxisStyle, Diagnostic> {
        let theme = theme_base()?;
        let flushes = matches!(self.channel, ChannelType::Quantitative | ChannelType::Temporal);
        if let Some(leaf) = changed_total_leaf(scope, theme, flushes) {
            return Err(unsupported(refuse!("a {family} axis that sets `{leaf}` is not supported")));
        }
        let mut resolved = scope.clone();
        let mut ruler = horizontal_ruler.then(|| self.ruler_face(scope)).flatten();
        for layer in self.rungs.lowest_first() {
            // The board's layers are the overlays below the first rung that is not.
            if !layer.overlay && !matches!(layer.rung, AxisRung::Total(_)) {
                if let Some(swap) = ruler.take() {
                    resolved = lay(&resolved, swap)?;
                }
            }
            if let Some(leaf) = layer.rung.unwalked_part(theme, flushes) {
                return Err(unsupported(refuse!("a {family} axis that sets `{leaf}` is not supported")));
            }
            let Some(mut shared) = layer.rung.shared() else { continue };
            if layer.overlay {
                shared.drop_nulls();
            }
            resolved = lay(&resolved, shared)?;
        }
        if let Some(swap) = ruler {
            resolved = lay(&resolved, swap)?;
        }
        let above = self.rungs.walk(|layer| match &layer.rung {
            AxisRung::Y(p) => p.labels.position.as_ref(),
            _ => Tri::Unset,
        });
        if above == Some(&AxisYLabelPosition::AboveGridline) {
            return Err(unsupported(refuse!("a {family} y axis with `labels.position: above_gridline` is not supported")));
        }
        // An x axis's `auto` shows its tick stubs where the axis has no gridlines of its own or the y
        // gridlines do not reach the labels (`build_resolved_axis`); no other axis holds the rule.
        let ticks_drawn = match (self.axis, self.ticks_shown()) {
            (AxisName::X, Some(TicksShown::Auto)) => Some(self.grid_visible() != Some(true) || caps_bottom),
            _ => None,
        };
        let mut parts = parts_of(&resolved.total.charts.axis, self.side(y_auto_right), family, ticks_drawn, labeled)?;
        if self.axis == AxisName::X && self.channel == ChannelType::Temporal {
            parts.time = Some(time_label_style(&resolved.total.charts)?);
        }
        Ok(parts)
    }
}

/// How a time axis crowds its labels: the walked `labels.overlap` switches and the theme's tilt
/// ladder (`labels.tilt_increments`, which no rung may move off the theme's value) and usable ratio.
fn time_label_style(charts: &ChartsStyle) -> Result<TimeLabelStyle, Diagnostic> {
    let overlap = &charts.axis.labels.overlap;
    let tilt = match (overlap.tilt, charts.axis_x.labels.tilt_increments.as_ref()) {
        (true, Tri::Value(angles)) => Some(angles.0.clone()),
        (true, _) => return Err(diag::err_internal("`labels.overlap.tilt` is on but the theme leaves `labels.tilt_increments` unset")),
        (false, _) => None,
    };
    Ok(TimeLabelStyle { skip: overlap.skip, tilt, usable_ratio: charts.label_usable_ratio })
}

fn lay(scope: &Resolved<Style>, axis: Patch<BaseAxisStyle>) -> Result<Resolved<Style>, Diagnostic> {
    let mut upper = Patch::<Style>::default();
    upper.charts.axis = axis;
    scope
        .apply(&upper)
        .map_err(|e| diag::err_internal(format!("an axis rung failed to lay over its scope: {e:?}")))
}

fn line(color: &impl AsRef<str>, width: f64) -> Line {
    Line { color: color.as_ref().to_string(), width, opacity: 1.0, dash: None }
}

fn face(font: &board_model::style::font::FontStyle, family: &str) -> Result<Face, Diagnostic> {
    if font.style != FontSlant::Normal || font.case != LetterCase::None || font.decoration != TextDecoration::None {
        return Err(unsupported(refuse!("a {family} axis face with a `font.style`, `font.case` or `font.decoration` is not supported")));
    }
    Ok(Face {
        family: font.family.as_ref().to_string(),
        size: font.size,
        weight: font.weight.clone(),
        color: font.color.as_ref().to_string(),
    })
}

fn parts_of(axis: &BaseAxisStyle, side: Side, family: &str, ticks_drawn: Option<bool>, labeled: bool) -> Result<AxisStyle, Diagnostic> {
    let shown = |rule: &Engine<bool, board_model::rule::Auto>, auto: bool| match rule {
        Engine::Value(shown) => *shown,
        Engine::Rule(_) => auto,
    };
    let grid = &axis.grid;
    let ticks = &axis.ticks;
    let labels = &axis.labels;
    let title = &axis.title;
    Ok(AxisStyle {
        side,
        offset: match &ticks.rest.offset {
            Engine::Value(offset) => *offset,
            Engine::Rule(_) => 0.0,
        },
        grid: grid.visible.then(|| Line {
            color: grid.color.as_ref().to_string(),
            width: grid.width,
            opacity: grid.opacity,
            dash: grid.dash.clone(),
        }),
        threshold: grid.threshold.visible.then(|| line(&grid.threshold.color, grid.threshold.width)),
        domain: axis.line.visible.then(|| line(&axis.line.color, axis.line.width)),
        ticks: ticks_drawn.unwrap_or(ticks.visible).then(|| Ticks {
            line: line(&ticks.rest.color, ticks.rest.width),
            length: ticks.rest.length,
        }),
        // An `auto` label set draws; `auto` is turned to unset after the cascade.
        labels: shown(&labels.visible, true)
            .then(|| {
                Ok::<_, Diagnostic>(Labels {
                    face: face(&labels.font, family)?,
                    padding: labels.padding,
                    min_gap: labels.min_gap,
                    max_width: labels.max_width.clone(),
                    angle: labels.angle.clone(),
                    align: labels.align.clone(),
                    flush: match &labels.flush {
                        Engine::Value(SwitchOrPixels::Switch(true)) => Some(1.0),
                        Engine::Value(SwitchOrPixels::Pixels(pixels)) => Some(*pixels),
                        Engine::Value(SwitchOrPixels::Switch(false)) | Engine::Rule(_) => None,
                    },
                })
            })
            .transpose()?,
        // An `auto` title shows only when the chart authors an `x_label` or `y_label`.
        title: shown(&title.visible, labeled)
            .then(|| {
                Ok::<_, Diagnostic>(Title {
                    face: face(&title.font, family)?,
                    padding: title.padding,
                    angle: title.angle.clone(),
                    align: title.align.clone(),
                })
            })
            .transpose()?,
        time: None,
    })
}
