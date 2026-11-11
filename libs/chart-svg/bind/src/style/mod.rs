//! The data-chosen style slots (D-24): which tiers speak for a slot, in what order.
//! Normalize folds the total slots into a chart's `style`; every slot a fact of the
//! data chooses (axis by channel type, legend, title) stays per tier, and a ladder
//! here walks it leaf by leaf (`walk.rs`). A ladder reads three tiers, most specific
//! first: the chart's own patches, its family's (`charts.<family>`), and the board's
//! (`charts.*`), whose total slot ends the list.
//!
//! `axis.rs`, `legend.rs`, `title.rs` (and the subtitle) and `marks.rs` (a layer's mark over
//! its parent's) each declare their list once.

pub mod axis;
pub mod axis_parts;
pub mod legend;
pub mod marks;
pub mod title;

use board_model::chart::Chart;
use board_model::style::area::{AreaChartStyle, AreaChartStylePatch};
use board_model::style::axis::{
    AxisXStyle, AxisYStyle, BandAxisStyle, BaseAxisStyle, QuantitativeAxisStyle,
};
use board_model::style::bar::{BarChartStyle, BarChartStylePatch};
use board_model::style::board::ChartTitleStyle;
use board_model::style::charts::ChartsStyle;
use board_model::style::heatmap::{HeatmapChartStyle, HeatmapChartStylePatch};
use board_model::style::histogram::{HistogramChartStyle, HistogramChartStylePatch};
use board_model::style::legend::LegendStyleBase;
use board_model::style::line::{LineChartStyle, LineChartStylePatch};
use board_model::style::scatter::{ScatterChartStyle, ScatterChartStylePatch};
use diagnostic::{diag, Diagnostic};
use board_model::style::style::Style;
use model_core::{Patch, Tri};

/// The patches one tier sets for the slots a data fact chooses. A family that cannot
/// carry a slot (a heatmap has no `axis_quantitative`) leaves it `None`.
#[derive(Default, Clone, Copy)]
pub struct Tier<'a> {
    pub axis: Option<&'a Patch<BaseAxisStyle>>,
    pub axis_x: Option<&'a Patch<AxisXStyle>>,
    pub axis_y: Option<&'a Patch<AxisYStyle>>,
    pub axis_quantitative: Option<&'a Patch<QuantitativeAxisStyle>>,
    pub axis_band: Option<&'a Patch<BandAxisStyle>>,
    pub legend: Option<&'a Patch<LegendStyleBase>>,
    pub title: Option<&'a Patch<ChartTitleStyle>>,
}

impl Tier<'_> {
    /// Whether the tier authors any axis slot: a chart whose own tier does not has no key to restate.
    pub fn sets_axis(&self) -> bool {
        [self.axis.is_some(), self.axis_x.is_some(), self.axis_y.is_some(), self.axis_quantitative.is_some(), self.axis_band.is_some()]
            .contains(&true)
    }
}

/// An authored slot speaks only when set: Python cannot tell an authored `null` from an absent one.
pub(crate) fn set<T>(slot: &Tri<T>) -> Option<&T> {
    match slot {
        Tri::Value(patch) => Some(patch),
        Tri::Unset | Tri::Null => None,
    }
}

macro_rules! tier_of {
    ($style:ty, $patch:ty, quantitative: $quantitative:tt, legend: $legend:ident) => {
        impl<'a> From<&'a $patch> for Tier<'a> {
            fn from(s: &'a $patch) -> Self {
                Tier {
                    axis: set(&s.cartesian.axis),
                    axis_x: set(&s.cartesian.axis_x),
                    axis_y: set(&s.cartesian.axis_y),
                    axis_quantitative: tier_of!(@pq s, $quantitative),
                    axis_band: set(&s.cartesian.axis_band),
                    legend: set(&s.legended.legend).map(|l| &l.base),
                    title: set(&s.cartesian.titled.title),
                }
            }
        }

        impl<'a> From<&'a $style> for Tier<'a> {
            fn from(s: &'a $style) -> Self {
                Tier {
                    axis: s.cartesian.axis.as_ref(),
                    axis_x: s.cartesian.axis_x.as_ref(),
                    axis_y: s.cartesian.axis_y.as_ref(),
                    axis_quantitative: tier_of!(@q s, $quantitative),
                    axis_band: s.cartesian.axis_band.as_ref(),
                    legend: tier_of!(@l s, $legend),
                    title: s.cartesian.titled.title.as_ref(),
                }
            }
        }
    };
    (@q $s:ident, true) => { $s.quantitative.axis_quantitative.as_ref() };
    (@q $s:ident, false) => { None };
    (@pq $s:ident, true) => { set(&$s.quantitative.axis_quantitative) };
    (@pq $s:ident, false) => { None };
    (@l $s:ident, edge) => { $s.legended.legend.as_ref().map(|l| &l.base) };
    (@l $s:ident, side) => { Some(&$s.legended.legend.base) };
}

tier_of!(BarChartStyle, BarChartStylePatch, quantitative: true, legend: edge);
tier_of!(LineChartStyle, LineChartStylePatch, quantitative: true, legend: edge);
tier_of!(AreaChartStyle, AreaChartStylePatch, quantitative: true, legend: edge);
tier_of!(ScatterChartStyle, ScatterChartStylePatch, quantitative: true, legend: side);
tier_of!(HistogramChartStyle, HistogramChartStylePatch, quantitative: true, legend: side);
tier_of!(HeatmapChartStyle, HeatmapChartStylePatch, quantitative: false, legend: side);

/// The three tiers a cartesian chart's ladders read, and the board's `charts` whose
/// total slots end them.
pub struct Tiers<'a> {
    pub own: Tier<'a>,
    pub family: Tier<'a>,
    pub charts: &'a ChartsStyle,
}

impl<'a> Tiers<'a> {
    /// `chart`'s own tier, read off `own` (its authored `style:` as a board-style layer), and its
    /// family's under `charts`. A family whose slots are not walked yet is refused by name.
    pub fn of(chart: &'a Chart, own: &'a Patch<Style>, charts: &'a ChartsStyle) -> Result<Self, Diagnostic> {
        let (own, family) = match chart {
            Chart::Bar(_) => (Tier::from(&*own.charts.bar), Tier::from(&*charts.bar)),
            // `family_slots!` folds a histogram's own style into `charts.bar`.
            Chart::Histogram(_) => (Tier::from(&*own.charts.bar), Tier::from(&*charts.histogram)),
            Chart::Line(_) => (Tier::from(&*own.charts.line), Tier::from(&*charts.line)),
            Chart::Area(_) => (Tier::from(&*own.charts.area), Tier::from(&*charts.area)),
            Chart::Scatter(_) => (Tier::from(&*own.charts.scatter), Tier::from(&*charts.scatter)),
            Chart::Heatmap(_) => (Tier::from(&*own.charts.heatmap), Tier::from(&*charts.heatmap)),
            other => {
                return Err(diag::err_internal(format!(
                    "the style walks are asked of a {} chart; only cartesian families reach them",
                    crate::board::family(other)
                )))
            }
        };
        Ok(Self {
            own,
            family,
            charts,
        })
    }
}
