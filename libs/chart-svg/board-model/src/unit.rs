//! Unit newtypes that more than one style model shares (spec § 5). A unit used
//! by a single model stays `f64` with a `constraint`. Bounds and messages are
//! pydantic's, so NaN fails every bound and infinity passes the open ones.

/// A CSS pixel length as Python's `[0-9]+(?:\.[0-9]+)?(?:px)?` matches it: digits, an optional
/// fraction and an optional `px`; no sign, no space, no other unit.
pub(crate) fn pixel_length(text: &str) -> Option<f64> {
    let digits = text.strip_suffix("px").unwrap_or(text);
    let (whole, frac) = digits.split_once('.').unwrap_or((digits, "0"));
    let ok = |d: &str| !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit());
    (ok(whole) && ok(frac)).then(|| digits.parse().ok()).flatten()
}

macro_rules! unit {
    ($(#[$m:meta])* $name:ident, $bounds:expr, $ok:expr, $msg:literal) => {
        $(#[$m])*
        #[derive(Debug, Clone, Copy, PartialEq)]
        pub struct $name(f64);

        impl $name {
            pub fn get(self) -> f64 {
                self.0
            }
        }

        impl model_core::Spell for $name {
            fn spell(&self) -> model_core::serde_json::Value {
                model_core::Spell::spell(&self.0)
            }
        }

        impl TryFrom<f64> for $name {
            type Error = String;
            fn try_from(v: f64) -> Result<Self, String> {
                let ok: fn(f64) -> bool = $ok;
                if ok(v) { Ok(Self(v)) } else { Err($msg.to_string()) }
            }
        }

        model_core::from_scalar!($name, f64);

        impl model_core::Leaf for $name {
            type Whole = $name;
            type Authored = Option<$name>;
            type Patch = Option<$name>;
            const MERGE: model_core::Merge = model_core::Merge::Override;
            const EXPR: model_core::TypeExpr = model_core::TypeExpr::Keyed(
                &model_core::Keywords::bounded($bounds),
                &model_core::TypeExpr::Scalar(model_core::Scalar::Float),
            );
        }
        model_core::build_leaf!($name);
    };
}

unit!(
    /// A length in pixels, at least zero (`ge=0`).
    Px,
    model_core::Bounds { ge: Some(0.0), ..model_core::Bounds::NONE },
    |v| v >= 0.0,
    "Input should be greater than or equal to 0"
);
/// A share between zero and one (`ge=0, le=1`): opacity, proportion.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Fraction(f64);

impl Fraction {
    pub fn get(self) -> f64 {
        self.0
    }
}

impl model_core::Spell for Fraction {
    fn spell(&self) -> model_core::serde_json::Value {
        model_core::Spell::spell(&self.0)
    }
}

impl TryFrom<f64> for Fraction {
    type Error = String;
    fn try_from(v: f64) -> Result<Self, String> {
        if (0.0..=1.0).contains(&v) {
            Ok(Self(v))
        } else if v > 1.0 {
            Err("Input should be less than or equal to 1".to_string())
        } else {
            Err("Input should be greater than or equal to 0".to_string())
        }
    }
}

model_core::from_scalar!(Fraction, f64);

impl model_core::Leaf for Fraction {
    type Whole = Fraction;
    type Authored = Option<Fraction>;
    type Patch = Option<Fraction>;
    const MERGE: model_core::Merge = model_core::Merge::Override;
    const EXPR: model_core::TypeExpr = model_core::TypeExpr::Keyed(
        &model_core::Keywords::bounded(model_core::Bounds {
            ge: Some(0.0),
            le: Some(1.0),
            ..model_core::Bounds::NONE
        }),
        &model_core::TypeExpr::Scalar(model_core::Scalar::Float),
    );
}
model_core::build_leaf!(Fraction);
unit!(
    /// A positive ratio such as an aspect ratio (`gt=0`).
    Ratio,
    model_core::Bounds { gt: Some(0.0), ..model_core::Bounds::NONE },
    |v| v > 0.0,
    "Input should be greater than 0"
);

/// A number pydantic types as a union of `float` and `int`, which the schema states as two arms in
/// the order the annotation writes them.
macro_rules! float_or_int {
    ($(#[$m:meta])* $name:ident, $first:ident, $second:ident) => {
        $(#[$m])*
        #[derive(Debug, Clone, Copy, PartialEq)]
        pub struct $name(f64);

        impl $name {
            pub fn get(self) -> f64 {
                self.0
            }
        }

        impl model_core::Spell for $name {
            fn spell(&self) -> model_core::serde_json::Value {
                model_core::Spell::spell(&self.0)
            }
        }

        impl TryFrom<f64> for $name {
            type Error = String;
            fn try_from(v: f64) -> Result<Self, String> {
                Ok(Self(v))
            }
        }

        model_core::from_scalar!($name, f64);

        impl model_core::Leaf for $name {
            type Whole = $name;
            type Authored = Option<$name>;
            type Patch = Option<$name>;
            const MERGE: model_core::Merge = model_core::Merge::Override;
            const EXPR: model_core::TypeExpr = model_core::TypeExpr::Either(
                &model_core::TypeExpr::Scalar(model_core::Scalar::$first),
                &model_core::TypeExpr::Scalar(model_core::Scalar::$second),
            );
        }
        model_core::build_leaf!($name);
    };
}

float_or_int!(
    /// A scale's `min`/`max`, typed `float | int`.
    Limit, Float, Int
);
float_or_int!(
    /// A chart size or a rule's threshold, typed `int | float`.
    Number, Int, Float
);
