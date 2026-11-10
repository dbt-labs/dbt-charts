//! The formats law: an authored format to a [`ResolvedFormat`] (d3 spec, affix,
//! notation, and the authored `raw` the house rules key on), the one merge rule that
//! picks a slot's format from its tiers, and the one painter that prints a value
//! through it under the board's number locale ([`locale`]). `compile/format.py`'s
//! `resolve_format`, `resolve_format_parts`, `_merge_tiers` and `column_format`, and
//! `text/format_d3.py`'s `format_d3`, over plain inputs.
//!
//! A format is **house** when its authored name is one of the engine's predefined
//! number names, and **literal** otherwise (a d3 spec, or a name the board
//! redefined): house formats take the engine's rules (narrative register,
//! sub-unit votes, alignment), a literal spec prints its own characters.

pub mod locale;
mod lanes;
mod paint;

pub use lanes::Lanes;

use diagnostic::{diag, Diagnostic};
use indexmap::IndexMap;
use serde::Deserialize;
use std::fmt;
use std::sync::LazyLock;

/// `GRAIN_NAMES`: the engine's own per-grain date axis labels, which a board must
/// redefine into a strftime spec before a slot can ask for one.
const GRAIN_NAMES: [&str; 4] = ["date_month", "date_quarter", "date_week", "date_year"];
/// `PREDEFINED_TEXT_NAMES`: names with no spec, for text a surface may link.
pub const TEXT_NAMES: [&str; 2] = ["email", "url"];
/// `_ENGINE_FORMATS`: the names that bake the analytic register into themselves.
const ANALYTIC_NAMES: [&str; 2] = ["compact", "currency_compact"];

/// The register an SI letter is spelled in.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Notation {
    Analytic,
    Narrative,
}

/// Where a leading sign sits against the prefix: `−US$500` or `US$−500`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SignPlacement {
    AfterPrefix,
    BeforePrefix,
}

/// How a ladder (axis, column, strip) repeats the affix; a per-value painter ignores it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Repeat {
    Anchor,
    Every,
}

impl Notation {
    pub fn as_str(self) -> &'static str {
        match self {
            Notation::Analytic => "analytic",
            Notation::Narrative => "narrative",
        }
    }
}

impl fmt::Display for Notation {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl fmt::Display for SignPlacement {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            SignPlacement::AfterPrefix => "after_prefix",
            SignPlacement::BeforePrefix => "before_prefix",
        })
    }
}

impl fmt::Display for Repeat {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Repeat::Anchor => "anchor",
            Repeat::Every => "every",
        })
    }
}

/// A format as the author wrote it: a bare name or spec ([`AuthoredFormat::named`]), or
/// the `FormatConfig` mapping. An empty spec is unset.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthoredFormat {
    #[serde(default, deserialize_with = "non_empty")]
    pub spec: Option<String>,
    pub prefix: Option<String>,
    pub suffix: Option<String>,
    pub notation: Option<Notation>,
    pub sign_placement: Option<SignPlacement>,
    pub repeat: Option<Repeat>,
}

fn non_empty<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    Ok(Option::<String>::deserialize(d)?.filter(|spec| !spec.is_empty()))
}

impl AuthoredFormat {
    /// A format that is only a name or spec.
    pub fn named(spec: &str) -> Self {
        Self { spec: Some(spec.to_string()).filter(|spec| !spec.is_empty()), ..Self::default() }
    }
}

/// What a `style.formats` alias names: another name or spec, or a mapping.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(untagged)]
pub enum Alias {
    Spec(String),
    Config(AuthoredFormat),
}

impl Alias {
    fn spec(&self) -> Option<&str> {
        match self {
            Alias::Spec(spec) => Some(spec),
            Alias::Config(config) => config.spec.as_deref(),
        }
    }
}

/// The board's `style.formats` aliases (none when the board has no table).
#[derive(Clone, Copy)]
pub struct Formats<'a>(Option<&'a IndexMap<String, Alias>>);

impl<'a> Formats<'a> {
    pub fn new(aliases: Option<&'a IndexMap<String, Alias>>) -> Self {
        Self(aliases)
    }

    fn get(&self, name: &str) -> Option<&'a Alias> {
        self.0?.get(name)
    }

    /// Whether the board's `style.formats` defines `name`.
    pub fn defines(&self, name: &str) -> bool {
        self.get(name).is_some()
    }

    /// The notation the alias `name` sets, when it is a mapping that sets one.
    pub fn alias_notation(&self, name: &str) -> Option<Notation> {
        self.config(Some(name))?.notation
    }

    /// The mapping `spec` names, if its target is one.
    fn config(&self, spec: Option<&str>) -> Option<&'a AuthoredFormat> {
        match self.get(spec?)? {
            Alias::Config(config) => Some(config),
            Alias::Spec(_) => None,
        }
    }
}

/// A resolved d3 spec plus the affix and notation it cannot carry, and the authored
/// spec (`raw`) house rules key on.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ResolvedFormat {
    pub spec: String,
    pub prefix: String,
    pub suffix: String,
    pub notation: Option<Notation>,
    pub sign_placement: Option<SignPlacement>,
    pub repeat: Option<Repeat>,
    pub raw: Option<String>,
}

/// Whether a format takes the engine's rules or prints its own spec.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FormatLook {
    House,
    Literal,
}

impl ResolvedFormat {
    /// `raw` names a predefined number format (house rules apply).
    pub fn is_house(&self) -> bool {
        self.raw.as_deref().is_some_and(is_predefined_number_name)
    }

    pub fn look(&self) -> FormatLook {
        if self.is_house() {
            FormatLook::House
        } else {
            FormatLook::Literal
        }
    }

    /// `BakedAxis.is_house`: whether axis labels inheriting this format take house
    /// rules, a predefined member whose own spec is SI. Every house raw resolves to
    /// the engine's spec, so `d3rs` parses it.
    pub fn is_house_si(&self) -> bool {
        self.is_house() && d3rs::format::parse_parts(&self.spec).is_ok_and(|parts| parts.type_ == 's')
    }
}

fn is_native_name(name: &str) -> bool {
    d3rs::predefined_formats::PREDEFINED_NATIVE.contains(&name)
}

fn is_predefined_number_name(name: &str) -> bool {
    d3rs::format::predefined_spec(name).is_some() || is_native_name(name)
}

fn is_time_name(name: &str) -> bool {
    d3rs::format::predefined_time_spec(name).is_some() || GRAIN_NAMES.contains(&name)
}

/// `ALL_PREDEFINED_NAMES`: every name the engine owns.
fn is_engine_name(name: &str) -> bool {
    is_predefined_number_name(name) || is_time_name(name) || TEXT_NAMES.contains(&name)
}

/// `text/format_d3.py::is_time_format`: a d3-time-format directive (`%<letter>`,
/// optionally `%-d`/`%_d`/`%0d`) once `%%` escapes are removed.
pub fn is_time_format(spec: &str) -> bool {
    static DIRECTIVE: LazyLock<regex::Regex> =
        LazyLock::new(|| regex::Regex::new(r"%[-_0]?[A-Za-z]").expect("a valid pattern"));
    DIRECTIVE.is_match(&spec.replace("%%", ""))
}

/// The predefined name an alias targets, if its target is one.
fn alias_preset<'a>(name: Option<&str>, formats: Formats<'a>) -> Option<&'a str> {
    let target = formats.get(name?)?.spec()?;
    is_engine_name(target).then_some(target)
}

/// `compile/format.py::resolve_format`: a format to its d3 spec ("" for none). A
/// `style.formats` name wins: its preset's spec, else its literal target, so a board
/// that redefines an engine name replaces the engine's spec. Then a predefined number
/// name resolves to its house spec (native members pass through as the bare name), a
/// time name to its strftime spec, and anything else passes through. A grain name the
/// board did not redefine has no spec: refused.
pub fn resolve_format(input: &AuthoredFormat, formats: Formats<'_>) -> Result<String, Diagnostic> {
    let Some(name) = input.spec.as_deref() else {
        return Ok(String::new());
    };
    let preset = alias_preset(Some(name), formats);
    // Through the board's own table, so `revenue: currency` prints what the board
    // made of `currency`; a name aliased to itself is the engine's own.
    if let Some(preset) = preset.filter(|preset| *preset != name) {
        return resolve_format(&AuthoredFormat::named(preset), formats);
    }
    if preset.is_none() {
        if let Some(alias) = formats.get(name) {
            return Ok(match alias {
                Alias::Spec(spec) => spec.to_string(),
                Alias::Config(config) => config.spec.clone().unwrap_or_default(),
            });
        }
    }
    if is_predefined_number_name(name) {
        return Ok(d3rs::format::predefined_spec(name).map_or(name, |spec| spec.d3).to_string());
    }
    if GRAIN_NAMES.contains(&name) {
        return Err(Diagnostic::refused(format!(
            "ERR-FORMAT-GRAIN-NAME-NOT-REDEFINED: {name} is the engine's own axis labels for its grain: \
             redefine it in style.formats with a strftime spec, or use a strftime spec"
        )));
    }
    if let Some(spec) = d3rs::format::predefined_time_spec(name) {
        return Ok(spec.to_string());
    }
    Ok(name.to_string())
}

/// `get_format_prefix_suffix`: an explicit affix wins over its alias's.
fn prefix_suffix(input: &AuthoredFormat, formats: Formats<'_>) -> (String, String) {
    let (prefix, suffix) = (input.prefix.clone().unwrap_or_default(), input.suffix.clone().unwrap_or_default());
    if !prefix.is_empty() || !suffix.is_empty() {
        return (prefix, suffix);
    }
    match formats.config(input.spec.as_deref()) {
        Some(aliased) => {
            (aliased.prefix.clone().unwrap_or_default(), aliased.suffix.clone().unwrap_or_default())
        }
        None => (String::new(), String::new()),
    }
}

/// `_named_options`: the option the format named `name` carries: its own, else what
/// the name it targets carries in turn, down to the engine's own definition of an
/// engine name. A board's entry wins over the engine's; a name aliased to itself
/// fills what it leaves unset from the engine's definition.
fn named_option<T: Copy>(
    name: &str,
    formats: Formats<'_>,
    own: impl Fn(&AuthoredFormat) -> Option<T>,
    engine: impl Fn(&str) -> Option<T>,
) -> Option<T> {
    let mut seen: Vec<&str> = Vec::new();
    let mut current = name;
    loop {
        if seen.contains(&current) {
            return None;
        }
        let target = formats.get(current);
        if let Some(Alias::Config(config)) = target {
            if let Some(value) = own(config) {
                return Some(value);
            }
        }
        let inner = target.and_then(Alias::spec);
        if target.is_none() || inner == Some(current) {
            return engine(current);
        }
        seen.push(current);
        current = inner?;
    }
}

/// `_resolved_raw`: the identity downstream rules key on. The preset a name resolves
/// to (an alias of a name the board redefined follows the redefinition; a name aliased
/// to itself is the engine's own), the author's literal spec when a board redefined an
/// engine name (so the engine's rules for that name stop applying), else the name itself.
fn resolved_raw(raw: Option<&str>, formats: Formats<'_>) -> Option<String> {
    if let Some(preset) = alias_preset(raw, formats) {
        return if Some(preset) == raw {
            Some(preset.to_string())
        } else {
            resolved_raw(Some(preset), formats)
        };
    }
    let name = raw?;
    match formats.get(name).filter(|_| is_engine_name(name)) {
        None => Some(name.to_string()),
        Some(target) => target.spec().filter(|spec| !spec.is_empty()).map(str::to_string),
    }
}

/// `resolve_format_parts`: the authored spec, prefix, suffix and options resolved; a
/// slot with no spec of its own stays empty ([`column_format`] fills it).
pub fn resolve_format_parts(input: &AuthoredFormat, formats: Formats<'_>) -> Result<ResolvedFormat, Diagnostic> {
    let (prefix, mut suffix) = prefix_suffix(input, formats);
    let raw = resolved_raw(input.spec.as_deref(), formats);
    if prefix.is_empty() && suffix.is_empty() {
        // An authored affix replaces the engine's own unit (`°`).
        if let Some(spec) = raw.as_deref().and_then(d3rs::format::predefined_spec) {
            suffix = spec.suffix.to_string();
        }
    }
    // An unset option follows the name the spec names.
    fn option<T: Copy>(
        authored: Option<T>,
        input: &AuthoredFormat,
        formats: Formats<'_>,
        own: impl Fn(&AuthoredFormat) -> Option<T>,
        engine: impl Fn(&str) -> Option<T>,
    ) -> Option<T> {
        authored.or_else(|| named_option(input.spec.as_deref()?, formats, own, engine))
    }
    Ok(ResolvedFormat {
        spec: resolve_format(input, formats)?,
        prefix,
        suffix,
        notation: option(input.notation, input, formats, |c| c.notation, |name| {
            ANALYTIC_NAMES.contains(&name).then_some(Notation::Analytic)
        }),
        sign_placement: option(input.sign_placement, input, formats, |c| c.sign_placement, |_| None),
        repeat: option(input.repeat, input, formats, |c| c.repeat, |_| None),
        raw,
    })
}

/// `predefined_formats.is_money_format`: a spec carrying a literal `$`, or an affix
/// naming a currency (a small explicit set; what it cannot identify is not money).
fn is_money_format(format: &ResolvedFormat) -> bool {
    const MONEY_SYMBOLS: &[&str] = &["$", "£", "€", "¥", "₹", "₩", "₽"];
    const MONEY_CODES: &[&str] = &["USD", "GBP", "EUR", "JPY", "CAD", "AUD", "CHF", "CNY", "INR"];
    let affix_is_money = |affix: &str| {
        let stripped = affix.trim();
        !stripped.is_empty()
            && (MONEY_SYMBOLS.iter().any(|symbol| stripped.contains(symbol))
                || MONEY_CODES.contains(&stripped.to_uppercase().as_str()))
    };
    format.spec.contains('$') || affix_is_money(&format.prefix) || affix_is_money(&format.suffix)
}

fn in_sub_unit_band(value: f64) -> bool {
    (d3rs::format::SUB_UNIT_FLOOR..1.0).contains(&value.abs())
}

/// `sub_unit_spec`: the `_full` sibling of an engine SI member, as the board
/// redefined it if it did.
fn sub_unit_spec(raw: &str, formats: Formats<'_>) -> Result<String, Diagnostic> {
    let fallback = d3rs::format::sub_unit_fallback(raw).expect("a member with a sub-unit sibling");
    if formats.get(fallback).is_some() {
        resolve_format(&AuthoredFormat::named(fallback), formats)
    } else {
        Ok(d3rs::format::sub_unit_engine_spec(fallback).to_string())
    }
}

/// `sub_unit_spec_for_value`: the spec a per-value surface paints `value` with. An
/// engine SI member never prints an SI prefix below 1: in the sub-unit band it prints
/// its `_full` sibling, below that its significant digits, and past the scientific
/// floor its exponential register; zero and everything from 1 up keep `spec`.
pub fn sub_unit_spec_for_value(
    raw: Option<&str>,
    spec: &str,
    value: f64,
    formats: Formats<'_>,
) -> Result<String, Diagnostic> {
    let Some(raw) = raw.filter(|raw| d3rs::format::sub_unit_fallback(raw).is_some()) else {
        return Ok(spec.to_string());
    };
    let full = if in_sub_unit_band(value) { sub_unit_spec(raw, formats)? } else { String::new() };
    d3rs::format::sub_unit_spec_for_value(spec, &full, value).map_err(Diagnostic::refused)
}

/// `_finished`'s sub-unit vote: a money slot with any value inside the band prints
/// its `_full` sibling slot-wide; a plain `number` slot keeps its spec and paints its
/// sub-1 values per value ([`sub_unit_spec_for_value`]).
fn vote_sub_unit(format: &mut ResolvedFormat, formats: Formats<'_>, values: &[f64]) -> Result<(), Diagnostic> {
    let raw = format.raw.clone();
    if let Some(raw) = raw.as_deref().filter(|raw| d3rs::format::sub_unit_fallback(raw).is_some()) {
        if is_money_format(format) && values.iter().any(|value| in_sub_unit_band(*value)) {
            format.spec = sub_unit_spec(raw, formats)?;
        }
    }
    Ok(())
}

/// Which half of the engine's vocabulary a slot takes by default (`_DEFAULT_NAME`).
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Number,
    Time,
}

impl Kind {
    fn default_name(self) -> &'static str {
        match self {
            Kind::Number => "number",
            Kind::Time => "date_short",
        }
    }
}

/// The formats one slot can take from, strongest first: the chart's own slot, the
/// format a query's `formats:` declares for the column (a name), and the board- or
/// theme-level slot. `None` is a tier that sets nothing; callers pass each tier as its
/// own input and never pre-merge them.
#[derive(Clone, Copy, Default)]
pub struct Tiers<'a> {
    pub own: Option<&'a AuthoredFormat>,
    pub column: Option<&'a AuthoredFormat>,
    pub board: Option<&'a AuthoredFormat>,
}

/// A tier that spells a look of the other kind has nothing to say about this slot's
/// values. A text name belongs to neither kind.
fn is_other_kind(fields: &ResolvedFormat, kind: Kind) -> bool {
    !fields.spec.is_empty()
        && !TEXT_NAMES.contains(&fields.spec.as_str())
        && is_time_format(&fields.spec) != (kind == Kind::Time)
}

fn fill(own: &str, lower: &str) -> String {
    if own.is_empty() { lower } else { own }.to_string()
}

/// `_merge_tiers`: each field comes from the strongest tier that sets it. The first tier
/// is the slot's own and keeps whatever kind it is; the later ones are defaults for a
/// column with none of its own, and one that spells the other kind contributes nothing,
/// affix included. The default for the kind joins as the weakest tier only when no tier
/// sets a spec, so a tier that names its own spec never inherits the default's affix.
fn merge_tiers(
    kind: Kind,
    tiers: Tiers<'_>,
    formats: Formats<'_>,
    engine_default: Option<&str>,
) -> Result<ResolvedFormat, Diagnostic> {
    let mut resolved = Vec::new();
    for (position, tier) in [tiers.own, tiers.column, tiers.board].into_iter().enumerate() {
        let Some(tier) = tier else { continue };
        let fields = resolve_format_parts(tier, formats)?;
        if position == 0 || !is_other_kind(&fields, kind) {
            resolved.push(fields);
        }
    }
    if resolved.iter().all(|tier| tier.spec.is_empty()) {
        let name = kind.default_name();
        let mut default = resolve_format_parts(&AuthoredFormat::named(name), formats)?;
        if let Some(instead) = engine_default.filter(|_| default.raw.as_deref() == Some(name)) {
            // The destination's name resolves through the board like any other, so a
            // board that redefined it (affix included) prints it whole.
            let instead = resolve_format_parts(&AuthoredFormat::named(instead), formats)?;
            default = ResolvedFormat {
                raw: instead.raw.clone().filter(|raw| !raw.is_empty()),
                prefix: fill(&instead.prefix, &default.prefix),
                suffix: fill(&instead.suffix, &default.suffix),
                notation: instead.notation.or(default.notation),
                sign_placement: instead.sign_placement.or(default.sign_placement),
                repeat: instead.repeat.or(default.repeat),
                spec: instead.spec,
            };
        }
        resolved.push(default);
    }
    let weakest = resolved.last().expect("a default joins when no tier is set");
    let spec_tier = resolved.iter().find(|tier| !tier.spec.is_empty()).unwrap_or(weakest);
    let text = |field: fn(&ResolvedFormat) -> &String| {
        resolved.iter().map(field).find(|value| !value.is_empty()).cloned().unwrap_or_default()
    };
    Ok(ResolvedFormat {
        spec: spec_tier.spec.clone(),
        raw: spec_tier.raw.clone(),
        prefix: text(|f| &f.prefix),
        suffix: text(|f| &f.suffix),
        notation: resolved.iter().find_map(|f| f.notation),
        sign_placement: resolved.iter().find_map(|f| f.sign_placement),
        repeat: resolved.iter().find_map(|f| f.repeat),
    })
}

/// `column_format`: the format a value of `kind` gets in one slot, `tiers` over the
/// board's default for the kind (`number` or `date_short` through `formats`, so a name
/// the board redefined supplies spec, affix and notation together). `engine_default` is
/// a destination's policy for the engine's own default (a donut total says `integer`);
/// it never replaces a board's redefinition of the default. `notation` is the
/// destination's register when the format sets none. The spec is then voted over `values`.
pub fn column_format(
    kind: Kind,
    tiers: Tiers<'_>,
    formats: Formats<'_>,
    engine_default: Option<&str>,
    notation: Option<Notation>,
    values: &[f64],
) -> Result<ResolvedFormat, Diagnostic> {
    let mut format = merge_tiers(kind, tiers, formats, engine_default)?;
    format.notation = format.notation.or(notation);
    vote_sub_unit(&mut format, formats, values)?;
    Ok(format)
}

/// `ALL_PREDEFINED_NAMES`: the engine's number, time and text format names.
pub fn is_predefined_name(name: &str) -> bool {
    use d3rs::predefined_formats::{PREDEFINED_NATIVE, PREDEFINED_SPECS, PREDEFINED_TIME_SPECS};
    PREDEFINED_SPECS.iter().any(|spec| spec.name == name)
        || PREDEFINED_NATIVE.contains(&name)
        || PREDEFINED_TIME_SPECS.iter().any(|(spec, _)| *spec == name)
        || TEXT_NAMES.contains(&name)
}

/// `_check_percent_range`'s threshold: a share or rate past it is a forgotten ÷100.
const PERCENT_RANGE_THRESHOLD: f64 = 10.0;

/// `format_value`: one number through `format` over the board's default for a
/// number: an engine native as its own digits and unit, else the d3 spec the value
/// takes (its sub-unit sibling below 1), in the format's notation or, for an engine
/// house format, the analytic register. A non-delta percent of 10 or more is
/// `ERR-PERCENT-RANGE`.
pub fn format_value(
    value: f64,
    format: &AuthoredFormat,
    formats: Formats<'_>,
    locale: &locale::NumberLocale,
) -> Result<String, Diagnostic> {
    let parts = column_format(Kind::Number, Tiers { own: Some(format), ..Tiers::default() }, formats, None, None, &[])?;
    if let Some((number, unit)) = d3rs::native::predefined_native(&parts.spec, value).map_err(Diagnostic::refused)? {
        return Ok(format!("{}{number}{unit}{}", parts.prefix, parts.suffix));
    }
    let spec = sub_unit_spec_for_value(parts.raw.as_deref(), &parts.spec, value, formats)?;
    if spec.ends_with('%') && !spec.starts_with('+') && value.abs() >= PERCENT_RANGE_THRESHOLD {
        return Err(diag::err_percent_range(value, spec));
    }
    let notation = parts.notation.or(parts.is_house().then_some(Notation::Analytic));
    parts.paint_as(locale, &spec, notation, value)
}

/// `declared_format`: the format name a query's `formats:` gives every one of
/// `columns`. `None` when any column has no entry or the entries differ: a destination
/// painting several columns with one format has no single column to speak for them, so
/// it keeps its own default rather than guess.
pub fn declared_format<'a>(declared: &'a IndexMap<String, String>, columns: &[&str]) -> Option<&'a str> {
    let mut entries = columns.iter().map(|column| declared.get(*column).map(String::as_str));
    let first = entries.next()??;
    entries.all(|entry| entry == Some(first)).then_some(first)
}

/// `declared_column_format`: the format name the chart's query declares for `columns`,
/// when it is of the asked kind (a time format for `Kind::Time`, else a number format).
/// A number format on a date axis would paint garbage, and a time format has nothing to
/// say about a number, so the other kind is no format at all. A text name is of neither
/// kind: it comes back only for a destination that links text, and is no format at all
/// to any other.
pub fn declared_column_format(
    declared: &IndexMap<String, String>,
    columns: &[&str],
    formats: Formats<'_>,
    kind: Kind,
    text: bool,
) -> Result<Option<AuthoredFormat>, Diagnostic> {
    let Some(name) = declared_format(declared, columns) else {
        return Ok(None);
    };
    let resolved = resolve_format(&AuthoredFormat::named(name), formats)?;
    if TEXT_NAMES.contains(&resolved.as_str()) {
        return Ok(text.then(|| AuthoredFormat::named(name)));
    }
    Ok((is_time_format(&resolved) == (kind == Kind::Time)).then(|| AuthoredFormat::named(name)))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The engine names production defines (`gen_format_names.py`); a name added there must be known here.
    #[test]
    fn the_name_tables_match_production() {
        let names: serde_json::Value =
            serde_json::from_str(include_str!("format_names.json")).expect("names");
        let list = |key: &str| -> Vec<String> {
            names[key].as_array().expect(key).iter().map(|n| n.as_str().expect("name").to_string()).collect()
        };
        assert_eq!(list("grain"), GRAIN_NAMES);
        assert_eq!(list("text"), TEXT_NAMES);
        assert_eq!(list("analytic_notation"), ANALYTIC_NAMES);
    }
}
