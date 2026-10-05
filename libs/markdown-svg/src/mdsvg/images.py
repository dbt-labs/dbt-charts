"""Image utilities for fetching dimensions and URL mapping."""

from __future__ import annotations

import base64
import binascii
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.parse import unquote_to_bytes, urlparse
from xml.etree import ElementTree

# Type alias for URL mapping functions
ImageUrlMapper = Callable[[str], str]


@dataclass(frozen=True)
class ImageSize:
    """Dimensions of an image, both strictly positive.

    A zero is never a size: a raster header declaring one is corrupt (raises
    here), and an SVG without a pixel size is unsized (None, not an ImageSize).
    """

    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError(
                f"Image header declares a zero dimension ({self.width}x{self.height})"
            )

    @property
    def aspect_ratio(self) -> float:
        """Width / height ratio."""
        return self.width / self.height


def get_image_size(
    url: str,
    base_path: Optional[str] = None,
    timeout: float = 10.0,
) -> Optional[ImageSize]:
    """
    Get the dimensions of an image from a local file or remote URL.

    For local files, reads just the header bytes to extract dimensions
    without loading the full image. For remote URLs, fetches the minimum
    bytes needed to determine dimensions.

    Args:
        url: Image URL or local file path.
        base_path: Base directory for resolving relative paths.
        timeout: Timeout in seconds for remote requests.

    Returns:
        ImageSize with width and height, or None if dimensions couldn't be determined.

    Raises:
        ValueError: For a structurally corrupt `data:` URI (no ',' separator,
            invalid base64, an `image/svg+xml` payload that is not XML, or a
            raster header declaring a zero width or height). A payload that is
            merely unmeasurable returns None like any other source, and a local
            file or remote URL returns None for a corrupt header too.

    Example:
        >>> size = get_image_size("/path/to/image.png")
        >>> if size:
        ...     print(f"{size.width}x{size.height}")

        >>> size = get_image_size("https://example.com/image.jpg", timeout=5.0)
    """
    parsed = urlparse(url)

    # Inline payload — must not reach the filesystem, where a long base64 body
    # trips ENAMETOOLONG instead of reporting a missing file.
    if parsed.scheme == "data":
        return _get_data_uri_image_size(url)

    # Check if it's a remote URL
    if parsed.scheme in ("http", "https"):
        return _get_remote_image_size(url, timeout)

    # Local file path
    return _get_local_image_size(url, base_path)


def _get_data_uri_image_size(url: str) -> Optional[ImageSize]:
    """
    Get dimensions from a `data:` URI by decoding its own payload.

    Corrupt input raises: the author wrote bytes that cannot be an image, and
    guessing would hide it. But a payload that is intact and simply not
    measurable — a format the header parser doesn't know, or an SVG with no
    intrinsic size — returns None, exactly as the local and remote branches do
    for the same bytes. Measuring is best-effort here; the renderer owns the
    fallback via `image_fallback_aspect_ratio`.
    """
    header, separator, payload = url[len("data:") :].partition(",")
    if not separator:
        raise ValueError("Malformed data: URI — no ',' separating header from payload")

    # Media types and the base64 token are case-insensitive (RFC 2045), and a
    # space after the ';' is common enough that browsers accept it.
    parameters = [parameter.strip().lower() for parameter in header.split(";")]
    media_type = parameters[0]

    if "base64" in parameters[1:]:
        # The URL spec's data: processor uses forgiving-base64, so browsers
        # render an unpadded payload; b64decode would reject it as corrupt.
        try:
            data = base64.b64decode(payload + "=" * (-len(payload) % 4), validate=True)
        except binascii.Error as e:
            raise ValueError(
                f"Malformed base64 payload in data:{media_type} URI: {e}"
            ) from e
    else:
        data = unquote_to_bytes(payload)

    if media_type == "image/svg+xml":
        return _parse_svg_dimensions(data)
    return _parse_image_dimensions(data)


# SVG lengths only have a pixel size when unitless or in px; %, em, etc. do not.
_SVG_PIXEL_LENGTH = re.compile(r"\s*([0-9]*\.?[0-9]+)(px)?\s*\Z")


def _parse_svg_dimensions(data: bytes) -> Optional[ImageSize]:
    """
    Parse the intrinsic size of an SVG document: width/height, else viewBox.

    None when the document genuinely has no intrinsic size — `width="100%"`
    with no viewBox is the common case, and the consumer is meant to supply
    one. Raises only when the payload is not XML at all.
    """
    # Decline to measure entity-declaring payloads rather than hand them to
    # expat, which only caps entity amplification from 2.6 on and this package
    # supports Pythons that may link an older one. Unmeasured, not corrupt:
    # such a document is often perfectly valid, so it takes the fallback.
    if b"<!ENTITY" in data:
        return None

    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as e:
        raise ValueError(f"Malformed XML in data:image/svg+xml URI: {e}") from e

    size = _svg_image_size(
        _parse_svg_length(root.get("width")), _parse_svg_length(root.get("height"))
    )
    return size if size is not None else _parse_svg_view_box(root.get("viewBox"))


def _svg_image_size(
    width: Optional[float], height: Optional[float]
) -> Optional[ImageSize]:
    """
    Build an ImageSize from SVG lengths, or None if either has no pixel size.

    The "no size" test has to run on the rounded value, not the parsed float:
    a legal `width="0.4"` is positive but rounds to zero, which is unsized
    for an SVG rather than the corrupt header ImageSize would reject.
    """
    if width is None or height is None:
        return None
    rounded_width, rounded_height = round(width), round(height)
    if rounded_width < 1 or rounded_height < 1:
        return None
    return ImageSize(width=rounded_width, height=rounded_height)


def _parse_svg_length(value: Optional[str]) -> Optional[float]:
    """Parse an SVG length in pixels, or None if it has no pixel size."""
    if value is None:
        return None
    match = _SVG_PIXEL_LENGTH.match(value)
    return float(match.group(1)) if match else None


def _parse_svg_view_box(value: Optional[str]) -> Optional[ImageSize]:
    """Parse the width and height out of a `viewBox="min-x min-y width height"`."""
    if value is None:
        return None

    parts = value.replace(",", " ").split()
    if len(parts) != 4:
        return None

    return _svg_image_size(_parse_svg_length(parts[2]), _parse_svg_length(parts[3]))


def _get_local_image_size(
    path: str,
    base_path: Optional[str] = None,
) -> Optional[ImageSize]:
    """Get dimensions from a local image file."""
    # Resolve path
    file_path = Path(path)
    if not file_path.is_absolute() and base_path:
        file_path = Path(base_path) / file_path

    if not file_path.exists():
        return None

    try:
        with open(file_path, "rb") as f:
            return _parse_image_dimensions(f.read(32768))  # Read first 32KB
    except (OSError, ValueError):
        return None


def _get_remote_image_size(
    url: str,
    timeout: float = 10.0,
) -> Optional[ImageSize]:
    """Get dimensions from a remote image URL."""
    try:
        import urllib.request

        # Create request with range header to fetch just the start
        request = urllib.request.Request(url)
        request.add_header("Range", "bytes=0-32767")  # First 32KB
        request.add_header("User-Agent", "mdsvg/1.0")

        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(32768)
            return _parse_image_dimensions(data)

    except ValueError:
        return None
    except Exception:  # noqa: BLE001
        # Try without range header (some servers don't support it)
        try:
            import urllib.request

            request = urllib.request.Request(url)
            request.add_header("User-Agent", "mdsvg/1.0")

            with urllib.request.urlopen(request, timeout=timeout) as response:
                # Read in chunks until we have enough to parse
                data = response.read(32768)
                return _parse_image_dimensions(data)
        except Exception:  # noqa: BLE001
            return None


def _parse_image_dimensions(data: bytes) -> Optional[ImageSize]:
    """
    Parse image dimensions from raw bytes.

    Supports: PNG, JPEG, GIF, WebP, BMP
    """
    if len(data) < 24:
        return None

    # PNG: 8-byte signature, then IHDR chunk with width/height
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width = struct.unpack(">I", data[16:20])[0]
        height = struct.unpack(">I", data[20:24])[0]
        return ImageSize(width=width, height=height)

    # JPEG: Look for SOF0/SOF2 markers
    if data[:2] == b"\xff\xd8":
        return _parse_jpeg_dimensions(data)

    # GIF: Header contains dimensions at fixed offset
    if data[:6] in (b"GIF87a", b"GIF89a"):
        width = struct.unpack("<H", data[6:8])[0]
        height = struct.unpack("<H", data[8:10])[0]
        return ImageSize(width=width, height=height)

    # WebP: RIFF container
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _parse_webp_dimensions(data)

    # BMP: Header contains dimensions
    if data[:2] == b"BM":
        width = struct.unpack("<I", data[18:22])[0]
        height = abs(struct.unpack("<i", data[22:26])[0])  # Can be negative
        return ImageSize(width=width, height=height)

    return None


def _parse_jpeg_dimensions(data: bytes) -> Optional[ImageSize]:
    """Parse dimensions from JPEG data."""
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            return None

        marker = data[i + 1]

        # Skip padding bytes
        if marker == 0xFF:
            i += 1
            continue

        # SOF markers (Start of Frame) contain dimensions
        # SOF0 (baseline), SOF1 (extended), SOF2 (progressive)
        if marker in (0xC0, 0xC1, 0xC2):
            height = struct.unpack(">H", data[i + 5 : i + 7])[0]
            width = struct.unpack(">H", data[i + 7 : i + 9])[0]
            return ImageSize(width=width, height=height)

        # Skip other segments
        if marker in (0xD8, 0xD9) or marker in (
            0xD0,
            0xD1,
            0xD2,
            0xD3,
            0xD4,
            0xD5,
            0xD6,
            0xD7,
        ):  # SOI, EOI, RST
            i += 2
        else:
            # Read segment length and skip
            if i + 4 > len(data):
                return None
            length = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + length

    return None


def _parse_webp_dimensions(data: bytes) -> Optional[ImageSize]:
    """Parse dimensions from WebP data."""
    # Check for VP8/VP8L/VP8X chunks
    if len(data) < 30:
        return None

    chunk_type = data[12:16]

    # VP8 (lossy)
    if chunk_type == b"VP8 ":
        # Skip to frame header
        if len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
            return ImageSize(width=width, height=height)

    # VP8L (lossless)
    elif chunk_type == b"VP8L":
        if len(data) >= 25:
            b0 = data[21]
            b1 = data[22]
            b2 = data[23]
            b3 = data[24]
            width = ((b1 & 0x3F) << 8 | b0) + 1
            height = ((b3 & 0x0F) << 10 | b2 << 2 | (b1 & 0xC0) >> 6) + 1
            return ImageSize(width=width, height=height)

    # VP8X (extended)
    elif chunk_type == b"VP8X" and len(data) >= 30:
        width = struct.unpack("<I", data[24:27] + b"\x00")[0] + 1
        height = struct.unpack("<I", data[27:30] + b"\x00")[0] + 1
        return ImageSize(width=width, height=height)

    return None


# URL Mapping utilities


def create_prefix_mapper(prefix_map: Dict[str, str]) -> ImageUrlMapper:
    """
    Create a URL mapper that replaces path prefixes.

    Useful for mapping local development paths to CDN URLs.

    Args:
        prefix_map: Dictionary mapping source prefixes to target prefixes.

    Returns:
        A function that transforms URLs based on the prefix map.

    Example:
        >>> mapper = create_prefix_mapper({
        ...     "/assets/": "https://cdn.example.com/assets/",
        ...     "./images/": "https://cdn.example.com/images/",
        ... })
        >>> mapper("/assets/logo.png")
        'https://cdn.example.com/assets/logo.png'
    """

    def mapper(url: str) -> str:
        for source, target in prefix_map.items():
            if url.startswith(source):
                return target + url[len(source) :]
        return url

    return mapper


def create_base_url_mapper(base_url: str) -> ImageUrlMapper:
    """
    Create a URL mapper that prepends a base URL to relative paths.

    Args:
        base_url: Base URL to prepend (should end with /).

    Returns:
        A function that prepends base_url to relative paths.

    Example:
        >>> mapper = create_base_url_mapper("https://cdn.example.com/")
        >>> mapper("images/logo.png")
        'https://cdn.example.com/images/logo.png'
        >>> mapper("https://other.com/image.png")  # Absolute URL unchanged
        'https://other.com/image.png'
    """

    def mapper(url: str) -> str:
        parsed = urlparse(url)
        # Don't modify absolute URLs
        if parsed.scheme or url.startswith("//"):
            return url
        # Don't modify data URLs
        if url.startswith("data:"):
            return url
        # Prepend base URL
        return base_url.rstrip("/") + "/" + url.lstrip("/")

    return mapper
