//! The family contract (spec § 6.1, D-26): a family plans, then draws. `plan` decides every
//! size-dependent choice at a width and draws nothing; `draw` builds the nodes of that plan in a slot
//! `height` tall and decides nothing. A card family is its card (`KpiCard`, `CalloutCard`, ...),
//! and the engine places any of them with `render`.

use diagnostic::Diagnostic;
use measure::shaped::FontBook;
use scene::Node;

/// What a family's `plan` reads besides its card.
pub struct Env<'a> {
    /// The served faces, which a card measures its texts in.
    pub book: &'a FontBook,
}

pub trait Family {
    type Plan;

    /// The card at `width`.
    fn plan(&self, env: &Env, width: f64) -> Result<Self::Plan, Diagnostic>;

    /// The card's nodes, drawn in a slot `height` tall.
    fn draw(&self, plan: Self::Plan, height: f64) -> Result<Vec<Node>, Diagnostic>;

    /// The card planned at `width`, then drawn in `height`: the one call a host makes.
    fn render(&self, env: &Env, width: f64, height: f64) -> Result<Vec<Node>, Diagnostic> {
        let plan = self.plan(env, width)?;
        self.draw(plan, height)
    }
}
