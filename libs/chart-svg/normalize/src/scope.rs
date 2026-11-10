//! The style scope of every nested board and tab: Python's `_propagate_resolved_style` walk.
//!
//! Each nested board resolves over its theme the patch `nested::in_force` gives it from its
//! parent's: the parent's authored style without its `nested = drop` fields, this board's own on
//! top, an unauthored background pinned transparent, the layout's gap applied. A tab is a nested
//! board of its own layout (rows when it has none) carrying the tab's `style:`. The result is keyed
//! by the board's position, as every other per-board fact is.

use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::board::theme::built_in_theme;
use board_model::cascade;
use board_model::facet::Color;
use board_model::ids::{ItemId, Step};
use board_model::nested::{in_force, pie_reaches_donut, scope_patch, GapLayout};
use board_model::style::style::Style;
use model_core::{Leaf, Resolved, ToAuthored, ToPatch, Tri};
use std::collections::BTreeMap;

type StylePatch = <Style as Leaf>::Patch;

/// What went wrong resolving a nested scope.
#[derive(Debug, Clone, PartialEq)]
pub struct ScopeError(pub String);

impl std::fmt::Display for ScopeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

struct Walk<'a> {
    theme: &'a StylePatch,
    transparent: Color,
    out: BTreeMap<String, Resolved<Style>>,
}

/// Every nested board's and tab's resolved style under `layout`, given the style the root board
/// authored and the theme it resolves over. The root's own style is the first parent.
pub fn nested_styles(
    layout: Option<&LayoutAuthored>,
    root_style: Option<&StylePatch>,
    theme: &str,
) -> Result<BTreeMap<String, Resolved<Style>>, ScopeError> {
    let built_in = built_in_theme(theme).map_err(|e| ScopeError(e.to_string()))?;
    let Tri::Value(base) = &built_in.style else {
        return Err(ScopeError(format!("theme {theme} has no style")));
    };
    let transparent =
        Color::try_from("transparent".to_string()).map_err(|e| ScopeError(e.to_string()))?;
    let mut walk = Walk {
        theme: base,
        transparent,
        out: BTreeMap::new(),
    };
    if let Some(layout) = layout {
        let children = scope_patch(None, root_style);
        walk.layout(layout, &mut Vec::new(), children.as_ref())?;
    }
    Ok(walk.out)
}

/// The layout a nested board's `layout.<type>.gap` is read from: rows when it has none, and none
/// for tabs.
fn gap_layout(layout: Option<&LayoutAuthored>) -> Option<GapLayout> {
    match layout {
        None | Some(LayoutAuthored::Rows(_)) => Some(GapLayout::Rows),
        Some(LayoutAuthored::Cols(_)) => Some(GapLayout::Cols),
        Some(LayoutAuthored::Grid(_)) => Some(GapLayout::Grid),
        Some(LayoutAuthored::Tabs(_)) => None,
    }
}

fn own_patch<T>(style: &Tri<Box<T>>) -> Option<StylePatch>
where
    T: ToPatch<Out = StylePatch> + Clone,
{
    match style {
        Tri::Value(own) => Some((**own).clone().to_patch()),
        Tri::Unset | Tri::Null => None,
    }
}

impl Walk<'_> {
    fn layout<'k>(
        &mut self,
        layout: &'k LayoutAuthored,
        at: &mut Vec<Step<'k>>,
        parent: Option<&StylePatch>,
    ) -> Result<(), ScopeError> {
        match layout {
            LayoutAuthored::Rows(items) => self.list("rows", items, at, parent),
            LayoutAuthored::Cols(items) => self.list("cols", items, at, parent),
            LayoutAuthored::Grid(g) => {
                at.push(Step::Key("grid"));
                at.push(Step::Key("items"));
                for (i, cell) in g.items.iter().flatten().enumerate() {
                    if let Some(item) = &cell.item {
                        at.push(Step::Index(i));
                        at.push(Step::Key("item"));
                        self.item(item, at, parent)?;
                        at.truncate(at.len() - 2);
                    }
                }
                at.truncate(at.len() - 2);
                Ok(())
            }
            LayoutAuthored::Tabs(t) => {
                at.push(Step::Key("tabs"));
                at.push(Step::Key("items"));
                for (i, tab) in t.items.iter().flatten().enumerate() {
                    at.push(Step::Index(i));
                    let scope =
                        self.scope(&tab.style, gap_layout(tab.layout.as_ref()), at, parent)?;
                    if let Some(inner) = &tab.layout {
                        self.layout(inner, at, scope.as_ref())?;
                    }
                    at.pop();
                }
                at.truncate(at.len() - 2);
                Ok(())
            }
        }
    }

    fn list<'k>(
        &mut self,
        key: &'k str,
        items: &'k [LayoutItemAuthored],
        at: &mut Vec<Step<'k>>,
        parent: Option<&StylePatch>,
    ) -> Result<(), ScopeError> {
        at.push(Step::Key(key));
        for (i, it) in items.iter().enumerate() {
            at.push(Step::Index(i));
            self.item(it, at, parent)?;
            at.pop();
        }
        at.pop();
        Ok(())
    }

    fn item<'k>(
        &mut self,
        item: &'k LayoutItemAuthored,
        at: &mut Vec<Step<'k>>,
        parent: Option<&StylePatch>,
    ) -> Result<(), ScopeError> {
        let LayoutItemAuthored::Board(nested) = item else {
            return Ok(());
        };
        let layout = nested.body.layout.as_ref();
        let scope = self.scope(&nested.body.style, gap_layout(layout), at, parent)?;
        if let Some(layout) = layout {
            self.layout(layout, at, scope.as_ref())?;
        }
        Ok(())
    }

    /// Resolve the board at `at` and return the patch its own children inherit.
    fn scope<T>(
        &mut self,
        style: &Tri<Box<T>>,
        layout: Option<GapLayout>,
        at: &[Step],
        parent: Option<&StylePatch>,
    ) -> Result<Option<StylePatch>, ScopeError>
    where
        T: ToPatch<Out = StylePatch> + Clone,
    {
        let own = own_patch(style);
        let force = in_force(parent, own.as_ref(), &self.transparent, layout);
        let mut resolve = force.resolve;
        pie_reaches_donut(&mut resolve);
        let mut folded = self.theme.clone();
        let mut context = folded.clone();
        context.palettes.extend(resolve.palettes.clone());
        context.roles.extend(resolve.roles.clone());
        folded.merge(resolve);
        let resolved = Resolved::base_in(&cascade::TABLES, folded, &context)
            .map_err(|e| ScopeError(format!("{}: {e}", ItemId::at(at).as_str())))?;
        self.out
            .insert(ItemId::at(at).as_str().to_string(), resolved);
        Ok(force.children)
    }
}

/// Write each nested board's and tab's resolved style into its `style:` slot, so the board built
/// from the patch holds the scope it is drawn in, and give a tab with no layout its empty `rows`.
pub fn install(layout: &mut LayoutAuthored, nested: &BTreeMap<String, Resolved<Style>>) {
    install_layout(layout, &mut Vec::new(), nested);
}

fn install_layout<'k>(
    layout: &'k mut LayoutAuthored,
    at: &mut Vec<Step<'k>>,
    nested: &BTreeMap<String, Resolved<Style>>,
) {
    match layout {
        LayoutAuthored::Rows(items) => install_list("rows", items, at, nested),
        LayoutAuthored::Cols(items) => install_list("cols", items, at, nested),
        LayoutAuthored::Grid(g) => {
            at.push(Step::Key("grid"));
            at.push(Step::Key("items"));
            for (i, cell) in g.items.iter_mut().flatten().enumerate() {
                if let Some(item) = &mut cell.item {
                    at.push(Step::Index(i));
                    at.push(Step::Key("item"));
                    install_item(item, at, nested);
                    at.truncate(at.len() - 2);
                }
            }
            at.truncate(at.len() - 2);
        }
        LayoutAuthored::Tabs(t) => {
            at.push(Step::Key("tabs"));
            at.push(Step::Key("items"));
            for (i, tab) in t.items.iter_mut().flatten().enumerate() {
                at.push(Step::Index(i));
                set_style(&mut tab.style, at, nested);
                // A tab that writes no layout (only `text:`) holds Python's empty `rows`. This
                // runs after the layout facts, which Python computes before it fills them.
                let inner = tab
                    .layout
                    .get_or_insert_with(|| LayoutAuthored::Rows(Vec::new()));
                install_layout(inner, at, nested);
                at.pop();
            }
            at.truncate(at.len() - 2);
        }
    }
}

fn install_list<'k>(
    key: &'k str,
    items: &'k mut [LayoutItemAuthored],
    at: &mut Vec<Step<'k>>,
    nested: &BTreeMap<String, Resolved<Style>>,
) {
    at.push(Step::Key(key));
    for (i, it) in items.iter_mut().enumerate() {
        at.push(Step::Index(i));
        install_item(it, at, nested);
        at.pop();
    }
    at.pop();
}

fn install_item<'k>(
    item: &'k mut LayoutItemAuthored,
    at: &mut Vec<Step<'k>>,
    nested: &BTreeMap<String, Resolved<Style>>,
) {
    let LayoutItemAuthored::Board(board) = item else {
        return;
    };
    set_style(&mut board.body.style, at, nested);
    if let Some(layout) = &mut board.body.layout {
        install_layout(layout, at, nested);
    }
}

fn set_style<T>(slot: &mut Tri<Box<T>>, at: &[Step], nested: &BTreeMap<String, Resolved<Style>>)
where
    StylePatch: ToAuthored<Out = T>,
{
    if let Some(resolved) = nested.get(ItemId::at(at).as_str()) {
        *slot = Tri::Value(Box::new((*resolved.inherited).clone().to_authored()));
    }
}
