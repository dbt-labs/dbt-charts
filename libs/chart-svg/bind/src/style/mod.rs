//! The data-chosen style slots (D-24): which tiers speak for a slot, in what order.
//! Normalize folds the total slots into a chart's `style`; every slot a fact of the
//! data chooses (axis by channel type, legend, title) stays per tier, and a ladder
//! here walks it leaf by leaf (`walk.rs`). A ladder reads three tiers, most specific
//! first: the chart's own patches, its family's (`charts.<family>`), and the board's
//! (`charts.*`), whose total slot ends the list.

pub mod axis;
pub mod legend;
pub mod marks;
pub mod title;

use board_model::chart::Chart;
use board_model::style::area::AreaChartStyle;
use board_model::style::axis::{
    AxisXStyle, AxisYStyle, BandAxisStyle, BaseAxisStyle, QuantitativeAxisStyle,
};
use board_model::style::bar::BarChartStyle;
use board_model::style::board::ChartTitleStyle;
use board_model::style::charts::ChartsStyle;
use board_model::style::heatmap::HeatmapChartStyle;
use board_model::style::histogram::HistogramChartStyle;
use board_model::style::legend::LegendStyleBase;
use board_model::style::line::LineChartStyle;
use board_model::style::scatter::ScatterChartStyle;
use diagnostic::{diag, Diagnostic};
use model_core::Patch;

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

macro_rules! tier_of {
    ($style:ty, quantitative: $quantitative:tt, legend: $legend:ident) => {
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
    (@l $s:ident, edge) => { $s.legended.legend.as_ref().map(|l| &l.base) };
    (@l $s:ident, side) => { Some(&$s.legended.legend.base) };
}

tier_of!(BarChartStyle, quantitative: true, legend: edge);
tier_of!(LineChartStyle, quantitative: true, legend: edge);
tier_of!(AreaChartStyle, quantitative: true, legend: edge);
tier_of!(ScatterChartStyle, quantitative: true, legend: side);
tier_of!(HistogramChartStyle, quantitative: true, legend: side);
tier_of!(HeatmapChartStyle, quantitative: false, legend: side);

/// The three tiers a cartesian chart's ladders read, and the board's `charts` whose
/// total slots end them.
pub struct Tiers<'a> {
    pub own: Tier<'a>,
    pub family: Tier<'a>,
    pub charts: &'a ChartsStyle,
}

impl<'a> Tiers<'a> {
    /// `chart`'s own tier and its family's under `charts`. A family whose slots are not
    /// walked yet is refused by name.
    pub fn of(chart: &'a Chart, charts: &'a ChartsStyle) -> Result<Self, Diagnostic> {
        let (own, family) = match chart {
            Chart::Bar(c) => (Tier::from(&c.style), Tier::from(&*charts.bar)),
            Chart::Histogram(c) => (Tier::from(&c.style), Tier::from(&*charts.histogram)),
            Chart::Line(c) => (Tier::from(&c.style), Tier::from(&*charts.line)),
            Chart::Area(c) => (Tier::from(&c.style), Tier::from(&*charts.area)),
            Chart::Scatter(c) => (Tier::from(&c.style), Tier::from(&*charts.scatter)),
            Chart::Heatmap(c) => (Tier::from(&c.style), Tier::from(&*charts.heatmap)),
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
