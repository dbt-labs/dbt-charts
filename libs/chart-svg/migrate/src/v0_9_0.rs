//! The 0.7.0 -> 0.9.0 boundary.
//!
//! Categorical color variants gain a third token segment (`category.blue.dark`,
//! `vivid-10.3.pale`) and the four companion roles (`category_dark`, `_light`,
//! `_ghost`, `_ink`) retire. Neither change is schema-visible (a retired token
//! is still a string; a retired role key is still an entry of the open map
//! `style.palettes`), so both are declared here and the pre-check in `migrate`
//! looks for them: the token respell as identity-path moves, the role keys as
//! a `MapKeyDeletion`. `style.text.column.gap` is a plain deletion.
//!
//! Reach: a board nested in a board is opaque to the field walk past the first
//! level, so a nested sub-board's own `charts:` map is not respelled.

use std::sync::Arc;

use crate::decl::{
    path, Declarations, Deletion, Grammars, MapKeyDeletion, Mapped, ScalarKey, ValueMap,
};

const COLOR_TOKEN_FIELD_TAILS: &[&str] = &[
    "color",
    "fill",
    "stroke",
    "null_color",
    "neutral_color",
    "glyph_color",
    "static",
    "positive",
    "negative",
    "warning",
    "info",
    "color_active",
    "color_inactive",
    "color_disabled",
    "focus_color",
    "drop_line_color",
    "accent",
    "muted",
    "background",
];
const PALETTE_NAME_FIELD_TAILS: &[&str] = &["palette", "single_series_palette"];
const CATEGORY_PIN: &[&str] = &["style", "charts", "category_colors", "*", "values", "*"];

/// The alias vocabulary every shipped categorical palette shares; a role-indirected
/// token does not know which family `category` is bound to, so every alias is covered.
const ROLE_ALIASES: &[&str] = &[
    "blue", "sky", "green", "gold", "orange", "purple", "sage", "brown", "gray", "charcoal",
];
/// No categorical palette ships more stops than this.
const MAX_CATEGORICAL_SLOTS: usize = 10;
/// The families that shipped `<family>-<suffix>.yml` companions, each with 10 stops.
const FAMILIES: &[&str] = &["vivid-10", "editorial-10"];
const ROLE_PREFIX: &str = "category";
/// Retired suffix to the literal variant word that replaced it.
const SUFFIX_SUCCESSORS: &[(&str, &str)] = &[
    ("dark", "dark"),
    ("light", "light"),
    ("ghost", "pale"),
    ("ink", "deep"),
];

/// Every retired color-token / palette-name spelling mapped to its replacement.
/// The domain is closed: only the aliases and slots the shipped catalog declared
/// were ever addressable.
fn retired_token_value_map() -> ValueMap {
    let mut map = ValueMap::new();
    let mut put = |from: String, to: String| {
        map.insert(ScalarKey::Str(from), Mapped::Str(to));
    };
    for (suffix, variant) in SUFFIX_SUCCESSORS {
        let old_role = format!("{ROLE_PREFIX}_{suffix}");
        put(old_role.clone(), format!("{ROLE_PREFIX}.{variant}"));
        for slot in 1..=MAX_CATEGORICAL_SLOTS {
            put(
                format!("{old_role}[{slot}]"),
                format!("{ROLE_PREFIX}[{slot}].{variant}"),
            );
        }
        for alias in ROLE_ALIASES {
            put(
                format!("{old_role}.{alias}"),
                format!("{ROLE_PREFIX}.{alias}.{variant}"),
            );
        }
        for family in FAMILIES {
            let old_family = format!("{family}-{suffix}");
            put(old_family.clone(), format!("{family}.{variant}"));
            for slot in 1..=10 {
                put(
                    format!("{old_family}.{slot}"),
                    format!("{family}.{slot}.{variant}"),
                );
                put(
                    format!("{old_family}:{slot}"),
                    format!("{family}:{slot}.{variant}"),
                );
                put(
                    format!("{old_family}:{slot}_r"),
                    format!("{family}:{slot}_r.{variant}"),
                );
            }
            put(format!("{old_family}_r"), format!("{family}_r.{variant}"));
        }
    }
    // Current spellings mapped to themselves: the value map is total over its
    // field's domain, and the value gate has something to prove itself against.
    for current in ["category[2]", "category.blue", "vivid-10", "vivid-10.3"] {
        put(current.to_string(), current.to_string());
    }
    map
}

fn role_message(role: &str, variant: &str) -> String {
    format!("style.palettes.{role} is no longer a role: write category[N].{variant} or category.<alias>.{variant}. Remove the key.")
}

pub fn declarations(g: &Grammars) -> Declarations {
    let value_map = Arc::new(retired_token_value_map());
    let mut moves = g.token_respell_moves(
        &value_map,
        COLOR_TOKEN_FIELD_TAILS,
        PALETTE_NAME_FIELD_TAILS,
        &[&["style", "palettes", "*"]],
    );
    let mut pin = g.identity(&path(CATEGORY_PIN), &value_map);
    pin.sub_board_prefixes = g.nesting_prefixes();
    moves.push(pin);
    let roles: Vec<(String, String)> = SUFFIX_SUCCESSORS
        .iter()
        .map(|(suffix, variant)| (format!("{ROLE_PREFIX}_{suffix}"), (*variant).to_string()))
        .collect();
    let mut gap = Deletion::new(g.source, g.target, &["style", "text", "column", "gap"]);
    gap.reason = Some("Prose columns sit on the board's card grid now, so the gutter between them is the gap the layout puts between cards plus twice the card padding and cannot be set on the text. The authored column gap was dropped; style.text.column.max_number, max_chars and rule still apply.".into());
    Declarations {
        moves,
        deletions: vec![gap],
        map_key_deletions: vec![MapKeyDeletion {
            source: g.source,
            target: g.target,
            container_path: path(&["style", "palettes"]),
            messages: roles
                .iter()
                .map(|(r, v)| (r.clone(), role_message(r, v)))
                .collect(),
        }],
        retired_tokens: vec![value_map],
        retired_role_keys: roles.into_iter().map(|(r, _)| r).collect(),
        ..Declarations::default()
    }
}
