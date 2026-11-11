use serde_json::Value;

/// The text a restatement message shows for a leaf, as Python's `restated_diagnostic` makes it:
/// a string as is, a whole float as the integer, anything else as `json.dumps` (a float as `repr`).
pub fn restated_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) if n.is_f64() && n.as_f64().is_some_and(|f| f.fract() == 0.0) => {
            format!("{:.0}", n.as_f64().unwrap_or_default())
        }
        _ => dumps(v),
    }
}

/// `json.dumps`: `", "` and `": "` separators, non-ASCII escaped.
fn dumps(v: &Value) -> String {
    match v {
        Value::String(s) => {
            let mut out = String::new();
            for c in serde_json::to_string(s).unwrap_or_default().chars() {
                if c.is_ascii() {
                    out.push(c);
                } else {
                    for unit in c.encode_utf16(&mut [0; 2]) {
                        out.push_str(&format!("\\u{unit:04x}"));
                    }
                }
            }
            out
        }
        Value::Array(items) => {
            let items: Vec<String> = items.iter().map(dumps).collect();
            format!("[{}]", items.join(", "))
        }
        Value::Object(map) => {
            let items: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", dumps(&Value::String(k.clone())), dumps(v)))
                .collect();
            format!("{{{}}}", items.join(", "))
        }
        Value::Number(n) if n.is_f64() => {
            d3rs::py_float::float_repr(n.as_f64().unwrap_or_default())
        }
        _ => v.to_string(),
    }
}
