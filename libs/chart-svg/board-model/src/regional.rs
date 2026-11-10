//! `primitives.py` (`NumberLocale`, `TimeLocale`, `RegionalLocale`, `Regional`): how a board
//! writes numbers and times. A locale is a bundled name or a full d3 definition; the root
//! board's `regional:` takes both. Each defaulted field is `Option`, the default stated in
//! `absent`: normalize fills it.

use crate::names::{NumberLocaleName, TimeLocaleName, TimeZoneName};
use crate::string_enum;
use diagnostic::{diag, Diagnostic};
use model_core::{
    build_leaf, from_scalar, Bounds, Cx, FromNode, Keywords, Kind, Leaf, Merge, Node, ReadScalar,
    Scalar, Spell, TypeExpr,
};
use model_core::{Authored, Tri};
use model_derive::Model;
use regex::Regex;

string_enum!(
    /// ISO 4217 codes `regional.currency` accepts.
    CurrencyCode {
        Usd = "USD", Eur = "EUR", Gbp = "GBP", Jpy = "JPY", Cad = "CAD", Aud = "AUD", Chf = "CHF",
        Sek = "SEK", Nok = "NOK", Dkk = "DKK", Inr = "INR", Cny = "CNY", Brl = "BRL", Mxn = "MXN"
    }
);

string_enum!(
    /// The languages of the board's generated words (tab names, series counts).
    LanguageCode {
        En = "en", De = "de", Fr = "fr", Es = "es", It = "it", Pt = "pt", Nl = "nl", Sv = "sv",
        Da = "da", Nb = "nb", Fi = "fi", Pl = "pl", Cs = "cs", Ru = "ru", Uk = "uk", Tr = "tr",
        Ja = "ja", Zh = "zh", Ko = "ko"
    }
);

string_enum!(
    /// A day of the week.
    WeekdayName {
        Sunday = "sunday", Monday = "monday", Tuesday = "tuesday", Wednesday = "wednesday",
        Thursday = "thursday", Friday = "friday", Saturday = "saturday"
    }
);

macro_rules! leaf {
    ($t:ty, $expr:expr) => {
        impl FromNode for $t {
            fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
                Self::read(node, cx)
            }
            fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
                Self::read(node, cx)
            }
            fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
                Self::read(node, cx)
            }
        }
        impl Leaf for $t {
            type Whole = $t;
            type Authored = Option<$t>;
            type Patch = Option<$t>;
            const MERGE: Merge = Merge::Override;
            const EXPR: TypeExpr = $expr;
        }
        build_leaf!($t);
    };
}

const STR: TypeExpr = TypeExpr::Scalar(Scalar::Str);
const WORD: TypeExpr = TypeExpr::Keyed(&Keywords::pattern(r"\S"), &STR);

/// An integer above zero (`PositiveInt`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PositiveInt(u32);

impl TryFrom<u32> for PositiveInt {
    type Error = String;
    fn try_from(v: u32) -> Result<Self, String> {
        if v > 0 {
            Ok(Self(v))
        } else {
            Err("Input should be greater than 0".to_string())
        }
    }
}
impl Spell for PositiveInt {
    fn spell(&self) -> serde_json::Value {
        self.0.spell()
    }
}
from_scalar!(PositiveInt, u32);
impl Leaf for PositiveInt {
    type Whole = PositiveInt;
    type Authored = Option<PositiveInt>;
    type Patch = Option<PositiveInt>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Keyed(
        &Keywords::bounded(Bounds {
            gt: Some(0.0),
            ..Bounds::NONE
        }),
        &TypeExpr::Scalar(Scalar::Int),
    );
}
build_leaf!(PositiveInt);

/// A calendar month, 1 to 12.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Month(u32);

impl TryFrom<u32> for Month {
    type Error = String;
    fn try_from(v: u32) -> Result<Self, String> {
        match v {
            0 => Err("Input should be greater than or equal to 1".to_string()),
            1..=12 => Ok(Self(v)),
            _ => Err("Input should be less than or equal to 12".to_string()),
        }
    }
}
impl Spell for Month {
    fn spell(&self) -> serde_json::Value {
        self.0.spell()
    }
}
from_scalar!(Month, u32);
impl Leaf for Month {
    type Whole = Month;
    type Authored = Option<Month>;
    type Patch = Option<Month>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Keyed(
        &Keywords::bounded(Bounds {
            ge: Some(1.0),
            le: Some(12.0),
            ..Bounds::NONE
        }),
        &TypeExpr::Scalar(Scalar::Int),
    );
}
build_leaf!(Month);

/// The group sizes of a number locale: at least one.
#[derive(Debug, Clone, PartialEq)]
pub struct Grouping(pub Vec<PositiveInt>);

impl ReadScalar for Grouping {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let sizes = <Vec<PositiveInt> as FromNode>::whole(node, cx)?;
        if sizes.is_empty() {
            cx.invalid(
                node.span,
                "List should have at least 1 item after validation, not 0",
            );
            return None;
        }
        Some(Self(sizes))
    }
}
impl Spell for Grouping {
    fn spell(&self) -> serde_json::Value {
        self.0.spell()
    }
}
leaf!(
    Grouping,
    TypeExpr::Keyed(
        &Keywords::at_least_items(1),
        &TypeExpr::List(&<PositiveInt as Leaf>::EXPR)
    )
);

/// A fixed-length list of strings, each passing `$ok`.
macro_rules! strings {
    ($(#[$m:meta])* $name:ident, $n:literal, $item:expr, $ok:expr, $what:literal) => {
        $(#[$m])*
        #[derive(Debug, Clone, PartialEq)]
        pub struct $name(pub [String; $n]);

        impl ReadScalar for $name {
            fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
                let Kind::Seq(items) = &node.kind else {
                    cx.mismatch(node, $what);
                    return None;
                };
                if items.len() != $n {
                    cx.mismatch(node, $what);
                    return None;
                }
                let ok: fn(&str) -> bool = $ok;
                let mut out = Vec::with_capacity($n);
                for item in items {
                    let s = String::read(item, cx)?;
                    if !ok(&s) {
                        cx.invalid(item.span, "String should match pattern '\\S'");
                        return None;
                    }
                    out.push(s);
                }
                out.try_into().ok().map(Self)
            }
        }
        impl Spell for $name {
            fn spell(&self) -> serde_json::Value {
                self.0.spell()
            }
        }
        leaf!($name, TypeExpr::Tuple(&[&$item; $n]));
    };
}

strings!(
    /// A prefix and a suffix.
    Affixes, 2, STR, |_| true, "a list of 2 strings"
);
strings!(
    /// Thousand, million, billion and trillion suffixes, each with a visible character.
    MagnitudeWords, 4, WORD, |s| s.chars().any(|c| !c.is_whitespace()), "a list of 4 strings"
);
strings!(
    /// Seven weekday names.
    Days, 7, STR, |_| true, "a list of 7 strings"
);
strings!(
    /// Twelve month names.
    Months, 12, STR, |_| true, "a list of 12 strings"
);

/// A d3 formatLocale definition.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct NumberLocale {
    /// The decimal mark (e.g. `"."`).
    pub decimal: String,
    /// The group separator (e.g. `","`).
    pub thousands: String,
    /// The group sizes (e.g. `[3]`), cycled as needed.
    pub grouping: Grouping,
    /// The currency prefix and suffix (e.g. `["$", ""]`) a `$` in a d3 spec prints.
    pub currency: Affixes,
    /// The percent sign.
    #[model(fill = "%")]
    pub percent: String,
    /// The minus sign.
    #[model(fill = "\u{2212}")]
    pub minus: String,
    /// The extra-short thousand, million, billion and trillion suffixes, where a magnitude is declared once (en-US: K, M, B, T). A leading space, where a locale wants one, is part of the suffix.
    #[model(absent = "K, M, B, T")]
    pub analytic: Option<MagnitudeWords>,
    /// The abbreviated thousand, million, billion and trillion suffixes on every value (en-US: `["k", "m", "bn", "trn"]`).
    #[model(absent = "k, m, bn, trn")]
    pub narrative: Option<MagnitudeWords>,
}

/// A pattern of whole painted directives, `letters` the ones it may use.
macro_rules! time_pattern {
    ($(#[$m:meta])* $name:ident, $pattern:literal) => {
        $(#[$m])*
        #[derive(Debug, Clone, PartialEq)]
        pub struct $name(pub String);

        impl ReadScalar for $name {
            fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
                let s = String::read(node, cx)?;
                if !Regex::new($pattern).is_ok_and(|re| re.is_match(&s)) {
                    cx.invalid(
                        node.span,
                        format!("String should match pattern '{}'", $pattern),
                    );
                    return None;
                }
                Some(Self(s))
            }
        }
        impl Spell for $name {
            fn spell(&self) -> serde_json::Value {
                self.0.spell()
            }
        }
        leaf!($name, TypeExpr::Keyed(&Keywords::pattern($pattern), &STR));
    };
}

time_pattern!(
    /// The `%c` pattern.
    DateTimePattern,
    "^(?:[^%]|%%|%[-_0]?[ABGHILMQSUVWXYabdejmpquwxy])*$"
);
time_pattern!(
    /// The `%x` pattern.
    DatePattern,
    "^(?:[^%]|%%|%[-_0]?[ABGHILMQSUVWYabdejmpquwy])*$"
);
time_pattern!(
    /// The `%X` pattern.
    TimePattern,
    "^(?:[^%]|%%|%[-_0]?[ABGHILMQSUVWYabdejmpquwy])*$"
);

/// A d3 timeFormatLocale definition.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TimeLocale {
    pub date_time: DateTimePattern,
    pub date: DatePattern,
    pub time: TimePattern,
    /// The AM and PM names.
    pub periods: Affixes,
    /// The weekday names, Sunday first.
    pub days: Days,
    /// The abbreviated weekday names.
    pub short_days: Days,
    /// The month names, January first.
    pub months: Months,
    /// The abbreviated month names.
    pub short_months: Months,
    /// The first day of the calendar week.
    pub week_start: WeekdayName,
}

macro_rules! locale_choice {
    ($(#[$m:meta])* $name:ident, $names:ident, $def:ident) => {
        $(#[$m])*
        #[derive(Debug, Clone, PartialEq)]
        pub enum $name {
            Name($names),
            Definition(Box<<$def as Leaf>::Whole>),
        }

        impl ReadScalar for $name {
            fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
                if matches!(node.kind, Kind::Map(_)) {
                    return $def::whole(node, cx).map(|d| Self::Definition(Box::new(d)));
                }
                $names::read(node, cx).map(Self::Name)
            }
        }
        impl Spell for $name {
            fn spell(&self) -> serde_json::Value {
                match self {
                    Self::Name(n) => n.spell(),
                    Self::Definition(d) => d.spell(),
                }
            }
        }
        leaf!(
            $name,
            TypeExpr::Either(
                &TypeExpr::Ref(stringify!($def), <$def as model_core::Model>::info),
                &<$names as Leaf>::EXPR,
            )
        );
    };
}

locale_choice!(
    /// A d3-format locale name or definition.
    NumberLocaleChoice, NumberLocaleName, NumberLocale
);
locale_choice!(
    /// A d3-time-format locale name or definition.
    TimeLocaleChoice, TimeLocaleName, TimeLocale
);

/// The d3 locales a board writes numbers and dates in.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RegionalLocale {
    /// A d3-format locale name or definition.
    #[model(fill = "en-US")]
    pub number: NumberLocaleChoice,
    /// A d3-time-format locale name or definition.
    #[model(fill = "en-US")]
    pub time: TimeLocaleChoice,
}

/// How a board writes numbers and times: its locales, currency and zone.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct Regional {
    /// The number and time locales.
    #[model(absent = "en-US for both")]
    pub locale: Option<RegionalLocale>,
    /// ISO 4217 code whose symbol replaces the locale's own in its currency template, at the locale's placement. Unset prints the locale's own.
    #[model(absent = "the locale's own currency")]
    pub currency: Option<CurrencyCode>,
    /// The language words the board generates (tab names, series counts) print in. Unset takes the language of `locale.time`, or `en` when that locale has no catalog or is a definition.
    #[model(absent = "the language of locale.time")]
    pub language: Option<LanguageCode>,
    /// IANA time zone of the board's clock: the date daterange presets resolve on, the zone of the data-freshness stamp, and the zone a query timestamp carrying a zone or offset prints, buckets and ticks in. Warehouse queries run in this zone too, so SQL date truncation and casts agree with the printed times.
    #[model(fill = "UTC")]
    pub time_zone: TimeZoneName,
    /// Calendar month (1=Jan..12=Dec) the board's fiscal year opens in: the anchor of the `fiscal_` daterange presets (`this_fiscal_quarter`). Chart axes keep their own `fiscal_year_start_month`.
    #[model(fill = 1)]
    pub fiscal_year_start_month: Month,
}

impl Regional {
    /// `Regional._currency_has_a_place`: a `currency` replaces the symbol in the number
    /// locale's currency template, so a defined template that prints none leaves it nowhere.
    /// Hand-written: it reads a field of the nested locale, which no `constraint` kind does.
    pub fn currency_has_a_place(regional: &Authored<Regional>) -> Vec<Diagnostic> {
        let Tri::Value(locale) = &regional.locale else {
            return Vec::new();
        };
        let Some(NumberLocaleChoice::Definition(number)) = &locale.number else {
            return Vec::new();
        };
        let blank = number
            .currency
            .as_ref()
            .is_some_and(|c| c.0.iter().all(|s| s.trim().is_empty()));
        if !matches!(regional.currency, Tri::Value(_)) || !blank {
            return Vec::new();
        }
        vec![diag::err_validation_field(
            "regional",
            "Value error, regional.currency replaces the symbol in regional.locale.number.currency, which has none: give the template one (e.g. [\"$\", \"\"]).".to_string(),
        )]
    }
}
