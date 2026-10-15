# markdown-svg

> The missing markdown-to-SVG library for Python

Convert Markdown to SVG with automatic text wrapping. Zero dependencies.

**[🚀 Try the Live Playground](https://markdown-svg-production.up.railway.app/)** — see it in action, no installation required!

📝 [Read the blog post](https://thingsilearned.com/things/markdown-svg) for background on the challenges and how they were solved.

## Why This Library?

SVG has no native text flow or wrapping. If you need to embed formatted text in SVG (for dashboards, diagrams, exports, or anywhere else), you've had to manually position every line. Until now.

**markdown-svg** parses your Markdown and renders it as properly wrapped, styled SVG.

## Installation

This library ships as a peer package inside the `dbt-charts` wheel (it is not published to PyPI on its own):

```bash
pip install dbt-charts
```

## Quick Start

```python
from mdsvg import render

# Render markdown to SVG
svg = render("""
# Hello World

This is a paragraph with **bold** and *italic* text.

- Item one
- Item two
- Item three
""")

# Save to file
with open("output.svg", "w") as f:
    f.write(svg)
```

## Features

- **Headings** (h1-h6)
- **Paragraphs** with automatic word wrapping
- **Inline formatting**: bold, italic, bold+italic, inline code
- **Links** (rendered as styled text, clickable in SVG viewers)
- **Lists**: unordered (bullets) and ordered (numbers)
- **Code blocks** with background styling (syntax highlighting coming soon)
- **Blockquotes** with styled left border
- **Horizontal rules**
- **Tables** with headers and alignment
- **Images** (as `<image>` elements)
- **Zero dependencies** - pure Python

## API

### Basic Rendering

```python
from mdsvg import render, measure

# Render with default settings
svg = render("# Hello World")

# Customize width and padding
svg = render("# Hello World", width=600, padding=30)

# Measure dimensions without rendering
size = measure("# Hello\n\nLong paragraph...", width=400)
print(f"Height needed: {size.height}px")
```

### Structured Result (for Composing SVGs)

When you need to embed mdsvg output in larger SVG compositions, use `render_content()` to get the SVG elements without the wrapper, along with the actual dimensions:

```python
from mdsvg import render_content, RenderResult

# Get structured result
result: RenderResult = render_content("# Hello World", width=400)

result.elements     # SVG elements (rects, text, etc.) without style block
result.style_block  # The <style>...</style> CSS block
result.content      # Combined: style_block + elements (backwards compatible)
result.width        # 400.0
result.height       # Actual rendered height

# Convert to full SVG when needed
svg = result.to_svg()  # Full SVG with wrapper

# Embed in a larger SVG composition
large_svg = f"""
<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">
  <rect fill="#f0f0f0" width="800" height="600"/>
  <g transform="translate(50, 100)">
    {result.content}
  </g>
</svg>
"""
```

When composing multiple sections, use `elements` and `style_block` separately to avoid duplicating the CSS:

```python
# Compose multiple sections with a single style block
left = render_content("# Section 1\n\nLeft content", width=350)
right = render_content("# Section 2\n\nRight content", width=350)

combined = f"""
<svg xmlns="http://www.w3.org/2000/svg" width="750" height="{max(left.height, right.height)}">
  {left.style_block}
  <g transform="translate(0, 0)">{left.elements}</g>
  <g transform="translate(400, 0)">{right.elements}</g>
</svg>
"""
```

> **Note:** When composing multiple sections, use the same `Style` object for all `render_content()` calls to ensure consistent styling.

### Custom Styling

```python
from mdsvg import render, Style

style = Style(
    # Fonts
    font_family="Georgia, serif",
    mono_font_family="'Courier New', monospace",
    base_font_size=16.0,
    line_height=1.6,

    # Colors
    text_color="#333333",
    heading_color="#111111",
    link_color="#0066cc",
    code_color="#c7254e",
    code_background="#f9f2f4",

    # Heading scales
    h1_scale=2.25,
    h2_scale=1.75,
)

svg = render("# Styled Heading", style=style)
```

### Text Alignment

Control horizontal text alignment using the `text_align` option:

```python
from mdsvg import render, Style

# Center-aligned text (great for titles in dashboards)
style = Style(text_align="center")
svg = render("# Centered Title", style=style)

# Right-aligned text
style = Style(text_align="right")
svg = render("Right aligned content", style=style)

# Default is left-aligned
style = Style(text_align="left")
```

### Built-in Themes

```python
from mdsvg import render, DARK_THEME, GITHUB_THEME, LIGHT_THEME

# Use dark theme
svg = render("# Dark Mode", style=DARK_THEME)

# Use GitHub-style theme
svg = render("# GitHub Style", style=GITHUB_THEME)
```

### Style Presets

Style presets optimize spacing for different contexts. Themes control colors, presets control margins:

```python
from mdsvg import render, COMPACT_PRESET, MINIMAL_PRESET, DOCUMENT_PRESET

# Default behavior (generous whitespace for documents/articles)
svg = render("# Title", style=DOCUMENT_PRESET)

# Compact spacing for dashboards, cards, and UI components
svg = render("# Title", style=COMPACT_PRESET)

# Minimal spacing for tooltips and tight spaces
svg = render("# Title", style=MINIMAL_PRESET)
```

Combine presets with themes using `merge_styles()`:

```python
from mdsvg import render, merge_styles, COMPACT_PRESET, DARK_THEME

# Compact spacing + dark colors
style = merge_styles(COMPACT_PRESET, DARK_THEME)
svg = render("# Dashboard Title", style=style)
```

| Preset | heading_margin_top | heading_margin_bottom | paragraph_spacing |
|--------|-------------------|----------------------|-------------------|
| `DOCUMENT_PRESET` | 1.5em | 0.5em | 12px |
| `COMPACT_PRESET` | 0.3em | 0.3em | 8px |
| `MINIMAL_PRESET` | 0.1em | 0.1em | 4px |

### Working with the AST

```python
from mdsvg import parse, render_blocks

# Parse to AST (for inspection or modification)
blocks = parse("# Hello\n\nWorld")
# Returns: [Heading(level=1, ...), Paragraph(...)]

# Render pre-parsed blocks
svg = render_blocks(blocks, width=400)
```

### Modifying Styles

```python
from mdsvg import Style

# Create a base style and modify it
style = Style()
dark_style = style.with_updates(
    text_color="#e0e0e0",
    code_background="#2d2d2d",
)
```

## Text Width Estimation

By default, this library estimates text width using character-based heuristics. This works well for common system fonts but may be less accurate for unusual fonts.

### Precise Measurement (Default)

Text measurement uses `fonttools` for accurate width calculation:

```python
from mdsvg import FontMeasurer, create_precise_wrapper

# Use system font
measurer = FontMeasurer.system_default()
width = measurer.measure("Hello World", font_size=14)

# Or use precise text wrapping
wrap = create_precise_wrapper(max_width=300, font_size=14, measurer=measurer)
lines = wrap("Long text that needs accurate wrapping...")
```

### Custom Fonts

Use any TTF/OTF font file for measurement:

```python
from mdsvg import FontMeasurer

# From your project's fonts directory
measurer = FontMeasurer("./fonts/MyFont-Regular.ttf")

# Or from system font directories
measurer = FontMeasurer("/Library/Fonts/Arial.ttf")  # macOS
measurer = FontMeasurer("C:/Windows/Fonts/arial.ttf")  # Windows
```

**Recommended font locations:**
- Project directory: `./fonts/MyFont.ttf`
- macOS user fonts: `~/Library/Fonts/`
- Linux user fonts: `~/.local/share/fonts/`
- Windows user fonts: `C:\Users\<user>\AppData\Local\Microsoft\Windows\Fonts\`

### Google Fonts

Download fonts from Google Fonts automatically:

```python
from mdsvg import download_google_font, FontMeasurer

# Downloads and caches the font
font_path = download_google_font("Inter")
measurer = FontMeasurer(font_path)

# With specific weight (400=regular, 700=bold)
bold_path = download_google_font("Inter", weight=700)

# Popular Google Fonts that work well:
# - Inter (modern sans-serif)
# - Roboto (Android default)
# - Open Sans (highly readable)
# - Lato (elegant sans-serif)
# - Source Code Pro (monospace)
```

## Full Style Options

| Option | Default | Description |
|--------|---------|-------------|
| `font_family` | system-ui, sans-serif | Primary font stack |
| `mono_font_family` | ui-monospace, monospace | Font for code |
| `base_font_size` | 14.0 | Base font size in pixels |
| `line_height` | 1.5 | Line height multiplier |
| `text_color` | #1a1a1a | Default text color |
| `heading_color` | None | Heading color (falls back to text_color) |
| `link_color` | #2563eb | Link color |
| `link_underline` | True | Whether to underline links |
| `code_color` | #be185d | Inline code text color |
| `code_background` | #f3f4f6 | Code background color |
| `blockquote_color` | #6b7280 | Blockquote text color |
| `h1_scale` - `h6_scale` | 2.0 - 0.9 | Heading size multipliers |
| `paragraph_spacing` | 12.0 | Space between paragraphs (px) |
| `list_indent` | 24.0 | List indentation (px) |
| `char_width_ratio` | 0.48 | Average character width ratio |
| `text_align` | "left" | Horizontal text alignment ("left", "center", "right") |

## Playground

Try markdown-svg in your browser with the interactive playground:

**🚀 [Live Demo and Playground](https://markdown-svg-production.up.railway.app/)** — no installation required!

Or run locally:

```bash
git clone https://github.com/davefowler/markdown-svg
cd markdown-svg
uv run python playground/server.py
# Open http://localhost:8765
```

Features:
- Live preview as you type
- Customize styling via JSON
- Load example markdown files
- Download rendered SVGs

See [playground/README.md](playground/README.md) for details and API documentation.

## Examples

See the [examples/](examples/) directory for more usage examples:

- `basic.py` - Simple rendering
- `custom_style.py` - Custom styling and themes
- `measure_and_render.py` - Measuring before rendering

The [playground/examples/](playground/examples/) directory contains markdown files showcasing various features.

## Development

```bash
# Clone and install
git clone https://github.com/davefowler/markdown-svg.git
cd markdown-svg
uv sync --extra dev

# Common commands
uv run python playground/server.py   # Run the playground
uv run pytest tests/                 # Run tests
uv run ruff check src/ tests/        # Run linter
uv run pyright                       # Run type checker
```

Inside the dbt charts monorepo, the equivalent recipes are `just markdown_svg play`, `just markdown_svg test`, and `just pyright`.

## Roadmap

Planned enhancements:

- **Syntax Highlighting** - Colorized code blocks using Pygments with VS Code theme support. See [notes/SYNTAX_HIGHLIGHTING_PLAN.md](notes/SYNTAX_HIGHLIGHTING_PLAN.md) for details.

## Related Projects

- This is the only Python library (that we know of) for rendering Markdown directly to SVG
- For HTML output, see [markdown](https://pypi.org/project/Markdown/), [mistune](https://pypi.org/project/mistune/), or [markdown-it-py](https://pypi.org/project/markdown-it-py/)
