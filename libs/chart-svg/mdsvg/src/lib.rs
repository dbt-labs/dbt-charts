//! Rust transliteration of `libs/markdown-svg/src/mdsvg` (the Python
//! `mdsvg` package): a Markdown parser and precise-text-measurement SVG
//! renderer. One Rust module per Python file, ladder-first parity with the
//! Python library as the byte oracle — see `docs/laws-board.md`'s
//! `## mdsvg crate` section for the module/function mapping table.

pub mod css;
pub mod east_asian_width;
pub mod fonts;
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
pub use renderer::{measure, render, RenderResult, SVGRenderer, Size};
pub use style::Style;
pub use types::Document;
