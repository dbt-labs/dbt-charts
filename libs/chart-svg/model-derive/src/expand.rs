use crate::attrs::{self, FieldAttrs};
use proc_macro2::TokenStream;
use quote::{format_ident, quote};
use syn::ext::IdentExt;
use syn::{Data, DeriveInput, Error, Fields, GenericArgument, Ident, PathArguments, Result, Type};

/// Type arguments of the last path segment.
fn args(t: &Type) -> Option<(String, Vec<&Type>)> {
    let Type::Path(p) = t else { return None };
    let seg = p.path.segments.last()?;
    let tys = match &seg.arguments {
        PathArguments::AngleBracketed(a) => a
            .args
            .iter()
            .filter_map(|g| {
                if let GenericArgument::Type(t) = g {
                    Some(t)
                } else {
                    None
                }
            })
            .collect(),
        _ => Vec::new(),
    };
    Some((seg.ident.to_string(), tys))
}

/// A Rust primitive or `String`: a type with no empty value of its own.
fn is_primitive(t: &Type) -> bool {
    let Type::Path(p) = t else { return false };
    p.path.get_ident().is_some_and(|i| {
        matches!(
            i.to_string().as_str(),
            "f32"
                | "f64"
                | "bool"
                | "String"
                | "char"
                | "usize"
                | "isize"
                | "u8"
                | "u16"
                | "u32"
                | "u64"
                | "i8"
                | "i16"
                | "i32"
                | "i64"
        )
    })
}

fn is_map(t: &Type) -> bool {
    args(t).is_some_and(|(n, _)| n == "IndexMap")
}

/// Whether `ty` is `Option<Patch<_>>` with `absent`: a sparse overlay whose `null` is a state.
fn absent_patch_ty(ty: &Type, a: &FieldAttrs) -> bool {
    a.absent.is_some()
        && args(ty).is_some_and(|(n, inner)| {
            n == "Option"
                && inner
                    .first()
                    .is_some_and(|t| args(t).is_some_and(|(n, _)| n == "Patch"))
        })
}

fn type_expr(t: &Type) -> TokenStream {
    match args(t) {
        Some((n, a)) if n == "Option" && a.len() == 1 => {
            let e = a.first().map(|t| type_expr(t));
            quote!(&::model_core::TypeExpr::Option(#e))
        }
        Some((n, a)) if n == "Patch" && a.len() == 1 => {
            let e = a.first().map(|t| type_expr(t));
            quote!(&::model_core::TypeExpr::Patch(#e))
        }
        _ => quote!(&<#t as ::model_core::Leaf>::EXPR),
    }
}

/// `MatchLine` -> `match_line`: the authored spelling of a variant.
fn spelling(ident: &str) -> String {
    let mut out = String::new();
    for (i, c) in ident.chars().enumerate() {
        if c.is_uppercase() {
            if i > 0 {
                out.push('_');
            }
            out.extend(c.to_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}

fn opt_str(o: &Option<String>) -> TokenStream {
    match o {
        Some(s) => quote!(Some(#s)),
        None => quote!(None),
    }
}

struct Slot {
    name: Ident,
    /// `T` of an `Option<T>` field with `absent`, merged by `merge_nullable`.
    nullable: Option<Type>,
    /// A `Patch<_>` slot: two set values merge deep, as for `nullable`.
    patched: bool,
    /// A map field marked `merge = deep`: entries merge field by field.
    deep_map: bool,
    authored: TokenStream,
    patch: TokenStream,
    field: TokenStream,
    asserts: Vec<TokenStream>,
}

/// A field's slot. An `omit` field is an `Option<T>` whose twins are `T`'s own: it is expanded as
/// the plain field `T`, and only the field table keeps the `Option`.
fn slot(name: Ident, ty: &Type, a: &FieldAttrs, doc: &str, unit: bool) -> Result<Slot> {
    if a.omit.is_none() {
        return slot_of(name, ty, ty, a, doc, unit);
    }
    if a.default_empty
        || a.accept.is_some()
        || a.flatten
        || a.keys
        || a.merge.as_deref() == Some("override")
    {
        return Err(Error::new_spanned(
            ty,
            "`omit` cannot combine with `default_empty`, `accept`, `flatten`, `keys` or `merge = override`",
        ));
    }
    match args(ty) {
        Some((n, inner)) if n == "Option" && a.absent.is_none() => {
            let t = inner
                .first()
                .ok_or_else(|| Error::new_spanned(ty, "Option needs a type"))?;
            if is_map(t) {
                return Err(Error::new_spanned(
                    ty,
                    "`omit` is for a scalar or a model: a map that may be left out is `default_empty`",
                ));
            }
            slot_of(name, t, ty, a, doc, unit)
        }
        _ => Err(Error::new_spanned(
            ty,
            "`omit` is for an `Option<T>` field and cannot combine with `absent`",
        )),
    }
}

fn slot_of(
    name: Ident,
    ty: &Type,
    shown: &Type,
    a: &FieldAttrs,
    doc: &str,
    unit: bool,
) -> Result<Slot> {
    if a.default_empty && (args(ty).is_some_and(|(n, _)| n == "Option") || is_primitive(ty)) {
        return Err(Error::new_spanned(
            ty,
            "`default_empty` is for a map, a list or a model: the built value is the empty value; \
             an `Option` is `absent` and a scalar takes a `fill`",
        ));
    }
    let mut asserts = Vec::new();
    let mut nullable = None;
    let opt = args(ty).filter(|(n, _)| n == "Option");
    let patch = args(ty).filter(|(n, _)| n == "Patch");
    let patched = patch.is_some() || absent_patch_ty(ty, a);
    let mut deep_map = false;
    if patch.is_some() && a.accept.is_some() {
        return Err(Error::new_spanned(
            ty,
            "`accept` is for a plain leaf: it cannot combine with a `Patch` slot",
        ));
    }
    let absent_patch = match (&opt, &a.absent) {
        (Some((_, inner)), Some(_)) => inner
            .first()
            .filter(|t| args(t).is_some_and(|(n, _)| n == "Patch")),
        _ => None,
    };
    let (authored, patch, merge) = match (&opt, &a.absent) {
        (Some(_), Some(_)) if absent_patch.is_some() => {
            let t = absent_patch;
            (
                quote!(::model_core::Tri<#t>),
                quote!(::model_core::Tri<#t>),
                quote!(::model_core::Merge::Deep),
            )
        }
        (Some((_, inner)), Some(_)) => {
            let t = inner
                .first()
                .ok_or_else(|| Error::new_spanned(ty, "Option needs a type"))?;
            if a.merge.is_some() {
                return Err(Error::new_spanned(
                    ty,
                    "`merge` cannot combine with `absent`: the strategy is the type's",
                ));
            }
            nullable = Some((*t).clone());
            (
                quote!(::model_core::Tri<<<#t as ::model_core::Leaf>::Authored as ::model_core::Lift>::Value>),
                quote!(::model_core::Tri<<<#t as ::model_core::Leaf>::Patch as ::model_core::Lift>::Value>),
                quote!(<#t as ::model_core::Leaf>::MERGE),
            )
        }
        (Some(_), None) => {
            return Err(Error::new_spanned(
                ty,
                "an `Option` spine field needs `#[model(absent = \"what absent means\")]`",
            ))
        }
        (None, Some(_)) => {
            return Err(Error::new_spanned(
                ty,
                "`absent` is only for `Option<T>` fields",
            ))
        }
        (None, None) if patch.is_some() => {
            let t = quote!(::model_core::Tri<#ty>);
            (t.clone(), t, quote!(::model_core::Merge::Deep))
        }
        (None, None) if a.merge.as_deref() == Some("override") => {
            let o = quote!(Option<<#ty as ::model_core::Leaf>::Whole>);
            (o.clone(), o, quote!(::model_core::Merge::Override))
        }
        (None, None) if unit => (
            quote!(()),
            quote!(()),
            quote!(::model_core::Merge::Override),
        ),
        (None, None) => {
            let m = match a.merge.as_deref() {
                Some("deep") if args(ty).is_some_and(|(n, _)| n == "IndexMap") => {
                    deep_map = true;
                    quote!(::model_core::Merge::Deep)
                }
                Some(w) => {
                    let (variant, name) = if w == "deep" {
                        (quote!(Deep), "deep")
                    } else {
                        (quote!(ByKey), "by_key")
                    };
                    let msg = format!("`merge = {name}` does not match this field's type: use `override`, or omit `merge`");
                    asserts.push(quote!(const _: () = assert!(matches!(<#ty as ::model_core::Leaf>::MERGE, ::model_core::Merge::#variant), #msg);));
                    quote!(::model_core::Merge::#variant)
                }
                None => quote!(<#ty as ::model_core::Leaf>::MERGE),
            };
            (
                quote!(<#ty as ::model_core::Leaf>::Authored),
                quote!(<#ty as ::model_core::Leaf>::Patch),
                m,
            )
        }
    };
    let n = name.unraw().to_string();
    let te = if unit {
        quote!(&::model_core::TypeExpr::Unit)
    } else {
        type_expr(shown)
    };
    let omit = opt_str(&a.omit);
    let default_empty = a.default_empty;
    let (absent, inherit, inherit_slot) = (
        opt_str(&a.absent),
        opt_str(&a.inherit),
        opt_str(&a.inherit_slot),
    );
    let (fill, role) = (opt_str(&a.fill), opt_str(&a.role));
    let fill_value = match &a.fill_value {
        Some(v) => quote!(Some(#v)),
        None => quote!(None),
    };
    let accept = match &a.accept {
        Some(t) => {
            let e = type_expr(t);
            quote!(Some(#e))
        }
        None => quote!(None),
    };
    let links = a.links.iter().map(|(l, p)| quote!((#l, #p)));
    let links_at = a.links_at.iter().map(|(t, f)| quote!((#t, #f)));
    let fills_at = a.fills_at.iter().map(|(t, v)| quote!((#t, #v)));
    let wrapped = args(ty)
        .is_some_and(|(n, _)| matches!(n.as_str(), "Patch" | "Option" | "Vec" | "IndexMap"));
    let filled_list = a.fill.is_some() && args(ty).is_some_and(|(n, _)| n == "Vec");
    if a.min_items.is_some() && !filled_list {
        return Err(Error::new_spanned(
            ty,
            "`min_items` is for a list with a `fill`",
        ));
    }
    if a.nullable
        && ((wrapped && !filled_list)
            || a.flatten
            || a.keys
            || a.accept.is_some()
            || a.absent.is_some())
    {
        return Err(Error::new_spanned(
            ty,
            "`nullable` is for a plain model field: not `Patch`, `Option`, a list or a map, and not with `flatten`, `keys`, `accept` or `absent`",
        ));
    }
    let min_items = match a.min_items {
        Some(n) => quote!(Some(#n)),
        None => quote!(None),
    };
    let (local, nested_drop, null_empty, directive, keys, flatten, arm_pattern) = (
        a.local,
        a.nested_drop,
        a.nullable,
        a.directive,
        a.keys,
        a.flatten,
        a.arm_pattern,
    );
    let field = quote! {
        ::model_core::Field {
            name: #n, ty: #te, doc: #doc, merge: #merge,
            attrs: ::model_core::Attrs {
                absent: #absent, omit: #omit, default_empty: #default_empty, inherit: #inherit, inherit_slot: #inherit_slot,
                links: &[#(#links),*], links_at: &[#(#links_at),*], fills_at: &[#(#fills_at),*], local: #local, nested_drop: #nested_drop, nullable: #null_empty,
                fill: #fill, fill_value: #fill_value, accept: #accept, directive: #directive, keys: #keys,
                flatten: #flatten, role: #role, arm_pattern: #arm_pattern, min_items: #min_items,
            },
        }
    };
    Ok(Slot {
        name,
        nullable,
        patched,
        deep_map,
        authored,
        patch,
        field,
        asserts,
    })
}

pub fn expand(input: &DeriveInput) -> Result<TokenStream> {
    if !input.generics.params.is_empty() {
        return Err(Error::new_spanned(
            &input.generics,
            "Model types are not generic",
        ));
    }
    let (name, vis) = (&input.ident, &input.vis);
    let authored = format_ident!("{}Authored", name);
    let patch = format_ident!("{}Patch", name);
    let doc = attrs::doc(&input.attrs);
    let mut errs: Option<Error> = None;
    let mut keep = |r: Result<Slot>, out: &mut Vec<Slot>| match r {
        Ok(s) => out.push(s),
        Err(e) => match &mut errs {
            Some(all) => all.combine(e),
            None => errs = Some(e),
        },
    };
    let mut slots = Vec::new();
    let container = attrs::container(&input.attrs)?;
    let name_s = name.to_string();
    let cons = &container.constraints;
    if matches!(input.data, Data::Enum(_)) && !container.constraints.is_empty() {
        return Err(Error::new_spanned(
            name,
            "constraints belong on the structs an enum selects, not on the enum",
        ));
    }
    let kind = match (&input.data, &container.tag) {
        (Data::Struct(_), None) => quote!(::model_core::ModelKind::Struct),
        (Data::Enum(_), Some(t)) => quote!(::model_core::ModelKind::Tagged(#t)),
        (Data::Enum(e), None) if e.variants.iter().all(|v| matches!(v.fields, Fields::Unit)) => {
            quote!(::model_core::ModelKind::Enum)
        }
        (Data::Enum(_), None) => quote!(::model_core::ModelKind::Variants),
        (_, Some(_)) => return Err(Error::new_spanned(name, "`tag` is for enums")),
        (Data::Union(_), None) => quote!(::model_core::ModelKind::Struct),
    };
    let shorthand = match &container.shorthand {
        Some(k) => quote!(Some(#k)),
        None => quote!(None),
    };
    let string_pattern = match (&container.shorthand_with, &container.string_pattern) {
        (Some(_), Some(p)) => {
            let p: syn::Path = syn::parse_str(p)?;
            quote!(Some(#p))
        }
        (Some(_), None) => {
            return Err(Error::new_spanned(
                name,
                "`shorthand_with` needs a `string_pattern` for the schema's string arm",
            ))
        }
        (None, Some(_)) => {
            return Err(Error::new_spanned(
                name,
                "`string_pattern` goes with `shorthand_with`",
            ))
        }
        (None, None) => quote!(None),
    };
    let complete = container.complete;
    let info = |fields: &[Slot]| {
        let f = fields.iter().map(|s| &s.field);
        let asserts = fields.iter().flat_map(|s| &s.asserts);
        quote! {
            fn info() -> &'static ::model_core::ModelInfo {
                #(#asserts)*
                const INFO: ::model_core::ModelInfo = ::model_core::ModelInfo {
                    name: #name_s, doc: #doc, kind: #kind, fields: &[#(#f),*], constraints: &[#(#cons),*], shorthand: #shorthand, string_pattern: #string_pattern, complete: #complete,
                };
                &INFO
            }
        }
    };

    match &input.data {
        Data::Struct(s) => {
            let Fields::Named(named) = &s.fields else {
                return Err(Error::new_spanned(name, "Model structs have named fields"));
            };
            let mut reads = Vec::new();
            for f in &named.named {
                let (Some(id), fa) = (f.ident.clone(), attrs::field(&f.attrs)) else {
                    continue;
                };
                if let Ok(fa) = &fa {
                    reads.push(Read {
                        id: id.clone(),
                        key: id.unraw().to_string(),
                        ty: f.ty.clone(),
                        fa: fa.clone(),
                    });
                }
                keep(
                    fa.and_then(|fa| slot(id, &f.ty, &fa, &attrs::doc(&f.attrs), false)),
                    &mut slots,
                );
            }
            if let Some(e) = errs {
                return Err(e);
            }
            let id = slots.iter().map(|s| &s.name).collect::<Vec<_>>();
            let (a, p) = (
                slots.iter().map(|s| &s.authored),
                slots.iter().map(|s| &s.patch),
            );
            let merges = slots.iter().map(|s| {
                let n = &s.name;
                let merge = match &s.nullable {
                    Some(t) => quote!(::model_core::merge_nullable::<#t>(&mut self.#n, upper.#n);),
                    None if s.deep_map => {
                        quote!(::model_core::merge_map_deep(&mut self.#n, upper.#n);)
                    }
                    None if s.patched => {
                        quote!(::model_core::merge_patch_slot(&mut self.#n, upper.#n);)
                    }
                    None => quote!(::model_core::Slot::merge(&mut self.#n, upper.#n);),
                };
                quote!(if !::model_core::Empty::is_empty(&upper.#n) { #merge })
            });
            let empties = slots.iter().map(|s| {
                let n = &s.name;
                quote!(::model_core::Empty::is_empty(&self.#n))
            });
            let set_leaves = reads.iter().map(set_leaves_stmt);
            let leaf_values = reads.iter().map(leaf_values_stmt);
            let to_patch = slots.iter().map(|s| {
                let n = &s.name;
                if s.authored.to_string() == s.patch.to_string() {
                    quote!(#n: self.#n)
                } else if let Some(t) = &s.nullable {
                    quote!(#n: ::model_core::tri_to_patch::<<#t as ::model_core::Leaf>::Authored>(self.#n))
                } else {
                    quote!(#n: ::model_core::ToPatch::to_patch(self.#n))
                }
            });
            let to_authored = slots.iter().map(|s| {
                let n = &s.name;
                if s.authored.to_string() == s.patch.to_string() {
                    quote!(#n: self.#n)
                } else if let Some(t) = &s.nullable {
                    quote!(#n: ::model_core::tri_to_authored::<<#t as ::model_core::Leaf>::Patch>(self.#n))
                } else {
                    quote!(#n: ::model_core::ToAuthored::to_authored(self.#n))
                }
            });
            let info = info(&slots);
            let build = build_impl(name, &authored, &patch, &reads);
            if let Some(k) = container.shorthand.as_deref() {
                if !reads.iter().any(|r| r.key == k || r.fa.flatten) {
                    return Err(Error::new_spanned(
                        name,
                        format!("`shorthand = \"{k}\"` names no field of this struct"),
                    ));
                }
            }
            if container.shorthand.is_some() && container.shorthand_with.is_some() {
                return Err(Error::new_spanned(
                    name,
                    "`shorthand` and `shorthand_with` both widen a bare string: use one",
                ));
            }
            let widen = match (container.shorthand.as_deref(), &container.shorthand_with) {
                (Some(k), _) => {
                    quote!(let widened = ::model_core::shorthand(node, #k); let node = widened.as_ref().unwrap_or(node);)
                }
                (None, Some(f)) => {
                    let f: syn::Path = syn::parse_str(f)?;
                    quote! {
                        let widened = match #f(node, cx) {
                            ::model_core::Shorthand::Keep => None,
                            ::model_core::Shorthand::Refused => return Default::default(),
                            ::model_core::Shorthand::Widened(n) => Some(n),
                        };
                        let node = widened.as_ref().unwrap_or(node);
                    }
                }
                (None, None) => quote!(),
            };
            let from_node = struct_from_node(name, &authored, &patch, &reads, widen);
            Ok(quote! {
                #from_node
                #[derive(Debug, Clone, PartialEq, Default)]
                #vis struct #authored { #(pub #id: #a),* }

                #[derive(Debug, Clone, PartialEq, Default)]
                #vis struct #patch { #(pub #id: #p),* }

                impl #patch {
                    /// Fold `upper` onto `self`: unset keeps lower, null sets the
                    /// absent state, a value applies the field's strategy.
                    pub fn merge(&mut self, upper: Self) {
                        #(#merges)*
                    }
                }
                impl ::model_core::Slot for #patch {
                    fn merge(&mut self, upper: Self) { #patch::merge(self, upper) }
                }
                impl ::model_core::ToAuthored for #patch {
                    type Out = #authored;
                    fn to_authored(self) -> #authored { #authored { #(#to_authored),* } }
                }
                impl ::model_core::ToPatch for #authored {
                    type Out = #patch;
                    fn to_patch(self) -> #patch { #patch { #(#to_patch),* } }
                }
                impl ::model_core::Empty for #patch {
                    fn is_empty(&self) -> bool { true #(&& #empties)* }
                }
                impl ::model_core::SetLeaves for #patch {
                    #[allow(unused_variables)]
                    fn set_leaves(&self, at: &str, f: &mut dyn FnMut(&str)) {
                        #(#set_leaves)*
                    }
                }
                impl ::model_core::Spell for #patch {
                    fn spell(&self) -> ::model_core::serde_json::Value { ::model_core::spell_leaves(self) }
                }
                impl ::model_core::Spell for #authored {
                    fn spell(&self) -> ::model_core::serde_json::Value {
                        ::model_core::Spell::spell(&::model_core::ToPatch::to_patch(self.clone()))
                    }
                }
                impl ::model_core::LeafValues for #patch {
                    #[allow(unused_variables)]
                    fn leaf_values(&self, at: &str, f: &mut dyn FnMut(&str, &dyn ::model_core::Spell)) {
                        #(#leaf_values)*
                    }
                }
                impl ::model_core::Lift for #authored {
                    type Value = Self;
                    fn lift(self) -> Option<Self> { Some(self) }
                    fn put(v: Self) -> Self { v }
                    fn leaves(_: &Self, _: &str, _: &mut dyn FnMut(&str)) {}
                }
                impl ::model_core::Lift for #patch {
                    type Value = Self;
                    fn lift(self) -> Option<Self> { Some(self) }
                    fn put(v: Self) -> Self { v }
                    fn leaves(v: &Self, at: &str, f: &mut dyn FnMut(&str)) {
                        ::model_core::SetLeaves::set_leaves(v, at, f);
                    }
                    fn values(v: &Self, at: &str, f: &mut dyn FnMut(&str, &dyn ::model_core::Spell)) {
                        ::model_core::LeafValues::leaf_values(v, at, f);
                    }
                }
                #build
                impl ::model_core::Fold for #patch {
                    fn fold(lower: &mut Self, upper: Self) { #patch::merge(lower, upper) }
                }
                impl ::model_core::Leaf for #name {
                    type Whole = #authored;
                    type Authored = #authored;
                    type Patch = #patch;
                    const MERGE: ::model_core::Merge = ::model_core::Merge::Deep;
                    const EXPR: ::model_core::TypeExpr = ::model_core::TypeExpr::Ref(#name_s, <#name as ::model_core::Model>::info);
                }
                impl ::model_core::Model for #name {
                    #info
                }
            })
        }
        Data::Enum(e) => {
            let mut variants = Vec::new();
            let mut arms = Vec::new();
            for v in &e.variants {
                let ty: Type = match &v.fields {
                    Fields::Unit => syn::parse_quote!(()),
                    Fields::Unnamed(u) if u.unnamed.len() == 1 => u
                        .unnamed
                        .first()
                        .map(|f| f.ty.clone())
                        .unwrap_or_else(|| syn::parse_quote!(())),
                    _ => return Err(Error::new_spanned(v, "variants are unit or hold one type")),
                };
                let unit = matches!(v.fields, Fields::Unit);
                if unit && container.tag.is_some() {
                    return Err(Error::new_spanned(
                        v,
                        "a tagged variant holds the model it selects",
                    ));
                }
                let fa = attrs::field(&v.attrs)?;
                keep(
                    slot(
                        Ident::new(&spelling(&v.ident.to_string()), v.ident.span()),
                        &ty,
                        &fa,
                        &attrs::doc(&v.attrs),
                        unit,
                    ),
                    &mut slots,
                );
                let id = &v.ident;
                arms.push(Arm {
                    id: id.clone(),
                    key: spelling(&id.to_string()),
                    ty: ty.clone(),
                    unit,
                });
                variants.push(if unit {
                    quote!(#id)
                } else {
                    quote!(#id(<#ty as ::model_core::Leaf>::Whole))
                });
            }
            if let Some(e) = errs {
                return Err(e);
            }
            let info = info(&slots);
            let from_node = enum_from_node(name, &authored, &arms, &container);
            let cases = arms.iter().map(|a| {
                let (id, ty) = (&a.id, &a.ty);
                let key = &a.key;
                if a.unit {
                    quote!(#authored::#id => Ok(#name::#id))
                } else {
                    // A tagged variant's keys sit beside the tag, so its payload has no segment of its own.
                    let payload_at = if container.tag.is_some() {
                        quote!(at)
                    } else {
                        quote!(&::model_core::join(at, #key))
                    };
                    quote!(#authored::#id(w) => Ok(#name::#id(::model_core::build_payload::<#ty>(w, #payload_at)?)))
                }
            });
            let spelling = |owner: &Ident| {
                arms.iter()
                    .map(|a| {
                        let (id, key) = (&a.id, &a.key);
                        if a.unit {
                            quote!(#owner::#id => ::model_core::serde_json::Value::from(#key))
                        } else {
                            quote!(#owner::#id(w) => ::model_core::Spell::spell(w))
                        }
                    })
                    .collect::<Vec<_>>()
            };
            let spells = spelling(&authored);
            // A leaf slot holds the enum itself only when it is a plain keyword set.
            let name_spell = arms.iter().all(|a| a.unit).then(|| {
                let spells = spelling(name);
                quote! {
                    impl ::model_core::Spell for #name {
                        fn spell(&self) -> ::model_core::serde_json::Value {
                            match self { #(#spells,)* }
                        }
                    }
                }
            });
            Ok(quote! {
                            #from_node
            impl ::model_core::Spell for #authored {
                                fn spell(&self) -> ::model_core::serde_json::Value {
                                    match self { #(#spells,)* }
                                }
                            }
                            #name_spell
                            impl ::model_core::Build for #name {
                                fn build(slot: Option<#authored>, at: &str) -> Result<Self, ::model_core::Unset> {
                                    <Self as ::model_core::Build>::build_authored(slot, at)
                                }
                                fn build_authored(slot: Option<#authored>, at: &str) -> Result<Self, ::model_core::Unset> {
                                    match slot {
                                        None => Err(::model_core::Unset { paths: vec![at.to_string()] }),
                                        Some(a) => match a { #(#cases,)* },
                                    }
                                }
                            }
                            #[derive(Debug, Clone, PartialEq)]
                            #vis enum #authored { #(#variants),* }

                            impl ::model_core::Leaf for #name {
                                type Whole = #authored;
                                type Authored = Option<#authored>;
                                type Patch = Option<#authored>;
                                const MERGE: ::model_core::Merge = ::model_core::Merge::Override;
                                const EXPR: ::model_core::TypeExpr = ::model_core::TypeExpr::Ref(#name_s, <#name as ::model_core::Model>::info);
                            }
                            impl ::model_core::Model for #name {
                                #info
                            }
                        })
        }
        Data::Union(_) => Err(Error::new_spanned(name, "unions are not models")),
    }
}

/// The statement of `SetLeaves` for one field: an empty slot is skipped, a flattened mixin sits at
/// the same path, a `Patch<_>` slot or a map is a leaf, a model is walked.
fn set_leaves_stmt(r: &Read) -> TokenStream {
    let (id, key, ty) = (&r.id, &r.key, &r.ty);
    let inner = quote!(::model_core::Empty::is_empty(&self.#id));
    if r.fa.flatten {
        return quote!(if !#inner { ::model_core::SetLeaves::set_leaves(&self.#id, at, f); });
    }
    let path = quote!(&::model_core::join(at, #key));
    match (args(ty), &r.fa.absent) {
        // An `Option<Patch<_>>` overlay is a leaf like a `Patch<_>` slot: set, or `null`.
        _ if absent_patch_ty(ty, &r.fa) => quote!(if !#inner { f(#path); }),
        (Some((n, a)), Some(_)) if n == "Option" => {
            let t = a.first();
            quote!(match &self.#id {
                ::model_core::Tri::Unset => {}
                ::model_core::Tri::Null => f(#path),
                ::model_core::Tri::Value(v) => {
                    f(#path);
                    <<#t as ::model_core::Leaf>::Patch as ::model_core::Lift>::leaves(v, #path, f)
                }
            })
        }
        (Some((n, _)), _) if n == "Patch" => quote!(if !#inner { f(#path); }),
        _ => quote!(if !#inner { ::model_core::SetLeaves::set_leaves(&self.#id, #path, f); }),
    }
}

/// The statement of `LeafValues` for one field, [`set_leaves_stmt`]'s walk with the values: a
/// `Patch<_>` overlay is walked like a model, and a path with leaves below it is not a leaf.
fn leaf_values_stmt(r: &Read) -> TokenStream {
    let (id, key, ty) = (&r.id, &r.key, &r.ty);
    let inner = quote!(::model_core::Empty::is_empty(&self.#id));
    if r.fa.flatten {
        return quote!(if !#inner { ::model_core::LeafValues::leaf_values(&self.#id, at, f); });
    }
    let path = quote!(&::model_core::join(at, #key));
    match (args(ty), &r.fa.absent) {
        _ if absent_patch_ty(ty, &r.fa) => {
            quote!(if let ::model_core::Tri::Value(v) = &self.#id {
                f(#path, v);
                ::model_core::LeafValues::leaf_values(v, #path, f);
            })
        }
        (Some((n, a)), Some(_)) if n == "Option" => {
            let t = a.first();
            quote!(if let ::model_core::Tri::Value(v) = &self.#id {
                f(#path, v);
                <<#t as ::model_core::Leaf>::Patch as ::model_core::Lift>::values(v, #path, f)
            })
        }
        (Some((n, _)), _) if n == "Patch" => {
            quote!(if let ::model_core::Tri::Value(v) = &self.#id {
                f(#path, v);
                ::model_core::LeafValues::leaf_values(v, #path, f);
            })
        }
        _ => quote!(if !#inner { ::model_core::LeafValues::leaf_values(&self.#id, #path, f); }),
    }
}

/// `Build` for a struct, from its patch and from its parse output, and the `TryFrom` of its
/// patch: every field is built, every unset leaf is reported by authored path, and a `Patch<_>`
/// slot (finished later by `bind`) is carried as is.
fn build_impl(name: &Ident, authored: &Ident, patch: &Ident, reads: &[Read]) -> TokenStream {
    let body = |twin: &Ident, authored_side: bool| {
        let lets = reads.iter().enumerate().map(|(i, r)| {
            let (v, id, key, ty) = (format_ident!("f{}", i), &r.id, &r.key, &r.ty);
            let at = if r.fa.flatten {
                quote!(at)
            } else {
                quote!(&::model_core::join(at, #key))
            };
            let (field, nullable) = if authored_side {
                (
                    quote!(build_field_authored),
                    quote!(build_nullable_authored),
                )
            } else {
                (quote!(build_field), quote!(build_nullable))
            };
            match (args(ty), &r.fa.absent) {
                // A sparse overlay whose `null` is a state: unset and `null` build to `None`.
                _ if absent_patch_ty(ty, &r.fa) => {
                    quote!(let #v = Some(match p.#id {
                        ::model_core::Tri::Value(v) => Some(v),
                        _ => None,
                    });)
                }
                (Some((n, a)), Some(_)) if n == "Option" => {
                    let inner = a.first();
                    quote!(let #v = ::model_core::#nullable::<#inner>(p.#id, #at, &mut unset);)
                }
                (Some((n, _)), _) if n == "Patch" => {
                    let path = at.clone();
                    quote!(let #v = match p.#id {
                        ::model_core::Tri::Value(v) => Some(v),
                        ::model_core::Tri::Unset => Some(Default::default()),
                        // A `null` on a `Patch` slot has no total value to build: refuse it by path.
                        ::model_core::Tri::Null => { unset.push((#path).to_string()); None }
                    };)
                }
                _ if r.fa.default_empty
                    && args(ty).is_some_and(|(n, _)| n == "IndexMap" || n == "Vec") =>
                {
                    let defaulted = if authored_side {
                        quote!(build_defaulted_authored)
                    } else {
                        quote!(build_defaulted)
                    };
                    quote!(let #v = ::model_core::#defaulted::<#ty>(p.#id, #at, &mut unset);)
                }
                _ if r.fa.omit.is_some() => {
                    let inner = args(ty).and_then(|(_, a)| a.first().copied().cloned());
                    let omitted = if authored_side {
                        quote!(build_omitted_authored)
                    } else {
                        quote!(build_omitted)
                    };
                    quote!(let #v = ::model_core::#omitted::<#inner>(p.#id, #at, &mut unset);)
                }
                _ if r.fa.merge.as_deref() == Some("override") => {
                    quote!(let #v = ::model_core::build_whole::<#ty>(p.#id, #at, &mut unset);)
                }
                // A declared `fill` is what a total value holds where nothing set it.
                _ if r.fa.fill_value.is_some() || r.fa.fill.as_deref() == Some("fill_empty") => {
                    let fill = match &r.fa.fill_value {
                        Some(value) => quote!(|| ::model_core::fill_literal::<#ty>(#value)),
                        None => quote!(|| Some(<#ty as Default>::default())),
                    };
                    quote!(let #v = {
                        let mut paths = Vec::new();
                        let built = ::model_core::#field::<#ty>(p.#id, #at, &mut paths);
                        ::model_core::or_fill::<#ty>(built, paths, #at, &mut unset, #fill)
                    };)
                }
                _ => quote!(let #v = ::model_core::#field::<#ty>(p.#id, #at, &mut unset);),
            }
        });
        let vars: Vec<_> = (0..reads.len()).map(|i| format_ident!("f{}", i)).collect();
        let names: Vec<_> = reads.iter().map(|r| &r.id).collect();
        quote! {
            |p: #twin, at: &str| -> Result<#name, ::model_core::Unset> {
                let mut unset = Vec::new();
                #(#lets)*
                match (#(#vars,)*) {
                    (#(Some(#vars),)*) if unset.is_empty() => Ok(#name { #(#names: #vars),* }),
                    _ => Err(::model_core::Unset { paths: unset }),
                }
            }
        }
    };
    let (from_patch, from_authored) = (body(patch, false), body(authored, true));
    quote! {
        impl ::model_core::Build for #name {
            fn build(p: #patch, at: &str) -> Result<Self, ::model_core::Unset> {
                (#from_patch)(p, at)
            }
            fn build_authored(p: #authored, at: &str) -> Result<Self, ::model_core::Unset> {
                (#from_authored)(p, at)
            }
        }
        impl TryFrom<#patch> for #name {
            type Error = ::model_core::Unset;
            fn try_from(p: #patch) -> Result<Self, Self::Error> {
                <Self as ::model_core::Build>::build(p, "")
            }
        }
    }
}

struct Read {
    id: Ident,
    key: String,
    ty: Type,
    fa: FieldAttrs,
}

struct Arm {
    id: Ident,
    key: String,
    ty: Type,
    unit: bool,
}

fn read_stmt(f: &Read, patch: bool) -> TokenStream {
    let (id, key, ty, fa) = (&f.id, &f.key, &f.ty, &f.fa);
    let (node_fn, in_fn) = if patch {
        (quote!(patch), quote!(patch_in))
    } else {
        (quote!(authored), quote!(authored_in))
    };
    if fa.flatten || fa.keys {
        return quote!(out.#id = <#ty as ::model_core::FromMap>::#in_fn(m, cx););
    }
    let overlay = if absent_patch_ty(ty, fa) {
        args(ty)
            .and_then(|(_, a)| a.first().and_then(|t| args(t)))
            .map(|(_, a)| a)
    } else {
        args(ty).filter(|(n, _)| n == "Patch").map(|(_, a)| a)
    };
    if let Some(a) = overlay {
        if let Some(inner) = a.first() {
            return quote!(if let Some(v) = cx.field(m, #key, |n, cx| ::model_core::read_patch_tri::<#inner>(n, cx)) { out.#id = v; });
        }
    }
    let inner = args(ty)
        .filter(|(n, _)| n == "Option")
        .and_then(|(_, a)| a.first().copied().cloned());
    let read = match (&fa.absent, &fa.accept, inner) {
        (Some(_), _, Some(inner)) => {
            let read = if patch {
                quote!(read_nullable_patch)
            } else {
                quote!(read_nullable)
            };
            return quote!(if let Some(v) = cx.field(m, #key, |n, cx| ::model_core::#read::<#inner>(n, cx)) { out.#id = v; });
        }
        (None, None, Some(inner)) if fa.omit.is_some() => {
            quote!(<#inner as ::model_core::FromNode>::#node_fn(n, cx))
        }
        (_, Some(alt), _) => {
            return quote!(if let Some(Some(v)) = cx.field(m, #key, |n, cx| ::model_core::read_accept::<#ty, #alt>(n, cx)) { out.#id = Some(v); });
        }
        _ if fa.nullable && is_list(ty) => {
            let min = fa.min_items.unwrap_or(0);
            if patch {
                quote!(::model_core::read_list(n, cx, #min, <#ty as ::model_core::FromNode>::patch))
            } else {
                quote!(::model_core::read_list(n, cx, #min, <#ty as ::model_core::FromNode>::authored))
            }
        }
        _ if fa.nullable => {
            if patch {
                quote!(::model_core::read_or_empty(n, cx, <#ty as ::model_core::FromNode>::patch))
            } else {
                quote!(::model_core::read_or_empty(n, cx, <#ty as ::model_core::FromNode>::authored))
            }
        }
        _ if fa.merge.as_deref() == Some("override") => {
            quote!(<#ty as ::model_core::FromNode>::whole(n, cx))
        }
        _ => quote!(<#ty as ::model_core::FromNode>::#node_fn(n, cx)),
    };
    quote!(if let Some(v) = cx.field(m, #key, |n, cx| #read) { out.#id = v; })
}

fn is_list(ty: &Type) -> bool {
    args(ty).is_some_and(|(n, _)| n == "Vec")
}

fn struct_from_node(
    name: &Ident,
    authored: &Ident,
    patch: &Ident,
    reads: &[Read],
    widen: TokenStream,
) -> TokenStream {
    let name_s = name.to_string();
    let a: Vec<_> = reads.iter().map(|r| read_stmt(r, false)).collect();
    let p: Vec<_> = reads.iter().map(|r| read_stmt(r, true)).collect();
    let shared = |r: &&Read| r.fa.flatten || r.fa.keys;
    let own: Vec<_> = reads.iter().filter(|r| !shared(r)).collect();
    let own_keys = own.iter().map(|r| &r.key);
    let own_walk = own.iter().map(|r| {
        let k = &r.key;
        let t = match args(&r.ty) {
            Some((_, a)) if absent_patch_ty(&r.ty, &r.fa) => a
                .first()
                .and_then(|t| args(t))
                .and_then(|(_, a)| a.first().map(|t| (*t).clone()))
                .unwrap_or_else(|| r.ty.clone()),
            Some((n, a))
                if n == "Patch"
                    || (n == "Option" && (r.fa.absent.is_some() || r.fa.omit.is_some())) =>
            {
                a.first()
                    .map(|t| (*t).clone())
                    .unwrap_or_else(|| r.ty.clone())
            }
            _ => r.ty.clone(),
        };
        quote!(w.field(#k, <#t as ::model_core::FromNode>::walk);)
    });
    let shared_tys: Vec<_> = reads.iter().filter(shared).map(|r| &r.ty).collect();
    quote! {
        impl ::model_core::FromNode for #name {
            fn authored(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> #authored {
                #widen
                ::model_core::read_map(node, cx, <Self as ::model_core::FromNode>::keys(), |m, cx| <Self as ::model_core::FromMap>::authored_in(m, cx))
            }
            fn patch(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> #patch {
                #widen
                ::model_core::read_map(node, cx, <Self as ::model_core::FromNode>::keys(), |m, cx| <Self as ::model_core::FromMap>::patch_in(m, cx))
            }
            fn whole(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<#authored> {
                Some(<Self as ::model_core::FromNode>::authored(node, cx))
            }
            fn keys() -> Vec<String> {
                let mut k: Vec<String> = vec![#(#own_keys.to_string()),*];
                #(k.extend(<#shared_tys as ::model_core::FromNode>::keys());)*
                k
            }
            fn walk(w: &mut ::model_core::Walk) {
                w.model(#name_s, &<Self as ::model_core::FromNode>::keys(), |w| {
                    #(#own_walk)*
                    #(<#shared_tys as ::model_core::FromNode>::walk(w);)*
                });
            }
        }
        impl ::model_core::FromMap for #name {
            fn authored_in(m: &mut ::model_core::MapRead, cx: &mut ::model_core::Cx) -> #authored {
                let mut out = #authored::default();
                #(#a)*
                out
            }
            fn patch_in(m: &mut ::model_core::MapRead, cx: &mut ::model_core::Cx) -> #patch {
                let mut out = #patch::default();
                #(#p)*
                out
            }
        }
    }
}

fn enum_from_node(
    name: &Ident,
    authored: &Ident,
    arms: &[Arm],
    ta: &attrs::Container,
) -> TokenStream {
    let name_s = name.to_string();
    let keys: Vec<_> = arms.iter().map(|a| &a.key).collect();
    if ta.tag.is_none() && arms.iter().all(|a| a.unit) {
        let cases = arms.iter().enumerate().map(|(i, a)| {
            let id = &a.id;
            quote!(#i => Some(#authored::#id))
        });
        let unit_cases = arms.iter().enumerate().map(|(i, a)| {
            let id = &a.id;
            quote!(#i => Some(#name::#id))
        });
        return quote! {
            impl ::model_core::ReadScalar for #name {
                fn read(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<Self> {
                    match ::model_core::read_enum(node, cx, &[#(#keys),*])? {
                        #(#unit_cases,)*
                        _ => None,
                    }
                }
            }
            impl ::model_core::FromNode for #name {
                fn authored(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<#authored> {
                    match ::model_core::read_enum(node, cx, &[#(#keys),*])? {
                        #(#cases,)*
                        _ => None,
                    }
                }
                fn patch(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<#authored> {
                    <Self as ::model_core::FromNode>::authored(node, cx)
                }
                fn whole(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<#authored> {
                    <Self as ::model_core::FromNode>::authored(node, cx)
                }
            }
        };
    }
    let tys: Vec<_> = arms.iter().filter(|a| !a.unit).map(|a| &a.ty).collect();
    let tagged = ta.tag.is_some();
    let walks = arms.iter().filter(|a| !a.unit).map(|a| {
        let (k, t) = (&a.key, &a.ty);
        if tagged {
            quote!(<#t as ::model_core::FromNode>::walk(w);)
        } else {
            quote!(w.field(#k, <#t as ::model_core::FromNode>::walk);)
        }
    });
    let read_in = match &ta.tag {
        Some(tag) => {
            let infer = match &ta.tag_fill {
                Some(f) => {
                    let f = parsed(f);
                    quote!(Some(#f as fn(&[&str]) -> Option<&'static str>))
                }
                None => quote!(None),
            };
            let cases = arms.iter().enumerate().map(|(i, a)| {
                let (id, t) = (&a.id, &a.ty);
                if a.unit {
                    quote!(#i => Some(#authored::#id))
                } else {
                    quote!(#i => Some(#authored::#id(<#t as ::model_core::FromMap>::authored_in(m, cx))))
                }
            });
            quote! {
                    let Some(tag) = ::model_core::read_tag(m, cx, #tag, &[#(#keys),*], #infer) else {
                        // A bad tag leaves the payload unread: its keys are not unknown.
                        for k in <Self as ::model_core::FromNode>::keys() {
                            m.ask(&k);
                        }
                        return None;
                    };
                    match tag {
                        #(#cases,)*
                        _ => None,
                    }
            }
        }
        None => {
            let cases = arms.iter().enumerate().map(|(i, a)| {
                let (id, k, t) = (&a.id, &a.key, &a.ty);
                if a.unit {
                    quote!(#i => cx.field(m, #k, ::model_core::read_null).flatten().map(|()| #authored::#id))
                } else {
                    quote!(#i => cx.field(m, #k, |n, cx| <#t as ::model_core::FromNode>::whole(n, cx)).flatten().map(#authored::#id))
                }
            });
            let units = arms.iter().map(|a| a.unit);
            quote! {
                    match ::model_core::read_keys(m, cx, &[#(#keys),*], &[#(#units),*])? {
                        #(#cases,)*
                        _ => None,
                    }
            }
        }
    };
    let tag_key = ta.tag.iter();
    let variant_keys = if ta.tag.is_some() {
        quote!(#(k.extend(<#tys as ::model_core::FromNode>::keys());)*)
    } else {
        quote!(k.extend([#(#keys.to_string()),*]);)
    };
    quote! {
        impl ::model_core::FromNode for #name {
            fn authored(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<#authored> {
                ::model_core::read_map(node, cx, <Self as ::model_core::FromNode>::keys(), |m, cx| <Self as ::model_core::FromMap>::authored_in(m, cx))
            }
            fn patch(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<#authored> {
                <Self as ::model_core::FromNode>::authored(node, cx)
            }
            fn whole(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<#authored> {
                <Self as ::model_core::FromNode>::authored(node, cx)
            }
            fn keys() -> Vec<String> {
                let mut k: Vec<String> = vec![#(#tag_key.to_string()),*];
                #variant_keys
                k
            }
            fn walk(w: &mut ::model_core::Walk) {
                w.model(#name_s, &<Self as ::model_core::FromNode>::keys(), |w| {
                    #(#walks)*
                });
            }
        }
        impl ::model_core::FromMap for #name {
            fn authored_in(m: &mut ::model_core::MapRead, cx: &mut ::model_core::Cx) -> Option<#authored> {
                #read_in
            }
            fn patch_in(m: &mut ::model_core::MapRead, cx: &mut ::model_core::Cx) -> Option<#authored> {
                <Self as ::model_core::FromMap>::authored_in(m, cx)
            }
        }
    }
}

/// A path recorded as text by an attribute, back as tokens; a bad one is a compile error.
fn parsed(text: &str) -> TokenStream {
    match syn::parse_str::<syn::Path>(text) {
        Ok(t) => quote!(#t),
        Err(e) => e.to_compile_error(),
    }
}
