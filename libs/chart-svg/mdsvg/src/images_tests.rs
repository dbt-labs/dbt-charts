// Every expectation below is `images.py::get_image_size` on the same URI
// (Python 3.13), success or raised message alike.

fn b64(media_type: &str, data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = format!("data:{media_type};base64,");
    for chunk in data.chunks(3) {
        let b = [
            chunk[0],
            *chunk.get(1).unwrap_or(&0),
            *chunk.get(2).unwrap_or(&0),
        ];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for k in 0..4 {
            if k <= chunk.len() {
                out.push(ALPHABET[((n >> (18 - 6 * k)) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

fn size(url: &str) -> Option<(f64, f64)> {
    data_uri_image_size(url)
        .unwrap()
        .map(|s| (s.width, s.height))
}

fn png(width: u32, height: u32) -> Vec<u8> {
    let mut data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR".to_vec();
    data.extend(width.to_be_bytes());
    data.extend(height.to_be_bytes());
    data.extend(b"\x08\x06\x00\x00\x00");
    data
}

#[test]
fn raster_headers_decode_like_images_py() {
    assert_eq!(
        size(&b64("image/png", &png(300, 150))),
        Some((300.0, 150.0))
    );

    let mut gif = b"GIF89a".to_vec();
    gif.extend(40u16.to_le_bytes());
    gif.extend(20u16.to_le_bytes());
    gif.extend([0; 16]);
    assert_eq!(size(&b64("image/gif", &gif)), Some((40.0, 20.0)));

    // An APP0 segment is skipped by its length; SOF0 holds height then width.
    let mut jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00".to_vec();
    jpeg.extend([0; 9]);
    jpeg.extend(b"\xff\xc0\x00\x11\x08\x00\x64\x00\xc8\x03");
    jpeg.extend([0; 10]);
    assert_eq!(size(&b64("image/jpeg", &jpeg)), Some((200.0, 100.0)));

    // A 0xFF fill byte advances one; SOF2 (progressive) is read too.
    let mut progressive = b"\xff\xd8\xff\xff\xc2\x00\x11\x08\x00\x07\x00\x09".to_vec();
    progressive.extend([0; 20]);
    assert_eq!(size(&b64("image/png", &progressive)), Some((9.0, 7.0)));

    // A byte that is not a marker prefix ends the scan unmeasured.
    let mut stray = b"\xff\xd8\x00".to_vec();
    stray.extend([0; 30]);
    assert_eq!(size(&b64("image/png", &stray)), None);

    let mut vp8 = b"RIFF\x00\x00\x00\x00WEBPVP8 ".to_vec();
    vp8.extend([0; 7]);
    vp8.extend(b"\x9d\x01\x2a");
    vp8.extend((0xC000u16 | 320).to_le_bytes());
    vp8.extend(240u16.to_le_bytes());
    assert_eq!(size(&b64("image/png", &vp8)), Some((320.0, 240.0)));

    let mut vp8l = b"RIFF\x00\x00\x00\x00WEBPVP8L\x00\x00\x00\x00".to_vec();
    vp8l.extend([0x2F, 0xFF, 0x7F, 0xFE, 0x03, 0, 0, 0, 0, 0]);
    assert_eq!(size(&b64("image/png", &vp8l)), Some((16384.0, 4090.0)));

    let mut vp8x = b"RIFF\x00\x00\x00\x00WEBPVP8X".to_vec();
    vp8x.extend([0; 8]);
    vp8x.extend(&639u32.to_le_bytes()[..3]);
    vp8x.extend(&479u32.to_le_bytes()[..3]);
    assert_eq!(size(&b64("image/png", &vp8x)), Some((640.0, 480.0)));

    // A bottom-up BMP stores a negative height.
    let mut bmp = b"BM".to_vec();
    bmp.extend([0; 16]);
    bmp.extend(64i32.to_le_bytes());
    bmp.extend((-32i32).to_le_bytes());
    bmp.extend([0; 4]);
    assert_eq!(size(&b64("image/bmp", &bmp)), Some((64.0, 32.0)));
}

#[test]
fn raster_bytes_are_sniffed_whatever_the_media_type_says() {
    let mut percent = String::from("data:image/png,");
    for byte in png(300, 150) {
        percent.push_str(&format!("%{byte:02X}"));
    }
    assert_eq!(size(&percent), Some((300.0, 150.0)));
    assert_eq!(size(&b64("text/plain", &png(3, 2))), Some((3.0, 2.0)));
    assert_eq!(size(&b64("image/png", b"\x89PNG")), None);
    assert_eq!(size(&b64("image/png", &[b'x'; 30])), None);
}

#[test]
fn corrupt_raster_headers_raise_like_images_py() {
    assert_eq!(
        data_uri_image_size(&b64("image/png", &png(0, 150))).unwrap_err(),
        "Image header declares a zero dimension (0x150)"
    );
    let mut short_bmp = b"BM".to_vec();
    short_bmp.extend([0; 16]);
    short_bmp.extend(64i32.to_le_bytes());
    short_bmp.extend([0, 0]);
    assert_eq!(
        data_uri_image_size(&b64("image/bmp", &short_bmp)).unwrap_err(),
        "unpack requires a buffer of 4 bytes"
    );
}

#[test]
fn svg_size_is_width_and_height_else_the_view_box() {
    let svg = |doc: &str| size(&b64("image/svg+xml", doc.as_bytes()));
    assert_eq!(
        size("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='40px' height=' 20 '/>"),
        Some((40.0, 20.0))
    );
    assert_eq!(
        svg(r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0,0  412 128"/>"#),
        Some((412.0, 128.0))
    );
    assert_eq!(svg(r#"<svg width="100%" height="50"/>"#), None);
    assert_eq!(
        svg(r#"<svg width="100%" height="50" viewBox="0 0 10 5"/>"#),
        Some((10.0, 5.0))
    );
    // `round()` ties to even.
    assert_eq!(svg(r#"<svg width="2.5" height="3.5"/>"#), Some((2.0, 4.0)));
    // A length that rounds to zero is unsized, so the view box answers.
    assert_eq!(
        svg(r#"<svg width="0.4" height="3" viewBox="0 0 8 4"/>"#),
        Some((8.0, 4.0))
    );
    assert_eq!(svg(r#"<svg viewBox="0 0 8px 4"/>"#), Some((8.0, 4.0)));
    assert_eq!(svg(r#"<svg viewBox="0 0 8"/>"#), None);
    assert_eq!(svg(r#"<svg width="1." height="3"/>"#), None);
    assert_eq!(
        svg(r#"<svg xmlns:x="u" x:width="9" width="4" height="2"/>"#),
        Some((4.0, 2.0))
    );
    assert_eq!(
        svg(
            r#"<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg width="4" height="2"/>"#
        ),
        Some((4.0, 2.0))
    );
    assert_eq!(
        svg("\u{feff}<?xml version=\"1.0\" encoding=\"UTF-8\"?><svg width=\"6\" height=\"2\"/>"),
        Some((6.0, 2.0))
    );
    // An entity-declaring payload is declined unmeasured, never parsed.
    assert_eq!(
        svg(r#"<!DOCTYPE svg [<!ENTITY a "b">]><svg width="4" height="4"/>"#),
        None
    );
    assert_eq!(
        size("data:image/svg+xml,<svg width='4' height='2'><title>\u{e9}</title></svg>"),
        Some((4.0, 2.0))
    );
}

#[test]
fn svg_lengths_past_float_range_raise_like_round() {
    let doc = format!(r#"<svg width="{}" height="3"/>"#, "9".repeat(400));
    assert_eq!(
        data_uri_image_size(&b64("image/svg+xml", doc.as_bytes())).unwrap_err(),
        "cannot convert float infinity to integer"
    );
}

#[test]
fn malformed_svg_refuses_by_name() {
    // Python raises with expat's own message, which is not ported.
    for doc in [
        &b"<svg width=\"4\" height=\"4\">"[..],
        b"",
        b"<svg x:width=\"9\"/>",
    ] {
        assert_eq!(
            data_uri_image_size(&b64("image/svg+xml", doc)).unwrap_err(),
            "mdsvg-rs: image data URI malformed SVG not supported"
        );
    }
    // Bytes that are not UTF-8 (raster bytes here; a UTF-16 document, or one
    // declaring another encoding, which expat would decode) refuse too.
    for doc in [
        png(3, 2),
        b"\xff\xfe<\x00/\x00".to_vec(),
        br#"<?xml version="1.0" encoding="latin-1"?><svg width="4" height="2"/>"#.to_vec(),
    ] {
        assert_eq!(
            data_uri_image_size(&b64("image/svg+xml", &doc)).unwrap_err(),
            "mdsvg-rs: image data URI SVG not in UTF-8 not supported"
        );
    }
    // expat applies an ATTLIST default to the root; this crate does not read DTDs.
    let attlist = br#"<!DOCTYPE svg [<!ATTLIST svg width CDATA "9">]><svg height="2"/>"#;
    assert_eq!(
        data_uri_image_size(&b64("image/svg+xml", attlist)).unwrap_err(),
        "mdsvg-rs: image data URI SVG with an ATTLIST declaration not supported"
    );
}

#[test]
fn header_parameters_are_trimmed_and_case_folded() {
    let payload = &b64("x", br#"<svg width="8" height="2"/>"#)["data:x;base64,".len()..];
    assert_eq!(
        size(&format!("data:IMAGE/SVG+XML ; Base64 ,{payload}")),
        Some((8.0, 2.0))
    );
    // Forgiving base64: a payload missing its padding decodes.
    assert_eq!(
        size(&format!(
            "data:image/svg+xml;base64,{}",
            payload.trim_end_matches('=')
        )),
        Some((8.0, 2.0))
    );
}

#[test]
fn malformed_payloads_raise_with_production_s_message() {
    let cases = [
        ("data:image/png;base64", "Malformed data: URI \u{2014} no ',' separating header from payload"),
        ("data:image/png;base64,QQ=\n=", "Malformed base64 payload in data:image/png URI: Only base64 data is allowed"),
        ("data:image/png;base64,=QQ=", "Malformed base64 payload in data:image/png URI: Leading padding not allowed"),
        (
            "data:image/png;base64,Q",
            "Malformed base64 payload in data:image/png URI: Invalid base64-encoded string: number of data characters (1) cannot be 1 more than a multiple of 4",
        ),
        ("data:image/png;base64,QQ==QQ==", "Malformed base64 payload in data:image/png URI: Excess data after padding"),
        ("data:image/png;base64,QQ=Q", "Malformed base64 payload in data:image/png URI: Discontinuous padding not allowed"),
        ("data:image/png;base64,QUJD=", "Malformed base64 payload in data:image/png URI: Excess padding not allowed"),
        ("data:image/png;base64,QQ\u{e9}=", "string argument should contain only ASCII characters"),
    ];
    for (url, want) in cases {
        assert_eq!(data_uri_image_size(url).unwrap_err(), want, "{url:?}");
    }
}
