# Generated skill pages

`dct skills chart|styling|theme` print pages that are rendered from the engine,
not written by hand.

Regenerate (after any model, CLI, registry or allowlist change):

    uv run python dbt-charts/scripts/gen_skill_pages.py

`dbt-charts/tests/ai/skills/test_gen_skill_pages.py` fails when a committed
`ai/skills/<name>/SKILL.md` differs from the generator's output.

## What lives where

- `<name>.md.j2`: hand-written text only: the frontmatter, the orientation
  paragraph, the Finish loop, a few one-line rules that no model, CLI or
  registry states, and the section order.
- `allowlist.yml`: names only. It picks which keys, commands, chart types,
  codes and themes the pages show in full, and in what order. It carries no
  description, type, default or value; a name the engine no longer has fails
  generation with the list of names it does have.
- `../gen_skill_pages.py`: reads the schema IR (`introspect()`), the Typer app,
  the diagnostics `REGISTRY`, the predefined-format and palette registries and
  the Jinja helper factories, and fills the template.

A field's line is `` `name` (type, default D): first sentence of its model
description``. A first sentence over 12 words is cut at its first `;` or `:`,
and generation fails if that is still too long: shorten the description in the
model, where the schema and the docs read it too.

## Allowlist grammar

    objects.<name>.path    Model.field.field ...; `chart:bar` starts at a chart type's
                           model; `field=Model` picks one of several nested models
    objects.<name>.label   heading shown for the object (its YAML path)
    objects.<name>.keys    fields shown with a description line, in order; [] lists
                           every field by name only
    objects.<name>.detail  keys shown with their whole description
    objects.<name>.also    false drops the "Also exist" line (for a subset view of
                           an object another page lists in full)
    chart_types            per chart type: keys it takes
    commands               one row each: `line` (an example line quoted in the
                           command's help) or `args`+`flags` (checked against the
                           command's real arguments and options); text comes from
                           `option` (that option's help) or `pick` ([paragraph,
                           sentence] pairs of the command's help)
    rules                  cross-field constraints a model declares, picked by a
                           substring of the validator's message
    codes                  diagnostics-registry codes: summary and fix text
    formats                sample value and the format names printed against it
