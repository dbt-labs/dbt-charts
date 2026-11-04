"""PNG format conversion.

Stage: RENDER
Purpose: Convert SVG output to PNG using vl-convert.
"""

from dbt_charts.core.diagnostics.codes_render import (
    ERR_FORMAT_CONVERSION_FAILED,
    ERR_FORMAT_CONVERTER_UNAVAILABLE,
)
from dbt_charts.core.render.errors import FormatError
from dbt_charts.core.render.font_support import (
    normalize_svg_font_families_for_vl_convert,
    register_vl_convert_fonts,
)


def pattern_safe_scale(svg_content: str, scale: float) -> float:
    """Snap `scale` to the nearest multiple of 0.25 when the SVG carries a pattern fill.

    Tiles are multiples of 4 px, so only at such a scale does every tile edge land on a whole
    device pixel; at any other the repeat blurs across a pixel boundary. The engine's PNG
    export (`chart-renderer png`) applies the same rule.
    """
    if "<pattern" not in svg_content:
        return scale
    return max(round(scale * 4) / 4, 0.25)


def to_png(svg_content: str, scale: float = 1.0) -> bytes:
    """Convert SVG to PNG using vl-convert.

    Args:
        svg_content: SVG string to convert
        scale: Scale factor for the output image; snapped to a quarter step when the SVG
            carries a pattern fill (see `pattern_safe_scale`)

    Returns:
        PNG image as bytes

    Raises:
        FormatError: If conversion fails or dependencies missing
    """
    try:
        import vl_convert as vlc

        register_vl_convert_fonts(vlc)
        normalized_svg = normalize_svg_font_families_for_vl_convert(svg_content)
        return vlc.svg_to_png(
            normalized_svg, scale=pattern_safe_scale(svg_content, scale)
        )
    except ImportError:
        raise FormatError.from_code(
            ERR_FORMAT_CONVERTER_UNAVAILABLE, format="png"
        ) from None
    except FormatError:
        raise
    except Exception as e:
        raise FormatError.from_code(
            ERR_FORMAT_CONVERSION_FAILED,
            format="png",
            detail=str(e),
            alt_formats="HTML",
        ) from e
