//! Parity code: laws kept only because production's output carries them, each one a function with
//! its deletion phase beside it. Nothing here is a design; the cutover deletes the module.

use md5::{Digest, Md5};
use std::fmt::Write;

/// A number as production's `callout_hash` writes it into the stripe id: the shortest round-trip
/// text, `-0` as `0`. Deleted when the error card's pattern id stops mirroring production.
pub(crate) fn hashed_number(v: f64) -> String {
    if v == 0.0 {
        "0".to_string()
    } else {
        v.to_string()
    }
}

/// production's `callout_hash` (callout.rs:541): the first 8 hex digits of an md5 over `parts` joined
/// by NUL. Deleted with the error card's production-mirrored pattern id.
pub(crate) fn callout_hash(parts: &[&str]) -> String {
    let digest = Md5::digest(parts.join("\x00").as_bytes());
    let mut hex = String::with_capacity(8);
    for byte in digest.iter().take(4) {
        let _ = write!(hex, "{byte:02x}");
    }
    hex
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_hashed_number_is_the_shortest_round_trip_with_negative_zero_as_zero() {
        let texts: Vec<_> = [0.0, -0.0, 12.0, 1.5, 0.1 + 0.2].map(hashed_number).into();
        assert_eq!(texts, ["0", "0", "12", "1.5", "0.30000000000000004"]);
    }

    #[test]
    fn the_callout_hash_is_eight_hex_digits_of_an_md5_over_nul_joined_parts() {
        // The digest itself is pinned by the recorded error card (`tests/it/failed_card.rs`).
        let joined = callout_hash(&["a", "b"]);
        assert_eq!(joined.len(), 8);
        assert!(joined.bytes().all(|b| b.is_ascii_hexdigit()));
        assert_ne!(joined, callout_hash(&["ab"]));
    }
}
