"""Version-migration module for the unreleased 0.5.0 -> current boundary.

Declares the pending structural changes since the 0.5.0 freeze. Authored as
``versions/current.py`` while unreleased (the ``catalog.latest.version ->
current`` boundary); renamed to ``versions/v<new_version>.py`` at release time
with no content edit, same convention as ``v0_4_0.py``/``v0_5_0.py``.

THIS FILE IS SCHEMA CHANGES ONLY. Do not add an entry here for anything that
is not a key rename, a key removal, or an authored value's meaning changing
in place (the unmigratable-redefinition case, which still owes the author a
signal per ``migrations/AGENTS.md``). A renderer-default tweak, a bug fix, a
performance change, a visual behavior change, an internal refactor -- none of
these are grammar changes, and none of them get a bullet here, no matter how
significant. If you cannot point to the ``Move``, ``Deletion``, or in-place
redefinition backing your bullet, it does not belong in this file. Put it in
the task or the PR description instead. See ``migrations/AGENTS.md``'s
"If you catch yourself thinking..." table.

Changes in this release:

- ``variables.<name>.data_type`` redefined in place: it was an unvalidated
  free-text hint ("informational; not consumed"), and is now the type
  contract for what a ``select`` / ``radio`` / ``multiselect`` sends back —
  ``number``, ``date`` and ``boolean`` convert the value before it reaches
  SQL; ``string`` and ``array`` remain accepted and inert. Every value the
  Looker migrator or an in-repo board ever authored (``string``, ``number``,
  ``boolean``, ``array``) keeps parsing, so no rewrite applies. A board that
  authored any other spelling now fails validation naming the field and the
  five values, and a ``number``/``date``/``boolean`` that disagrees with the
  variable's ``options.static`` or ``default`` is refused at compile rather
  than failing the first query — both are the diagnostic this redefinition
  owes, and both are the only places the old, inert intent is detectable.

- ``variables.<name>.options.static`` with every option a number now types
  the value the control sends back as a number, where it used to bind the
  text the browser sent. A board that filters a *text* column through a
  numeric-looking option list (zip codes, account codes) authored nothing
  new and now binds an integer; Postgres and BigQuery reject that
  comparison. The YAML is byte-identical before and after, so nothing can
  recognize it; the opt-out is ``data_type: string`` on that variable.

- ``variables.<name>.options.static`` mixing numbers and strings
  (``[2023, 2024, All]``) is retired: the list is now the type contract for
  the values that come back, and a mixed one names no type. Parsing refuses
  it with ``ERR-VALIDATION-FIELD`` naming the field; the rewrite is by hand
  (quote every option, or drop the odd one and leave the variable unset for
  the no-filter choice). No in-repo board authored a mixed list.

- ``style.support_table.position`` (also reachable per chart type and per
  chart) widened from ``top | bottom`` to ``top | bottom | left | right``.
  Pure addition: every board authoring ``top`` or ``bottom`` keeps its exact
  meaning and needs no rewrite. ``left``/``right`` are new spellings, valid
  only when the chart's category axis is vertical (a horizontal bar);
  authoring them on a horizontal category axis (vertical bar/line/area), or
  ``top``/``bottom`` on a vertical one, raises at render time rather than
  silently remapping. The shipped theme default changed from the literal
  ``top`` to unset (resolved at render time to ``top`` on a horizontal
  category axis, or the side the category labels are on for a vertical one) —
  behaviorally identical for every existing board, since ``top`` was the only
  reachable default before this change.

- ``editorial-10`` retained its public name while its ten colors, slot order,
  companions, and alias targets were refreshed. Numeric tokens such as
  ``editorial-10.3``, theme-relative brackets such as ``category[3]``, and
  named roles such as ``category.sage`` remain valid spellings but now
  resolve to the refreshed colors. Schema recognition cannot distinguish old
  intent from current intent because the authored strings are byte-identical,
  so there is no automatic rewrite or targeted diagnostic. Named roles remain
  the stable way to preserve semantic hue intent across future slot reorders;
  they do not pin an exact color value.

  ``solid`` briefly carried the same rebind (its five categorical roles onto
  the editorial family instead of vivid) earlier in this same unreleased
  window; it is moot now that ``solid`` is one of the two themes deleted
  below, so there is nothing left for a board to have observed it.

- Theme set collapsed from seven built-in themes to five. ``plain.yaml`` and
  ``solid.yaml`` are deleted outright — neither carried any adoption or
  distinct design intent once ``solid``'s single-series ink diverged into a
  three-line board override rather than a theme. ``editorial`` is renamed
  ``clarity`` (the shipped default — ``SHIPPED_DEFAULT_THEME_NAME``).
  ``cream`` is renamed ``paper``. ``stark`` is untouched — still a
  schema-offered pick and still the structural root every other built-in
  theme transitively extends.

  ``theme:`` *is* expressible as a ``Move``, despite carrying no declared key
  path of its own: it validates against ``ThemeName``, a real JSON Schema
  enum property. But ``theme:`` never
  *disappears* from any grammar (it is permanent authoring sugar, unlike a
  genuinely retired field), so this cannot be an ordinary rename: old_path and
  new_path are both ``("theme",)``, an identity-path ``Move`` whose
  ``value_map`` does the renaming's job instead of the path. Because the key
  never disappears, the *key being present* is never evidence a document is
  old the way it is for every other declared ``Move`` — only the *value*
  found there is (``migrations.py``'s ``move_source_locations``, gated by
  ``_identity_value_would_change``). ``THEME_VALUE_MAP`` below covers the
  four retired names above plus every current ``ThemeName`` value — see its
  comment for why the current names need entries too: a value the map sends
  to itself is never migrated, only one it sends elsewhere is.

  That value-only signal has a second consequence: an identity-path Move
  cannot go through the ordinary schema-diff ``_recognize`` machinery at
  all (it needs no proof the *document* is old, only that this one *value*
  is retired), so it runs before any currency check, gated only by a cheap
  ``_theme_value_needs_recheck`` filter that skips building the migration
  registry entirely when the raw ``theme:`` value is absent or already
  current — see ``_apply_identity_moves`` and ``prepare_board_mapping`` in
  ``migrations.py``.

  ``extends:`` gets the identical value-only rename for a scalar entry —
  ``extends: cream`` migrates the same as ``theme: cream`` — but *not*
  through the same ``theme:`` ``Move``, and not through
  ``_apply_identity_moves`` either. Unlike ``theme:``, ``extends:``'s
  authored type (``ThemeName | str | list[str]``) makes even a *retired*
  literal validate against the generic ``str`` arm — nothing in the JSON
  Schema, past or present, can ever distinguish a retired name from a
  project-relative board name there, so no amount of schema tightening
  makes the value visible to ``_current_schema_rejections``. Worse, a plain
  ``extends:`` string is genuinely ambiguous with a real project board of
  the same name (``extends: cream`` legitimately resolving to a project's
  own ``cream.yaml``) in a way ``theme:`` never is, so rewriting it
  unconditionally on the raw mapping — safe for ``theme:``, since that key
  can *only* ever mean a theme — would silently break a working board whose
  author happens to have named a board after a since-retired theme.

  So ``extends:`` resolution lives one layer down, in the merge/extends
  engine (``merge.py``'s ``_retired_theme_redirect``, reached from
  ``_resolve_entry``), where the ambiguity is already being resolved: it
  runs before ``_resolve_entry`` falls through to ``_resolve_fragment_file``
  (the site that would otherwise raise ``ERR-EXTENDS-UNRESOLVED``), and
  internally re-checks the same "does a project board of this name exist"
  condition ``_resolve_fragment_file`` checks, via the one shared helper
  both call — so a real project board still wins even though the redirect
  is asked first. Applying it there — as one entry in a chain instead of
  one Move over the whole document — also covers the list form for free:
  ``extends: [cream, ./frag.yaml]`` resolves each entry independently, so
  the retired name migrates and the path ref is untouched, with no separate
  mechanism needed. ``THEME_RENAMES`` and ``THEME_VALUE_MAP`` happen to hold
  the same four entries today (see the comment above ``THEME_VALUE_MAP`` for
  why they stay two separate tables regardless), so both lanes redirect
  ``cream`` to ``paper`` — ``THEME_RENAMES`` because it is the only entry
  in scope for ``extends:``, ``THEME_VALUE_MAP`` because ``paper`` is also
  ``cream``'s current ``theme:`` landing.

  This mechanism has no reach outside a real project: the bare in-memory
  ``compile()`` lane never invokes ``merge.py``'s extends resolution for a
  plain entry at all (see ``_theme_from_extends``'s docstring — "nothing
  folds a chain outside ``compile_file``'s root board"), so
  ``extends: cream`` there still gets the loud unknown-theme failure and
  needs hand-editing. A nested sub-board's own ``theme:``/``extends:``
  (``rows.*.theme``, ``cols.*.theme``, ``grid.items.*.item.theme``,
  ``tabs.items.*.theme``, at any depth) is unreached by either mechanism for
  the same reason as every other position-scoped ``Move`` in this file:
  neither ``_apply_identity_moves`` nor ``_retired_theme_redirect`` walks
  the document tree looking for one — ``theme:``'s Move is hand-declared at
  the document root only, and ``_resolve_entry`` only ever sees the entries
  its caller's own extends chain hands it. A retired name at one of these
  positions fails loud instead, ``ERR-UNKNOWN-THEME`` with no migration
  warning, the same as any other never-valid ``theme:``/``extends:`` value
  there.

  ``axis.line.color`` stays set on every remaining theme (``stark``,
  ``clarity``, ``paper``, ``vivid``, ``neon``) rather than being centralized
  into ``_base.yaml``: ``_base.yaml`` fixes the axis line's fill to
  ``transparent`` (paired with ``axis.line.visible: false``, off by
  default), and that shared default would silently no-op the authored,
  documented ``style.axis.line.color`` override the moment a board turns
  ``axis.line.visible`` on — the same YAML drew a visible line before this
  release. Each theme keeps its own concrete color so that authoring stays
  live. Not a board-schema change, so it carries no migration entry of its
  own.

- ``vivid-10`` retained its public name, its ten colors, and its companion
  set while its slot order changed: brown moved from slot 4 to slot 8, gold
  and moss each shifted one slot earlier, and orange two (7 to 5). Numeric
  tokens such as ``vivid-10.4``, theme-relative brackets such as
  ``category[4]``, and the four positionally-paired companions
  (``-dark``/``-light``/``-ghost``/``-ink``) all remain valid spellings but
  now resolve to a different color.
  Named roles such as ``category.brown`` re-point with the slot and keep their
  hue. Same situation as the editorial-10 refresh above and the same outcome:
  the authored strings are byte-identical before and after, so schema
  recognition cannot distinguish old intent from current intent, and there is
  no automatic rewrite or targeted diagnostic.

  Unlike the editorial-10 refresh, no hex value changed — a board that pinned
  a vivid-10 color by literal hex is unaffected, and only slot- or
  bracket-addressed references move.

- MetricFlow support removed from core (task:
  remove-metricflow-support-from-core-archive-the-excised-implementation).
  The removal deletes three fields no longer accepted anywhere in the
  authored schema:

  - chart-level ``model:`` (semantic-layer chart sugar — was declared on
    every chart family's shared base except CalloutChart) — auto-stripped by
    ``dct migrate``; authors should remove the key and either write an
    explicit ``query:`` or delete the chart.
  - ``Variable.model`` / ``Variable.dimension`` / ``Variable.measure``
    (MetricFlow option-source bindings; never consumed by any code path —
    authored-but-inert) — auto-stripped by ``dct migrate``. ``Variable.model``
    shares the one-segment ``("model",)`` tail with the chart sugar above.

  ``type: metricflow`` itself is a discriminated-union tag, not a field, so
  it is not expressible as a ``Deletion`` (the mechanism strips declared key
  chains, not enum/tag values) — a board authoring ``type: metricflow`` (or
  bare ``metrics:``/``dimensions:`` inferring it) is not recognized as an old
  grammar by this boundary and instead fails the current schema directly,
  same as the LookML pull-out's ``type: lookml`` before it.

- row_height, default_width, default_height removed from GridLayout. All three
  were schema-valid and inert: ``row_height`` reached the normalized layout and
  no render path ever read it, and the two ``default_*`` spans were never even
  copied — grid item spans default to 1 unconditionally. Auto-stripped by
  ``dct migrate`` **when authored without** ``gap``: a board carrying all four
  cannot reach the current schema (``gap`` below has no Deletion), so the
  migration cannot finish and the original mapping is reported instead — the
  author sees one unknown-field error per key rather than one for ``gap``. That
  is the canonical five-key block this release deletes, so it is the likely
  shape, not an edge case.

  ``gap`` was removed from GridLayout for the same reason (the normalized value
  is discarded at resolve, which reads ``style.layout.grid.gap`` instead) but is
  fail-loud: the parser raises an unknown-field error hinting at
  ``style.layout.grid.gap``. Declarable but unconverted — the tail also matches
  that live key, so it is legal only root-anchored (``validate_declarations``'s
  ``retired_at_root``), with ``_live_declares_tail`` holding the firing off the
  layout slot. The enum → pixel mapping is not lossless either way, so the
  successor is a hint rather than a value a migration could carry over.

- ``width``/``color``/``dash_array``/``line_cap``/``dash_offset`` removed from
  four ``border:`` slots that typed the full ``BorderStyle`` but whose renderer
  reads only ``radius``: ``variables.input.border``,
  ``charts.table.spark.columns.border``, ``charts.table.spark.bar.border``, and
  ``charts.spark_bar.border``. Each is narrowed to a radius-only ``CornerStyle``
  (task: borderstyle-slots-that-only-apply-radius-accept-inert-width-color-and-dash).
  Tails are slot-qualified (``("input", "border", "width")``, not a bare
  ``("border", "width")``) because ``border.<field>`` is suffix-matched
  repo-wide and several other slots (``TableStyle.border``,
  ``charts.marks.bar.border``, ``VariablesStyle.border``, ...) keep the full
  ``BorderStyle`` — an unqualified tail would strip those too. The spark-bar
  table cell needs the extra ``spark`` qualifier (``("spark", "bar", "border",
  ...)``) since a bare ``("bar", "border", ...)`` also matches the live
  ``charts.marks.bar.border``.

  Not covered: a chart-local style override on a ``type: spark_bar`` chart
  (``charts.<id>.style.border.<field>``) shares the same ``SparkBarChartStyle``
  Patch class as the board-level ``style.charts.spark_bar.border`` slot but has
  no ``spark_bar`` segment in its own path, so it cannot be slot-qualified
  without also matching every other chart-local ``style.border.<field>``.
  Fail-loud, and declarable but unconverted like ``grid.gap`` above:
  ``chart_type="spark_bar"`` is the scope it needs.

- KPI text styling reconciliation (task: kpi-text-styling-style-color-is-a-
  family-outlier-tones-live-in-the-wrong-family-and-align-is-hardcoded).
  Two related schema changes, only one of them declarable as a migration:

  - ``KpiTonesStyle`` relocated from ``style.charts.kpi.tones`` to board-level
    ``style.tones`` (a sibling of ``style.palettes``) — table conditional glyphs
    read the same tones and had to reach across chart families for them.
    Declared via ``suffix_rename_moves`` below, which yields 2 ``Move``
    entries: the board root and ``tabs.items.*``.

    **That coverage is partial.** ``_relative_field_paths`` checks its
    ``seen`` ancestor set at entry to each model, so a self-referential model
    (``AuthoredBoard`` reachable from itself through ``rows``/``cols``/
    ``grid.items.*.item``) yields fields only the first time it is entered on
    a path — a nested sub-board (``rows.*.tones``, ``cols.*.tones``,
    ``grid.items.*.item.tones``, any depth) is never walked, so ``tones``
    inside one is uncovered. ``charts.*.style.tones`` is uncovered too, for
    an unrelated reason: a chart-scoped override cannot become a board-level
    key without widening what it paints, so it is not losslessly movable in
    any case.

    Partial is safe: no input half-migrates, though the two uncovered shapes
    reach that outcome by different paths.

    * **Mixed** (covered scope *and* an uncovered nested-sub-board scope):
      the moves run, the residual re-check at ``migrate_mapping`` raises
      ``IncompleteMigrationError``, and ``prepare_board_mapping`` warns
      ("could not finish migrating") and returns the original mapping.
    * **Uncovered-only**: ``_recognize`` matches no declared transition and
      raises ``UnsupportedSchemaError`` before any move runs;
      ``prepare_board_mapping`` returns the original **silently**. The author
      sees ``extra_forbidden`` on retired syntax with no hint that a migration
      was skipped — a real rough edge, not a loud abort.

    Widening the coverage means changing how ``_relative_field_paths`` tracks
    self-nesting depth — not hand-listing paths: no version module writes a
    literal ``Move``, and hand-enumeration is a technique this repo has never
    used. Widening it is unattempted here, not known-safe: nothing in the
    tree pins the nested-position behavior a depth-tracking walker would
    have to preserve, so a retry needs its own coverage before it can be
    trusted. Left alone pre-1.0: no external board corpus authors ``tones`` inside a nested
    sub-board, and the silent path above is the only cost.

    ``dct migrate``'s text-preserving rewriter (``migrate_yaml_text``) is
    narrower by design (its own module docstring: only scalar block-mapping
    moves or same-position key renames — a non-scalar move to a different
    parent is out of scope rather than risk reformatting the file through a
    YAML dump/load round trip). So a board authoring ``tones`` at either
    covered position compiles and loads fine on the in-memory Move, while
    ``dct migrate`` raises ``MigrationError`` and the author relocates
    ``tones:`` by hand. ``test_kpi_tones_style_migration.py`` pins all of
    these outcomes.
  - ``kpi.style.color`` (the family's sole bare-string ``color`` — every
    other family types ``style.color`` as an object) is a ``Deletion`` scoped
    to ``chart_type="kpi"`` (``DELETED_CHART_FAMILY_TAILS``). The tail
    ``("style", "color")`` still matches the live ``style.color`` on bar,
    line, area, scatter, heatmap, pie, table, and geo, so the scope is what
    makes the declaration legal; ``_live_declares_tail`` is what keeps the
    walk off those families' working keys. It carries a ``reason`` naming the
    ``style.value.font.color`` replacement, since the value is dropped rather
    than relocated (``font.color`` is live, so redirecting a bare kpi color
    onto it would change the render rather than preserve it).

    The theme-level ``style.charts.kpi.color`` (a *different* field —
    ``KpiChartStylePatch.color`` in 0.5.0, not the chart-root sugar above) is
    a plain ``Deletion`` in ``DELETED_TAILS``, anchored at ``charts``: its
    tail collides with nothing live. It is not an inert key — it painted the
    value text — so it carries a ``reason`` naming both the successor and the
    fact that the board's kpi text changes color until an author moves it.

- ``font``/``border`` removed from the seven board-level chart-family style
  slots that have no per-chart card to paint them onto: ``charts.bar``,
  ``charts.line``, ``charts.area``, ``charts.scatter``, ``charts.histogram``,
  ``charts.heatmap``, and ``charts.pie`` (task: font-and-border-are-a-dead-
  per-family-style-path-on-cartesian-pie-and-donut). Both fields validated,
  cascaded, and were then discarded — these families emit via Vega-Lite (or a
  hybrid VL+SVG composition for pie/donut) with no card surface distinct from
  the board frame, and no renderer ever read either value. Auto-stripped by
  ``dct migrate``.

  Tails are anchored at ``charts`` (``("charts", "bar", "font")``, not a bare
  ``("bar", "font")``) because both key names are heavily reused in the style
  tree and a bare tail collides with live slots: ``("bar", "border")`` also
  matches ``charts.marks.bar.border`` (``BarMarkStyle`` keeps a full border)
  and ``("bar", "font")`` also matches ``charts.table.spark.bar.font``. Six of
  the seven families would have been safe unqualified, but the ``charts``
  anchor is applied uniformly so the set can be audited as one slot shape
  rather than six exceptions and one special case. ``donut`` needs no tail: it
  is a chart ``type:`` only, styled through ``charts.pie``, and has no
  board-level slot of its own in any released grammar.

  Not covered: the chart-local position (``charts.<id>.style.font`` /
  ``charts.<id>.style.border``). It shares the narrowed per-family Patch
  classes but has no family segment in its own path, so the only available
  tails are ``("style", "font")`` and ``("style", "border")`` — both still
  live (the board-frame ``style.border``, and ``style.font`` on kpi/table/
  callout). Fail-loud, and declarable but unconverted like ``grid.gap`` and the
  spark_bar chart-local border above: per-family ``chart_type`` scoping is what
  confines these — the gate never sees them, since the ``type:`` check short-
  circuits first. The parser raises an unknown-field error naming the field and
  saying it is unsupported on that chart family.

- ``axis_quantitative`` removed from heatmap's per-family style surface (task:
  surface-accepts-axis-fields-a-family-cannot-honor). Heatmap's axes are both
  nominal — its magnitude lives on the color channel, not on either axis — so
  it has no quantitative axis for this field to style. It validated and
  cascaded like every other cartesian family's ``axis_quantitative``, but
  never reached anything a heatmap renders, the same "accept and silently do
  nothing" shape the font/border removal above fixes.

  The **theme-level** slot, ``style.charts.heatmap.axis_quantitative``, is
  covered by a ``Deletion`` (``("charts", "heatmap", "axis_quantitative")``,
  anchored at ``charts`` for the same reason as the font/border tails above —
  a bare ``("axis_quantitative",)`` tail still matches the live
  ``style.charts.<family>.axis_quantitative`` slot on the five other
  cartesian families). Auto-stripped by ``dct migrate``.

  The **chart-level** slot (``charts.<id>.style.axis_quantitative``) carries
  no family segment of its own, so its only available tail — ``("style",
  "axis_quantitative")`` — still matches every other cartesian family's live
  chart-local override. Same shape as ``kpi.style.color`` above and declared
  the same way: ``chart_type="heatmap"`` in ``DELETED_CHART_FAMILY_TAILS``,
  with a ``reason`` naming ``axis_band``, the field an author reaches for
  instead. The parser's unknown-field hint for the position stays — it is
  what an author sees where migration cannot finish.

- ``description:`` renamed to ``notes:`` on every object that carried
  non-rendering prose — board, chart (every family that carried it;
  ``CalloutChart`` never did and has no ``notes`` today), query (every type),
  grid item, tab item, and variable (task:
  rename-the-non-rendering-description-field-to-note-on-boards-charts-queries-and-layout-items).
  Declared via ``suffix_rename_moves`` in ``NOTES_RENAMES`` below, same
  mechanism as ``TONES_RENAMES`` and ``SUPPORT_TABLE_RENAMES``.

  The open-map objection this rename attracts — ``queries``/``charts``/
  ``variables`` are open maps, so an author may legitimately *name* a query
  ``description``, and renaming that key would leave its ``query:
  description`` reference dangling — is real, and is answered by the schema
  gate in ``move_source_locations`` rather than by the resolved path's shape.
  A ``*`` is not self-protecting. It consumes the author's identifier only
  where it is a *map key* (``queries.*.description``); where it is a list
  index it does not, and the identifier sits one level below it. Both
  ``rows.*.description`` (a row item authored as ``dict[str, AuthoredChart]``
  — the ``*`` is the index, the chart id is the final segment) and
  ``rows.*.*.description`` (a sub-board's own ``queries:`` map) corrupted on
  disk before the gate existed. The gate yields a location only where the
  source grammar declares the final segment as a property there, with
  ``_open_map_claims`` breaking the board-field/chart-id tie by value.
  ``test_description_notes_migration.py`` pins the top-level, row-item, and
  sub-board cases and their surviving references.

  A position no move reaches is not a partial failure but a whole-board one:
  the post-migration document check fails, ``prepare_board_mapping`` returns
  the original mapping, and every *other* field in that board is reported
  unmigrated too.

  The reachable case is a **sub-board that declares its own** ``charts:`` /
  ``queries:`` **map**. ``_relative_field_paths``' ``seen`` guard makes a
  self-nested ``AuthoredBoard`` opaque to the walk, so
  ``rows.*.charts.*.description`` is not among the 29 resolved positions —
  and a board carrying one strands its *top-level* ``description:`` too. That
  board was valid 0.5.0, so this is the one shape where the rename does not
  repair what it should; the ``extra="forbid"`` hint is what the author gets
  instead. Same limitation as ``SUPPORT_TABLE_RENAMES`` below, restated here
  because this is where it costs something.

  ``CalloutChart`` strands a board the same way but is not a real case: it
  declares no ``notes``, and declared no ``description`` in 0.5.0 either, so
  no valid 0.5.0 board reaches it.

  ``_CHARTREF_KEYS`` keeps the literal spelling ``"description"`` alongside
  ``"notes"`` — that guard polices the retired ``chart:`` reference form and
  must still recognize the historical spelling to emit its targeted message.

  The ``extra="forbid"`` hint naming ``notes:``
  (``_extra_field_diagnostic`` in ``compile/parse/yaml_error_formatter.py``)
  stays: it still fires past the transparent-migration cutoff, and at the
  self-nesting positions the resolved move set does not reach (same caveat as
  ``SUPPORT_TABLE_RENAMES`` below).

- ``data_table:`` renamed to ``support_table:`` on every position that carried it.
  The old name described the attachment after the one thing it
  structurally is not: it renders no header, no rule, no row shading and no
  column separators, unlike the standalone ``type: table`` chart. Declared via
  ``suffix_rename_moves`` in ``SUPPORT_TABLE_RENAMES`` below, same mechanism as
  ``TONES_RENAMES`` — one tail-only tuple resolves to every structurally
  matching position in both frozen schemas rather than a hand-enumerated list.

  ``support_table`` collides with no live field of the same name (KPI's
  ``support`` row is a distinct tail), so this bare tail is unambiguous and
  resolves cleanly wherever it structurally exists in both frozen schemas: the
  chart-level attachment (``charts.<id>.data_table``, on bar/line/area, plus
  the same field on a chart authored inline under ``rows``/``cols``/
  ``grid.items.*.item`` instead of the ``charts:`` map), the chart-local style
  override (``charts.<id>.style.data_table``), and every board- and
  theme-level style slot carrying the per-chart-type ``InheritSlot`` mixin
  (``style.charts.data_table`` and the per-family overrides
  ``style.charts.{bar,line,area,scatter,histogram,heatmap}.data_table``).
  ``test_support_table_migration.py`` pins the full resolved position set —
  all 36 of them, by path — rather than this prose re-deriving it by hand.

  Same self-nesting caveat as ``TONES_RENAMES`` above, and it starts at the
  *first* level: ``_relative_field_paths`` returns ``()`` on re-entry into
  ``AuthoredBoard``, so a sub-board nested under ``rows``/``cols``/
  ``grid.items.*.item`` that declares its own ``charts:`` map
  (``rows.*.charts.*.data_table``) is not among the resolved moves and reports
  ``Unknown field 'data_table'`` unmigrated. Inline charts at those positions
  (``rows.*.data_table``, ``rows.*.*.data_table``) are covered. A tab's own
  scope (``tabs.items.*.*``) is a distinct model from ``AuthoredBoard`` and
  stays covered at every position, same as tones.

- A layer's own ``axis_y.label:`` (tick-label format patch, ``LayerAxisYLabel``
  in 0.5.0) renamed to ``axis_y.labels:`` (``LayerAxisYLabels`` now), so the
  layer-level ``axis_y`` grammar spells its tick-label field the same way as
  the chart-level ``style.axis_y.labels`` (``BaseAxisStyle.labels``) — the one
  field name the two ``axis_y`` grammars disagreed on (task:
  rename-layer-axis-y-label-to-labels-for-chart-level-naming-parity).
  Declared via ``suffix_rename_moves`` in ``AXIS_Y_LABEL_RENAMES`` below, with
  a two-segment tail (``("axis_y", "labels")`` -> ``("axis_y", "label")``)
  rather than the bare one-segment tail ``TONES_RENAMES``/``SUPPORT_TABLE_RENAMES``/
  ``NOTES_RENAMES`` use.

  ``suffix_rename_moves`` matches candidates by the *new* tail (``labels``),
  not the old one, so a bare tail was never a risk to ``TypedLayerBase.label``
  specifically — that field's own path ends in ``label`` (singular), which a
  ``("labels",)`` tail does not match at all. The real over-match a bare tail
  invites is every *other* live field ending in ``labels``: chiefly the
  chart-level ``style.axis_x.labels`` / ``style.axis_y.labels``
  (``BaseAxisStyle.labels``, present at many chart-family and tier
  positions). Each candidate's substituted old path is still checked against
  the frozen 0.5.0 schema before becoming a ``Move`` — and the chart-level
  field was already spelled ``labels`` in 0.5.0 too, never ``label`` — so
  every one of those candidates is dropped there. A bare and an anchored tail
  therefore resolve to the identical 11-position move set for this rename;
  the two-segment anchor is kept for the same self-documenting hygiene the
  ``DELETED_TAILS`` anchors above use, not because it is load-bearing here.

  Same self-nesting caveat as ``TONES_RENAMES``/``SUPPORT_TABLE_RENAMES``
  above: a sub-board nested under ``rows``/``cols``/``grid.items.*.item``
  that declares its own ``charts:`` map (``rows.*.charts.*.layers.*.axis_y.
  label``) is not among the resolved moves and reports the field unmigrated.

- A query's inline ``source: <path>`` ref is now resolved against the project
  root as well as the board's own directory, where it was board-relative
  only. No board that rendered keeps a different meaning: a path that exists
  at the board-relative location still resolves there, a path that exists
  nowhere still fails at execution as before, and a path that exists only
  at the root, which used to fail, now resolves. The one redefinition is a
  board whose ref names a file present at *both* locations (say
  ``charts/sales/data/orders.parquet`` and ``data/orders.parquet`` for a
  board in ``charts/sales/``): it used to resolve board-relative and now
  fails compile with ``ERR-FILE-SOURCE-AMBIGUOUS`` naming both, the only
  place the old intent is detectable. The rewrite is by hand — remove or
  rename one copy. No in-repo board authors that layout.
"""

from __future__ import annotations

from typing import get_args

from dbt_charts.core.compile.migrations.migrations import (
    Deletion,
    MappedScalar,
    Move,
    YamlKeyPath,
    suffix_rename_moves,
)
from dbt_charts.core.compile.models.schema_names import ThemeName
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
)

# Retired built-in theme names that map identically whichever key holds them
# -- `theme:` sugar or an `extends:` entry. Hand-written, not derived: the
# *old* ThemeName values (cream, editorial, plain, solid) no longer exist
# anywhere in source — generate_schema_names.py regenerated ThemeName in the
# same release that retired them, so there is no live artifact left to walk.
# `stark` was never retired (it kept its name across this boundary), so it
# has no entry here.
#
# Consulted directly by `merge.py`'s `_retired_theme_redirect` for the
# `extends:` lane. It must hold *only* retired names: an identity entry for
# a live theme (e.g. `"clarity": "clarity"`) would make every
# `extends: clarity` redirect-and-warn as if `clarity` were retired --
# `_retired_theme_redirect` treats any present key as evidence of
# retirement, unlike `THEME_VALUE_MAP` below.
THEME_RENAMES: dict[MappedScalar, MappedScalar] = {
    "editorial": "clarity",
    "plain": "clarity",
    "solid": "clarity",
    "cream": "paper",
}

# `value_map` for the `theme:` Move below must be total over every value this
# Move can ever be asked to map -- not just the retired names. `theme:` is
# permanent authoring sugar (see the module docstring), so a Move on it is
# necessarily an identity-path Move (old_path == new_path == ("theme",)); the
# positional gate that fires it (`move_source_locations`) only checks that the
# `theme` key is present, never what value it holds. A board already authoring
# a *current* name (`theme: vivid`) is normally never routed through this Move
# at all -- `_recognize` short-circuits to the DEV version before checking
# any transition once the whole document already validates. But a board carrying
# `theme: vivid` *and* some unrelated retired construct (an old `data_table:`,
# say) is recognized at the "0.5.0" boundary for that unrelated reason, and
# every Move declared for that boundary runs, this one included. Without an
# identity entry for `vivid`, that board would fail with "Migrate this field
# manually" for a theme value that was never wrong. So the map covers
# THEME_RENAMES's retired names union the current schema's theme enum
# (`ThemeName`) -- together, everything `theme:` was ever allowed to hold.
# This is why THEME_VALUE_MAP stays a separate table from THEME_RENAMES
# rather than the two collapsing into one: THEME_RENAMES's contract (retired
# names only, see the comment above it) and this one's contract (total over
# every legal `theme:` value, including identity entries) are incompatible
# in the same dict.
THEME_VALUE_MAP: dict[MappedScalar, MappedScalar] = {
    **THEME_RENAMES,
    **{name: name for name in get_args(ThemeName)},
}

DELETED_TAILS: tuple[YamlKeyPath, ...] = (
    # Chart-level model: sugar — removed with MetricFlow, the only remaining
    # consumer after the LookML pull-out.
    ("model",),
    # Variable.dimension / Variable.measure — MetricFlow option-source
    # bindings, never consumed.
    ("dimension",),
    ("measure",),
    # Inert grid sizing keys — nothing read them; grid rows size to content and
    # spans come from the items.
    ("grid", "row_height"),
    ("grid", "default_width"),
    ("grid", "default_height"),
    # BorderStyle -> CornerStyle narrowing on four radius-only slots. Each
    # slot-qualified so the tail can't also strip a fully-applied border
    # elsewhere (see the module docstring).
    ("input", "border", "width"),
    ("input", "border", "color"),
    ("input", "border", "dash_array"),
    ("input", "border", "line_cap"),
    ("input", "border", "dash_offset"),
    ("columns", "border", "width"),
    ("columns", "border", "color"),
    ("columns", "border", "dash_array"),
    ("columns", "border", "line_cap"),
    ("columns", "border", "dash_offset"),
    ("spark", "bar", "border", "width"),
    ("spark", "bar", "border", "color"),
    ("spark", "bar", "border", "dash_array"),
    ("spark", "bar", "border", "line_cap"),
    ("spark", "bar", "border", "dash_offset"),
    ("spark_bar", "border", "width"),
    ("spark_bar", "border", "color"),
    ("spark_bar", "border", "dash_array"),
    ("spark_bar", "border", "line_cap"),
    ("spark_bar", "border", "dash_offset"),
    # font/border removed from the seven board-level chart-family style slots
    # with no per-chart card to paint them onto. Anchored at ``charts`` so the
    # tail can't also strip a live marks.bar.border / table.spark.bar.font
    # (see the module docstring).
    ("charts", "bar", "font"),
    ("charts", "bar", "border"),
    ("charts", "line", "font"),
    ("charts", "line", "border"),
    ("charts", "area", "font"),
    ("charts", "area", "border"),
    ("charts", "scatter", "font"),
    ("charts", "scatter", "border"),
    ("charts", "histogram", "font"),
    ("charts", "histogram", "border"),
    ("charts", "heatmap", "font"),
    ("charts", "heatmap", "border"),
    ("charts", "pie", "font"),
    ("charts", "pie", "border"),
    # Theme-level style.charts.heatmap.axis_quantitative -- heatmap has no
    # quantitative axis (both axes are nominal). Anchored at `charts` so the
    # tail can't also strip the live style.charts.<family>.axis_quantitative
    # slot on the five other cartesian families (see the module docstring).
    ("charts", "heatmap", "axis_quantitative"),
    # Theme-level style.charts.kpi.color — KpiChartStylePatch.color, the
    # family's own slot, not the chart-root sugar below. Anchored at
    # ``charts`` for the same reason as the tails above.
    ("charts", "kpi", "color"),
)

# Tails retired on one chart family while a same-named sibling stays live on
# the others. `Deletion.chart_type` scopes the declaration; `_live_declares_tail`
# is what stops the walk stripping the families that kept theirs.
DELETED_CHART_FAMILY_TAILS: tuple[tuple[YamlKeyPath, str], ...] = (
    # kpi's sole bare-string `color` — every other family types style.color as
    # an object. Replaced by style.value.font.color.
    (("style", "color"), "kpi"),
    # heatmap has no quantitative axis (both are nominal); the five other
    # cartesian families keep their chart-local override.
    (("style", "axis_quantitative"), "heatmap"),
)

# KpiTonesStyle moved from charts.kpi.tones to board-level tones, shared with
# table conditional-formatting glyphs. `suffix_rename_moves` resolves this to
# the two positions a hand-written list would also name (board root and
# `tabs.items.*`) — see the module docstring for why nested sub-boards are
# uncovered. It is used anyway so that a newly reachable `style:` position is
# picked up here without anyone remembering to edit this file. Same mechanism
# as v0_5_0's style.board relocation.
TONES_RENAMES: tuple[tuple[YamlKeyPath, YamlKeyPath], ...] = (
    (("tones",), ("charts", "kpi", "tones")),
)

# data_table -> support_table: a straight name swap at every position that
# carried the old field, chart-level attachment and style slots alike (see
# the module docstring for the exact set of positions this resolves to).
SUPPORT_TABLE_RENAMES: tuple[tuple[YamlKeyPath, YamlKeyPath], ...] = (
    (("support_table",), ("data_table",)),
)

# description -> notes: a straight name swap at every position that carried
# non-rendering prose (see the module docstring for why the open-map key
# position is not among them).
NOTES_RENAMES: tuple[tuple[YamlKeyPath, YamlKeyPath], ...] = (
    (("notes",), ("description",)),
)

# A layer's own axis_y.label -> axis_y.labels. Anchored two segments deep for
# self-documenting hygiene, not because a bare tail is unsafe here -- see the
# module docstring for why both forms resolve to the identical move set.
AXIS_Y_LABEL_RENAMES: tuple[tuple[YamlKeyPath, YamlKeyPath], ...] = (
    (("axis_y", "labels"), ("axis_y", "label")),
)


def moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    """Return Move objects for the 0.5.0 -> current boundary."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    renamed_field_moves = suffix_rename_moves(
        AuthoredBoard,
        source_schema,
        target_schema,
        TONES_RENAMES + SUPPORT_TABLE_RENAMES + NOTES_RENAMES + AXIS_Y_LABEL_RENAMES,
        catalog=catalog,
    )
    # `theme:` is authoring sugar with no backing Pydantic field (see
    # `desugar_theme`), so `suffix_rename_moves`'s field-tree walk can never
    # discover it -- it only finds real declared fields. Hand-declared at the
    # document root only: a nested sub-board's own `theme:` key
    # (`rows.*.theme`, `grid.items.*.item.theme`, ...) is a different document
    # position this Move does not reach. See the module docstring.
    theme_move = Move(
        source_schema,
        target_schema,
        ("theme",),
        ("theme",),
        value_map=THEME_VALUE_MAP,
    )
    return renamed_field_moves + (theme_move,)


def _card_style_deletion_reason(tail: YamlKeyPath) -> str | None:
    """Reason text for the board-level ``charts.<family>.font``/``border`` tails.

    The other ``DELETED_TAILS`` entries are inert keys nothing ever read and
    need no explanation. These fourteen are different: the family they name
    genuinely has no card surface to paint the field onto, which is exactly
    the fact the chart-local position's own unknown-field hint gives the
    author (see ``_card_style_slot_chart_type`` in ``yaml_error_formatter``)
    — this mirrors that wording for the board-level position, which cannot
    raise the same error because its presence is what identifies the board
    as pre-migration.

    Gated on ``_CARD_STYLE_UNSUPPORTED_CHART_TYPES`` (the same set the hint
    uses), not merely on tail shape: a future ``("charts", "kpi", "font")``
    tail would match the shape too, but kpi has a card, so it must not carry
    this reason.
    """
    from dbt_charts.core.compile.parse.yaml_error_formatter import (
        _CARD_STYLE_UNSUPPORTED_CHART_TYPES,
    )

    if len(tail) != 3 or tail[0] != "charts" or tail[2] not in ("font", "border"):
        return None
    family = tail[1]
    if family not in _CARD_STYLE_UNSUPPORTED_CHART_TYPES:
        return None
    return (
        f"{family} has no per-chart card to paint it onto (it renders via "
        "Vega-Lite with no card surface separate from the board frame). The "
        "field had no effect and has been removed."
    )


_RETIRED_PAINT_REASONS: dict[YamlKeyPath, str] = {
    ("style", "color"): (
        "kpi's style.color is gone; style.value.font.color paints the value "
        "text instead. Every other chart family keeps its own style.color, "
        "which types as an object rather than a bare color string."
    ),
    ("style", "axis_quantitative"): (
        "heatmap has no quantitative axis to style: both of its axes are "
        "nominal, and the magnitude lives on the color channel. Use "
        "style.axis_band for the axes it does have."
    ),
    ("charts", "heatmap", "axis_quantitative"): (
        "heatmap has no quantitative axis to style: both of its axes are "
        "nominal. Use style.charts.heatmap.axis_band for the axes it does "
        "have."
    ),
    ("charts", "kpi", "color"): (
        "style.charts.kpi.color is gone; style.charts.kpi.value.font.color "
        "paints the value text instead. This was a real theme override, so "
        "the board's kpi text takes its inherited color until you move it."
    ),
}


def deletions(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Deletion, ...]:
    """Return Deletion objects for the 0.5.0 -> current boundary."""
    return tuple(
        Deletion(
            source_schema,
            target_schema,
            tail,
            reason=_card_style_deletion_reason(tail)
            or _RETIRED_PAINT_REASONS.get(tail),
        )
        for tail in DELETED_TAILS
    ) + tuple(
        Deletion(
            source_schema,
            target_schema,
            tail,
            reason=_RETIRED_PAINT_REASONS[tail],
            chart_type=chart_type,
        )
        for tail, chart_type in DELETED_CHART_FAMILY_TAILS
    )
