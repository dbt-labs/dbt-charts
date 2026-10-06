# compile/migrations

Recognizes which retained board grammar a document was authored against and
rewrites it to the current one.

- `prepare_board_mapping` — runs on every board load (`parse/parser.py`,
  `compiler.py`) and on patch-shaped fragments (`merge.py`). Migrates **in
  memory**, never touches the file. The only automatic path; Cloud has no
  other.
- `migrate_board_yaml_text` — `dct migrate`, same declarations, rewriting the
  file.

Declarations live in `versions/`, one module per boundary, including the
unreleased one — named for the manifest's DEV entry (e.g. `v0_7_0.py`), not a
`current` placeholder; its docstring is the per-change record. Frozen schemas
are package data under `dbt_charts/data/schemas/yaml/`.

## Implementation philosophy

**`versions/*.py` — the DEV boundary module included — is schema changes only. Nothing
else goes in there.** A key renamed, a key removed, or an authored value's
meaning changing in place (the unmigratable-redefinition case below) — that
is the entire list of things that earn a docstring bullet. A renderer-default
tweak, a bug fix, a performance change, a visual behavior change, an internal
refactor: none of these are grammar changes, and **none of them get an entry
here, ever, no matter how significant the change feels.** If nothing an
author could have typed changed meaning, it does not belong in `versions/`.
Document it in the task or the PR description instead. Before adding a
bullet, name the `Move`, `Deletion`, or in-place redefinition it documents —
if you can't, delete the bullet, not add it.

**Every grammar change ships its migration in the same PR** — key renamed,
key removed, value meaning changed. Done means a board written against the
previous grammar still renders, not that the models validate.

**Prove it with a test, both ways.** A migration claim is a test; so is a
claim that something *cannot* be migrated. Twice now an untested "this would
corrupt open-map keys" has stood in for the fixture that would have settled
it. Building a `MigrationRegistry` checks nothing: whether your declaration
is coherent against the schemas it names is decided by `validate_declarations`
(`tests/core/compile/_migration_declarations.py`), in CI.

**Prefer a change recognition can see.** `_recognize` reads the document, not
a version marker. Renaming a key or retiring a value leaves evidence;
redefining one **in place does not**, and is unmigratable at any cost — a
board authoring `format: currency` is byte-identical before and after the
meaning flips. Introduce the new spelling and retire the old one.

**When an in-place redefinition is unavoidable, it still owes the author a
signal** — a docstring entry in the DEV boundary module like every declarable
change, plus a diagnostic wherever the old intent is *detectable*. This is
about an authored value whose meaning moved under it. A renderer default
that changes what an *unauthored* chart looks like is not a redefinition of
anything the board wrote, and does not belong in `versions/`. The
worked example is `RETIRED_FORMAT_SUCCESSORS`
(`core/diagnostics/hints.py`): a successor map naming the replacement, and a
deliberate refusal to fuzzy-match, because the nearest spelling to
`currency_compact` is `currency_whole` — which compiles clean and silently
drops compaction. Silence is the failure mode here, not a crash.

**Renames go through `suffix_rename_moves`**, never a hand-enumerated path
list. Not exhaustive: `_relative_field_paths`' `seen` guard makes a
self-nested `AuthoredBoard` opaque, so a sub-board declaring its own `charts:`
map is outside the resolved set.

**A resolved path is not permission to rewrite; the gate is.** Its `*` came
from one declared union arm while application is arm-blind, so wherever an
open map sits beside a declared field the final segment can land on a key the
author *named*. `move_source_locations` gates every yield:
`_plausible_positions` (identity — required props and an agreeing `type`,
honoring the union's `if`/`then`), `_declares_tail` (declared properties
only, never `additionalProperties` — the distinction `Deletion` draws), and
`_open_map_claims` (breaks the board-field/chart-id tie by *value*, which
identity cannot). Read `additionalProperties` from **both** grammars: a
position can gain the arm after the freeze (`GridItem.item`).

**Identity, not validity.** Never `_matching_positions` here — it demands
whole-node validity, which a mid-migration document lacks (retired fields
carry retired *value shapes*).

**The text rewriter replays, it does not re-derive.** `_apply_deletions`
returns the concrete paths it struck; `_delete_paths_in_yaml_text` resolves
those through the YAML node tree. Any rule the scanner re-derives from key
names is a second rule that drifts — that is what the `staged` equality guard
kept catching.

**Never hand a composition node to `_declares_tail`.** It re-expands through
the ungated `_schema_branches`, re-admitting every arm the walk just gated.
`_without_composition` strips the keywords.

**An unreachable position strands the whole board**, not just its own chart:
the post-migration check fails and the original mapping is returned. The
`extra="forbid"` hint in `parse/yaml_error_formatter.py` is the fallback and
survives declaring a `Move` — it still fires there and past the six-month
cutoff (`_enforce_support_window`).

### If you catch yourself thinking…

| Objection | Response |
|---|---|
| "open maps mean an author could name a query `description` — a rename would corrupt it" | Real hazard, answered by the gate, not by the path's shape. Sweep for the positions rather than guessing: read `additionalProperties` from the live grammar as well as the frozen one, or `grid.items.*.item` stays invisible. |
| "it's ambiguous, so fail loud with a good hint instead" | Fail-loud is the fallback for positions a move cannot reach, never the plan for a change untested against the resolver. |
| "it's just a default flip, not a grammar change" | Ask what the board authored. If an authored value now means something else, it is a grammar change and needs a new spelling — in-place redefinition cannot be recognized. If nothing authored changed meaning and only the default for charts that authored nothing moved, that is renderer behavior: it belongs in the PR and the task, not in `versions/`. |
| "the tail collides with a live sibling, so it can't be a Deletion" | A `Deletion` is positional. `_live_declares_tail` fires it only where the current grammar has stopped declaring the tail, so `("style", "color")` strips a board's dead override and leaves eight chart families' working one. Four keys shipped fail-loud on the global tail check alone. |
| "`dct migrate` covers it" | Nothing runs `dct migrate` for a Cloud user. |
| "the mapping-level test passes" | `migrate_mapping` skips the currency check, the fallbacks, and the whole-document gate. Pin it at `compile()`. |
