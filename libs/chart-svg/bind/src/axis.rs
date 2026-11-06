//! An axis slot's priority list: which style layers speak for an axis, in what
//! order, picked by the channel type the data gave it. Mirrors `_axis_layers` /
//! `_merge_axis_cascade` read top down:
//!
//! ```text
//! chart.<type>, chart.<axis>, chart.axis, <column's declared format>,
//! family.<type>, family.<axis>, family.axis, board.<type>, board.<axis>, board.axis
//! ```
//!
//! `<type>` is `axis_quantitative` for a quantitative channel, `axis_band` for a
//! nominal one and absent for a temporal one; `<axis>` is `axis_x` or `axis_y`. The
//! chart's own patches ride `chart_styles[id]`, its family's in `chart_styles[id].family`
//! and the board's in `chart_tiers`. A layer's leaf is unset when the seam carries it
//! as null: the seam cannot tell an explicit `null` that clears a lower layer from an
//! omitted field, so null always inherits.

use board_types::{
    AxisLabelStyleFormat, AxisLabelStylePatchFormat, AxisXStyle, AxisXStylePatch, AxisYStyle,
    AxisYStylePatch, BandAxisStyle, BandAxisStylePatch, BaseAxisStyle, BaseAxisStylePatch,
    BoardChartStylesValue, BoardChartTiers, DimensionLabelStyleFormat, DimensionLabelStylePatchFormat,
    FormatConfig, FormatConfigNotation, FormatConfigRepeat, FormatConfigSignPlacement,
    QuantitativeAxisStyle, QuantitativeAxisStylePatch,
};
use diagnostic::Diagnostic;

use crate::channel_type::ChannelType;
use crate::formats::{AuthoredFormat, Notation, Repeat, SignPlacement};
use crate::walk::Ladder;

/// Every `format:` slot's union is the same three shapes (an engine name, a bare
/// spec, a mapping) and null; null is "no format".
pub trait AuthoredSlot {
    /// The format the slot authors, `None` for null.
    fn authored(&self) -> Option<AuthoredFormat>;
}

fn from_config(config: &FormatConfig) -> AuthoredFormat {
    AuthoredFormat {
        spec: config.spec.clone().filter(|spec| !spec.is_empty()),
        prefix: config.prefix.clone(),
        suffix: config.suffix.clone(),
        notation: config.notation.map(|notation| match notation {
            FormatConfigNotation::Analytic => Notation::Analytic,
            FormatConfigNotation::Narrative => Notation::Narrative,
        }),
        sign_placement: config.sign_placement.map(|placement| match placement {
            FormatConfigSignPlacement::AfterPrefix => SignPlacement::AfterPrefix,
            FormatConfigSignPlacement::BeforePrefix => SignPlacement::BeforePrefix,
        }),
        repeat: config.repeat.map(|repeat| match repeat {
            FormatConfigRepeat::Anchor => Repeat::Anchor,
            FormatConfigRepeat::Every => Repeat::Every,
        }),
    }
}

macro_rules! authored_from_slot {
    ($($slot:ident),*) => {$(
        impl AuthoredSlot for $slot {
            fn authored(&self) -> Option<AuthoredFormat> {
                match self {
                    $slot::Variant0(name) => Some(AuthoredFormat::named(&name.to_string())),
                    $slot::Variant1(spec) => Some(AuthoredFormat::named(spec)),
                    $slot::Variant2(config) => Some(from_config(config)),
                    $slot::Variant3 => None,
                }
            }
        }
    )*};
}
authored_from_slot!(
    AxisLabelStyleFormat,
    AxisLabelStylePatchFormat,
    DimensionLabelStyleFormat,
    DimensionLabelStylePatchFormat
);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AxisName {
    X,
    Y,
}

/// One layer of an axis's priority list.
#[derive(Clone, Copy)]
pub enum AxisRung<'a> {
    Axis(&'a BaseAxisStylePatch),
    X(&'a AxisXStylePatch),
    Y(&'a AxisYStylePatch),
    Quantitative(&'a QuantitativeAxisStylePatch),
    Band(&'a BandAxisStylePatch),
    BoardAxis(&'a BaseAxisStyle),
    BoardX(&'a AxisXStyle),
    BoardY(&'a AxisYStyle),
    BoardQuantitative(&'a QuantitativeAxisStyle),
    BoardBand(&'a BandAxisStyle),
    /// The format the axis column's query declares.
    Column(&'a AuthoredFormat),
}

/// Applies `$body` to the style behind a rung, whichever model it is: every axis model
/// names its `labels` the same way. `$column` answers for the column rung.
macro_rules! on_rung {
    ($rung:expr, $s:ident => $body:expr, column($c:ident) => $column:expr) => {
        match $rung {
            AxisRung::Axis($s) => $body,
            AxisRung::X($s) => $body,
            AxisRung::Y($s) => $body,
            AxisRung::Quantitative($s) => $body,
            AxisRung::Band($s) => $body,
            AxisRung::BoardAxis($s) => $body,
            AxisRung::BoardX($s) => $body,
            AxisRung::BoardY($s) => $body,
            AxisRung::BoardQuantitative($s) => $body,
            AxisRung::BoardBand($s) => $body,
            AxisRung::Column($c) => $column,
        }
    };
}

impl AxisRung<'_> {
    fn labels_format(&self) -> Option<AuthoredFormat> {
        on_rung!(self, s => s.labels.as_ref().and_then(|labels| labels.format.authored()), column(c) => Some((*c).clone()))
    }

    fn labels_visible(&self) -> Option<bool> {
        on_rung!(self, s => s.labels.as_ref().and_then(|labels| labels.visible), column(_c) => None)
    }

    fn band_position(&self) -> Option<f64> {
        match self {
            AxisRung::Band(s) => s.band_position,
            AxisRung::BoardBand(s) => s.band_position,
            _ => None,
        }
    }
}

/// The axis patches of one layer (a chart's own, or its family's).
#[derive(Default, Clone, Copy)]
struct Patches<'a> {
    axis: Option<&'a BaseAxisStylePatch>,
    x: Option<&'a AxisXStylePatch>,
    y: Option<&'a AxisYStylePatch>,
    quantitative: Option<&'a QuantitativeAxisStylePatch>,
    band: Option<&'a BandAxisStylePatch>,
}

/// A chart's own axis patches, and its family's when the family draws axes.
fn style_patches(style: &BoardChartStylesValue) -> Result<(Patches<'_>, Patches<'_>), Diagnostic> {
    macro_rules! chart_patches {
        ($s:expr) => {
            Patches {
                axis: $s.axis.as_ref(),
                x: $s.axis_x.as_ref(),
                y: $s.axis_y.as_ref(),
                quantitative: $s.axis_quantitative.as_ref(),
                band: $s.axis_band.as_ref(),
            }
        };
    }
    macro_rules! family_patches {
        ($s:expr, quantitative) => {
            Patches {
                axis: $s.family.axis.as_ref(),
                x: $s.family.axis_x.as_ref(),
                y: $s.family.axis_y.as_ref(),
                quantitative: $s.family.axis_quantitative.as_ref(),
                band: $s.family.axis_band.as_ref(),
            }
        };
        ($s:expr) => {
            Patches {
                axis: $s.family.axis.as_ref(),
                x: $s.family.axis_x.as_ref(),
                y: $s.family.axis_y.as_ref(),
                quantitative: None,
                band: $s.family.axis_band.as_ref(),
            }
        };
    }
    use BoardChartStylesValue as S;
    Ok(match style {
        S::BarStyle(s) => (chart_patches!(s), family_patches!(s, quantitative)),
        S::HistogramStyle(s) => (chart_patches!(s), family_patches!(s, quantitative)),
        S::LineStyle(s) => (chart_patches!(s), family_patches!(s, quantitative)),
        S::AreaStyle(s) => (chart_patches!(s), family_patches!(s, quantitative)),
        S::ScatterStyle(s) => (chart_patches!(s), family_patches!(s, quantitative)),
        S::HeatmapStyle(s) => (chart_patches!(s), family_patches!(s)),
        S::PieStyle(_)
        | S::KpiStyle(_)
        | S::TableStyle(_)
        | S::GeoshapeStyle(_)
        | S::PointMapStyle(_)
        | S::SparkBarStyle(_)
        | S::CalloutStyle(_)
        | S::CustomStyle(_) => {
            return Err(Diagnostic::refused(format!("a {} chart draws no cartesian axis", family_name(style))));
        }
    })
}

fn family_name(style: &BoardChartStylesValue) -> &'static str {
    use BoardChartStylesValue as S;
    match style {
        S::PieStyle(_) => "pie",
        S::KpiStyle(_) => "kpi",
        S::TableStyle(_) => "table",
        S::GeoshapeStyle(_) => "geoshape",
        S::PointMapStyle(_) => "point_map",
        S::SparkBarStyle(_) => "spark_bar",
        S::CalloutStyle(_) => "callout",
        S::CustomStyle(_) => "custom",
        _ => "cartesian",
    }
}

/// An axis's declared priority list.
pub struct AxisLadder<'a> {
    channel: ChannelType,
    rungs: Ladder<AxisRung<'a>>,
}

impl<'a> AxisLadder<'a> {
    /// The list for `axis` carrying a `channel`-typed field. `column_format` is the
    /// format the axis column's query declares. `temporal_band` marks a band axis whose
    /// column is a date (a heatmap's nominal month axis): it skips the board's
    /// `axis_band` so time stays on the board's `axis` voice; a family's or chart's own
    /// `axis_band` still styles it.
    pub fn new(
        axis: AxisName,
        channel: ChannelType,
        style: &'a BoardChartStylesValue,
        tiers: &'a BoardChartTiers,
        column_format: Option<&'a AuthoredFormat>,
        temporal_band: bool,
    ) -> Result<Self, Diagnostic> {
        let (chart, family) = style_patches(style)?;
        let patches = |p: Patches<'a>| {
            let by_channel = match axis {
                AxisName::X => p.x.map(AxisRung::X),
                AxisName::Y => p.y.map(AxisRung::Y),
            };
            let by_type = match channel {
                ChannelType::Quantitative => p.quantitative.map(AxisRung::Quantitative),
                ChannelType::Nominal => p.band.map(AxisRung::Band),
                ChannelType::Temporal => None,
            };
            [by_type, by_channel, p.axis.map(AxisRung::Axis)]
        };
        let board_type = match channel {
            ChannelType::Quantitative => Some(AxisRung::BoardQuantitative(&tiers.axis_quantitative)),
            ChannelType::Nominal if !temporal_band => Some(AxisRung::BoardBand(&tiers.axis_band)),
            ChannelType::Nominal | ChannelType::Temporal => None,
        };
        let board_channel = match axis {
            AxisName::X => AxisRung::BoardX(&tiers.axis_x),
            AxisName::Y => AxisRung::BoardY(&tiers.axis_y),
        };
        let rungs = patches(chart)
            .into_iter()
            .chain([column_format.map(AxisRung::Column)])
            .chain(patches(family))
            .chain([board_type, Some(board_channel), Some(AxisRung::BoardAxis(&tiers.axis))])
            .flatten()
            .collect();
        Ok(Self { channel, rungs: Ladder::new(rungs) })
    }

    /// The tick label format: the first rung that sets one. `auto` is decided here
    /// from the channel type: the bounded SI `number` on a quantitative axis, no format
    /// on any other, whose labels name themselves.
    pub fn labels_format(&self) -> Option<AuthoredFormat> {
        let format = self.rungs.walk(AxisRung::labels_format)?;
        if format.spec.as_deref() == Some("auto") {
            return (self.channel == ChannelType::Quantitative).then(|| AuthoredFormat::named("number"));
        }
        Some(format)
    }

    pub fn labels_visible(&self) -> Option<bool> {
        self.rungs.walk(AxisRung::labels_visible)
    }

    /// Where a band sits within its step (0 to 1).
    pub fn band_position(&self) -> Option<f64> {
        self.rungs.walk(AxisRung::band_position)
    }
}
