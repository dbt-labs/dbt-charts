"""PDF format conversion.

Stage: RENDER
Purpose: Convert SVG output to PDF using vl-convert.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics.codes_render import (
    ERR_FORMAT_CONVERSION_FAILED,
    ERR_FORMAT_CONVERTER_UNAVAILABLE,
)
from dbt_charts.core.render.errors import FormatError
from dbt_charts.core.render.font_support import (
    normalize_svg_font_families_for_vl_convert,
    register_vl_convert_fonts,
)


def to_pdf(svg_content: str) -> bytes:
    """Convert SVG to PDF using vl-convert.

    Args:
        svg_content: SVG string to convert

    Returns:
        PDF document as bytes

    Raises:
        FormatError: If conversion fails or dependencies missing
    """
    try:
        import vl_convert as vlc

        register_vl_convert_fonts(vlc)
        normalized_svg = normalize_svg_font_families_for_vl_convert(svg_content)
        return vlc.svg_to_pdf(normalized_svg)
    except ImportError:
        raise FormatError.from_code(
            ERR_FORMAT_CONVERTER_UNAVAILABLE, format="pdf"
        ) from None
    except FormatError:
        raise
    except Exception as e:
        raise FormatError.from_code(
            ERR_FORMAT_CONVERSION_FAILED,
            format="pdf",
            detail=str(e),
            alt_formats="PNG or HTML",
        ) from e
