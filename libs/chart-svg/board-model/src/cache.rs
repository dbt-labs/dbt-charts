//! `models/cache.py`: the authored `cache:` layer. A scalar (`5m`, `forever`, `true`, `false`)
//! or the block form (`{ttl, prefetch}`) is one layer of three optional facts, `enabled`
//! (never authored: any value but `false` turns caching on at its scope), `ttl` and `prefetch`.
//! Unset facts inherit from the scope above. The resolved per-query policy and the four-scope
//! merge are normalize's.
//!
//! [`CachePatch`] is the authored block (the model the schema describes); [`CacheLayer`] is the
//! layer a scalar or a block reads to, with the `enabled` fact the desugaring sets.

use model_core::{
    build_leaf, Cx, FromNode, Keywords, Kind, Leaf, Merge, Node, ReadScalar, Scalar, Spell, Tri,
    TypeExpr,
};
use model_derive::Model;
use regex::Regex;
use std::sync::OnceLock;
use yaml::Value;

/// The short-duration spelling the schema states (`_DURATION_SCHEMA_PATTERN`).
const DURATION_SCHEMA_PATTERN: &str = r"^\s*(?:[0-9]+\s*[smhdwSMHDW]\s*)+$";
/// Longest ttl an author can mean, in seconds (`_MAX_TTL`: 3650 days).
const MAX_TTL_SECONDS: u64 = 3650 * 86_400;
/// Digits allowed in one segment (`_MAX_SEGMENT_DIGITS`).
const MAX_SEGMENT_DIGITS: usize = 9;

fn too_large(shown: &str) -> String {
    format!(
        "Invalid duration {shown}: too large — the longest ttl is 3650d. For results that should be kept until a manual refresh write cache: forever."
    )
}

/// The full-duration and one-segment patterns, ASCII like Python's `re.ASCII`.
fn patterns() -> Option<&'static (Regex, Regex)> {
    static PATTERNS: OnceLock<Option<(Regex, Regex)>> = OnceLock::new();
    PATTERNS
        .get_or_init(|| {
            Some((
                Regex::new(r"(?-u)^(?:\d+\s*[smhdw]\s*)+$").ok()?,
                Regex::new(r"(?-u)(\d+)\s*([smhdw])").ok()?,
            ))
        })
        .as_ref()
}

/// `parse_duration`: `<number><unit>` segments with units s/m/h/d/w (m = minutes), compound
/// allowed, case- and whitespace-insensitive, each unit at most once, positive, at most 3650d.
fn check_duration(text: &str) -> Result<(), String> {
    let shown = diagnostic::py_str_repr(text);
    let normalized = text.trim().to_lowercase();
    let Some((full, segment)) = patterns() else {
        return Err("the duration pattern does not compile".to_string());
    };
    if !full.is_match(&normalized) {
        return Err(format!(
            "Invalid duration {shown}: expected <number><unit> segments with units s/m/h/d/w (m = minutes), e.g. '15m', '24h', '1h30m'."
        ));
    }
    let mut seen: Vec<&str> = Vec::new();
    let mut total: u64 = 0;
    for caps in segment.captures_iter(&normalized) {
        let (Some(amount), Some(unit)) = (caps.get(1), caps.get(2)) else {
            continue;
        };
        let (amount, unit) = (amount.as_str(), unit.as_str());
        if seen.contains(&unit) {
            return Err(format!(
                "Invalid duration {shown}: unit '{unit}' appears more than once — write each unit at most once (e.g. '1h30m', not '1h1h')."
            ));
        }
        seen.push(unit);
        if amount.trim_start_matches('0').len() > MAX_SEGMENT_DIGITS {
            return Err(too_large(&shown));
        }
        let per = match unit {
            "s" => 1,
            "m" => 60,
            "h" => 3_600,
            "d" => 86_400,
            _ => 604_800,
        };
        let n: u64 = amount.parse().map_err(|_| too_large(&shown))?;
        total = total.saturating_add(n.saturating_mul(per));
    }
    if total > MAX_TTL_SECONDS {
        return Err(too_large(&shown));
    }
    if total == 0 {
        return Err(format!(
            "Invalid duration {shown}: a ttl must be positive — to disable caching write cache: false; to keep results until a manual refresh write cache: forever."
        ));
    }
    Ok(())
}

/// How long cached results live: `forever`, or a short duration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CacheTtl {
    /// Never auto-expire (manual refresh always remains available).
    Forever,
    /// The duration as written, validated.
    Duration(String),
}

impl ReadScalar for CacheTtl {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let Kind::Scalar(Value::Str(s)) = &node.kind else {
            cx.mismatch(node, "a duration such as `5m`, or `forever`");
            return None;
        };
        if s == "forever" {
            return Some(Self::Forever);
        }
        match check_duration(s) {
            Ok(()) => Some(Self::Duration(s.clone())),
            Err(e) => {
                cx.invalid(node.span, format!("Value error, {e}"));
                None
            }
        }
    }
}

impl Spell for CacheTtl {
    fn spell(&self) -> serde_json::Value {
        match self {
            Self::Forever => "forever".into(),
            Self::Duration(d) => d.as_str().into(),
        }
    }
}

impl FromNode for CacheTtl {
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

const DURATION: TypeExpr = TypeExpr::Keyed(
    &Keywords::pattern(DURATION_SCHEMA_PATTERN).in_arm(),
    &TypeExpr::Scalar(Scalar::Str),
);

impl Leaf for CacheTtl {
    type Whole = CacheTtl;
    type Authored = Option<CacheTtl>;
    type Patch = Option<CacheTtl>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(&TypeExpr::OneOf(&["forever"]), &DURATION);
}
build_leaf!(CacheTtl);

/// Cache policy at one scope: cache: 1h / forever / true / false (the only opt-out).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CachePatch {
    /// Expire cached results after this wall-clock age (lazy: the next read recomputes). Short-duration string: units s/m/h/d/w, m = minutes, compound allowed ('1h30m'). Omit to inherit from the parent scope; write 'forever' to override an inherited ttl with never-auto-expire (manual refresh always remains available).
    #[model(omit = "inherits the parent scope's ttl")]
    pub ttl: Option<CacheTtl>,
    /// Load this scope's queries into the cache in the background when they sit on a hidden tab, inner tabs of a hidden tab included. Omit to inherit from the parent scope. Needs caching on.
    #[model(absent = "inherits the parent scope's prefetch")]
    pub prefetch: Option<bool>,
}

/// One cache layer, as the cascade merges it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CacheLayer {
    /// Whether results at this scope may be cached: `Some(false)` is `cache: false`, `Some(true)`
    /// any other value; `None` (a block with only `prefetch`, or `{}`) leaves it to the scope above.
    pub enabled: Option<bool>,
    pub ttl: Option<CacheTtl>,
    pub prefetch: Option<bool>,
}

impl ReadScalar for CacheLayer {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let layer = |enabled, ttl| {
            Some(Self {
                enabled,
                ttl,
                prefetch: None,
            })
        };
        match &node.kind {
            Kind::Scalar(Value::Bool(b)) => layer(Some(*b), None),
            Kind::Scalar(Value::Str(_)) => {
                let ttl = CacheTtl::read(node, cx)?;
                layer(Some(true), Some(ttl))
            }
            Kind::Map(entries) => {
                if entries.contains_key("enabled") {
                    cx.invalid(node.span, "Value error, cache.enabled is not authorable — write cache: false to turn caching off at this scope, or cache: <duration> (e.g. cache: 1h) / cache: true to turn it on.");
                    return None;
                }
                if entries
                    .get("ttl")
                    .is_some_and(|t| matches!(t.value.kind, Kind::Scalar(Value::Null)))
                {
                    cx.invalid(node.span, "Value error, ttl: null is ambiguous — omit ttl to inherit from the parent scope, or write cache: forever to never auto-expire.");
                    return None;
                }
                let block = CachePatch::whole(node, cx)?;
                // A block that carries any value at all means caching is on, except one that
                // carries only `prefetch`, which says nothing about caching itself.
                let enabled = entries.keys().any(|k| k != "prefetch").then_some(true);
                Some(Self {
                    enabled,
                    ttl: block.ttl,
                    prefetch: match block.prefetch {
                        Tri::Value(b) => Some(b),
                        _ => None,
                    },
                })
            }
            _ => {
                cx.mismatch(
                    node,
                    "a duration, `forever`, a boolean or a `{ttl, prefetch}` block",
                );
                None
            }
        }
    }
}

impl Spell for CacheLayer {
    fn spell(&self) -> serde_json::Value {
        match (self.enabled, &self.ttl, self.prefetch) {
            (Some(false), ..) => false.into(),
            (Some(true), None, None) => true.into(),
            (Some(true), Some(ttl), None) => ttl.spell(),
            (_, ttl, prefetch) => {
                let mut block = serde_json::Map::new();
                if let Some(ttl) = ttl {
                    block.insert("ttl".into(), ttl.spell());
                }
                if let Some(prefetch) = prefetch {
                    block.insert("prefetch".into(), prefetch.into());
                }
                block.into()
            }
        }
    }
}

impl FromNode for CacheLayer {
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

impl Leaf for CacheLayer {
    type Whole = CacheLayer;
    type Authored = Option<CacheLayer>;
    type Patch = Option<CacheLayer>;
    const MERGE: Merge = Merge::Override;
    /// `CacheInput`'s schema input: the block, `forever`, a boolean or a duration.
    const EXPR: TypeExpr = TypeExpr::Either(
        &TypeExpr::Ref("CachePatch", <CachePatch as model_core::Model>::info),
        &TypeExpr::Either(
            &TypeExpr::OneOf(&["forever"]),
            &TypeExpr::Either(&TypeExpr::Scalar(Scalar::Bool), &DURATION),
        ),
    );
}
build_leaf!(CacheLayer);
