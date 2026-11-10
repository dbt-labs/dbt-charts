//! A model's declared [`Constraint`]s, evaluated over a value's keys. The JSON Schema states the
//! same table (`schema`); this reads it for a value already built from several layers, so a rule
//! is written once.

use crate::table::{Condition, Constraint, ConstraintKind};
use std::fmt;

/// A key of an authored value: not written, written as `null`, or given a value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Unset,
    Null,
    Set,
}

/// The keys of an authored value, by the names its constraints use.
pub trait Keyed {
    /// `None` when the value has no such key.
    fn state(&self, key: &str) -> Option<State>;
}

/// A constraint the view cannot answer: it reads more than whether a key is set.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unanswerable(pub String);

impl fmt::Display for Unanswerable {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Unanswerable {}

fn is_set(view: &dyn Keyed, key: &str) -> Result<bool, Unanswerable> {
    view.state(key)
        .map(|s| s == State::Set)
        .ok_or_else(|| Unanswerable(format!("the value has no key `{key}`")))
}

fn holds(when: &[Condition], view: &dyn Keyed) -> Result<bool, Unanswerable> {
    for c in when {
        match c {
            Condition::IsSet(key) if !is_set(view, key)? => return Ok(false),
            Condition::IsSet(_) => {}
            other => {
                return Err(Unanswerable(format!(
                    "condition {other:?} reads a value, not whether a key is set"
                )))
            }
        }
    }
    Ok(true)
}

fn count_set(c: &Constraint, view: &dyn Keyed) -> Result<usize, Unanswerable> {
    let mut n = 0;
    for key in c.fields {
        n += usize::from(is_set(view, key)?);
    }
    Ok(n)
}

/// Whether `c` holds over `view`. A kind that reads a value rather than whether keys are set
/// (`value_in`, `range`, ..), or a `nonempty` / `auto` modifier, is [`Unanswerable`]: a rule is
/// never skipped.
pub fn satisfied(c: &Constraint, view: &dyn Keyed) -> Result<bool, Unanswerable> {
    if !c.nonempty.is_empty() || !c.auto.is_empty() {
        return Err(Unanswerable(
            "`nonempty` and `auto` read values, not whether a key is set".to_string(),
        ));
    }
    if !holds(c.when, view)? {
        return Ok(true);
    }
    Ok(match c.kind {
        ConstraintKind::Requires => count_set(c, view)? == c.fields.len(),
        ConstraintKind::Forbids => count_set(c, view)? == 0,
        ConstraintKind::AtLeastOneOf => count_set(c, view)? >= 1,
        ConstraintKind::AtMostOneOf => count_set(c, view)? <= 1,
        other => {
            return Err(Unanswerable(format!(
                "a `{other:?}` constraint reads values, not whether keys are set"
            )))
        }
    })
}

/// The first constraint of `constraints`, in declaration order, that `view` breaks.
pub fn first_violation<'a>(
    constraints: &'a [Constraint],
    view: &dyn Keyed,
) -> Result<Option<&'a Constraint>, Unanswerable> {
    for c in constraints {
        if !satisfied(c, view)? {
            return Ok(Some(c));
        }
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::table::Bounds;

    struct Keys(&'static [(&'static str, State)]);

    impl Keyed for Keys {
        fn state(&self, key: &str) -> Option<State> {
            self.0.iter().find(|(k, _)| *k == key).map(|(_, s)| *s)
        }
    }

    const fn rule(kind: ConstraintKind, fields: &'static [&'static str]) -> Constraint {
        Constraint {
            kind,
            fields,
            when: &[],
            nonempty: &[],
            auto: &[],
            values: &[],
            pattern: None,
            bounds: Bounds::NONE,
            message: None,
        }
    }

    #[test]
    fn a_null_key_is_not_set_and_a_rule_under_an_unmet_when_holds() {
        let view = Keys(&[("a", State::Null), ("b", State::Set), ("c", State::Unset)]);
        let at_least = rule(ConstraintKind::AtLeastOneOf, &["a", "c"]);
        let at_most = rule(ConstraintKind::AtMostOneOf, &["a", "b"]);
        let requires = Constraint {
            when: &[Condition::IsSet("c")],
            ..rule(ConstraintKind::Requires, &["a"])
        };
        assert_eq!(satisfied(&at_least, &view), Ok(false));
        assert_eq!(satisfied(&at_most, &view), Ok(true));
        assert_eq!(satisfied(&requires, &view), Ok(true));
        let rules = [at_most, at_least];
        let first = first_violation(&rules, &view).map(|c| c.map(|c| c.kind));
        assert_eq!(first, Ok(Some(ConstraintKind::AtLeastOneOf)));
    }

    #[test]
    fn a_rule_the_view_cannot_answer_is_an_error_never_skipped() {
        let view = Keys(&[("a", State::Set)]);
        assert!(satisfied(&rule(ConstraintKind::Range, &["a"]), &view).is_err());
        assert!(satisfied(&rule(ConstraintKind::Requires, &["missing"]), &view).is_err());
        let nonempty = Constraint {
            nonempty: &["a"],
            ..rule(ConstraintKind::Requires, &["a"])
        };
        assert!(satisfied(&nonempty, &view).is_err());
    }
}
