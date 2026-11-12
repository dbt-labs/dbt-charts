//! The authoring law of a format spec: where `d3_format.parse` refuses a number spec, and which
//! strftime directives a spec names (`text/format_d3.py`). No formatting happens here.

use crate::invariant::at;

/// Why `d3_format.parse` refuses a spec; `position` indexes the spec's characters.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// A `.` with no digit after it.
    Precision { position: usize },
    /// A type letter outside the d3 grammar.
    Type { position: usize, found: char },
    /// Characters left over after the type.
    Trailing { position: usize, rest: String },
}

impl ParseError {
    pub fn position(&self) -> usize {
        match self {
            Self::Precision { position }
            | Self::Type { position, .. }
            | Self::Trailing { position, .. } => *position,
        }
    }
}

/// `d3_format.parse` over `[[fill]align][sign][symbol][0][width][,][.precision][~][type]`: the
/// type letter the spec names, if any.
pub fn parse(spec: &str) -> Result<Option<char>, ParseError> {
    let s: Vec<char> = spec.chars().collect();
    let peek = |i: usize| s.get(i).copied();
    let is_align = |c: Option<char>| c.is_some_and(|c| "><^=".contains(c));
    let mut i = if s.len() >= 2 && is_align(peek(1)) {
        2
    } else {
        usize::from(is_align(peek(0)))
    };
    let skip = |set: &str, i: &mut usize| {
        if peek(*i).is_some_and(|c| set.contains(c)) {
            *i += 1;
        }
    };
    skip("-+( ", &mut i);
    skip("$#", &mut i);
    skip("0", &mut i);
    while peek(i).is_some_and(|c| c.is_ascii_digit()) {
        i += 1;
    }
    skip(",", &mut i);
    if peek(i) == Some('.') {
        let dot = i;
        i += 1;
        let start = i;
        while peek(i).is_some_and(|c| c.is_ascii_digit()) {
            i += 1;
        }
        if i == start {
            return Err(ParseError::Precision { position: dot });
        }
    }
    skip("~", &mut i);
    let kind = match peek(i) {
        Some(c) if c.is_ascii_alphabetic() || c == '%' => Some(c),
        Some(found) => return Err(ParseError::Type { position: i, found }),
        None => None,
    };
    i += usize::from(kind.is_some());
    match i < s.len() {
        true => Err(ParseError::Trailing {
            position: i,
            rest: at(&s, i..).iter().collect(),
        }),
        false => Ok(kind),
    }
}

/// `is_time_format`: a d3-time-format directive (`%<letter>`, optionally `%-d`/`%_d`/`%0d`) once
/// `%%` escapes are removed.
pub fn is_time_format(spec: &str) -> bool {
    let spec = spec.replace("%%", "");
    let b = spec.as_bytes();
    (0..b.len()).any(|i| {
        *at(b, i) == b'%' && {
            let j = i + 1 + usize::from(matches!(b.get(i + 1), Some(b'-' | b'_' | b'0')));
            b.get(j).is_some_and(u8::is_ascii_alphabetic)
        }
    })
}

/// `GRAIN_NAMES`: the engine's own per-grain date axis labels, which a board must redefine into a
/// strftime spec before a slot can ask for one.
pub const GRAIN_NAMES: [&str; 4] = ["date_month", "date_quarter", "date_week", "date_year"];
/// `PREDEFINED_TEXT_NAMES`: names with no spec, for text a surface may link.
pub const TEXT_NAMES: [&str; 2] = ["email", "url"];

/// `PREDEFINED_NUMBER_NAMES`.
pub fn is_number_name(name: &str) -> bool {
    crate::format::predefined_spec(name).is_some() || crate::predefined_formats::PREDEFINED_NATIVE.contains(&name)
}

/// `PREDEFINED_TIME_NAMES`.
pub fn is_time_name(name: &str) -> bool {
    crate::format::predefined_time_spec(name).is_some() || GRAIN_NAMES.contains(&name)
}

/// `ALL_PREDEFINED_NAMES`: every name the engine owns.
pub fn is_engine_name(name: &str) -> bool {
    is_number_name(name) || is_time_name(name) || TEXT_NAMES.contains(&name)
}

/// The `field_path` Python's `resolve_format` names for a slot it cannot place.
pub const UNPLACED_SLOT: &str = "a format";

/// A grain name the board did not redefine has no spec to resolve to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GrainNotRedefined;

/// `compile/format.py::resolve_format`: a format name to its d3 spec ("" for none). `alias` is the
/// board's `style.formats`: `None` for a name it does not define, else the spec its entry carries
/// (`None` for a mapping with no `spec`). An entry wins: its preset's spec, else its literal
/// target, so a board that redefines an engine name replaces the engine's spec. Then a predefined
/// number name resolves to its house spec (native members pass through as the bare name), a time
/// name to its strftime spec, and anything else passes through.
pub fn resolve_spec<'a>(
    name: &str,
    alias: &impl Fn(&str) -> Option<Option<&'a str>>,
) -> Result<String, GrainNotRedefined> {
    let entry = alias(name);
    let preset = entry.flatten().filter(|target| is_engine_name(target));
    // Through the board's own table, so `revenue: currency` prints what the board made of
    // `currency`; a name aliased to itself is the engine's own.
    if let Some(preset) = preset.filter(|preset| *preset != name) {
        return resolve_spec(preset, alias);
    }
    if preset.is_none() {
        if let Some(spec) = entry {
            return Ok(spec.unwrap_or_default().to_string());
        }
    }
    if is_number_name(name) {
        return Ok(crate::format::predefined_spec(name).map_or(name, |spec| spec.d3).to_string());
    }
    if GRAIN_NAMES.contains(&name) {
        return Err(GrainNotRedefined);
    }
    if let Some(spec) = crate::format::predefined_time_spec(name) {
        return Ok(spec.to_string());
    }
    Ok(name.to_string())
}

/// `declared_format`: the format name a query's `formats:` (`entry`) gives every one of `columns`.
/// `None` when any column has no entry or the entries differ: a destination painting several
/// columns with one format has no single column to speak for them, so it keeps its own default
/// rather than guess.
pub fn declared_format<'a>(entry: impl Fn(&str) -> Option<&'a str>, columns: &[&str]) -> Option<&'a str> {
    let mut entries = columns.iter().map(|column| entry(column));
    let first = entries.next()??;
    entries.all(|e| e == Some(first)).then_some(first)
}

/// `declared_column_format`: the format name the chart's query declares for `columns`, when it is
/// of the asked kind (a time format for `time`, else a number format). A number format on a date
/// axis would paint garbage, and a time format has nothing to say about a number, so the other
/// kind is no format at all. A text name is of neither kind: it comes back only for a destination
/// that links text (`text`), and is no format at all to any other.
pub fn declared_column_format<'a>(
    entry: impl Fn(&str) -> Option<&'a str>,
    columns: &[&str],
    alias: &impl Fn(&str) -> Option<Option<&'a str>>,
    time: bool,
    text: bool,
) -> Result<Option<&'a str>, GrainNotRedefined> {
    let Some(name) = declared_format(entry, columns) else {
        return Ok(None);
    };
    let resolved = resolve_spec(name, alias)?;
    if TEXT_NAMES.contains(&resolved.as_str()) {
        return Ok(text.then_some(name));
    }
    Ok((is_time_format(&resolved) == time).then_some(name))
}

/// `GRAIN_STRFTIME`: the strftime spec an error offers in place of an engine grain name.
pub fn grain_strftime(grain_name: &str) -> &'static str {
    match grain_name {
        "date_year" => "%Y",
        "date_week" => "%-d %b %Y",
        _ => "%b %Y",
    }
}

/// `PYTHON_STRFTIME_DIRECTIVES`: the directives `portable_strftime` paints.
pub const STRFTIME_DIRECTIVES: &str = "aAbBcCdDefFgGhHIjklLmMnpPqQrRsSTuUVwWxXyYzZ";

/// `VEGA_SAFE_TIME_DIRECTIVES`: the directives a Vega-painted slot measures as it paints.
pub const VEGA_SAFE_DIRECTIVES: &str = "aAbBcdeGHIjLmMpqQSuUVwWxXyY";

/// `find_unsupported_directive`: the first `%<letter>` (any character after the optional `-_0`
/// modifier) that `portable_strftime` does not paint.
pub fn find_unsupported_directive(spec: &str) -> Option<char> {
    let s: Vec<char> = spec.replace("%%", "").chars().collect();
    let mut i = 0;
    while i < s.len() {
        if *at(&s, i) != '%' {
            i += 1;
            continue;
        }
        let modifier = s.get(i + 1).is_some_and(|c| "-_0".contains(*c));
        // The regex `%[-_0]?(.)` falls back to reading the modifier as the letter.
        let (letter, used) = match (modifier, s.get(i + 2)) {
            (true, Some(c)) if *c != '\n' => (*c, 3),
            (true, _) => (*at(&s, i + 1), 2),
            (false, _) if s.get(i + 1).is_some_and(|c| *c != '\n') => (*at(&s, i + 1), 2),
            _ => {
                i += 1;
                continue;
            }
        };
        if !STRFTIME_DIRECTIVES.contains(letter) {
            return Some(letter);
        }
        i += used;
    }
    None
}

/// `unsupported_time_directives`: the sorted, distinct letters of `spec`'s directives (`%%`
/// aside) outside [`VEGA_SAFE_DIRECTIVES`].
pub fn vega_unsupported_directives(spec: &str) -> Vec<char> {
    let s: Vec<char> = spec.chars().collect();
    let mut found = std::collections::BTreeSet::new();
    let mut i = 0;
    while i < s.len() {
        if *at(&s, i) != '%' {
            i += 1;
            continue;
        }
        let modifier = s.get(i + 1).is_some_and(|c| "-_0".contains(*c));
        match s.get(i + 1 + usize::from(modifier)) {
            _ if s.get(i + 1) == Some(&'%') => i += 2,
            Some(c) if c.is_ascii_alphabetic() => {
                if !VEGA_SAFE_DIRECTIVES.contains(*c) {
                    found.insert(*c);
                }
                i += 2 + usize::from(modifier);
            }
            _ => i += 1,
        }
    }
    found.into_iter().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_reports_python_positions() {
        assert_eq!(parse(",.2f"), Ok(Some('f')));
        assert_eq!(parse("$,.0f"), Ok(Some('f')));
        assert_eq!(parse(""), Ok(None));
        assert_eq!(parse("n"), Ok(Some('n')));
        assert_eq!(parse(".f"), Err(ParseError::Precision { position: 0 }));
        assert_eq!(
            parse("percent_1"),
            Err(ParseError::Trailing {
                position: 1,
                rest: "ercent_1".into()
            })
        );
        assert_eq!(
            parse("1_"),
            Err(ParseError::Type {
                position: 1,
                found: '_'
            })
        );
    }

    #[test]
    fn directives() {
        assert!(is_time_format("%b %Y") && !is_time_format("%%Y"));
        assert_eq!(find_unsupported_directive("%Y %K"), Some('K'));
        assert_eq!(find_unsupported_directive("%-d %b"), None);
        assert_eq!(
            vega_unsupported_directives("%Y %H:%M %Z %-Z %%y"),
            vec!['Z']
        );
    }
}
