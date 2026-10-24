//! A query cell as Vega reads it. Production hands Vega-Lite the cells after Python's
//! `normalize_data_types`, which keeps a list and stringifies a mapping with `str()`; Vega
//! then coerces and orders them with JavaScript's rules.

use serde_json::{Map, Value};

use crate::common::js_string_to_number;
use crate::families::color_scale::js_number_string;

/// `normalize_data_types`: a mapping cell is Python's `str(value)`; a list keeps its elements as they are.
pub(super) fn normalized(row: &Map<String, Value>) -> Map<String, Value> {
    row.iter()
        .map(|(key, cell)| (key.clone(), if cell.is_object() { Value::String(python_repr(cell)) } else { cell.clone() }))
        .collect()
}

/// Python's `repr` of a JSON value.
fn python_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Number(n) if n.is_f64() => d3rs::py_float::float_repr(n.as_f64().expect("is_f64")),
        Value::Number(n) => n.to_string(),
        Value::String(s) => python_string_repr(s),
        Value::Array(items) => format!("[{}]", items.iter().map(python_repr).collect::<Vec<_>>().join(", ")),
        Value::Object(map) => {
            format!("{{{}}}", map.iter().map(|(k, v)| format!("{}: {}", python_string_repr(k), python_repr(v))).collect::<Vec<_>>().join(", "))
        }
    }
}

/// Python's `repr(str)`: single quotes unless the text holds one and no double quote.
fn python_string_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if !py_printable(c) => out.push_str(&match c as u32 {
                n @ 0..=0xff => format!("\\x{n:02x}"),
                n @ 0..=0xffff => format!("\\u{n:04x}"),
                n => format!("\\U{n:08x}"),
            }),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Production's `chart_has_nonzero_measure` for one cell: a measure that is not `None` or `0`.
pub(super) fn has_ink(cell: Option<&Value>) -> bool {
    match cell {
        None | Some(Value::Null) | Some(Value::Bool(false)) => false,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(_) => true,
    }
}

/// Python's `str.isprintable` for one character: not a control, a space other than ` `, a format
/// character or a private-use one. Unassigned code points are not recognized.
fn py_printable(c: char) -> bool {
    let format = matches!(c as u32,
        0xad | 0x600..=0x605 | 0x61c | 0x6dd | 0x70f | 0x890..=0x891 | 0x8e2 | 0x180e | 0x200b..=0x200f
        | 0x202a..=0x202e | 0x2060..=0x2064 | 0x2066..=0x206f | 0xfeff | 0xfff9..=0xfffb | 0xe0001 | 0xe0020..=0xe007f);
    let private = matches!(c as u32, 0xe000..=0xf8ff | 0xf0000..=0xffffd | 0x100000..=0x10fffd);
    !(c.is_control() || (c.is_whitespace() && c != ' ') || format || private)
}

/// JavaScript's `String(cell)`: a list joins its elements with commas, a null element empty.
pub(super) fn string(cell: &Value) -> String {
    match cell {
        Value::Null => "null".into(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => js_number_string(n.as_f64().expect("a JSON number is an f64")),
        Value::String(s) => s.clone(),
        Value::Array(items) => items.iter().map(element).collect::<Vec<_>>().join(","),
        Value::Object(_) => "[object Object]".into(),
    }
}

fn element(item: &Value) -> String {
    if item.is_null() { String::new() } else { string(item) }
}

/// The text Vega-Lite's description gives a list cell: its elements joined with a space.
pub(super) fn description(cell: &Value) -> String {
    match cell {
        Value::Array(items) => items.iter().map(element).collect::<Vec<_>>().join(" "),
        other => string(other),
    }
}

/// JavaScript's `+cell`; a missing cell is `undefined`.
pub(super) fn number(cell: Option<&Value>) -> f64 {
    match cell {
        None => f64::NAN,
        Some(Value::Null) => 0.0,
        Some(Value::Bool(b)) => f64::from(u8::from(*b)),
        Some(Value::Number(n)) => n.as_f64().expect("a JSON number is an f64"),
        Some(other) => js_string_to_number(&string(other)).unwrap_or(f64::NAN),
    }
}

/// Whether a scale keyed by `a` finds `b`: the same kind of value and equal (a text `7` is not a number 7).
pub(super) fn same_value(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(a), Value::Number(b)) => a.as_f64() == b.as_f64(),
        (a, b) => a == b,
    }
}

/// The operand of a relational comparison after `ToPrimitive`: a list is its string.
fn primitive(cell: &Value) -> Value {
    if cell.is_array() { Value::String(string(cell)) } else { cell.clone() }
}

/// JavaScript's `a < b` for primitives: two strings compare by UTF-16 code unit, anything else as numbers.
fn less(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::String(a), Value::String(b)) => a.encode_utf16().lt(b.encode_utf16()),
        (a, b) => number(Some(a)) < number(Some(b)),
    }
}

/// vega-util's `ascending`: a null sorts first, and two values JavaScript cannot order (a text against
/// a number it does not read as) are equal.
fn ascending(u: &Value, v: &Value) -> i8 {
    let (u, v) = (primitive(u), primitive(v));
    if (less(&u, &v) || u.is_null()) && !v.is_null() {
        -1
    } else if (less(&v, &u) || v.is_null()) && !u.is_null() {
        1
    } else {
        0
    }
}

/// `domain` in the order Vega's `sort: true` leaves it. `Array.prototype.sort` is V8's TimSort, which for
/// fewer than 64 elements is one run extended by binary insertion; on values `ascending` does not order
/// consistently (text beside numbers) that order is the algorithm's, so it is replayed. A mixed list of
/// 64 or more would need the merge phase.
pub(super) fn sort_domain(domain: &mut Vec<Value>) -> Result<(), String> {
    let mixed = domain.iter().filter(|v| !v.is_null()).any(|v| v.is_string())
        && domain.iter().filter(|v| !v.is_null()).any(|v| !v.is_string());
    if domain.len() >= 64 {
        if mixed {
            return Err(crate::refuse!("a point map nominal color mixing text and numbers over 63 values is not supported"));
        }
        domain.sort_by(|a, b| ascending(a, b).cmp(&0));
        return Ok(());
    }
    let n = domain.len();
    if n < 2 {
        return Ok(());
    }
    // V8's `CountAndMakeRun`.
    let descending = ascending(&domain[1], &domain[0]) < 0;
    let mut run = 2;
    for i in 2..n {
        let order = ascending(&domain[i], &domain[i - 1]);
        if (descending && order >= 0) || (!descending && order < 0) {
            break;
        }
        run += 1;
    }
    if descending {
        domain[..run].reverse();
    }
    // `BinaryInsertionSort` over the rest.
    for start in run..n {
        let pivot = domain[start].clone();
        let (mut left, mut right) = (0, start);
        while left < right {
            let mid = left + ((right - left) >> 1);
            if ascending(&pivot, &domain[mid]) < 0 {
                right = mid;
            } else {
                left = mid + 1;
            }
        }
        domain[left..=start].rotate_right(1);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn a_mapping_cell_is_pythons_repr() {
        let row = json!({"a": {"b": [1, 2.5, null, true], "c": "it's"}}).as_object().unwrap().clone();
        assert_eq!(normalized(&row)["a"], json!("{'b': [1, 2.5, None, True], 'c': \"it's\"}"));
    }

    #[test]
    fn python_escapes_what_it_cannot_print() {
        let row = json!({"a": {"k": "x\u{a0}y\u{200b}\u{2028}\u{1}\u{e000}\u{e0001}é"}}).as_object().unwrap().clone();
        assert_eq!(normalized(&row)["a"], json!("{'k': 'x\\xa0y\\u200b\\u2028\\x01\\ue000\\U000e0001é'}"));
    }

    #[test]
    fn a_list_reads_as_javascript_does() {
        let list = json!([1, [2, 3], null, "a"]);
        assert_eq!(string(&list), "1,2,3,,a");
        assert_eq!(description(&list), "1 2,3  a");
        assert!(number(Some(&json!([1, 2]))).is_nan());
        assert_eq!(number(Some(&json!([5]))), 5.0);
        assert_eq!(number(Some(&json!([]))), 0.0);
        assert!(number(None).is_nan());
    }

    #[test]
    fn a_domain_of_64_values_sorts_unless_it_mixes_text_and_numbers() {
        let mut texts: Vec<Value> = (0..64).rev().map(|i| json!(format!("t{i:02}"))).collect();
        sort_domain(&mut texts).unwrap();
        assert_eq!(texts[0], json!("t00"));
        assert_eq!(texts[63], json!("t63"));
        let mut numbers: Vec<Value> = (0..64).rev().map(|i| json!(i)).collect();
        sort_domain(&mut numbers).unwrap();
        assert_eq!(numbers[0], json!(0));
        let mut mixed: Vec<Value> = (0..63).map(|i| json!(i)).chain([json!("a")]).collect();
        assert!(sort_domain(&mut mixed).unwrap_err().ends_with("not supported"));
        let mut mixed_below: Vec<Value> = (0..62).map(|i| json!(i)).chain([json!("a")]).collect();
        sort_domain(&mut mixed_below).unwrap();
    }

    #[test]
    fn a_domain_sorts_as_v8_leaves_values_it_cannot_order() {
        let mut mixed = vec![json!("B"), json!("A"), json!(7), json!(3), json!(10), json!("C")];
        sort_domain(&mut mixed).unwrap();
        assert_eq!(mixed, vec![json!("A"), json!("B"), json!(3), json!(7), json!(10), json!("C")]);
        let mut with_null = vec![json!("b"), json!(null), json!("a")];
        sort_domain(&mut with_null).unwrap();
        assert_eq!(with_null, vec![json!(null), json!("a"), json!("b")]);
    }
}
