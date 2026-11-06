//! The data half of a legend: how an authored `legend.values` resolves against a color domain and where
//! `symbolLimit` folds the entries. Symbols, grids and drawing are chart-renderer's `families::legend`.

/// Case/separator-fold a legend token for alias matching: collapse `-`, `_`
/// and runs of whitespace to a single space, then lowercase, so
/// `net_revenue`, `Net-Revenue` and `net revenue` all fold to the same key.
///
/// ASCII-equivalent to `emitters/_channels.py`'s `_fold_legend_token`, not
/// identical: that folds with `casefold()`, this with `to_lowercase()`, so
/// they disagree where casefold expands a character: `straße` folds to
/// `strasse` in Python and stays `straße` here.
pub fn fold_legend_token(token: &str) -> String {
    let mut out = String::with_capacity(token.len());
    let mut last_was_sep = false;
    for c in token.chars() {
        if c == '-' || c == '_' || c.is_whitespace() {
            if !last_was_sep {
                out.push(' ');
            }
            last_was_sep = true;
        } else {
            out.push(c);
            last_was_sep = false;
        }
    }
    out.trim().to_lowercase()
}

/// Production's `apply_legend_entry_order` for an authored `legend.values`
/// (`emitters/_channels.py`: `resolve_legend_entries`, then the dedupe and the
/// nothing-matched fallback): indices into `domain` in authored order. A
/// token takes the domain entry it spells exactly, else the one entry whose
/// alias it spells exactly, else the one entry it folds to
/// (`fold_legend_token`); a token matching nothing, or more than one entry at
/// its tier, is dropped, never guessed. When no token resolves the whole
/// domain keeps its own order. `authored` is never empty here (an explicit
/// `values: []` draws a title-only legend, which callers handle before this).
///
/// `aliases`, when given, is one extra spelling per `domain` entry (a wide
/// fold's raw measure name for its humanized label, `wide_legend_aliases`);
/// an alias spelling another entry's own name never resolves to this one.
pub fn resolve_legend_values(
    authored: &[String],
    domain: &[&str],
    aliases: Option<&[&str]>,
) -> Vec<usize> {
    let alias_of = |idx: usize| aliases.map(|spellings| spellings[idx]);
    let resolve = |token: &str| -> Option<usize> {
        if let Some(idx) = domain.iter().position(|entry| *entry == token) {
            return Some(idx);
        }
        let mut aliased = (0..domain.len()).filter(|&idx| alias_of(idx) == Some(token));
        if let (Some(idx), None) = (aliased.next(), aliased.next()) {
            return Some(idx);
        }
        let folded = fold_legend_token(token);
        let mut matches = (0..domain.len()).filter(|&idx| {
            fold_legend_token(domain[idx]) == folded
                || alias_of(idx).is_some_and(|alias| {
                    !domain.contains(&alias) && fold_legend_token(alias) == folded
                })
        });
        match (matches.next(), matches.next()) {
            (Some(idx), None) => Some(idx),
            _ => None,
        }
    };
    let mut resolved: Vec<usize> = Vec::new();
    for token in authored {
        if let Some(idx) = resolve(token).filter(|idx| !resolved.contains(idx)) {
            resolved.push(idx);
        }
    }
    if resolved.is_empty() {
        (0..domain.len()).collect()
    } else {
        resolved
    }
}

/// `symbolLimit`: vega-encode's `LegendEntries` keeps the first `limit - 1` of `count` entries and
/// folds the rest into one "+N more" note, which draws no swatch of its own. `None` where
/// the entries fit.
pub fn fold_symbol_limit(count: usize, limit: usize) -> Option<(usize, String)> {
    (limit > 0 && count > limit).then(|| (limit - 1, format!("+{} more", count - (limit - 1))))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `resolve_legend_entries`' two tiers: the exact spelling, else the one
    /// folded match; an unmatched or ambiguous token drops; nothing resolving
    /// leaves the domain's own order.
    #[test]
    fn authored_values_resolve_exact_then_folded_and_drop_what_does_not_match() {
        let domain = ["Net Revenue", "Net-Revenue 2", "Tools"];
        let authored = |tokens: &[&str]| tokens.iter().map(|t| t.to_string()).collect::<Vec<_>>();
        let resolve = |tokens: &[&str]| resolve_legend_values(&authored(tokens), &domain, None);
        assert_eq!(resolve(&["Tools", "net_revenue"]), [2, 0]);
        assert_eq!(resolve(&["tools", "Tools", "nothing"]), [2]);
        // "net revenue 2" folds to exactly one entry; "net-revenue" to the
        // first only, so each resolves.
        assert_eq!(resolve_legend_values(&authored(&["net revenue 2"]), &domain, None), [1]);
        // Nothing resolves: the whole domain, in its own order.
        assert_eq!(resolve_legend_values(&authored(&["x", "y"]), &domain, None), [0, 1, 2]);
        // Two entries folding to one key make the token ambiguous: dropped.
        assert_eq!(resolve_legend_values(&authored(&["a b"]), &["A-B", "a_b"], None), [0, 1]);
    }

    /// A wide fold's raw measure name resolves to its humanized entry
    /// (`wide_legend_aliases`); an alias two entries share, or one that spells
    /// another entry's own name, resolves to neither.
    #[test]
    fn authored_values_resolve_an_alias_after_the_exact_spelling() {
        let authored = |tokens: &[&str]| tokens.iter().map(|t| t.to_string()).collect::<Vec<_>>();
        let domain = ["growth %", "total cost"];
        let aliases: &[&str] = &["growth_pct", "total_cost"];
        let resolve =
            |tokens: &[&str]| resolve_legend_values(&authored(tokens), &domain, Some(aliases));
        assert_eq!(resolve(&["growth_pct", "total cost"]), [0, 1]);
        assert_eq!(resolve(&["total_cost", "growth %"]), [1, 0]);
        assert_eq!(resolve(&["Total-Cost"]), [1]);
        assert_eq!(resolve(&["growth pct"]), [0]);
        let shared: &[&str] = &["x", "x"];
        assert_eq!(resolve_legend_values(&authored(&["x"]), &domain, Some(shared)), [0, 1]);
        let stolen: &[&str] = &["total cost", "total_cost"];
        assert_eq!(resolve_legend_values(&authored(&["total cost"]), &domain, Some(stolen)), [1]);
        assert_eq!(resolve_legend_values(&authored(&["total-cost"]), &domain, Some(stolen)), [1]);
    }
}
