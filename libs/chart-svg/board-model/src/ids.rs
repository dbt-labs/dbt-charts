//! Identities and cross-file references (spec §§ 3, 9; D-05).
//!
//! Inline charts, queries and templates are hoisted into the board's maps under an
//! id; a layout item or a chart holds the id. An authored name (`kpi_total`) and a
//! tree-position id (`@rows[0].cols[1]`) share one id type. A position starts with
//! `@`, which an authored name may not, so the two cannot collide.
//!
//! - [`BoardId`], [`ChartId`], [`TemplateRef`]: strings in a board's own scope.
//! - [`QueryId`]: a query's origin file and its name in that file. A local query
//!   carries the board's own file; an imported one the file it came from, so
//!   `query: revenue` in an imported chart means that file's `revenue`.
//! - [`VariableRef`], [`QueryRef`], [`ChartRef`]: the authored pointer to a
//!   definition in another file, `{ref: "<file>.<section>.<name>"}`; a bare string
//!   is accepted as the same pointer (`shorthand`). The file part stays as written:
//!   anchoring it at a directory is the fold's.
//!
//! Composing positions from the tree is the normalize `ids` pass; this module only
//! builds an id from the [`Step`]s the pass walked.

use diagnostic::py_repr;
use model_core::Keywords;
use model_derive::Model;
use project::ProjectPath;
use regex::Regex;
use serde_json::Value;
use std::fmt;
use std::sync::OnceLock;

fn repr(s: &str) -> String {
    py_repr(&Value::String(s.to_string()))
}

/// One step from a board down to a hoisted item: a mapping key or a list index.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step<'a> {
    Key(&'a str),
    Index(usize),
}

/// `@rows[0].cols[1]`; no steps is `@`, the board itself.
fn position(steps: &[Step]) -> String {
    let mut out = String::from("@");
    for (i, step) in steps.iter().enumerate() {
        match step {
            Step::Key(k) if i == 0 => out.push_str(k),
            Step::Key(k) => {
                out.push('.');
                out.push_str(k);
            }
            Step::Index(n) => out.push_str(&format!("[{n}]")),
        }
    }
    out
}

macro_rules! model_leaf {
    ($t:ty, $keywords:expr) => {
        impl model_core::Leaf for $t {
            type Whole = $t;
            type Authored = Option<$t>;
            type Patch = Option<$t>;
            const MERGE: model_core::Merge = model_core::Merge::Override;
            const EXPR: model_core::TypeExpr = model_core::TypeExpr::Keyed(
                &$keywords,
                &model_core::TypeExpr::Scalar(model_core::Scalar::Str),
            );
        }
        model_core::build_leaf!($t);
        model_core::from_scalar!($t, String);
    };
}

macro_rules! string_newtype {
    ($(#[$m:meta])* $name:ident, $keywords:expr, $check:expr) => {
        $(#[$m])*
        #[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
        pub struct $name(String);

        impl $name {
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl TryFrom<&str> for $name {
            type Error = String;
            fn try_from(v: &str) -> Result<Self, String> {
                let check: fn(&str) -> Result<(), String> = $check;
                check(v).map(|()| Self(v.to_string()))
            }
        }

        impl TryFrom<String> for $name {
            type Error = String;
            fn try_from(v: String) -> Result<Self, String> {
                Self::try_from(v.as_str())
            }
        }

        impl AsRef<str> for $name {
            fn as_ref(&self) -> &str {
                &self.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str(&self.0)
            }
        }

        model_leaf!($name, $keywords);
    };
}

/// A name an author writes: not empty, and not a position.
fn authored_name(what: &str, v: &str) -> Result<(), String> {
    match v {
        "" => Err(format!("{what} must not be empty")),
        v if v.starts_with('@') => Err(format!(
            "{what} {} starts with '@', which marks a position in the board",
            repr(v)
        )),
        _ => Ok(()),
    }
}

const NAME: Keywords = Keywords::pattern(r"^[^@]");

string_newtype!(
    /// A board's id: the authored `id:`, or the one normalize generates.
    BoardId,
    Keywords::pattern(r"^[\s\S]"),
    |v| if v.is_empty() {
        Err("board id must not be empty".to_string())
    } else {
        Ok(())
    }
);

string_newtype!(
    /// A chart in `Board.charts`: the key it is authored under, or the position of
    /// an inline chart.
    ChartId,
    NAME,
    |v| authored_name("chart id", v)
);

string_newtype!(
    /// A query's name within its file: authored, or the position of an inline query.
    QueryName,
    NAME,
    |v| authored_name("query name", v)
);

string_newtype!(
    /// A template in `Board.templates`: a project template's name (`templates/<name>.svg.j2`),
    /// or the position of an inline `svg:` source.
    TemplateRef,
    NAME,
    |v| authored_name("template name", v)
);

macro_rules! hoisted {
    ($($name:ident),+) => {$(
        impl $name {
            /// The id of the inline item at `steps` below the board.
            pub fn at(steps: &[Step]) -> Self {
                Self(position(steps))
            }

            /// Whether this is a position (an inline item) rather than an authored name.
            pub fn is_inline(&self) -> bool {
                self.0.starts_with('@')
            }
        }
    )+};
}
hoisted!(ChartId, QueryName, TemplateRef);

/// A layout item's place in the board tree, in the grammar of a position id (`@rows[2]`,
/// `@grid.items[2].item`, `@tabs.items[0].rows[1]`). A nested-board item's `ItemId` is the position
/// of its body, so the one string names the item and the board it places.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ItemId(String);

impl ItemId {
    /// The id of the item at `steps` below the board.
    pub fn at(steps: &[Step]) -> Self {
        Self(position(steps))
    }

    /// The board itself, `@`.
    pub fn root() -> Self {
        Self("@".to_string())
    }

    pub fn key(&self, key: &str) -> Self {
        match self.0.as_str() {
            "@" => Self(format!("@{key}")),
            at => Self(format!("{at}.{key}")),
        }
    }

    pub fn index(&self, n: usize) -> Self {
        Self(format!("{}[{n}]", self.0))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ItemId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A query in `Board.queries`: its origin file and its name there. Two files may
/// each define `revenue`; the file keeps them apart. The file holds no `#`, which is
/// what ends it in the printed form `<file>#<name>`.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct QueryId {
    file: ProjectPath,
    name: QueryName,
}

impl QueryId {
    pub fn new(file: ProjectPath, name: QueryName) -> Result<Self, String> {
        match file.as_str().contains('#') {
            true => Err(format!(
                "query file {} must not contain '#', which ends it in a query id",
                repr(file.as_str())
            )),
            false => Ok(Self { file, name }),
        }
    }

    /// The id of the inline query at `steps` below the board authored in `file`.
    pub fn at(file: ProjectPath, steps: &[Step]) -> Result<Self, String> {
        Self::new(file, QueryName::at(steps))
    }

    pub fn file(&self) -> &ProjectPath {
        &self.file
    }

    pub fn name(&self) -> &QueryName {
        &self.name
    }
}

impl QueryName {
    /// A hoisted item's name as `at` builds it: `@` and the position.
    fn from_position(v: &str) -> Result<Self, String> {
        match v.starts_with('@') {
            true => Ok(Self(v.to_string())),
            false => Err(format!("query position {} must start with '@'", repr(v))),
        }
    }
}

impl fmt::Display for QueryId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}#{}", self.file, self.name)
    }
}

macro_rules! ref_kind {
    ($(#[$m:meta])* $path:ident, $ref_:ident, $section:literal, $shape:literal, $pattern:literal) => {
        string_newtype!(
            #[doc = concat!("The `ref` of a [`", stringify!($ref_), "`]: `<file>.", $section, ".<name>`.")]
            $path,
            Keywords::pattern($pattern),
            |v| {
                static GRAMMAR: OnceLock<Option<Regex>> = OnceLock::new();
                let re = GRAMMAR.get_or_init(|| Regex::new($pattern).ok());
                if re.as_ref().is_some_and(|re| re.is_match(v)) {
                    Ok(())
                } else {
                    Err(format!(
                        "Invalid {} reference {}. Expected '<file>.{}.<name>', \
                         optionally prefixed with '../' segments.",
                        $shape,
                        repr(v),
                        $section
                    ))
                }
            }
        );

        impl $path {
            /// The file part as written: `shared.yml` in `shared.yml.queries.revenue`,
            /// possibly climbing with `../`. Not anchored, and `.yml` is not appended.
            pub fn file_part(&self) -> &str {
                self.0
                    .rsplit_once(concat!(".", $section, "."))
                    .map_or("", |(file, _)| file)
            }

            #[doc = concat!("The name in that file's `", $section, "` section.")]
            pub fn name(&self) -> &str {
                self.0
                    .rsplit_once(concat!(".", $section, "."))
                    .map_or("", |(_, name)| name)
            }
        }

        $(#[$m])*
        #[derive(Debug, Clone, PartialEq, Eq, Hash, Model)]
        #[model(shorthand = "ref")]
        pub struct $ref_ {
            #[doc = concat!("Reference path: `<file>.", $section, ".<name>`. May start with one or more `../` segments to reach a sibling directory. A bare string value is accepted as the reference.")]
            pub r#ref: $path,
        }

        impl TryFrom<&str> for $ref_ {
            type Error = String;
            fn try_from(v: &str) -> Result<Self, String> {
                $path::try_from(v).map(|r#ref| Self { r#ref })
            }
        }
    };
}

ref_kind!(
    /// Cross-file variable reference.
    VariableRefPath,
    VariableRef,
    "variables",
    "variable",
    r"^(?:\.\./)*[A-Za-z_][\w/\-.]*\.variables\.[A-Za-z_]\w*$"
);
ref_kind!(
    /// Cross-file query reference.
    QueryRefPath,
    QueryRef,
    "queries",
    "query",
    r"^(?:\.\./)*[A-Za-z_][\w/\-.]*\.queries\.[A-Za-z_]\w*$"
);
ref_kind!(
    /// Cross-file chart reference.
    ChartRefPath,
    ChartRef,
    "charts",
    "chart",
    r"^(?:\.\./)*[A-Za-z_][\w/\-.]*\.charts\.[A-Za-z_]\w*$"
);

const QUERY_ID: Keywords = Keywords::pattern(r"^[^#]+#.+$");

/// `<file>#<name>`, as [`QueryId`] prints. The first `#` ends the file; `QueryId::new` refuses a
/// file holding one. A bare name, an inline block and a cross-file ref are the authored sugar the
/// refs pass hoists into this.
impl TryFrom<String> for QueryId {
    type Error = String;
    fn try_from(v: String) -> Result<Self, String> {
        let (file, name) = v
            .split_once('#')
            .ok_or_else(|| format!("query id {} must read '<file>#<name>'", repr(&v)))?;
        let name = match name.starts_with('@') {
            true => QueryName::from_position(name)?,
            false => QueryName::try_from(name)?,
        };
        QueryId::new(
            ProjectPath::try_from(file).map_err(|e| e.to_string())?,
            name,
        )
    }
}

impl model_core::Leaf for QueryId {
    type Whole = QueryId;
    type Authored = Option<QueryId>;
    type Patch = Option<QueryId>;
    const MERGE: model_core::Merge = model_core::Merge::Override;
    const EXPR: model_core::TypeExpr = model_core::TypeExpr::Keyed(
        &QUERY_ID,
        &model_core::TypeExpr::Scalar(model_core::Scalar::Str),
    );
}
model_core::build_leaf!(QueryId);
model_core::from_scalar!(QueryId, String);
