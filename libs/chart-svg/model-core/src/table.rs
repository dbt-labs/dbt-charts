/// How an upper layer's set value combines with a lower one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Merge {
    Override,
    Deep,
    ByKey,
}

/// A JSON scalar type; the base of facets and the leaves of the spine.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scalar {
    Bool,
    Int,
    Float,
    Str,
    /// ISO date text, pydantic's `date`.
    Date,
    /// ISO date-time text, pydantic's `datetime`.
    DateTime,
    /// Any JSON value: a cell of inline data, a variable's default.
    Json,
    /// Any mapping, as Python's `dict[str, Any]` states it: `{"type": "object"}` with no value type.
    Object,
}

/// A field's type as written, for schema emission. Built by [`Leaf::EXPR`]
/// (the derive only adds the `Option` / `Patch` wrappers a field can carry).
#[derive(Debug)]
pub enum TypeExpr {
    Scalar(Scalar),
    /// A newtype with a facet name (`color`, `url`, ...) over a scalar.
    Facet(&'static str, Scalar),
    /// A model (struct, enum or tagged enum), resolved lazily so models may recurse.
    Ref(&'static str, fn() -> &'static ModelInfo),
    List(&'static TypeExpr),
    Map(&'static TypeExpr),
    Option(&'static TypeExpr),
    /// `Engine<value, Rule>`: the rule enum is a unit-only model; its variants are the spellings.
    Engine(&'static TypeExpr, &'static TypeExpr),
    /// `Patch<T>`: a slot the cascade finishes later; unset falls through, null wins.
    Patch(&'static TypeExpr),
    /// The JSON Schema keywords a facet or unit declares (`pattern`, bounds) over its type.
    Keyed(&'static Keywords, &'static TypeExpr),
    /// A closed set of spellings.
    OneOf(&'static [&'static str]),
    /// A newtype that is either of two shapes, such as a name or a number.
    Either(&'static TypeExpr, &'static TypeExpr),
    /// A boolean constant: the only value a flag takes (`Literal[True]`).
    Const(bool),
    /// A closed set of JSON scalars of any kind, as one `Literal[..]` lists them: `"bold"`, `700`.
    Literals(&'static [Value]),
    /// A fixed-length array of positional types: a pair, a rectangle.
    Tuple(&'static [&'static TypeExpr]),
    /// The payload of a unit variant.
    Unit,
}

/// The JSON Schema keywords a facet or a unit carries: what a validator of the
/// newtype checks, stated where the newtype is declared.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Keywords {
    pub pattern: Option<&'static str>,
    /// What the pattern becomes when an `Engine` takes the rule spellings before the facet
    /// sees the value (`auto` is then no longer the facet's to refuse or admit).
    pub in_engine: InEngine,
    pub bounds: Bounds,
    /// `minItems` of a list-valued newtype.
    pub min_items: Option<u64>,
    /// `maxItems` of a list-valued newtype.
    pub max_items: Option<u64>,
    /// `minLength` of a string newtype.
    pub min_length: Option<u64>,
    /// The pattern stays in the arm that states it (`Literal[..] | Duration`, where the pattern is
    /// the string arm's own) instead of moving beside the field's `anyOf`; the other keywords still move.
    pub in_arm: bool,
}

/// A facet's pattern under an `Engine`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum InEngine {
    Same,
    Drop,
    Pattern(&'static str),
}

impl Keywords {
    /// No keywords: the base to add one to.
    pub const NONE: Self = Self {
        pattern: None,
        in_engine: InEngine::Same,
        bounds: Bounds::NONE,
        min_items: None,
        max_items: None,
        min_length: None,
        in_arm: false,
    };

    pub const fn pattern(pattern: &'static str) -> Self {
        Self {
            pattern: Some(pattern),
            in_engine: InEngine::Same,
            bounds: Bounds::NONE,
            min_items: None,
            max_items: None,
            min_length: None,
            in_arm: false,
        }
    }

    pub const fn bounded(bounds: Bounds) -> Self {
        Self {
            pattern: None,
            in_engine: InEngine::Same,
            bounds,
            min_items: None,
            max_items: None,
            min_length: None,
            in_arm: false,
        }
    }

    pub const fn at_least_items(min_items: u64) -> Self {
        Self {
            pattern: None,
            in_engine: InEngine::Same,
            bounds: Bounds::NONE,
            min_items: Some(min_items),
            max_items: None,
            min_length: None,
            in_arm: false,
        }
    }

    /// A list of exactly `n` items.
    pub const fn exactly_items(n: u64) -> Self {
        Self {
            pattern: None,
            in_engine: InEngine::Same,
            bounds: Bounds::NONE,
            min_items: Some(n),
            max_items: Some(n),
            min_length: None,
            in_arm: false,
        }
    }

    /// A string of at least `n` characters.
    pub const fn at_least_chars(mut self, n: u64) -> Self {
        self.min_length = Some(n);
        self
    }

    /// Keep the pattern in its arm of a union.
    pub const fn in_arm(mut self) -> Self {
        self.in_arm = true;
        self
    }

    pub const fn in_engine(mut self, in_engine: InEngine) -> Self {
        self.in_engine = in_engine;
        self
    }
}

impl PartialEq for TypeExpr {
    /// Models compare by name: the function pointer carries no extra identity.
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Scalar(a), Self::Scalar(b)) => a == b,
            (Self::Facet(a, x), Self::Facet(b, y)) => a == b && x == y,
            (Self::Ref(a, _), Self::Ref(b, _)) => a == b,
            (Self::List(a), Self::List(b))
            | (Self::Map(a), Self::Map(b))
            | (Self::Option(a), Self::Option(b))
            | (Self::Patch(a), Self::Patch(b)) => a == b,
            (Self::Engine(a, r), Self::Engine(b, s)) => a == b && r == s,
            (Self::Keyed(k, a), Self::Keyed(l, b)) => k == l && a == b,
            (Self::Either(a, b), Self::Either(c, d)) => a == c && b == d,
            (Self::OneOf(a), Self::OneOf(b)) => a == b,
            (Self::Tuple(a), Self::Tuple(b)) => a == b,
            (Self::Const(a), Self::Const(b)) => a == b,
            (Self::Literals(a), Self::Literals(b)) => a == b,
            (Self::Unit, Self::Unit) => true,
            _ => false,
        }
    }
}

/// The closed attribute set of spec § 4, as written. `Field::merge` holds the
/// resolved strategy; `absent` is the meaning of an `Option` field.
#[derive(Debug, Default, PartialEq)]
pub struct Attrs {
    pub absent: Option<&'static str>,
    /// `omit`: an `Option<T>` the layers may leave unset, whose authored twin is `T`'s own, so
    /// `null` is refused as for a plain field. Names what `None` means.
    pub omit: Option<&'static str>,
    /// `default_empty`: a map, list or model the layers leave unset builds empty.
    pub default_empty: bool,
    pub inherit: Option<&'static str>,
    pub inherit_slot: Option<&'static str>,
    /// `(leaf, path)`: re-parent single leaves of an inherit slot.
    pub links: &'static [(&'static str, &'static str)],
    /// `(path, source)`: a leaf or container below the field takes `source`, at this mount only
    /// (one shared type mounted at different tiers).
    pub links_at: &'static [(&'static str, &'static str)],
    /// `(path, value)`: a leaf below the field holds `value` where nothing set it, at this mount
    /// only.
    pub fills_at: &'static [(&'static str, Value)],
    pub local: bool,
    pub nested_drop: bool,
    /// Python types the field `X | None` and a null means nothing authored: it reads as the empty mapping.
    pub nullable: bool,
    pub fill: Option<&'static str>,
    /// `fill = <literal>`: the constant normalize supplies; the schema states it as `default`.
    pub fill_value: Option<Value>,
    /// The type the field also accepts (`accept = Type`).
    pub accept: Option<&'static TypeExpr>,
    pub directive: bool,
    pub keys: bool,
    pub flatten: bool,
    pub role: Option<&'static str>,
    /// Python constrains the string inside its arm (`Annotated[str, StringConstraints(pattern=..)] |
    /// None`), so the schema keeps the pattern in that arm instead of beside the `anyOf`.
    pub arm_pattern: bool,
    /// `min_items = N` on a list: the least it may hold, as Python's `min_length`; null is unset.
    pub min_items: Option<u64>,
}

/// A field of a struct, or a variant of an enum. `name` is the authored key:
/// the field's identifier, or a variant's snake-case spelling.
#[derive(Debug, PartialEq)]
pub struct Field {
    pub name: &'static str,
    pub ty: &'static TypeExpr,
    pub doc: &'static str,
    pub merge: Merge,
    pub attrs: Attrs,
}

/// A constraint's kind; the set is closed (spec § 4, D-21).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConstraintKind {
    /// Every field is set (under `when`).
    Requires,
    /// No field is set (under `when`).
    Forbids,
    /// The value at the one field is one of `values` (under `when`).
    ValueIn,
    /// Each item of the list at the one field fully matches `pattern` (under `when`).
    ItemsMatch,
    /// The number at the one field, or each number of the list at it, lies within `bounds`.
    Range,
    /// No value repeats across the union of the named lists.
    Unique,
    AtLeastOneOf,
    AtMostOneOf,
    /// The field, when written, is not `null`; it need not be written.
    NotNull,
}

/// A JSON scalar value in a constraint.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(&'static str),
}

/// A precondition of a constraint.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Condition {
    IsSet(&'static str),
    ValueIn {
        key: &'static str,
        values: &'static [Value],
        negate: bool,
    },
    /// The value is an argument-less call to one of `names`: `name()`.
    IsCall {
        key: &'static str,
        names: &'static [&'static str],
        negate: bool,
    },
    IsList(&'static str),
}

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Bounds {
    pub gt: Option<f64>,
    pub ge: Option<f64>,
    pub lt: Option<f64>,
    pub le: Option<f64>,
}

impl Bounds {
    pub const NONE: Self = Self {
        gt: None,
        ge: None,
        lt: None,
        le: None,
    };
}

/// One declarative cross-field rule. Keys are authored keys; a dotted key
/// reaches into a nested object. A key is *set* when present and not null.
#[derive(Debug, PartialEq)]
pub struct Constraint {
    pub kind: ConstraintKind,
    pub fields: &'static [&'static str],
    pub when: &'static [Condition],
    /// Keys that must also be non-empty to count as set.
    pub nonempty: &'static [&'static str],
    /// `at_most_one_of`: keys that count as unset while they read `auto`.
    pub auto: &'static [&'static str],
    pub values: &'static [Value],
    pub pattern: Option<&'static str>,
    pub bounds: Bounds,
    /// What a violation says when a caller refuses a value over it (`{query}` is the caller's
    /// name for the offender); the JSON Schema states the rule and never this text.
    pub message: Option<&'static str>,
    /// A registered diagnostic code the violation is reported under, in place of
    /// `ERR-VALIDATION-FIELD` (a code whose message takes no parameters).
    pub code: Option<&'static str>,
}

/// What a model is, which decides how its fields read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModelKind {
    Struct,
    /// A unit-only enum: its fields are the spellings.
    Enum,
    /// An enum with data: used as a `keys` field, its variants are sibling keys.
    Variants,
    /// An enum read by the named tag key; each variant holds one model.
    Tagged(&'static str),
}

/// The field table of one model.
#[derive(Debug, PartialEq)]
pub struct ModelInfo {
    pub name: &'static str,
    pub doc: &'static str,
    pub kind: ModelKind,
    pub fields: &'static [Field],
    pub constraints: &'static [Constraint],
    /// The key a bare string stands for: `font: label` is `font: {extends: label}`.
    pub shorthand: Option<&'static str>,
    /// The pattern of the bare string a `shorthand_with` struct also reads from.
    pub string_pattern: Option<&'static str>,
    /// Every key is required of an authored layer even where a slot mirrors the model.
    pub complete: bool,
    /// The tag a tagged enum reads this model under, when a rule of the model depends on it (a
    /// histogram is a bar): a constraint may name that key though it is no field.
    pub read_under: Option<&'static str>,
}
