//! Vega's axis ARIA caption (`vega-scenegraph` `util/aria.js` `axisCaption`,
//! `vega-scale` `caption.js` `domainCaption`): the `aria-label` a screen
//! reader announces on an axis group. One function describes every family's
//! axis; a family supplies only what Vega reads: the rendered title, the
//! scale's type and final domain, and the axis's `format`.

use d3rs::format::{format, format_float, parse_parts, vega_tick_format};

use super::table_dates::{strftime, Moment};
use super::temporal::{
    axis_time_format, date_text_day, time_unit_specifier, floor_to_bucket_start, parse_iso_date, week_floor, LabelUnit, TimeUnit, VegaType,
};
use super::x_type::{Grain, XResolution};
use crate::common::{axis_title_band, AxisStyle, AxisTitle, FormatSlot};
use crate::contract::AxisChannel;
use crate::number_locale::active_number_locale;

/// How many values a discrete domain lists before it elides the middle
/// (`domainCaption`'s `maxlen`, 7).
const MAX_LISTED_VALUES: usize = 7;
/// The tick count `domainCaption` sizes a numeric format's precision for.
const CAPTION_TICK_COUNT: usize = 5;
pub const MS_PER_DAY: f64 = 86_400_000.0;

/// Vega-Lite's numeric scale types with a continuous domain.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NumericScale {
    Linear,
    Log,
    Pow,
    Sqrt,
    Symlog,
}

impl NumericScale {
    fn name(self) -> &'static str {
        match self {
            NumericScale::Linear => "linear",
            NumericScale::Log => "log",
            NumericScale::Pow => "pow",
            NumericScale::Sqrt => "sqrt",
            NumericScale::Symlog => "symlog",
        }
    }

    /// The scale an axis's `scale.continuous.type` names; absent is linear.
    pub fn of_axis(axis: &AxisStyle) -> Result<NumericScale, String> {
        match axis.scale.get("continuous").and_then(|c| c.get("type")).and_then(|t| t.as_str()) {
            None | Some("linear") => Ok(NumericScale::Linear),
            Some("log") => Ok(NumericScale::Log),
            Some("pow") => Ok(NumericScale::Pow),
            Some("sqrt") => Ok(NumericScale::Sqrt),
            Some("symlog") => Ok(NumericScale::Symlog),
            Some(other) => Err(format!("axis aria: no numeric Vega scale named {other:?}")),
        }
    }
}

/// What the scale behind an axis is, as `domainCaption` reads it.
#[derive(Clone, Debug, PartialEq)]
pub enum CaptionScale {
    /// A band, point or ordinal scale: its domain values in scale order.
    /// `format` is the axis's d3 number `format`, which Vega applies to the
    /// value read as a number.
    Discrete { values: Vec<String>, format: Option<String> },
    Numeric { kind: NumericScale, domain: (f64, f64), format: Option<String> },
    /// A `time` (local) or `utc` scale over millisecond instants. `format` is
    /// the axis's strftime `format`.
    Time { utc: bool, domain: (f64, f64), format: Option<String> },
}

/// One axis's caption inputs.
#[derive(Clone, Debug, PartialEq)]
pub struct AxisCaption {
    /// The title text Vega drew (lines joined by a space); `None` when the
    /// axis draws no title.
    pub title: Option<String>,
    pub scale: CaptionScale,
    /// Vega-Lite drops an axis none of whose parts is on: the group wrapper
    /// is left out and nothing it held is lost.
    pub bare: bool,
}

impl AxisCaption {
    /// A caption whose title is `text` as `style` draws it.
    pub fn new(text: &str, style: &AxisTitle, scale: CaptionScale) -> AxisCaption {
        AxisCaption { title: drawn_title(text, style), scale, bare: false }
    }

    /// Marks the caption bare when `axis` draws no tick, label, domain line or
    /// title; its grid lines are a separate Vega axis with no description.
    pub fn bare_if_undrawn(mut self, axis: &AxisStyle) -> AxisCaption {
        self.bare = self.title.is_none()
            && !axis.ticks.visible.unwrap_or(true)
            && !axis.labels.visible.unwrap_or(true)
            && !axis.line_visible();
        self
    }

    /// The same axis without its title: a mirror's ghost copies the primary
    /// axis but draws no title.
    pub fn untitled(&self) -> AxisCaption {
        AxisCaption { title: None, scale: self.scale.clone(), bare: self.bare }
    }

    /// The mirror ghost's axis: the primary's scale under the ghost's own
    /// `format` where it has one, and no title.
    pub fn ghost(&self, format: Option<&FormatSlot>) -> Result<AxisCaption, String> {
        let mut ghost = self.untitled();
        if let (Some(slot), CaptionScale::Numeric { format: shown, .. }) = (format, &mut ghost.scale) {
            *shown = slot_format(slot, false, true)?;
        }
        Ok(ghost)
    }

    /// Vega's caption for the axis on `channel`.
    pub fn describe(&self, channel: AxisChannel) -> Result<String, String> {
        let xy = match channel {
            AxisChannel::X => "X",
            AxisChannel::Y => "Y",
        };
        let titled = self.title.as_ref().map(|t| format!(" titled '{t}'")).unwrap_or_default();
        let (kind, domain) = match &self.scale {
            CaptionScale::Discrete { values, format } => ("discrete", discrete_caption(values, format.as_deref())?),
            CaptionScale::Numeric { kind, domain, format } => {
                (kind.name(), span_caption(domain, |v| numeric_text(*kind, *domain, format.as_deref(), v))?)
            }
            CaptionScale::Time { utc, domain, format } => {
                (if *utc { "utc" } else { "time" }, span_caption(domain, |v| time_text(*utc, format.as_deref(), v))?)
            }
        };
        Ok(format!("{xy}-axis{titled} for a {kind} scale with {domain}"))
    }
}

/// The caption of a quantitative axis over its final `domain`. A normalized
/// stack pins its axis to `.0%` (`pin_normalize_axis_format`).
pub fn value_axis_caption(
    text: &str,
    title: &AxisTitle,
    axis: &AxisStyle,
    domain: (f64, f64),
    normalize: bool,
) -> Result<AxisCaption, String> {
    let format = if normalize { Some(".0%".to_string()) } else { vega_axis_format(axis, true)? };
    let scale = CaptionScale::Numeric { kind: NumericScale::of_axis(axis)?, domain, format };
    Ok(AxisCaption::new(text, title, scale).bare_if_undrawn(axis))
}

/// The caption of a `time` axis over a `domain` in day numbers.
pub fn date_value_caption(text: &str, axis: &AxisStyle, domain: (f64, f64)) -> Result<AxisCaption, String> {
    let scale = CaptionScale::Time {
        utc: false,
        domain: (domain.0 * MS_PER_DAY, domain.1 * MS_PER_DAY),
        format: vega_axis_format(axis, false)?.filter(|spec| super::temporal::is_time_format(spec)),
    };
    Ok(AxisCaption::new(text, &axis.title, scale).bare_if_undrawn(axis))
}

/// The caption of an x axis as the x-type law resolved it: a category for an
/// ordinal or nominal type, a `utc` scale over the bucket starts under a
/// calendar grain, a `time` scale over the raw instants without one, and a
/// numeric scale for a quantitative type. `domain` is the axis's x values,
/// year-shaped values already read as dates, and for a category the scale's
/// own order.
pub fn x_axis_caption(
    text: &str,
    axis: &AxisStyle,
    resolution: &XResolution,
    domain: &[String],
    plot_w: f64,
) -> Result<AxisCaption, String> {
    match resolution.vl_type {
        VegaType::Ordinal | VegaType::Nominal => {
            let values: Vec<String> = domain.iter().map(|v| bucket_text(v, resolution.grain)).collect();
            category_axis_caption(text, axis, &values)
        }
        VegaType::Temporal => {
            let grain = match resolution.grain {
                Grain::Calendar(unit) => Some(unit),
                Grain::None | Grain::Part(_) => None,
            };
            let format = match (authored_time_format(axis, grain)?, resolution.grain) {
                (Some(format), _) => Some(format),
                (None, Grain::Calendar(unit)) => Some(time_unit_specifier(unit).to_string()),
                (None, Grain::Part(part)) => Some(part.format_spec().to_string()),
                (None, Grain::None) => None,
            };
            // A value that is no date is NaN to Vega.
            let day = |date: &str| date_text_day(date).unwrap_or(f64::NAN);
            let bucket_day = |date: &str| match resolution.grain {
                Grain::Calendar(unit) => {
                    let floored = match unit {
                        TimeUnit::YearWeek => week_floor(date),
                        _ => parse_iso_date(date).map(|d| floor_to_bucket_start(d, unit, 1)).map(|(y, m, d)| format!("{y:04}-{m:02}-{d:02}")),
                    };
                    floored.map_or(f64::NAN, |floored| day(&floored))
                }
                _ => day(date),
            };
            // Vega's timeunit transform reads a year below 100 as NaN, which stays out of the domain.
            let finite = |d: &&String| !(grain.is_some() && super::temporal::timeunit_nan(d));
            let mut days: Vec<f64> = domain.iter().filter(finite).map(|d| bucket_day(d)).filter(|d| !d.is_nan()).collect();
            if days.is_empty() {
                // Every date is NaN to Vega, which prints it as `NaN`: name the dates instead.
                days = domain.iter().map(|d| bucket_day(d)).filter(|d| !d.is_nan()).collect();
            }
            let extent = if days.is_empty() {
                (f64::NAN, f64::NAN)
            } else {
                (
                    days.iter().copied().fold(f64::INFINITY, f64::min),
                    days.iter().copied().fold(f64::NEG_INFINITY, f64::max),
                )
            };
            let (lo, hi) = super::scatter_axis::authored_day_domain(axis, "axis aria")?.unwrap_or(extent);
            let pad = axis.scale.get("padding").and_then(|p| p.as_f64()).unwrap_or(0.0);
            let (lo, hi) = padded_day_domain((lo, hi), plot_w, pad);
            let scale = CaptionScale::Time {
                utc: grain.is_some(),
                domain: (lo * MS_PER_DAY, hi * MS_PER_DAY),
                format,
            };
            Ok(AxisCaption::new(text, &axis.title, scale).bare_if_undrawn(axis))
        }
        VegaType::Quantitative => {
            let numbers = domain
                .iter()
                .map(|v| v.parse::<f64>().map_err(|e| format!("axis aria: x value {v:?} is not a number: {e}")))
                .collect::<Result<Vec<_>, _>>()?;
            let lo = numbers.iter().copied().fold(f64::INFINITY, f64::min);
            let hi = numbers.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            value_axis_caption(text, &axis.title, axis, (lo, hi), false)
        }
    }
}

/// The strftime spec an axis labels its dates with: the authored time
/// `labels.format`, else the name a board redefined for the label grain.
pub fn authored_time_format(axis: &AxisStyle, grain: Option<TimeUnit>) -> Result<Option<String>, String> {
    let label_grain = match grain {
        Some(unit) => LabelUnit::resolve(unit, axis.labels.time_unit.as_deref())?.grain(),
        None => None,
    };
    Ok(axis_time_format(axis.labels.spec(), &axis.grain_formats, label_grain).map(str::to_string))
}

/// A date scale's domain in days, widened by `scale.padding` pixels over a
/// range `plot_w` wide; Dates keep whole milliseconds.
pub fn padded_day_domain(domain: (f64, f64), plot_w: f64, pad: f64) -> (f64, f64) {
    let (lo, hi) = crate::vega_fit::pad_domain(domain, plot_w, pad, crate::vega_fit::Bend::Linear);
    let whole_ms = |day: f64| (day * MS_PER_DAY + 1e-3).trunc() / MS_PER_DAY;
    (whole_ms(lo), whole_ms(hi))
}

/// A category date as its calendar bucket's date-only text: the ordinal
/// scaffold keys every row by `bucket.isoformat()`.
pub fn bucket_text(value: &str, grain: Grain) -> String {
    match (grain, parse_iso_date(value)) {
        (Grain::Calendar(_), Some((y, m, d))) => format!("{y:04}-{m:02}-{d:02}"),
        _ => value.to_string(),
    }
}

/// The caption of a band or point axis over its domain `values`.
pub fn category_axis_caption(text: &str, axis: &AxisStyle, values: &[String]) -> Result<AxisCaption, String> {
    let scale = CaptionScale::Discrete { values: values.to_vec(), format: vega_axis_format(axis, false)? };
    Ok(AxisCaption::new(text, &axis.title, scale).bare_if_undrawn(axis))
}

/// The title text an axis draws: `None` when its title is hidden or empty.
pub fn drawn_title(text: &str, style: &AxisTitle) -> Option<String> {
    (axis_title_band(style, text) > 0.0).then(|| text.split('\n').collect::<Vec<_>>().join(" "))
}

fn span_caption(domain: &(f64, f64), text: impl Fn(&f64) -> Result<String, String>) -> Result<String, String> {
    Ok(format!("values from {} to {}", text(&domain.0)?, text(&domain.1)?))
}

fn discrete_caption(values: &[String], format: Option<&str>) -> Result<String, String> {
    // Vega holds a midnight datetime as the date-only text its data carries.
    let values: Vec<String> =
        values.iter().map(|v| v.strip_suffix("T00:00:00").unwrap_or(v).to_string()).collect();
    // A time format on a category axis is its field's `formatType: time`, which
    // Vega applies to dates the scale does not hold: the values print as given.
    let text = |value: &str| match format.filter(|spec| !super::temporal::is_time_format(spec)) {
        None => Ok(value.to_string()),
        Some(spec) => format_js_number(spec, value),
    };
    let listed = if values.len() > MAX_LISTED_VALUES {
        let head = values[..MAX_LISTED_VALUES - 2].iter().map(|v| text(v)).collect::<Result<Vec<_>, _>>()?;
        format!("{}, ending with {}", head.join(", "), text(&values[values.len() - 1])?)
    } else {
        values.iter().map(|v| text(v)).collect::<Result<Vec<_>, _>>()?.join(", ")
    };
    let n = values.len();
    Ok(format!("{n} value{}: {listed}", if n == 1 { "" } else { "s" }))
}

/// `locale.format(spec)(value)` on a domain value Vega holds as a string: d3
/// reads it as a number (`+value`), so a non-numeric string prints NaN.
fn format_js_number(spec: &str, value: &str) -> Result<String, String> {
    let number = value.trim().parse::<f64>().unwrap_or(f64::NAN);
    format(&active_number_locale().locale, spec, number)
}

fn numeric_text(kind: NumericScale, domain: (f64, f64), spec: Option<&str>, value: &f64) -> Result<String, String> {
    let locale = &active_number_locale().locale;
    if kind == NumericScale::Log {
        return format_float(locale, spec, *value);
    }
    // vega-format's `formatSpan` only derives from the tick step when the spec has no precision;
    // d3-scale's `tickFormat` also fixes one SI prefix for an `s` spec that has one.
    if let Some(spec) = spec.filter(|spec| parse_parts(spec).is_ok_and(|parts| parts.precision.is_some())) {
        return format(locale, spec, *value);
    }
    vega_tick_format(locale, spec, domain.0, domain.1, CAPTION_TICK_COUNT, *value)
}

fn time_text(utc: bool, spec: Option<&str>, value: &f64) -> Result<String, String> {
    if !value.is_finite() {
        return Ok("NaN".to_string());
    }
    let spec = match spec {
        Some(spec) => spec.replace("%a", "%A").replace("%b", "%B"),
        None if utc => "%A, %d %B %Y, %X UTC".to_string(),
        None => "%A, %d %B %Y, %X".to_string(),
    };
    let moment = Moment::from_epoch_millis(value.round() as i64)?;
    // d3-time-format pads `%Y` to four digits; strftime does not below 1000.
    strftime(moment, &spec.replace("%Y", &format!("{:04}", moment.year())))
}

/// The `format` Vega's axis carries for `axis.labels.format`
/// (`compose_axis_format`, `axis_to_vl`): a d3 or strftime spec, or `None`
/// where production paints through a `labelExpr` and leaves the axis
/// `format` unset. `quantitative` is the axis's channel type.
pub fn vega_axis_format(axis: &AxisStyle, quantitative: bool) -> Result<Option<String>, String> {
    let composed = axis.ruler.is_some() || axis.tick_label.is_some() || axis.labels.expr.is_some();
    axis.labels.format.as_ref().map_or(Ok(None), |slot| slot_format(slot, composed, quantitative))
}

/// [`vega_axis_format`] for one slot; `composed` is whether a ruler, a
/// `tick_label` ladder or an authored `labels.expr` paints the labels, which
/// leaves the slot's spec as the axis `format` whatever its affix.
pub fn slot_format(slot: &FormatSlot, composed: bool, quantitative: bool) -> Result<Option<String>, String> {
    let native = matches!(slot.spec.as_str(), "bytes" | "duration");
    let spec = (!native).then(|| slot.spec.clone());
    if composed {
        return Ok(spec);
    }
    if !slot.prefix.is_empty() || !slot.suffix.is_empty() {
        return Ok(None);
    }
    let is_time = super::temporal::is_time_format(&slot.spec);
    let symbol = quantitative && !is_time && !native && d3rs::format::parse_parts(&slot.spec)?.symbol;
    let si = !is_time && !native && d3rs::format::parse_parts(&slot.spec)?.type_ == 's';
    if native || symbol || (slot.notation.is_some() && si) {
        return Ok(symbol.then(|| slot.spec.clone()));
    }
    Ok(spec)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::FormatSlot;
    use crate::number_locale::{with_number_locale, NumberLocale};
    use crate::time_locale::{en_us, with_time_locale};

    fn describe(scale: CaptionScale, title: Option<&str>) -> String {
        let caption = AxisCaption { title: title.map(String::from), scale, bare: false };
        with_number_locale(&NumberLocale::en_us(), || {
            with_time_locale(&en_us(), || caption.describe(AxisChannel::X).unwrap())
        })
    }

    fn numeric(kind: NumericScale, lo: f64, hi: f64, format: Option<&str>) -> CaptionScale {
        CaptionScale::Numeric { kind, domain: (lo, hi), format: format.map(String::from) }
    }

    fn words(values: &[&str]) -> CaptionScale {
        CaptionScale::Discrete { values: values.iter().map(|v| v.to_string()).collect(), format: None }
    }

    // Expected strings come from `vega-scale`'s own `domainCaption` and
    // `vega-scenegraph`'s `axisCaption` (vega 6.2).

    #[test]
    fn linear_with_an_si_format_prints_the_authored_precision() {
        assert_eq!(
            describe(numeric(NumericScale::Linear, 0.0, 4_430_000.0, Some(".3~s")), None),
            "X-axis for a linear scale with values from 0 to 4.43M"
        );
    }

    #[test]
    fn linear_with_a_currency_format_and_a_title() {
        assert_eq!(
            describe(numeric(NumericScale::Linear, 406_240.0, 605_760.0, Some("$,.0f")), Some("Revenue")),
            "X-axis titled 'Revenue' for a linear scale with values from $406,240 to $605,760"
        );
    }

    #[test]
    fn linear_without_a_format_takes_its_precision_from_the_tick_step() {
        assert_eq!(
            describe(numeric(NumericScale::Linear, 0.0, 1500.0, None), None),
            "X-axis for a linear scale with values from 0 to 1,500"
        );
        assert_eq!(
            describe(numeric(NumericScale::Linear, 0.0, 0.5, Some(".0%")), None),
            "X-axis for a linear scale with values from 0% to 50%"
        );
    }

    /// An all-zero measure collapses the domain to `(0, 0)`, where Vega's bundled d3-format reads
    /// `exponent(0)` as 0 (vl-convert's own captions).
    #[test]
    fn an_all_zero_domain_prints_whole_numbers_as_vega_does() {
        for (format, zero) in [(None, "0"), (Some("s"), "0"), (Some("~s"), "0"), (Some("$s"), "$0"), (Some(".0%"), "0%")] {
            assert_eq!(
                describe(numeric(NumericScale::Linear, 0.0, 0.0, format), None),
                format!("X-axis for a linear scale with values from {zero} to {zero}"),
                "format {format:?}"
            );
        }
    }

    #[test]
    fn a_bare_si_format_fixes_one_prefix_for_the_whole_span() {
        assert_eq!(
            describe(numeric(NumericScale::Linear, 0.0, 81_800.0, Some("s")), None),
            "X-axis for a linear scale with values from 0k to 82k"
        );
    }

    #[test]
    fn log_prints_twelve_significant_digits_trimmed() {
        assert_eq!(
            describe(numeric(NumericScale::Log, 1e8, 1e10, Some(".3~s")), None),
            "X-axis for a log scale with values from 100M to 10G"
        );
        assert_eq!(
            describe(numeric(NumericScale::Log, 10_000.0, 100_000.0, Some("$,.0f")), None),
            "X-axis for a log scale with values from $10,000 to $100,000"
        );
        assert_eq!(
            describe(numeric(NumericScale::Log, 0.5, 2.5, None), None),
            "X-axis for a log scale with values from 0.5 to 2.5"
        );
    }

    #[test]
    fn utc_names_its_domain_through_the_axis_format() {
        let scale = CaptionScale::Time {
            utc: true,
            domain: (1_767_225_600_000.0, 1_780_272_000_000.0),
            format: Some("%b %Y".into()),
        };
        assert_eq!(describe(scale, None), "X-axis for a utc scale with values from January 2026 to June 2026");
    }

    #[test]
    fn time_and_utc_without_a_format_spell_out_the_instant() {
        let utc = CaptionScale::Time { utc: true, domain: (1_767_225_600_000.0, 1_780_272_000_000.0), format: None };
        assert_eq!(
            describe(utc, None),
            "X-axis for a utc scale with values from Thursday, 01 January 2026, 12:00:00 AM UTC \
             to Monday, 01 June 2026, 12:00:00 AM UTC"
        );
        let local = CaptionScale::Time { utc: false, domain: (0.0, 1_000_000_000_000.0), format: None };
        assert_eq!(
            describe(local, None),
            "X-axis for a time scale with values from Thursday, 01 January 1970, 12:00:00 AM \
             to Sunday, 09 September 2001, 1:46:40 AM"
        );
    }

    #[test]
    fn discrete_lists_every_value_up_to_seven() {
        assert_eq!(describe(words(&["a", "b", "c"]), None), "X-axis for a discrete scale with 3 values: a, b, c");
        assert_eq!(describe(words(&["EMEA"]), None), "X-axis for a discrete scale with 1 value: EMEA");
        let seven = ["a", "b", "c", "d", "e", "f", "g"];
        assert_eq!(
            describe(words(&seven), None),
            "X-axis for a discrete scale with 7 values: a, b, c, d, e, f, g"
        );
    }

    #[test]
    fn discrete_over_seven_ends_with_the_last_value() {
        let eight = ["a", "b", "c", "d", "e", "f", "g", "h"];
        assert_eq!(
            describe(words(&eight), Some("Letter")),
            "X-axis titled 'Letter' for a discrete scale with 8 values: a, b, c, d, e, ending with h"
        );
    }

    fn axis(patch: serde_json::Value) -> AxisStyle {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../../fixtures/line_single.json")).expect("fixture parses");
        let mut axis = fixture["resolved"]["style"]["axis_y"].clone();
        axis.as_object_mut().expect("an axis object").extend(patch.as_object().expect("a patch object").clone());
        serde_json::from_value(axis).expect("axis_y")
    }

    fn plain(mut axis: AxisStyle) -> AxisStyle {
        axis.labels.format = None;
        axis
    }

    fn x_caption(axis: &AxisStyle, vl_type: VegaType, grain: Grain, domain: &[&str]) -> String {
        let resolution = XResolution { vl_type, from_data: vl_type, grain, fill_grain: grain };
        let domain: Vec<String> = domain.iter().map(|d| d.to_string()).collect();
        let caption = x_axis_caption("", axis, &resolution, &domain, 600.0).expect("a caption");
        with_number_locale(&NumberLocale::en_us(), || {
            with_time_locale(&en_us(), || caption.describe(AxisChannel::X).expect("describes"))
        })
    }

    #[test]
    fn pow_sqrt_and_symlog_scales_name_themselves() {
        for (name, kind) in [
            ("pow", NumericScale::Pow),
            ("sqrt", NumericScale::Sqrt),
            ("symlog", NumericScale::Symlog),
            ("log", NumericScale::Log),
        ] {
            let axis = axis(serde_json::json!({"scale": {"continuous": {"type": name}}}));
            assert_eq!(NumericScale::of_axis(&axis).unwrap(), kind);
            assert!(describe(numeric(kind, 1.0, 100.0, None), None).contains(&format!("for a {name} scale")));
        }
    }

    #[test]
    fn a_hidden_title_is_left_out() {
        let mut axis = axis(serde_json::json!({}));
        axis.title.visible = Some(true);
        assert_eq!(drawn_title("Revenue", &axis.title).as_deref(), Some("Revenue"));
        assert_eq!(drawn_title("Net\nrevenue", &axis.title).as_deref(), Some("Net revenue"));
        axis.title.visible = Some(false);
        assert_eq!(drawn_title("Revenue", &axis.title), None);
        assert_eq!(drawn_title("", &AxisTitle { visible: Some(true), ..axis.title.clone() }), None);
    }

    #[test]
    fn an_affix_leaves_the_axis_format_to_the_label_expression() {
        let mut axis = axis(serde_json::json!({}));
        (axis.ruler, axis.tick_label, axis.labels.expr) = (None, None, None);
        let slot = |spec: &str| FormatSlot { spec: spec.into(), ..FormatSlot::default() };
        axis.labels.format = Some(slot(",.0f"));
        assert_eq!(vega_axis_format(&axis, true).unwrap().as_deref(), Some(",.0f"));
        axis.labels.format = Some(FormatSlot { prefix: "EUR ".into(), ..slot(",.0f") });
        assert_eq!(vega_axis_format(&axis, true).unwrap(), None);
        axis.labels.format = Some(slot("bytes"));
        assert_eq!(vega_axis_format(&axis, true).unwrap(), None);
        axis.labels.format = Some(slot("$,.0f"));
        assert_eq!(vega_axis_format(&axis, true).unwrap().as_deref(), Some("$,.0f"));
    }

    #[test]
    fn a_calendar_grain_names_its_bucket_starts_in_the_time_unit_format() {
        let axis = plain(axis(serde_json::json!({})));
        let month = Grain::Calendar(TimeUnit::YearMonth);
        assert_eq!(
            x_caption(&axis, VegaType::Temporal, month, &["2026-06-01", "2026-01-01", "2026-03-01"]),
            "X-axis for a utc scale with values from January 2026 to June 2026"
        );
        let week = Grain::Calendar(TimeUnit::YearWeek);
        assert_eq!(
            x_caption(&axis, VegaType::Temporal, week, &["2024-01-03", "2024-01-17"]),
            "X-axis for a utc scale with values from 2023 W53 to 2024 W02"
        );
    }

    #[test]
    fn a_year_below_a_thousand_pads_to_four_digits_and_below_a_hundred_leaves_the_domain() {
        let axis = plain(axis(serde_json::json!({})));
        let year = Grain::Calendar(TimeUnit::Year);
        assert_eq!(
            x_caption(&axis, VegaType::Temporal, year, &["0099-01-01", "0100-01-01", "0102-01-01"]),
            "X-axis for a utc scale with values from 0100 to 0102"
        );
    }

    #[test]
    fn dates_without_a_grain_are_a_time_scale_and_a_gap_filled_axis_is_discrete() {
        let axis = plain(axis(serde_json::json!({})));
        assert_eq!(
            x_caption(&axis, VegaType::Temporal, Grain::None, &["2024-03-05", "2024-03-06"]),
            "X-axis for a time scale with values from Tuesday, 05 March 2024, 12:00:00 AM \
             to Wednesday, 06 March 2024, 12:00:00 AM"
        );
        assert_eq!(
            x_caption(
                &axis,
                VegaType::Ordinal,
                Grain::Calendar(TimeUnit::YearMonthDate),
                &["2025-01-20T00:00:00", "2025-01-21"]
            ),
            "X-axis for a discrete scale with 2 values: 2025-01-20, 2025-01-21"
        );
    }

    #[test]
    fn a_time_part_is_a_time_scale_under_its_own_format() {
        let axis = plain(axis(serde_json::json!({})));
        let part = Grain::Part(super::super::temporal::TimePart::MonthOfYear);
        assert_eq!(
            x_caption(&axis, VegaType::Temporal, part, &["2024-02-10", "2024-07-04"]),
            "X-axis for a time scale with values from February to July"
        );
    }

    #[test]
    fn a_number_format_on_a_discrete_axis_reads_each_value_as_a_number() {
        let scale = CaptionScale::Discrete {
            values: vec!["2".into(), "East".into()],
            format: Some(",.0f".into()),
        };
        assert_eq!(describe(scale, None), "X-axis for a discrete scale with 2 values: 2, NaN");
    }
}
