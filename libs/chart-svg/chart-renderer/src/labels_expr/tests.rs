//! Each behavior the evaluator must share with Vega's generated JavaScript, one test per divergence a reader of
//! the expression would otherwise get wrong. The expected text is what Vega paints (`corpus_tests` replays
//! thousands more against real Vega output).

use super::funcs::{self, Support, TABLE};
use super::{EvalError, Expr, Tick, Val};

fn lines(src: &str, value: Val, label: &str, index: f64) -> Result<Vec<String>, EvalError> {
    let _locale = crate::time_locale::active_time_locale().is_err().then(crate::time_locale::en_us_scope);
    Expr::parse(src)?.eval_lines(&Tick { value, label, index })
}

fn text(src: &str, label: &str) -> String {
    lines(src, Val::Num(1.0), label, 0.0).unwrap().join("|")
}

#[test]
fn datum_index_marks_the_first_and_last_tick() {
    let src = "datum.index == 0 || datum.index == 1 ? datum.label : ''";
    let paints = |i: usize, n: usize| lines(src, Val::Num(i as f64), "L", Tick::at(i, n, Val::Null, "").index).unwrap().join("");
    assert_eq!((paints(0, 5), paints(2, 5), paints(4, 5)), ("L".into(), String::new(), "L".into()));
    assert_eq!(Tick::at(0, 1, Val::Null, "").index, 0.0, "a lone tick sits at i / (n - 1 || 1)");
    assert_eq!(text("datum.nope", "x"), "", "an absent datum field is undefined and paints nothing");
}

#[test]
fn double_equals_is_abstract_equality() {
    assert_eq!(text("datum.label == 0 ? '' : datum.label", "0"), "");
    assert_eq!(text("datum.label == 0 ? '' : datum.label", "5"), "5");
    assert_eq!(text("datum.label === 0 ? '' : datum.label", "0"), "0");
    assert_eq!(text("datum.label != 0 ? 'y' : 'n'", "0"), "n");
    assert!(matches!(Expr::parse("undefined"), Err(EvalError::Unknown(_))), "`undefined` is an unknown signal");
    assert_eq!(text("'1' == 1 && null == 0 ? 'a' : 'b'", ""), "b");
    assert_eq!(text("[1] == 1 ? 'a' : 'b'", ""), "a");
}

#[test]
fn is_nan_does_not_coerce() {
    assert_eq!(text("isNaN(datum.label)", "abc"), "false");
    assert_eq!(text("isNaN(toNumber(datum.label))", "abc"), "true");
    assert_eq!(text("isNaN(NaN)", ""), "true");
    assert_eq!(text("isFinite('5')", ""), "false");
}

#[test]
fn to_boolean_follows_vega_util() {
    for (arg, want) in [("'false'", "false"), ("'0'", "false"), ("0", "false"), ("'a'", "true"), ("1", "true"), ("''", ""), ("null", ""), ("[]", "true")] {
        assert_eq!(text(&format!("toBoolean({arg})"), ""), want, "{arg}");
    }
}

#[test]
fn string_escapes_decode_as_javascript_decodes_them() {
    assert_eq!(text("' \\u00b7 '", ""), "\u{b7}");
    assert_eq!(text("'\\x41\\u{1F600}'", ""), "A\u{1F600}");
    assert_eq!(text("'a\\tb'", ""), "a\tb".trim_matches(' '));
    assert_eq!(text("'a\\0b'", ""), "a\0b");
    assert_eq!(text("'\\q'", ""), "q");
    assert_eq!(text("length('\\r\\n\\b\\f\\v')", ""), "5");
    assert!(matches!(Expr::parse("'\\1'"), Err(EvalError::Unknown(_))), "octal escapes are rejected");
    assert!(matches!(Expr::parse("'\\xZZ'"), Err(EvalError::Unknown(_))));
}

#[test]
fn regex_classes_are_ascii_without_the_unicode_flag() {
    assert_eq!(text("replace(datum.label, /\\W/g, '')", "Z\u{fc}rich 7"), "Zrich7");
    assert_eq!(text("replace(datum.label, /\\w+/g, '<$&>')", "Z\u{fc}rich"), "<Z>\u{fc}<rich>");
    assert_eq!(text("test(/^\\d+$/, datum.label)", "\u{663}"), "false");
    assert_eq!(text("test(/\\s/, datum.label)", "\u{a0}"), "true", "\\s is the ECMAScript white space");
    assert_eq!(text("replace(datum.label, /\\b/g, '|')", "ab cd"), "|ab| |cd|");
}

#[test]
fn lookaround_and_backreferences_compile() {
    assert_eq!(text("replace(datum.label, /(?<=\\d)(?=(\\d{3})+$)/g, ',')", "1234567"), "1,234,567");
    assert_eq!(text("replace(datum.label, /(a)\\1/, '[$1]')", "xaay"), "x[a]y");
}

#[test]
fn coercing_calls_render_where_the_generated_code_casts() {
    assert_eq!(text("month(datum.label)", "2020-03-04"), "2");
    assert_eq!(text("substring(toDate('2020-03-04'), 0, 3)", ""), "158");
    assert_eq!(text("test('^a', datum.label)", "abc"), "true");
    assert_eq!(text("if(datum.label, 'a', 'b')", ""), "b");
    assert_eq!(text("pad(datum.label, 5, '0', 'left')", "42"), "00042");
    assert_eq!(text("truncate(datum.label, 5, 'center')", "abcdefghij"), "ab\u{2026}ij");
    assert_eq!(text("isValid(datum.nope) + ',' + isValid(0)", ""), "false,true");
    assert_eq!(text("parseFloat('1.5kg') + parseInt('0x1F', 16)", ""), "32.5");
    assert_eq!(text("join(split('a,b,c', ','), '-')", ""), "a-b-c");
    assert_eq!(text("exp(0) + log(1)", ""), "1");
}

#[test]
fn only_real_throws_and_unknown_names_fail() {
    assert!(matches!(lines("slice(null, 1)", Val::Null, "", 0.0), Err(EvalError::Throws(_))));
    assert!(matches!(lines("indexof(5, 5)", Val::Null, "", 0.0), Err(EvalError::Throws(_))));
    assert!(matches!(lines("replace('a', 5, 'b')", Val::Null, "", 0.0), Err(EvalError::Throws(_))));
    assert!(matches!(lines("regexp('(')", Val::Null, "", 0.0), Err(EvalError::Throws(_))));
    assert!(matches!(lines("view()", Val::Null, "", 0.0), Err(EvalError::Throws(_))), "no event outside an event handler");
    assert!(matches!(Expr::parse("nope(1)"), Err(EvalError::Unknown(_))));
    assert!(matches!(Expr::parse("foo"), Err(EvalError::Unknown(_))));
    assert!(matches!(Expr::parse("1 +"), Err(EvalError::Unknown(_))));
    assert!(matches!(Expr::parse("/a/s"), Err(EvalError::Unknown(_))), "Vega's lexer takes only the g, i, m, u and y flags");
    assert!(matches!(Expr::parse("length(5)"), Err(EvalError::Unknown(_))), "`5.length` is not JavaScript");
    assert!(matches!(lines("item.x", Val::Null, "", 0.0), Err(EvalError::Unmodeled(_))));
}

#[test]
fn null_and_undefined_paint_nothing_and_arrays_paint_a_line_each() {
    assert_eq!(lines("null", Val::Null, "", 0.0).unwrap(), vec![String::new()]);
    assert_eq!(lines("[datum.label, ' x ']", Val::Null, "a", 0.0).unwrap(), vec!["a".to_string(), "x".to_string()]);
    assert_eq!(lines("['only']", Val::Null, "", 0.0).unwrap(), vec!["only".to_string()]);
    assert_eq!(lines("'  padded  '", Val::Null, "", 0.0).unwrap(), vec!["padded".to_string()]);
}

/// Every name in Vega's function table (`corpus/names.mjs`) is evaluated here or rejected by name; each name
/// Vega does not know is rejected.
#[test]
fn every_name_in_vegas_function_table_is_accounted_for() {
    let vega: Vec<String> = serde_json::from_str(include_str!("corpus/function_names.json")).unwrap();
    assert_eq!(vega.len(), TABLE.len());
    for (name, support) in TABLE {
        assert!(vega.iter().any(|v| v == name), "{name} is not in Vega's table");
        if name == "if" {
            continue; // a conditional, resolved when the expression is parsed
        }
        let args = || (0..5).map(|_| Val::Undefined).collect::<Vec<_>>();
        let called = funcs::call(name, &args(), &Val::Undefined);
        match support {
            Support::Evaluated | Support::Impure => {
                assert!(!matches!(called, Err(EvalError::Unmodeled(_))), "{name} is listed as evaluated but is not");
            }
            Support::Throws => assert!(matches!(called, Err(EvalError::Throws(_))), "{name} must throw outside an event handler"),
        }
    }
    for name in vega {
        assert!(TABLE.iter().any(|(n, _)| *n == name), "{name} is in Vega's table but not ours");
    }
    for absent in ["substr", "toUpperCase", "includes", "startsWith", "Math", "parseDate", "ceiling", "len", "sum", "mean", "datetimeFormat"] {
        assert!(matches!(Expr::parse(&format!("{absent}(1)")), Err(EvalError::Unknown(_))), "{absent} is not a Vega function");
    }
}

#[test]
fn a_chart_with_a_rejected_expression_fails_the_conversion() {
    let resolved = serde_json::json!({"style": {"axis_x": {"labels": {"expr": "nope(1)"}}}});
    let err = super::validate(&resolved).unwrap_err();
    assert!(err.to_string().contains("ERR-INTERNAL"), "{err}");
    let fine = serde_json::json!({"style": {"axis_x": {"labels": {"expr": "upper(datum.label)"}}, "legend": {"labels": {"expr": "nope(1)"}}}});
    assert!(super::validate(&fine).is_ok());
}

/// Runs `body` with a view holding the scale `x` over `[0, 100]` on a 300px plot and a source of two rows.
fn with_view<T>(body: impl FnOnce() -> T) -> T {
    use super::view::{enter, register_band, register_linear, set_plot, set_rows, Span};
    let _open = enter();
    set_plot(300.0, 200.0);
    register_linear("y", 0.0, 100.0, Span::Height);
    register_band("b", &["a".to_string(), "b".to_string()], 0.0, 0.0, Span::Width).unwrap();
    let rows = serde_json::json!([{"a": 1, "k": "x"}, {"a": 2, "k": "y"}]);
    set_rows(&rows.as_array().unwrap().iter().map(|r| r.as_object().unwrap().clone()).collect::<Vec<_>>()).unwrap();
    body()
}

#[test]
fn scale_functions_read_the_plots_scales_at_the_plot_size() {
    with_view(|| {
        assert_eq!(text("scale('y', 25)", ""), "150", "a y range runs from the plot's height to 0");
        assert_eq!(text("range('y')", ""), "200|0", "an array paints a line per element");
        assert_eq!(text("domain('y') + ''", ""), "0,100");
        assert_eq!(text("invert('y', 50)", ""), "75");
        assert_eq!(text("scale('b', 'b')", ""), "150");
        assert_eq!(text("bandwidth('b')", ""), "150");
        assert_eq!(text("bandwidth('y')", ""), "0", "only a band scale has a bandwidth");
        assert_eq!(text("scale('nope', 1)", ""), "", "a scale that does not exist is undefined");
        assert_eq!(text("domain('nope')", ""), "");
        assert_eq!(text("copy('y')", ""), "function g(t){return null==t||isNaN(t=+t)?r:(u||(u=e(o.map(n),i,c)))(n(p(t)))}");
        assert!(matches!(lines("scale('y', 1, 2)", Val::Null, "", 0.0), Err(EvalError::Throws(_))), "a group has no context to read");
    });
}

#[test]
fn data_functions_read_the_charts_source() {
    with_view(|| {
        assert_eq!(text("length(data('source_0'))", ""), "2");
        assert_eq!(text("data('source_0')[1].k", ""), "y");
        assert_eq!(text("indata('source_0', 'a', 2)", ""), "1");
        assert_eq!(text("indata('source_0', 'a', 3)", ""), "", "a value the source lacks counts as undefined");
        assert_eq!(text("length(data('nope'))", ""), "0");
        assert!(matches!(lines("data('data_0')", Val::Null, "", 0.0), Err(EvalError::Unmodeled(_))), "Vega-Lite derives its other datasets");
        assert!(matches!(lines("indata('nope', 'a', 1)", Val::Null, "", 0.0), Err(EvalError::Unknown(_))), "an unknown dataset fails the compiler");
    });
}

#[test]
fn a_string_that_spells_an_object_method_fails_the_compiler() {
    for method in ["toString", "constructor", "valueOf", "hasOwnProperty"] {
        assert!(matches!(Expr::parse(&format!("'{method}'")), Err(EvalError::Unknown(_))), "{method}");
    }
    assert!(Expr::parse("'__proto__' + 'prototype' + 'constructor1'").is_ok());
}

#[test]
fn reading_a_built_in_method_gives_its_native_source() {
    assert_eq!(text("(1).toFixed", ""), "function toFixed() { [native code] }");
    assert_eq!(text("datum.label.toUpperCase", "a"), "function toUpperCase() { [native code] }");
    assert_eq!(text("[].map", ""), "function map() { [native code] }");
    assert_eq!(text("datum.label.nope", "a"), "");
}

#[test]
fn lassos_trees_and_geometry_follow_vega() {
    assert_eq!(text("lassoPath([[0,0],[1,1],[2,2]])", ""), "M 0,0 L 1,1  Z");
    assert_eq!(text("lassoAppend([[0,0]], 1, 1)", ""), "0,0");
    assert_eq!(text("lassoAppend([[0,0]], 10, 10)", ""), "0,0|10,10");
    with_view(|| {
        assert_eq!(text("treePath('source_0', 'a', 'b')", ""), "", "rows have no tree");
        assert_eq!(text("geoArea(0, {type: 'Sphere'})", ""), "12.566370614359172");
        assert_eq!(text("geoArea('nope', {type: 'Sphere'})", ""), "", "a name no projection answers to");
        assert!(matches!(lines("geoArea('y', {})", Val::Null, "", 0.0), Err(EvalError::Throws(_))), "a scale is no projection");
        assert!(matches!(lines("geoScale('y')", Val::Null, "", 0.0), Err(EvalError::Throws(_))), "a scale is no projection");
    });
}

#[test]
fn a_label_item_carries_its_axis_layout_and_what_its_last_evaluation_left() {
    use super::view::{enter, register_axis, register_linear, set_plot, AxisSpec, ItemStyle, Orient, Span};
    let _open = enter();
    set_plot(300.0, 200.0);
    register_linear("y", 0.0, 100.0, Span::Height);
    let style = ItemStyle { font: "f".into(), size: 11.0, fill: "#000".into(), weight: Val::Num(400.0), limit: 180.0 };
    register_axis("y", AxisSpec { orient: Orient::Right, offset: 6.0, shift: 0.0, align: None, baseline: None, angle: 0.0, flush: None, style });
    let expr = Expr::parse("item.x + ' ' + item.y + ' ' + item.align + ' ' + item.text + ' ' + item.fontSize").unwrap().on("y");
    let tick = Tick { value: Val::Num(25.0), label: "", index: 0.0 };
    assert_eq!(expr.eval_lines(&tick).unwrap(), ["6 150 undefined undefined 11"], "a new item has its position, not yet its anchor or text");
    assert_eq!(expr.eval_lines(&tick).unwrap(), ["6 150 undefined undefined 11"], "the same plot is the same evaluation");
    set_plot(300.0, 100.0);
    assert_eq!(expr.eval_lines(&tick).unwrap(), ["6 75 left 6 150 undefined undefined 11 11"], "the position is current; the anchor and text are the last evaluation's");
    assert!(matches!(Expr::parse("item.bounds").unwrap().on("y").eval_lines(&tick), Err(EvalError::Unmodeled(_))));
    assert!(matches!(Expr::parse("item.x").unwrap().on("x").eval_lines(&tick), Err(EvalError::Unmodeled(_))), "an axis nobody laid out");
}

#[test]
fn an_expression_deeper_than_the_bound_is_refused_not_a_stack_overflow() {
    // The default thread stack is 512 KB on non-main threads: the bound must hold there, for the nesting of
    // parentheses and of operators, members and calls alike.
    let shapes: Vec<String> = vec![
        format!("{}1{}", "(".repeat(20_000), ")".repeat(20_000)),
        format!("{}1", "!".repeat(20_000)),
        format!("{}1{}", "[".repeat(20_000), "]".repeat(20_000)),
        format!("datum.label{}", "+''".repeat(200_000)),
        format!("datum.label{}", "&&1".repeat(200_000)),
        format!("datum.label{}", "||1".repeat(200_000)),
        format!("datum{}", ".a".repeat(200_000)),
        format!("datum{}", "['a']".repeat(200_000)),
        format!("{}datum.label{}", "abs(".repeat(20_000), ")".repeat(20_000)),
        format!("{}1", "1?1:".repeat(20_000)),
    ];
    let results: Vec<_> = std::thread::Builder::new()
        .stack_size(512 * 1024)
        .spawn(move || shapes.iter().map(|src| Expr::parse(src).err()).collect::<Vec<_>>())
        .unwrap()
        .join()
        .expect("deep nesting is a refusal, not a stack overflow");
    for found in results {
        assert!(matches!(found, Some(EvalError::Unmodeled(ref m)) if m.contains("nested past 100 levels")), "{found:?}");
    }
    assert!(Expr::parse(&format!("{}1{}", "(".repeat(99), ")".repeat(99))).is_ok());
    assert_eq!(text(&format!("datum.label{}", "+''".repeat(90)), "ab"), "ab");
}

#[test]
fn a_regexp_that_backtracks_is_answered_or_refused_never_called_a_miss() {
    let test = |n: usize| text(&format!(r"test(/(a|a)*\bb/, '{} b') + ''", "a".repeat(n)), "");
    assert_eq!(test(20), "true", "V8 answers it; so must a miss that is only slow");
    assert!(matches!(lines(&format!(r"test(/(a|a)*\bb/, '{} b') + ''", "a".repeat(32)), Val::Null, "", 0.0), Err(EvalError::Unmodeled(_))));
}


#[test]
fn a_regexp_with_deeply_nested_groups_is_refused_not_a_stack_overflow() {
    let nested = |n: usize| format!("test(/{}a{}/, 'a') + ''", "(".repeat(n), ")".repeat(n));
    assert!(matches!(Expr::parse(&nested(20_000)), Err(EvalError::Unmodeled(_))));
    assert_eq!(text(&nested(50), ""), "true");
}

#[test]
fn pad_and_sequence_stop_where_javascript_does_not_where_the_heap_would() {
    assert!(matches!(lines("pad('', 1e7, pad('', 1e7, 'x'))", Val::Null, "", 0.0), Err(EvalError::Throws(m)) if m == "Invalid string length"));
    assert!(matches!(lines("pad('', 1e300, 'x')", Val::Null, "", 0.0), Err(EvalError::Throws(m)) if m == "Invalid string length"));
    assert_eq!(text("length(pad('', 20000000, 'x')) + ''", ""), "20000000", "a long pad is a valid string, not a truncated one");
    assert_eq!(text("pad('a', NaN, 'x')", ""), "a");
    assert!(matches!(lines("sequence(20000000)", Val::Null, "", 0.0), Err(EvalError::Unmodeled(_))));
    assert!(matches!(lines("timeSequence('seconds', 0, 1e12)", Val::Null, "", 0.0), Err(EvalError::Unmodeled(_))));
}

#[test]
fn toDate_keeps_three_digits_of_a_longer_fraction_like_v8() {
    assert_eq!(text("timeFormat(toDate('2024/01/01 10:00:00.1234567891'), '%L')", ""), "123");
}

#[test]
fn time_formats_speak_the_boards_time_locale() {
    crate::time_locale::with_time_locale(&crate::time_locale::de_de(), || {
        assert_eq!(text("timeFormat(toDate('2024-01-15'), '%B %b %A %a')", ""), "Januar Jan Montag Mo");
        assert_eq!(text("timeFormat(toDate('2024-03-02'), '%x')", ""), "02.03.2024");
        assert_eq!(text("monthFormat(2)", ""), "März");
        assert_eq!(text("timeFormat(timeParse('15. Januar 2024', '%d. %B %Y'), '%Y-%m-%d')", ""), "2024-01-15");
    });
}

#[test]
fn inverting_a_time_scale_past_what_a_date_holds_is_an_invalid_date() {
    use super::view::{enter, register_utc, set_plot, Span};
    let _open = enter();
    set_plot(300.0, 200.0);
    register_utc("t", 0.0, 86_400_000.0 * 365.0, Span::Width);
    assert_eq!(text("year(invert('t', 1e20)) + ''", ""), "NaN");
    assert_eq!(text("year(invert('t', 150)) + ''", ""), "1970");
}

#[test]
fn the_scan_pass_reports_the_error_a_family_raised_before_its_fit() {
    let scanned = super::view::render_passes::<(), String>(&[], || Err("bar: the chart is too small".to_string()));
    assert_eq!(scanned, Err("bar: the chart is too small".to_string()));
}

#[test]
fn a_channel_scale_the_chart_does_not_register_is_refused_not_read_as_empty() {
    with_view(|| {
        for src in ["range('color')[0]", "scale('size', 3)", "bandwidth('xOffset')", "domain('color')", "copy('shape')"] {
            assert!(matches!(lines(src, Val::Null, "", 0.0), Err(EvalError::Unmodeled(_))), "{src}");
        }
        assert_eq!(text("scale('nope', 1)", ""), "", "a name no chart has is genuinely undefined");
    });
}

#[test]
fn a_gradient_with_no_ticks_still_gets_its_end_stops() {
    // d3's ticks come back empty for a count that is not positive; Vega then unshifts the domain's ends.
    for count in ["-1", "0.5", "-1/0", "0/0 - 1"] {
        let src = format!("datum.label + length(gradient('y', [0,0], [1,0], {count}).stops)");
        let found = with_view(|| text(&src, "x"));
        assert!(found.starts_with('x'), "{src}: {found}");
    }
}

fn too_long(src: &str) -> bool {
    matches!(lines(src, Val::Null, "", 0.0), Err(EvalError::Throws(m)) if m == "Invalid string length")
}

fn was_declined(src: &str) -> bool {
    matches!(lines(src, Val::Null, "", 0.0), Err(EvalError::Unmodeled(_)))
}

/// A million units and a few megabytes stand in for V8's string and the evaluation budget, so every builder's
/// limit is reached with inputs of that size.
fn small() -> impl Drop {
    super::val::small_limits(1_000_000, 64 << 20)
}

#[test]
fn a_string_past_the_limit_throws_wherever_it_is_built() {
    let _small = small();
    let big = "pad('', 700000, 'x')";
    for src in [
        format!("{big} + {big}"),
        "join(sequence(600), pad('', 3000, 'x'))".to_string(),
        format!("replace('abcabc', /b/g, {big})"),
        "replace(pad('', 1000, 'x'), pad('', 1000, 'x'), pad('', 4000, '$&'))".to_string(),
        "replace(pad('', 1000, 'x'), /x/g, pad('', 4000, '$`$\\''))".to_string(),
        "timeFormat(0, pad('', 100000, '%c'))".to_string(),
        "format(1, '>1000000000')".to_string(),
        "encodeURIComponent(pad('', 200000, '\u{e9}'))".to_string(),
        "btoa(pad('', 800000, 'x'))".to_string(),
        "upper(pad('', 400000, '\u{390}'))".to_string(),
    ] {
        assert!(too_long(&src), "{src}");
    }
    assert!(matches!(lines("format(1, '.1000000000f')", Val::Null, "", 0.0), Err(EvalError::Throws(_))));
}

#[test]
fn an_evaluation_past_its_memory_budget_is_refused_wherever_it_allocates() {
    let _small = super::val::small_limits(1_000_000, 8 << 20);
    for src in [
        // Each of these makes a string, an array or a date array V8 builds without trouble.
        "length(split(pad('', 300000, 'a'), ''))",
        "length(toString(timeSequence('milliseconds', 0, 300000)))",
        "length(sequence(150000)) + length(sequence(150000)) + length(sequence(150000)) + length(sequence(150000))",
        "length([sequence(150000), sequence(150000), sequence(150000), sequence(150000)])",
        "length(merge(sequence(150000), sequence(150000), sequence(150000)))",
        "length(split(pad('', 450000, 'a,'), ','))",
        "length([pad('', 900000, 'a'), pad('', 900000, 'a'), pad('', 900000, 'a'), pad('', 900000, 'a'), pad('', 900000, 'a'), pad('', 900000, 'a'), pad('', 900000, 'a'), pad('', 900000, 'a'), pad('', 900000, 'a'), pad('', 900000, 'a')])",
    ] {
        assert!(was_declined(src), "{src}");
    }
    assert_eq!(text("length(sequence(1000)) + ''", ""), "1000");
}

#[test]
fn a_sequence_past_the_cap_is_refused_at_the_real_limits() {
    assert!(was_declined("sequence(3000000)"));
    assert!(was_declined("timeSequence('milliseconds', 0, 3000000)"));
}

/// The cases at V8's real string length. They take gigabytes, so they run on demand:
/// `cargo test -p chart-renderer full_size -- --ignored`.
#[test]
#[ignore = "allocates gigabytes"]
fn full_size_a_string_past_v8s_limit_throws_wherever_it_is_built() {
    let big = "pad('', 270000000, 'x')";
    for src in [
        format!("{big} + {big}"),
        "join(sequence(600), pad('', 1000000, 'x'))".to_string(),
        "replace(pad('', 100000, 'x'), pad('', 100000, 'x'), pad('', 400000, '$&'))".to_string(),
        "timeFormat(0, pad('', 26000000, '%c'))".to_string(),
        "encodeURIComponent(pad('', 100000000, '\u{e9}'))".to_string(),
        "btoa(pad('', 410000000, 'x'))".to_string(),
    ] {
        assert!(too_long(&src) || was_declined(&src), "{src}");
    }
}

/// A tiny xorshift: the fuzz must replay the same expressions on every run.
struct Dice(u64);

impl Dice {
    fn below(&mut self, n: usize) -> usize {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        (self.0 % n as u64) as usize
    }

    fn pick<'a>(&mut self, items: &[&'a str]) -> &'a str {
        items[self.below(items.len())]
    }

    fn expr(&mut self, depth: usize, names: &[&str]) -> String {
        const LEAVES: &[&str] = &[
            "0", "1", "-1", "0.5", "2", "3", "10", "100", "1e9", "1e300", "4294967296", "-1e300", "(0/0)", "(1/0)", "(-1/0)", "''", "'a'", "'abc'",
            "'2020-01-05'", "'%Y-%m-%d'", "',.2f'", "'x'", "'y'", "'b'", "'t'", "'nope'", "'left'", "'center'", "[]", "[1]", "[1,2]", "[1,2,3]",
            "['a','b']", "[[1,2],[3]]", "{}", "{a:1}", "/a+/", "/(a|b)*c/g", "/(/", "null", "true", "false", "datum.label", "datum.value",
            "datum.index", "datum.nope", "item.x", "item.align", "item.mark.name", "item.datum.label", "datum",
            "pad('', 3000, 'x')", "pad('', 2000, '$&')", "'$&$`$\\''", "'%c%c%c'", "pad('', 3000, '%c')", "replace('abc', 'b', '$&$&$&')",
            "pad('', 150000, 'x')", "pad('', 100000, '\u{4e00}')", "pad('', 5000, 'a')", "pad('', 3000, '\\\\b')", "sequence(150000)", "split(pad('', 150000, 'a'), '')", "timeSequence('milliseconds', 0, 150000)", "toString(sequence(100000))", "regexp(pad('', 5000, '(a)'))",
            "regexp(pad('', 4001, 'a'))", "'(?:a{1000}){1000}'",
        ];
        const OPS: &[&str] = &["+", "-", "*", "/", "%", "==", "===", "!=", "<", ">=", "&&", "||", "|", "&", "^", "<<", ">>", ">>>", " in "];
        if depth == 0 {
            return self.pick(LEAVES).to_string();
        }
        match self.below(8) {
            0 | 1 | 2 | 3 => {
                let name = names[self.below(names.len())];
                let args: Vec<String> = (0..self.below(5)).map(|_| self.expr(depth - 1, names)).collect();
                format!("{name}({})", args.join(","))
            }
            4 => format!("({}{}{})", self.expr(depth - 1, names), self.pick(OPS), self.expr(depth - 1, names)),
            5 => format!("({}?{}:{})", self.expr(depth - 1, names), self.expr(depth - 1, names), self.expr(depth - 1, names)),
            6 => format!("{}{}", self.pick(&["!", "-", "+", "~"]), self.expr(depth - 1, names)),
            _ => format!("{}[{}]", self.expr(depth - 1, names), self.expr(depth - 1, names)),
        }
    }
}

/// Counts the bytes the running thread holds, and past a thread's budget aborts: an expression that allocates
/// without bound must fail the test run, not wait for the OS to kill it.
struct Budgeted;

thread_local! {
    static HELD: std::cell::Cell<isize> = const { std::cell::Cell::new(0) };
    static BUDGET: std::cell::Cell<isize> = const { std::cell::Cell::new(isize::MAX) };
    /// The most this thread has held since [`reset_peak`].
    static PEAK: std::cell::Cell<isize> = const { std::cell::Cell::new(0) };
}

/// Starts measuring: the bytes the thread holds now are the baseline.
fn reset_peak() -> isize {
    let base = HELD.with(std::cell::Cell::get);
    PEAK.with(|p| p.set(base));
    base
}

unsafe impl std::alloc::GlobalAlloc for Budgeted {
    unsafe fn alloc(&self, layout: std::alloc::Layout) -> *mut u8 {
        let held = HELD.with(|h| {
            h.set(h.get() + layout.size() as isize);
            h.get()
        });
        PEAK.with(|p| p.set(p.get().max(held)));
        if held > BUDGET.with(std::cell::Cell::get) {
            return std::ptr::null_mut();
        }
        unsafe { std::alloc::System.alloc(layout) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: std::alloc::Layout) {
        HELD.with(|h| h.set(h.get() - layout.size() as isize));
        unsafe { std::alloc::System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static ALLOCATOR: Budgeted = Budgeted;

/// What one fuzz case may hold at once, and the stack it runs on.
const CASE_MEMORY: isize = 2 << 30;
const CASE_STACK: usize = 1 << 20;

#[test]
fn random_expressions_never_panic_or_exhaust_memory_or_stack() {
    let names: Vec<&str> = TABLE.iter().map(|(name, _)| *name).collect();
    let mut dice = Dice(0xD1B5_4A32_D192_ED03);
    let (mut parsed, mut ran) = (0, 0);
    for round in 0..1_000 {
        let src = dice.expr(1 + round % 5, &names);
        let case = src.clone();
        let outcome = std::thread::Builder::new()
            .stack_size(CASE_STACK)
            .spawn(move || {
                BUDGET.with(|b| b.set(CASE_MEMORY));
                let _small = super::val::small_limits(200_000, 8 << 20);
                let Ok(expr) = Expr::parse(&case) else { return (0, 0) };
                let mut ran = 0;
                for label in ["", "abc", "2020-01-05"] {
                    let tick = Tick { value: Val::Num(3.0), label, index: 0.5 };
                    ran += usize::from(with_view(|| expr.eval_lines(&tick)).is_ok());
                    ran += usize::from(expr.eval_lines(&tick).is_ok());
                }
                (1, ran)
            })
            .expect("a thread starts")
            .join();
        let (p, r) = outcome.unwrap_or_else(|_| panic!("panicked on: {src}"));
        (parsed, ran) = (parsed + p, ran + r);
    }
    // The generator must reach the evaluator, or it fuzzes the parser alone.
    assert!(parsed > 75 && ran > 75, "only {parsed} parsed and {ran} ran ");
}

#[test]
fn a_regular_expression_too_large_to_compile_is_refused_not_built() {
    for src in ["test(regexp(pad('', 4001, 'a')), 'a')", "test(regexp('(?:a{1000}){1000}'), 'a')", "test(regexp(pad('', 2000000, '\\\\b')), 'a')"] {
        assert!(matches!(lines(src, Val::Null, "", 0.0), Err(EvalError::Unmodeled(_))), "{src}");
    }
    assert_eq!(text("test(regexp(pad('', 3000, 'a')), pad('', 3000, 'a')) + ''", ""), "true");
}

/// Every function the table lists, called with the largest inputs the small limits let through (a text of a
/// budget's third, CJK text that takes three bytes a unit, `$&` templates, wide formats, long separators, large
/// sequences), must stay under three budgets of memory in one evaluation. A builtin added to the table is covered
/// without a line here.
#[test]
fn no_builtin_holds_more_than_three_budgets_at_its_peak() {
    const BUDGET: usize = 4 << 20;
    const POOL: &[&str] = &[
        "pad('', 524288, 'a')", "pad('', 1398101, 'a')", "pad('', 3774873, 'a')", "pad('', 699050, '\u{4e00}')", "pad('', 1300000, '\u{4e00}')",
        "pad('', 4000, '$&')", "pad('', 130, '\u{4e00}')", "pad('', 3000, '%c')", "pad('', 800000, '\u{390}')", "sequence(1000000)", "sequence(60000)",
        "timeSequence('milliseconds', 0, 400000)", "0", "1", "-1", "2", "1e9", "'0530000000'", "'>9000000'", "'.99f'", "'%c'", "''", "'a'", "','", "'$&$`$\\''",
        "/x/g", "/a/", "/a*/g", "regexp(pad('', 3000, 'a'))", "[]", "{}", "datum.label", "'\u{4e00}'", "[1, 2, 3]", "'x'",
    ];
    let names: Vec<&str> = TABLE.iter().map(|(name, _)| *name).collect();
    let mut dice = Dice(0x0BAD_5EED_1234_5678);
    let mut offenders: Vec<String> = Vec::new();
    let mut worst = (0isize, String::new());
    // The review's repros, at the small limits' scale.
    let repros: Vec<String> = [
        "length(substring(pad('', 3774873, 'a'), 1))", "length(slice(pad('', 3774873, 'a'), 1))",
        "length(replace(pad('', 100000, 'x'), /x/g, pad('', 4000, '\u{4e00}')))", "length(truncate(pad('', 3774873, 'a'), 3700000))",
        "length(format(1, '0530000000'))", "length(format(1, '0003774873'))", "length(join(sequence(2000000), pad('', 130, '\u{4e00}')))",
        "length(pad('', 1300000, '\u{4e00}'))", "length(encodeURIComponent(pad('', 699050, '\u{4e00}')))", "timeFormat(0, pad('', 400000, '%c'))",
        "length(split(pad('', 2000000, 'a'), ''))", "length([split(pad('', 500000, 'a'), ''), split(pad('', 500000, 'a'), ''), split(pad('', 500000, 'a'), ''), split(pad('', 500000, 'a'), '')])",
        "length(replace(pad('', 1000, 'x'), pad('', 1000, 'x'), pad('', 4000, '$&')))", "length(toString(timeSequence('milliseconds', 0, 400000)))",
        "length(pad('', 3774873, 'a') + pad('', 3774873, 'a'))", "length(pluck(sequence(60000), 'a'))", "length(merge(pad('', 600000, 'a')))", "length(upper(pad('', 800000, '\u{390}')))", "length(btoa(pad('', 3774873, 'a')))",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    // Three pool entries a builtin, rotated so the builtins together meet every entry.
    for (n, name) in names.into_iter().enumerate() {
        let mut cases: Vec<String> = (0..3).map(|k| format!("{name}({})", POOL[(n * 3 + k) % POOL.len()])).collect();
        for _ in 0..3 {
            cases.push(format!("{name}({},{})", POOL[dice.below(POOL.len())], POOL[dice.below(POOL.len())]));
        }
        cases.push(format!("{name}({},{},{})", POOL[dice.below(POOL.len())], POOL[dice.below(POOL.len())], POOL[dice.below(POOL.len())]));
        if name == "length" {
            cases.extend(repros.iter().cloned());
        }
        for src in cases {
            let case = src.clone();
            let peak = std::thread::Builder::new()
                .stack_size(CASE_STACK)
                .spawn(move || {
                    let _small = super::val::small_limits(8 << 20, BUDGET);
                    let Ok(expr) = Expr::parse(&case) else { return 0 };
                    let tick = Tick { value: Val::Num(3.0), label: "abc", index: 0.5 };
                    let base = reset_peak();
                    let _ = with_view(|| expr.eval_lines(&tick));
                    PEAK.with(std::cell::Cell::get) - base
                })
                .expect("a thread starts")
                .join()
                .unwrap_or_else(|_| panic!("panicked on: {src}"));
            if peak > worst.0 {
                worst = (peak, src.clone());
            }
            if peak as usize > 3 * BUDGET {
                offenders.push(format!("{} MiB: {src}", peak >> 20));
            }
        }
    }
    eprintln!("highest peak: {} KiB of {} KiB allowed: {}", worst.0 >> 10, (3 * BUDGET) >> 10, worst.1);
    assert!(offenders.is_empty(), "{} calls held more than two budgets:\n{}", offenders.len(), offenders.iter().take(40).cloned().collect::<Vec<_>>().join("\n"));
}

/// One value of each shape an argument can take, as large as a budget lets one value be, and the small values that
/// name a key, a field, a count or a format into them.
fn shapes(budget: usize, scale: usize) -> (Vec<(&'static str, Val)>, Vec<(&'static str, Val)>) {
    let budget = budget / scale;
    let text = |s: String| Val::string(s).unwrap();
    let element = super::val::ELEMENT_BYTES;
    let numbers_of = |weight: usize| Val::Array((0..weight / element).map(|i| Val::Num(i as f64)).collect());
    let numbers = |share: usize| numbers_of(budget / share);
    let record = |i: usize| Val::tuple(vec![("a".into(), Val::Num(i as f64)), ("b".into(), text(format!("row {i}"))), ("c".into(), Val::Array(vec![Val::Num(1.0), Val::Num(2.0)]))]);
    let big = vec![
        ("numbers 1/2", numbers(2)),
        ("numbers 1/8", numbers(8)),
        ("dates", Val::Array((0..budget / 4 / 80).map(|i| Val::Date(i as f64 * 1e3)).collect())),
        ("records", Val::Array((0..budget / 2 / 800).map(record).collect())),
        ("strings", Val::Array((0..budget / 4 / 100).map(|i| text(format!("item {i}"))).collect())),
        ("nested", Val::Array((0..budget / 2 / 400).map(|i| Val::Array(vec![Val::Num(i as f64); 4])).collect())),
        ("long text", text("a".repeat(budget / 3))),
        ("long CJK text", text("\u{4e00}".repeat(budget / 8))),
        ("long csv", text("a,".repeat(budget / 12))),
        ("object", Val::Object((0..budget / 2 / 250).map(|i| (format!("k{i}"), Val::Num(i as f64))).collect())),
        // Just under what a builtin's reservation for its output allows, so the cost of each entry decides.
        ("array just under", numbers_of(budget * 45 / 100)),
        ("text just under", text("a".repeat(budget / 66))),
        ("CJK text just under", text("\u{4e00}".repeat(budget / 66))),
        ("object just under", Val::Object((0..budget * 45 / 100 / 124).map(|i| (format!("k{i}"), Val::Num(i as f64))).collect())),
        ("object of arrays", Val::Object((0..budget / 2 / 600).map(|i| (format!("k{i}"), Val::Array(vec![Val::Num(1.0); 4]))).collect())),
    ];
    let small = vec![
        ("0", Val::Num(0.0)), ("1", Val::Num(1.0)), ("-1", Val::Num(-1.0)), ("2.5", Val::Num(2.5)), ("1e9", Val::Num(1e9)), ("NaN", Val::Num(f64::NAN)),
        ("'a'", text("a".into())), ("'0'", text("0".into())), ("'length'", text("length".into())), ("'a.b'", text("a.b".into())), ("'k3'", text("k3".into())),
        ("''", text(String::new())), ("','", text(",".into())), ("'$&'", text("$&".into())), ("'%c'", text("%c".into())), ("'>9000000'", text(">9000000".into())),
        ("'.99f'", text(".99f".into())), ("['a','b']", Val::Array(vec![text("a".into()), text("b".into())])), ("null", Val::Null), ("undefined", Val::Undefined),
        ("true", Val::Bool(true)), ("date", Val::Date(0.0)), ("/a/g", Val::Regex("a".into(), "g".into())), ("{a: 1}", Val::tuple(vec![("a".into(), Val::Num(1.0))])),
        ("'seconds'", text("seconds".into())), ("'x'", text("x".into())),
    ];
    (big, small)
}

/// Every function the table lists, called directly with each large argument shape a script can pass: alone, with
/// three small shapes in either position, with three other large shapes, and in one triple. The picks rotate so the
/// large shapes together meet every small one. The shapes, not a list of calls, decide the cases, so a builtin that
/// allocates per element of one of them is met whichever it is.
#[test]
fn every_builtin_stays_within_three_budgets_for_every_argument_shape() {
    const LIMIT: usize = 1 << 20;
    // `if` is the one name the parser compiles and `call` never sees.
    let names: Vec<&str> = TABLE.iter().map(|(name, _)| *name).filter(|name| *name != "if").collect();
    let mut offenders: Vec<String> = Vec::new();
    let mut worst = (0isize, String::new());
    for name in names {
        let outcome = std::thread::Builder::new()
            .stack_size(CASE_STACK * 4)
            .spawn(move || {
                BUDGET.with(|b| b.set(CASE_MEMORY));
                let _small = super::val::small_limits(8 << 20, LIMIT);
                let small = shapes(LIMIT, 1).1;
                // Each shape at the largest value a budget allows, where a reservation refuses it, and a sixteenth
                // of that, where it passes the reservations and the per-element costs decide.
                let bigs = [shapes(LIMIT, 1).0, shapes(LIMIT, 16).0];
                let mut calls: Vec<Vec<&(&str, Val)>> = small.iter().map(|s| vec![s]).collect();
                for big in &bigs {
                    for (i, b) in big.iter().enumerate() {
                        calls.push(vec![b]);
                        for k in 0..3 {
                            let s = &small[(i * 3 + k) % small.len()];
                            calls.push(vec![b, s]);
                            calls.push(vec![s, b]);
                            calls.push(vec![b, &big[(i + 1 + k * 5) % big.len()]]);
                        }
                        let (s, t) = (&small[(i * 7) % small.len()], &small[(i * 7 + 13) % small.len()]);
                        calls.push(if i % 2 == 0 { vec![b, s, t] } else { vec![s, b, t] });
                    }
                }
                let mut found: Vec<(isize, String)> = Vec::new();
                for call in calls {
                    let vals: Vec<Val> = call.iter().map(|(_, v)| v.clone()).collect();
                    let label = format!("{name}({})", call.iter().map(|(n, _)| *n).collect::<Vec<_>>().join(", "));
                    let measure = || {
                        let base = reset_peak();
                        // What the evaluator does: the arguments are node values, charged before the call.
                        let _evaluation = super::val::Evaluation::begin();
                        let charged = vals.iter().try_for_each(|v| super::val::charge(v.weight()));
                        if charged.is_ok() {
                            let _ = with_view(|| funcs::call(name, &vals, &Val::tuple(vec![("label".into(), Val::string("abc".into()).unwrap())])).and_then(|out| super::val::charge(out.weight())));
                        }
                        PEAK.with(std::cell::Cell::get) - base
                    };
                    // A call that is over the bar runs once more: the first run of a library may fill a cache it
                    // keeps, which is not what one evaluation holds.
                    let first = measure();
                    found.push((if first as usize > 2 * LIMIT { measure() } else { first }, label));
                }
                found
            })
            .expect("a thread starts")
            .join();
        match outcome {
            Err(_) => offenders.push(format!("panicked in {name}")),
            Ok(found) => {
                for (peak, label) in found {
                    if peak > worst.0 {
                        worst = (peak, label.clone());
                    }
                    if peak as usize > 2 * LIMIT {
                        offenders.push(format!("{} MiB: {label}", peak >> 20));
                    }
                }
            }
        }
    }
    eprintln!("highest peak: {} KiB of {} KiB allowed: {}", worst.0 >> 10, (2 * LIMIT) >> 10, worst.1);
    assert!(offenders.is_empty(), "{} calls held more than two budgets:\n{}", offenders.len(), offenders.iter().take(40).cloned().collect::<Vec<_>>().join("\n"));
}

/// Calls a builtin the way the evaluator does: the arguments are node values, charged before the call.
fn call_charged(name: &str, args: &[Val]) -> Result<Val, EvalError> {
    let _evaluation = super::val::Evaluation::begin();
    args.iter().try_for_each(|v| super::val::charge(v.weight()))?;
    funcs::call(name, args, &Val::Null)
}

/// An input that passes a builtin's reservation for its output but costs more than the budget once each entry is
/// charged. The reservation alone would let it through; only the per-entry charge refuses it.
#[test]
fn merge_of_a_string_charges_each_entry_it_builds() {
    let _small = super::val::small_limits(1_000_000, 8 << 20);
    let text = Val::string("a".repeat(60_000)).unwrap();
    assert!(matches!(call_charged("merge", &[text]), Err(EvalError::Unmodeled(_))));
}

#[test]
fn pluck_of_an_array_charges_each_value_it_reads() {
    let _small = super::val::small_limits(1_000_000, 8 << 20);
    let items = Val::Array((0..60_000).map(|i| Val::Num(f64::from(i))).collect());
    for key in [Val::string("a".into()).unwrap(), Val::Array(vec![Val::string("a".into()).unwrap(), Val::string("b".into()).unwrap()])] {
        assert!(matches!(call_charged("pluck", &[items.clone(), key]), Err(EvalError::Unmodeled(_))));
    }
}
