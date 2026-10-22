//! Letter-case transform: the one implementation, read by the renderer, the
//! compile stages and (through `_engine.apply_case`) Python, for both a
//! chart title's `FontStyle.case` and a board variable's label
//! `FontStyle.case`. All seven values ("none", "upper", "lower", "sentence",
//! "title", "slug", "camel") live here; what `title` means is the board
//! language's `case.title_style` (see [`apply_case_in`]).
//!
//! The `en_small_words` style is the `titlecase` PyPI library's (v2.4.1)
//! algorithm plus the project's small-word extension, `dbt` brand-token
//! lowering and standalone-acronym guard. A path-like word
//! (2+ slashes, or a slash with `-`, `_` or `.`: repo slugs, paths, URLs)
//! keeps its authored case; prose pairs (`pie/donut` -> `Pie/Donut`) title-case
//! each side. Inline periods (`rev.usd`), apostrophe-second words
//! (`o'brien` -> `O'Brien`), Mc-prefixed surnames, `Mr`/`Mrs`/`Ms`/`Dr`
//! prefixes and ALL-CAPS initials (`U.S`) are handled because a board
//! variable's inferred label runs slug text through this same `title` branch.

use std::collections::{BTreeMap, HashSet};
use std::sync::LazyLock;

use regex::{Captures, Regex};

use crate::titlecase_first_char::titlecase_first_char;

/// Python's `\w` for a `str` pattern (`isalnum()` or `_`). The `regex`
/// crate's own `\w` also counts combining marks, so each `\b` and `\w` of
/// the library's patterns is spelled against this class. `\b` after a small
/// word is `(?:$|[^word])`: for `v.`/`vs.` the optional `.` backtracks to the
/// bare word, which capitalizes the same text.
const WORD_CHARS: &str = r"\p{L}\p{N}_";

fn compile(pattern: &str) -> Regex {
    Regex::new(pattern).expect("titlecase pattern is a valid regex")
}

fn punct_class() -> String {
    format!("[{}]", regex::escape(PUNCT))
}

/// The small-word patterns of one language's `en_small_words` title style,
/// built from its catalog lists: `case.small_words` (the library's NYT list,
/// where `v` and `vs` are abbreviations that may carry a period) lowercased
/// mid-title, and `case.extra_small_words` (the editorial extension) lowercased
/// mid-title only, never as the first or last word.
struct Small {
    /// `SMALL_WORDS = ^(SMALL)$`, `regex.I` (`titlecase/__init__.py:31`). The
    /// `I` flag is scoped to the letters of `SMALL` in every pattern: Python
    /// applies it to `\w`/`\b` too, but a case-insensitive `[\p{L}\p{N}_]` in
    /// the `regex` crate also admits U+0345, which Python's word class does not.
    words: Regex,
    /// `SMALL_FIRST = ^([PUNCT]*)(SMALL)\b`, `regex.I` (`:33`; applied at `:181`).
    first: Regex,
    /// `SMALL_LAST = \b(SMALL)[PUNCT]?$`, `regex.I` (`:34`; applied at `:187`).
    last: Regex,
    /// `SUBPHRASE = ([:.;?!\-–‒—―][ ])(SMALL)`, case-SENSITIVE and with no `\b`
    /// (`:35`; applied at `:193`): the alternation's first matching prefix
    /// wins, so `: about` matches `a`, not a whole token.
    subphrase: Regex,
    extra: HashSet<String>,
}

impl Small {
    fn build(lang: &str) -> Result<Self, String> {
        let alternation = crate::language::text(lang, "case.small_words")?
            .split(',')
            .map(|word| match word {
                "v" | "vs" => format!(r"{word}\.?"),
                other => regex::escape(other),
            })
            .collect::<Vec<_>>()
            .join("|");
        let extra = crate::language::text(lang, "case.extra_small_words")?
            .split(',')
            .map(str::to_string)
            .collect();
        if alternation.is_empty() {
            return Err(format!(
                "language '{lang}': title style en_small_words needs case.small_words"
            ));
        }
        let small = alternation.as_str();
        Ok(Self {
            words: compile(&format!("^(?i:{small})$")),
            first: compile(&format!("^{}*((?i:{small}))(?:$|[^{WORD_CHARS}])", punct_class())),
            last: compile(&format!("(?:^|[^{WORD_CHARS}])((?i:{small})){}?$", punct_class())),
            subphrase: compile(&format!(r"([:.;?!\-–‒—―][ ])({small})")),
            extra,
        })
    }

    /// The patterns of `lang`, built once; a language whose title style is not
    /// `en_small_words` has none.
    fn of(lang: &str) -> Result<&'static Small, String> {
        static SMALLS: LazyLock<BTreeMap<&'static str, Small>> = LazyLock::new(|| {
            crate::language::LANGUAGES
                .iter()
                .copied()
                .filter(|lang| crate::language::text(lang, "case.title_style") == Ok("en_small_words"))
                .map(|lang| (lang, Small::build(lang).unwrap_or_else(|e| panic!("{e}"))))
                .collect()
        });
        SMALLS.get(lang).ok_or_else(|| format!("language '{lang}': title style en_small_words needs case.small_words"))
    }
}

/// `[\r\n]+` (`titlecase/__init__.py:99`): the library cases each line on its
/// own and rejoins them with `\n` (`:200`).
static LINE_BREAKS: LazyLock<Regex> = LazyLock::new(|| compile(r"[\r\n]+"));

/// `INLINE_PERIOD = [\w][.][\w]` (`titlecase/__init__.py:46`; searched at
/// `:139`): the stdlib fallback counts digits and `_` as word characters, so
/// `v1.2` and `a_b.c_d` are dotted tokens kept verbatim.
static INLINE_PERIOD: LazyLock<Regex> =
    LazyLock::new(|| compile(&format!("[{WORD_CHARS}][.][{WORD_CHARS}]")));

/// `UC_ELSEWHERE = [PUNCT]*?[a-zA-Z]+[A-Z]+?` under `.match()`
/// (`titlecase/__init__.py:47`; used at `:139`). The installed library has no
/// `regex` package, so this ASCII fallback, not the `\p{L}`/`\p{Lu}` pattern
/// at `:41`, is the law: after leading punctuation the first letter run must
/// hold an uppercase letter past its first character.
static UC_ELSEWHERE: LazyLock<Regex> =
    LazyLock::new(|| compile(&format!("^{}*?[a-zA-Z]+[A-Z]+?", punct_class())));

/// `UC_INITIALS = ^(?:[A-Z]\.|[A-Z]\.[A-Z])+$` (`titlecase/__init__.py:50`;
/// matched at `:115`, ALL-CAPS lines only): a whole word of one-or-two-capital
/// initials, each followed by a period (`V.`, `N.Y.`, `U.S`). ASCII capitals
/// only, like the other fallback patterns.
static UC_INITIALS: LazyLock<Regex> =
    LazyLock::new(|| compile(r"^(?:[A-Z]\.|[A-Z]\.[A-Z])+$"));

/// `APOS_SECOND = ^[dol]['‘][\w]+(?:['s]{2})?$`, `regex.I`
/// (`titlecase/__init__.py:49`; used at `:119`). `[\w]+` backtracks to leave
/// the two characters of the optional tail.
static APOS_SECOND: LazyLock<Regex> = LazyLock::new(|| {
    compile(&format!(
        "^(?i:[dol])['‘][{WORD_CHARS}]+(?:(?i:['s]){{2}})?$"
    ))
});

/// Tokens forced lowercase ("dbt" is a stylized-lowercase brand name).
fn force_lowercase(word_lower: &str) -> bool {
    word_lower == "dbt"
}

/// A slug or path (`dbt-labs/dbt-charts`, `src/core/case.py`, a URL), as
/// opposed to a prose pair like `pie/donut` or `x/y-axis`. Trailing sentence
/// punctuation is ignored; one slash needs `_`, an inner `.`, or `-` in every
/// segment. Keeps its authored case.
fn is_path_like(word: &str) -> bool {
    let token = word.trim_end_matches(['.', ',', ';', ':', '!', '?']);
    let segments: Vec<&str> = token.split('/').collect();
    match segments.len() {
        0 | 1 => false,
        2 => {
            segments.iter().any(|seg| {
                seg.contains('_')
                    || seg
                        .char_indices()
                        .any(|(i, c)| c == '.' && i + 1 < seg.len())
            }) || segments.iter().all(|seg| seg.contains('-'))
        }
        _ => true,
    }
}

/// Callback for the acronym/brand overrides (`_acronym_callback`). Returns
/// the immutable replacement, or `None` to defer to the standard per-word
/// rules.
fn acronym_callback(word: &str) -> Option<String> {
    if is_path_like(word) {
        return Some(word.to_string());
    }
    let lower = word.to_lowercase();
    if force_lowercase(&lower) {
        return Some(lower);
    }
    let is_alpha = !word.is_empty() && word.chars().all(char::is_alphabetic);
    let has_upper = word.chars().any(char::is_uppercase);
    let no_lowercase = word.chars().all(|c| !c.is_lowercase());
    if word.chars().count() > 1 && is_alpha && no_lowercase && has_upper {
        return Some(word.to_string());
    }
    None
}

/// titlecase's `PUNCT` class — the only characters `CAPFIRST` steps over
/// on its way to a token's first letter.
const PUNCT: &str =
    "!\"\u{201C}#$%&'\u{2018}()*+,-\u{2013}\u{2012}\u{2014}\u{2015}./:;?@[\\]_`{|}~";

/// titlecase's `CAPFIRST` as production runs it (`^[PUNCT]*?([\w])` — the
/// `regex` module's `\p{Letter}` branch is not installed): uppercase the
/// first character past the leading punctuation, which changes nothing
/// unless it is a letter — a digit-led token (`$150k`, `3rd`) stays as
/// authored, and `_`, a word character before it is punctuation, stops the
/// search itself.
fn capfirst(word: &str) -> String {
    let mut out = String::with_capacity(word.len());
    let mut chars = word.chars();
    for c in chars.by_ref() {
        if c != '_' && PUNCT.contains(c) {
            out.push(c);
        } else {
            if c.is_alphabetic() {
                out.extend(c.to_uppercase());
            } else {
                out.push(c);
            }
            break;
        }
    }
    out.extend(chars);
    out
}

/// `word.capitalize()` semantics: first char TITLECASED, rest lower.
/// `char::to_uppercase()` is the Unicode UPPERCASE mapping, which
/// disagrees with Python's own titlecase mapping for a small, generated
/// exception set (`titlecase_first_char.rs` — `ß` -> `"Ss"` not `"SS"`,
/// a few ligatures, Georgian Mkhedruli left unchanged). Reached from `camel`
/// case and every small-word capitalization.
fn capitalize(word: &str) -> String {
    let mut chars = word.chars();
    match chars.next() {
        None => String::new(),
        Some(first) => {
            let head = match titlecase_first_char(first as u32) {
                Some(mapped) => mapped.to_string(),
                None => first.to_uppercase().collect::<String>(),
            };
            head + &chars.as_str().to_lowercase()
        }
    }
}

fn is_word_char(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// `capitalize` of `s[start..end]`, the rest of `s` verbatim.
fn splice_capitalized(s: &str, start: usize, end: usize) -> String {
    format!("{}{}{}", &s[..start], capitalize(&s[start..end]), &s[end..])
}

/// `SMALL_FIRST.sub(lambda m: group(1) + group(2).capitalize(), s)`
/// (`titlecase/__init__.py:181`): a BOUNDARY match, not a whole-token one, so
/// a slash/hyphen-joined token like "a/b" capitalizes only its leading "a".
fn capitalize_leading_small_word(small: &Small, s: &str) -> String {
    match small.first.captures(s) {
        Some(caps) => {
            let word = caps.get(1).expect("group 1 always participates");
            splice_capitalized(s, word.start(), word.end())
        }
        None => s.to_string(),
    }
}

/// `SMALL_LAST.sub(lambda m: m.group(0).capitalize(), s)`
/// (`titlecase/__init__.py:187`): the match is the small word plus its one
/// optional trailing PUNCT character, so the capitalized span includes it.
fn capitalize_trailing_small_word(small: &Small, s: &str) -> String {
    match small.last.captures(s) {
        Some(caps) => {
            let word = caps.get(1).expect("group 1 always participates");
            let whole = caps.get(0).expect("group 0 always participates");
            splice_capitalized(s, word.start(), whole.end())
        }
        None => s.to_string(),
    }
}

/// `SUBPHRASE.sub(lambda m: group(1) + group(2).capitalize(), result)`
/// (`titlecase/__init__.py:193`), anywhere in the line.
fn apply_subphrase_fix(small: &Small, text: &str) -> String {
    small
        .subphrase
        .replace_all(text, |caps: &Captures| {
            format!("{}{}", &caps[1], capitalize(&caps[2]))
        })
        .into_owned()
}

/// `titlecase`'s `APOS_SECOND` branch: lowercase a leading consonant
/// (d/l) or uppercase a leading vowel (o), keep the apostrophe, uppercase
/// the letter right after it, leave the remainder untouched.
fn apply_apos_second(word: &str) -> String {
    let chars: Vec<char> = word.chars().collect();
    let first = if chars[0].eq_ignore_ascii_case(&'o') {
        chars[0].to_ascii_uppercase()
    } else {
        chars[0].to_ascii_lowercase()
    };
    let third: String = chars[2].to_uppercase().collect();
    let rest: String = chars[3..].iter().collect();
    format!("{first}{}{third}{rest}", chars[1])
}

/// titlecase's `MAC_MC` (`^([Mm]c|MC)(\w.+)`): a Mc/mc/MC-prefixed
/// surname — capitalize the prefix (`match.group(1).capitalize()` is
/// always `"Mc"` regardless of the matched casing), title-case the
/// remainder as its own word (recursing with `small_first_last = true`,
/// matching `titlecase(match.group(2), callback, True)`).
fn mac_mc_rest(word: &str) -> Option<&str> {
    let rest = word
        .strip_prefix("Mc")
        .or_else(|| word.strip_prefix("mc"))
        .or_else(|| word.strip_prefix("MC"))?;
    let mut chars = rest.chars();
    let first = chars.next()?;
    (is_word_char(first) && chars.next().is_some()).then_some(rest)
}

/// titlecase's `MR_MRS_MS_DR` (`^((m((rs?)|s))|Dr)$`, case-insensitive):
/// `mr`/`mrs`/`ms` in any case, or `dr` in any case (the `Dr` alternative
/// is itself case-insensitive under the compiled flag, not literal-`Dr`
/// only). Checked as a whole-word match, same as the Python regex's `^…$`
/// anchors.
fn is_mr_mrs_ms_dr(word: &str) -> bool {
    matches!(word.to_lowercase().as_str(), "mr" | "mrs" | "ms" | "dr")
}

/// titlecase's own `MR_MRS_MS_DR` substitution: `word[0].upper() +
/// word[1:]` — capitalize the first character only, the rest verbatim
/// (so an already-uppercase `"MRS"` stays `"MRS"`, not `"Mrs"`).
fn apply_mr_mrs_ms_dr(word: &str) -> String {
    let mut chars = word.chars();
    match chars.next() {
        None => String::new(),
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
    }
}

/// Title-cases one line (or one hyphen-split sub-word, recursively).
/// `small_first_last` mirrors the Python parameter: true for the top-level
/// call, false when recursing into a hyphenated word's parts.
fn process_line(small: &Small, line: &str, small_first_last: bool) -> String {
    let all_caps = line.to_uppercase() == line;
    let words: Vec<&str> = line.split([' ', '\t']).collect();

    // (word, is_immutable) — immutable words bypass first/last capitalization.
    let mut tc: Vec<(String, bool)> = Vec::with_capacity(words.len());

    for word in words {
        if word.is_empty() {
            tc.push((String::new(), false));
            continue;
        }
        if let Some(replaced) = acronym_callback(word) {
            tc.push((replaced, true));
            continue;
        }
        // titlecase's own check order: the ALL-CAPS UC_INITIALS exemption
        // first (an ALL-CAPS line only), then APOS_SECOND, then MAC_MC,
        // then MR_MRS_MS_DR, then INLINE_PERIOD/UC_ELSEWHERE.
        if all_caps && UC_INITIALS.is_match(word) {
            tc.push((word.to_string(), false));
            continue;
        }
        if APOS_SECOND.is_match(word) {
            tc.push((apply_apos_second(word), false));
            continue;
        }
        if let Some(rest) = mac_mc_rest(word) {
            tc.push((format!("Mc{}", process_line(small, rest, true)), false));
            continue;
        }
        if is_mr_mrs_ms_dr(word) {
            tc.push((apply_mr_mrs_ms_dr(word), false));
            continue;
        }
        // titlecase's INLINE_PERIOD rule: a word.word sequence marks a
        // filename, domain, version or config-path token, kept verbatim — the library
        // appends it before its all-caps lowering ever runs, so it survives
        // uppercase on an ALL-CAPS line too.
        if INLINE_PERIOD.is_match(word) {
            tc.push((word.to_string(), false));
            continue;
        }
        // titlecase's own check order: INLINE_PERIOD/UC_ELSEWHERE, then
        // SMALL_WORDS, THEN "/", THEN "-" — `UC_ELSEWHERE`/
        // `SMALL_WORDS` must run BEFORE the slash/hyphen split, or an
        // acronym-led slash token like "AWS/pie" splits before
        // UC_ELSEWHERE ever gets a chance to leave it verbatim.
        if !all_caps && UC_ELSEWHERE.is_match(word) {
            tc.push((word.to_string(), false));
            continue;
        }
        if small.words.is_match(word) {
            tc.push((word.to_lowercase(), false));
            continue;
        }
        // titlecase's own `"/" in word and "//" not in word` branch (checked
        // BEFORE the hyphen split): a slash-joined token title-cases each
        // side independently and rejoins with "/".
        // URL-shaped words (`//`) are path-like and caught by the callback earlier.
        if word.contains('/') {
            let joined = word
                .split('/')
                .map(|part| process_line(small, part, false))
                .collect::<Vec<_>>()
                .join("/");
            tc.push((joined, false));
            continue;
        }
        if word.contains('-') {
            let joined = word
                .split('-')
                .map(|part| process_line(small, part, false))
                .collect::<Vec<_>>()
                .join("-");
            tc.push((joined, false));
            continue;
        }
        let mut w = word.to_string();
        if all_caps {
            w = w.to_lowercase();
        }
        tc.push((capfirst(&w), false));
    }

    if small_first_last && !tc.is_empty() {
        let last = tc.len() - 1;
        if !tc[0].1 {
            tc[0].0 = capitalize_leading_small_word(small, &tc[0].0);
        }
        if !tc[last].1 {
            tc[last].0 = capitalize_trailing_small_word(small, &tc[last].0);
        }
    }

    let joined = tc.into_iter().map(|(w, _)| w).collect::<Vec<_>>().join(" ");
    apply_subphrase_fix(small, &joined)
}

/// Post-process pass (`_lowercase_extra_small_words`): lowercase the
/// editorial small-word set when it is NOT the first or last
/// whitespace-separated token, skipping tokens the callback already
/// preserved as a standalone all-caps acronym.
fn lowercase_extra_small_words(small: &Small, text: &str) -> String {
    let mut words: Vec<String> = text.split(' ').map(str::to_string).collect();
    if words.len() <= 2 {
        return text.to_string();
    }
    let last = words.len() - 1;
    for w in &mut words[1..last] {
        let lower = w.to_lowercase();
        let is_preserved_acronym = !w.is_empty()
            && w.chars().all(char::is_alphabetic)
            && w.chars().all(|c| !c.is_lowercase());
        if small.extra.contains(lower.as_str()) && !is_preserved_acronym {
            *w = lower;
        }
    }
    words.join(" ")
}

/// Apply the standalone-all-caps guard, then title-case, then the
/// small-words post-process — mirrors `apply_case(text, "title")` exactly.
fn title_case(small: &Small, text: &str) -> String {
    let is_alpha = !text.is_empty() && text.chars().all(char::is_alphabetic);
    let is_upper =
        is_alpha && text.chars().all(|c| !c.is_lowercase()) && text.chars().any(char::is_uppercase);
    if is_alpha && !text.contains(' ') && is_upper {
        return text.to_string();
    }
    let titled = LINE_BREAKS
        .split(text)
        .map(|line| process_line(small, line, true))
        .collect::<Vec<_>>()
        .join("\n");
    let mut out = lowercase_extra_small_words(small, &titled);
    for unit in crate::CASED_UNIT_SYMBOLS {
        out = out.replace(&unit_inner_cap(unit), unit);
    }
    out
}

/// `(kg)` -> `(Kg)`: the form title case gives a unit symbol.
fn unit_inner_cap(unit: &str) -> String {
    let inner = &unit[1..unit.len() - 1];
    format!("({})", capitalize(inner))
}

/// [`apply_case_in`] in the active board language.
pub fn apply_case(text: &str, case: &str) -> Result<String, String> {
    apply_case_in(crate::language::active_language(), text, case)
}

/// Apply a letter-case transform to text in the word rules of `lang`: its
/// catalog's `case.title_style` decides what `title` means (`en_small_words`
/// is the library algorithm above; `first_word` only capitalizes the first
/// word; `none` leaves the text as authored). Unsupported values return `Err`
/// so a surprise usage fails loudly instead of rendering wrong.
pub fn apply_case_in(lang: &str, text: &str, case: &str) -> Result<String, String> {
    match case {
        "none" => Ok(text.to_string()),
        "upper" => Ok(text.to_uppercase()),
        "lower" => Ok(text.to_lowercase()),
        "sentence" => Ok(sentence_case(text)),
        "title" => match crate::language::text(lang, "case.title_style")? {
            "en_small_words" => Ok(title_case(Small::of(lang)?, text)),
            "first_word" => Ok(sentence_case(text)),
            "none" => Ok(text.to_string()),
            other => Err(format!("language '{lang}': unknown case.title_style '{other}'")),
        },
        "slug" => Ok(text.replace([' ', '-'], "_").to_lowercase()),
        "camel" => {
            let normalized = text.replace(['-', '_'], " ");
            let words: Vec<&str> = normalized.split_whitespace().collect();
            Ok(match words.split_first() {
                None => text.to_string(),
                Some((first, rest)) => {
                    first.to_lowercase() + &rest.iter().map(|w| capitalize(w)).collect::<String>()
                }
            })
        }
        other => Err(format!("unknown case: {other:?}")),
    }
}

/// Uppercase the first character only, except a leading `dbt`, which stays
/// lowercase.
pub fn sentence_case(text: &str) -> String {
    let first_word = text.split(' ').next().unwrap_or("");
    if force_lowercase(&first_word.to_lowercase()) {
        return first_word.to_lowercase() + &text[first_word.len()..];
    }
    let mut chars = text.chars();
    match chars.next() {
        None => String::new(),
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
    }
}

/// `case.py::apply_case_to_runs`: case the runs' concatenation as one string,
/// so a word split by emphasis is one word and the first and last words are
/// the string's, then cut it back at the runs' edges. A transform that
/// changes the string's length (`ß` upper-cased) moves those edges; then each
/// run is cased on its own instead.
pub fn apply_case_to_runs(runs: &[&str], case: &str) -> Result<Vec<String>, String> {
    let joined = runs.concat();
    let cased = apply_case(&joined, case)?;
    if cased.chars().count() != joined.chars().count() {
        return runs.iter().map(|run| apply_case(run, case)).collect();
    }
    let mut rest = cased.as_str();
    Ok(runs
        .iter()
        .map(|run| {
            let end = rest
                .char_indices()
                .nth(run.chars().count())
                .map_or(rest.len(), |(at, _)| at);
            let (piece, tail) = rest.split_at(end);
            rest = tail;
            piece.to_string()
        })
        .collect())
}

#[cfg(test)]
mod inline_period_tests {
    use super::*;

    fn apply_case(text: &str, case: &str) -> Result<String, String> {
        apply_case_in("en", text, case)
    }

    // Expected strings from production:
    //   uv run python -c "from dbt_charts.core.text.case import apply_case; ..."
    /// titlecase's CAPFIRST steps over punctuation only: `$150k` keeps its
    /// lowercase k (production renders "Above $150k"), `q4` still
    /// capitalizes, and a parenthesized word capitalizes past the bracket.
    #[test]
    fn digit_led_tokens_keep_their_first_letter_under_title_case() {
        assert_eq!(apply_case("above $150k", "title").unwrap(), "Above $150k");
        assert_eq!(
            apply_case("revenue 10k and 3rd", "title").unwrap(),
            "Revenue 10k and 3rd"
        );
        assert_eq!(
            apply_case("q4 fy25 recap", "title").unwrap(),
            "Q4 Fy25 Recap"
        );
        assert_eq!(
            apply_case("(hello world)", "title").unwrap(),
            "(Hello World)"
        );
        assert_eq!(
            apply_case("_underscore led", "title").unwrap(),
            "_underscore Led"
        );
    }

    #[test]
    fn all_caps_inline_period_lines_stay_verbatim() {
        // Production: apply_case('CONFIG.YAML SETTINGS','title')
        //   -> 'CONFIG.YAML SETTINGS' (verified 2026-08-29). The titlecase
        // library appends an INLINE_PERIOD word before its all-caps lowering
        // ever runs, so the word survives uppercase.
        assert_eq!(
            apply_case("CONFIG.YAML SETTINGS", "title").unwrap(),
            "CONFIG.YAML SETTINGS"
        );
        assert_eq!(
            apply_case("SEE CONFIG.YAML FOR THE SETTINGS", "title").unwrap(),
            "SEE CONFIG.YAML FOR THE SETTINGS"
        );
    }

    #[test]
    fn dotted_config_path_tokens_stay_verbatim_under_title_case() {
        assert_eq!(
            apply_case("style.value.font.color alone", "title").unwrap(),
            "style.value.font.color Alone"
        );
        assert_eq!(
            apply_case("both slots named, no shared style.font.color", "title").unwrap(),
            "Both Slots Named, No Shared style.font.color"
        );
    }

    #[test]
    fn sentence_case_keeps_a_leading_dbt_lowercase() {
        assert_eq!(
            apply_case("dbt models grew 12%", "sentence").unwrap(),
            "dbt models grew 12%"
        );
        assert_eq!(apply_case("DBT", "sentence").unwrap(), "dbt");
        // Only a bare leading token counts: `dbt-charts` is one word.
        assert_eq!(
            apply_case("dbt-charts usage", "sentence").unwrap(),
            "Dbt-charts usage"
        );
        assert_eq!(
            apply_case("models in dbt", "sentence").unwrap(),
            "Models in dbt"
        );
    }

    #[test]
    fn title_case_keeps_slash_words_as_authored() {
        for (input, expected) in [
            (
                "dbt-labs/dbt-charts issues since launch",
                "dbt-labs/dbt-charts Issues Since Launch",
            ),
            (
                "year-over-year growth in docs/Foo",
                "Year-Over-Year Growth in Docs/Foo",
            ),
            (
                "https://example.com/path is up",
                "https://example.com/path Is Up",
            ),
            ("src/core/case.py rules", "src/core/case.py Rules"),
            ("x/y-axis chart", "X/Y-Axis Chart"),
            ("a/b-test results", "A/B-Test Results"),
            ("price/unit-cost analysis", "Price/Unit-Cost Analysis"),
            ("pie/donut.", "Pie/Donut."),
            ("pie/donut", "Pie/Donut"),
            ("dbt-labs/dbt-charts", "dbt-labs/dbt-charts"),
            ("pie/donut table", "Pie/Donut Table"),
            ("a/b testing results", "A/B Testing Results"),
        ] {
            assert_eq!(apply_case(input, "title").unwrap(), expected, "{input:?}");
        }
    }

    #[test]
    fn title_case_leaves_unit_symbols_alone() {
        for (input, expected) in [
            ("weight (lb)", "Weight (lb)"),
            ("total mass (kg) by year", "Total Mass (kg) by Year"),
            ("top speed (mph)", "Top Speed (mph)"),
            ("wait (s)", "Wait (s)"),
            ("p95 latency (ms)", "P95 Latency (ms)"),
        ] {
            assert_eq!(apply_case(input, "title").unwrap(), expected, "{input:?}");
        }
    }

    #[test]
    fn title_case_does_not_guess_acronyms_from_missing_vowels() {
        for (input, expected) in [
            ("weight in lbs", "Weight in Lbs"),
            ("cpu hrs", "Cpu Hrs"),
            ("total kpi count", "Total Kpi Count"),
            ("SpaceX since 2006", "SpaceX Since 2006"),
        ] {
            assert_eq!(apply_case(input, "title").unwrap(), expected, "{input:?}");
        }
    }

    #[test]
    fn dbt_stays_lowercase_but_charts_after_it_capitalizes_like_any_other_word() {
        // Production dropped `_preserve_dbt_charts_brand` (the regex that
        // used to flatten "dbt Charts" back to "dbt charts") when the brand
        // recapitalized "Charts" — verified against production:
        //   apply_case('made with dbt charts', 'title') -> 'Made with dbt Charts'
        //   apply_case('dbt charts dashboards', 'title') -> 'dbt Charts Dashboards'
        // `_FORCE_LOWERCASE` still keeps "dbt" itself lowercase; only the
        // brand-specific override on the following word is gone.
        assert_eq!(
            apply_case("made with dbt charts", "title").unwrap(),
            "Made with dbt Charts"
        );
        assert_eq!(
            apply_case("dbt charts dashboards", "title").unwrap(),
            "dbt Charts Dashboards"
        );
    }

    // Round-2 review: three independent divergences from real
    // `dbt_charts.core.text.case.apply_case` found by an adversarial
    // differential battery (44 mismatches across `mrs`/`dr`, an
    // uppercase-after-punctuation subphrase, and ALL-CAPS initials).
    // Expected strings verified against production:
    //   uv run python -c "from dbt_charts.core.text.case import apply_case; ..."

    #[test]
    fn subphrase_capitalization_is_case_sensitive_like_titlecase_own_regex() {
        // titlecase's SUBPHRASE regex is compiled WITHOUT regex.I (unlike
        // SMALL_FIRST/SMALL_LAST beside it), so it only ever capitalizes an
        // ALREADY-LOWERCASE small word after a subphrase mark — an
        // uppercase one like "AS"/"THE" must stay as authored.
        assert_eq!(
            apply_case("Revenue: AS reported", "title").unwrap(),
            "Revenue: AS Reported"
        );
        assert_eq!(apply_case("Q3 — THE end", "title").unwrap(), "Q3 — THE End");
    }

    #[test]
    fn mr_mrs_ms_dr_capitalize_only_their_first_letter() {
        assert_eq!(apply_case("mrs smith", "title").unwrap(), "Mrs Smith");
        assert_eq!(apply_case("dr watson", "title").unwrap(), "Dr Watson");
    }

    #[test]
    fn all_caps_initials_are_exempt_from_small_wording() {
        assert_eq!(
            apply_case("COST.OF.GOODS OF V. N.Y.", "title").unwrap(),
            "COST.OF.GOODS OF V. N.Y."
        );
        assert_eq!(apply_case("V. VS", "title").unwrap(), "V. VS");
    }

    #[test]
    fn an_all_caps_line_keeps_its_possessives() {
        for line in [
            "O'BRIEN'S VIA VS O'BRIEN ARR/MRR",
            "MCDONALD O'BRIEN'S",
            "REVENUE O'BRIEN'S 52",
        ] {
            assert_eq!(apply_case_in("en", line, "title").unwrap(), line);
        }
    }

    #[test]
    fn an_empty_small_word_list_is_an_error() {
        let err = Small::build("de").err().expect("de lists no small words");
        assert!(err.contains("needs case.small_words"), "{err}");
    }

    #[test]
    fn the_title_style_is_the_languages_own() {
        assert_eq!(apply_case_in("en", "revenue per region", "title").unwrap(), "Revenue per Region");
        assert_eq!(apply_case_in("de", "umsatz pro Region", "title").unwrap(), "Umsatz pro Region");
        assert_eq!(apply_case_in("ja", "売上 by region", "title").unwrap(), "売上 by region");
        assert_eq!(apply_case_in("ja", "abc", "upper").unwrap(), "ABC");
    }
}
