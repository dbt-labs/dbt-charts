//! `core/compile/models/board/`: the board, root and nested.
//!
//! A board is [`Board`], the root, or a [`NestedBoard`] placed in a parent's layout;
//! both hold a [`BoardBody`]. What only a root can say lives on `Board`: its own pixel
//! width, the links and locale, listing metadata and the schema stamp. A nested board that
//! writes one of those is an unknown key.
//!
//! The spine holds authored meaning. Left off, with where they went:
//!
//! - Sizing outputs (`width`, `height`, `x`, `y`, `content_*`, `calculated_*`,
//!   `title_shift`, `pie_mode`, `prose_plan`): render `layout`.
//! - Provenance (`base_dir`, `source_path`, `authored_style`, `authored_width`) and working
//!   state (`variable_registry`, `resolved_style`, `chart_style_context`,
//!   `chart_styles`, `meta`): deleted.
//! - Derived facts (`details_variable`, `details_summary`, `details_expanded_summary`,
//!   `tab_titles`, `tab_slugs`, `tab_variable`, `default_tab`, `variable_defaults`,
//!   `level`, `visible_variables`, `placed_charts`): `BoardIndex`.
//! - `sources` (`dbt_charts.yml`): `Config`, out of scope.
//!
//! Not yet ported, each a field added when its type lands: `Board.links`, `Board.regional`,
//! `BoardBody.cache`, `BoardBody.incremental`.

pub mod layout;
pub mod theme;
pub mod value;

use crate::chart::Chart;
use crate::facet::{DisplayText, Markdown};
use crate::ids::{BoardId, ChartId};
use crate::names::ThemeName;
use crate::query::Query;
use crate::string_enum;
use crate::style::style::Style;
use crate::variable::Variable;
use indexmap::IndexMap;
pub use layout::{
    items, GridItem, GridLayout, Layout, LayoutItem, NestedBoard, Placement, TabItem, TabLayout,
    TabPosition,
};
use model_derive::Model;
pub use value::{Extends, Length, SingleRowBoolProbe, Visible, Width};

string_enum!(
    /// How HTML in a board's body text renders.
    ///
    /// `safe-subset` is reserved for a parser-checked allowlist and currently renders as
    /// `none`. `trusted-raw` writes raw HTML through `foreignObject`: for first-party
    /// boards only, it is not a sandbox.
    HtmlPolicy { Escaped = "none", SafeSubset = "safe-subset", TrustedRaw = "trusted-raw" }
);

/// Collapsible section metadata for a board. A bare string is the collapsed `summary`.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "summary")]
pub struct BoardDetails {
    /// Label shown when the section is collapsed.
    pub summary: DisplayText,
    /// Label shown when the section is expanded.
    #[model(absent = "the summary is shown")]
    pub expanded_title: Option<DisplayText>,
    /// Whether the section is open by default.
    #[model(fill = false)]
    pub expanded: bool,
}

/// What every board holds, root or nested.
///
/// `variables`, `queries` and `charts` are the board's own scoped definitions: a nested board
/// authors them too (the corpus does). Normalize hoists a nested board's into the root's maps
/// under their tree positions, so a built nested board's maps are empty and the root's hold all.
/// The maps hold inline definitions only: a `{ref: ..}` entry, and a bare SQL string for a query,
/// are authored sugar the refs pass resolves before construct; their arms join with it.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(at_most_one_of(theme, extends)),
    constraint(at_least_one_of(
        rows,
        cols,
        grid,
        tabs,
        text,
        charts,
        title,
        nonempty(charts, title)
    ))
)]
pub struct BoardBody {
    /// Heading shown at the top of the board.
    #[model(absent = "no heading")]
    pub title: Option<DisplayText>,
    /// What search finds: what the board is for and which tables it reads. Never painted.
    #[model(absent = "no search notes")]
    pub notes: Option<DisplayText>,
    /// A Markdown block, as Jinja over query results.
    #[model(absent = "no text block", role = content)]
    pub text: Option<Markdown>,
    /// HTML rendering policy for the board's body text. Inline nested boards inherit it
    /// unless they set their own; tabs always inherit, and imported board files do not.
    #[model(fill = "none")]
    pub html_policy: HtmlPolicy,
    /// Default source name for all queries in this board.
    #[model(absent = "queries name their own source")]
    pub source: Option<String>,
    /// Named inputs that parameterize queries; each renders as a control unless it sets
    /// `visible: false`.
    #[model(fill = fill_empty)]
    pub variables: IndexMap<String, Variable>,
    /// Named result sets the charts draw from. Normalize hoists every inline query, and every
    /// nested board's, into this map.
    #[model(fill = fill_empty, role = content)]
    pub queries: IndexMap<String, Query>,
    /// Named chart definitions. Normalize hoists every inline chart, and every nested board's,
    /// into this map under its tree position; layout items hold the key. When no explicit layout
    /// is present, charts render as an implicit row layout in authored order.
    #[model(fill = fill_empty, role = content)]
    pub charts: IndexMap<String, Chart>,
    /// How the board is arranged: one of `rows`, `cols`, `grid`, `tabs`.
    #[model(keys, role = content, fill = fill_rows)]
    pub layout: Layout,
    /// When true, adds gap between cards. Cards are edge-to-edge otherwise.
    #[model(fill = false)]
    pub card_gap: bool,
    /// Collapsible section metadata.
    #[model(absent = "not collapsible")]
    pub details: Option<BoardDetails>,
    /// Explicit id for this board.
    #[model(fill = fill_board_id)]
    pub id: BoardId,
    /// Appearance overrides for this board (background, border, and more). Most fields this board
    /// or an ancestor board explicitly authors cascade to nested child boards.
    #[model(absent = "the theme's style applies")]
    pub style: Option<Box<Style>>,
    /// Themes or board files to inherit; the last one wins. A bare name is a built-in
    /// theme; write `./` before a file path (`./_brand.yml`). One name or a list.
    #[model(directive, absent = "inherits nothing beyond the project's meta.yml")]
    pub extends: Option<Extends>,
    /// Built-in theme name; shorthand for `extends: <name>`. Both is an error.
    #[model(directive, absent = "no theme named")]
    pub theme: Option<ThemeName>,
}

/// The root board: a body, and what only the file's own board says.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct Board {
    /// Keywords for grouping and searching boards.
    #[model(fill = fill_empty)]
    pub tags: Vec<String>,
    /// Additional URLs that redirect (302) to this board's canonical file-path URL; each
    /// entry starts with `/`.
    #[model(fill = fill_empty)]
    pub aliases: Vec<String>,
    /// The latest released schema version this file was last migrated to, written by
    /// `dct migrate` only (authored as `_schema_version`). Informational.
    #[model(directive, absent = "never migrated")]
    pub _schema_version: Option<String>,
    /// Render only this named chart with its dependent variables.
    #[model(absent = "renders the whole board")]
    pub chart_focus: Option<ChartId>,
    /// The board's own exact width in pixels, even past `style.frame.max_width`. Percentages
    /// are rejected: there is no parent to size relative to.
    #[model(absent = "hugs its content up to style.frame.max_width")]
    pub width: Option<Width>,
    #[flatten]
    pub body: BoardBody,
}
