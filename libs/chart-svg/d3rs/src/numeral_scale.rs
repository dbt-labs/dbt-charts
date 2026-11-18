//! The laws that label a value-axis ladder with numerals, shared by the resolve stage that bakes
//! them and the renderer that re-derives them once a legend view refits the axis
//! (`core/text/numeral_scale.py`'s ladder slice): does a ladder compact and at what magnitude,
//! and the fixed-point precision a ladder that does not compact writes its rungs with.

use crate::invariant::at;

/// `text/numeral_scale.py`'s `_D3_PREFIX_BY_EXPONENT` keys — the SI tiers a
/// shared scale may land on.
pub const TIERS: &[i32] = &[3, 6, 9, 12];

/// A set compacts only when its extreme value, written out in plain
/// digits, reaches this many integer digits.
pub const COMPACTION_DIGIT_THRESHOLD: u32 = 6;

/// Once the magnitude is chosen, the extreme value at that scale reaching
/// this many integer digits means the suffix must repeat rather than
/// anchor once.
pub const MODE_TRIGGER_DIGITS: u32 = 4;

/// `SuffixMode` — which of the two compacting modes a shared scale uses.
/// `ResolvedColumnSharedScale.mode`'s own two string values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SuffixMode {
    Anchor,
    Repeat,
}

impl SuffixMode {
    pub fn as_str(self) -> &'static str {
        match self {
            SuffixMode::Anchor => "anchor",
            SuffixMode::Repeat => "repeat",
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct SharedScale {
    pub exponent: i32,
    pub mode: SuffixMode,
}

/// `_integer_digit_count`: digits before the decimal point in `value`'s
/// plain, written-out form. Truncates rather than rounds.
pub fn integer_digit_count(value: f64) -> u32 {
    value.abs().floor().to_string().len() as u32
}

/// `_mode_for_scaled_extreme`.
pub fn mode_for_scaled_extreme(scaled_extreme: f64) -> SuffixMode {
    if integer_digit_count(scaled_extreme) >= MODE_TRIGGER_DIGITS {
        SuffixMode::Repeat
    } else {
        SuffixMode::Anchor
    }
}

/// `_largest_tier_dividing`: the largest SI tier (at or above thousands)
/// `step` divides into evenly (a whole multiple, float-noise tolerant —
/// `nice_tick_values` rounds to 10 decimal places).
pub fn largest_tier_dividing(step: f64) -> Option<i32> {
    let mut tiers: Vec<i32> = TIERS.to_vec();
    tiers.sort_unstable_by(|a, b| b.cmp(a));
    for tier in tiers {
        let ratio = step / 10f64.powi(tier);
        if ratio >= 1.0 && (ratio - ratio.round()).abs() <= 1e-9 * ratio.round().abs().max(1.0) {
            return Some(tier);
        }
    }
    None
}

/// `shared_scale_for_ladder`: resolve the shared scale for a `nice_tick_
/// values`-produced axis ladder, or `None` if it doesn't compact. The
/// magnitude comes from the tick STEP (never a majority vote over the
/// ticks — we chose them ourselves, so voting would be circular): some SI
/// tier at or above thousands must divide the step evenly, and the extreme
/// tick, written out in plain digits, must reach the compaction-digit
/// threshold.
pub fn shared_scale_for_ladder(ticks: &[f64]) -> Option<SharedScale> {
    if ticks.len() < 2 {
        return None;
    }
    let step = (*at(ticks, 1) - *at(ticks, 0)).abs();
    let tier = largest_tier_dividing(step)?;
    let extreme = ticks.iter().fold(0.0_f64, |acc, &t| acc.max(t.abs()));
    if integer_digit_count(extreme) < COMPACTION_DIGIT_THRESHOLD {
        return None;
    }
    let tier = rolled_up_tier(tier, step, extreme);
    Some(SharedScale {
        exponent: tier,
        mode: mode_for_scaled_extreme(extreme / 10f64.powi(tier)),
    })
}

/// `_rolled_up_tier`: `tier`, or the next tier up when the top tick would
/// print 4+ digits and `step` is a whole number of tenths of that next tier
/// (500k -> `0.5M`; 250k stays in thousands).
fn rolled_up_tier(tier: i32, step: f64, extreme: f64) -> i32 {
    let Some(&next) = TIERS.iter().filter(|&&t| t > tier).min() else { return tier };
    if integer_digit_count(extreme / 10f64.powi(tier)) < MODE_TRIGGER_DIGITS {
        return tier;
    }
    let tenths = step / 10f64.powi(next) * 10.0;
    if tenths >= 1.0 && (tenths - tenths.round()).abs() <= 1e-9 * tenths.round().abs().max(1.0) {
        next
    } else {
        tier
    }
}

/// `_natural_tier`: the SI tier `value`'s own magnitude falls into, `None` below thousands.
fn natural_tier(value: f64) -> Option<i32> {
    TIERS.iter().rev().copied().find(|&tier| value.abs() >= 10f64.powi(tier))
}

/// `shared_scale_for_column`: the one magnitude a table column's non-zero values share, or `None` when the
/// column does not compact. Every non-zero value votes its natural tier, and a value below thousands votes
/// `None`. The majority tier wins, and where tiers tie the higher one does; a tie that includes `None` leaves
/// no shared scale. A column also has none when its extreme value is short of the compaction digits or has
/// outgrown its tier (a repeating suffix).
pub fn shared_scale_for_column(values: &[f64]) -> Option<SharedScale> {
    let mut tally: Vec<(Option<i32>, usize)> = Vec::new();
    for tier in values.iter().filter(|&&value| value != 0.0).map(|&value| natural_tier(value)) {
        match tally.iter_mut().find(|(seen, _)| *seen == tier) {
            Some((_, count)) => *count += 1,
            None => tally.push((tier, 1)),
        }
    }
    let top_count = tally.iter().map(|&(_, count)| count).max()?;
    let tied: Vec<Option<i32>> = tally.iter().filter(|&&(_, count)| count == top_count).map(|&(tier, _)| tier).collect();
    if tied.contains(&None) {
        return None;
    }
    let majority_tier = tied.into_iter().flatten().max()?;
    let extreme = values.iter().fold(0.0_f64, |acc, &value| acc.max(value.abs()));
    if integer_digit_count(extreme) < COMPACTION_DIGIT_THRESHOLD {
        return None;
    }
    match mode_for_scaled_extreme(extreme / 10f64.powi(majority_tier)) {
        SuffixMode::Repeat => None,
        mode => Some(SharedScale { exponent: majority_tier, mode }),
    }
}

/// `forced_shared_scale`: the scale a column takes when a sibling prints SI and its own vote declined: the
/// unit of its largest value. `None` when that value is below thousands or has outgrown the unit.
pub fn forced_shared_scale(values: &[f64]) -> Option<SharedScale> {
    let extreme = values.iter().fold(0.0_f64, |acc, &value| acc.max(value.abs()));
    let exponent = natural_tier(extreme)?;
    match mode_for_scaled_extreme(extreme / 10f64.powi(exponent)) {
        SuffixMode::Repeat => None,
        mode => Some(SharedScale { exponent, mode }),
    }
}

/// `column_digit_format`: the fixed-point spec a shared-scale column's scaled values print through. Its
/// decimals bring the smallest value at or above the unit (`majority_scaled`) to the source spec's
/// significant figures (6 when it has none), never fewer than `min_precision`, the most a sibling column on
/// the same scale needs.
pub fn column_digit_spec(sign: char, sig_figs: Option<usize>, majority_scaled: f64, min_precision: usize) -> String {
    // `_decimal_exponent`: the digits before the decimal point, negative below 1.
    let exponent = if majority_scaled == 0.0 { 1 } else { majority_scaled.abs().log10().floor() as i64 + 1 };
    let precision = (sig_figs.unwrap_or(6) as i64 - exponent).max(min_precision as i64).max(0);
    digit_spec_str(sign, precision as usize)
}

/// `_digit_spec`/`ruler_digit_format`'s/`non_compacting_tick_format`'s
/// shared tail: `dataclasses.replace(parsed, type="f", precision=…,
/// trim=True, comma=True, symbol="", width=None, zero=False)` serialized —
/// every field but `precision`/`sign` is fixed by that replace; `sign`
/// (`'-'`, the default, writes nothing) is the one field a source spec can
/// still carry through.
pub fn digit_spec_str(sign: char, precision: usize) -> String {
    let sign_str = if sign == '-' {
        String::new()
    } else {
        sign.to_string()
    };
    format!("{sign_str},.{precision}~f")
}

/// `text/numeral_scale.py::_precision_for_step`: decimal places `step`
/// needs written out in full. `Err` — `_fixed_point_reaches` false — when
/// `step` is finer than the finest grid a fixed-point spec can express;
/// callers route that to the scientific register, not ported by this rung
/// (no corpus ladder reaches it).
pub const MAX_STEP_PRECISION: i32 = 10;

pub fn precision_for_step(step: f64) -> Result<usize, String> {
    if step.abs() < 10f64.powi(-MAX_STEP_PRECISION) {
        return Err(format!(
            "step {step}: scientific-register tick format (below fixed-point precision) not modeled"
        ));
    }
    let rounded = (step * 10f64.powi(MAX_STEP_PRECISION)).round() / 10f64.powi(MAX_STEP_PRECISION);
    let text = format!("{rounded:.*}", MAX_STEP_PRECISION as usize);
    let trimmed = text.trim_end_matches('0');
    Ok(trimmed.split_once('.').map_or(0, |(_, frac)| frac.len()))
}

/// `text/numeral_scale.py::non_compacting_tick_format`: the spec a ladder that
/// does NOT compact paints with, `(currency prefix, digit-only spec,
/// precision)`. Precision comes from the step (a non-compacting ladder writes
/// its raw values, so a half-step must not round away); a `%` step is read in
/// displayed percent points. An SI source is rebuilt as the trimmed,
/// comma-grouped fixed-point digit spec; a fixed-point source keeps its own
/// type, sign and grouping and is not trimmed, so every rung carries the
/// decimal. A step finer than the fixed-point grid refuses (the scientific
/// register is not modeled).
pub fn non_compacting_tick_format(
    format_spec: &str,
    step: f64,
) -> Result<(String, String, usize), String> {
    let parts =
        crate::format::parse_parts(format_spec).map_err(|e| format!("{format_spec}: {e}"))?;
    let display_step = if parts.type_ == '%' {
        step * 100.0
    } else {
        step
    };
    let precision = precision_for_step(display_step)?;
    let prefix = if parts.symbol { "$" } else { "" }.to_string();
    if parts.type_ == 's' {
        return Ok((
            prefix,
            digit_spec_str(parts.sign, precision),
            precision,
        ));
    }
    let sign = if parts.sign == '-' {
        String::new()
    } else {
        parts.sign.to_string()
    };
    let comma = if parts.comma { "," } else { "" };
    Ok((
        prefix,
        format!("{sign}{comma}.{precision}{}", parts.type_),
        precision,
    ))
}

/// Which end of `drawn` a ruler anchors on. An unbaked top edge is fitted by Vega, so a positive
/// rung may not be drawn: the ruler anchors on the magnitude extreme, the earliest on a tie.
pub fn ruler_anchor_at_start(drawn: &[f64], top_baked: bool) -> bool {
    if top_baked {
        return ladder_anchor_at_start(drawn);
    }
    drawn.split_first().is_some_and(|(first, rest)| rest.iter().all(|v| v.abs() <= first.abs()))
}

/// `scale.py::ladder_anchor_at_start`: whether an anchored unit sits on the
/// first tick rather than the last. Sign-aware: the largest positive tick, else
/// the most negative, so a zero-crossing ladder never anchors on its negative
/// end or the 0 cap. `tick_values` are the ticks Vega draws; with none, Vega's
/// last tick.
pub fn ladder_anchor_at_start(tick_values: &[f64]) -> bool {
    if tick_values.is_empty() {
        return false;
    }
    let positives = tick_values.iter().copied().filter(|&v| v > 0.0);
    let anchor = positives
        .reduce(f64::max)
        .unwrap_or_else(|| tick_values.iter().copied().fold(f64::INFINITY, f64::min));
    tick_values.iter().position(|&v| v == anchor) == Some(0)
}

/// `tick_min_step_for_format`: the smallest step a fixed-decimal d3 spec can
/// distinguish, or `None`. `d` is always whole; `f` derives `10^-precision`; `%`
/// derives two extra zeros of precision (it multiplies by 100 first). A
/// precision-less `f`/`%` takes d3's own default of 6. Every other type, or a
/// spec d3 cannot parse (a native predefined name), has none.
pub fn tick_min_step_for_format(spec: &str) -> Option<f64> {
    if spec.is_empty() {
        return None;
    }
    let parts = crate::format::parse_parts(spec).ok()?;
    let precision = parts.precision.unwrap_or(6) as i32;
    match parts.type_ {
        'd' => Some(1.0),
        'f' => Some(10f64.powi(-precision)),
        '%' => Some(10f64.powi(-(precision + 2))),
        _ => None,
    }
}

/// What deciding a ladder's labels reads of an axis, as plain values: resolve gets them from the
/// axis it is baking, the renderer from the axis it was handed, and both decide with the same
/// function.
pub struct LabelInputs<'a> {
    /// The format's spec when it is SI-shaped and the axis may compact (not a raw spec that
    /// `force_right` only right-aligned).
    pub si_format: Option<&'a str>,
    /// The format's spec when it is a house fixed-decimal format (percent, currency_full, ...).
    pub fixed_decimal_format: Option<&'a str>,
    /// The author named a predefined format.
    pub format_is_house: bool,
    /// The format carries a prefix or suffix text.
    pub format_affixed: bool,
    /// The authored magnitude-word register.
    pub notation: Option<&'a str>,
    /// `anchor` or `every`: how the affix repeats.
    pub repeat: Option<&'a str>,
    pub label_expr_authored: bool,
    /// The ticks stack into a column (everything but a horizontal bar's measure axis).
    pub column_forming: bool,
    /// The labels end up anchored at the axis's start.
    pub start_anchored: bool,
    /// The label font has tabular figures.
    pub tabular: bool,
}

/// The magnitude ruler a ladder is labeled with.
pub struct RulerChoice {
    pub exponent: i32,
    pub mode: SuffixMode,
    /// A currency symbol leads the digits.
    pub currency: bool,
    pub digit_spec: String,
    /// `analytic` or `narrative`.
    pub register: String,
    /// Non-suffixed ticks reserve the suffix's width, and mixed decimals are padded to align.
    pub reserve: bool,
    pub prefix_repeats: bool,
}

/// Why a ladder could not be labeled.
pub enum LabelError {
    /// A column-forming axis needs a font whose digits share one width.
    ColumnNeedsTabularFont,
    Other(String),
}

impl From<String> for LabelError {
    fn from(message: String) -> Self {
        LabelError::Other(message)
    }
}

/// `resolve/style/scale.py::_build_ruler`'s decision: the ruler `ticks` are labeled with, or `None`
/// for every one of its early-return gates (the ladder does not compact, no SI format, an authored
/// `label.expr`, or a start-anchored column-forming axis).
pub fn decide_ruler(inputs: &LabelInputs, ticks: &[f64]) -> Result<Option<RulerChoice>, LabelError> {
    let Some(raw_scale) = shared_scale_for_ladder(ticks) else { return Ok(None) };
    let Some(si_format) = inputs.si_format else { return Ok(None) };
    if inputs.label_expr_authored || (inputs.column_forming && inputs.start_anchored) {
        return Ok(None);
    }
    let mode = if inputs.column_forming { raw_scale.mode } else { SuffixMode::Repeat };
    let parts = crate::format::parse_parts(si_format).map_err(|e| format!("{si_format}: {e}"))?;
    let register = inputs.notation.unwrap_or(if mode == SuffixMode::Anchor { "analytic" } else { "narrative" });
    if inputs.column_forming && !inputs.tabular {
        return Err(LabelError::ColumnNeedsTabularFont);
    }
    Ok(Some(RulerChoice {
        exponent: raw_scale.exponent,
        mode,
        currency: parts.symbol,
        digit_spec: digit_spec_str(parts.sign, 1),
        register: register.to_string(),
        reserve: inputs.column_forming,
        prefix_repeats: inputs.repeat.map_or(!inputs.column_forming, |r| r == "every"),
    }))
}

/// The labels of a ladder that does not compact: every rung written in full, to the precision the
/// step needs.
pub struct TickLabelChoice {
    pub prefix: String,
    pub digit_spec: String,
    pub precision: usize,
    /// The affix sits on one end's tick: the first when `Some(true)`.
    pub anchor_at_start: Option<bool>,
    /// Mixed decimals are padded to align, when the rungs carry more than one depth.
    pub pad_decimals: bool,
}

/// `tick_label`'s decision in `resolve/style/axis_cascade.py`: how a ladder that does not compact is
/// labeled, `None` when the format is not one resolve derives a precision for (a literal spec stays as
/// written) or the ladder has fewer than two distinct rungs. `drawn_ticks` are the rungs inside the
/// domain.
pub fn decide_tick_label(inputs: &LabelInputs, ticks: &[f64], drawn_ticks: &[f64]) -> Result<Option<TickLabelChoice>, String> {
    let compacts = !ticks.is_empty() && shared_scale_for_ladder(ticks).is_some();
    let step_format = inputs.fixed_decimal_format.or(if compacts { None } else { inputs.si_format });
    let two_distinct_rungs = ticks.len() >= 2 && *at(ticks, 0) != *at(ticks, 1);
    let (Some(step_format), true, true) = (step_format, two_distinct_rungs, inputs.format_is_house && !inputs.label_expr_authored) else {
        return Ok(None);
    };
    let (prefix, digit_spec, precision) = non_compacting_tick_format(step_format, (*at(ticks, 1) - *at(ticks, 0)).abs())?;
    let anchored = match inputs.repeat {
        None => inputs.column_forming,
        Some(repeat) => repeat == "anchor",
    };
    let anchor_at_start = ((!prefix.is_empty() || inputs.format_affixed) && anchored).then(|| ladder_anchor_at_start(drawn_ticks));
    Ok(Some(TickLabelChoice {
        prefix,
        digit_spec,
        precision,
        anchor_at_start,
        pad_decimals: inputs.column_forming && !inputs.start_anchored && inputs.tabular,
    }))
}

/// `axis_cascade.py::_effective_align`.
pub fn effective_align<'a>(resolved_align: Option<&'a str>, edge: Option<&str>) -> Option<&'a str> {
    if let Some(a) = resolved_align {
        return Some(if a == "left" || a == "right" {
            a
        } else {
            "center"
        });
    }
    match edge {
        Some("right") => Some("left"),
        Some("left") => Some("right"),
        _ => None,
    }
}

/// `axis_cascade.py::_label_align_fallback_fires`.
pub fn label_align_fallback_fires(label_align: Option<&str>, edge: Option<&str>, case: &str) -> bool {
    if !matches!(edge, Some("left") | Some("right")) {
        return false;
    }
    let effective = effective_align(label_align, edge);
    effective == Some("center") || (effective == edge && matches!(case, "upper" | "lower"))
}

/// `axis_cascade.py::_render_align` — the align that actually reaches
/// paint, after render's own-side/center safety net.
pub fn render_align<'a>(
    label_align: Option<&'a str>,
    edge: Option<&str>,
    fallback_fires: bool,
) -> Option<&'a str> {
    effective_align(if fallback_fires { None } else { label_align }, edge)
}

#[cfg(test)]
mod tests {
    use super::*;

    const LADDER_1_75M: [f64; 4] = [0.0, 500_000.0, 1_000_000.0, 1_500_000.0];

    fn inputs(column_forming: bool) -> LabelInputs<'static> {
        LabelInputs {
            si_format: Some(",.1s"),
            fixed_decimal_format: None,
            format_is_house: true,
            format_affixed: false,
            notation: None,
            repeat: None,
            label_expr_authored: false,
            column_forming,
            start_anchored: false,
            tabular: true,
        }
    }

    #[test]
    fn a_vertical_ladder_whose_thousands_would_reach_four_digits_anchors_one_tier_up() {
        let ruler = decide_ruler(&inputs(true), &LADDER_1_75M).ok().flatten().unwrap();
        assert_eq!((ruler.exponent, ruler.mode), (6, SuffixMode::Anchor));
    }

    #[test]
    fn a_horizontal_ladder_repeats_at_the_rolled_up_tier() {
        let ruler = decide_ruler(&inputs(false), &LADDER_1_75M).ok().flatten().unwrap();
        assert_eq!((ruler.exponent, ruler.mode), (6, SuffixMode::Repeat));
    }

    #[test]
    fn a_quarter_million_step_stays_in_thousands() {
        let ticks = [0.0, 250_000.0, 500_000.0, 750_000.0, 1_000_000.0, 1_250_000.0];
        let scale = shared_scale_for_ladder(&ticks).unwrap();
        assert_eq!((scale.exponent, scale.mode), (3, SuffixMode::Repeat));
    }
}

#[cfg(test)]
mod column_scale_tests {
    use super::*;

    #[test]
    fn a_column_shares_the_majority_tier_once_its_extreme_has_six_digits() {
        let scale = shared_scale_for_column(&[150_000.0, 220_000.0, -310_000.0]).expect("one tier");
        assert_eq!((scale.exponent, scale.mode), (3, SuffixMode::Anchor));
        assert!(shared_scale_for_column(&[15_000.0, 22_000.0]).is_none(), "five digits do not compact");
    }

    #[test]
    fn a_forced_scale_takes_the_unit_of_the_largest_value_and_a_digit_spec_its_decimals_from_the_smallest() {
        assert_eq!(forced_shared_scale(&[858.0, 1270.0]).map(|s| s.exponent), Some(3));
        assert!(forced_shared_scale(&[858.0, 12.0]).is_none(), "below thousands");
        assert!(forced_shared_scale(&[2_659_000.0, 12.0]).map(|s| s.exponent) == Some(6));
        assert_eq!(column_digit_spec('-', Some(3), 12.3, 0), ",.1~f");
        assert_eq!(column_digit_spec('-', Some(3), 150.0, 0), ",.0~f");
        assert_eq!(column_digit_spec('-', Some(3), 150.0, 1), ",.1~f");
        assert_eq!(column_digit_spec('-', None, 1.25, 0), ",.5~f");
    }

    #[test]
    fn a_tie_with_no_tier_or_an_outgrown_unit_shares_nothing() {
        assert!(shared_scale_for_column(&[1234.5, -99.6, 0.5, 1_000_000.0]).is_none(), "None ties the thousands");
        assert!(shared_scale_for_column(&[2_659_000.0, 631_000.0, 800_000.0, 900_000.0, 700_000.0]).is_none(), "2,659 K has outgrown the thousands");
        assert!(shared_scale_for_column(&[0.0, 0.0]).is_none());
    }
}
