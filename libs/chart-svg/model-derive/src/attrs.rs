use proc_macro2::{Span, TokenStream};
use quote::quote;
use syn::ext::IdentExt;
use syn::parse::ParseStream;
use syn::{Attribute, Error, Expr, Ident, Lit, LitStr, Path, Result, Token, Type};

#[derive(Default, Clone)]
pub struct FieldAttrs {
    pub absent: Option<String>,
    /// `omit = ".."` on an `Option<T>`: the field may be left out; its twins are `T`'s own.
    pub omit: Option<String>,
    /// `default_empty`: a map, list or model left unset builds empty.
    pub default_empty: bool,
    pub merge: Option<String>,
    pub inherit: Option<String>,
    pub inherit_slot: Option<String>,
    pub links: Vec<(String, String)>,
    /// `link_at(to = "labels.font", from = "Style.charts.marks.text.font")`: a leaf or container
    /// below this field takes `from`, at this mount only.
    pub links_at: Vec<(String, String)>,
    /// `fill_at(to = "stroke.color", value = "auto")`: a leaf below this field holds `value` where
    /// nothing set it, at this mount only.
    pub fills_at: Vec<(String, TokenStream)>,
    pub local: bool,
    pub nested_drop: bool,
    pub nullable: bool,
    pub fill: Option<String>,
    /// `fill = "line"`, `fill = false`, `fill = 3`: a constant instead of a function.
    pub fill_value: Option<TokenStream>,
    pub accept: Option<Type>,
    pub directive: bool,
    pub keys: bool,
    pub flatten: bool,
    pub role: Option<String>,
    pub arm_pattern: bool,
    pub min_items: Option<u64>,
}

pub fn doc(attrs: &[Attribute]) -> String {
    let lines: Vec<String> = attrs
        .iter()
        .filter(|a| a.path().is_ident("doc"))
        .filter_map(|a| match &a.meta {
            syn::Meta::NameValue(nv) => match &nv.value {
                Expr::Lit(l) => match &l.lit {
                    Lit::Str(s) => Some(
                        s.value()
                            .strip_prefix(' ')
                            .unwrap_or(&s.value())
                            .trim_end()
                            .to_string(),
                    ),
                    _ => None,
                },
                _ => None,
            },
            _ => None,
        })
        .collect();
    lines.join("\n")
}

fn string(meta: &syn::meta::ParseNestedMeta) -> Result<String> {
    Ok(meta.value()?.parse::<LitStr>()?.value())
}

pub fn field(attrs: &[Attribute]) -> Result<FieldAttrs> {
    let mut a = FieldAttrs::default();
    for attr in attrs {
        if attr.path().is_ident("flatten") {
            attr.meta.require_path_only()?;
            a.flatten = true;
        } else if attr.path().is_ident("model") {
            attr.parse_nested_meta(|m| {
                let id = m.path.get_ident().map(Ident::to_string).unwrap_or_default();
                match id.as_str() {
                    "absent" => a.absent = Some(string(&m)?),
                    "omit" => a.omit = Some(string(&m)?),
                    "default_empty" => a.default_empty = true,
                    "inherit" => a.inherit = Some(string(&m)?),
                    "inherit_slot" => a.inherit_slot = Some(string(&m)?),
                    "min_items" => {
                        a.min_items = Some(m.value()?.parse::<syn::LitInt>()?.base10_parse()?);
                    }
                    "role" => {
                        let v = m.value()?.call(Ident::parse_any)?;
                        a.role = Some(v.to_string());
                    }
                    "merge" => {
                        let v = m.value()?.call(Ident::parse_any)?.to_string();
                        if !matches!(v.as_str(), "override" | "deep" | "by_key") {
                            return Err(m.error("merge is `override`, `deep` or `by_key`"));
                        }
                        a.merge = Some(v);
                    }
                    "nested" => {
                        let v: Ident = m.value()?.parse()?;
                        if v != "drop" {
                            return Err(syn::Error::new(v.span(), "only `nested = drop`"));
                        }
                        a.nested_drop = true;
                    }
                    "fill" => {
                        let v = m.value()?;
                        if let Ok(lit) = v.fork().parse::<Lit>() {
                            v.parse::<Lit>()?;
                            a.fill_value = Some(match lit {
                                Lit::Str(s) => quote!(::model_core::Value::Str(#s)),
                                Lit::Bool(b) => quote!(::model_core::Value::Bool(#b)),
                                Lit::Int(i) => quote!(::model_core::Value::Int(#i)),
                                Lit::Float(f) => quote!(::model_core::Value::Float(#f)),
                                other => {
                                    return Err(Error::new_spanned(
                                        other,
                                        "`fill` takes a function path or a string, bool, int or float literal",
                                    ))
                                }
                            });
                        } else {
                            a.fill = Some(path(&v.parse::<Path>()?));
                        }
                    }
                    "accept" => a.accept = Some(m.value()?.parse::<Type>()?),
                    "links" => m.parse_nested_meta(|l| {
                        let leaf = l.path.require_ident()?.to_string();
                        a.links.push((leaf, string(&l)?));
                        Ok(())
                    })?,
                    "link_at" => {
                        let (mut to, mut from) = (None, None);
                        m.parse_nested_meta(|l| {
                            if l.path.is_ident("to") {
                                to = Some(string(&l)?);
                            } else if l.path.is_ident("from") {
                                from = Some(string(&l)?);
                            } else {
                                return Err(l.error("`link_at(to = \"..\", from = \"..\")`"));
                            }
                            Ok(())
                        })?;
                        match (to, from) {
                            (Some(t), Some(f)) => a.links_at.push((t, f)),
                            _ => return Err(m.error("`link_at(to = \"..\", from = \"..\")`")),
                        }
                    }
                    "fill_at" => {
                        let (mut to, mut value) = (None, None);
                        m.parse_nested_meta(|l| {
                            if l.path.is_ident("to") {
                                to = Some(string(&l)?);
                            } else if l.path.is_ident("value") {
                                value = Some(match l.value()?.parse::<Lit>()? {
                                    Lit::Str(s) => quote!(::model_core::Value::Str(#s)),
                                    Lit::Bool(b) => quote!(::model_core::Value::Bool(#b)),
                                    Lit::Int(i) => quote!(::model_core::Value::Int(#i)),
                                    Lit::Float(f) => quote!(::model_core::Value::Float(#f)),
                                    other => {
                                        return Err(Error::new_spanned(
                                            other,
                                            "`fill_at` takes a string, bool, int or float literal",
                                        ))
                                    }
                                });
                            } else {
                                return Err(l.error("`fill_at(to = \"..\", value = ..)`"));
                            }
                            Ok(())
                        })?;
                        match (to, value) {
                            (Some(t), Some(v)) => a.fills_at.push((t, v)),
                            _ => return Err(m.error("`fill_at(to = \"..\", value = ..)`")),
                        }
                    }
                    "local" => a.local = true,
                    "nullable" => a.nullable = true,
                    "directive" => a.directive = true,
                    "keys" => a.keys = true,
                    "arm_pattern" => a.arm_pattern = true,
                    _ => return Err(m.error("unknown `model` attribute")),
                }
                Ok(())
            })?;
        }
    }
    if !a.links.is_empty() && a.inherit_slot.is_none() {
        return Err(syn::Error::new_spanned(
            attrs.iter().find(|a| a.path().is_ident("model")),
            "`links(..)` needs `inherit_slot` on the same field",
        ));
    }
    if a.accept.is_some() && (a.absent.is_some() || a.flatten || a.keys) {
        return Err(syn::Error::new_spanned(
            attrs.iter().find(|a| a.path().is_ident("model")),
            "`accept` is for a plain leaf: it cannot combine with `absent`, `flatten` or `keys`",
        ));
    }
    if a.inherit.is_some() && a.inherit_slot.is_some() {
        return Err(syn::Error::new_spanned(
            attrs.iter().find(|a| a.path().is_ident("model")),
            "`inherit` (a leaf) and `inherit_slot` (a struct) are exclusive",
        ));
    }
    Ok(a)
}

/// What a struct or enum declares at its own level.
#[derive(Default)]
pub struct Container {
    pub constraints: Vec<TokenStream>,
    pub tag: Option<String>,
    /// `shorthand = "key"`: a bare string stands for `{key: string}`.
    pub shorthand: Option<String>,
    /// `shorthand_with = path`: a bare string is widened by `fn(&Node, &mut Cx) -> Shorthand`.
    pub shorthand_with: Option<String>,
    /// `string_pattern = PATH`: the pattern the schema states for that string arm.
    pub string_pattern: Option<String>,
    /// `complete`: every key is required even under an inherit slot.
    pub complete: bool,
    /// `read_under = "type"`: constraints may name the tag the model is read under.
    pub read_under: Option<String>,
    /// `fill = path`: infers an absent tag from the keys present.
    pub tag_fill: Option<String>,
}

enum Item {
    Name(String),
    Null,
    Lit(Lit),
    Call(Span, String, Vec<Item>),
    Eq(String, Lit),
}

fn items(input: ParseStream) -> Result<Vec<Item>> {
    let mut out = Vec::new();
    while !input.is_empty() {
        if input.peek(Token![-]) {
            let neg: Token![-] = input.parse()?;
            let l: Lit = input.parse()?;
            let text = format!("-{}", quote!(#l));
            let lit = syn::parse_str(&text).map_err(|_| Error::new(neg.span, "bad number"))?;
            out.push(Item::Lit(lit));
        } else if input.peek(Lit) {
            out.push(Item::Lit(input.parse()?));
        } else {
            let id = Ident::parse_any(input)?;
            let name = id.to_string();
            if input.peek(syn::token::Paren) {
                let inner;
                syn::parenthesized!(inner in input);
                out.push(Item::Call(id.span(), name, items(&inner)?));
            } else if input.peek(Token![=]) {
                input.parse::<Token![=]>()?;
                out.push(Item::Eq(name, input.parse()?));
            } else if name == "null" {
                out.push(Item::Null);
            } else {
                out.push(Item::Name(name));
            }
        }
        if !input.is_empty() {
            input.parse::<Token![,]>()?;
        }
    }
    Ok(out)
}

fn key(i: &Item) -> Option<String> {
    match i {
        Item::Name(n) => Some(n.clone()),
        Item::Lit(Lit::Str(s)) => Some(s.value()),
        _ => None,
    }
}

fn value(i: &Item) -> Option<TokenStream> {
    Some(match i {
        Item::Null => quote!(::model_core::Value::Null),
        Item::Lit(Lit::Str(s)) => {
            let v = s.value();
            quote!(::model_core::Value::Str(#v))
        }
        Item::Lit(Lit::Bool(b)) => {
            let v = b.value;
            quote!(::model_core::Value::Bool(#v))
        }
        Item::Lit(Lit::Int(n)) => quote!(::model_core::Value::Int(#n)),
        Item::Lit(Lit::Float(f)) => quote!(::model_core::Value::Float(#f)),
        _ => return None,
    })
}

fn strs(items: &[Item], at: Span) -> Result<Vec<String>> {
    items
        .iter()
        .map(|i| key(i).ok_or_else(|| Error::new(at, "expected a key")))
        .collect()
}

fn values(items: &[Item], at: Span) -> Result<Vec<TokenStream>> {
    items
        .iter()
        .map(|i| value(i).ok_or_else(|| Error::new(at, "expected a string, number, bool or null")))
        .collect()
}

fn condition(span: Span, name: &str, args: &[Item]) -> Result<TokenStream> {
    let (first, rest) = args
        .split_first()
        .ok_or_else(|| Error::new(span, "a condition names a key"))?;
    let k = key(first).ok_or_else(|| Error::new(span, "a condition starts with a key"))?;
    Ok(match name {
        "set" => quote!(::model_core::Condition::IsSet(#k)),
        "list" => quote!(::model_core::Condition::IsList(#k)),
        "is_in" | "not_in" => {
            let (v, neg) = (values(rest, span)?, name == "not_in");
            quote!(::model_core::Condition::ValueIn { key: #k, values: &[#(#v),*], negate: #neg })
        }
        "call" | "not_call" => {
            let (n, neg) = (strs(rest, span)?, name == "not_call");
            quote!(::model_core::Condition::IsCall { key: #k, names: &[#(#n),*], negate: #neg })
        }
        _ => {
            return Err(Error::new(
                span,
                "a condition is `set`, `list`, `is_in`, `not_in`, `call` or `not_call`",
            ))
        }
    })
}

fn constraint(span: Span, kind: &str, args: Vec<Item>) -> Result<TokenStream> {
    let variant = match kind {
        "requires" => "Requires",
        "forbids" => "Forbids",
        "value_in" => "ValueIn",
        "items_match" => "ItemsMatch",
        "range" => "Range",
        "unique" => "Unique",
        "at_least_one_of" => "AtLeastOneOf",
        "at_most_one_of" => "AtMostOneOf",
        "not_null" => "NotNull",
        _ => {
            return Err(Error::new(
                span,
                "a constraint is `requires`, `forbids`, `value_in`, `items_match`, `range`, `unique`, `at_least_one_of`, `at_most_one_of` or `not_null`",
            ))
        }
    };
    let variant = Ident::new(variant, span);
    let (mut fields, mut when, mut nonempty, mut auto) = (vec![], vec![], vec![], vec![]);
    let (mut vals, mut pattern, mut message, mut code) = (vec![], None, None, None);
    let mut bounds = [None, None, None, None];
    for a in &args {
        match a {
            Item::Call(sp, n, inner) if n == "when" => {
                for c in inner {
                    let Item::Call(csp, cn, cargs) = c else {
                        return Err(Error::new(*sp, "`when` lists conditions like `set(key)`"));
                    };
                    when.push(condition(*csp, cn, cargs)?);
                }
            }
            Item::Call(sp, n, inner) if n == "nonempty" => nonempty = strs(inner, *sp)?,
            Item::Call(sp, n, inner) if n == "auto" => auto = strs(inner, *sp)?,
            Item::Eq(n, Lit::Str(s)) if n == "pattern" => pattern = Some(s.value()),
            Item::Eq(n, Lit::Str(s)) if n == "message" => message = Some(s.value()),
            Item::Eq(n, Lit::Str(s)) if n == "code" => code = Some(s.value()),
            Item::Eq(n, l) if matches!(n.as_str(), "gt" | "ge" | "lt" | "le") => {
                let v: f64 = match l {
                    Lit::Int(i) => i.base10_parse()?,
                    Lit::Float(f) => f.base10_parse()?,
                    _ => return Err(Error::new(span, "a bound is a number")),
                };
                let at = ["gt", "ge", "lt", "le"]
                    .iter()
                    .position(|b| b == n)
                    .unwrap_or(0);
                if let Some(slot) = bounds.get_mut(at) {
                    *slot = Some(v);
                }
            }
            Item::Eq(n, _) => return Err(Error::new(span, format!("unknown argument `{n}`"))),
            other => {
                if fields.is_empty() || kind != "value_in" {
                    fields.push(key(other).ok_or_else(|| Error::new(span, "expected a key"))?);
                } else {
                    vals.push(value(other).ok_or_else(|| Error::new(span, "expected a value"))?);
                }
            }
        }
    }
    let one = |what: &str| -> Result<()> {
        if fields.len() == 1 {
            Ok(())
        } else {
            Err(Error::new(span, format!("`{what}` names exactly one key")))
        }
    };
    match kind {
        "requires" | "forbids" | "unique" | "not_null" if fields.is_empty() => {
            return Err(Error::new(span, format!("`{kind}` names at least one key")))
        }
        "at_least_one_of" | "at_most_one_of" if fields.len() < 2 => {
            return Err(Error::new(
                span,
                format!("`{kind}` names at least two keys"),
            ))
        }
        "value_in" => {
            one(kind)?;
            if vals.is_empty() {
                return Err(Error::new(span, "`value_in` lists the allowed values"));
            }
        }
        "items_match" => {
            one(kind)?;
            if pattern.is_none() {
                return Err(Error::new(span, "`items_match` needs `pattern = \"..\"`"));
            }
        }
        "range" => {
            one(kind)?;
            if bounds.iter().all(Option::is_none) {
                return Err(Error::new(
                    span,
                    "`range` needs one of `gt`, `ge`, `lt`, `le`",
                ));
            }
        }
        _ => {}
    }
    if !matches!(kind, "value_in") && !vals.is_empty() {
        return Err(Error::new(span, format!("`{kind}` takes no values")));
    }
    if pattern.is_some() && kind != "items_match" {
        return Err(Error::new(span, format!("`{kind}` takes no pattern")));
    }
    if !auto.is_empty() && kind != "at_most_one_of" {
        return Err(Error::new(span, "`auto` is for `at_most_one_of`"));
    }
    let sets = matches!(
        kind,
        "requires" | "forbids" | "at_least_one_of" | "at_most_one_of"
    );
    if !nonempty.is_empty() && !sets {
        return Err(Error::new(span, format!("`{kind}` takes no `nonempty`")));
    }
    for k in nonempty.iter().chain(&auto) {
        if !fields.contains(k) {
            return Err(Error::new(
                span,
                format!("`{k}` in `nonempty`/`auto` must also be a key of `{kind}`"),
            ));
        }
    }
    if !when.is_empty()
        && !matches!(
            kind,
            "requires" | "forbids" | "value_in" | "items_match" | "range"
        )
    {
        return Err(Error::new(span, format!("`{kind}` takes no `when`")));
    }
    let pat = match pattern {
        Some(p) => quote!(Some(#p)),
        None => quote!(None),
    };
    let msg = match message {
        Some(m) => quote!(Some(#m)),
        None => quote!(None),
    };
    let code = match code {
        Some(c) => quote!(Some(#c)),
        None => quote!(None),
    };
    let b = bounds.map(|b| match b {
        Some(v) => quote!(Some(#v)),
        None => quote!(None),
    });
    let [gt, ge, lt, le] = b;
    Ok(quote! {
        ::model_core::Constraint {
            kind: ::model_core::ConstraintKind::#variant,
            fields: &[#(#fields),*], when: &[#(#when),*], nonempty: &[#(#nonempty),*],
            auto: &[#(#auto),*], values: &[#(#vals),*], pattern: #pat,
            bounds: ::model_core::Bounds { gt: #gt, ge: #ge, lt: #lt, le: #le },
            message: #msg, code: #code,
        }
    })
}

pub fn container(attrs: &[Attribute]) -> Result<Container> {
    let mut out = Container::default();
    for attr in attrs.iter().filter(|a| a.path().is_ident("model")) {
        attr.parse_nested_meta(|m| {
            if m.path.is_ident("tag") {
                out.tag = Some(string(&m)?);
                return Ok(());
            }
            if m.path.is_ident("read_under") {
                out.read_under = Some(string(&m)?);
                return Ok(());
            }
            if m.path.is_ident("complete") {
                out.complete = true;
                return Ok(());
            }
            if m.path.is_ident("shorthand") {
                out.shorthand = Some(string(&m)?);
                return Ok(());
            }
            if m.path.is_ident("shorthand_with") {
                out.shorthand_with = Some(path(&m.value()?.parse::<Path>()?));
                return Ok(());
            }
            if m.path.is_ident("string_pattern") {
                out.string_pattern = Some(path(&m.value()?.parse::<Path>()?));
                return Ok(());
            }
            if m.path.is_ident("fill") {
                out.tag_fill = Some(path(&m.value()?.parse::<Path>()?));
                return Ok(());
            }
            if !m.path.is_ident("constraint") {
                return Err(m.error(
                    "a struct or enum takes only `constraint(..)`, `complete`, `read_under = \"..\"`, `shorthand = \"..\"`, `tag = \"..\"` or, with a tag, `fill = fn`",
                ));
            }
            let inner;
            syn::parenthesized!(inner in m.input);
            for item in items(&inner)? {
                let Item::Call(span, kind, args) = item else {
                    return Err(m.error("a constraint reads `kind(key, ..)`"));
                };
                out.constraints.push(constraint(span, &kind, args)?);
            }
            Ok(())
        })?;
    }
    if out.tag_fill.is_some() && out.tag.is_none() {
        return Err(Error::new(
            Span::call_site(),
            "`fill` on a type needs `tag`",
        ));
    }
    Ok(out)
}

fn path(p: &Path) -> String {
    p.segments
        .iter()
        .map(|s| s.ident.to_string())
        .collect::<Vec<_>>()
        .join("::")
}
