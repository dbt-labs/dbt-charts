---
name: custom-chart-build
kind: workflow
description: >
  Draw a chart shape no built-in type has with `type: custom`: an SVG template
  over the chart's query rows, rendered by the engine. Covers the authoring
  surface, the template header, every name the template sees (rows, style,
  variables, helpers), links, fonts, where the drawing lands, the allowed SVG,
  and the template dialect. Use when the user asks for a shape by name that no
  built-in type or composed recipe draws: a Sankey, funnel, gauge, radar, XmR
  chart, isotype pictogram, waffle. Do NOT use when a built-in type answers the
  question (see {{ s_skill_name_chart }}), or for styling an existing chart.
metadata:
  author: fivetran
---

# Custom chart build

A custom chart is a template that turns query rows into SVG. The engine
renders it, checks the output, and frames it like every other chart: the
title and subtitle above, the board's card padding around, the chart-root
`link:` honored. Reach for it last: a built-in chart gets axes, legends,
hover, sizing rules and visual warnings for free; a custom chart draws only
what its template draws.

## 1. Check what the project already has

```bash
dct describe templates/            # every template: description, channels, options
dct describe templates/funnel.svg.j2
dct examples charts/custom-inline  # a complete inline board to copy
```

A `template:` name with no file lists the templates the project does have.

## 2. Author the chart

```yaml
queries:
  stages:
    columns: [stage, visitors]
    values:
      - [Visited, 4200]
      - [Signed up, 3100]
      - [Upgraded, 740]
charts:
  signup_flow:
    type: custom
    query: stages
    # template: funnel          # templates/funnel.svg.j2, or the template inline
    svg: |                      # under svg: (exactly one of the two)
      {#---
      description: One bar per stage, each a link.
      channels:
        x: {}
        y: {}
      options:
        bar_height:
          type: integer
          default: 24
      ---#}
      {% set top = rows | map(attribute=channels.y) | max %}
      {% for r in rows %}
        <a href="{{ href(r) }}">
          <rect y="{{ loop.index0 * (options.bar_height + 8) }}" height="{{ options.bar_height }}"
                width="{{ r[channels.y] / top * width }}" fill="{{ style.muted }}"/>
        </a>
      {% endfor %}
    title: Where visitors go
    x: stage                    # channels sit at the chart root, as on any chart
    y: visitors
    options:
      bar_height: 24            # the template's own settings
    link: /boards/stage/{{ x }} # optional: a click-through, like every chart
    style:
      aspect_ratio: 2           # sizing only: aspect_ratio, min_height, max_height
rows:
  - signup_flow
```

Channels use the built-in names (`x`, `y`, `color`, `size`, `shape`, `theta`,
`value`); pick the role a built-in chart would use (a funnel's stage is `x`,
its count `y`; a waffle's parts are `color` and `theta`). Everything else the
template needs goes under `options:`. The look comes from the board's
`style:`, never from the chart.

Let the query do the math. Sorting, ranking, shares, running totals and
binning belong in SQL; the template loops and draws, mapping data to pixels
with `d3` scales rather than inline arithmetic.

## 3. Declare the header (template files)

A template file opens with a YAML header in a Jinja comment. It is the
contract the chart's channels and options are validated against at compile:

```jinja
{#---
description: Flow diagram from an edge list.
channels:
  x:
    description: Source node, then target node.
    multiple: true
  value:
    description: Size of each flow.
options:
  node_width:
    type: number
    default: 12
---#}
```

No `default` means required; `default: null` means optional (`none` in the
template). Option types: `column`, `columns`, `string`, `number`, `integer`,
`boolean`, `color`. An inline `svg:` template takes the same header; without
one it declares no channels or options and reads columns by name (`r.revenue`).

## 4. What the template sees

| Name | Holds |
|---|---|
| `rows` | The query result, one mapping per row. A chart with no `query:` gets `[]`. |
| `columns` | The result's column names, in order. |
| `channels` | Declared channel to column: `channels.x` is a column name, a list for a `multiple` channel, `none` when optional and unset. |
| `options` | The chart's options with the header's defaults filled. |
| `width`, `height` | The drawing box in pixels: inside the card padding, below the title. Draw from `(0, 0)` to `(width, height)`. |
| `uid` | A prefix unique to this chart; use it for every `id` (`id="{{ uid }}-clip"`) so two charts on one board never collide. |
| `style` | The board's merged style tree, every color a hex value: `style.font.family`, `style.font.color`, `style.muted`, `style.tones.positive`, `style.charts.color.categorical.palette`, `style.charts.axis.grid.color`, `style.palettes.<role>` (a list of colors, or a mapping of stops for a tone). |
| `variables` | The board's variable values for this render (`variables.region`). |
| `math` | `sin`, `cos`, `tan`, `asin`, `acos`, `atan2`, `sqrt`, `log`, `exp`, `floor`, `ceil`, `pi`, `tau`. Radians. Write a negative angle as `-1 * math.pi / 2`: `-math.pi` negates `math`. |
| `d3` | d3's scales, ticks, path generators and pie layout, in snake_case (below). |

Helpers:

| Helper | Returns |
|---|---|
| `value \| format('.1%')` | The value through a d3-format spec, the same specs `format:` takes elsewhere. |
| `value \| format(column=channels.y)` | The value through the format the board resolved for that column: the query's `formats:`, `style.formats`, the number locale and currency, the way a KPI prints it. Prefer it to a `value_format` option that only restates the column's format; keep an option only when the template should print something different. A spec and `column=` together is an error, as is a column the query did not return. Numbers only: a date or text value under `column=` is an error. With no rows there are no columns: wrap a derived total in `{% if rows %}`. |
| `text_width(text, size, weight)` | Pixel width of `text` in the board's font at `size`; `weight` optional (`600`, `"bold"`, or any weight `style` carries). Measure labels instead of guessing. |
| `color_for(value, column)` | The board's pinned color for that column's value (`style.charts.category_colors`), else the next categorical palette color; stable within a chart. |
| `href(row)` | The chart's `link:` for that row, every `{{ channel }}` placeholder filled from the row. |
| `raise_error(message)` | Stops the render and shows `message` in the chart's place. Use it for data the template cannot draw. |

`d3` replaces the arithmetic a template would repeat, and draws with the same
numbers the built-in charts use. Arguments go by position or keyword; a
zero-width domain, a non-numeric value or an unknown curve fails the chart
with an error naming the call.

| `d3` function | Returns |
|---|---|
| `d3.scale_linear(domain, range)` | A scale for two-number lists. `x(v)` maps, `x.invert(px)` maps back, `x.domain` and `x.range` read the ends, `x.nice(count=10)` returns a new scale with round ends, `x.ticks(count=10)` the tick list, `x.tick_format(count=10, spec=none)` a function printing a value at the ticks' precision through the board's number locale (`spec` is a d3-format spec). |
| `d3.scale_sqrt(domain, range)` | The same, range proportional to the square root of the value: radius from a value so area follows it. |
| `d3.scale_band(domain, range, padding=0, padding_inner=0, padding_outer=0, align=0.5)` | Names to bands: `b(name)` is the band start, `b.bandwidth()` the width, `b.step()` the pitch; `padding=` sets both paddings. A name outside the domain is an error. |
| `d3.line(points, curve="linear")`, `d3.area(points, y0, curve="linear")` | A path `d` through `[x, y]` pairs; `area` closes down to the line at `y0`. Curves: `linear`, `monotone_x`, `monotone_y`, `step`, `step_before`, `step_after`, `basis`, `natural`, `cardinal`. |
| `d3.arc(inner_radius, outer_radius, start_angle, end_angle, pad_angle=0, corner_radius=0)` | A sector path `d` centered on the origin; angles in radians clockwise from twelve o'clock; place it with `transform="translate(cx, cy)"`. |
| `d3.pie(values, start_angle=0, end_angle=math.tau, pad_angle=0)` | A list of `{index, value, start_angle, end_angle, pad_angle}` in data order (no default sort: order in the query), ready for `d3.arc`. |

Names are strict: a misspelled column, option or style key fails the chart
and names the template line. Output is escaped, so a label with `<` draws as
text.

## 5. Links

Set `link:` on the chart like any other chart:

- A link with no `{{ channel }}` placeholder wraps the whole drawing in one
  anchor; the template draws nothing extra.
- A link with placeholders (`/boards/stage/{{ x }}`) is per row: the template
  wraps the marks it wants clickable in `<a href="{{ href(r) }}">...</a>`. A
  template that never calls `href` under such a link fails the chart.
  Board paths resolve against the host; `http`, `https` and `mailto` pass
  through; anything else is refused.

## 6. Fonts and theme

Read fonts and colors from `style` rather than writing them in:
`font-family="{{ style.font.family }}"` paints the board font, and an export
embeds that font file exactly as it does for built-in charts. A template
that reads only `style` follows every theme, dark ones included.

## 7. Where the drawing lands

The engine draws the title and subtitle, then places the template's output
in a group translated below them, inside the card's padding. `width` and
`height` are that box, so a template never allows for the title or padding
itself. Output outside that box is clipped. A template that wants the full
card height omits `title:`.

## 8. What the output may contain

Well-formed SVG from drawing elements only: shapes (`rect`, `circle`,
`ellipse`, `line`, `polyline`, `polygon`, `path`), text (`text`, `tspan`,
`textPath`), grouping (`g`, `defs`, `symbol`, `use`), paint
(`linearGradient`, `radialGradient`, `stop`, `pattern`, `clipPath`, `mask`,
`marker`), `title` and `desc` (a `<title>` inside a shape is a native browser tooltip, not the board's tooltip card),
and `a` with an `href` the link rules above accept.

Refused, never stripped (`ERR-CUSTOM-SVG` names the construct): `script`,
`foreignObject`, `style`, `image`, `filter` (shadow and blur and raster images
stay unsupported: vector-only, sandboxed output), animation elements, `on*` attributes, the
board's own hooks (`role`, `aria-*`, `data-*`, classes starting with `role-`
or `dbt-`), a paint attribute that is not a color or `url(#id)`, an `href` or
`url()` that is not `#local` outside `<a>`, `javascript:`, CSS escapes and
loading functions, nesting past 64 levels, comments, processing instructions,
CDATA, DOCTYPE.

## 9. The template dialect

The engine runs a Jinja dialect (minijinja), so write filters, not Python
methods:

| Instead of | Write |
|---|---|
| `text.split(",")` | `text \| split(",")` |
| `d.get(k, 0)` | `d[k] if k in d else 0` |
| `d.keys()`, `d.values()` | `d \| items \| map(attribute=0)`, `... attribute=1` |
| `x \| round(0, "ceil")` | `math.ceil(x)` |
| `"%d" % n` | `n \| format('d')` |

Supported as in Jinja: `set`, `for` with `loop.index0`, `if`, `macro`,
`namespace()`, `dict()` with `**` merging, `range`, `string`, `int`, `float`,
`list`, `length`, `sort(attribute=...)`, `map`, `selectattr`, `rejectattr`,
`groupby`, `unique`, `max`, `min`, `sum`, `join`, `replace`, `upper`,
`lower`, `round(n)`, `abs`, the `~` concatenation and `//` floor division.
Integers are 128-bit, not arbitrary precision. A runaway loop fails the chart
on an instruction budget, and output is capped by the project's
`execution.max_template_output_bytes`.

## 10. Verify

```bash
dct validate charts/flow.yml   # header, channels, options, template syntax
dct render charts/flow.yml     # the drawing, under the default theme
```

Render once more under a dark theme (`theme: neon`) to confirm nothing is
hard-coded. Check the four error codes if a render fails:
`ERR-CUSTOM-TEMPLATE` (file, header or syntax), `ERR-CUSTOM-FIELDS`
(channels or options against the header), `ERR-CUSTOM-RENDER` (the template
while drawing), `ERR-CUSTOM-SVG` (the output).
