//! What the caller supplies beside the board and its run.
//!
//! The scope's total style comes from `Resolved::base` over the theme (a nested board's from
//! `CompiledBoard.nested`). The engine reads the error tile and the root font stack off it, and
//! the KPI faces and the callout style off the placement's `ChartStyles` (`placed`; `own` is
//! filled from `CompiledBoard.placed_own` beside it and read by the bind's cartesian families).
//! Layout still takes the all-leaves-set patch through `EngineFacts::style`.

use board_model::board::BoardBody;
use diagnostic::Diagnostic;
use layout::board::assemble::StylePatch;

/// The facts the engine does not derive. Each is the one place that changes when the spine's total
/// `Style` takes it over.
pub trait EngineFacts {
    /// The effective style patch `body` lays out with: the theme, its `extends:` and its own `style:`.
    fn style(&self, body: &BoardBody) -> Result<&StylePatch, Diagnostic>;
    /// The effective style patch of tab `tab` of the tabs board `body`: a pane has no body of its own.
    fn pane_style(&self, body: &BoardBody, tab: usize) -> Result<&StylePatch, Diagnostic>;
    /// The docs site URL error codes link to, without a trailing slash.
    fn docs_site(&self) -> &str;
    /// The body of the root's `<style>`: the served `@font-face` block and the page's rules. Which
    /// faces are served from where is the host's fact until the fonts slice.
    fn stylesheet(&self) -> &str;
}
