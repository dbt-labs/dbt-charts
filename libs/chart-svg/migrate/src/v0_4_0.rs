//! The 0.3.1 -> 0.4.0 axis/scale model reshape.
//!
//! Every rename is a pure tail substitution, so `suffix_rename_moves` finds
//! every occurrence. Leaf renames that land inside the `labels`/`scale`
//! containers run before the `labels`/`line` container renames themselves: a
//! leaf move creates its destination container on demand, and the later
//! container move must find that destination still empty (or absent).

use crate::decl::{Declarations, Grammars};

const RENAMES: &[(&[&str], &[&str])] = &[
    (&["labels", "format"], &["format"]),
    (&["scale", "values"], &["values"]),
    (&["scale", "continuous", "domain"], &["scale", "domain"]),
    (&["scale", "continuous", "zero"], &["scale", "zero"]),
    (&["scale", "continuous", "type"], &["scale", "type"]),
    (&["scale", "continuous", "log", "base"], &["scale", "base"]),
    (
        &["scale", "continuous", "pow", "exponent"],
        &["scale", "exponent"],
    ),
    (
        &["scale", "continuous", "symlog", "constant"],
        &["scale", "constant"],
    ),
    (&["scale", "padding"], &["scale", "band_padding_inner"]),
    (&["ticks", "length"], &["ticks", "size"]),
    (&["labels"], &["label"]),
    (&["line"], &["domain"]),
];

pub fn declarations(g: &Grammars) -> Declarations {
    Declarations {
        moves: g.suffix_rename_moves(RENAMES, None, None),
        ..Declarations::default()
    }
}
