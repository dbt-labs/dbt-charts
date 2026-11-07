//! Rule enums for `Engine<T, R>` fields (spec § 5). A rule's spelling is a
//! contract: renaming one needs a migration.
//!
//! Every Python `AutoUnset` site, and every `Literal["auto"]` arm beside a
//! value, authors one rule: `auto`.

use crate::string_enum;

string_enum!(
    /// `auto`: the engine decides.
    Auto { Auto = "auto" }
);
