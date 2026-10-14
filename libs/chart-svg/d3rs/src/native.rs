//! `PREDEFINED_NATIVE`'s formatters (`predefined_formats.py`), shared by the
//! resolve stage's label templates and the renderer's format slots.

/// The house dash a missing value prints.
pub const NULL_DISPLAY: &str = "\u{2014}";

/// Native formatters (`PREDEFINED_NATIVE`, `predefined_formats.py`) bypass d3
/// entirely — the value already IS the caller's unit (whole-number percent,
/// point delta, seconds, bytes). Returns `(number, unit)`; the house minus
/// replaces every `-`.
pub fn predefined_native(name: &str, value: f64) -> Result<Option<(String, String)>, String> {
    let unit = match name {
        "percent_number" | "percent_number_delta" => "%",
        "percentage_points_delta" => " pts",
        "duration" => return duration(value).map(Some),
        "bytes" => {
            return bytes(value, |spec, v| crate::format::format(&crate::format::Locale::en_us(), spec, v)).map(Some)
        }
        _ => return Ok(None),
    };
    let number = if name == "percent_number" {
        format!("{value:.1}")
    } else {
        format!("{value:+.1}")
    };
    Ok(Some((number.replace('-', "\u{2212}"), unit.to_string())))
}

/// `_DURATION_UNITS`: seconds in a unit, largest first.
const DURATION_UNITS: [(u128, &str); 4] = [(86400, "d"), (3600, "h"), (60, "m"), (1, "s")];

/// `_duration` (`predefined_formats.py`): the two largest units of a span in
/// seconds, a zero lower unit dropped (`1h 23m`, `45s`); the fraction rounds
/// half up. Python's `int` is unbounded; past `u128` there is no exact integer
/// to print here, so it refuses rather than print a rounded one.
fn duration(value: f64) -> Result<(String, String), String> {
    if !value.is_finite() {
        return Ok((NULL_DISPLAY.to_string(), String::new()));
    }
    let rounded = (value.abs() + 0.5).floor();
    if rounded >= 2f64.powi(127) {
        return Err(format!("format \"duration\" on {value}: a span too long to print exactly"));
    }
    let seconds = rounded as u128;
    let sign = if value < 0.0 && seconds > 0 { "\u{2212}" } else { "" };
    for pair in DURATION_UNITS.windows(2) {
        let ((size, unit), (lower_size, lower_unit)) = (pair[0], pair[1]);
        if seconds >= size {
            let lower = seconds % size / lower_size;
            let tail = if lower > 0 { format!(" {lower}{lower_unit}") } else { String::new() };
            return Ok((format!("{sign}{}{unit}{tail}", seconds / size), String::new()));
        }
    }
    Ok((format!("{sign}{seconds}s"), String::new()))
}

/// `_bytes` (`predefined_formats.py`): decimal SI (1 kB = 1000 B) to three
/// significant digits, the unit spaced from the number at every magnitude:
/// `950 B`, `1.53 MB`. Under one byte d3's SI would reach for milli, so `r`
/// prints it plainly. `format` is the digits' formatter: en-US for the Python
/// slots (production's own `d3_format`), the board's locale for Vega's.
pub fn bytes(value: f64, format: impl Fn(&str, f64) -> Result<String, String>) -> Result<(String, String), String> {
    if !value.is_finite() {
        return Ok((NULL_DISPLAY.to_string(), String::new()));
    }
    let spec = if value.abs() < 1.0 { ".3~r" } else { ".3~s" };
    let text = format(spec, value)?;
    Ok(match text.chars().next_back().filter(|c| "kMGTPEZY".contains(*c)) {
        Some(prefix) => (text[..text.len() - 1].to_string(), format!(" {prefix}B")),
        None => (text, " B".to_string()),
    })
}
