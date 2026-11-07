//! The values a theme layer states, by authored path: the schema walk behind a Style-rooted
//! `default` check (D-26).

use super::{Body, Schema, Ty};
use crate::Value;
use serde_json::Value as J;
use std::collections::BTreeMap;

/// `(owner model and key, path, fill, stated)`: a `fill` that differs from the theme's value.
pub type FillDisagreement = (String, String, J, J);

/// Where a key's value comes from at one mount path.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Supply {
    /// The theme states it there.
    Theme,
    /// The mount is under an `inherit_slot` or `Patch` field and the theme does not state it
    /// there: another layer fills it.
    Cascade,
    /// Nothing states it there.
    Unset,
}

/// Per model, per authored key, every mount path of the model with where the key comes from.
pub type ThemeSupply = BTreeMap<String, BTreeMap<String, Vec<(String, Supply)>>>;

#[derive(Default)]
struct Walked {
    values: BTreeMap<String, J>,
    differ: Vec<FillDisagreement>,
    supply: ThemeSupply,
}

impl Schema {
    /// Every authored leaf path from the root with the value a layer without it would get: what
    /// `theme` states there, else the field's `fill` literal. A leaf is anything but a model or a
    /// map: a scalar, an enum, a facet, a list. A path through a map or a tagged union is not
    /// followed, since its keys belong to the document; a model the theme leaves out is followed,
    /// so the `fill` literals below it are found.
    pub fn theme_values(&self, theme: &J) -> BTreeMap<String, J> {
        self.theme_walk(theme).values
    }

    /// The `fill` literals that differ from what `theme` states at their path: `(owner model
    /// and key, path, fill, stated)`. A fill is what the field gets when no layer says it, so it must be the theme's.
    pub fn fill_disagreements(&self, theme: &J) -> Vec<FillDisagreement> {
        self.theme_walk(theme).differ
    }

    /// For every leaf key of every model, each path the model is mounted at and whether `theme`
    /// states the key there (a `fill` does not count).
    pub fn theme_supply(&self, theme: &J) -> ThemeSupply {
        self.theme_walk(theme).supply
    }

    fn theme_walk(&self, theme: &J) -> Walked {
        let mut w = Walked::default();
        self.walk_theme(&self.root, theme, "", false, &mut Vec::new(), &mut w);
        w
    }

    fn walk_theme<'a>(
        &'a self,
        model: &'a str,
        theme: &J,
        at: &str,
        cascade: bool,
        stack: &mut Vec<&'a str>,
        w: &mut Walked,
    ) {
        let Some(m) = self.models.get(model) else {
            return;
        };
        if !matches!(m.body, Body::Struct { .. }) || stack.contains(&m.name) {
            return;
        }
        stack.push(m.name);
        for p in self.flat(m).props {
            let stated = theme.get(p.key);
            let path = if at.is_empty() {
                p.key.to_string()
            } else {
                format!("{at}.{}", p.key)
            };
            match leaf(p.ty) {
                Ty::Ref(n) => {
                    let under = cascade
                        || p.field.attrs.inherit_slot.is_some()
                        || matches!(p.ty, Ty::Patch(_))
                        || matches!(p.ty, Ty::Option(t, _) if matches!(**t, Ty::Patch(_)));
                    w.note(m.name, p.key, at, stated.is_some(), cascade);
                    let empty = J::Null;
                    self.walk_theme(n, stated.unwrap_or(&empty), &path, under, stack, w)
                }
                Ty::Map(_) | Ty::Keys(_) => {}
                _ => {
                    let filled = p.field.attrs.fill_value.as_ref().map(fill_json);
                    if let (Some(f), Some(t)) = (&filled, stated) {
                        if !same(f, t) {
                            w.differ.push((
                                format!("{}.{}", m.name, p.key),
                                path.clone(),
                                f.clone(),
                                t.clone(),
                            ));
                        }
                    }
                    w.note(m.name, p.key, at, stated.is_some(), cascade);
                    if let Some(v) = stated.cloned().or(filled) {
                        w.values.insert(path, v);
                    }
                }
            }
        }
        stack.pop();
    }
}

impl Walked {
    fn note(&mut self, model: &str, key: &str, at: &str, stated: bool, cascade: bool) {
        let source = match (stated, cascade) {
            (true, _) => Supply::Theme,
            (false, true) => Supply::Cascade,
            (false, false) => Supply::Unset,
        };
        self.supply
            .entry(model.to_string())
            .or_default()
            .entry(key.to_string())
            .or_default()
            .push((at.to_string(), source));
    }
}

/// The type a value is read as: under `Option`, `Patch`, `Engine`, `Accept` and `Keyed`.
fn leaf(ty: &Ty) -> &Ty {
    match ty {
        Ty::Option(t, _) | Ty::Patch(t) | Ty::Keyed(_, t) | Ty::Engine(t, _) | Ty::Accept(t, _) => {
            leaf(t)
        }
        t => t,
    }
}

/// Equal as JSON, a number by value: `8` is `8.0`.
fn same(a: &J, b: &J) -> bool {
    match (a.as_f64(), b.as_f64()) {
        (Some(x), Some(y)) => x == y,
        _ => a == b,
    }
}

fn fill_json(v: &Value) -> J {
    match v {
        Value::Null => J::Null,
        Value::Bool(b) => J::from(*b),
        Value::Int(i) => J::from(*i),
        Value::Float(f) => J::from(*f),
        Value::Str(s) => J::from(*s),
    }
}
