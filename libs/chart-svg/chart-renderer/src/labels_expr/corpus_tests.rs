//! Differential tests: every case in `corpus/corpus.json` is an expression Vega itself ran on real axes
//! (`corpus/corpus.mjs`, over the expressions in `corpus/exprs.json`), recorded with the ticks it saw and the lines
//! it painted, or the way it failed.

use super::{EvalError, Expr, Tick, Val};
use serde_json::Value;

const KINDS: [&str; 3] = ["linear", "time", "point"];

/// A case's `o` is three per-axis outcomes (not one outcome that is itself a list of per-tick lines) when every
/// element is an outcome: a string, a `{"c": ..}` object, or a list of per-tick line lists.
fn is_triple(o: &Value) -> bool {
    let Some(items) = o.as_array() else { return false };
    items.len() == 3
        && items.iter().all(|x| {
            x.is_string()
                || x.get("c").is_some()
                || x.as_array().is_some_and(|ticks| ticks.iter().all(|t| t.as_array().is_some_and(|ls| ls.iter().all(Value::is_string))))
        })
        && !items.iter().all(|x| x.as_array().is_some_and(|l| l.iter().all(Value::is_string)))
}

/// Last-digit differences the platform's math library causes: V8's fdlibm is compiled with fused multiply-adds on
/// some machines, so transcendental results agree only to rounding.
const TRANSCENDENTAL: [&str; 40] = [
    "acos", "asin", "atan", "atan2", "cos", "exp", "log", "sin", "tan", "pow", "hypot", "sqrt", "cumulativeNormal", "cumulativeLogNormal",
    "densityNormal", "densityLogNormal", "quantileNormal", "quantileLogNormal", "panLinear", "panLog", "panPow", "panSymlog",
    "zoomLinear", "zoomLog", "zoomPow", "zoomSymlog", "sampleNormal", "sampleLogNormal", "lab", "hcl", "hsl", "rgb", "luminance",
    "contrast", "scale", "invert", "gradient", "geoArea", "geoBounds", "geoCentroid",
];

/// Whether `src` calls the function `name`: `name(` not part of a longer identifier or member.
fn calls(src: &str, name: &str) -> bool {
    src.match_indices(&format!("{name}(")).any(|(at, _)| {
        !src[..at].chars().next_back().is_some_and(|c| c.is_alphanumeric() || c == '_' || c == '.' || c == '$')
    })
}

fn same_within_rounding(src: &str, got: &[String], want: &[&str]) -> bool {
    // A function prints its source, which is the bundle's own: unminified here, minified in vl-convert's.
    if got.len() == 1 && want.len() == 1 && got[0].starts_with("function") && want[0].starts_with("function") {
        return true;
    }
    TRANSCENDENTAL.iter().any(|f| calls(src, f))
        && got.len() == want.len()
        && got.iter().zip(want).all(|(g, w)| match (g.parse::<f64>(), w.parse::<f64>()) {
            (Ok(g), Ok(w)) => (g - w).abs() <= 1e-12 * w.abs().max(1.0),
            _ => g == w,
        })
}

/// What `corpus.mjs` gave every axis: the scale `x` of its kind on a 300px plot (`width` 300, `height` 50), one
/// scale of each other type, and the chart's source.
fn open_view(kind: &str) -> super::view::Guard {
    use super::scales::Kind;
    use super::view::{register, register_linear, register_utc, ScaleSpec, Span};
    let open = super::view::enter();
    super::view::set_plot(300.0, 50.0);
    let strings = |items: &[&str]| items.iter().map(|s| Val::string((*s).to_string()).unwrap()).collect::<Vec<_>>();
    let nums = |lo: f64, hi: f64| vec![Val::Num(lo), Val::Num(hi)];
    match kind {
        "linear" => register_linear("x", -5.0, 123456.789, Span::Width),
        "time" => register_utc("x", 1_577_836_800_000.0, 1_590_969_600_000.0, Span::Width),
        _ => register(
            "x",
            ScaleSpec {
                kind: Kind::Point { padding_outer: 0.0 },
                domain: strings(&["a", "bb", "Zürich", "", " x ", "2020-03-01", "10", "0"]),
                span: Span::Width,
            },
        ),
    }
    let spec = |kind, domain, span| ScaleSpec { kind, domain, span };
    let band = |padding_inner, padding_outer| Kind::Band { padding_inner, padding_outer };
    let abcd = || strings(&["a", "b", "c", "d"]);
    register("y", spec(Kind::Linear, nums(0.0, 50.0), Span::Height));
    register("l", spec(Kind::Log, nums(1.0, 1000.0), Span::Width));
    register("ln", spec(Kind::Log, nums(-1000.0, -1.0), Span::Width));
    register("p", spec(Kind::Pow(2.0), nums(0.0, 10.0), Span::Width));
    register("q", spec(Kind::Pow(0.5), nums(0.0, 100.0), Span::Width));
    register("s", spec(Kind::Symlog(1.0), nums(-100.0, 100.0), Span::Width));
    register("u", spec(Kind::Utc, vec![Val::Date(1_577_836_800_000.0), Val::Date(1_609_459_200_000.0)], Span::Width));
    register("b", spec(band(0.2, 0.1), abcd(), Span::Width));
    register("br", spec(band(0.3, 0.3), abcd(), Span::Fixed(nums(300.0, 0.0))));
    register("pt", spec(Kind::Point { padding_outer: 0.5 }, strings(&["a", "b", "c"]), Span::Width));
    register("o", spec(Kind::Ordinal, strings(&["a", "b", "c"]), Span::Fixed(strings(&["red", "#00ff00", "blue"]))));
    let source = serde_json::json!([{"a": 1, "b": "x", "c": null}, {"a": 2, "b": "y", "c": 3}, {"a": 2, "b": "z", "c": "s"}]);
    let rows: Vec<_> = source.as_array().unwrap().iter().map(|r| r.as_object().unwrap().clone()).collect();
    super::view::set_rows(&rows).unwrap();
    open
}

fn tick_value(v: &Value) -> Val {
    let raw = &v["v"];
    match v["t"].as_str().unwrap() {
        "num" => Val::Num(match raw {
            Value::String(s) => match s.as_str() {
                "NaN" => f64::NAN,
                "Infinity" => f64::INFINITY,
                _ => f64::NEG_INFINITY,
            },
            n => n.as_f64().unwrap(),
        }),
        "date" => Val::Date(raw.as_f64().unwrap()),
        "str" => Val::string(raw.as_str().unwrap().to_string()).unwrap(),
        other => panic!("tick value type {other}"),
    }
}

#[test]
fn every_expression_vega_ran_paints_what_vega_painted() {
    // `LABELS_EXPR_CORPUS=<file>` replays a corpus built by `corpus/build_corpus.py` instead of the committed one.
    let texts = match std::env::var("LABELS_EXPR_CORPUS") {
        Ok(path) => vec![std::fs::read_to_string(path).unwrap()],
        Err(_) => vec![include_str!("corpus/corpus.json").to_string(), include_str!("corpus/corpus_view.json").to_string()],
    };
    let (mut mismatches, mut checked, mut unmodeled) = (Vec::new(), 0, 0);
    for text in &texts {
        replay(text, &mut mismatches, &mut checked, &mut unmodeled);
    }
    assert!(unmodeled <= UNMODELED_OUTCOMES, "{unmodeled} outcomes were refused rather than compared (at most {UNMODELED_OUTCOMES} are known to be)");
    let shown: Vec<String> = mismatches.iter().take(400).cloned().collect();
    assert!(mismatches.is_empty(), "{} of {checked} outcomes differ from Vega:\n{}", mismatches.len(), shown.join("\n"));
}

/// The outcomes the replay may count as agreeing because this engine refuses the expression by name (what reads the
/// scenegraph, `item.mark.group`, a selection's rows, an axis nobody laid out): a growing count is a regression, not
/// an agreement.
const UNMODELED_OUTCOMES: usize = 39;

fn replay(text: &str, mismatches: &mut Vec<String>, checked: &mut usize, unmodeled: &mut usize) {
    let _locale = crate::time_locale::en_us_scope();
    let corpus: Value = serde_json::from_str(text).unwrap();
    let kinds = corpus["kinds"].as_object().unwrap();
    for case in corpus["cases"].as_array().unwrap() {
        let src = case["e"].as_str().unwrap();
        // Node's dataflow is idle where vl-convert's is running, so the calls that edit a dataset in it (they pulse
        // it) answer differently there than here; production is probed for them instead (`fixtures/diagnostics`).
        if src.starts_with("setdata(") || src.starts_with("modify(") {
            continue;
        }
        let parsed = Expr::parse(src);
        // One outcome for every axis, or one per axis kind in `KINDS` order.
        let per_kind: Vec<&Value> = match case["o"].as_array() {
            Some(outs) if outs.len() == 3 && outs.iter().all(|o| o.is_string() || o.is_object() || o.is_array()) && case["o"][0] != Value::Null && is_triple(&case["o"]) => outs.iter().collect(),
            _ => vec![&case["o"]; 3],
        };
        for (kind, outcome) in KINDS.iter().zip(per_kind) {
            *checked += 1;
            let _open = open_view(kind);
            let ticks = kinds[*kind].as_array().unwrap();
            match outcome.as_str() {
                Some("unknown") => {
                    let refused = parsed.as_ref().is_ok_and(|e| {
                        ticks.iter().any(|t| matches!(e.eval_lines(&Tick { value: tick_value(&t["value"]), label: t["label"].as_str().unwrap(), index: 0.0 }), Err(EvalError::Unmodeled(_) | EvalError::Unknown(_))))
                    });
                    *unmodeled += usize::from(refused && !matches!(parsed, Err(EvalError::Unknown(_))));
                    if !matches!(parsed, Err(EvalError::Unknown(_))) && !refused {
                        mismatches.push(format!("{src} [{kind}]: Vega's compiler rejects it; got {:?}", parsed.as_ref().map(|_| "parsed")));
                    }
                }
                Some("throws") => {
                    let Ok(expr) = &parsed else {
                        mismatches.push(format!("{src} [{kind}]: Vega throws at run time; we reject {parsed:?}"));
                        continue;
                    };
                    let threw = ticks.iter().any(|t| {
                        let label = t["label"].as_str().unwrap();
                        let tick = Tick { value: tick_value(&t["value"]), label, index: t["index"].as_f64().unwrap() };
                        matches!(expr.eval_lines(&tick), Err(EvalError::Throws(_) | EvalError::Unmodeled(_)))
                    });
                    if !threw {
                        mismatches.push(format!("{src} [{kind}]: Vega throws; we paint"));
                    }
                }
                Some(other) => panic!("outcome {other}"),
                None => {
                    let Ok(expr) = &parsed else {
                        mismatches.push(format!("{src} [{kind}]: Vega paints; we reject {parsed:?}"));
                        continue;
                    };
                    let want: Vec<&Value> = match outcome.get("c") {
                        Some(lines) => vec![lines; ticks.len()],
                        None => outcome.as_array().unwrap().iter().collect(),
                    };
                    for (t, w) in ticks.iter().zip(want) {
                        let label = t["label"].as_str().unwrap();
                        let tick = Tick { value: tick_value(&t["value"]), label, index: t["index"].as_f64().unwrap() };
                        let want_lines: Vec<&str> = w.as_array().unwrap().iter().map(|l| l.as_str().unwrap()).collect();
                        match expr.eval_lines(&tick) {
                            Ok(got) if got == want_lines => {}
                            Ok(got) if same_within_rounding(src, &got, &want_lines) => {}
                            Err(EvalError::Unmodeled(_)) => {
                                *unmodeled += 1;
                                break;
                            }
                            other => {
                                mismatches.push(format!("{src} [{kind}] value={:?} label={label:?} index={}: Vega {want_lines:?}, we {other:?}", tick.value, tick.index));
                                break;
                            }
                        }
                    }
                }
            }
        }
    }
}
