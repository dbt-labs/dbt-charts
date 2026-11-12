//! `images.py`'s size read for a `data:` URI (`get_image_size` :42 →
//! `_get_data_uri_image_size` :91): the one source production measures with
//! `fetch_image_sizes` off, because the bytes are already in hand. A local
//! file or remote read is not ported; the renderer refuses it by name.
//!
//! Raster headers (`_parse_image_dimensions` and its JPEG/WebP helpers) are
//! transliterated byte for byte. An SVG's size is read from its root
//! element's `width`/`height`, else its `viewBox`, as `_parse_svg_dimensions`
//! does; production parses with expat (`ElementTree.fromstring`), this crate
//! with roxmltree, so where the two parsers could disagree — a payload that is
//! not UTF-8, a DTD attribute default, expat's error text — it refuses by name.

use d3rs::invariant::Invariant;
use d3rs::py_str::is_py_space;

/// `images.py::ImageSize`: integer pixel dimensions, both at least 1. Held as
/// `f64` because an SVG size is `round()` of a float and can exceed `u64`;
/// every value is integral.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ImageSize {
    pub width: f64,
    pub height: f64,
}

impl ImageSize {
    fn new(width: f64, height: f64) -> Result<Self, String> {
        if width < 1.0 || height < 1.0 {
            return Err(format!(
                "Image header declares a zero dimension ({width}x{height})"
            ));
        }
        Ok(ImageSize { width, height })
    }

    pub fn aspect_ratio(&self) -> f64 {
        self.width / self.height
    }
}

/// `renderer.py::_get_image_size`'s scheme test (`:2437`): a URL measured
/// from its own payload whether or not sizes are fetched.
pub fn is_data_uri(url: &str) -> bool {
    url.get(..5)
        .is_some_and(|scheme| scheme.eq_ignore_ascii_case("data:"))
}

/// `images.py::_get_data_uri_image_size`. `Ok(None)` is an intact payload with
/// no measurable size; `Err` is production's `ValueError` (or the `struct` /
/// `OverflowError` it lets through) message, or a by-name refusal.
pub fn data_uri_image_size(url: &str) -> Result<Option<ImageSize>, String> {
    let Some((header, payload)) = url.get(5..).and_then(|rest| rest.split_once(',')) else {
        return Err("Malformed data: URI \u{2014} no ',' separating header from payload".into());
    };
    let parameters: Vec<String> = header
        .split(';')
        .map(|p| p.trim_matches(is_py_space).to_lowercase())
        .collect();
    let (media_type, flags) = parameters
        .split_first()
        .held("split yields at least one piece");

    let data = if flags.iter().any(|p| p == "base64") {
        // `b64decode` encodes the `str` to ASCII first, outside the
        // `binascii.Error` handler, so this message reaches the caller bare.
        if !payload.is_ascii() {
            return Err("string argument should contain only ASCII characters".into());
        }
        let padded = format!("{payload}{}", "=".repeat((4 - payload.len() % 4) % 4));
        a2b_base64_strict(padded.as_bytes())
            .map_err(|e| format!("Malformed base64 payload in data:{media_type} URI: {e}"))?
    } else {
        unquote_to_bytes(payload)
    };

    if media_type == "image/svg+xml" {
        return parse_svg_dimensions(&data);
    }
    parse_image_dimensions(&data)
}

/// CPython 3.13 `binascii.a2b_base64(s, strict_mode=True)`, the decoder
/// `base64.b64decode(..., validate=True)` calls, with its error messages.
fn a2b_base64_strict(ascii: &[u8]) -> Result<Vec<u8>, String> {
    if ascii.first() == Some(&b'=') {
        return Err("Leading padding not allowed".into());
    }
    let mut out = Vec::with_capacity(ascii.len() / 4 * 3);
    let mut quad_pos = 0u8;
    let mut left: u8 = 0;
    let mut pads = 0u8;
    let mut padding_started = false;
    for (i, &ch) in ascii.iter().enumerate() {
        if ch == b'=' {
            padding_started = true;
            if quad_pos == 0 {
                return Err("Excess padding not allowed".into());
            }
            if quad_pos >= 2 {
                pads += 1;
                if quad_pos + pads >= 4 {
                    if i + 1 < ascii.len() {
                        return Err("Excess data after padding".into());
                    }
                    return Ok(out);
                }
            }
            continue;
        }
        let value = match ch {
            b'A'..=b'Z' => ch - b'A',
            b'a'..=b'z' => ch - b'a' + 26,
            b'0'..=b'9' => ch - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return Err("Only base64 data is allowed".into()),
        };
        if padding_started {
            return Err("Discontinuous padding not allowed".into());
        }
        pads = 0;
        match quad_pos {
            0 => {
                quad_pos = 1;
                left = value;
            }
            1 => {
                quad_pos = 2;
                out.push((left << 2) | (value >> 4));
                left = value & 0x0f;
            }
            2 => {
                quad_pos = 3;
                out.push((left << 4) | (value >> 2));
                left = value & 0x03;
            }
            _ => {
                quad_pos = 0;
                out.push((left << 6) | value);
                left = 0;
            }
        }
    }
    match quad_pos {
        0 => Ok(out),
        1 => Err(format!(
            "Invalid base64-encoded string: number of data characters ({}) cannot be 1 more than a multiple of 4",
            out.len() / 3 * 4 + 1
        )),
        _ => Err("Incorrect padding".into()),
    }
}

/// `urllib.parse.unquote_to_bytes`: UTF-8 bytes with each `%XX` replaced; a
/// `%` not followed by two hex digits stays literal.
fn unquote_to_bytes(text: &str) -> Vec<u8> {
    let bytes = text.as_bytes();
    let hex = |b: u8| (b as char).to_digit(16).map(|d| d as u8);
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while let Some(&byte) = bytes.get(i) {
        if byte == b'%' {
            if let (Some(hi), Some(lo)) = (
                bytes.get(i + 1).copied().and_then(hex),
                bytes.get(i + 2).copied().and_then(hex),
            ) {
                out.push(hi << 4 | lo);
                i += 3;
                continue;
            }
        }
        out.push(byte);
        i += 1;
    }
    out
}

/// `images.py::_parse_svg_dimensions`.
fn parse_svg_dimensions(data: &[u8]) -> Result<Option<ImageSize>, String> {
    if contains(data, b"<!ENTITY") {
        return Ok(None);
    }
    if contains(data, b"<!ATTLIST") {
        return Err(
            "mdsvg-rs: image data URI SVG with an ATTLIST declaration not supported".into(),
        );
    }
    let not_utf8 = || "mdsvg-rs: image data URI SVG not in UTF-8 not supported".to_string();
    let text = std::str::from_utf8(data).map_err(|_| not_utf8())?;
    if declared_encoding(text).is_some_and(|e| !e.eq_ignore_ascii_case("utf-8")) {
        return Err(not_utf8());
    }
    let options = roxmltree::ParsingOptions {
        allow_dtd: true,
        ..roxmltree::ParsingOptions::default()
    };
    let document = roxmltree::Document::parse_with_options(text, options)
        .map_err(|_| "mdsvg-rs: image data URI malformed SVG not supported".to_string())?;
    let root = document.root_element();
    let size = match (
        parse_svg_length(root.attribute("width")),
        parse_svg_length(root.attribute("height")),
    ) {
        (Some(width), Some(height)) => svg_image_size(width, height)?,
        _ => None,
    };
    match size {
        Some(size) => Ok(Some(size)),
        None => parse_svg_view_box(root.attribute("viewBox")),
    }
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack.windows(needle.len()).any(|w| w == needle)
}

/// The `encoding="..."` of a leading XML declaration, if any.
fn declared_encoding(text: &str) -> Option<&str> {
    let declaration = text
        .strip_prefix('\u{feff}')
        .unwrap_or(text)
        .strip_prefix("<?xml")?;
    let (declaration, _) = declaration.split_once("?>")?;
    let (_, rest) = declaration.split_once("encoding")?;
    let rest = rest.trim_start().strip_prefix('=')?.trim_start();
    let quote = rest.chars().next().filter(|q| *q == '"' || *q == '\'')?;
    let (value, _) = rest.strip_prefix(quote)?.split_once(quote)?;
    Some(value)
}

/// `images.py::_svg_image_size`: Python's `round()` (ties to even) of each
/// length; a length that rounds below 1 is unsized, not corrupt.
fn svg_image_size(width: f64, height: f64) -> Result<Option<ImageSize>, String> {
    let round = |v: f64| -> Result<f64, String> {
        if v.is_infinite() {
            return Err("cannot convert float infinity to integer".into());
        }
        Ok(v.round_ties_even())
    };
    let (width, height) = (round(width)?, round(height)?);
    if width < 1.0 || height < 1.0 {
        return Ok(None);
    }
    ImageSize::new(width, height).map(Some)
}

/// `images.py::_parse_svg_length` with `_SVG_PIXEL_LENGTH`
/// (`\s*([0-9]*\.?[0-9]+)(px)?\s*\Z`): unitless or `px`, else no pixel size.
fn parse_svg_length(value: Option<&str>) -> Option<f64> {
    let trimmed = value?.trim_matches(is_py_space);
    let number = trimmed.strip_suffix("px").unwrap_or(trimmed);
    let (int_part, frac_part) = match number.split_once('.') {
        Some((int_part, frac_part)) => (int_part, Some(frac_part)),
        None => ("", Some(number)),
    };
    let digits = |s: &str| s.bytes().all(|b| b.is_ascii_digit());
    let frac_part = frac_part?;
    if !digits(int_part) || frac_part.is_empty() || !digits(frac_part) {
        return None;
    }
    number.parse().ok()
}

/// `images.py::_parse_svg_view_box`: the third and fourth of exactly four
/// comma- or space-separated numbers.
fn parse_svg_view_box(value: Option<&str>) -> Result<Option<ImageSize>, String> {
    let Some(value) = value else {
        return Ok(None);
    };
    let replaced = value.replace(',', " ");
    let parts: Vec<&str> = replaced
        .split(is_py_space)
        .filter(|p| !p.is_empty())
        .collect();
    let &[_, _, width, height] = parts.as_slice() else {
        return Ok(None);
    };
    match (parse_svg_length(Some(width)), parse_svg_length(Some(height))) {
        (Some(width), Some(height)) => svg_image_size(width, height),
        _ => Ok(None),
    }
}

/// `struct.unpack` of `N` bytes at `pos`, raising `struct.error` past the end.
fn read<const N: usize>(d: &[u8], pos: usize) -> Result<[u8; N], String> {
    d.get(pos..pos + N)
        .and_then(|bytes| bytes.try_into().ok())
        .ok_or_else(|| format!("unpack requires a buffer of {N} bytes"))
}

fn be_u16(d: &[u8], pos: usize) -> Result<f64, String> {
    Ok(f64::from(u16::from_be_bytes(read(d, pos)?)))
}

fn le_u16(d: &[u8], pos: usize) -> Result<f64, String> {
    Ok(f64::from(u16::from_le_bytes(read(d, pos)?)))
}

/// `images.py::_parse_image_dimensions`: PNG, JPEG, GIF, WebP, BMP, sniffed
/// from the bytes whatever media type the URI declared.
fn parse_image_dimensions(d: &[u8]) -> Result<Option<ImageSize>, String> {
    if d.len() < 24 {
        return Ok(None);
    }
    if d.starts_with(b"\x89PNG\r\n\x1a\n") && read::<4>(d, 12)? == *b"IHDR" {
        let width = f64::from(u32::from_be_bytes(read(d, 16)?));
        let height = f64::from(u32::from_be_bytes(read(d, 20)?));
        return ImageSize::new(width, height).map(Some);
    }
    if d.starts_with(b"\xff\xd8") {
        return parse_jpeg_dimensions(d);
    }
    if d.starts_with(b"GIF87a") || d.starts_with(b"GIF89a") {
        return ImageSize::new(le_u16(d, 6)?, le_u16(d, 8)?).map(Some);
    }
    if d.starts_with(b"RIFF") && read::<4>(d, 8)? == *b"WEBP" {
        return parse_webp_dimensions(d);
    }
    if d.starts_with(b"BM") {
        let width = f64::from(u32::from_le_bytes(read(d, 18)?));
        let height = f64::from(i32::from_le_bytes(read(d, 22)?)).abs();
        return ImageSize::new(width, height).map(Some);
    }
    Ok(None)
}

/// `images.py::_parse_jpeg_dimensions`: walk the segments to SOF0/1/2.
fn parse_jpeg_dimensions(d: &[u8]) -> Result<Option<ImageSize>, String> {
    let mut i = 2;
    while i + 9 < d.len() {
        let [first, marker] = read(d, i)?;
        if first != 0xFF {
            return Ok(None);
        }
        if marker == 0xFF {
            i += 1;
            continue;
        }
        if matches!(marker, 0xC0..=0xC2) {
            return ImageSize::new(be_u16(d, i + 7)?, be_u16(d, i + 5)?).map(Some);
        }
        if matches!(marker, 0xD0..=0xD9) {
            i += 2;
        } else {
            i += 2 + be_u16(d, i + 2)? as usize;
        }
    }
    Ok(None)
}

/// `images.py::_parse_webp_dimensions`: the VP8, VP8L, or VP8X chunk.
fn parse_webp_dimensions(d: &[u8]) -> Result<Option<ImageSize>, String> {
    if d.len() < 30 {
        return Ok(None);
    }
    match &read::<4>(d, 12)? {
        b"VP8 " if read::<3>(d, 23)? == *b"\x9d\x01\x2a" => {
            let width = f64::from(u16::from_le_bytes(read(d, 26)?) & 0x3FFF);
            let height = f64::from(u16::from_le_bytes(read(d, 28)?) & 0x3FFF);
            ImageSize::new(width, height).map(Some)
        }
        b"VP8L" => {
            let [b0, b1, b2, b3] = read::<4>(d, 21)?.map(u32::from);
            let width = ((b1 & 0x3F) << 8 | b0) + 1;
            let height = ((b3 & 0x0F) << 10 | b2 << 2 | (b1 & 0xC0) >> 6) + 1;
            ImageSize::new(f64::from(width), f64::from(height)).map(Some)
        }
        b"VP8X" => {
            let [w0, w1, w2] = read(d, 24)?;
            let [h0, h1, h2] = read(d, 27)?;
            let width = u32::from_le_bytes([w0, w1, w2, 0]) + 1;
            let height = u32::from_le_bytes([h0, h1, h2, 0]) + 1;
            ImageSize::new(f64::from(width), f64::from(height)).map(Some)
        }
        _ => Ok(None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    include!("images_tests.rs");
}
