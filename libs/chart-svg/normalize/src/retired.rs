//! Keys a past grammar retired, as parse reports them: `ERR-RETIRED-FIELD` with the version, the
//! successor and what to do, instead of a bare `ERR-EXTRA-FIELD`. The facts are `migrate`'s own
//! declarations (`retired_keys`), read from the document the parse sees, so a key the migration
//! could not finish stays known.

use migrate::RetiredKey;
use model_core::{Retired, Retirement};
use std::cell::OnceCell;
use yaml::Node;

/// The keys `doc` retired, read on the first lookup: a board that parses clean never asks.
pub struct RetiredKeys<'a> {
    doc: &'a Node,
    keys: OnceCell<Vec<RetiredKey>>,
}

impl<'a> RetiredKeys<'a> {
    pub fn of(doc: &'a Node) -> Self {
        Self {
            doc,
            keys: OnceCell::new(),
        }
    }
}

impl Retired for RetiredKeys<'_> {
    fn lookup(&self, parent: &str, key: &str) -> Option<Retirement> {
        let at = |path: &[String]| path.join(".");
        let full = if parent.is_empty() {
            key.to_string()
        } else {
            format!("{parent}.{key}")
        };
        let keys = self.keys.get_or_init(|| migrate::retired_keys(self.doc));
        let found = keys.iter().find(|k| at(&k.path) == full)?;
        Some(story(found, parent))
    }
}

/// `(old, history, action)` as Python words a retired key: the old spelling is what differs from
/// the successor, so a move across levels names the whole stretch.
fn story(k: &RetiredKey, parent: &str) -> Retirement {
    let version = k.retired_in;
    let successor = k.successor.as_ref().map(|s| s.join("."));
    let last = |p: &[String]| p.last().cloned().unwrap_or_default();
    let (old, history, action, parent) = match &k.successor {
        None => {
            let reason = match &k.reason {
                Some(r) => format!(": {r}"),
                None => ".".to_string(),
            };
            (
                last(&k.path),
                format!("was removed in {version}{reason}"),
                "Delete the key.".to_string(),
                parent.to_string(),
            )
        }
        Some(succ) => {
            let shared = k
                .path
                .iter()
                .zip(succ)
                .position(|(a, b)| a != b)
                .unwrap_or(k.path.len().min(succ.len()));
            let (head, old) = k.path.split_at(shared.min(k.path.len()));
            let new = succ.get(shared..).unwrap_or_default();
            let (verb, action) = if old.len() == 1 && new.len() == 1 {
                ("renamed to", "Rename")
            } else {
                ("moved to", "Move")
            };
            let alternative = if succ.ends_with(&["labels".to_string(), "format".to_string()]) {
                ", or set `formats:` on the query to format the column everywhere"
            } else {
                ""
            };
            (
                old.join("."),
                format!("was {verb} '{}' in {version}.", new.join(".")),
                format!("{action} the key{alternative}."),
                head.join("."),
            )
        }
    };
    Retirement {
        successor,
        retired_in: version.to_string(),
        old,
        parent: if parent.is_empty() {
            "root".to_string()
        } else {
            parent
        },
        history,
        action,
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::indexing_slicing)]
mod tests {
    use super::*;

    fn key(path: &[&str], successor: Option<&[&str]>, reason: Option<&str>) -> RetiredKey {
        let own = |p: &[&str]| p.iter().map(ToString::to_string).collect::<Vec<_>>();
        RetiredKey {
            path: own(path),
            retired_in: "0.9.0",
            successor: successor.map(own),
            reason: reason.map(ToString::to_string),
        }
    }

    /// `(old, parent, history, action)` for the key at `parent` + `name`, spelled as Python's
    /// `_retired_key_story` spells it.
    fn told(k: RetiredKey, parent: &str, name: &str) -> (String, String, String, String) {
        let doc = yaml::parse_node("a: 1\n").unwrap();
        let keys = RetiredKeys {
            doc: &doc,
            keys: OnceCell::from(vec![k]),
        };
        let r = keys.lookup(parent, name).unwrap();
        (r.old, r.parent, r.history, r.action)
    }

    fn strings(a: [&str; 4]) -> (String, String, String, String) {
        (a[0].into(), a[1].into(), a[2].into(), a[3].into())
    }

    #[test]
    fn a_rename_names_the_old_key_and_where_it_sits() {
        let k = key(
            &["charts", "c", "grid", "zero"],
            Some(&["charts", "c", "grid", "threshold"]),
            None,
        );
        assert_eq!(
            told(k, "charts.c.grid", "zero"),
            strings([
                "zero",
                "charts.c.grid",
                "was renamed to 'threshold' in 0.9.0.",
                "Rename the key."
            ])
        );
    }

    #[test]
    fn a_move_across_levels_names_the_whole_stretch_and_the_shared_prefix() {
        let k = key(
            &["charts", "c", "size"],
            Some(&["charts", "c", "style", "title", "size"]),
            None,
        );
        assert_eq!(
            told(k, "charts.c", "size"),
            strings([
                "size",
                "charts.c",
                "was moved to 'style.title.size' in 0.9.0.",
                "Move the key."
            ])
        );
    }

    #[test]
    fn a_move_onto_labels_format_offers_the_query_formats() {
        let k = key(
            &["charts", "c", "format"],
            Some(&["charts", "c", "style", "labels", "format"]),
            None,
        );
        assert_eq!(
            told(k, "charts.c", "format").3,
            "Move the key, or set `formats:` on the query to format the column everywhere."
        );
    }

    #[test]
    fn a_removal_says_so_with_or_without_its_reason() {
        assert_eq!(
            told(
                key(&["charts", "c", "old"], None, Some("it never drew")),
                "charts.c",
                "old"
            ),
            strings([
                "old",
                "charts.c",
                "was removed in 0.9.0: it never drew",
                "Delete the key."
            ])
        );
        assert_eq!(
            told(key(&["charts", "c", "old"], None, None), "charts.c", "old").2,
            "was removed in 0.9.0."
        );
    }

    #[test]
    fn a_top_level_key_sits_at_the_root() {
        let k = key(&["heading"], Some(&["title"]), None);
        assert_eq!(
            told(k, "", "heading"),
            strings([
                "heading",
                "root",
                "was renamed to 'title' in 0.9.0.",
                "Rename the key."
            ])
        );
    }
}
