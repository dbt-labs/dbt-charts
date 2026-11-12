//! The style scope of every nested board and tab: Python's `_propagate_resolved_style` walk.
//!
//! Each nested board resolves over its theme the patch `nested::in_force` gives it from its
//! parent's: the parent's authored style without its `nested = drop` fields, this board's own on
//! top, an unauthored background pinned transparent, the layout's gap applied. A tab is a nested
//! board of its own layout (rows when it has none) carrying the tab's `style:`. The result is keyed
//! by the board's position, as every other per-board fact is.

use crate::files::is_path_ref;
use crate::merge_warnings::leaves;
use crate::restated::dotted;
use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::board::theme::{built_in_theme, is_built_in, BASE_THEME};
use board_model::board::BoardBodyAuthored;
use board_model::cascade;
use board_model::facet::Color;
use board_model::ids::{ItemId, Step};
use board_model::names::ThemeName;
use board_model::nested::{in_force, pie_reaches_donut, scope_patch, GapLayout};
use board_model::style::style::Style;
use diagnostic::{diag, restated_text, Diagnostic, ModelPath};
use model_core::{CascadeError, Leaf, Resolved, ToAuthored, ToPatch, Tri};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};

type StylePatch = <Style as Leaf>::Patch;

/// What went wrong resolving a nested scope.
#[derive(Debug, Clone, PartialEq)]
pub enum ScopeError {
    /// The style of the board or tab refused, coded.
    Invalid(Vec<Diagnostic>),
    /// A nested board names a theme that is not a built-in one.
    Theme(Box<Diagnostic>),
}

impl std::fmt::Display for ScopeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(d) => f.write_str(d.first().map_or("", |d| d.code)),
            Self::Theme(d) => f.write_str(d.code),
        }
    }
}

fn style_err(e: impl std::fmt::Display) -> ScopeError {
    ScopeError::Invalid(vec![diag::err_internal(e.to_string())])
}

/// A refused cascade as diagnostics, as Python raises them: an unknown font is `ERR-FONT-UNKNOWN`,
/// a font cycle or a fill literal its type refuses (a model bug) `ERR-INTERNAL`, and every other
/// refusal comes from resolving a color token or palette, `ERR-COLOR-TOKEN` over `scope`.
pub(crate) fn cascade_diagnostics(scope: &str, e: &CascadeError) -> Vec<Diagnostic> {
    match e {
        CascadeError::Refused(refused) => refused
            .iter()
            .map(|r| match r.why.as_str() {
                why if why.starts_with("Unknown font") => diag::err_font_unknown(why),
                why if why.starts_with("Fonts extend") || why.starts_with("the fill literal") => {
                    diag::err_internal(format!("{scope}.{}: {why}", r.at))
                }
                why => diag::err_color_token(scope, why),
            })
            .collect(),
        CascadeError::Unset(_) => vec![diag::err_internal(format!("{scope}: {e}"))],
    }
}

/// The theme a nested board declares: the last built-in name among its `extends:` entries and
/// `theme:` (`_theme_from_extends`), `None` when it names none. A path entry is a board reference
/// nothing folds for a nested board; a plain name that is no authorable theme is refused, as
/// `validate_theme_names` does.
fn declared_theme(body: &BoardBodyAuthored) -> Result<Option<String>, ScopeError> {
    let mut entries: Vec<&str> = Vec::new();
    if let Tri::Value(e) = &body.extends {
        entries.extend(e.0.iter().map(String::as_str));
    }
    if let Tri::Value(t) = &body.theme {
        entries.push(t.as_str());
    }
    let mut declared = None;
    for entry in entries {
        if is_path_ref(entry) {
            continue;
        }
        if entry == BASE_THEME || !is_built_in(entry) || entry.starts_with('_') {
            return Err(ScopeError::Theme(Box::new(diag::err_unknown_theme(
                entry,
                ThemeName::VALUES.to_vec(),
            ))));
        }
        declared = Some(entry.to_string());
    }
    Ok(declared)
}

/// What a nested scope inherits: its parent's authored patch and effective theme name.
/// Its patch as nested scopes see it, the same before the pinned background and the prose font a
/// block's font sets (which the restated-style comparison leaves out), and its theme.
type Parent<'p> = (Option<&'p StylePatch>, Option<&'p StylePatch>, &'p str);

struct Walk {
    transparent: Color,
    out: BTreeMap<String, Resolved<Style>>,
    /// Each nested board's restated-style findings, by position.
    restated: BTreeMap<String, Vec<Diagnostic>>,
    /// Each nested board's own authored style, by position.
    authored: BTreeMap<String, StylePatch>,
    /// The leaves each theme the walk met sets, spelled once.
    themes: HashMap<String, HashMap<String, Value>>,
}

/// The scopes of a board's nested boards and tabs, and each nested board's restated-style findings.
pub struct Scopes {
    pub styles: BTreeMap<String, Resolved<Style>>,
    pub restated: BTreeMap<String, Vec<Diagnostic>>,
    /// What each nested board's `style:` itself authors, by position.
    pub authored: BTreeMap<String, StylePatch>,
}

/// Every nested board's and tab's resolved style under `layout`, given the style the root board
/// authored and the theme it resolves over. The root's own style is the first parent.
pub fn nested_styles(
    layout: Option<&LayoutAuthored>,
    root_style: Option<&StylePatch>,
    theme: &str,
) -> Result<Scopes, ScopeError> {
    let transparent = Color::try_from("transparent".to_string()).map_err(style_err)?;
    let mut walk = Walk {
        transparent,
        out: BTreeMap::new(),
        restated: BTreeMap::new(),
        authored: BTreeMap::new(),
        themes: HashMap::new(),
    };
    if let Some(layout) = layout {
        let children = scope_patch(None, root_style);
        walk.layout(
            layout,
            &mut Vec::new(),
            (children.as_ref(), children.as_ref(), theme),
        )?;
    }
    Ok(Scopes {
        styles: walk.out,
        restated: walk.restated,
        authored: walk.authored,
    })
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

pub(crate) fn own_patch<T>(style: &Tri<Box<T>>) -> Option<StylePatch>
where
    T: ToPatch<Out = StylePatch> + Clone,
{
    match style {
        Tri::Value(own) => Some((**own).clone().to_patch()),
        Tri::Unset | Tri::Null => None,
    }
}

impl Walk {
    fn layout<'k>(
        &mut self,
        layout: &'k LayoutAuthored,
        at: &mut Vec<Step<'k>>,
        parent: Parent<'_>,
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
                    let (children, plain, theme) = self.scope(
                        &tab.style,
                        None,
                        gap_layout(tab.layout.as_ref()),
                        at,
                        parent,
                    )?;
                    if let Some(inner) = &tab.layout {
                        self.layout(inner, at, (children.as_ref(), plain.as_ref(), &theme))?;
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
        parent: Parent<'_>,
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
        parent: Parent<'_>,
    ) -> Result<(), ScopeError> {
        let LayoutItemAuthored::Board(nested) = item else {
            return Ok(());
        };
        let layout = nested.body.layout.as_ref();
        let declared = declared_theme(&nested.body)?;
        let (children, plain, theme) =
            self.scope(&nested.body.style, declared, gap_layout(layout), at, parent)?;
        if let Some(layout) = layout {
            self.layout(layout, at, (children.as_ref(), plain.as_ref(), &theme))?;
        }
        Ok(())
    }

    /// The leaves of a nested board's own style that its parent's scope over its theme already
    /// gives it (Python's `tree_restated`, nested branch). Its background is pinned, never
    /// inherited, so restating the parent's changes the render and is not judged.
    fn restated(
        &mut self,
        own: &StylePatch,
        parent: Option<&StylePatch>,
        theme: &str,
        base: &StylePatch,
        at: &[Step],
    ) {
        let position = ItemId::at(at);
        let kept: HashMap<String, Value> = scope_patch(parent, None)
            .map(|kept| leaves(&kept).into_iter().collect())
            .unwrap_or_default();
        let below = self
            .themes
            .entry(theme.to_string())
            .or_insert_with(|| leaves(base).into_iter().collect());
        let prefix = dotted(&position);
        let found: Vec<Diagnostic> = leaves(own)
            .into_iter()
            .filter(|(path, value)| {
                path.split('.').next() != Some("background")
                    && !path.ends_with(".extends")
                    && kept.get(path).or_else(|| below.get(path)) == Some(value)
            })
            .map(|(path, value)| {
                let at = format!("{prefix}.style.{path}");
                let model_path = ModelPath(at.split('.').map(str::to_string).collect());
                diag::warn_restated_style(
                    at,
                    restated_text(&value),
                    "what this board inherits from its parent",
                )
                .with_sink_param("why", "inherited".into())
                .at_path(model_path)
            })
            .collect();
        if !found.is_empty() {
            self.restated.insert(position.as_str().to_string(), found);
        }
    }

    /// Resolve the board at `at`, under the theme it `declared` (else its parent's), and return
    /// the patch its own children inherit with the theme they inherit. A scope that switches theme
    /// paints that theme's own canvas where it authors no background.
    fn scope<T>(
        &mut self,
        style: &Tri<Box<T>>,
        declared: Option<String>,
        layout: Option<GapLayout>,
        at: &[Step],
        (parent_patch, plain_parent, parent_theme): Parent<'_>,
    ) -> Result<(Option<StylePatch>, Option<StylePatch>, String), ScopeError>
    where
        T: ToPatch<Out = StylePatch> + Clone,
    {
        let own = own_patch(style);
        if let Some(own) = &own {
            self.authored
                .insert(ItemId::at(at).as_str().to_string(), own.clone());
        }
        let theme = declared.unwrap_or_else(|| parent_theme.to_string());
        let built_in = built_in_theme(&theme).map_err(style_err)?;
        let Tri::Value(base) = &built_in.style else {
            return Err(style_err(format!("theme {theme} has no style")));
        };
        if let Some(own) = &own {
            self.restated(own, plain_parent, &theme, base, at);
        }
        let canvas = match (&base.background, theme != parent_theme) {
            (Some(own), true) => own,
            _ => &self.transparent,
        };
        let force = in_force(parent_patch, own.as_ref(), canvas, layout);
        let mut resolve = force.resolve;
        pie_reaches_donut(&mut resolve);
        let mut folded = (**base).clone();
        let mut context = folded.clone();
        context.palettes.extend(resolve.palettes.clone());
        context.roles.extend(resolve.roles.clone());
        board_model::palette::adopt_board_palette(&mut context, &resolve);
        folded.merge(resolve);
        let resolved = Resolved::base_in(&cascade::TABLES, folded, &context).map_err(|e| {
            ScopeError::Invalid(cascade_diagnostics(
                &format!("{}.style", ItemId::at(at).as_str()),
                &e,
            ))
        })?;
        self.out
            .insert(ItemId::at(at).as_str().to_string(), resolved);
        Ok((
            force.children,
            scope_patch(plain_parent, own.as_ref()),
            theme,
        ))
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
