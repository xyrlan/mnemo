//! `owner-oracle FILE.rs ...`: one `file<TAB>owner<TAB>member,member` row per
//! type a file declares, as `syn` parses it, and `file<TAB><TAB>` for a file
//! that declares none. The members are what `mnemo land` reads for the same
//! owner (#675): named fields, enum variants, and the `fn`/`const`/`type`
//! items of every `trait` and `impl` block, an impl keyed by its self type's
//! last path segment. A file `syn` cannot parse prints `file<TAB>!PARSE<TAB>`.
use std::collections::{BTreeMap, BTreeSet};
use syn::visit::{self, Visit};

#[derive(Default)]
struct Owners(BTreeMap<String, BTreeSet<String>>);

impl Owners {
    fn add(&mut self, owner: String, members: impl IntoIterator<Item = String>) {
        self.0.entry(owner).or_default().extend(members);
    }
}

fn name(ident: &syn::Ident) -> String {
    ident.to_string().trim_start_matches("r#").to_string()
}

fn self_type(ty: &syn::Type) -> Option<String> {
    match ty {
        syn::Type::Path(p) => p.path.segments.last().map(|s| name(&s.ident)),
        syn::Type::Reference(r) => self_type(&r.elem),
        syn::Type::Paren(p) => self_type(&p.elem),
        _ => None,
    }
}

fn named(fields: &syn::Fields) -> Vec<String> {
    match fields {
        syn::Fields::Named(n) => n.named.iter().filter_map(|f| f.ident.as_ref().map(name)).collect(),
        _ => vec![],
    }
}

impl<'ast> Visit<'ast> for Owners {
    fn visit_item_struct(&mut self, i: &'ast syn::ItemStruct) {
        self.add(name(&i.ident), named(&i.fields));
        visit::visit_item_struct(self, i);
    }
    fn visit_item_union(&mut self, i: &'ast syn::ItemUnion) {
        self.add(name(&i.ident), i.fields.named.iter().filter_map(|f| f.ident.as_ref().map(name)));
        visit::visit_item_union(self, i);
    }
    fn visit_item_enum(&mut self, i: &'ast syn::ItemEnum) {
        self.add(name(&i.ident), i.variants.iter().map(|v| name(&v.ident)));
        visit::visit_item_enum(self, i);
    }
    fn visit_item_trait(&mut self, i: &'ast syn::ItemTrait) {
        let items = i.items.iter().filter_map(|it| match it {
            syn::TraitItem::Fn(f) => Some(name(&f.sig.ident)),
            syn::TraitItem::Const(c) => Some(name(&c.ident)),
            syn::TraitItem::Type(t) => Some(name(&t.ident)),
            _ => None,
        });
        self.add(name(&i.ident), items.collect::<Vec<_>>());
        visit::visit_item_trait(self, i);
    }
    fn visit_item_impl(&mut self, i: &'ast syn::ItemImpl) {
        if let Some(owner) = self_type(&i.self_ty) {
            let items = i.items.iter().filter_map(|it| match it {
                syn::ImplItem::Fn(f) => Some(name(&f.sig.ident)),
                syn::ImplItem::Const(c) => Some(name(&c.ident)),
                syn::ImplItem::Type(t) => Some(name(&t.ident)),
                _ => None,
            });
            self.add(owner, items.collect::<Vec<_>>());
        }
        visit::visit_item_impl(self, i);
    }
}

fn main() {
    for path in std::env::args().skip(1) {
        let parsed = std::fs::read_to_string(&path).ok().and_then(|s| syn::parse_file(&s).ok());
        let Some(file) = parsed else {
            println!("{path}\t!PARSE\t");
            continue;
        };
        let mut owners = Owners::default();
        owners.visit_file(&file);
        if owners.0.is_empty() {
            println!("{path}\t\t");
        }
        for (owner, members) in owners.0 {
            println!("{path}\t{owner}\t{}", members.into_iter().collect::<Vec<_>>().join(","));
        }
    }
}
