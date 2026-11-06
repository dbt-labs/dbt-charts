//! Prints the typify output for `board.schema.json` to stdout; run by
//! `chart_svg_dev/gen_board_types.py`, which owns writing `src/generated.rs`.

use typify::typespace::settings::ContainerType;
use typify::{TypeSpace, TypeSpaceSettings};

fn main() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../board.schema.json");
    let text = std::fs::read_to_string(path).expect("read board.schema.json");
    let schema = serde_json::from_str(&text).expect("parse board.schema.json");
    // An object keeps the order the board wrote: a pin map's order seats its colors.
    let mut settings = TypeSpaceSettings::default();
    settings.map_typespace_settings(|t| {
        t.with_map_type(ContainerType::hash_map().with_path("::indexmap::IndexMap"))
    });
    let mut space = TypeSpace::new(&settings);
    space.add_root_schema(schema).expect("typify board schema");
    let file = syn::parse2(space.to_stream().expect("render types")).expect("parse generated tokens");
    print!("{}", prettyplease::unparse(&file));
}
