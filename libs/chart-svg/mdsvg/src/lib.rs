//! Rust port of `libs/markdown-svg/src/mdsvg` (the Python `mdsvg` package): a
//! Markdown parser and a layout that emits `scene::Element` nodes, measured
//! through the `measure` crate. `print` is the only serializer; this crate
//! writes no markup and escapes nothing. The parser, CSS and highlight modules
//! are pinned against the Python library; see `docs/laws-board.md`'s
//! `## mdsvg crate` section for the module/function mapping table.

pub mod css;
pub mod highlight;
pub mod images;
pub mod markdown_fence;
pub mod parser;
pub mod pygments_data;
pub mod pygments_lexer;
pub mod pygments_lexers;
pub mod pygments_re;
pub mod pygments_yaml;
pub mod renderer;
pub mod style;
pub mod types;
pub mod unicode_unassigned;
pub mod utils;

pub use parser::parse;
pub use renderer::{indented, indented_nodes, render, RenderResult, SVGRenderer};
pub use style::Style;
pub use types::Document;
