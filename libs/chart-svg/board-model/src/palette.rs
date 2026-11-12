//! The shipped palettes and the color-token grammar over them: `palette.slot`, `role[N]`,
//! `role.alias`, a bare theme role, and a trailing variant, as `compile/resolve/style/palette.py`'s
//! `color` and `color_from_theme` resolve them. The catalog is the package's palette YAML, compiled
//! in (`build.rs`); an unknown palette, role, alias or stop is an error naming it. Palette
//! references (`:N`, `_r`, a role, a variant) resolve as `compile/resolve/style/palette.py` does,
//! checked by `tests/test_board_model_palette_fixture.py`.

use indexmap::IndexMap;
use std::fmt;
use std::sync::OnceLock;
use yaml::{Kind, Node, Value};

include!(concat!(env!("OUT_DIR"), "/palette_sources.rs"));

/// Why a token or a palette does not resolve.
#[derive(Debug, Clone, PartialEq)]
pub struct PaletteError(pub String);

impl fmt::Display for PaletteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for PaletteError {}

impl PaletteError {
    /// Python's `UnknownPaletteError`: a name no palette answers to, a perceptual anti-pattern, or
    /// a variant on a palette that takes none. Every other failure (a tone, an over-request) is
    /// the palette's own and unusable, not unknown.
    pub fn is_unknown(&self) -> bool {
        self.0.starts_with("unknown palette ")
            || self.0.contains("is a known perceptual anti-pattern")
            || self.0.contains("has no 'colors:' array")
    }
}

fn err<T>(msg: impl Into<String>) -> Result<T, PaletteError> {
    Err(PaletteError(msg.into()))
}

#[derive(Debug, Clone, PartialEq)]
enum Alias {
    Stop(i64),
    Text(String),
}

#[derive(Debug, Clone)]
struct Spine {
    family: &'static str,
    colors: Option<Vec<String>>,
    aliases: IndexMap<String, Alias>,
}

struct Raw {
    family: &'static str,
    extends: Option<String>,
    colors: Option<Vec<String>>,
    aliases: IndexMap<String, Alias>,
}

fn scalar_text(n: &Node) -> Option<String> {
    match &n.kind {
        Kind::Scalar(Value::Str(s)) => Some(s.clone()),
        Kind::Scalar(Value::Number(x)) => Some(x.to_string()),
        _ => None,
    }
}

fn parse(family: &'static str, text: &str) -> Result<(String, Raw), PaletteError> {
    let node = yaml::parse_node(text).map_err(|d| {
        PaletteError(format!(
            "palette YAML: {}",
            d.iter()
                .map(|d| d.code.to_string())
                .collect::<Vec<_>>()
                .join(", ")
        ))
    })?;
    let Kind::Map(map) = &node.kind else {
        return err("palette is not a mapping");
    };
    let get = |k: &str| map.get(k).map(|e| &e.value);
    let name = get("name")
        .and_then(scalar_text)
        .ok_or_else(|| PaletteError("palette has no name".into()))?;
    let extends = get("extends").and_then(scalar_text);
    let colors = match get("colors").map(|n| &n.kind) {
        Some(Kind::Seq(items)) => Some(
            items
                .iter()
                .map(|i| {
                    scalar_text(i).ok_or_else(|| PaletteError("palette colors must be text".into()))
                })
                .collect::<Result<Vec<_>, _>>()?,
        ),
        _ => None,
    };
    let mut aliases = IndexMap::new();
    if let Some(Kind::Map(a)) = get("aliases").map(|n| &n.kind) {
        for (k, e) in a {
            let alias = match &e.value.kind {
                Kind::Scalar(Value::Number(x)) => match x.as_i64() {
                    Some(i) => Alias::Stop(i),
                    None => return err(format!("alias {k:?} is not an integer")),
                },
                _ => match scalar_text(&e.value) {
                    Some(t) => Alias::Text(t),
                    None => return err(format!("alias {k:?} is not text")),
                },
            };
            aliases.insert(k.clone(), alias);
        }
    }
    Ok((
        name,
        Raw {
            family,
            extends,
            colors,
            aliases,
        },
    ))
}

/// Every shipped palette by name, `extends:` folded.
fn catalog() -> &'static Result<IndexMap<String, Spine>, PaletteError> {
    static CATALOG: OnceLock<Result<IndexMap<String, Spine>, PaletteError>> = OnceLock::new();
    CATALOG.get_or_init(|| {
        let mut raw: IndexMap<String, Raw> = IndexMap::new();
        for (family, text) in PALETTE_SOURCES {
            let (name, r) = parse(family, text)?;
            raw.insert(name, r);
        }
        let mut out = IndexMap::new();
        for name in raw.keys() {
            out.insert(name.clone(), merged(&raw, name, &mut Vec::new())?);
        }
        Ok(out)
    })
}

fn merged(
    raw: &IndexMap<String, Raw>,
    name: &str,
    seen: &mut Vec<String>,
) -> Result<Spine, PaletteError> {
    if seen.iter().any(|s| s == name) {
        return err(format!("palette extends cycle detected at {name}"));
    }
    let Some(r) = raw.get(name) else {
        return err(format!("unknown palette {name:?}"));
    };
    let Some(parent) = &r.extends else {
        return Ok(Spine {
            family: r.family,
            colors: r.colors.clone(),
            aliases: r.aliases.clone(),
        });
    };
    seen.push(name.to_string());
    let p = merged(raw, parent, seen)?;
    seen.pop();
    let mut aliases = p.aliases;
    aliases.extend(r.aliases.clone());
    Ok(Spine {
        family: r.family,
        colors: r.colors.clone().or(p.colors),
        aliases,
    })
}

fn spine(name: &str) -> Result<&'static Spine, PaletteError> {
    match catalog() {
        Ok(c) => c
            .get(name)
            .ok_or_else(|| PaletteError(format!("unknown palette {name:?}"))),
        Err(e) => Err(e.clone()),
    }
}

/// Every shipped palette name, sorted (Python's `list_palettes`).
pub fn palette_names() -> Result<Vec<String>, PaletteError> {
    let mut names: Vec<String> = catalog().as_ref().map_err(Clone::clone)?.keys().cloned().collect();
    names.sort_unstable();
    Ok(names)
}

/// Whether `name` is a shipped palette.
pub fn is_palette(name: &str) -> bool {
    spine(name).is_ok()
}

const STOP_LIST: [&str; 4] = ["categorical", "sequential", "diverging", "scaffold"];

fn alias_chain(
    key: &str,
    aliases: &IndexMap<String, Alias>,
    colors: Option<&[String]>,
) -> Result<String, PaletteError> {
    let mut visited: Vec<&str> = Vec::new();
    let mut current = key;
    loop {
        if visited.contains(&current) {
            return err(format!(
                "alias cycle detected: {} → {current}",
                visited.join(" → ")
            ));
        }
        visited.push(current);
        let Some(value) = aliases.get(current) else {
            return err(format!(
                "alias '{key}' → '{current}' not found in palette aliases. Known aliases: {}",
                known_aliases(aliases)
            ));
        };
        match value {
            Alias::Stop(i) => {
                let Some(colors) = colors else {
                    return err(format!("alias '{current}' resolves to slot index {i} but palette has no 'colors:' array"));
                };
                return match usize::try_from(*i)
                    .ok()
                    .filter(|i| (1..=colors.len()).contains(i))
                {
                    Some(i) => Ok(colors.get(i - 1).cloned().unwrap_or_default()),
                    None => err(format!("alias '{current}' slot index {i} out of range")),
                };
            }
            Alias::Text(t) if t.starts_with('#') => return Ok(t.clone()),
            Alias::Text(t) => current = t,
        }
    }
}

/// `sorted(aliases)` as Python prints the list.
fn known_aliases(aliases: &IndexMap<String, Alias>) -> String {
    let mut keys: Vec<&String> = aliases.keys().collect();
    keys.sort_unstable();
    let items: Vec<String> = keys
        .into_iter()
        .map(|k| diagnostic::py_str_repr(k))
        .collect();
    format!("[{}]", items.join(", "))
}

fn positional(name: &str, idx: i64, sp: &Spine) -> Result<String, PaletteError> {
    if sp.aliases.contains_key(&idx.to_string()) {
        return alias_chain(&idx.to_string(), &sp.aliases, sp.colors.as_deref());
    }
    let stops = sp.colors.as_deref().unwrap_or(&[]);
    match usize::try_from(idx)
        .ok()
        .filter(|i| (1..=stops.len()).contains(i))
    {
        Some(i) => Ok(stops.get(i - 1).cloned().unwrap_or_default()),
        None => {
            let mut valid: Vec<usize> = sp
                .aliases
                .keys()
                .filter_map(|k| {
                    k.bytes()
                        .all(|b| b.is_ascii_digit())
                        .then(|| k.parse().ok())?
                })
                .chain(1..=stops.len())
                .collect();
            valid.sort_unstable();
            valid.dedup();
            err(format!(
                "palette '{name}' has no stop {idx}; valid stops: {}",
                stop_runs(&valid).join(", ")
            ))
        }
    }
}

/// Sorted stops as Python words them: a run of three or more consecutive stops is `a-b`.
fn stop_runs(valid: &[usize]) -> Vec<String> {
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for &n in valid {
        match groups.last_mut() {
            Some(run) if run.last().is_some_and(|last| last + 1 == n) => run.push(n),
            _ => groups.push(vec![n]),
        }
    }
    groups
        .iter()
        .flat_map(|run| match (run.first(), run.last()) {
            (Some(a), Some(b)) if run.len() >= 3 => vec![format!("{a}-{b}")],
            _ => run.iter().map(usize::to_string).collect(),
        })
        .collect()
}

/// `(base, variant)` when `name` ends in `.dark`, `.light`, `.pale` or `.deep`.
fn split_variant(name: &str) -> (&str, Option<&'static str>) {
    if let Some((base, tail)) = name.rsplit_once('.') {
        if let Some(w) = color::VARIANT_WORDS.iter().find(|w| **w == tail) {
            if !base.is_empty() {
                return (base, Some(w));
            }
        }
    }
    (name, None)
}

fn color_without_variant(token: &str) -> Result<String, PaletteError> {
    let Some((palette, slot)) = token.split_once('.') else {
        return err(format!(
            "color token must be dotted 'palette.slot', got {token:?}"
        ));
    };
    let sp = spine(palette)
        .map_err(|_| PaletteError(format!("unknown palette {palette:?} in {token:?}")))?;
    if matches!(sp.family, "tone" | "scaffold") {
        if sp.aliases.contains_key(slot) {
            return alias_chain(slot, &sp.aliases, sp.colors.as_deref());
        }
        if sp.family == "tone" || !is_int(slot) {
            return err(format!(
                "palette '{palette}' has no alias '{slot}'. Known aliases: {}",
                known_aliases(&sp.aliases)
            ));
        }
    }
    if STOP_LIST.contains(&sp.family) {
        if !(slot.is_ascii() && !slot.is_empty() && slot.bytes().all(|b| b.is_ascii_digit())) {
            return err(format!(
                "{} palette '{palette}' indexed by integer; got '{slot}'",
                sp.family
            ));
        }
        return match slot.parse::<i64>() {
            Ok(i) => positional(palette, i, sp),
            Err(_) => err(format!("palette '{palette}' has no stop {slot}")),
        };
    }
    err(format!(
        "palette '{palette}' (family={}) does not support color() access",
        sp.family
    ))
}

fn is_int(s: &str) -> bool {
    let t = s.strip_prefix('-').unwrap_or(s);
    !t.is_empty() && t.bytes().all(|b| b.is_ascii_digit())
}

/// A `palette.slot` token (`dbt-grays.8`, `vivid-10.3.dark`) as an sRGB hex.
pub fn color(token: &str) -> Result<String, PaletteError> {
    match color_without_variant(token) {
        Ok(c) => Ok(c),
        Err(original) => {
            let (base, Some(kind)) = split_variant(token) else {
                return Err(original);
            };
            let resolved = color_without_variant(base).map_err(|_| original.clone())?;
            let palette = base.split_once('.').map_or(base, |(p, _)| p);
            if spine(palette).map(|s| s.family) != Ok("categorical") {
                return err(format!(
                    "unknown color token {token:?}. Variants apply to categorical palettes."
                ));
            }
            apply_variant(&resolved, kind)
        }
    }
}

fn apply_variant(c: &str, kind: &str) -> Result<String, PaletteError> {
    color::variant(c, kind).map_err(PaletteError)
}

/// A theme's palette bindings and role aliases, the single-series ink list, and the board's
/// categorical palette: the context a role-indirected token resolves in.
pub struct ThemeColors<'a> {
    pub palettes: &'a IndexMap<String, String>,
    pub roles: &'a IndexMap<String, String>,
    pub single_series: Option<&'a [String]>,
    /// The board's own categorical palette, what `category[N]` indexes. `None` when the board
    /// authors none: the `category` role's bound palette is then the board's palette.
    pub category: Option<&'a [String]>,
}

fn bound(
    role: &str,
    token: &str,
    t: &ThemeColors,
) -> Result<(String, Option<&'static str>, &'static Spine), PaletteError> {
    let Some(b) = t.palettes.get(role) else {
        let mut roles: Vec<&str> = t.palettes.keys().map(String::as_str).collect();
        roles.sort_unstable();
        let retired = migrate::respell_retired_variant(token)
            .map(|now| format!(" '{token}' was retired; use '{now}' instead."))
            .unwrap_or_default();
        return err(format!(
            "color token {}: theme has no palette assigned to role '{role}'.{retired} Roles with a palette: {}. Write a token as `<role>.<alias>` or `<role>[N]` with one of those roles.",
            diagnostic::py_repr(&token.into()),
            roles.join(", ")
        ));
    };
    let (name, v) = split_variant(b);
    let sp = spine(name).map_err(|_| {
        PaletteError(format!(
            "palette '{name}' (role '{role}') not found in catalog"
        ))
    })?;
    Ok((name.to_string(), v, sp))
}

fn with_bound(c: String, v: Option<&str>) -> Result<String, PaletteError> {
    match v {
        None => Ok(c),
        Some(k) => apply_variant(&c, k),
    }
}

fn dispatch(
    token: &str,
    t: &ThemeColors,
    visited: &mut Vec<String>,
) -> Result<(String, &'static str), PaletteError> {
    // role[N]
    if let Some((role, rest)) = token.split_once('[') {
        if let Some(n) = rest
            .strip_suffix(']')
            .filter(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
        {
            if role.chars().next().is_some_and(|c| c.is_ascii_alphabetic())
                && role
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
            {
                let n: i64 = n
                    .parse()
                    .map_err(|_| PaletteError(format!("bad stop in {token:?}")))?;
                if role == "single_series" {
                    let Some(list) = t.single_series else {
                        return err(format!(
                            "no single-series palette context available for {token:?}"
                        ));
                    };
                    return match usize::try_from(n)
                        .ok()
                        .filter(|i| (1..=list.len()).contains(i))
                    {
                        Some(i) => Ok((list.get(i - 1).cloned().unwrap_or_default(), "literal")),
                        None => err(format!(
                            "single-series palette has {} slot(s); requested slot {n}",
                            list.len()
                        )),
                    };
                }
                if let ("category", Some(list)) = (role, t.category) {
                    return match usize::try_from(n)
                        .ok()
                        .filter(|i| (1..=list.len()).contains(i))
                    {
                        Some(i) => Ok((list.get(i - 1).cloned().unwrap_or_default(), "categorical")),
                        None => err(format!(
                            "color token '{token}': the board's categorical palette has no stop {n}; valid stops 1-{}",
                            list.len()
                        )),
                    };
                }
                let (name, v, sp) = bound(role, token, t)?;
                if sp.colors.is_none() {
                    return err(format!("palette '{name}' (role '{role}') has no 'colors:' array; bracket form requires a colors array"));
                }
                return Ok((with_bound(positional(&name, n, sp)?, v)?, sp.family));
            }
        }
    }
    if let Some((role, alias)) = token.split_once('.') {
        let (name, v, sp) = bound(role, token, t)?;
        if !sp.aliases.contains_key(alias) {
            let variants = if alias.contains('.') {
                format!(
                    " If you meant a variant, the four are: {}.",
                    color::VARIANT_WORDS.join(", ")
                )
            } else {
                String::new()
            };
            return err(format!(
                "palette '{name}' (role '{role}') has no alias '{alias}'. Known aliases: {}.{variants}",
                known_aliases(&sp.aliases)
            ));
        }
        let c = alias_chain(alias, &sp.aliases, sp.colors.as_deref())?;
        return Ok((with_bound(c, v)?, sp.family));
    }
    let Some(target) = t.roles.get(token) else {
        return err(format!("unknown color token '{token}': not a dotted palette address, bracket form, or theme role"));
    };
    if visited.iter().any(|v| v == token) {
        return err(format!("theme.roles cycle detected involving '{token}'"));
    }
    visited.push(token.to_string());
    let out = resolve(target, t, visited);
    visited.pop();
    out
}

fn resolve(
    token: &str,
    t: &ThemeColors,
    visited: &mut Vec<String>,
) -> Result<(String, &'static str), PaletteError> {
    match dispatch(token, t, visited) {
        Ok(r) => Ok(r),
        Err(original) => {
            let (base, Some(kind)) = split_variant(token) else {
                return Err(original);
            };
            let (resolved, family) = dispatch(base, t, visited).map_err(|_| original.clone())?;
            if !matches!(family, "categorical" | "literal") {
                return err(format!(
                    "unknown color token {token:?}. Variants apply to categorical palettes."
                ));
            }
            Ok((apply_variant(&resolved, kind)?, family))
        }
    }
}

/// A role-indirected token (`chrome[11]`, `chrome.white`, `category[2].dark`, `ink`,
/// `single_series[1]`) as an sRGB hex, against a theme's palette bindings and roles.
pub fn color_from_theme(token: &str, theme: &ThemeColors) -> Result<String, PaletteError> {
    resolve(token, theme, &mut Vec::new()).map(|(c, _)| c)
}

/// What a token resolves to wherever the theme's palette context applies: a direct
/// `palette.slot` address first, then the role-indirected forms, as `_resolve_one_color_token`.
pub fn resolve_token(token: &str, theme: &ThemeColors) -> Result<String, PaletteError> {
    match color(token) {
        Ok(c) => Ok(c),
        Err(direct) => {
            // A shipped palette that is not also a theme role can only mean itself.
            let head = token.split_once('.').map(|(h, _)| h);
            if let Some(head) = head {
                if !theme.palettes.contains_key(head) && is_palette(head) {
                    return Err(direct);
                }
            }
            color_from_theme(token, theme)
        }
    }
}

/// `(base, variants)` with every trailing variant segment split off, in application order.
fn split_variants(mut name: &str) -> (&str, Vec<&'static str>) {
    let mut found = Vec::new();
    while let (base, Some(v)) = split_variant(name) {
        found.push(v);
        name = base;
    }
    found.reverse();
    (name, found)
}

/// `name[:N][_r]` as `(name, steps, reverse)`.
fn parse_reference(r: &str) -> Result<(String, Option<usize>, bool), PaletteError> {
    if r.is_empty() {
        return err("palette reference must be a non-empty string");
    }
    let (body, reverse) = match r.strip_suffix("_r") {
        Some(b) => (b, true),
        None => (r, false),
    };
    let (name, steps) = match body.rsplit_once(':') {
        Some((n, steps)) => {
            if !is_int(steps) {
                return err(format!(
                    "palette reference steps must be a positive integer, got {}",
                    diagnostic::py_str_repr(r)
                ));
            }
            match steps.parse::<usize>() {
                Ok(k) if k > 0 => (n, Some(k)),
                _ => return err(format!("steps must be positive, got {steps}")),
            }
        }
        None => (body, None),
    };
    if name.is_empty() {
        return err(format!("palette reference is missing name: {r:?}"));
    }
    Ok((name.to_string(), steps, reverse))
}

/// `_downsample`: `n` evenly spaced stops, the midpoint of a diverging ramp skipped on an even `n`.
fn downsample(stops: &[String], n: usize, skip_midpoint_on_even: bool) -> Vec<String> {
    let len = stops.len();
    let at = |i: usize| stops.get(i).cloned().unwrap_or_default();
    if n >= len {
        return stops.to_vec();
    }
    if n == 1 {
        return vec![at(len / 2)];
    }
    // Python's `round` is half-to-even; the quotients here are exact or never halves except at
    // ties, which the `Equal` arm rounds to even.
    let round = |num: usize, den: usize| -> usize {
        let (q, r) = (num / den, num % den);
        match (2 * r).cmp(&den) {
            std::cmp::Ordering::Less => q,
            std::cmp::Ordering::Greater => q + 1,
            std::cmp::Ordering::Equal => q + (q % 2),
        }
    };
    if skip_midpoint_on_even && n.is_multiple_of(2) && len % 2 == 1 {
        let mid = len / 2;
        let half = n / 2;
        if half == 1 {
            return vec![at(0), at(len - 1)];
        }
        let left = (0..half).map(|i| round(i * (mid - 1), half - 1));
        let right = (0..half).map(|i| mid + 1 + round(i * (len - 1 - mid - 1), half - 1));
        return left.chain(right).map(at).collect();
    }
    (0..n).map(|i| at(round(i * (len - 1), n - 1))).collect()
}

/// A palette name as its list of sRGB stops, `palette()` for the default surface: `:N` takes the
/// first N stops of a categorical or scaffold palette and evenly spaces N over a continuous ramp
/// (11 when unstated), `_r` reverses, and a trailing variant moves every stop of a categorical one.
pub fn palette_list(name: &str) -> Result<Vec<String>, PaletteError> {
    let authored = name;
    let (name, variants) = split_variants(name);
    let (mut name, steps, reverse) = if name.contains(':') || name.ends_with("_r") {
        parse_reference(name)?
    } else {
        (name.to_string(), None, false)
    };
    if ["hsv", "jet", "rainbow"].contains(&name.as_str()) {
        return err(format!(
            "palette '{name}' is a known perceptual anti-pattern and is not shipped"
        ));
    }
    name = match name.as_str() {
        "RdYlGn" => "dbt-div-crimson-green".to_string(),
        "parula" => "dbt-seq-blue".to_string(),
        _ => name,
    };
    let sp = spine(&name)
        .map_err(|_| PaletteError(format!("unknown palette {name:?} in {authored:?}")))?;
    let colors = || {
        sp.colors
            .clone()
            .ok_or_else(|| PaletteError(format!("palette '{name}' has no 'colors:' array")))
    };
    let mut stops = match sp.family {
        "tone" => {
            return err(format!(
                "'{name}' is a tone palette. Use color('{name}.solid') etc., not palette()."
            ))
        }
        "pattern" => {
            return err(format!(
                "'{name}' is a pattern palette: it names fill patterns, not color stops. Set it as `style.pattern.palette`."
            ))
        }
        "categorical" | "scaffold" => {
            let mut stops = colors()?;
            if let Some(n) = steps {
                if n > stops.len() {
                    return err(format!(
                        "palette '{name}' has {} stops; cannot return {n}. Pick a different palette or reduce steps.",
                        stops.len()
                    ));
                }
                stops.truncate(n);
            }
            if reverse {
                stops.reverse();
            }
            stops
        }
        family => {
            let mut stops = downsample(&colors()?, steps.unwrap_or(11), family == "diverging");
            if reverse {
                stops.reverse();
            }
            stops
        }
    };
    if !variants.is_empty() {
        if sp.family != "categorical" {
            return err(format!(
                "unknown palette '{name}.{}'. Variants apply to categorical palettes.",
                variants.join(".")
            ));
        }
        for v in variants {
            stops = stops
                .iter()
                .map(|s| apply_variant(s, v))
                .collect::<Result<_, _>>()?;
        }
    }
    Ok(stops)
}

/// `resolve_palette_ref`: a leading theme role in a palette reference replaced by the palette the
/// theme binds, the reference's `:N`/`_r` shorthand and variant kept (the binding's variant first).
pub fn resolve_palette_ref(
    r: &str,
    palettes: &IndexMap<String, String>,
) -> Result<String, PaletteError> {
    let (base, requested) = split_variant(r);
    let (name, _, _) = parse_reference(base)?;
    let Some(target) = palettes.get(&name) else {
        return Ok(r.to_string());
    };
    let (target_name, bound) = split_variant(target);
    let mut substituted = format!(
        "{target_name}{}",
        base.get(name.len()..).unwrap_or_default()
    );
    for v in [bound, requested].into_iter().flatten() {
        substituted = format!("{substituted}.{v}");
    }
    Ok(substituted)
}

/// Carry the categorical palette a board authors into the theme-derived `context` its tokens
/// resolve against, so a bare `category[N]` indexes the board's palette.
pub fn adopt_board_palette(
    context: &mut crate::style::style::StylePatch,
    own: &crate::style::style::StylePatch,
) {
    let model_core::Tri::Value(authored) = &own.charts.color.categorical else {
        return;
    };
    let Some(palette) = &authored.palette else {
        return;
    };
    if let model_core::Tri::Value(theirs) = &mut context.charts.color.categorical {
        theirs.palette = Some(palette.clone());
    }
}

/// What the generated color pass calls: the palette bindings and roles of the patch being
/// resolved, and one token at a time.
pub mod hook {
    use super::{palette_list, resolve_palette_ref, resolve_token, ThemeColors};
    use crate::style::style::StylePatch;
    use indexmap::IndexMap;

    pub struct Ctx {
        palettes: IndexMap<String, String>,
        roles: IndexMap<String, String>,
        /// The theme's single-series ink list, what `single_series[N]` indexes.
        single_series: Option<Vec<String>>,
        /// The board's categorical palette, what `category[N]` indexes; the error when the list
        /// the board authors does not resolve, which every token then reports.
        category: Result<Option<Vec<String>>, String>,
    }

    impl Ctx {
        fn theme(&self) -> Result<ThemeColors<'_>, String> {
            Ok(ThemeColors {
                palettes: &self.palettes,
                roles: &self.roles,
                single_series: self.single_series.as_deref(),
                category: self.category.as_ref().map_err(Clone::clone)?.as_deref(),
            })
        }
    }

    pub fn ctx(style: &StylePatch) -> Ctx {
        let palettes = style
            .palettes
            .iter()
            .filter_map(|(k, v)| Some((k.clone(), v.as_ref()?.as_str().to_string())))
            .collect();
        let roles = style
            .roles
            .iter()
            .filter_map(|(k, v)| Some((k.clone(), v.as_ref()?.clone())))
            .collect();
        let single_series = single_series_list(style, &palettes, &roles);
        let category = category_list(style, &palettes, &roles, single_series.as_deref());
        Ctx {
            palettes,
            roles,
            single_series,
            category,
        }
    }

    /// The categorical palette the patch authors, as colors: a palette name's stops, or the listed
    /// colors with their tokens resolved against the role, never against the list itself, and the
    /// canvas token `theme.background` kept for the board's canvas. `None` when it authors none.
    fn category_list(
        style: &StylePatch,
        palettes: &IndexMap<String, String>,
        roles: &IndexMap<String, String>,
        single_series: Option<&[String]>,
    ) -> Result<Option<Vec<String>>, String> {
        let model_core::Tri::Value(categorical) = &style.charts.color.categorical else {
            return Ok(None);
        };
        let Some(leaf) = categorical.palette.as_ref() else {
            return Ok(None);
        };
        match PaletteLeaf::name(leaf) {
            Some(name) => {
                let r = resolve_palette_ref(&name, palettes).map_err(|e| e.0)?;
                palette_list(&r)
                    .map(Some)
                    .map_err(|e| format!("unknown palette {name:?}: {}", e.0))
            }
            None => {
                let theme = ThemeColors {
                    palettes,
                    roles,
                    single_series,
                    category: None,
                };
                PaletteLeaf::colors(leaf)
                    .iter()
                    .map(|c| {
                        if c != "theme.background" && color::is_color_token(c) {
                            resolve_token(c, &theme).map_err(|e| e.0)
                        } else {
                            Ok(c.clone())
                        }
                    })
                    .collect::<Result<_, _>>()
                    .map(Some)
            }
        }
    }

    /// The single-series palette as colors: a palette name's stops, or the listed colors with
    /// their tokens resolved (`single_series[N]` inside it has no list to index). `None` when it
    /// does not resolve; its own leaf pass then refuses the token.
    fn single_series_list(
        style: &StylePatch,
        palettes: &IndexMap<String, String>,
        roles: &IndexMap<String, String>,
    ) -> Option<Vec<String>> {
        let model_core::Tri::Value(categorical) = &style.charts.color.categorical else {
            return None;
        };
        let leaf = categorical.single_series_palette.as_ref()?;
        match PaletteLeaf::name(leaf) {
            Some(name) => palette_list(&resolve_palette_ref(&name, palettes).ok()?).ok(),
            None => {
                let theme = ThemeColors {
                    palettes,
                    roles,
                    single_series: None,
                    category: None,
                };
                PaletteLeaf::colors(leaf)
                    .iter()
                    .map(|c| resolve_token(c, &theme).ok())
                    .collect()
            }
        }
    }

    /// `None` for a string that is neither a token nor a bare theme role, or the canvas token
    /// `theme.background`, which stays until the board's canvas is known.
    pub fn resolve(ctx: &Ctx, s: &str) -> Result<Option<String>, String> {
        if s == "theme.background" {
            return Ok(None);
        }
        if !color::is_color_token(s) && !ctx.roles.contains_key(s) {
            return Ok(None);
        }
        let theme = ctx.theme()?;
        resolve_token(s, &theme).map(Some).map_err(|e| e.0)
    }

    /// As [`resolve`] inside `annotations`, where `theme.series` stands for the series' ink and is
    /// left for the annotation's own chart to substitute.
    pub fn resolve_annotation(ctx: &Ctx, s: &str) -> Result<Option<String>, String> {
        if s == "theme.series" {
            return Ok(None);
        }
        resolve(ctx, s)
    }

    /// As [`resolve`], for a field that may name a query column: a bare word there is the column,
    /// never a role.
    pub fn resolve_column(ctx: &Ctx, s: &str) -> Result<Option<String>, String> {
        if s == "theme.background" || !color::is_color_token(s) {
            return Ok(None);
        }
        let theme = ctx.theme()?;
        resolve_token(s, &theme).map(Some).map_err(|e| e.0)
    }

    /// A palette field of a categorical color style: a name (or a theme role) becomes its stops;
    /// a list keeps its colors, each resolved as a token (a bare word is never a role there).
    pub fn palette_leaf<L: PaletteLeaf>(ctx: &Ctx, slot: &mut Option<L>) -> Result<(), String> {
        let Some(leaf) = slot.as_ref() else {
            return Ok(());
        };
        let stops = match leaf.name() {
            Some(name) => {
                let r = resolve_palette_ref(&name, &ctx.palettes)
                    .map_err(|e| named_unknown(&name, e))?;
                palette_list(&r).map_err(|e| named_unknown(&name, e))?
            }
            None => {
                // These colors define a palette, so `category[N]` among them names the role's
                // palette, never the list being defined.
                let theme = ThemeColors {
                    palettes: &ctx.palettes,
                    roles: &ctx.roles,
                    single_series: ctx.single_series.as_deref(),
                    category: None,
                };
                let mut colors = leaf.colors();
                for c in &mut colors {
                    if c != "theme.background" && color::is_color_token(c) {
                        *c = resolve_token(c, &theme).map_err(|e| e.0)?;
                    }
                }
                colors
            }
        };
        *slot = Some(L::from_colors(stops)?);
        Ok(())
    }

    /// The refusal for a palette field that names nothing usable: `unknown palette "<authored>":
    /// <why>` when no palette answers to it, `unusable palette "<authored>": <why>` when one does
    /// but cannot serve as written (a tone, an over-request, a malformed `:N`).
    pub fn named_unknown(name: &str, e: super::PaletteError) -> String {
        let lead = if e.is_unknown() {
            UNKNOWN_PALETTE
        } else {
            UNUSABLE_PALETTE
        };
        format!("{lead}{name:?}: {}", e.0)
    }

    /// The start of a refusal [`named_unknown`] words; the authored name follows, quoted.
    pub const UNKNOWN_PALETTE: &str = "unknown palette ";
    /// As [`UNKNOWN_PALETTE`], for a palette that exists but cannot be used as written.
    pub const UNUSABLE_PALETTE: &str = "unusable palette ";

    /// The authored name in a [`named_unknown`] refusal, `None` for any other.
    pub fn unknown_name(why: &str) -> Option<&str> {
        quoted_name(why, UNKNOWN_PALETTE).map(|(name, _)| name)
    }

    /// The authored name and the palette's own reason in an unusable-palette refusal.
    pub fn unusable_name(why: &str) -> Option<(&str, &str)> {
        quoted_name(why, UNUSABLE_PALETTE)
    }

    fn quoted_name<'a>(why: &'a str, lead: &str) -> Option<(&'a str, &'a str)> {
        why.strip_prefix(lead)?
            .strip_prefix('"')?
            .split_once("\": ")
    }

    /// A palette name in a slot that bakes it later (a gradient, a scale): a theme role is
    /// replaced by the palette it binds, and the name must still be a shipped palette or a Vega
    /// scheme. Returns the name to keep.
    pub fn name_only_ref(ctx: &Ctx, name: &str) -> Result<String, String> {
        let r = resolve_palette_ref(name, &ctx.palettes).map_err(|e| named_unknown(name, e))?;
        if d3rs::vega_schemes::scheme(&r).is_none() {
            palette_list(&r).map_err(|e| named_unknown(name, e))?;
        }
        Ok(r)
    }

    /// [`name_only_ref`] with no theme bindings: the refusal for a name no shipped palette or
    /// Vega scheme serves.
    pub fn name_only_check(name: &str) -> Result<(), String> {
        let ctx = Ctx {
            palettes: IndexMap::new(),
            roles: IndexMap::new(),
            single_series: None,
            category: Ok(None),
        };
        name_only_ref(&ctx, name).map(drop)
    }

    /// The refusal for a name that is no shipped palette's stops (a Vega scheme is no answer).
    pub fn name_check(name: &str) -> Result<(), String> {
        palette_list(name)
            .map(drop)
            .map_err(|e| named_unknown(name, e))
    }

    /// What Python's model validators say of a palette name before any theme role is known:
    /// `Some(text)` is the `ValueError` they raise, `None` is a name they accept or defer (a bare
    /// word may still be a theme role). `gradient` is a gradient or scale slot, which also takes a
    /// Vega scheme and words a dash-digit variant of one.
    pub fn python_refusal(authored: &str, gradient: bool) -> Option<String> {
        if gradient && d3rs::vega_schemes::scheme(authored).is_some() {
            return None;
        }
        let Err(e) = palette_list(authored) else {
            return None;
        };
        if !e.is_unknown() {
            return Some(e.0);
        }
        if gradient {
            if let Some((base, steps)) = authored.rsplit_once('-') {
                if d3rs::vega_schemes::scheme(base).is_some()
                    && !steps.is_empty()
                    && steps.chars().all(|c| c.is_ascii_digit())
                {
                    return Some(format!(
                        "palette '{authored}' requests a {steps}-step discrete variant of the '{base}' scheme. dbt charts does not support bucketed/quantized color scales yet \u{2014} use the continuous scheme '{base}' instead."
                    ));
                }
            }
        }
        let role = !authored.is_empty()
            && !authored.contains(':')
            && !authored.starts_with('#')
            && d3rs::vega_schemes::scheme(authored).is_none()
            && !["hsv", "jet", "rainbow"].contains(&authored);
        if role {
            return None;
        }
        Some(match e.0.strip_prefix("unknown palette \"") {
            Some(rest) => match rest.split_once('"') {
                Some((name, _)) => unknown_message(name, authored),
                None => e.0.clone(),
            },
            None if e.0.contains("is a known perceptual anti-pattern") => format!(
                "{}. See https://docs.dbtcharts.com/guides/palette-resolver/#anti-patterns",
                e.0
            ),
            None => e.0,
        })
    }

    /// `_unknown_palette_message`: the retirement hint for a retired spelling of `authored`, else
    /// the shipped palette `name` is closest to.
    fn unknown_message(name: &str, authored: &str) -> String {
        if let Some(now) = migrate::respell_retired_variant(authored) {
            return format!(
                "unknown palette '{name}'. '{authored}' was retired; use '{now}' instead."
            );
        }
        let names: Vec<&str> = match super::catalog() {
            Ok(c) => c.keys().map(String::as_str).collect(),
            Err(_) => Vec::new(),
        };
        match diagnostic::hints::close_match_at(name, &names, 0.6) {
            Some(near) => format!("unknown palette '{name}'. Did you mean '{near}'?"),
            None => format!("unknown palette '{name}'"),
        }
    }

    /// A palette-or-colors leaf type.
    pub trait PaletteLeaf: Sized {
        /// The palette name, when it is one.
        fn name(&self) -> Option<String>;
        /// The colors, when it is a list.
        fn colors(&self) -> Vec<String>;
        fn from_colors(colors: Vec<String>) -> Result<Self, String>;
    }

    impl PaletteLeaf for crate::primitives::PaletteChoice {
        fn name(&self) -> Option<String> {
            match self {
                Self::Name(n) => Some(n.as_str().to_string()),
                Self::Colors(_) => None,
            }
        }
        fn colors(&self) -> Vec<String> {
            match self {
                Self::Colors(c) => c.iter().map(|c| c.as_str().to_string()).collect(),
                Self::Name(_) => Vec::new(),
            }
        }
        fn from_colors(colors: Vec<String>) -> Result<Self, String> {
            colors
                .into_iter()
                .map(crate::facet::Color::try_from)
                .collect::<Result<_, _>>()
                .map(Self::Colors)
        }
    }

    impl PaletteLeaf for crate::primitives::SeriesInk {
        fn name(&self) -> Option<String> {
            self.0.name()
        }
        fn colors(&self) -> Vec<String> {
            self.0.colors()
        }
        fn from_colors(colors: Vec<String>) -> Result<Self, String> {
            crate::primitives::PaletteChoice::from_colors(colors).map(Self)
        }
    }

    /// A gradient's or a scale's palette: the name is baked later, so only a theme role is replaced
    /// by the palette it binds.
    pub fn palette_name_leaf<L: NameOnlyLeaf>(
        ctx: &Ctx,
        slot: &mut Option<L>,
    ) -> Result<(), String> {
        let Some(leaf) = slot.as_ref() else {
            return Ok(());
        };
        let Some(name) = leaf.name() else {
            let mut colors = leaf.colors();
            for c in &mut colors {
                if let Some(resolved) = resolve_column(ctx, c)? {
                    *c = resolved;
                }
            }
            if let Some(updated) = leaf.with_colors(colors)? {
                *slot = Some(updated);
            }
            return Ok(());
        };
        let r = name_only_ref(ctx, &name)?;
        if r != name {
            *slot = Some(L::with_name(r)?);
        }
        Ok(())
    }

    /// A palette-name-or-colors leaf whose name survives.
    pub trait NameOnlyLeaf: Sized {
        fn name(&self) -> Option<String>;
        /// The colors, when the leaf is a list of them.
        fn colors(&self) -> Vec<String>;
        fn with_name(name: String) -> Result<Self, String>;
        /// The leaf with its colors replaced; `None` when it holds no color list.
        fn with_colors(&self, colors: Vec<String>) -> Result<Option<Self>, String>;
    }

    impl NameOnlyLeaf for crate::primitives::ScalePalette {
        fn name(&self) -> Option<String> {
            match self {
                Self::Name(n) => Some(n.as_str().to_string()),
                _ => None,
            }
        }
        fn colors(&self) -> Vec<String> {
            match self {
                Self::Colors(c) => c.iter().map(|c| c.as_str().to_string()).collect(),
                _ => Vec::new(),
            }
        }
        fn with_colors(&self, colors: Vec<String>) -> Result<Option<Self>, String> {
            match self {
                Self::Colors(_) => colors
                    .into_iter()
                    .map(crate::facet::Color::try_from)
                    .collect::<Result<_, _>>()
                    .map(|c| Some(Self::Colors(c))),
                _ => Ok(None),
            }
        }
        fn with_name(name: String) -> Result<Self, String> {
            crate::facet::SchemeName::try_from(name).map(Self::Name)
        }
    }

    impl NameOnlyLeaf for crate::primitives::ColumnPalette {
        fn name(&self) -> Option<String> {
            match self {
                Self::Name(n) => Some(n.as_str().to_string()),
                Self::Colors(_) => None,
            }
        }
        fn colors(&self) -> Vec<String> {
            match self {
                Self::Colors(c) => c.iter().map(|c| c.as_str().to_string()).collect(),
                Self::Name(_) => Vec::new(),
            }
        }
        fn with_colors(&self, colors: Vec<String>) -> Result<Option<Self>, String> {
            match self {
                Self::Colors(_) => colors
                    .into_iter()
                    .map(crate::facet::Color::try_from)
                    .collect::<Result<_, _>>()
                    .map(|c| Some(Self::Colors(c))),
                Self::Name(_) => Ok(None),
            }
        }
        fn with_name(name: String) -> Result<Self, String> {
            crate::facet::SchemeName::try_from(name).map(Self::Name)
        }
    }
}
