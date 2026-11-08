//! Facet newtypes (spec § 4). Each validates as its Python facet does
//! (`models/markers.py`, `core/colors.py`, `core/fonts.py`) and fails with
//! `ERR-VALIDATION-FIELD`; the parse pass fills `field_path`, which a scalar
//! cannot know.
//!
//! `Palette`, `Url`, `Markdown`, `DisplayText` and `Channel` carry
//! meaning, not a shape: Python checks none of them at the field, and the
//! passes that know the context (formats, palettes, refs) check them later.
//! They still go through `TryFrom` so every facet is one parse call.
//!
//! A field that also takes `auto` (a chart title font) is `Engine<Facet, Auto>`.

use diagnostic::py_repr;
use serde_json::Value;

/// What a field means beyond what it holds; the schema emitter reads it.
pub trait Facet {
    const FACET: &'static str;
}

/// Pydantic's literal-mismatch message: `Input should be 'a', 'b' or 'c'`.
pub fn one_of(values: &[&str]) -> String {
    let q: Vec<String> = values.iter().map(|v| format!("'{v}'")).collect();
    let list = match q.split_last() {
        Some((last, rest)) if !rest.is_empty() => format!("{} or {last}", rest.join(", ")),
        Some((last, _)) => last.clone(),
        None => String::new(),
    };
    format!("Input should be {list}")
}

fn repr(s: &str) -> String {
    py_repr(&Value::String(s.to_string()))
}

/// The `pattern` of a color string, Python's `core/colors.py` shape check: a hex, an identifier,
/// a palette token or a color function.
const COLOR_PATTERN: &str = r##"(?:^$)|(?:^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$)|(?:^[A-Za-z_][A-Za-z0-9_-]*$)|(?:^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+|\[\d+\])(\.(dark|light|pale|deep))?$)|(?:^(?:[rR][gG][bB]|[rR][gG][bB][aA]|[hH][sS][lL]|[hH][sS][lL][aA]|[oO][kK][lL][cC][hH]|[oO][kK][lL][aA][bB]|[lL][aA][bB]|[lL][cC][hH]|[cC][oO][lL][oO][rR]|[vV][aA][rR])\([^;{}<>"'\\*\x00-\x1f\x7f]*\)$)"##;

/// A font family or stack: no character that could end the CSS declaration.
const FONT_FAMILY_PATTERN: &str = r##"^(?!auto$)[^;{}<>"\\*\x00-\x1f\x7f]*$"##;

/// The same under an `Engine` that takes `auto` before the facet.
const FONT_FAMILY_PATTERN_WITH_AUTO: &str = r##"^[^;{}<>"\\*\x00-\x1f\x7f]*$"##;

macro_rules! facet {
    ($(#[$m:meta])* $name:ident = $facet:literal, $check:expr $(, $keywords:expr)?) => {
        $(#[$m])*
        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct $name(String);

        impl Facet for $name {
            const FACET: &'static str = $facet;
        }

        impl $name {
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl TryFrom<&str> for $name {
            type Error = String;
            fn try_from(v: &str) -> Result<Self, String> {
                let check: fn(&str) -> Result<String, String> = $check;
                check(v).map(Self)
            }
        }

        impl AsRef<str> for $name {
            fn as_ref(&self) -> &str {
                &self.0
            }
        }

        impl TryFrom<String> for $name {
            type Error = String;
            fn try_from(v: String) -> Result<Self, String> {
                Self::try_from(v.as_str())
            }
        }

        model_core::facet!($name, $facet, Str $(, $keywords)?);
        model_core::from_scalar!($name, String);
    };
}

pub(crate) use facet;

fn unchecked(v: &str) -> Result<String, String> {
    Ok(v.to_string())
}

facet!(
    /// A CSS color, a palette token or a color function. Shape only: a bare
    /// identifier passes, since whether it names a color is a later check.
    Color = "color",
    color,
    model_core::Keywords::pattern(COLOR_PATTERN)
);
facet!(
    /// A color string whose schema states no pattern: pydantic checks a box border's color in a
    /// validator, not in its schema, and the two emit the same document shape.
    BorderColor = "color",
    color
);
facet!(
    /// A table cell color, or the name of a query column that holds one. Neither
    /// shape- nor name-checked: whether it names a column is a later check.
    ColorOrColumn = "color_or_column",
    unchecked
);
impl From<Color> for ColorOrColumn {
    /// A named font's color fills a table column's font color as written.
    fn from(c: Color) -> Self {
        ColorOrColumn(c.as_str().to_string())
    }
}

/// The names `format` takes beside a d3 spec: the engine's predefined formats, plus `email` and
/// `url`, which render text and have no d3 spec.
pub const FORMAT_NAMES: &[&str] = &[
    "compact",
    "currency",
    "currency_compact",
    "currency_full",
    "currency_whole",
    "date_short",
    "delta",
    "email",
    "id",
    "integer",
    "latitude",
    "longitude",
    "number",
    "number_full",
    "percent",
    "percent_delta",
    "percent_whole",
    "rating",
    "time_short",
    "url",
    "year",
];

/// A string that is one of a closed list of names or else any string its check admits. The
/// schema lists the names before the free string, as pydantic's `Literal[...] | str` does.
macro_rules! open_names {
    ($(#[$m:meta])* $name:ident = $facet:literal, $names:expr, $check:expr $(, $keywords:expr)?) => {
        $(#[$m])*
        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct $name(String);

        impl Facet for $name {
            const FACET: &'static str = $facet;
        }

        impl $name {
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl TryFrom<&str> for $name {
            type Error = String;
            fn try_from(v: &str) -> Result<Self, String> {
                let check: fn(&str) -> Result<String, String> = $check;
                check(v).map(Self)
            }
        }

        impl AsRef<str> for $name {
            fn as_ref(&self) -> &str {
                &self.0
            }
        }

        impl TryFrom<String> for $name {
            type Error = String;
            fn try_from(v: String) -> Result<Self, String> {
                Self::try_from(v.as_str())
            }
        }

        impl model_core::Leaf for $name {
            type Whole = $name;
            type Authored = Option<$name>;
            type Patch = Option<$name>;
            const MERGE: model_core::Merge = model_core::Merge::Override;
            const EXPR: model_core::TypeExpr = model_core::TypeExpr::Either(
                &model_core::TypeExpr::OneOf($names),
                open_names!(@free $facet $(, $keywords)?),
            );
        }
        model_core::build_leaf!($name);
        model_core::from_scalar!($name, String);
    };
    (@free $facet:literal) => {
        &model_core::TypeExpr::Facet($facet, model_core::Scalar::Str)
    };
    (@free $facet:literal, $keywords:expr) => {
        &model_core::TypeExpr::Keyed(
            &$keywords,
            &model_core::TypeExpr::Facet($facet, model_core::Scalar::Str),
        )
    };
}

pub(crate) use open_names;

open_names!(
    /// A number or time format: a predefined name, an alias or a d3 spec.
    FormatName = "format",
    FORMAT_NAMES,
    unchecked
);

facet!(
    /// A font family or stack; entries that are not CSS identifiers are quoted.
    FontFamily = "font_family",
    font_family,
    model_core::Keywords::pattern(FONT_FAMILY_PATTERN)
        .in_engine(model_core::InEngine::Pattern(FONT_FAMILY_PATTERN_WITH_AUTO))
);
open_names!(
    /// A palette name, or (as a list elsewhere) its stops.
    Palette = "palette",
    crate::names::StopsPaletteName::VALUES,
    unchecked
);
open_names!(
    /// A scale's palette: a palette name or Vega scheme.
    SchemeName = "scheme_name",
    crate::names::ScalePaletteName::VALUES,
    unchecked
);
facet!(
    /// A link target: a URL or an author-space board path.
    Url = "url",
    unchecked
);
facet!(
    /// A markdown body.
    Markdown = "markdown",
    unchecked
);
facet!(
    /// Prose written for a reader.
    DisplayText = "display_text",
    unchecked
);
facet!(
    /// The name of a column of the chart's query.
    Channel = "channel",
    unchecked
);

facet!(
    /// A Jinja `where:` filter: a string with a character other than whitespace
    /// or a brace. Whether it compiles is the template pass's check.
    LabelWhere = "label_where",
    label_where,
    model_core::Keywords::pattern(r"[^\s{}]")
);

/// A font weight: a name (`bold`) or a number (`700`).
#[derive(Debug, Clone, PartialEq)]
pub enum FontWeightValue {
    Name(String),
    Number(f64),
}

impl Facet for FontWeightValue {
    const FACET: &'static str = "font_weight";
}

fn reject_auto(v: &str) -> Result<(), String> {
    if v == "auto" {
        return Err("'auto' is only valid on a chart title font".to_string());
    }
    Ok(())
}

impl TryFrom<&str> for FontWeightValue {
    type Error = String;
    fn try_from(v: &str) -> Result<Self, String> {
        reject_auto(v)?;
        Ok(Self::Name(v.to_string()))
    }
}

impl TryFrom<f64> for FontWeightValue {
    type Error = String;
    fn try_from(v: f64) -> Result<Self, String> {
        Ok(Self::Number(v))
    }
}

impl model_core::ReadScalar for FontWeightValue {
    fn read(node: &model_core::Node, cx: &mut model_core::Cx) -> Option<Self> {
        if let Ok(Some(n)) = cx.attempt(|cx| f64::read(node, cx)) {
            return Some(Self::Number(n));
        }
        let name = String::read(node, cx)?;
        match Self::try_from(name.as_str()) {
            Ok(v) => Some(v),
            Err(e) => {
                cx.invalid(node.span, e.to_string());
                None
            }
        }
    }
}

impl model_core::FromNode for FontWeightValue {
    fn authored(node: &model_core::Node, cx: &mut model_core::Cx) -> Self::Authored {
        <Self as model_core::ReadScalar>::read(node, cx)
    }
    fn patch(node: &model_core::Node, cx: &mut model_core::Cx) -> Self::Patch {
        <Self as model_core::ReadScalar>::read(node, cx)
    }
    fn whole(node: &model_core::Node, cx: &mut model_core::Cx) -> Option<Self::Whole> {
        <Self as model_core::ReadScalar>::read(node, cx)
    }
}

impl model_core::Leaf for FontWeightValue {
    type Whole = FontWeightValue;
    type Authored = Option<FontWeightValue>;
    type Patch = Option<FontWeightValue>;
    const MERGE: model_core::Merge = model_core::Merge::Override;
    const EXPR: model_core::TypeExpr = model_core::TypeExpr::Keyed(
        &model_core::Keywords::pattern("^(?!auto$)").in_engine(model_core::InEngine::Drop),
        &model_core::TypeExpr::Either(
            &model_core::TypeExpr::Facet(
                <FontWeightValue as Facet>::FACET,
                model_core::Scalar::Str,
            ),
            &model_core::TypeExpr::Scalar(model_core::Scalar::Float),
        ),
    );
}
model_core::build_leaf!(FontWeightValue);

const FONT_WEIGHT_NAMES: [&str; 11] = [
    "normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900",
];

const FONT_WEIGHT_LITERALS: [model_core::Value; 20] = {
    use model_core::Value::{Int, Str};
    [
        Str("normal"),
        Str("bold"),
        Str("100"),
        Str("200"),
        Str("300"),
        Str("400"),
        Str("500"),
        Str("600"),
        Str("700"),
        Str("800"),
        Str("900"),
        Int(100),
        Int(200),
        Int(300),
        Int(400),
        Int(500),
        Int(600),
        Int(700),
        Int(800),
        Int(900),
    ]
};

const FONT_WEIGHT_MESSAGE: &str = "Input should be 'normal', 'bold', '100', '200', '300', '400', '500', '600', '700', '800', '900', 100, 200, 300, 400, 500, 600, 700, 800 or 900";

/// A font weight from Python's closed `FontWeight` literal: `normal`, `bold`, a hundreds name or
/// the hundreds number. Where a weight is any name or number, it is a [`FontWeightValue`].
#[derive(Debug, Clone, PartialEq)]
pub struct FontWeight(pub FontWeightValue);

impl TryFrom<&str> for FontWeight {
    type Error = String;
    fn try_from(v: &str) -> Result<Self, String> {
        if FONT_WEIGHT_NAMES.contains(&v) {
            return Ok(Self(FontWeightValue::Name(v.to_string())));
        }
        Err(FONT_WEIGHT_MESSAGE.to_string())
    }
}

impl TryFrom<f64> for FontWeight {
    type Error = String;
    fn try_from(v: f64) -> Result<Self, String> {
        let hundreds = (100..=900).step_by(100).any(|n| f64::from(n) == v);
        if hundreds {
            return Ok(Self(FontWeightValue::Number(v)));
        }
        Err(FONT_WEIGHT_MESSAGE.to_string())
    }
}

impl model_core::ReadScalar for FontWeight {
    fn read(node: &model_core::Node, cx: &mut model_core::Cx) -> Option<Self> {
        let read = if let Ok(Some(n)) = cx.attempt(|cx| f64::read(node, cx)) {
            Self::try_from(n)
        } else {
            Self::try_from(String::read(node, cx)?.as_str())
        };
        read.map_err(|e| cx.invalid(node.span, e)).ok()
    }
}

impl model_core::FromNode for FontWeight {
    fn authored(node: &model_core::Node, cx: &mut model_core::Cx) -> Self::Authored {
        <Self as model_core::ReadScalar>::read(node, cx)
    }
    fn patch(node: &model_core::Node, cx: &mut model_core::Cx) -> Self::Patch {
        <Self as model_core::ReadScalar>::read(node, cx)
    }
    fn whole(node: &model_core::Node, cx: &mut model_core::Cx) -> Option<Self::Whole> {
        <Self as model_core::ReadScalar>::read(node, cx)
    }
}

impl model_core::Leaf for FontWeight {
    type Whole = FontWeight;
    type Authored = Option<FontWeight>;
    type Patch = Option<FontWeight>;
    const MERGE: model_core::Merge = model_core::Merge::Override;
    const EXPR: model_core::TypeExpr = model_core::TypeExpr::Literals(&FONT_WEIGHT_LITERALS);
}
model_core::build_leaf!(FontWeight);

fn is_ident(s: &str) -> bool {
    let mut cs = s.chars();
    matches!(cs.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && cs.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

fn color(v: &str) -> Result<String, String> {
    if color::is_authored_color(v) {
        return Ok(v.to_string());
    }
    Err(format!(
        "{} is not a color: use hex, a CSS color name, a palette token (palette.slot), \
         or a color function such as rgb() or oklch()",
        repr(v)
    ))
}

fn font_family(stack: &str) -> Result<String, String> {
    reject_auto(stack)?;
    if stack.chars().any(|c| {
        matches!(c, ';' | '{' | '}' | '<' | '>' | '"' | '\\' | '*') || c.is_ascii_control()
    }) {
        return Err(format!(
            "{} is not a font family: remove any of ; {{ }} < > \" \\ * or a control character",
            repr(stack)
        ));
    }
    if stack.trim().is_empty() {
        return Ok(stack.to_string());
    }
    let mut entries = Vec::new();
    for raw in stack.split(',') {
        let entry = raw.trim();
        if entry.is_empty() {
            return Err(format!("{} has an empty entry", repr(stack)));
        }
        let quoted = |e: &str| format!("'{e}'");
        let entry = if let Some(inner) = entry.strip_prefix('\'') {
            match inner.strip_suffix('\'') {
                Some(body) if !body.is_empty() && !body.contains('\'') => entry.to_string(),
                _ => {
                    return Err(format!(
                        "{} in {} is not a quoted family name",
                        repr(entry),
                        repr(stack)
                    ))
                }
            }
        } else if entry.contains('\'') {
            return Err(format!(
                "{} in {} has a stray quote",
                repr(entry),
                repr(stack)
            ));
        } else if entry
            .split_whitespace()
            .all(|w| is_ident(w.strip_prefix('-').unwrap_or(w)))
        {
            entry.to_string()
        } else {
            quoted(entry)
        };
        entries.push(entry);
    }
    Ok(entries.join(", "))
}

fn label_where(v: &str) -> Result<String, String> {
    if v.chars()
        .any(|c| !c.is_whitespace() && c != '{' && c != '}')
    {
        return Ok(v.to_string());
    }
    Err("where: must be a non-empty expression or be omitted".to_string())
}
