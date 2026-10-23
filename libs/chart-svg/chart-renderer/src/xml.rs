//! Markup writer for the families not yet split onto the node tree (KPI,
//! table, callout, error placard). Names and values go through quick-xml, which
//! escapes every XML metacharacter in text and attribute values, so a family
//! never escapes by hand. A fragment drawn by a hand-written shared emitter
//! enters through [`Xml::raw`] and nowhere else.

use std::borrow::Cow;

use quick_xml::events::{BytesCData, BytesEnd, BytesStart, BytesText, Event};
use quick_xml::Writer;

/// An element's attributes in document order.
#[derive(Clone, Default)]
pub struct Attrs<'a>(Vec<(&'a str, Cow<'a, str>)>);

impl<'a> Attrs<'a> {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set(mut self, name: &'a str, value: impl Into<Cow<'a, str>>) -> Self {
        self.0.push((name, value.into()));
        self
    }

    pub fn set_some(self, name: &'a str, value: Option<impl Into<Cow<'a, str>>>) -> Self {
        match value {
            Some(value) => self.set(name, value),
            None => self,
        }
    }

    pub fn set_if(self, condition: bool, name: &'a str, value: impl Into<Cow<'a, str>>) -> Self {
        if condition {
            self.set(name, value)
        } else {
            self
        }
    }

    pub fn extend(mut self, other: Attrs<'a>) -> Self {
        self.0.extend(other.0);
        self
    }
}

pub struct Xml(Writer<Vec<u8>>);

impl Default for Xml {
    fn default() -> Self {
        Self(Writer::new(Vec::new()))
    }
}

impl Xml {
    pub fn new() -> Self {
        Self::default()
    }

    fn event(&mut self, event: Event) {
        // The sink is a `Vec`, which never fails to accept a write.
        self.0.write_event(event).expect("writing to a Vec cannot fail");
    }

    fn start(tag: &str, attrs: Attrs) -> BytesStart<'static> {
        let mut start = BytesStart::new(tag.to_string());
        for (name, value) in &attrs.0 {
            start.push_attribute((*name, value.as_ref()));
        }
        start
    }

    pub fn open(&mut self, tag: &str, attrs: Attrs) {
        self.event(Event::Start(Self::start(tag, attrs)));
    }

    pub fn close(&mut self, tag: &str) {
        self.event(Event::End(BytesEnd::new(tag)));
    }

    pub fn empty(&mut self, tag: &str, attrs: Attrs) {
        self.event(Event::Empty(Self::start(tag, attrs)));
    }

    pub fn text(&mut self, text: &str) {
        self.event(Event::Text(BytesText::new(text)));
    }

    pub fn leaf(&mut self, tag: &str, attrs: Attrs, text: &str) {
        self.open(tag, attrs);
        self.text(text);
        self.close(tag);
    }

    /// A CDATA section; a literal `]]>` splits it into two sections.
    pub fn cdata(&mut self, text: &str) {
        for section in BytesCData::escaped(text) {
            self.event(Event::CData(section));
        }
    }

    /// Markup that is already well-formed: a fragment from a shared
    /// hand-written emitter, or from another `Xml`. Never authored text.
    pub fn raw(&mut self, markup: &str) {
        self.event(Event::Text(BytesText::from_escaped(markup)));
    }

    pub fn finish(self) -> String {
        String::from_utf8(self.0.into_inner()).expect("quick-xml writes UTF-8 from str input")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_and_attribute_values_escape_every_metacharacter() {
        let mut xml = Xml::new();
        xml.leaf("a", Attrs::new().set("k", "x\"y'z<&>\n"), "t\"'<&>");
        assert_eq!(
            xml.finish(),
            "<a k=\"x&quot;y&apos;z&lt;&amp;&gt;&#10;\">t&quot;&apos;&lt;&amp;&gt;</a>"
        );
    }

    #[test]
    fn cdata_splits_a_literal_close() {
        let mut xml = Xml::new();
        xml.cdata("a]]>b");
        assert_eq!(xml.finish(), "<![CDATA[a]]]]><![CDATA[>b]]>");
    }

    #[test]
    fn raw_markup_is_written_verbatim() {
        let mut xml = Xml::new();
        xml.raw("<b x=\"&amp;\"/>");
        assert_eq!(xml.finish(), "<b x=\"&amp;\"/>");
    }
}
