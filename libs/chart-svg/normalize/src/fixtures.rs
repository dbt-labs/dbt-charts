//! The host inputs normalize's tests share (cargo feature `fixtures`).

use crate::compile::HostInputs;
use crate::sources::SourceRegistry;
use board_model::cache::CacheLayer;
use board_model::fixtures as boards;
use std::collections::BTreeMap;

static NO_SOURCES: SourceRegistry = SourceRegistry::empty();
/// A project root that turns caching on and sets no ttl.
static ROOT: CacheLayer = CacheLayer {
    enabled: Some(true),
    ttl: None,
    prefetch: Some(false),
    refused: false,
};
static NO_SOURCE_CACHES: BTreeMap<String, CacheLayer> = BTreeMap::new();

/// The host inputs the tests share: the project root, a fixed day, the `clarity` theme, no pinned
/// ceiling, opaque SQL and no declared sources. A test varies one field by struct update.
pub fn host_inputs() -> HostInputs<'static> {
    HostInputs {
        boards_root: ".",
        today: (2026, 11, 6),
        default_theme: "clarity",
        html_policy_ceiling: None,
        sql: &boards::Opaque,
        sources: &NO_SOURCES,
        cache_root: &ROOT,
        source_caches: &NO_SOURCE_CACHES,
        named: false,
    }
}
