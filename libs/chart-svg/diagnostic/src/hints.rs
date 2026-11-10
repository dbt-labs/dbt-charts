//! The hints Python builds with a code's `hint_generator`, for the codes a card can reach:
//! `Did you mean 'x'?` from `difflib.get_close_matches(value, candidates, n=1, cutoff=0.5)`.
//!
//! [`close_match`] is difflib's `SequenceMatcher` as CPython has it (`find_longest_match` with
//! its junk-free extension loops, the autojunk popularity cut for a second sequence of 200 or
//! more characters, `get_matching_blocks`, `ratio`), because which near miss wins, and which of
//! two equal scores, is part of the text. Registered generators not named in [`PORTED`] yield no
//! hint; `tests/fixtures/hints.json` lists them all.

use crate::py_str_repr;
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};

/// The codes whose generator is ported.
pub const PORTED: [&str; 5] = [
    "ERR-DBT-REF-UNKNOWN-NODE",
    "ERR-DBT-MODEL-COLUMN-MISSING",
    "ERR-DBT-SOURCE-UNKNOWN-TABLE",
    "ERR-QUERY-FORMAT-UNKNOWN",
    "ERR-UNKNOWN-QUERY",
];

/// Format names that were renamed: a successor in the pool beats a fuzzy match.
const RETIRED_FORMAT_SUCCESSORS: [(&str, &str); 1] = [("number_default", "number")];

/// Python's `str.strip()` whitespace: Unicode white space plus the four ASCII separators.
fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

struct Matcher<'a> {
    a: &'a [char],
    b: &'a [char],
    b2j: HashMap<char, Vec<usize>>,
}

impl<'a> Matcher<'a> {
    /// `set_seq2(b)`: index `b`, dropping the characters autojunk finds popular.
    fn new(a: &'a [char], b: &'a [char]) -> Self {
        let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
        for (j, c) in b.iter().enumerate() {
            b2j.entry(*c).or_default().push(j);
        }
        let n = b.len();
        if n >= 200 {
            let ntest = n / 100 + 1;
            b2j.retain(|_, idx| idx.len() <= ntest);
        }
        Self { a, b, b2j }
    }

    fn at_a(&self, i: usize) -> Option<char> {
        self.a.get(i).copied()
    }

    fn at_b(&self, j: usize) -> Option<char> {
        self.b.get(j).copied()
    }

    /// The longest matching block of `a[alo..ahi]` and `b[blo..bhi]`, earliest in `a`, then in `b`.
    fn find_longest_match(
        &self,
        alo: usize,
        ahi: usize,
        blo: usize,
        bhi: usize,
    ) -> (usize, usize, usize) {
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0);
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        for i in alo..ahi {
            let mut newj2len: HashMap<usize, usize> = HashMap::new();
            if let Some(indexes) = self.at_a(i).and_then(|c| self.b2j.get(&c)) {
                for &j in indexes {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let k = if j == 0 {
                        0
                    } else {
                        j2len.get(&(j - 1)).copied().unwrap_or(0)
                    } + 1;
                    newj2len.insert(j, k);
                    if k > bestsize {
                        besti = i + 1 - k;
                        bestj = j + 1 - k;
                        bestsize = k;
                    }
                }
            }
            j2len = newj2len;
        }
        // Extend with the non-junk elements that autojunk left out of `b2j`.
        while besti > alo
            && bestj > blo
            && self.at_a(besti - 1).is_some()
            && self.at_a(besti - 1) == self.at_b(bestj - 1)
        {
            besti -= 1;
            bestj -= 1;
            bestsize += 1;
        }
        while besti + bestsize < ahi
            && bestj + bestsize < bhi
            && self.at_a(besti + bestsize).is_some()
            && self.at_a(besti + bestsize) == self.at_b(bestj + bestsize)
        {
            bestsize += 1;
        }
        (besti, bestj, bestsize)
    }

    /// The sum of the sizes of `get_matching_blocks()`.
    fn matches(&self) -> usize {
        let mut queue = vec![(0, self.a.len(), 0, self.b.len())];
        let mut blocks: Vec<(usize, usize, usize)> = Vec::new();
        while let Some((alo, ahi, blo, bhi)) = queue.pop() {
            let (i, j, k) = self.find_longest_match(alo, ahi, blo, bhi);
            if k > 0 {
                blocks.push((i, j, k));
                if alo < i && blo < j {
                    queue.push((alo, i, blo, j));
                }
                if i + k < ahi && j + k < bhi {
                    queue.push((i + k, ahi, j + k, bhi));
                }
            }
        }
        blocks.iter().map(|b| b.2).sum()
    }

    fn ratio(&self) -> f64 {
        let length = self.a.len() + self.b.len();
        if length == 0 {
            1.0
        } else {
            2.0 * self.matches() as f64 / length as f64
        }
    }
}

/// `difflib.get_close_matches(word, candidates, n=1, cutoff=0.5)`: the best-scoring candidate,
/// the greatest string among equal scores.
fn get_close_match<'a>(word: &str, candidates: &[&'a str]) -> Option<&'a str> {
    let b: Vec<char> = word.chars().collect();
    let mut best: Option<(f64, &'a str)> = None;
    for &x in candidates {
        let a: Vec<char> = x.chars().collect();
        let score = Matcher::new(&a, &b).ratio();
        if score >= 0.5 && best.is_none_or(|(s, bx)| score > s || (score == s && x > bx)) {
            best = Some((score, x));
        }
    }
    best.map(|(_, x)| x)
}

/// `_suggest_close_match`: the candidates are stripped and blanks dropped.
fn close_match(value: &str, available: &[String]) -> Option<String> {
    let candidates: Vec<&str> = available
        .iter()
        .map(|c| c.trim_matches(is_space))
        .filter(|c| !c.is_empty())
        .collect();
    get_close_match(value, &candidates).map(|m| format!("Did you mean {}?", py_str_repr(m)))
}

fn text<'a>(params: &'a BTreeMap<&'static str, Value>, name: &str) -> Option<&'a str> {
    params.get(name)?.as_str()
}

fn pool(params: &BTreeMap<&'static str, Value>) -> Option<Vec<String>> {
    params
        .get("available")?
        .as_array()?
        .iter()
        .map(|v| v.as_str().map(str::to_string))
        .collect()
}

/// The hint `code`'s generator makes of `params`, or `None` for no hint (also: a code whose
/// generator is not ported, or params that are not the strings the generator reads).
pub fn generate(code: &str, params: &BTreeMap<&'static str, Value>) -> Option<String> {
    let available = pool(params)?;
    match code {
        "ERR-DBT-REF-UNKNOWN-NODE" => close_match(text(params, "ref_name")?, &available),
        "ERR-UNKNOWN-QUERY" => close_match(text(params, "query_name")?, &available),
        "ERR-DBT-MODEL-COLUMN-MISSING" => close_match(text(params, "column_name")?, &available),
        "ERR-DBT-SOURCE-UNKNOWN-TABLE" => {
            let name = format!(
                "{}.{}",
                text(params, "source_name")?,
                text(params, "table_name")?
            );
            close_match(&name, &available)
        }
        "ERR-QUERY-FORMAT-UNKNOWN" => {
            let spec = text(params, "spec")?;
            let successor = RETIRED_FORMAT_SUCCESSORS
                .iter()
                .find(|(from, _)| *from == spec)
                .map(|(_, to)| *to)
                .filter(|to| available.iter().any(|a| a == to));
            match successor {
                Some(to) => Some(format!(
                    "{} was renamed to {}.",
                    py_str_repr(spec),
                    py_str_repr(to)
                )),
                None => close_match(spec, &available),
            }
        }
        _ => None,
    }
}
