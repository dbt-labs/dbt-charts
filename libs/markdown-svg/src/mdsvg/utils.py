"""Utility functions for markdown-svg."""

from __future__ import annotations

import html
import re
from typing import List


def escape_xml(text: str) -> str:
    """
    Escape text for safe inclusion in XML/SVG, byte-exact under strict XML
    attribute-value normalization.

    Order matters: escape ``&<>"`` first, then replace whitespace, so the
    character references' own ``&`` isn't re-escaped.

    Args:
        text: Raw text string.

    Returns:
        XML-escaped string.
    """
    escaped = html.escape(text, quote=True)
    return escaped.replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#9;")


def escape_svg_text(text: str) -> str:
    """
    Escape text for inclusion in SVG text elements.

    This handles special characters that could break SVG rendering.

    Args:
        text: Raw text string.

    Returns:
        Escaped string safe for SVG.
    """
    # Escape XML entities
    result = escape_xml(text)
    # Preserve single spaces but collapse multiple spaces
    result = re.sub(r"  +", " ", result)
    return result


def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in text (collapse multiple spaces, strip edges).

    Args:
        text: Text with potentially irregular whitespace.

    Returns:
        Normalized text.
    """
    return " ".join(text.split())


def split_lines(text: str) -> List[str]:
    """
    Split text into lines, handling different line endings.

    Args:
        text: Text with line breaks.

    Returns:
        List of lines (without line ending characters).
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def format_number(n: float, precision: int = 2) -> str:
    """
    Format a number for SVG attribute output.

    Removes unnecessary trailing zeros and decimal points.

    Args:
        n: Number to format.
        precision: Decimal precision.

    Returns:
        Formatted number string.
    """
    if n == int(n):
        return str(int(n))
    formatted = f"{n:.{precision}f}"
    # Remove trailing zeros
    formatted = formatted.rstrip("0").rstrip(".")
    return formatted
