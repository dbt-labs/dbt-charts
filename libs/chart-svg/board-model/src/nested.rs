//! A nested board's style scope: what crosses from its parent's authored style into its own, as
//! `compile_board_resolved_style` builds it before the cascade runs.
//!
//! Three steps on patches: [`scope_patch`] (the parent's patch minus the fields marked
//! `nested = drop`, under the board's own), [`pin_unauthored_background`] (CSS does not inherit a
//! background, so an unset one is pinned) and [`with_nested_layout_gap`] (a nested board pitches by
//! `style.gap`, so an authored `layout.<type>.gap` becomes it).
//!
//! A nested board is `Resolved::base(theme + in_force.resolve)`, not a `Resolved::apply` over its
//! parent: the scope drops fields the parent resolved, as Python resolves theme plus scoped patch.
//! `in_force` composes the three into the patch to resolve and the patch the board's children
//! inherit. `tests/test_board_model_nested_scope_fixture.py` checks them against Python; the
//! theme-switch canvas and ink canvas are `bind`'s.

use crate::facet::Color;
use crate::style::style::{Gap, StylePatch};
use model_core::Tri;

/// The layouts whose `layout.<type>.gap` a nested board reads.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GapLayout {
    Rows,
    Cols,
    Grid,
}

/// The patch in force inside a nested scope, given its parent's: `inherited` without its
/// `nested = drop` fields, with `own` merged on top. A scope with nothing to inherit takes `own`
/// as it is.
pub fn scope_patch(inherited: Option<&StylePatch>, own: Option<&StylePatch>) -> Option<StylePatch> {
    let Some(inherited) = inherited else {
        return own.cloned();
    };
    let mut kept = inherited.clone();
    kept.footer = Default::default();
    kept.timestamp = Default::default();
    kept.padding = Default::default();
    kept.margin = Default::default();
    if let Some(own) = own {
        kept.merge(own.clone());
    }
    Some(kept)
}

/// `patch` with `background` forced to `replacement` unless this nested scope's own raw style set
/// it: a nested scope's background is never an ancestor's color crossing in.
pub fn pin_unauthored_background(
    mut patch: StylePatch,
    own: Option<&StylePatch>,
    replacement: &Color,
) -> StylePatch {
    if !own.is_some_and(|o| o.background.is_some()) {
        patch.background = Some(replacement.clone());
    }
    patch
}

/// `patch` with its `layout.<kind>.gap` as its `gap`, unless it sets `gap` itself or authors no
/// layout gap: an unset layout gap leaves `gap` unset rather than taking the theme's.
pub fn with_nested_layout_gap(mut patch: StylePatch, kind: Option<GapLayout>) -> StylePatch {
    let Some(kind) = kind else { return patch };
    if matches!(patch.gap, Tri::Value(_)) {
        return patch;
    }
    let gap = match kind {
        GapLayout::Rows => &patch.layout.rows.gap,
        GapLayout::Cols => &patch.layout.cols.gap,
        GapLayout::Grid => &patch.layout.grid.gap,
    };
    if let Some(gap) = gap {
        patch.gap = Tri::Value(Gap::from(*gap));
    }
    patch
}

/// What `compile_board_resolved_style` resolves for a nested board and what it hands its own
/// children: the patch to resolve over the theme (scoped, background pinned, layout gap applied)
/// and the patch the children inherit from (the same before the gap; a board that authored no style
/// passes on the inherited patch unpinned, `None` when nothing was authored anywhere in the lineage).
pub struct InForce {
    pub resolve: StylePatch,
    pub children: Option<StylePatch>,
}

/// The nested scope of a board whose parent authored `parent`: [`scope_patch`], then
/// [`pin_unauthored_background`] with `replacement`, then [`with_nested_layout_gap`] for `layout`.
pub fn in_force(
    parent: Option<&StylePatch>,
    own: Option<&StylePatch>,
    replacement: &Color,
    layout: Option<GapLayout>,
) -> InForce {
    let own = own.map(block_font_sets_prose_font);
    let own = own.as_ref();
    let scoped = scope_patch(parent, own);
    let pinned = pin_unauthored_background(scoped.clone().unwrap_or_default(), own, replacement);
    InForce {
        resolve: with_nested_layout_gap(pinned.clone(), layout),
        children: if own.is_some() { Some(pinned) } else { scoped },
    }
}

/// A nested block's authored `font.family`/`font.size` also set its prose: the prose takes its font
/// from the theme's named `body` font, which already has both, so a block's `font` would never
/// reach it. An authored `text.font` still wins.
fn block_font_sets_prose_font(style: &StylePatch) -> StylePatch {
    if style.font.family.is_none() && style.font.size.is_none() {
        return style.clone();
    }
    let mut prose = StylePatch::default();
    prose.text.font.family = style.font.family.clone();
    prose.text.font.size = style.font.size;
    prose.merge(style.clone());
    prose
}

/// The `nested = drop` fields of [`StylePatch`], checked against the field table in the tests.
pub fn dropped_fields() -> Vec<&'static str> {
    <crate::style::style::Style as model_core::Model>::info()
        .fields
        .iter()
        .filter(|f| f.attrs.nested_drop)
        .map(|f| f.name)
        .collect()
}

/// A layer's `charts.pie` reaches its donuts and its `charts.bar` its funnels, and the layer's own
/// `charts.donut` or `charts.funnel` wins over it, so a board's pie beats the theme's donut default
/// and a board's bar the theme's funnel recipe (`_family_reaches_recipe`).
pub fn family_reaches_recipe(patch: &mut StylePatch) {
    if !model_core::Empty::is_empty(&patch.charts.pie) {
        let mut donut = (*patch.charts.pie).clone();
        if let Tri::Value(own) = std::mem::replace(&mut patch.charts.donut, Tri::Unset) {
            donut.merge(own);
        }
        patch.charts.donut = Tri::Value(donut);
    }
    if !model_core::Empty::is_empty(&patch.charts.bar) {
        let mut funnel = (*patch.charts.bar).clone();
        if let Tri::Value(own) = std::mem::replace(&mut patch.charts.funnel, Tri::Unset) {
            funnel.merge(own);
        }
        patch.charts.funnel = Tri::Value(funnel);
    }
}
