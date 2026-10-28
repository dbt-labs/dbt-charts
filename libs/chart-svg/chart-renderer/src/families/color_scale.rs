//! The continuous color scale a cartesian chart's color channel draws over a
//! numeric, date or authored-gradient column (Vega's `linear` and `time`
//! scales with a color range), shared by every family that paints one.

use d3rs::color::{byte, interpolate_hcl_color, interpolate_rgb_piecewise, rgb_text, Color, Rgb};
use d3rs::vega_schemes::BLUES;

use crate::common::{FormatSlot, ScaleTarget};
use crate::families::heatmap_facet::{GradientLegend, GradientPaint};
use crate::families::table::bind::{arm_denominators, resolve_hinge};
use crate::families::legend::{
    gradient_labeled_values, gradient_tick_count, unit_position, LegendConfig, GRADIENT_STOP_TICKS,
    VEGA_GRADIENT_THICKNESS, VEGA_LEGEND_TITLE_PADDING,
};
use crate::contract;
use measure::baseline_shift;
use scene::{Attrs, Node};
use print::print;
use measure::FontBook;
use crate::vega_fit;

/// How a value in the scale's domain becomes a color.
enum Ramp {
    /// A scheme's control colors, mixed piecewise in sRGB along the unit
    /// position (`blues`, the default `ramp`, and every continuous scheme).
    Rgb(&'static [Rgb]),
    /// A time scale's two-value domain: a scheme sampled at a third and two
    /// thirds (`quantize(scheme, 2)`), mixed in HCL.
    DatePair(&'static [Rgb]),
    /// An authored palette, its stops mixed piecewise in HCL (Vega-Lite's
    /// default `interpolate` for a color range list), evenly over the domain.
    Stops(Vec<Color>),
    /// A diverging palette: `colors[i]` sits at `domain[i]`, mixed in HCL and
    /// clamped to the domain's ends.
    Diverging { domain: Vec<f64>, colors: Vec<Color> },
    /// A scheme under an asymmetric pivot: the domain spans the ramp's
    /// positions `extent`, clamped (`scheme.extent`).
    Windowed { ramp: &'static [Rgb], extent: (f64, f64) },
    /// A scheme under a symmetric pivot (`domainMid`): each arm of the domain
    /// spans half the ramp, clamped. A categorical scheme's array is a range
    /// list, mixed in HCL (`hcl`); a continuous scheme is sampled by position.
    Halved { ramp: &'static [Rgb], pivot: f64, hcl: bool },
}

/// Where `v` sits in `[lo, hi]` for a sequential color scale: d3's `scaleSequential` multiplies by the
/// domain's reciprocal (`75000 * (1 / 100000)` is `0.7500000000000001`, which rounds a tie channel the
/// other way than dividing does) and puts a zero-width domain mid-ramp.
fn sequential_position(v: f64, lo: f64, hi: f64) -> f64 {
    if lo == hi {
        0.5
    } else {
        (v - lo) * (1.0 / (hi - lo))
    }
}

/// A color scale over the data extent `[lo, hi]`; a date scale counts days.
pub(crate) struct ContinuousColor {
    ramp: Ramp,
    pub(crate) lo: f64,
    pub(crate) hi: f64,
    dates: bool,
    /// A date scale over instants the board zone reads ([`ladder_zone`](super::temporal::ladder_zone)).
    aware: bool,
    /// The diverging scale's pivot; Vega labels it on the legend beside the ticks.
    pub(crate) pivot: Option<f64>,
}

impl ContinuousColor {
    pub(crate) fn blues(lo: f64, hi: f64) -> Self {
        Self { ramp: Ramp::Rgb(&BLUES), lo, hi, dates: false, aware: false, pivot: None }
    }

    /// A time scale over `[lo, hi]` days, colored from `scheme`; `aware` when its dates are instants the board
    /// zone reads.
    pub(crate) fn dates(scheme: &'static [Rgb], lo: f64, hi: f64, aware: bool) -> Self {
        Self { ramp: Ramp::DatePair(scheme), lo, hi, dates: true, aware, pivot: None }
    }

    #[cfg(test)]
    pub(crate) fn stops(colors: Vec<Rgb>, lo: f64, hi: f64) -> Self {
        Self { ramp: Ramp::Stops(colors.into_iter().map(Color::opaque).collect()), lo, hi, dates: false, aware: false, pivot: None }
    }

    /// A gradient-mode channel's scale over `kept`, the extent of the values
    /// the marks draw, and `all`, the extent over every row
    /// (`gradient_scale_to_vl` with `channel_to_encoding`'s hinge-only data).
    ///
    /// A sequential scale spans `kept`, either end replaced by an authored
    /// `min`/`max`. A diverging one (`hinge`) spans its bounds extended to
    /// hold the pivot, with the palette's breakpoints placed from the pivot.
    pub(crate) fn gradient(scale: &ScaleTarget, all: Option<(f64, f64)>, kept: (f64, f64), legend_drawn: bool) -> Result<Self, String> {
        let nice = scale.nice.unwrap_or(legend_drawn);
        let palette = Palette::of(scale)?;
        if let Some(hinge) = &scale.hinge {
            if let Some(bounds) = diverging_bounds(scale, all, nice) {
                let raw = raw_bounds(scale, all).expect("bounds resolve only from raw bounds");
                if let Some(pivot) = resolve_hinge(Some(hinge), raw.0, raw.1, None) {
                    let (lo, hi) = (bounds.0.min(pivot), bounds.1.max(pivot));
                    let asymmetric = scale.arm_mode == "asymmetric";
                    let (neg, pos) = arm_denominators(pivot, (lo, hi), asymmetric);
                    let ramp = match palette {
                        Palette::Stops(stops) => {
                            let (domain, colors) = breakpoints_truncated(stops, pivot, (lo, hi), (neg, pos))?;
                            Ramp::Diverging { domain, colors }
                        }
                        Palette::Ramp(ramp) if asymmetric => {
                            // `_diverging_scheme_extent`: the ramp positions the domain's ends reach.
                            Ramp::Windowed { ramp, extent: (0.5 - (pivot - lo) / (2.0 * neg), 0.5 + (hi - pivot) / (2.0 * pos)) }
                        }
                        Palette::Ramp(ramp) => Ramp::Halved { ramp, pivot, hcl: false },
                        // A categorical array is no ramp to window, so it runs its colors evenly over the domain.
                        Palette::Array(stops) if asymmetric => Ramp::Stops(stops.iter().copied().map(Color::opaque).collect()),
                        Palette::Array(colors) => Ramp::Halved { ramp: colors, pivot, hcl: true },
                    };
                    return Ok(Self { ramp, lo, hi, dates: false, aware: false, pivot: Some(pivot) });
                }
            }
        }
        let kept = nice_domain(scale.min, scale.max, nice, kept);
        let (lo, hi) = (scale.min.unwrap_or(kept.0), scale.max.unwrap_or(kept.1));
        let ramp = match palette {
            Palette::Stops(stops) => Ramp::Stops(stops.into_iter().map(|stop| stop.vega).collect()),
            Palette::Ramp(ramp) => Ramp::Rgb(ramp),
            Palette::Array(colors) => Ramp::Stops(colors.iter().copied().map(Color::opaque).collect()),
        };
        Ok(Self { ramp, lo, hi, dates: false, aware: false, pivot: None })
    }

    /// Whether the scale runs over dates, in day numbers.
    pub(crate) fn is_aware(&self) -> bool {
        self.aware
    }

    pub(crate) fn is_dated(&self) -> bool {
        self.dates
    }

    /// The color at `v`, printed the way d3 formats an `rgb()`. A one-value
    /// domain maps everything to the middle of the ramp.
    /// Whether the scale gives a mark a color: Vega's interpolator over a
    /// one-color range yields none, so production's chart paints no marks.
    pub(crate) fn paints(&self) -> bool {
        !matches!(&self.ramp, Ramp::Stops(colors) if colors.len() == 1)
    }

    pub(crate) fn fill(&self, v: f64) -> String {
        match &self.ramp {
            Ramp::Diverging { domain, colors } => {
                // d3 pairs a one-point domain with no second stop, so every value takes the lone color.
                if domain.len() == 1 {
                    return color_text(colors[0]);
                }
                let v = v.clamp(self.lo, self.hi);
                let i = domain.partition_point(|&x| x <= v).saturating_sub(1).min(domain.len() - 2);
                // d3 maps a zero-width segment to its middle.
                let span = domain[i + 1] - domain[i];
                let t = if span == 0.0 { 0.5 } else { (v - domain[i]) / span };
                color_text(interpolate_hcl_color(colors[i], colors[i + 1], t))
            }
            Ramp::Windowed { ramp, extent: (a, b) } => {
                let t = unit_position(v.clamp(self.lo, self.hi), self.lo, self.hi);
                rgb_text(interpolate_rgb_piecewise(ramp, a + t * (b - a)))
            }
            Ramp::Halved { ramp, pivot, hcl } => {
                // d3's `scaleDiverging`: each arm's own reciprocal width, zero for a collapsed one, and the
                // clamp on the position. An authored `min` past the pivot runs the domain backwards (`s`).
                let arm = |width: f64| if width == 0.0 { 0.0 } else { 0.5 / width };
                let s = if *pivot < self.lo { -1.0 } else { 1.0 };
                let k = if s * v < s * pivot { arm(pivot - self.lo) } else { arm(self.hi - pivot) };
                let t = (0.5 + (v - pivot) * k).clamp(0.0, 1.0);
                if *hcl {
                    color_text(piecewise_hcl(&ramp.iter().copied().map(Color::opaque).collect::<Vec<_>>(), t))
                } else {
                    rgb_text(interpolate_rgb_piecewise(ramp, t))
                }
            }
            _ if self.dates => self.fill_at(unit_position(v, self.lo, self.hi)),
            _ => self.fill_at(sequential_position(v, self.lo, self.hi)),
        }
    }

    /// The color a sequential ramp takes at position `t` of its domain.
    pub(crate) fn fill_at(&self, t: f64) -> String {
        // A pivoted scale is positioned by value; only its collapsed legend
        // (`lo == hi`) asks by fraction, and every fraction is that one value.
        if matches!(self.ramp, Ramp::Diverging { .. } | Ramp::Windowed { .. } | Ramp::Halved { .. }) {
            assert!(self.lo == self.hi, "a pivoted scale is positioned by value, never by fraction");
            return self.fill(self.lo);
        }
        match &self.ramp {
            Ramp::Rgb(colors) => rgb_text(interpolate_rgb_piecewise(colors, t)),
            Ramp::DatePair(scheme) => rgb_text(d3rs::color::quantized_pair_hcl(scheme, t)),
            Ramp::Stops(colors) => color_text(piecewise_hcl(colors, t)),
            Ramp::Diverging { .. } | Ramp::Windowed { .. } | Ramp::Halved { .. } => unreachable!("handled above"),
        }
    }

    /// The values a gradient legend `length` px long labels, with their text:
    /// the scale's ticks (its two ends when there are fewer than three). A
    /// date prints as Vega's `%b %d, %Y`; a number through the column's own
    /// `format` when the query declares one, else at the precision the tick
    /// step implies.
    pub(crate) fn legend_ladder(
        &self,
        length: f64,
        format: Option<&FormatSlot>,
        authored: Option<&[String]>,
    ) -> Result<(Vec<f64>, Vec<String>), String> {
        let count = gradient_tick_count(length);
        if self.dates {
            let values = crate::families::heatmap::date_legend_ticks(self.lo, self.hi, count, authored, self.aware)?;
            let labels = values.iter().map(|&d| crate::families::heatmap::legend_date_label(d, self.aware)).collect::<Result<_, _>>()?;
            return Ok((values, labels));
        }
        let values = match authored {
            // An authored `legend.values` replaces the ladder, each token read as
            // the number d3's scale coerces it to; a non-number is `NaN`, which
            // Vega labels `NaN` at the bar's origin.
            // The legend's data join keys its entries by value, so a repeat (`NaN` included) is one entry.
            Some(tokens) => {
                let mut values: Vec<f64> = Vec::new();
                for v in tokens.iter().map(|t| crate::common::js_string_to_number(t).unwrap_or(f64::NAN)) {
                    if !values.iter().any(|&seen| seen == v || (seen.is_nan() && v.is_nan())) {
                        values.push(v);
                    }
                }
                values
            }
            None => gradient_labeled_values(d3rs::ticks::d3_ticks(self.lo, self.hi, count), self.lo, self.hi),
        };
        let spec = crate::number_locale::vega_tick_spec(d3rs::ticks::d3_tick_step(self.lo, self.hi, count));
        let labels = values
            .iter()
            .map(|&v| match format {
                _ if v.is_nan() => Ok("NaN".to_string()),
                Some(format) => format.paint_tick(v),
                None => crate::number_locale::d3_format(&spec, v),
            })
            .collect::<Result<_, _>>()?;
        Ok((values, labels))
    }

    /// Where the legend's `<linearGradient>` places a stop besides its two
    /// ends: the scale's own ticks at `GRADIENT_STOP_TICKS`.
    pub(crate) fn stop_ticks(&self) -> Result<Vec<f64>, String> {
        if self.dates {
            let zone = crate::families::temporal::ladder_zone(self.aware);
            return crate::families::temporal::zone_instants(self.lo, self.hi, GRADIENT_STOP_TICKS, zone.as_ref());
        }
        Ok(d3rs::ticks::d3_ticks(self.lo, self.hi, GRADIENT_STOP_TICKS))
    }
}

/// The d3 spec `compose_axis_format` hands Vega-Lite as a legend's own `format`
/// (`vl_field_maps.py:221`): a bare spec, with no affix, no SI notation and
/// none of the two Vega arms (`bytes`, `duration`), which it paints through
/// `labelExpr` instead. The mark's tooltip reads it too: a color whose legend
/// carries no `format` prints as the bare number.
pub(crate) fn legend_format_spec(format: &FormatSlot) -> Option<&str> {
    let notated_si = format.notation.is_some() && d3rs::format::parse_parts(&format.spec).is_ok_and(|p| p.type_ == 's');
    let painted_by_expr = !format.prefix.is_empty() || !format.suffix.is_empty() || notated_si;
    (!painted_by_expr && !matches!(format.spec.as_str(), "bytes" | "duration")).then_some(format.spec.as_str())
}

/// JavaScript's `String(number)`: Rust's shortest round-trip digits, spelled in
/// exponent form past `1e21` and below `1e-6`, where JavaScript switches.
pub(crate) fn js_number_string(v: f64) -> String {
    if v == 0.0 {
        return "0".to_string();
    }
    if (1e-6..1e21).contains(&v.abs()) {
        return format!("{v}");
    }
    let spelled = format!("{v:e}");
    match spelled.split_once('e') {
        Some((mantissa, exp)) if !exp.starts_with('-') => format!("{mantissa}e+{exp}"),
        _ => spelled,
    }
}

/// `d3.piecewise(d3.interpolateHcl, colors)(t)`. An empty range has nothing to
/// mix: Vega paints it black.
fn piecewise_hcl(colors: &[Color], t: f64) -> Color {
    match colors {
        [] => Color::opaque((0, 0, 0)),
        // A one-color ramp has no segment to mix; the scale reports it unpaintable.
        [only] => *only,
        _ => {
            let n = (colors.len() - 1) as f64;
            let scaled = t * n;
            let i = scaled.floor().clamp(0.0, n - 1.0) as usize;
            interpolate_hcl_color(colors[i], colors[i + 1], scaled - i as f64)
        }
    }
}

/// d3's `formatRgb`: `rgb(r, g, b)`, or `rgba(r, g, b, a)` under a partial
/// opacity, each channel rounded and clamped to a byte.
fn color_text(c: Color) -> String {
    let alpha = if c.a.is_nan() { 1.0 } else { c.a.clamp(0.0, 1.0) };
    let (r, g, b) = (byte(c.r), byte(c.g), byte(c.b));
    if alpha == 1.0 {
        format!("rgb({r}, {g}, {b})")
    } else {
        format!("rgba({r}, {g}, {b}, {})", js_number_string(alpha))
    }
}

/// What a gradient channel's `palette` names: color stops (a named palette's
/// baked ones, or the authored list), a continuous Vega scheme, or a
/// categorical one.
enum Palette {
    Stops(Vec<Stop>),
    Ramp(&'static [Rgb]),
    Array(&'static [Rgb]),
}

/// One authored palette stop: as Vega reads its text (`vega`, undefined when d3 cannot parse it, as
/// for a space-separated `hsl()`), and as production's own parser reads it for the HCL blends it
/// bakes (`python`).
#[derive(Clone, Copy)]
struct Stop {
    vega: Color,
    python: Option<Color>,
}

/// `colors.py::parse_css_color` over what d3-color does not read: `none`, and the CSS-4 function
/// spellings (space-separated channels, `/ alpha`, a `%` alpha, a `deg` hue).
fn python_color(text: &str) -> Option<Color> {
    let lowered = text.trim().to_ascii_lowercase();
    if lowered == "none" {
        return d3rs::color::parse_color("transparent");
    }
    if let Some(color) = d3rs::color::parse_color(&lowered) {
        return Some(color);
    }
    let (name, rest) = lowered.split_once('(')?;
    let body = rest.strip_suffix(')')?;
    if !matches!(name, "rgb" | "rgba" | "hsl" | "hsla") {
        return None;
    }
    let (channels, slash_alpha) = match body.split_once('/') {
        Some((channels, alpha)) => (channels.trim(), Some(alpha.trim())),
        None => (body.trim(), None),
    };
    let mut tokens: Vec<&str> = if channels.contains(',') { channels.split(',').map(str::trim).collect() } else { channels.split_whitespace().collect() };
    let alpha_token = match (slash_alpha, tokens.len()) {
        (Some(alpha), 3) => alpha,
        (None, 4) => tokens.pop().expect("four tokens"),
        (None, 3) => "1",
        _ => return None,
    };
    let alpha = match alpha_token.strip_suffix('%') {
        Some(percent) => percent.trim().parse::<f64>().ok()? / 100.0,
        None => alpha_token.parse::<f64>().ok()?,
    }
    .clamp(0.0, 1.0);
    let [first, second, third] = tokens[..] else { return None };
    if name.starts_with("rgb") {
        let channel = |token: &str| match token.strip_suffix('%') {
            Some(percent) => percent.trim().parse::<f64>().ok().map(|p| (p * 255.0 / 100.0).clamp(0.0, 255.0)),
            None => token.parse::<f64>().ok().map(|v| v.clamp(0.0, 255.0)),
        };
        return Some(Color { r: channel(first)?, g: channel(second)?, b: channel(third)?, a: alpha });
    }
    let hue = first.strip_suffix("deg").unwrap_or(first).trim();
    d3rs::color::parse_color(&format!("hsla({hue}, {second}, {third}, {alpha})"))
}

impl Palette {
    fn of(scale: &ScaleTarget) -> Result<Self, String> {
        let texts: Vec<&str> = match (&scale.resolved_stops, &scale.palette) {
            (Some(stops), _) => stops.iter().map(String::as_str).collect(),
            (None, serde_json::Value::Array(list)) => list
                .iter()
                .map(|c| c.as_str().ok_or_else(|| format!("gradient palette color {c} is not a string")))
                .collect::<Result<_, _>>()?,
            (None, serde_json::Value::String(name)) => {
                let scheme = d3rs::vega_schemes::scheme(name)
                    .ok_or_else(|| format!("gradient palette {name:?} is neither a color list nor a Vega scheme"))?;
                return Ok(if scheme.continuous { Self::Ramp(scheme.colors) } else { Self::Array(scheme.colors) });
            }
            (None, other) => return Err(format!("gradient palette {other} is not a list of colors")),
        };
        Ok(Self::Stops(
            texts.into_iter().map(|c| Stop { vega: d3rs::color::parse_color(c).unwrap_or(Color::UNDEFINED), python: python_color(c) }).collect(),
        ))
    }
}

/// The nice tick ladder over a data extent: the one rounding law every gradient consumer
/// (heatmap, channel colors, maps, table columns) shares.
pub(crate) fn nice_ticks(lo: f64, hi: f64) -> Vec<f64> {
    d3rs::ticks::nice_tick_values(lo, hi, crate::constants::axis::GRADIENT_NICE_TICK_COUNT, None)
}

/// A gradient's color domain over the data extent `(lo, hi)`: widened to `nice_ticks`' round ends when
/// `nice` and neither bound is authored (a single-sided bound fixes that edge exactly); otherwise the
/// extent itself. An empty (non-finite) extent stays empty. Callers resolve `nice` as the author's
/// value, else whether the scale's legend is drawn.
pub(crate) fn nice_domain(min: Option<f64>, max: Option<f64>, nice: bool, (lo, hi): (f64, f64)) -> (f64, f64) {
    if !nice || min.is_some() || max.is_some() || !lo.is_finite() || !hi.is_finite() {
        return (lo, hi);
    }
    let ticks = nice_ticks(lo, hi);
    (ticks[0], ticks[ticks.len() - 1])
}

/// The data extent a diverging pivot is decided from, never nice-widened: an
/// authored bound wins per edge (`_raw_diverging_bounds`).
fn raw_bounds(scale: &ScaleTarget, all: Option<(f64, f64)>) -> Option<(f64, f64)> {
    if let (Some(lo), Some(hi)) = (scale.min, scale.max) {
        return Some((lo, hi));
    }
    let (lo, hi) = all?;
    Some((scale.min.unwrap_or(lo), scale.max.unwrap_or(hi)))
}

/// The bounds a diverging scale renders within (`_diverging_bounds`): both
/// authored, else the nice-widened data extent when neither is, else the raw
/// bounds.
fn diverging_bounds(scale: &ScaleTarget, all: Option<(f64, f64)>, nice: bool) -> Option<(f64, f64)> {
    if let (Some(lo), Some(hi)) = (scale.min, scale.max) {
        return Some((lo, hi));
    }
    if scale.min.is_none() && scale.max.is_none() && nice {
        return all.map(|extent| nice_domain(None, None, true, extent));
    }
    raw_bounds(scale, all)
}

/// An HCL blend of two stops as production hands it to Vega (`interpolate_hcl`): a hex color
/// when opaque, else `rgba(r, g, b, a)` with the linearly blended alpha rounded to four
/// places; either way each channel is already a byte. An unreadable stop raises there.
fn blended(a: Stop, b: Stop, t: f64) -> Result<Stop, String> {
    let (Some(a), Some(b)) = (a.python, b.python) else {
        return Err("gradient palette stop is not a readable color".to_string());
    };
    let mixed = interpolate_hcl_color(a, b, t);
    let channels = (byte(mixed.r), byte(mixed.g), byte(mixed.b));
    let color = if mixed.a >= 1.0 {
        Color::opaque(channels)
    } else {
        let alpha: f64 = format!("{:.4}", mixed.a).parse().expect("a formatted float parses");
        Color { r: f64::from(channels.0), g: f64::from(channels.1), b: f64::from(channels.2), a: alpha }
    };
    Ok(Stop { vega: color, python: Some(color) })
}

/// `_diverging_breakpoints_truncated`: the palette's stops at their natural
/// breakpoints about the pivot, cut to `[lo, hi]` with one HCL-blended stop
/// at a bound a dropped breakpoint crossed. An even stop count gains a
/// center stop blended from the two middle ones.
fn breakpoints_truncated(
    mut palette: Vec<Stop>,
    pivot: f64,
    (lo, hi): (f64, f64),
    (neg, pos): (f64, f64),
) -> Result<(Vec<f64>, Vec<Color>), String> {
    if palette.len() < 2 {
        return Err(format!("diverging gradient (hinge set) requires at least 2 palette stops, got {}", palette.len()));
    }
    if palette.len() % 2 == 0 {
        let mid = palette.len() / 2;
        palette.insert(mid, blended(palette[mid - 1], palette[mid], 0.5)?);
    }
    let n = palette.len();
    let mid = n / 2;
    let mut domain: Vec<f64> = (0..n)
        .map(|i| {
            if i <= mid {
                pivot - (mid - i) as f64 / mid as f64 * neg
            } else {
                pivot + (i - mid) as f64 / mid as f64 * pos
            }
        })
        .collect();
    let snap = 1e-9 * (hi - lo);
    if (domain[0] - lo).abs() < snap && lo < domain[1] {
        domain[0] = lo;
    }
    if (domain[n - 1] - hi).abs() < snap && hi > domain[n - 2] {
        domain[n - 1] = hi;
    }
    let points: Vec<(f64, Stop)> = domain.into_iter().zip(palette).collect();
    let mut kept: Vec<(f64, Stop)> = points.iter().copied().filter(|&(x, _)| lo <= x && x <= hi).collect();
    let dropped_lo = points.iter().copied().filter(|&(x, _)| x < lo).last();
    let dropped_hi = points.iter().copied().find(|&(x, _)| x > hi);
    if let Some((x0, c0)) = dropped_lo {
        if kept[0].0 != lo {
            let (x1, c1) = kept[0];
            kept.insert(0, (lo, blended(c0, c1, (lo - x0) / (x1 - x0))?));
        }
    }
    if let Some((x1, c1)) = dropped_hi {
        let (x0, c0) = kept[kept.len() - 1];
        if x0 != hi {
            kept.push((hi, blended(c0, c1, (hi - x0) / (x1 - x0))?));
        }
    }
    let (domain, stops): (Vec<f64>, Vec<Stop>) = kept.into_iter().unzip();
    Ok((domain, stops.into_iter().map(|stop| stop.vega).collect()))
}

/// A gradient legend's ladder at one plot size: a vertical bar follows the
/// plot's height, a horizontal one its width (`defaultGradientLength`).
pub(crate) struct GradientAt {
    pub(crate) length: f64,
    values: Vec<f64>,
    labels: Vec<String>,
    ticks: Vec<f64>,
}

impl GradientAt {
    pub(crate) fn new(
        legend: &LegendConfig,
        color_format: Option<&FormatSlot>,
        color: &ContinuousColor,
        unit: (f64, f64),
    ) -> Result<Self, String> {
        Self::with_ends(legend, color_format, color, unit, None)
    }

    /// [`Self::new`] with `ends`, the values the chart pins the legend to when `legend.values` authors
    /// none (a choropleth labels its scale's ends).
    pub(crate) fn with_ends(
        legend: &LegendConfig,
        color_format: Option<&FormatSlot>,
        color: &ContinuousColor,
        (pw, ph): (f64, f64),
        ends: Option<&[String]>,
    ) -> Result<Self, String> {
        let position = &legend.position;
        let length = if legend.direction == "vertical" {
            crate::families::legend::gradient_length(ph)
        } else {
            crate::families::legend::horizontal_gradient_length(&position.edge, position.overlay, pw)
        };
        let (values, labels) = color.legend_ladder(length, color_format, legend.values.as_deref().or(ends))?;
        Ok(Self { length, values, labels, ticks: color.stop_ticks()? })
    }

    /// The legend's box `(width, height)` beside a unit `(pw, ph)`.
    pub(crate) fn measure(
        legend: &LegendConfig,
        color_format: Option<&FormatSlot>,
        color: &ContinuousColor,
        (id, title): (&str, &str),
        fonts: &FontBook,
        unit: (f64, f64),
    ) -> Result<(f64, f64), String> {
        let at = Self::new(legend, color_format, color, unit)?;
        let laid = at.legend(legend, color, id, title, fonts)?;
        Ok((laid.box_width(at.length), laid.height(at.length)))
    }

    /// Paints the legend at `(x, y)`, sized for a unit `(pw, ph)`.
    pub(crate) fn paint(
        legend: &LegendConfig,
        color_format: Option<&FormatSlot>,
        color: &ContinuousColor,
        (id, title): (&str, &str),
        fonts: &FontBook,
        unit: (f64, f64),
        (svg, x, y): (&mut String, f64, f64),
    ) -> Result<(), String> {
        svg.push_str(&print(&Self::nodes(legend, color_format, color, (id, title), fonts, unit, (x, y))?));
        Ok(())
    }

    /// [`Self::paint`] as nodes.
    pub(crate) fn nodes(
        legend: &LegendConfig,
        color_format: Option<&FormatSlot>,
        color: &ContinuousColor,
        (id, title): (&str, &str),
        fonts: &FontBook,
        unit: (f64, f64),
        (x, y): (f64, f64),
    ) -> Result<Vec<Node>, String> {
        let at = Self::new(legend, color_format, color, unit)?;
        Ok(at.legend(legend, color, id, title, fonts)?.nodes(x, y, at.length))
    }

    pub(crate) fn legend<'a>(
        &'a self,
        legend: &'a LegendConfig,
        color: &'a ContinuousColor,
        id: &'a str,
        title: &str,
        fonts: &FontBook,
    ) -> Result<GradientLegend<'a>, String> {
        let paint = GradientPaint::Scale(color, self.ticks.clone());
        GradientLegend::over(legend, paint, id, (color.lo, color.hi, &self.values), &self.labels, fonts)?
            .with_title(legend, title.to_string(), fonts)
    }
}

/// How many ticks a symbol legend over a continuous scale draws (`legend.tickCount`'s default).
const SYMBOL_LEGEND_TICKS: usize = 5;

impl ContinuousColor {
    /// A symbol legend's entries over this scale: each tick's text and color.
    pub(crate) fn symbol_ladder(&self, format: Option<&FormatSlot>) -> Result<Vec<(String, String)>, String> {
        let spec = crate::number_locale::vega_tick_spec(d3rs::ticks::d3_tick_step(self.lo, self.hi, SYMBOL_LEGEND_TICKS));
        d3rs::ticks::d3_ticks(self.lo, self.hi, SYMBOL_LEGEND_TICKS)
            .into_iter()
            .map(|v| {
                let label = match format {
                    Some(slot) => slot.paint(v)?,
                    None => crate::number_locale::d3_format(&spec, v)?,
                };
                Ok((label, self.fill(v)))
            })
            .collect()
    }
}

/// The legends of layers colored by a numeric field, one beside the base's own
/// in layer order: each layer's color scale is its own, so Vega draws its
/// legend apart. A bar or scatter layer's is a gradient, a line or area layer's
/// a column of circles.
pub(crate) struct NumericLegends<'a> {
    parts: Vec<NumericPart<'a>>,
    /// A line layer's unlisted entry still collapses the base legend's cells to the
    /// font size (`_mixed_mark_legend_symbols`).
    pub(crate) unlisted_line: bool,
}

struct NumericPart<'a> {
    color: &'a ContinuousColor,
    gradient: bool,
    /// How the query prints the color column.
    format: Option<&'a FormatSlot>,
    title: String,
    id: String,
}

/// Where each numeric legend sits relative to the stack's origin, and the whole stack's size.
pub(crate) struct LegendStack {
    pub(crate) offsets: Vec<(f64, f64)>,
    pub(crate) size: (f64, f64),
    /// Whether a legend of the chart's own series leads the stack; a chart whose
    /// color field is numeric has only the numeric legends.
    base_block: bool,
}

impl<'a> NumericLegends<'a> {
    /// `layers` yields each numeric color's scale, whether it draws a gradient, and its legend title.
    pub(crate) fn new(
        layers: impl IntoIterator<Item = (&'a ContinuousColor, bool, String, Option<&'a FormatSlot>)>,
        cfg: &LegendConfig,
        id_stem: &str,
    ) -> Result<Self, String> {
        let parts = layers
            .into_iter()
            .enumerate()
            .map(|(i, (color, gradient, title, format))| {
                Ok(NumericPart {
                    color,
                    gradient,
                    format,
                    title: if cfg.title.visible == Some(false) { String::new() } else { title },
                    id: format!("{id_stem}-{i}"),
                })
            })
            .collect::<Result<_, String>>()?;
        Ok(Self { parts, unlisted_line: false })
    }

    pub(crate) fn len(&self) -> usize {
        self.parts.len()
    }

    /// Legend `index`'s box `(w, h)` beside a unit `(pw, ph)`.
    pub(crate) fn measure_part(
        &self,
        index: usize,
        cfg: &LegendConfig,
        fonts: &FontBook,
        unit: (f64, f64),
    ) -> Result<(f64, f64), String> {
        let part = &self.parts[index];
        if part.gradient {
            GradientAt::measure(cfg, part.format, part.color, (&part.id, &part.title), fonts, unit)
        } else {
            let (legend, _) = part.symbol_legend(cfg, fonts)?;
            Ok((legend.box_w, legend.box_h()))
        }
    }

    /// Paints legend `index` at `(x, y)` beside a unit `(pw, ph)`.
    pub(crate) fn paint_part(
        &self,
        index: usize,
        svg: &mut String,
        cfg: &LegendConfig,
        fonts: &FontBook,
        unit: (f64, f64),
        at: (f64, f64),
    ) -> Result<(), String> {
        svg.push_str(&print(&self.part_nodes(index, cfg, fonts, unit, at)?));
        Ok(())
    }

    pub(crate) fn part_nodes(
        &self,
        index: usize,
        cfg: &LegendConfig,
        fonts: &FontBook,
        unit: (f64, f64),
        (x, y): (f64, f64),
    ) -> Result<Vec<Node>, String> {
        let part = &self.parts[index];
        if part.gradient {
            return GradientAt::nodes(cfg, part.format, part.color, (&part.id, &part.title), fonts, unit, (x, y));
        }
        let (legend, ladder) = part.symbol_legend(cfg, fonts)?;
        let entries: Vec<_> = ladder
            .iter()
            .map(|(label, fill)| crate::families::legend::LegendEntry {
                label,
                fill,
                symbol: Some(crate::families::legend::LegendSymbol::Circle),
                series_key: None,
                recede: None,
            })
            .collect();
        Ok(crate::families::legend::drawn_symbol_legend(
            &entries,
            &legend,
            cfg,
            crate::families::legend::LegendSymbol::Circle,
            x,
            y,
            false,
        )
        .into_iter()
        .collect())
    }

    /// The stack of the base legend's box `base` and these, one `VEGA_LEGEND_GROUP_MARGIN` apart.
    pub(crate) fn stack(
        &self,
        base: (f64, f64),
        cfg: &LegendConfig,
        fonts: &FontBook,
        unit: (f64, f64),
    ) -> Result<LegendStack, String> {
        let base_block = base != (0.0, 0.0);
        let mut blocks = if base_block { vec![base] } else { Vec::new() };
        for index in 0..self.len() {
            blocks.push(self.measure_part(index, cfg, fonts, unit)?);
        }
        let along_x = matches!(cfg.position.edge.as_str(), "top" | "bottom");
        let (along, size) = crate::families::legend::stack_legends(&blocks, along_x);
        let offsets = along.into_iter().map(|o| if along_x { (o, 0.0) } else { (0.0, o) }).collect();
        Ok(LegendStack { offsets, size, base_block })
    }

    /// `base` stacked with these legends: its box grows to the stack's.
    pub(crate) fn stack_onto(
        &self,
        mut base: crate::families::legend::VegaLegend,
        cfg: &LegendConfig,
        fonts: &FontBook,
        unit: (f64, f64),
    ) -> Result<crate::families::legend::VegaLegend, String> {
        let own = (base.box_w, base.title_band + base.grid.box_h);
        let stack = self.stack(own, cfg, fonts, unit)?;
        base.box_w = stack.size.0;
        base.stack = Some(stack);
        Ok(base)
    }

    /// The numeric legends stacked onto `base`, from its origin `(x, y)`.
    pub(crate) fn nodes_after(
        &self,
        base: &crate::families::legend::VegaLegend,
        cfg: &LegendConfig,
        fonts: &FontBook,
        unit: (f64, f64),
        (x, y): (f64, f64),
    ) -> Result<Vec<Node>, String> {
        let stack = base.stack.as_ref().expect("a legend stacked with numeric legends carries its stack");
        let mut nodes = Vec::new();
        for (index, (dx, dy)) in stack.offsets.iter().skip(usize::from(stack.base_block)).enumerate() {
            nodes.extend(self.part_nodes(index, cfg, fonts, unit, (x + dx, y + dy))?);
        }
        Ok(nodes)
    }
}

impl NumericPart<'_> {
    fn symbol_legend(
        &self,
        cfg: &LegendConfig,
        fonts: &FontBook,
    ) -> Result<(crate::families::legend::VegaLegend, Vec<(String, String)>), String> {
        let ladder = self.color.symbol_ladder(self.format)?;
        let entries: Vec<_> = ladder
            .iter()
            .map(|(label, fill)| crate::families::legend::LegendEntry {
                label,
                fill,
                symbol: Some(crate::families::legend::LegendSymbol::Circle),
                series_key: None,
                recede: None,
            })
            .collect();
        let legend = crate::families::legend::vega_legend(&entries, false, &self.title, cfg, fonts)?;
        Ok((legend, ladder))
    }
}

/// A `<linearGradient>` in `<defs>`: vertical (y1=1 bottom, y2=0 top) for a
/// legend beside the plot, horizontal (x1=0 left, x2=1 right) for one above
/// or below it. `stops` are `(offset, color)` as printed.
fn gradient_def(id: &str, vertical: bool, stops: Vec<(String, String)>) -> Node {
    let (x2, y1, y2) = if vertical { ("0", "1", "0") } else { ("1", "0", "0") };
    let attrs = Attrs::new().set("id", id).set("x1", "0").set("x2", x2).set("y1", y1).set("y2", y2);
    let stops = stops
        .into_iter()
        .map(|(offset, color)| Node::Stop(Attrs::new().set("offset", offset).set("stop-color", color)))
        .collect();
    Node::Defs(vec![Node::LinearGradient { attrs, stops }])
}

/// The gradient of a continuous legend: a stop at each end and at each of the
/// scale's 15 ticks, each in the scale's own color.
fn scale_gradient_def(
    id: &str,
    (min, max): (f64, f64),
    ticks: &[f64],
    color: impl Fn(f64) -> String,
    vertical: bool,
) -> Node {
    let lead = ticks.first().is_none_or(|&t| t != min).then_some(min);
    let tail = ticks.last().is_none_or(|&t| t != max).then_some(max);
    let stops = lead
        .into_iter()
        .chain(ticks.iter().copied())
        .chain(tail)
        .map(|v| (((v - min) / (max - min)).to_string(), color(v)))
        .collect();
    gradient_def(id, vertical, stops)
}

/// The gradient of a legend over a one-value domain, which has no ticks to
/// sample: Vega runs its whole ramp across the bar in 20 even steps.
fn ramp_gradient_def(id: &str, vertical: bool, color_at: impl Fn(f64) -> String) -> Node {
    let stops = (0..=20)
        .map(|step| {
            let t = f64::from(step) / 20.0;
            (t.to_string(), color_at(t))
        })
        .collect();
    gradient_def(id, vertical, stops)
}

/// The gradient of a continuous `color` sampled at `ticks`; a one-value domain
/// has none to sample, so its whole ramp runs across the bar.
pub(crate) fn continuous_gradient_def(id: &str, color: &ContinuousColor, ticks: &[f64], vertical: bool) -> Node {
    if color.lo == color.hi || color.lo.is_nan() || color.hi.is_nan() {
        ramp_gradient_def(id, vertical, |t| color.fill_at(t))
    } else {
        scale_gradient_def(id, (color.lo, color.hi), ticks, |v| color.fill(v), vertical)
    }
}

/// The gradient of resolved `stops`, evenly spaced along the bar.
pub(crate) fn stops_gradient_def(id: &str, stops: &[String], vertical: bool) -> Node {
    let n = stops.len();
    let stops = stops
        .iter()
        .enumerate()
        .map(|(i, stop)| {
            let offset = if n == 1 { 0.0 } else { i as f64 / (n - 1) as f64 };
            (format!("{offset:.4}"), stop.clone())
        })
        .collect();
    gradient_def(id, vertical, stops)
}

/// A vertical gradient legend: title, color bar (min at the bottom), and the
/// labeled values beside it.
pub(crate) struct VerticalGradient<'a> {
    pub gradient_id: &'a str,
    pub title: &'a str,
    pub range: (f64, f64),
    pub values: &'a [f64],
    pub labels: &'a [String],
    pub length: f64,
}

impl VerticalGradient<'_> {
    /// The legend's nodes with the group's top-left at `(x, y)`; the bar's
    /// gradient def is the caller's.
    pub(crate) fn nodes(&self, cfg: &LegendConfig, x: f64, y: f64) -> Result<Vec<Node>, String> {
        let label_font = &cfg.label.font;
        let label_size = label_font.size_or(11.0);
        let label_family = label_font.family_or("sans-serif");
        let label_offset = cfg.label.padding.ok_or("legend.label.padding absent (theme required)")?;
        let title_size = cfg.title.font.size_or(12.0);
        let mut nodes = Vec::new();
        let mut bar_y = y;
        if !self.title.is_empty() {
            nodes.push(Node::text(
                Attrs::new()
                    .set("x", format!("{x:.2}"))
                    .set("y", format!("{:.2}", y + baseline_shift("top", title_size)))
                    .set("text-anchor", "start")
                    .vega("font-family", cfg.title.font.family_or("sans-serif"))
                    .set("font-size", title_size.to_string())
                    .set("font-weight", cfg.title.font.weight_str())
                    .set("fill", cfg.title.font.color.as_deref().unwrap_or("#313233")),
                self.title,
            ));
            bar_y += title_size + VEGA_LEGEND_TITLE_PADDING;
        }
        nodes.push(Node::Rect(
            Attrs::new()
                .set("x", format!("{x:.2}"))
                .set("y", format!("{bar_y:.2}"))
                .set("width", VEGA_GRADIENT_THICKNESS.to_string())
                .set("height", self.length.to_string())
                .set("fill", format!("url(#{})", self.gradient_id)),
        ));
        let label_color = label_font.color.as_deref().unwrap_or("#626366");
        let label_weight = label_font.weight_str();
        let label_x = x + VEGA_GRADIENT_THICKNESS + label_offset;
        let (min, max) = self.range;
        // A label at the bar's bottom end tucks inside as `bottom`, one at the
        // top end as `top`, every other `middle`. A date `NaN` has no
        // position: Vega draws it at the group's origin.
        let placed: Vec<(&str, f64)> = self
            .values
            .iter()
            .map(|v| {
                let t = unit_position(*v, min, max);
                let baseline = if t <= 0.0 {
                    "bottom"
                } else if t >= 1.0 {
                    "top"
                } else {
                    "middle"
                };
                (baseline, bar_y + if t.is_nan() { 0.0 } else { self.length * (1.0 - t) })
            })
            .collect();
        // Every label starts at one x, so only the vertical gaps decide an overlap.
        let ink: Vec<vega_fit::Bounds> = placed
            .iter()
            .map(|&(baseline, y)| {
                let top = match baseline {
                    "bottom" => y - label_size,
                    "top" => y,
                    _ => y - label_size / 2.0,
                };
                vega_fit::Bounds::new(label_x, top, label_x + 1.0, top + label_size)
            })
            .collect();
        let visible = vega_fit::parity_visible(&ink, 0.0);
        let labels = self
            .labels
            .iter()
            .zip(&placed)
            .zip(visible)
            .map(|((text, &(baseline, y)), shown)| {
                Node::text(
                    Attrs::new()
                        .set("x", format!("{label_x:.2}"))
                        .set("y", format!("{:.2}", y + baseline_shift(baseline, label_size)))
                        .set("text-anchor", "start")
                        .vega("font-family", label_family)
                        .set("font-size", label_size.to_string())
                        .set("font-weight", label_weight.clone())
                        .set("fill", label_color)
                        .set_if(!shown, "opacity", "0"),
                    text.as_str(),
                )
            })
            .collect();
        nodes.push(Node::group(contract::legend_label_attrs(None), labels));
        Ok(nodes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A symbol legend over a continuous scale labels each tick at the precision its step
    /// implies, and colors it as the scale does.
    #[test]
    fn a_symbol_legend_lists_the_ticks_of_the_scale() {
        let color = ContinuousColor::blues(1.0, 4.0);
        let ladder = color.symbol_ladder(None).unwrap();
        let labels: Vec<&str> = ladder.iter().map(|(label, _)| label.as_str()).collect();
        assert_eq!(labels, ["1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0"]);
        assert_eq!(ladder[0].1, color.fill(1.0));
        assert_eq!(ladder[6].1, color.fill(4.0));
    }

    #[test]
    fn a_number_prints_as_javascript_spells_it() {
        assert_eq!(js_number_string(5.0), "5");
        assert_eq!(js_number_string(-0.30000000000000004), "-0.30000000000000004");
        assert_eq!(js_number_string(0.000012345), "0.000012345");
        assert_eq!(js_number_string(1e21), "1e+21");
        assert_eq!(js_number_string(1.5e-7), "1.5e-7");
        assert_eq!(js_number_string(123456789012.0), "123456789012");
    }

    /// Oracle: production's compiled histogram `color: w` over `w` in 2.5..20,
    /// `ladder/histogram/15`: the blues ramp end to end.
    #[test]
    fn a_blues_scale_runs_the_ramp_over_the_extent() {
        let scale = ContinuousColor::blues(2.5, 20.0);
        assert_eq!(scale.fill(2.5), "rgb(207, 225, 242)");
        assert_eq!(scale.fill(20.0), "rgb(10, 74, 144)");
        assert_eq!(scale.fill(10.0), "rgb(109, 174, 213)");
    }

    /// d3 normalizes a zero-width domain to 0.5.
    #[test]
    fn a_one_value_domain_takes_the_middle_color() {
        assert_eq!(ContinuousColor::blues(5.0, 5.0).fill(5.0), "rgb(91, 163, 207)");
        assert_eq!(ContinuousColor::dates(&BLUES, 19000.0, 19000.0, false).fill(19000.0), "rgb(96, 161, 206)");
    }

    /// Oracle: a production histogram dated 2024-01-01 .. 2024-06-01 (a time
    /// scale over `blues`, ends 134/188/220 and 56/135/192).
    #[test]
    fn a_date_scale_mixes_two_samples_of_blues_in_hcl() {
        let scale = ContinuousColor::dates(&BLUES, 0.0, 152.0, false);
        assert_eq!(scale.fill(0.0), "rgb(134, 188, 220)");
        assert_eq!(scale.fill(152.0), "rgb(56, 135, 192)");
        assert_eq!(scale.fill(31.0), "rgb(119, 177, 214)");
    }

    /// Oracle: a production histogram over `w` in 2.5..20 with
    /// `style.color.gradient.palette: ['#ffffcc', '#800026']`.
    #[test]
    fn an_authored_palette_mixes_in_hcl() {
        let scale = ContinuousColor::stops(vec![(255, 255, 204), (128, 0, 38)], 2.5, 20.0);
        assert_eq!(scale.fill(2.5), "rgb(255, 255, 204)");
        assert_eq!(scale.fill(7.5), "rgb(220, 189, 133)");
        assert_eq!(scale.fill(20.0), "rgb(128, 0, 38)");
    }

    fn gradient(palette: serde_json::Value, extra: serde_json::Value) -> ContinuousColor {
        let mut scale = serde_json::json!({"palette": palette, "nice": true, "arm_mode": "asymmetric"});
        for (k, v) in extra.as_object().expect("an object").clone() {
            scale[k] = v;
        }
        let target: ScaleTarget = serde_json::from_value(scale).expect("a scale target");
        ContinuousColor::gradient(&target, Some((2.5, 20.0)), (2.5, 20.0), true).expect("a scale")
    }

    /// An authored `legend.values` token reads as JavaScript's `Number()` does: `0x10` is 16, `inf`
    /// and `1_000` are `NaN`, labeled `NaN`.
    #[test]
    fn a_legend_values_token_reads_as_a_javascript_number() {
        let scale = ContinuousColor::blues(0.0, 100.0);
        let ladder = |token: &str| scale.legend_ladder(200.0, None, Some(&[token.to_string()]));
        assert_eq!(ladder("0x10").expect("hex reads").0, vec![16.0]);
        assert_eq!(ladder(" 12 ").expect("padded reads").0, vec![12.0]);
        for token in ["inf", "nan", "1_000"] {
            let (values, labels) = ladder(token).expect("a non-number reads as NaN");
            assert!(values[0].is_nan() && labels == ["NaN"], "{token}");
        }
    }

    /// Oracle: Vega's `viridis` over `[0, 100000]`. d3 multiplies by the domain's reciprocal, so
    /// 75000 sits at 0.7500000000000001 and rounds the blue tie down where dividing rounds it up.
    #[test]
    fn a_sequential_scale_multiplies_by_the_reciprocal_of_its_domain() {
        let scale = gradient(serde_json::json!("viridis"), serde_json::json!({"min": 0.0, "max": 100000.0}));
        assert_eq!(scale.fill(75000.0), "rgb(93, 201, 98)");
        assert_eq!(scale.fill(74999.99999999999), "rgb(93, 200, 99)");
    }

    fn fills(scale: &ContinuousColor, ws: &[f64]) -> Vec<String> {
        ws.iter().map(|&w| scale.fill(w)).collect()
    }

    /// Oracle: production's `histogram` over `w` in 2.5..20, `ladder/histogram/79`
    /// to `81`: a Vega scheme name, sampled by position, re-windowed about an
    /// asymmetric pivot, or halved about a symmetric one.
    #[test]
    fn a_vega_scheme_runs_plain_windowed_or_halved() {
        let plain = gradient(serde_json::json!("viridis"), serde_json::json!({"nice": false}));
        assert_eq!(fills(&plain, &[2.5, 10.0, 20.0]), ["rgb(68, 1, 84)", "rgb(39, 128, 142)", "rgb(253, 231, 37)"]);
        let windowed = gradient(serde_json::json!("viridis"), serde_json::json!({"hinge": 7.5}));
        assert_eq!(fills(&windowed, &[2.5, 7.5, 20.0]), ["rgb(53, 96, 141)", "rgb(33, 145, 141)", "rgb(253, 231, 37)"]);
        let halved = gradient(serde_json::json!("viridis"), serde_json::json!({"hinge": 7.5, "arm_mode": "symmetric"}));
        assert_eq!(fills(&halved, &[2.5, 5.0, 12.5]), ["rgb(68, 58, 131)", "rgb(49, 104, 142)", "rgb(67, 191, 113)"]);
    }

    /// Oracle: `ladder/histogram/77` and `78`: a categorical scheme's array is a
    /// range list mixed in HCL, under either pivot.
    #[test]
    fn a_categorical_scheme_is_a_range_list_under_a_pivot() {
        let symmetric =
            gradient(serde_json::json!("category10"), serde_json::json!({"hinge": 7.5, "arm_mode": "symmetric"}));
        assert_eq!(fills(&symmetric, &[2.5, 5.0, 20.0]), ["rgb(160, 155, 0)", "rgb(214, 39, 40)", "rgb(23, 190, 207)"]);
        let asymmetric = gradient(serde_json::json!("category10"), serde_json::json!({"hinge": 7.5}));
        assert_eq!(fills(&asymmetric, &[2.5, 5.0, 20.0]), ["rgb(233, 137, 0)", "rgb(110, 144, 0)", "rgb(23, 190, 207)"]);
    }

    /// Oracle: `ladder/histogram/42` to `45`: authored stops about a pivot, each
    /// arm its own width or the wider one, an even count gaining a center stop,
    /// a pivot beyond the data extending the domain to hold it.
    #[test]
    fn authored_stops_diverge_about_the_pivot() {
        let three = serde_json::json!(["#ffffcc", "#41b6c4", "#0c2c84"]);
        let asymmetric = gradient(three.clone(), serde_json::json!({"hinge": 7.5}));
        assert_eq!(fills(&asymmetric, &[2.5, 7.5, 12.5, 20.0]), ["rgb(137, 216, 186)", "rgb(65, 182, 196)", "rgb(0, 129, 182)", "rgb(12, 44, 132)"]);
        let symmetric = gradient(three.clone(), serde_json::json!({"hinge": 7.5, "arm_mode": "symmetric"}));
        assert_eq!(fills(&symmetric, &[2.5, 5.0]), ["rgb(190, 234, 186)", "rgb(124, 210, 187)"]);
        let four = gradient(serde_json::json!(["#ffffcc", "#a1dab4", "#41b6c4", "#0c2c84"]), serde_json::json!({"hinge": 7.5}));
        assert_eq!(fills(&four, &[2.5, 12.5, 15.0]), ["rgb(151, 215, 180)", "rgb(73, 186, 194)", "rgb(0, 156, 190)"]);
        let beyond = gradient(three, serde_json::json!({"hinge": 30.0}));
        assert_eq!(fills(&beyond, &[2.5, 20.0]), ["rgb(239, 250, 197)", "rgb(124, 210, 187)"]);
    }

    /// Oracle: `ladder/histogram/47`: authored bounds replace the data extent.
    #[test]
    fn authored_bounds_replace_the_data_extent() {
        let scale = gradient(serde_json::json!(["#ffffcc", "#800026"]), serde_json::json!({"min": 0.0, "max": 20.0}));
        assert_eq!(fills(&scale, &[2.5, 10.0]), ["rgb(239, 226, 171)", "rgb(195, 139, 91)"]);
    }

    /// A pivot on the domain's top bound leaves its arm zero-width, which d3
    /// paints as the segment's middle rather than a NaN black.
    #[test]
    fn a_pivot_on_the_upper_bound_paints_no_black() {
        let scale = gradient(
            serde_json::json!(["#ffffcc", "#41b6c4", "#0c2c84"]),
            serde_json::json!({"hinge": 100_000.0, "arm_mode": "symmetric"}),
        );
        assert_ne!(scale.fill(100_000.0), "rgb(0, 0, 0)");
    }

    /// Oracle: `ladder/histogram/82` to `/87`. A min above the data collapses the
    /// light arm onto the pivot; a min above the max collapses the whole domain to
    /// one stop, which d3 paints on every value, legend included.
    #[test]
    fn inverted_bounds_beside_a_hinge_follow_d3() {
        let palette = serde_json::json!(["#ffffcc", "#41b6c4", "#0c2c84"]);
        let above_data = gradient(palette.clone(), serde_json::json!({"min": 30.0, "hinge": 7.5}));
        assert_eq!(fills(&above_data, &[2.5, 7.5, 20.0]), ["rgb(65, 182, 196)", "rgb(65, 182, 196)", "rgb(12, 44, 132)"]);
        let above_max = gradient(palette, serde_json::json!({"min": 20.0, "max": 0.0, "hinge": 7.5}));
        assert_eq!(fills(&above_max, &[2.5, 7.5, 20.0]), ["rgb(65, 182, 196)"; 3]);
        assert_eq!(above_max.fill_at(0.0), above_max.fill_at(1.0));
    }

    /// A scheme palette over collapsed bounds paints one color at every position.
    #[test]
    fn a_collapsed_pivoted_scheme_paints_one_color() {
        for arm_mode in ["asymmetric", "symmetric"] {
            let scale = gradient(
                serde_json::json!("viridis"),
                serde_json::json!({"min": 20.0, "max": 0.0, "hinge": 7.5, "arm_mode": arm_mode}),
            );
            let at = |t| scale.fill_at(t);
            assert_eq!((at(0.0), at(0.5)), (at(1.0), at(1.0)), "{arm_mode}");
            assert_eq!(scale.fill(2.5), at(0.0), "{arm_mode}");
        }
    }

    #[test]
    fn a_hingeless_inverted_gradient_runs_the_ramp_backwards() {
        let scale = gradient(serde_json::json!(["#ffffcc", "#800026"]), serde_json::json!({"min": 20.0, "max": 0.0}));
        assert_eq!(scale.fill(20.0), "rgb(255, 255, 204)");
        assert_eq!(scale.fill(0.0), "rgb(128, 0, 38)");
    }

    #[test]
    fn a_one_color_ramp_paints_nothing_and_an_empty_one_paints_black() {
        let one = gradient(serde_json::json!(["#c8281e"]), serde_json::json!({}));
        assert!(!one.paints());
        let empty = gradient(serde_json::json!([]), serde_json::json!({}));
        assert_eq!(empty.fill(10.0), "rgb(0, 0, 0)");
    }

    #[test]
    fn a_palette_color_reads_as_hex_rgb_or_a_keyword() {
        let scale = gradient(serde_json::json!(["#f00", "blue", "rgb(0, 255, 0)"]), serde_json::json!({"nice": false}));
        assert_eq!(scale.fill(2.5), "rgb(255, 0, 0)");
        assert_eq!(scale.fill(20.0), "rgb(0, 255, 0)");
        // An unreadable color is d3's undefined color: it takes its neighbor's.
        let bad = gradient(serde_json::json!(["notacolor", "red"]), serde_json::json!({"nice": false}));
        assert_eq!(bad.fill(2.5), "rgb(255, 0, 0)");
        assert_eq!(bad.fill(20.0), "rgb(255, 0, 0)");
    }

    /// A diverging scale's bounds: both authored win; neither authored takes the
    /// nice-widened extent when `nice`; otherwise the raw extent.
    #[test]
    fn a_diverging_scale_nice_widens_only_an_unbounded_extent() {
        let target = |nice: bool| -> ScaleTarget {
            serde_json::from_value(serde_json::json!({"palette": ["#ffffcc", "#41b6c4", "#0c2c84"], "nice": nice, "arm_mode": "asymmetric", "hinge": 0.0})).unwrap()
        };
        assert_eq!(diverging_bounds(&target(true), Some((2.5, 17.0)), true), Some((0.0, 20.0)));
        assert_eq!(diverging_bounds(&target(false), Some((2.5, 17.0)), false), Some((2.5, 17.0)));
        let mut bounded = target(true);
        bounded.min = Some(1.0);
        bounded.max = Some(9.0);
        assert_eq!(diverging_bounds(&bounded, Some((2.5, 17.0)), true), Some((1.0, 9.0)));
        assert_eq!(diverging_bounds(&target(true), None, true), None);
    }

    fn sequential_target(nice: Option<bool>, min: Option<f64>, max: Option<f64>) -> ScaleTarget {
        let mut target: ScaleTarget = serde_json::from_value(
            serde_json::json!({"palette": ["#000000", "#ffffff"], "nice": nice, "arm_mode": "asymmetric"}),
        )
        .unwrap();
        (target.min, target.max) = (min, max);
        target
    }

    /// Every gradient consumer widens an unbounded extent through `nice_domain`: 12-88 reads 0-100, and
    /// `nice: false` or any authored bound keeps the extent exactly.
    #[test]
    fn nice_domain_widens_only_an_unbounded_extent_to_round_ends() {
        assert_eq!(nice_domain(None, None, true, (12.0, 88.0)), (0.0, 100.0));
        assert_eq!(nice_domain(None, None, false, (12.0, 88.0)), (12.0, 88.0));
        assert_eq!(nice_domain(Some(5.0), None, true, (12.0, 88.0)), (12.0, 88.0));
        assert_eq!(nice_domain(None, Some(90.0), true, (12.0, 88.0)), (12.0, 88.0));
        assert_eq!(nice_domain(None, None, true, (7.0, 7.0)), (7.0, 7.0));
        let (lo, hi) = nice_domain(None, None, true, (f64::NAN, f64::NAN));
        assert!(lo.is_nan() && hi.is_nan(), "an empty extent stays empty");
    }

    /// An author's `nice` always wins; unset follows the legend: a drawn legend widens, none keeps the extent.
    #[test]
    fn a_sequential_channel_gradient_nices_by_author_else_by_legend() {
        let paint = |nice, legend, min, max| {
            let scale =
                ContinuousColor::gradient(&sequential_target(nice, min, max), Some((12.0, 88.0)), (12.0, 88.0), legend).unwrap();
            (scale.lo, scale.hi, scale.fill(12.0))
        };
        assert_eq!(paint(None, true, None, None), (0.0, 100.0, "rgb(31, 31, 31)".to_string()));
        assert_eq!(paint(None, false, None, None).0, 12.0);
        assert_eq!(paint(Some(true), false, None, None).0, 0.0);
        assert_eq!(paint(Some(false), true, None, None).0, 12.0);
        assert_eq!(paint(Some(true), true, Some(12.0), None).0, 12.0);
    }

    /// Breakpoints cut to the bounds keep one HCL-blended stop at a bound a
    /// dropped breakpoint crossed.
    #[test]
    fn breakpoints_cut_to_the_bounds_gain_a_blended_edge_stop() {
        let stop = |rgb| {
            let color = Color::opaque(rgb);
            Stop { vega: color, python: Some(color) }
        };
        let palette: Vec<Color> = [(255, 255, 204), (65, 182, 196), (12, 44, 132)].into_iter().map(Color::opaque).collect();
        let stops: Vec<Stop> = [(255, 255, 204), (65, 182, 196), (12, 44, 132)].into_iter().map(stop).collect();
        let (domain, colors) = breakpoints_truncated(stops, 10.0, (4.0, 14.0), (10.0, 10.0)).unwrap();
        assert_eq!(domain, [4.0, 10.0, 14.0]);
        assert_eq!(colors[1], Color::opaque((65, 182, 196)));
        assert_ne!(colors[0], palette[0], "the dropped first breakpoint blends into the edge");
        assert!(breakpoints_truncated(vec![stop((0, 0, 0))], 0.0, (0.0, 1.0), (1.0, 1.0)).is_err());
    }

    /// A blended stop keeps the alpha production's `interpolate_hcl` blends, printed to four places.
    #[test]
    fn a_blended_translucent_stop_keeps_its_alpha() {
        let stop = |text: &str| Stop { vega: d3rs::color::parse_color(text).unwrap_or(Color::UNDEFINED), python: python_color(text) };
        let mixed = blended(stop("#ff000080"), stop("#0000ff"), 0.5).unwrap().vega;
        assert!((mixed.a - 0.7510).abs() < 1e-9, "{}", mixed.a);
        let opaque = blended(stop("#ff0000"), stop("#0000ff"), 0.5).unwrap().vega;
        assert_eq!(opaque.a, 1.0);
        assert!(blended(stop("notacolor"), stop("red"), 0.5).is_err());
        assert!(python_color("hsl(0 80% 50% / 50%)").is_some_and(|c| c.a == 0.5));
    }

    /// The gradient legends in every orientation, a titled one with
    /// characters that need escaping, a one-value domain, a date legend and a
    /// resolved-stops bar, as bytes: their markup is pinned across the move
    /// from string emission to nodes.
    fn gradient_legend_markup() -> String {
        let fonts = {
            let manifest = env!("CARGO_MANIFEST_DIR");
            FontBook::load_dir(&format!("{manifest}/../../../src/dbt_charts/core/render/fonts")).unwrap()
        };
        let cfg = |edge: &str, direction: &str| -> LegendConfig {
            serde_json::from_value(serde_json::json!({
                "visible": true, "position": {"edge": edge, "align": "start", "overlay": false},
                "direction": direction,
                "label": {"font": {"family": "'Q', \"R\"", "size": 11.0}, "padding": 8.0},
                "title": {"font": {"family": "'Inter Variable', Inter, sans-serif"}, "visible": true},
            }))
            .unwrap()
        };
        let mut svg = String::new();
        let scale = ContinuousColor::blues(2.5, 20.0);
        for (edge, direction, title) in [("right", "vertical", "Ti<&'\"tle"), ("top", "horizontal", "Title"), ("top", "horizontal", "")] {
            let cfg = cfg(edge, direction);
            GradientAt::paint(&cfg, None, &scale, ("g<'1", title), &fonts, (300.0, 200.0), (&mut svg, 5.0, 7.5)).unwrap();
        }
        let flat = ContinuousColor::blues(5.0, 5.0);
        GradientAt::paint(&cfg("right", "vertical"), None, &flat, ("flat", "T"), &fonts, (300.0, 200.0), (&mut svg, 1.0, 2.0)).unwrap();
        let dates = ContinuousColor::dates(&BLUES, 0.0, 152.0, false);
        GradientAt::paint(&cfg("right", "vertical"), None, &dates, ("dates", "D"), &fonts, (300.0, 200.0), (&mut svg, 1.0, 2.0)).unwrap();
        let stops: Vec<String> = ["#ffffcc", "#41b6c4", "#0c2c84"].map(String::from).to_vec();
        let labels = ["0".to_string(), "5".to_string()];
        for (edge, direction) in [("right", "vertical"), ("top", "horizontal")] {
            let cfg = cfg(edge, direction);
            let legend = GradientLegend::over(&cfg, GradientPaint::Stops(&stops), "st<ops", (0.0, 5.0, &[0.0, 5.0]), &labels, &fonts).unwrap();
            svg.push_str(&print(&legend.nodes(3.0, 4.0, 120.0)));
        }
        let vertical = cfg("right", "vertical");
        let bar = VerticalGradient {
            gradient_id: "vg",
            title: "VG",
            range: (0.0, 10.0),
            values: &[0.0, 2.5, 10.0, f64::NAN],
            labels: &["a".to_string(), "b".to_string(), "c".to_string(), "NaN".to_string()],
            length: 90.0,
        };
        svg.push_str(&print(&bar.nodes(&vertical, 6.0, 8.0).unwrap()));
        for (min, max) in [(0.0, 152.0), (5.0, 5.0)] {
            svg.push_str(&print(&[crate::families::heatmap::date_gradient_def("dg", min, max, false).unwrap()]));
        }
        // Labels too close to all be drawn hide every other one.
        let crowded = VerticalGradient {
            gradient_id: "vh",
            title: "",
            range: (0.0, 10.0),
            values: &[4.9, 5.0, 5.1, 5.2],
            labels: &["a".to_string(), "b".to_string(), "c".to_string(), "d".to_string()],
            length: 20.0,
        };
        svg.push_str(&print(&crowded.nodes(&vertical, 0.0, 0.0).unwrap()));
        let horizontal = cfg("top", "horizontal");
        let labels: Vec<String> = (0..6).map(|i| format!("{i}000000")).collect();
        let values = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0];
        let legend = GradientLegend::over(&horizontal, GradientPaint::Stops(&stops), "hh", (0.0, 5.0, &values), &labels, &fonts).unwrap();
        svg.push_str(&print(&legend.nodes(0.0, 0.0, 60.0)));
        svg
    }

    #[test]
    fn gradient_legend_markup_is_pinned() {
        let _en = crate::time_locale::en_us_scope();
        assert_eq!(gradient_legend_markup(), include_str!("gradient_legends_golden.svg"));
    }
}
