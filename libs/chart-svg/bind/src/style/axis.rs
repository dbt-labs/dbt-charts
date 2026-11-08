//! An axis slot's priority list: which style layers speak for an axis, in what
//! order, picked by the channel type the data gave it. Mirrors `_axis_layers` /
//! `_merge_axis_cascade` (`resolve/style/axis_cascade.py`) read top down:
//!
//! ```text
//! chart.<type>, chart.<axis>, chart.axis, <column's declared format>,
//! family.<type>, family.<axis>, family.axis, board.<type>, board.<axis>, board.axis
//! ```
//!
//! `<type>` is `axis_quantitative` for a quantitative channel, `axis_band` for a
//! nominal one and absent for a temporal one; `<axis>` is `axis_x` or `axis_y`. Only
//! `board.axis` is total; it ends the list. The computed `RulerFont` rung joins when a
//! font leaf is walked.

use board_model::primitives::{AffixRepeat, FormatConfig, FormatSpec, Notation, SignPlacement};
use board_model::rule::Auto;
use board_model::style::axis::{
    AxisXStyle, AxisYStyle, BandAxisStyle, BaseAxisStyle, BaseScaleStyle, QuantitativeAxisStyle,
};
use diagnostic::Diagnostic;
use model_core::{Engine, Leaf, Patch, Tri};

use super::{Tier, Tiers};
use crate::channel_type::ChannelType;
use crate::formats::{self, AuthoredFormat};
use crate::walk::{absent, sparse, Ladder};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AxisName {
    X,
    Y,
}

/// A `format:` block as the board model holds it.
type Block = <FormatConfig as Leaf>::Whole;

/// One layer of an axis's priority list.
pub enum AxisRung<'a> {
    Axis(&'a Patch<BaseAxisStyle>),
    X(&'a Patch<AxisXStyle>),
    Y(&'a Patch<AxisYStyle>),
    Quantitative(&'a Patch<QuantitativeAxisStyle>),
    Band(&'a Patch<BandAxisStyle>),
    /// `board.axis`, the total slot.
    Total(&'a BaseAxisStyle),
    /// The format the axis column's query declares.
    Column(&'a FormatSpec),
}

/// A rung and how it reads `null`. The chart's and the family's patches state it: a `null`
/// clears every layer below. The board's `charts.axis_*` slots are overlays Python rebuilds
/// without their nulls (`_full_model_as_overlay`), so a `null` there inherits.
struct Layer<'a> {
    rung: AxisRung<'a>,
    overlay: bool,
}

impl<'a> Layer<'a> {
    fn patch(rung: AxisRung<'a>) -> Self {
        Self {
            rung,
            overlay: false,
        }
    }

    fn overlay(rung: AxisRung<'a>) -> Self {
        Self {
            rung,
            overlay: true,
        }
    }

    fn read<'s, T>(&'s self, leaf: impl FnOnce(&'s AxisRung<'a>) -> Tri<&'s T>) -> Tri<&'s T> {
        match leaf(&self.rung) {
            Tri::Null if self.overlay => Tri::Unset,
            read => read,
        }
    }

    fn format(&self) -> Tri<&FormatSpec> {
        self.read(AxisRung::labels_format)
    }

    /// One field of a block format. A block merges field by field over the blocks below
    /// it (`merge_onto_base` recurses into models); a name or a `null` below ends every
    /// field, since a block laid over a bare name replaces it whole.
    fn format_field<T>(&self, select: impl FnOnce(&Block) -> &Tri<T>) -> Tri<&T> {
        match self.format() {
            // The overlay's nulls are dropped at every depth, in a block's fields too.
            Tri::Value(FormatSpec::Block(block)) => match select(block).as_ref() {
                Tri::Null if self.overlay => Tri::Unset,
                field => field,
            },
            Tri::Value(FormatSpec::Name(_)) => Tri::Null,
            Tri::Null => Tri::Null,
            Tri::Unset => Tri::Unset,
        }
    }
}

impl<'a> AxisRung<'a> {
    fn labels_format(&self) -> Tri<&FormatSpec> {
        match self {
            AxisRung::Axis(p) => p.labels.format.as_ref(),
            AxisRung::X(p) => p.labels.label.format.as_ref(),
            AxisRung::Y(p) => p.labels.label.format.as_ref(),
            AxisRung::Quantitative(p) => p.base.labels.format.as_ref(),
            AxisRung::Band(p) => p.base.labels.format.as_ref(),
            AxisRung::Total(t) => absent(&t.labels.format),
            AxisRung::Column(f) => Tri::Value(f),
        }
    }

    fn labels_visible(&self) -> Tri<&Engine<bool, Auto>> {
        match self {
            AxisRung::Axis(p) => sparse(&p.labels.visible),
            AxisRung::X(p) => sparse(&p.labels.label.visible),
            AxisRung::Y(p) => sparse(&p.labels.label.visible),
            AxisRung::Quantitative(p) => sparse(&p.base.labels.visible),
            AxisRung::Band(p) => sparse(&p.base.labels.visible),
            AxisRung::Total(t) => Tri::Value(&t.labels.visible),
            AxisRung::Column(_) => Tri::Unset,
        }
    }

    fn grid_visible(&self) -> Tri<&bool> {
        match self {
            AxisRung::Axis(p) => sparse(&p.grid.visible),
            AxisRung::X(p) => sparse(&p.grid.visible),
            AxisRung::Y(p) => sparse(&p.grid.visible),
            AxisRung::Quantitative(p) => sparse(&p.base.grid.visible),
            AxisRung::Band(p) => sparse(&p.base.grid.visible),
            AxisRung::Total(t) => Tri::Value(&t.grid.visible),
            AxisRung::Column(_) => Tri::Unset,
        }
    }

    fn threshold_visible(&self) -> Tri<&bool> {
        match self {
            AxisRung::Axis(p) => sparse(&p.grid.threshold.visible),
            AxisRung::X(p) => sparse(&p.grid.threshold.visible),
            AxisRung::Y(p) => sparse(&p.grid.threshold.visible),
            AxisRung::Quantitative(p) => sparse(&p.base.grid.threshold.visible),
            AxisRung::Band(p) => sparse(&p.base.grid.threshold.visible),
            AxisRung::Total(t) => Tri::Value(&t.grid.threshold.visible),
            AxisRung::Column(_) => Tri::Unset,
        }
    }

    /// The rung's `scale` block, with an x scale's own `x_reverse`.
    fn scale(&self) -> Tri<Scale<'_>> {
        fn patch(scale: &Tri<Patch<BaseScaleStyle>>) -> Tri<Scale<'_>> {
            map(scale.as_ref(), |base| Scale::Patch(base, None))
        }
        match self {
            AxisRung::Axis(p) => patch(&p.scale),
            AxisRung::X(p) => map(p.scale.as_ref(), |x| Scale::Patch(&x.base, Some(&x.x_reverse))),
            AxisRung::Y(p) => patch(&p.scale),
            AxisRung::Quantitative(p) => patch(&p.base.scale),
            AxisRung::Band(p) => patch(&p.base.scale),
            AxisRung::Total(t) => map(absent(&t.scale), Scale::Total),
            AxisRung::Column(_) => Tri::Unset,
        }
    }

    fn ticks_count(&self) -> Tri<&Engine<i64, Auto>> {
        match self {
            AxisRung::Axis(p) => p.ticks.rest.count.as_ref(),
            AxisRung::X(p) => p.ticks.rest.count.as_ref(),
            AxisRung::Y(p) => p.ticks.rest.count.as_ref(),
            AxisRung::Quantitative(p) => p.base.ticks.rest.count.as_ref(),
            AxisRung::Band(p) => p.base.ticks.rest.count.as_ref(),
            AxisRung::Total(t) => absent(&t.ticks.rest.count),
            AxisRung::Column(_) => Tri::Unset,
        }
    }

    fn sets_labels_expr(&self) -> bool {
        match self {
            AxisRung::Axis(p) => !matches!(p.labels.expr, Tri::Unset),
            AxisRung::X(p) => !matches!(p.labels.label.expr, Tri::Unset),
            AxisRung::Y(p) => !matches!(p.labels.label.expr, Tri::Unset),
            AxisRung::Quantitative(p) => !matches!(p.base.labels.expr, Tri::Unset),
            AxisRung::Band(p) => !matches!(p.base.labels.expr, Tri::Unset),
            AxisRung::Total(t) => t.labels.expr.is_some(),
            AxisRung::Column(_) => false,
        }
    }

    /// An x leaf that decides a time or ordinal x ahead of the data (production's x-type law
    /// reads `type` and `time_unit`), or labels one.
    fn x_type_leaf(&self) -> Option<&'static str> {
        let AxisRung::X(p) = self else { return None };
        [
            ("type", !matches!(p.r#type, Tri::Unset)),
            ("time_unit", !matches!(p.time_unit, Tri::Unset)),
            ("ticks.time_unit", !matches!(p.ticks.time_unit, Tri::Unset)),
            ("labels.time_unit", !matches!(p.labels.time_unit, Tri::Unset)),
            ("labels.values", !matches!(p.labels.values, Tri::Unset)),
        ]
        .into_iter()
        .find_map(|(name, set)| set.then_some(name))
    }

    fn sets_ticks_step(&self) -> bool {
        match self {
            AxisRung::Axis(p) => !matches!(p.ticks.rest.step, Tri::Unset),
            AxisRung::X(p) => !matches!(p.ticks.rest.step, Tri::Unset),
            AxisRung::Y(p) => !matches!(p.ticks.rest.step, Tri::Unset),
            AxisRung::Quantitative(p) => !matches!(p.base.ticks.rest.step, Tri::Unset),
            AxisRung::Band(p) => !matches!(p.base.ticks.rest.step, Tri::Unset),
            AxisRung::Total(t) => t.ticks.rest.step.is_some(),
            AxisRung::Column(_) => false,
        }
    }

    /// `_axis_layers`'s band position reads `is not None`: an authored `null` falls
    /// through to the next band slot, where a patch's `null` ends every other leaf's walk.
    fn band_position(&self) -> Tri<&f64> {
        match self {
            AxisRung::Band(p) => match &p.band_position {
                Tri::Value(v) => Tri::Value(v),
                Tri::Null | Tri::Unset => Tri::Unset,
            },
            _ => Tri::Unset,
        }
    }
}

fn map<T, U>(tri: Tri<T>, f: impl FnOnce(T) -> U) -> Tri<U> {
    match tri {
        Tri::Value(value) => Tri::Value(f(value)),
        Tri::Null => Tri::Null,
        Tri::Unset => Tri::Unset,
    }
}

/// A rung's `scale` block: a tier's patch or the total's value.
enum Scale<'a> {
    Patch(&'a Patch<BaseScaleStyle>, Option<&'a Tri<bool>>),
    Total(&'a BaseScaleStyle),
}

impl<'a> Scale<'a> {
    fn headroom(&self) -> Tri<&'a f64> {
        match self {
            Scale::Patch(p, _) => p.headroom.as_ref(),
            Scale::Total(t) => absent(&t.headroom),
        }
    }

    fn unwalked_leaf(&self) -> Option<&'static str> {
        let padded = |padding: Option<&f64>| padding.is_some_and(|p| *p != 0.0);
        match self {
            Scale::Patch(p, x_reverse) => [
                ("scale.round", !matches!(p.round, Tri::Unset)),
                ("scale.clamp", !matches!(p.clamp, Tri::Unset)),
                ("scale.nice", !matches!(p.nice, Tri::Unset)),
                ("scale.padding", padded(match &p.padding {
                    Tri::Value(padding) => Some(padding),
                    Tri::Null | Tri::Unset => None,
                })),
                ("scale.values", !matches!(p.values, Tri::Unset)),
                ("scale.continuous", !matches!(p.continuous, Tri::Unset)),
                ("scale.x_reverse", x_reverse.is_some_and(|r| !matches!(r, Tri::Unset))),
            ],
            Scale::Total(t) => [
                ("scale.round", t.round.is_some()),
                ("scale.clamp", t.clamp.is_some()),
                ("scale.nice", t.nice.is_some()),
                ("scale.padding", padded(t.padding.as_ref())),
                ("scale.values", t.values.is_some()),
                ("scale.continuous", t.continuous.is_some()),
                ("scale.x_reverse", false),
            ],
        }
        .into_iter()
        .find_map(|(name, set)| set.then_some(name))
    }
}

/// An axis's declared priority list.
pub struct AxisLadder<'a> {
    channel: ChannelType,
    rungs: Ladder<Layer<'a>>,
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
        tiers: &Tiers<'a>,
        column_format: Option<&'a FormatSpec>,
        temporal_band: bool,
    ) -> Result<Self, Diagnostic> {
        let board = &tiers.charts;
        let tier = |t: &Tier<'a>| {
            let by_type = match channel {
                ChannelType::Quantitative => t.axis_quantitative.map(AxisRung::Quantitative),
                ChannelType::Nominal => t.axis_band.map(AxisRung::Band),
                ChannelType::Temporal => None,
            };
            let by_axis = match axis {
                AxisName::X => t.axis_x.map(AxisRung::X),
                AxisName::Y => t.axis_y.map(AxisRung::Y),
            };
            [by_type, by_axis, t.axis.map(AxisRung::Axis)].map(|rung| rung.map(Layer::patch))
        };
        let board_type = match channel {
            ChannelType::Quantitative => Some(AxisRung::Quantitative(&board.axis_quantitative)),
            ChannelType::Nominal if !temporal_band => Some(AxisRung::Band(&board.axis_band)),
            ChannelType::Nominal | ChannelType::Temporal => None,
        };
        let board_axis = match axis {
            AxisName::X => AxisRung::X(&board.axis_x),
            AxisName::Y => AxisRung::Y(&board.axis_y),
        };
        let rungs = tier(&tiers.own)
            .into_iter()
            .chain([column_format.map(|f| Layer::patch(AxisRung::Column(f)))])
            .chain(tier(&tiers.family))
            .chain([
                board_type.map(Layer::overlay),
                Some(Layer::overlay(board_axis)),
                Some(Layer::patch(AxisRung::Total(&board.axis))),
            ])
            .flatten()
            .collect();
        Ok(Self {
            channel,
            rungs: Ladder::new(rungs),
        })
    }

    fn field<T>(&self, select: impl Fn(&Block) -> &Tri<T>) -> Option<&T> {
        self.rungs.walk(|layer| layer.format_field(&select))
    }

    /// The tick label format: the first rung that sets one. A name or bare spec is one
    /// value; a block is walked field by field. `auto` is decided here from the channel
    /// type: the bounded SI `number` on a quantitative axis, no format on any other, whose
    /// labels name themselves.
    pub fn labels_format(&self) -> Option<AuthoredFormat> {
        Some(match self.rungs.walk(Layer::format)? {
            FormatSpec::Name(name) if name.as_str() == "auto" => {
                return (self.channel == ChannelType::Quantitative)
                    .then(|| AuthoredFormat::named("number"));
            }
            FormatSpec::Name(name) => AuthoredFormat::named(name.as_str()),
            FormatSpec::Block(_) => AuthoredFormat {
                spec: self
                    .field(|b| &b.spec)
                    .cloned()
                    .filter(|spec| !spec.is_empty()),
                prefix: self.field(|b| &b.prefix).cloned(),
                suffix: self.field(|b| &b.suffix).cloned(),
                notation: self.field(|b| &b.notation).map(|n| match n {
                    Notation::Analytic => formats::Notation::Analytic,
                    Notation::Narrative => formats::Notation::Narrative,
                }),
                sign_placement: self.field(|b| &b.sign_placement).map(|p| match p {
                    SignPlacement::AfterPrefix => formats::SignPlacement::AfterPrefix,
                    SignPlacement::BeforePrefix => formats::SignPlacement::BeforePrefix,
                }),
                repeat: self.field(|b| &b.repeat).map(|r| match r {
                    AffixRepeat::Anchor => formats::Repeat::Anchor,
                    AffixRepeat::Every => formats::Repeat::Every,
                }),
            },
        })
    }

    /// Whether the labels show, as walked: `auto` stays `Engine::Rule`, which Python's
    /// `unset_autos` (part of `_finalize_style`'s derived tail) turns into unset later.
    pub fn labels_visible(&self) -> Option<&Engine<bool, Auto>> {
        self.rungs
            .walk(|layer| layer.read(AxisRung::labels_visible))
    }

    /// Whether the grid draws, and with it the zero and unity threshold rules.
    pub fn grid_visible(&self) -> Option<bool> {
        self.rungs.walk(|layer| layer.read(AxisRung::grid_visible)).copied()
    }

    /// Whether the grid's threshold rule draws where the axis crosses zero or one.
    pub fn threshold_visible(&self) -> Option<bool> {
        self.rungs
            .walk(|layer| layer.read(AxisRung::threshold_visible))
            .copied()
    }

    /// `scale.headroom`, as walked.
    pub fn headroom(&self) -> Option<f64> {
        self.rungs
            .walk(|layer| layer.read(|rung| match rung.scale() {
                Tri::Value(scale) => scale.headroom(),
                Tri::Null => Tri::Null,
                Tri::Unset => Tri::Unset,
            }))
            .copied()
    }

    /// `ticks.count`, as walked: `auto` stays the rule.
    pub fn ticks_count(&self) -> Option<&Engine<i64, Auto>> {
        self.rungs.walk(|layer| layer.read(AxisRung::ticks_count))
    }

    /// The first `scale` leaf other than `headroom` that a rung sets, by name, or `ticks.step`,
    /// `labels.expr`, or an x leaf of a time or ordinal axis (`type`, `time_unit`, the tick and
    /// label grains, `labels.values`).
    /// No walk reads them yet, so a family that reads the axis refuses a chart that sets one.
    /// A `padding` of 0 pads nothing on any scale, so it is not counted.
    pub fn unwalked_leaf(&self) -> Option<&'static str> {
        self.rungs.find_map(|layer| {
            if layer.rung.sets_ticks_step() {
                return Some("ticks.step");
            }
            if layer.rung.sets_labels_expr() {
                return Some("labels.expr");
            }
            if let Some(leaf) = layer.rung.x_type_leaf() {
                return Some(leaf);
            }
            match layer.rung.scale() {
                Tri::Value(scale) => scale.unwalked_leaf(),
                Tri::Null | Tri::Unset => None,
            }
        })
    }

    /// Where a band sits within its step (0 to 1).
    pub fn band_position(&self) -> Option<f64> {
        self.rungs
            .walk(|layer| layer.read(AxisRung::band_position))
            .copied()
    }
}
