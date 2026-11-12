//! The one door for a port's "cannot happen" claims. A d3 port states each invariant its source
//! relies on (a string `format!` always wrote an `e`, a polygon has points); `held` names it at the
//! call and panics if the claim is ever false. The crate denies `unwrap`, `expect`, `panic` and
//! `unreachable` everywhere else, so a new panic in a port has to come through here, where
//! `rg 'held\(|broken\('` lists every claim. `mdsvg` shares the door instead of keeping a second one.

/// A value an invariant guarantees exists.
pub trait Invariant<T> {
    /// The value, or a panic naming the invariant that did not hold.
    fn held(self, why: &str) -> T;
}

impl<T> Invariant<T> for Option<T> {
    #[allow(clippy::expect_used, reason = "the named door for a port's invariants")]
    #[track_caller]
    fn held(self, why: &str) -> T {
        self.expect(why)
    }
}

impl<T, E: std::fmt::Debug> Invariant<T> for Result<T, E> {
    #[allow(clippy::expect_used, reason = "the named door for a port's invariants")]
    #[track_caller]
    fn held(self, why: &str) -> T {
        self.expect(why)
    }
}

/// A lookup an index or range an invariant bounds: the element or sub-slice, or a panic naming the claim. The one
/// checked replacement for `v[i]` and `&v[a..b]` in a port.
#[track_caller]
pub fn at<T, I: std::slice::SliceIndex<[T]>>(v: &[T], i: I) -> &I::Output {
    v.get(i).held("an index of the port is in range")
}

/// `at` for a write.
#[track_caller]
pub fn at_mut<T, I: std::slice::SliceIndex<[T]>>(v: &mut [T], i: I) -> &mut I::Output {
    v.get_mut(i).held("an index of the port is in range")
}

/// An invariant that cannot hold: the code reached a state its port rules out.
#[allow(clippy::panic, reason = "the named door for a port's invariants")]
#[track_caller]
pub fn broken(why: &str) -> ! {
    panic!("{why}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn at_reads_and_writes_in_range_and_names_a_miss() {
        let mut v = vec![1, 2, 3];
        assert_eq!(*at(&v, 1), 2);
        assert_eq!(at(&v, 1..), &[2, 3]);
        *at_mut(&mut v, 0) = 9;
        assert_eq!(v, [9, 2, 3]);
    }

    #[test]
    #[should_panic(expected = "an index of the port is in range")]
    fn at_panics_out_of_range() {
        at(&[1], 3);
    }

    #[test]
    fn a_held_invariant_hands_back_its_value() {
        assert_eq!(Some(3).held("three"), 3);
        assert_eq!("7".parse::<i32>().held("seven"), 7);
    }

    #[test]
    #[should_panic(expected = "a mantissa has an e")]
    fn a_failed_invariant_names_itself() {
        let _ = "12".split_once('e').held("a mantissa has an e");
    }

    #[test]
    #[should_panic(expected = "the arc is closed")]
    fn a_broken_invariant_names_itself() {
        broken("the arc is closed");
    }
}
