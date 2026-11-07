//! The one environment definition for every user-authored text: board SQL,
//! display text, labels, `where:`, annotation text, custom chart SVG and
//! markdown. Mirrors `compile/template/environment.py`: no site builds its own.
//!
//! minijinja has no attribute reflection to escape from, so the sandbox is the
//! absence of unsafe `Object` implementations plus the instruction fuel below,
//! not a policy class like jinja2's `ImmutableSandboxedEnvironment`.

use minijinja::syntax::SyntaxConfig;
use minijinja::{Environment, UndefinedBehavior};

/// Where a template is authored. The kind picks the syntax and how the source
/// is read (markdown masks fenced code; `where:` is an expression).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Sql,
    Text,
    Label,
    Where,
    Annotation,
    Svg,
    Markdown,
    /// A registered-view YAML template: `[[ ]]`, `[% %]`, `[# #]`.
    RegisteredView,
}

/// Instruction budget for one render: a runaway loop fails the chart instead
/// of holding a worker. The same figure `chart-renderer`'s custom family uses.
pub const FUEL: u64 = 20_000_000;

/// Namespaces and callables that are never user variables.
pub const RESERVED_VARIABLE_NAMES: &[&str] = &[
    "filter",
    "filter_date_range",
    "queries",
    "glob_boards",
    "glob_folders",
];

pub const LISTING_FUNCTIONS: &[&str] = &["glob_boards", "glob_folders"];

pub fn syntax(kind: Kind) -> Result<SyntaxConfig, minijinja::Error> {
    match kind {
        Kind::RegisteredView => SyntaxConfig::builder()
            .variable_delimiters("[[", "]]")
            .block_delimiters("[%", "%]")
            .comment_delimiters("[#", "#]")
            .build(),
        _ => Ok(SyntaxConfig::default()),
    }
}

/// The environment `bind` renders through: this kind's syntax, strict
/// undefined, the fuel budget, no auto-escape (SVG escaping is the caller's).
pub fn environment(kind: Kind) -> Result<Environment<'static>, minijinja::Error> {
    let mut env = Environment::new();
    env.set_syntax(syntax(kind)?);
    env.set_undefined_behavior(UndefinedBehavior::Strict);
    env.set_fuel(Some(FUEL));
    Ok(env)
}
