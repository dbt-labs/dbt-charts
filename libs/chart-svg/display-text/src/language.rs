//! Catalog values are strings, or objects of CLDR plural categories. `{name}`
//! placeholders take the call's args; `{n}` also takes the count.

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::sync::OnceLock;

use serde::Deserialize;

macro_rules! catalogs {
    ($($lang:literal),+) => {
        pub const LANGUAGES: [&str; catalogs!(@count $($lang)+)] = [$($lang),+];
        const SOURCES: [&str; catalogs!(@count $($lang)+)] =
            [$(include_str!(concat!("../language/", $lang, ".json"))),+];
    };
    (@count) => { 0 };
    (@count $head:literal $($tail:literal)*) => { 1 + catalogs!(@count $($tail)*) };
}

catalogs!(
    "en", "de", "fr", "es", "it", "pt", "nl", "sv", "da", "nb", "fi", "pl", "cs", "ru", "uk", "tr",
    "ja", "zh", "ko"
);

#[derive(Deserialize)]
#[serde(untagged)]
enum Entry {
    Text(String),
    Plural(BTreeMap<String, String>),
}

type Catalog = BTreeMap<String, Entry>;

fn catalogs() -> &'static BTreeMap<&'static str, Catalog> {
    static CATALOGS: OnceLock<BTreeMap<&'static str, Catalog>> = OnceLock::new();
    CATALOGS.get_or_init(|| {
        LANGUAGES
            .iter()
            .zip(SOURCES)
            .map(|(lang, source)| {
                let catalog = serde_json::from_str(source)
                    .unwrap_or_else(|e| panic!("language/{lang}.json: {e}"));
                (*lang, catalog)
            })
            .collect()
    })
}

fn catalog(lang: &str) -> Result<&'static Catalog, String> {
    catalogs().get(lang).ok_or_else(|| unknown_language(lang))
}

fn unknown_language(lang: &str) -> String {
    format!("unknown language '{lang}'; supported: {}", LANGUAGES.join(", "))
}

/// The language of a d3 time locale name (`de-DE` -> `de`). A custom
/// definition (`None`) or a locale whose language has no catalog is `en`.
pub fn language_of_time_locale(name: Option<&str>) -> &'static str {
    let subtag = name.and_then(|name| name.split('-').next());
    LANGUAGES.iter().copied().find(|l| Some(*l) == subtag).unwrap_or("en")
}

#[derive(Clone, Copy)]
enum Rules {
    NoPlural,
    OneIsExactlyOne,
    OneIsZeroOrOne,
    SpanishItalian,
    Russian,
    Polish,
    Czech,
}

fn rules(lang: &str) -> Result<Rules, String> {
    Ok(match lang {
        "ja" | "zh" | "ko" => Rules::NoPlural,
        "en" | "de" | "nl" | "sv" | "da" | "nb" | "fi" | "tr" => Rules::OneIsExactlyOne,
        "fr" | "pt" => Rules::OneIsZeroOrOne,
        "es" | "it" => Rules::SpanishItalian,
        "ru" | "uk" => Rules::Russian,
        "pl" => Rules::Polish,
        "cs" => Rules::Czech,
        _ => return Err(unknown_language(lang)),
    })
}

impl Rules {
    /// The CLDR cardinal categories, for whole counts and fractions.
    #[cfg(test)]
    fn categories(self) -> &'static [&'static str] {
        match self {
            Rules::NoPlural => &["other"],
            Rules::OneIsExactlyOne => &["one", "other"],
            Rules::OneIsZeroOrOne | Rules::SpanishItalian => &["one", "many", "other"],
            Rules::Russian | Rules::Polish | Rules::Czech => &["one", "few", "many", "other"],
        }
    }

    /// The CLDR plural category of the whole count `n`.
    fn category(self, n: u64) -> &'static str {
        let (m10, m100) = (n % 10, n % 100);
        let few = (2..=4).contains(&m10) && !(12..=14).contains(&m100);
        let million = n != 0 && n % 1_000_000 == 0;
        match self {
            Rules::NoPlural => "other",
            Rules::OneIsExactlyOne if n == 1 => "one",
            Rules::OneIsExactlyOne => "other",
            Rules::OneIsZeroOrOne if n <= 1 => "one",
            Rules::SpanishItalian if n == 1 => "one",
            Rules::OneIsZeroOrOne | Rules::SpanishItalian => {
                if million { "many" } else { "other" }
            }
            Rules::Russian if m10 == 1 && m100 != 11 => "one",
            Rules::Russian | Rules::Polish if few => "few",
            Rules::Polish if n == 1 => "one",
            Rules::Russian | Rules::Polish => "many",
            Rules::Czech => match n {
                1 => "one",
                2..=4 => "few",
                _ => "other",
            },
        }
    }
}

fn plural_category(lang: &str, n: u64) -> Result<&'static str, String> {
    Ok(rules(lang)?.category(n))
}

/// The CLDR plural category of a printed count that may carry a fraction
/// (`2.6`): a whole count follows [`plural_category`]; a fraction is `other`
/// except where CLDR gives `one` to the fractions below two (fr, pt, da) or
/// `many` to every fraction (cs).
pub fn plural_category_of(lang: &str, value: f64) -> Result<&'static str, String> {
    // CLDR classes a count by its magnitude: -1 day reads as 1 day.
    let value = value.abs();
    if value.fract() == 0.0 {
        return plural_category(lang, value as u64);
    }
    Ok(match lang {
        "fr" | "pt" | "da" if value < 2.0 => "one",
        "cs" => "many",
        "ja" | "zh" | "ko" | "en" | "de" | "nl" | "sv" | "nb" | "fi" | "tr" | "fr" | "pt" | "da"
        | "es" | "it" | "ru" | "uk" | "pl" => "other",
        _ => return Err(unknown_language(lang)),
    })
}

/// A Vega/JS expression over the numeric expression `x` that evaluates to the
/// category [`plural_category_of`] returns for it.
pub fn plural_expr(lang: &str, value: &str) -> Result<String, String> {
    let x = format!("abs({value})");
    let x = x.as_str();
    let whole = format!("{x} == floor({x})");
    let (m10, m100) = (format!("({x} % 10)"), format!("({x} % 100)"));
    let few = format!("{m10} >= 2 && {m10} <= 4 && !({m100} >= 12 && {m100} <= 14)");
    let million = format!("{x} != 0 && {x} % 1000000 == 0");
    Ok(match lang {
        "ja" | "zh" | "ko" => "'other'".to_string(),
        "en" | "de" | "nl" | "sv" | "nb" | "fi" | "tr" => format!("({x} == 1 ? 'one' : 'other')"),
        "da" => format!("(({x} == 1 || ({x} < 2 && {x} != floor({x}))) ? 'one' : 'other')"),
        "fr" | "pt" => format!("({x} < 2 ? 'one' : ({whole} && {million}) ? 'many' : 'other')"),
        "es" | "it" => format!("({x} == 1 ? 'one' : ({whole} && {million}) ? 'many' : 'other')"),
        "ru" | "uk" => format!(
            "(!({whole}) ? 'other' : ({m10} == 1 && {m100} != 11) ? 'one' : ({few}) ? 'few' : 'many')"
        ),
        "pl" => format!("(!({whole}) ? 'other' : {x} == 1 ? 'one' : ({few}) ? 'few' : 'many')"),
        "cs" => format!(
            "(!({whole}) ? 'many' : {x} == 1 ? 'one' : ({x} >= 2 && {x} <= 4) ? 'few' : 'other')"
        ),
        _ => return Err(unknown_language(lang)),
    })
}

/// The text of message `id` in `lang`, for a message with no placeholders.
pub fn text(lang: &str, id: &str) -> Result<&'static str, String> {
    match catalog(lang)?.get(id).ok_or_else(|| format!("language '{lang}' has no message '{id}'"))? {
        Entry::Text(text) => Ok(text),
        Entry::Plural(_) => Err(format!("message '{id}' in '{lang}' is plural")),
    }
}

/// The forms of plural message `id` in `lang`, by category.
pub fn forms(lang: &str, id: &str) -> Result<&'static BTreeMap<String, String>, String> {
    match catalog(lang)?.get(id).ok_or_else(|| format!("language '{lang}' has no message '{id}'"))? {
        Entry::Plural(forms) => Ok(forms),
        Entry::Text(_) => Err(format!("message '{id}' in '{lang}' is not plural")),
    }
}

/// Message `id` in `lang`. `n` selects the plural form of a plural entry and
/// fills `{n}`; every other placeholder takes its value from `args`.
pub fn message(
    lang: &str,
    id: &str,
    n: Option<u64>,
    args: &[(&str, &str)],
) -> Result<String, String> {
    let entry = catalog(lang)?
        .get(id)
        .ok_or_else(|| format!("language '{lang}' has no message '{id}'"))?;
    let template = match (entry, n) {
        (Entry::Text(text), _) => text,
        (Entry::Plural(forms), Some(n)) => {
            let category = plural_category(lang, n)?;
            forms.get(category).ok_or_else(|| {
                format!("message '{id}' in '{lang}' has no '{category}' form")
            })?
        }
        (Entry::Plural(_), None) => {
            return Err(format!("message '{id}' in '{lang}' is plural and needs a count"))
        }
    };
    if n.is_some() && args.iter().any(|(key, _)| *key == "n") {
        return Err(format!("message '{id}' in '{lang}': arg 'n' clashes with the count"));
    }
    fill(lang, id, template, n.map(|n| n.to_string()).as_deref(), args)
}

/// Plural message `id` in `lang` for a printed `value` that may carry a
/// fraction; `args` must supply `{n}`, the value as printed.
pub fn message_for_value(
    lang: &str,
    id: &str,
    value: f64,
    args: &[(&str, &str)],
) -> Result<String, String> {
    let category = plural_category_of(lang, value)?;
    let template = forms(lang, id)?
        .get(category)
        .ok_or_else(|| format!("message '{id}' in '{lang}' has no '{category}' form"))?;
    fill(lang, id, template, None, args)
}

fn fill(
    lang: &str,
    id: &str,
    template: &str,
    count: Option<&str>,
    args: &[(&str, &str)],
) -> Result<String, String> {
    let mut used = vec![false; args.len()];
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(open) = rest.find('{') {
        out.push_str(&rest[..open]);
        let close = rest[open..]
            .find('}')
            .ok_or_else(|| format!("message '{id}' in '{lang}': unclosed '{{'"))?
            + open;
        let name = &rest[open + 1..close];
        let value = match args.iter().position(|(key, _)| *key == name) {
            Some(i) => {
                used[i] = true;
                args[i].1
            }
            None if name == "n" && count.is_some() => count.expect("checked"),
            None => return Err(format!("message '{id}' in '{lang}': no value for '{{{name}}}'")),
        };
        out.push_str(value);
        rest = &rest[close + 1..];
    }
    out.push_str(rest);
    if let Some(i) = used.iter().position(|u| !u) {
        return Err(format!("message '{id}' in '{lang}': unused arg '{}'", args[i].0));
    }
    Ok(out)
}

/// A shipped language code, checked when built. The default is `en`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Language(&'static str);

impl Default for Language {
    fn default() -> Self {
        Self("en")
    }
}

impl Language {
    /// The language's code (`de`).
    pub fn code(self) -> &'static str {
        self.0
    }

    pub fn new(code: &str) -> Result<Self, String> {
        LANGUAGES
            .iter()
            .copied()
            .find(|l| *l == code)
            .map(Self)
            .ok_or_else(|| unknown_language(code))
    }
}

impl<'de> Deserialize<'de> for Language {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let code = String::deserialize(deserializer)?;
        Self::new(&code).map_err(serde::de::Error::custom)
    }
}

thread_local! {
    static ACTIVE: RefCell<Language> = RefCell::new(Language::default());
}

struct Restore(Language);

impl Drop for Restore {
    fn drop(&mut self) {
        ACTIVE.with(|active| *active.borrow_mut() = self.0);
    }
}

pub fn with_language<T>(lang: Language, render: impl FnOnce() -> T) -> T {
    let previous = ACTIVE.with(|active| active.replace(lang));
    let _restore = Restore(previous);
    render()
}

/// The active language's code; `en` outside a scope.
pub fn active_language() -> &'static str {
    ACTIVE.with(|active| active.borrow().0)
}

/// [`message`] in the active language, `en` outside a scope.
pub fn tr(id: &str, n: Option<u64>, args: &[(&str, &str)]) -> Result<String, String> {
    message(active_language(), id, n, args)
}

/// The theme's footer text, timestamp format and placeholder overlay text are
/// `auto` until an author writes their own; style resolution bakes the board
/// language's words in, with the brand left untranslated inside them.
pub const THEME_AUTO: &str = "auto";
pub const BRAND: &str = "dbt Charts";

/// [`message_for_value`] in the active language.
pub fn tr_value(id: &str, value: f64, args: &[(&str, &str)]) -> Result<String, String> {
    message_for_value(active_language(), id, value, args)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_catalog_has_the_english_keys_and_its_languages_plural_forms() {
        let english = catalog("en").unwrap();
        for lang in LANGUAGES {
            let catalog = catalog(lang).unwrap();
            assert_eq!(
                catalog.keys().collect::<Vec<_>>(),
                english.keys().collect::<Vec<_>>(),
                "{lang}: keys differ from en"
            );
            let want = rules(lang).unwrap().categories();
            for (id, entry) in catalog {
                match (entry, &english[id]) {
                    (Entry::Text(_), Entry::Text(_)) => {}
                    (Entry::Plural(forms), Entry::Plural(_)) => {
                        let mut have: Vec<&str> = forms.keys().map(String::as_str).collect();
                        let mut want = want.to_vec();
                        have.sort();
                        want.sort();
                        assert_eq!(have, want, "{lang}: {id} plural forms");
                    }
                    _ => panic!("{lang}: {id} is plural in only one of {lang} and en"),
                }
            }
        }
    }

    #[test]
    fn every_category_a_count_selects_is_one_the_language_has() {
        for lang in LANGUAGES {
            let have = rules(lang).unwrap().categories();
            for n in (0..2000).chain([1_000_000, 2_000_000, 5_000_001]) {
                let category = plural_category(lang, n).unwrap();
                assert!(have.contains(&category), "{lang} {n}: {category}");
            }
        }
    }

    #[test]
    fn every_form_carries_the_placeholders_of_the_english_message() {
        fn names(text: &str) -> Vec<&str> {
            let mut names: Vec<&str> =
                text.split('{').skip(1).filter_map(|s| s.split('}').next()).collect();
            names.sort_unstable();
            names
        }
        let forms = |entry: &Entry| -> Vec<String> {
            match entry {
                Entry::Text(text) => vec![text.clone()],
                Entry::Plural(forms) => forms.values().cloned().collect(),
            }
        };
        for lang in LANGUAGES {
            for (id, entry) in catalog(lang).unwrap() {
                let want = names(&forms(&catalog("en").unwrap()[id])[0]).join(",");
                for form in forms(entry) {
                    assert_eq!(names(&form).join(","), want, "{lang}: {id}");
                }
            }
        }
    }

    #[test]
    fn plural_category_uses_the_magnitude() {
        assert_eq!(plural_category_of("fr", -1.0).unwrap(), "one");
        assert_eq!(plural_category_of("en", -1.0).unwrap(), "one");
        assert_eq!(plural_category_of("ru", -2.0).unwrap(), "few");
        assert!(plural_expr("fr", "x").unwrap().contains("abs(x)"));
    }

    #[test]
    fn plural_categories_follow_cldr_for_whole_counts() {
        let cases: &[(&str, u64, &str)] = &[
            ("en", 1, "one"), ("en", 0, "other"), ("en", 2, "other"),
            ("fr", 0, "one"), ("fr", 1, "one"), ("fr", 2, "other"), ("fr", 1_000_000, "many"),
            ("pt", 0, "one"), ("pt", 2, "other"),
            ("es", 0, "other"), ("es", 1, "one"), ("es", 2_000_000, "many"),
            ("ru", 1, "one"), ("ru", 21, "one"), ("ru", 11, "many"), ("ru", 2, "few"),
            ("ru", 14, "many"), ("ru", 22, "few"), ("ru", 5, "many"), ("ru", 0, "many"),
            ("uk", 1, "one"), ("uk", 3, "few"), ("uk", 12, "many"),
            ("pl", 1, "one"), ("pl", 21, "many"), ("pl", 22, "few"), ("pl", 12, "many"),
            ("pl", 0, "many"),
            ("cs", 1, "one"), ("cs", 3, "few"), ("cs", 5, "other"), ("cs", 0, "other"),
            ("ja", 1, "other"), ("zh", 7, "other"), ("ko", 0, "other"),
        ];
        for (lang, n, want) in cases {
            assert_eq!(plural_category(lang, *n).unwrap(), *want, "{lang} {n}");
        }
    }

    #[test]
    fn a_message_picks_the_plural_form_and_fills_placeholders() {
        assert_eq!(message("ru", "series.count", Some(3), &[]).unwrap(), "3 серии");
        assert_eq!(message("ru", "series.count", Some(11), &[]).unwrap(), "11 серий");
        assert_eq!(message("de", "series.count", Some(1), &[]).unwrap(), "1 Serie");
        assert_eq!(message("de", "tab.title", None, &[("n", "2")]).unwrap(), "Tab 2");
        assert_eq!(message("ja", "tab.title", None, &[("n", "2")]).unwrap(), "タブ 2");
    }

    #[test]
    fn bad_calls_error_instead_of_falling_back() {
        let err = message("xx", "tab.title", None, &[("n", "1")]).unwrap_err();
        assert!(err.contains("supported: en, de"), "{err}");
        assert!(message("de", "nope", None, &[]).unwrap_err().contains("no message 'nope'"));
        assert!(message("de", "tab.title", None, &[]).unwrap_err().contains("no value for '{n}'"));
        assert!(message("de", "tab.title", None, &[("n", "1"), ("x", "2")])
            .unwrap_err()
            .contains("unused arg 'x'"));
        assert!(message("de", "series.count", None, &[]).unwrap_err().contains("needs a count"));
        let clash = message("ru", "series.count", Some(3), &[("n", "5")]).unwrap_err();
        assert!(clash.contains("clashes with the count"), "{clash}");
    }

    #[test]
    fn a_time_locale_maps_to_its_language() {
        assert_eq!(language_of_time_locale(Some("de-DE")), "de");
        assert_eq!(language_of_time_locale(Some("zh-TW")), "zh");
        assert_eq!(language_of_time_locale(Some("nb-NO")), "nb");
    }

    #[test]
    fn a_custom_definition_is_english() {
        assert_eq!(language_of_time_locale(None), "en");
    }

    #[test]
    fn a_time_locale_without_a_catalog_is_english() {
        for name in ["ar-EG", "he-IL", "ca-ES"] {
            assert_eq!(language_of_time_locale(Some(name)), "en", "{name}");
        }
    }

    #[test]
    fn tr_reads_the_scoped_language_and_is_english_outside_one() {
        assert_eq!(tr("tab.title", None, &[("n", "1")]).unwrap(), "Tab 1");
        let fr = Language::new("fr").unwrap();
        let out = with_language(fr, || tr("tab.title", None, &[("n", "1")]));
        assert_eq!(out.unwrap(), "Onglet 1");
        assert!(Language::new("xx").unwrap_err().contains("supported: en, de"));
        let parsed: Result<Language, _> = serde_json::from_str("\"xx\"");
        assert!(parsed.unwrap_err().to_string().contains("supported: en, de"));
        assert_eq!(serde_json::from_str::<Language>("\"ko\"").unwrap(), Language::new("ko").unwrap());
        assert_eq!(tr("tab.title", None, &[("n", "1")]).unwrap(), "Tab 1");
    }

    #[test]
    fn fractions_pick_cldr_categories() {
        let cases: &[(&str, f64, &str)] = &[
            ("en", 1.0, "one"), ("en", 1.5, "other"), ("fr", 1.5, "one"), ("fr", 2.5, "other"),
            ("pt", 0.5, "one"), ("da", 1.5, "one"), ("da", 2.5, "other"), ("cs", 2.6, "many"),
            ("ru", 2.6, "other"), ("pl", 1.5, "other"), ("ja", 2.6, "other"), ("es", 1.5, "other"),
        ];
        for (lang, value, want) in cases {
            assert_eq!(plural_category_of(lang, *value).unwrap(), *want, "{lang} {value}");
        }
    }

    #[test]
    fn the_plural_expression_names_every_category_of_its_language() {
        for lang in LANGUAGES {
            let expr = plural_expr(lang, "x").unwrap();
            for category in rules(lang).unwrap().categories() {
                assert!(expr.contains(&format!("'{category}'")), "{lang}: {expr} lacks {category}");
            }
        }
    }

    #[test]
    fn a_message_for_a_printed_value_uses_its_category() {
        assert_eq!(message_for_value("ru", "duration.week", 2.6, &[("n", "2,6")]).unwrap(), "2,6 недели");
        assert_eq!(message_for_value("ru", "duration.week", 5.0, &[("n", "5")]).unwrap(), "5 недель");
        assert_eq!(message_for_value("en", "duration.day", 1.0, &[("n", "1")]).unwrap(), "1 day");
    }
}
