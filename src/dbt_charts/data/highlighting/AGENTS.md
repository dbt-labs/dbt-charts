# Syntax highlighting

`board.json` is the board highlight manifest — generated from the schema IR by
`just gen-highlight-artifacts` (`scripts/gen_highlight_artifacts.py`) and the single source
of truth every dbt charts editor surface derives its highlighting from:

| Surface | Consumes | Derived artifact |
|---|---|---|
| VS Code extension | `top_level_keys`, `enum_values_by_key`, `sql_block_scalar_keys`, `sql_block_scalar_parents` | `apps/vscode-extension/syntaxes/dbt-charts.tmLanguage.json` (generated) |
| Cloud + Playground web editors | `sql_block_scalar_keys`, `sql_block_scalar_parents` | `libs/codemirror-dbt-charts/src/{language,highlight}.ts` (hand-written, manifest passed in) |
| Docs site (Pygments) | `sql_block_scalar_keys`, `sql_block_scalar_parents` | `src/dbt_charts/integrations/highlighting.py` (hand-written, reads the manifest) |

`sql_block_scalar_keys` names keys whose `|`/`>` body is SQL wherever they appear
(`query`, `sql`). `sql_block_scalar_parents` is the structural rule for the
`queries.<name>: |` shorthand: a block scalar that is a *direct child* of the top-level
mapping under one of these keys is SQL, whatever its key is named. A block scalar two
levels down, or under a `queries:` that is not top-level, is not.

Both derived artifacts live in the monorepo, not in this package.

Renderers live in `src/dbt_charts/core/compile/schema/renderers/`
(`highlight_manifest.py`, `textmate_grammar.py`). Generated artifacts are never hand-edited
— change the renderer and regenerate.

## Implementation philosophy

- **Color by YAML type, not by quoting.** `title: Foo` and `title: "Foo"` are the same
  value and get the same color. A plain scalar that isn't a number, boolean, null, anchor,
  or alias is a *string* and is scoped as one. A tokenizer that emits no token for plain
  scalars is a bug — half the file renders as unthemed default text.
- **Structure outranks content.** Keys read first and values sit under them; a key is never
  colored as the string it happens to contain. Pure punctuation (`:`, `-`) is the exception —
  it recedes, because the reader infers it from layout. Save saturated color for the
  distinctions YAML wrong-foots the reader on: `no` is a boolean, `1.10` is a float,
  `"1.10"` is not.
- **Multi-line plain scalars carry their scope onto every continuation line.** Coloring
  line 1 of a folded `notes:` and dropping to default text on line 2 is worse than
  coloring neither. Ship a multi-line test case with every tokenizer change.
- **Token classes are the contract across surfaces.** The same board resolves to the same
  classes in VS Code and the web editors; change one without the other and the two dbt charts
  products disagree about what a board looks like. Pinned by
  `libs/codemirror-dbt-charts/tests/tmlanguage-parity.test.ts` (value position) and
  `test_committed_textmate_grammar_in_sync` — both in the monorepo, alongside the
  artifacts they pin.
- **Grammars emit classes; the theme layer picks colors.** No hex in a grammar or tokenizer.
  On the web that theme layer is a CodeMirror `HighlightStyle`, so the palette is literal hex
  in `libs/codemirror-dbt-charts/src/highlight.ts` — that file *is* the theme, and it is the
  only place in the package allowed a color. Don't route it through CSS custom properties:
  that hands the palette to two apps independently, with unstyled text as the failure mode.
