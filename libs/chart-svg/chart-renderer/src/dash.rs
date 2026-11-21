//! A dashed stroke starts and ends on ink: the sites that stroke a shape of known size stretch the
//! pattern with [`layout::dash::fit`] through these.

use scene::{Attrs, Node, Role};

/// `attrs` with its `stroke-dasharray`, when it has one, stretched to a stroke `length` px long.
pub(crate) fn fit_attrs(attrs: Attrs, length: f64, closed: bool) -> Attrs {
    attrs.map_values(&["stroke-dasharray"], |pattern| ::layout::dash::fit_pattern(pattern, length, closed))
}

/// A line from `from` to `to`, its dash fitted to its length.
pub(crate) fn line(role: Role, from: (f64, f64), to: (f64, f64), attrs: Attrs) -> Node {
    let length = (to.0 - from.0).hypot(to.1 - from.1);
    Node::line(role, from, to, fit_attrs(attrs, length, false))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pattern_stretches_to_a_line_and_a_loop() {
        let open = ::layout::dash::fit_pattern("6,3", 100.0, false);
        let closed = ::layout::dash::fit_pattern("6,3", 100.0, true);
        assert_ne!(open, closed);
        assert_eq!(::layout::dash::fit_pattern("0,5", 100.0, false), "0,5");
        assert_eq!(::layout::dash::fit_pattern("6,3", 0.0, false), "6,3");
    }
}
