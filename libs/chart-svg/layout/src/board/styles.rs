//! The last step of a board's nodes (`boards.py::_dedupe_repeated_style_rules`): collapse
//! byte-identical markdown CSS rule lines repeated across the `<style>` elements of one `<svg>`.
//!
//! A markdown renderer scopes every rule as `.md-<hash>-<name>`, the hash a function of its own
//! style, so two renderers of the same style print the same lines and the later ones are safe to
//! drop. A rule line in another `<svg>` (a nested board's) is another scope and stays: a chart cut
//! out on its own takes the blocks of its enclosing `<svg>` with it. It works on the scene, so
//! `print` stays a serializer: a `<style>` element is the lines of its rules as text children
//! between newlines, and one left with no line vanishes with the indentation before it.

use std::collections::HashSet;

use diagnostic::{diag, Diagnostic};
use scene::{Child, Element, Node, Shape};

/// `_DEDUPE_ELIGIBLE_RULE_RE`: `^\s*\.md-[0-9a-f]+-[a-z]+\s*\{`.
fn eligible(line: &str) -> bool {
    let Some(rest) = line.trim_start().strip_prefix(".md-") else { return false };
    let hash = rest.chars().take_while(char::is_ascii_hexdigit).count();
    let Some(rest) = rest[hash..].strip_prefix('-').filter(|_| hash > 0) else { return false };
    let name = rest.chars().take_while(char::is_ascii_lowercase).count();
    name > 0 && rest[name..].trim_start().starts_with('{')
}

/// `nodes` with each eligible rule line kept at its first occurrence per enclosing `<svg>`.
pub fn dedupe_repeated_style_rules(nodes: &mut Vec<Node>) -> Result<(), Diagnostic> {
    Scopes::default().nodes(nodes, 0)
}

#[derive(Default)]
struct Scopes {
    opened: usize,
    seen: HashSet<(usize, String)>,
}

impl Scopes {
    fn open(&mut self) -> usize {
        self.opened += 1;
        self.opened
    }

    fn nodes(&mut self, list: &mut Vec<Node>, scope: usize) -> Result<(), Diagnostic> {
        let mut vanish = Vec::new();
        for (at, node) in list.iter_mut().enumerate() {
            match &mut node.shape {
                Shape::Svg { children, .. } => {
                    let inner = self.open();
                    self.nodes(children, inner)?;
                }
                Shape::Group(children) | Shape::Link(children) | Shape::Defs(children) | Shape::ClipPath(children) => self.nodes(children, scope)?,
                Shape::Mask { children, .. } | Shape::Pattern { children, .. } => self.nodes(children, scope)?,
                Shape::Element(element) if element.tag == "style" => {
                    if self.rules(element, scope)? {
                        vanish.push(at);
                    }
                }
                Shape::Element(element) => self.element(element, scope)?,
                _ => {}
            }
        }
        for at in vanish.into_iter().rev() {
            let before = at.checked_sub(1).filter(|before| matches!(&list[*before].shape, Shape::Chars(chars) if indent(&chars.text)));
            let Some(before) = before else { return Err(diag::err_internal("a `<style>` element is indented by the text before it")) };
            list.drain(before..=at);
        }
        Ok(())
    }

    fn element(&mut self, element: &mut Element, scope: usize) -> Result<(), Diagnostic> {
        let scope = if element.tag == "svg" { self.open() } else { scope };
        let mut vanish = Vec::new();
        for (at, child) in element.children.iter_mut().enumerate() {
            let Child::Element(inner) = child else { continue };
            if inner.tag == "style" {
                if self.rules(inner, scope)? {
                    vanish.push(at);
                }
            } else {
                self.element(inner, scope)?;
            }
        }
        for at in vanish.into_iter().rev() {
            let before = at.checked_sub(1).filter(|before| matches!(&element.children[*before], Child::Text(chars) if indent(&chars.text)));
            let Some(before) = before else { return Err(diag::err_internal("a `<style>` element is indented by the text before it")) };
            element.children.drain(before..=at);
        }
        Ok(())
    }

    /// The lines of `style` left after the repeated eligible ones are dropped; `true` when none is.
    fn rules(&mut self, style: &mut Element, scope: usize) -> Result<bool, Diagnostic> {
        let text = |child: &Child| match child {
            Child::Text(chars) => Some(chars.text.clone()),
            Child::Element(_) => None,
        };
        if !style.children.iter().filter_map(text).any(|line| eligible(&line)) {
            return Ok(false);
        }
        // `\n`, a line, `\n`, a line, ..., `\n` and the closing tag's indent.
        let children = std::mem::take(&mut style.children);
        let last = children.len().saturating_sub(1);
        let shaped = children.len() >= 3
            && children.len() % 2 == 1
            && children.iter().enumerate().all(|(at, child)| match (at % 2, text(child)) {
                (1, Some(line)) => !line.starts_with('\n'),
                (_, Some(gap)) => gap == if at == last { "\n  " } else { "\n" },
                _ => false,
            });
        if !shaped {
            return Err(diag::err_internal("a `<style>` element with markdown rules is its lines between newlines"));
        }
        let mut kept = Vec::new();
        for child in &children[1..children.len() - 1] {
            if let Some(line) = text(child).filter(|line| !line.starts_with('\n')) {
                if !eligible(&line) || self.seen.insert((scope, line)) {
                    kept.push(child.clone());
                }
            }
        }
        if kept.is_empty() {
            style.children = children;
            return Ok(true);
        }
        let (first, last) = (children[0].clone(), children[children.len() - 1].clone());
        style.children.push(first.clone());
        for (at, line) in kept.into_iter().enumerate() {
            if at > 0 {
                style.children.push(first.clone());
            }
            style.children.push(line);
        }
        style.children.push(last);
        Ok(false)
    }
}

/// The text a `<style>` element is indented by: two spaces, after a newline unless it is the first.
fn indent(text: &str) -> bool {
    text == "  " || text == "\n  "
}

#[cfg(test)]
mod tests {
    use super::*;
    use scene::{Attrs, Chars, Escape, Extent, Role};

    const RULE: &str = "    .md-abc123-text { fill: red; }";

    fn chars(text: &str) -> Node {
        Node::chars(Chars::new(text, Escape::Attr))
    }

    fn text(text: &str) -> Child {
        Child::Text(Chars::new(text, Escape::Mdsvg))
    }

    /// `  <style>\n<line>\n...\n  </style>` as the markdown renderer lays it out.
    fn style(lines: &[&str]) -> Element {
        let mut children = vec![text("\n")];
        for (at, line) in lines.iter().enumerate() {
            if at > 0 {
                children.push(text("\n"));
            }
            children.push(text(line));
        }
        children.push(text("\n  "));
        Element { tag: "style".to_string(), attrs: Attrs::new(), children }
    }

    fn block(lines: &[&str], first: bool) -> Vec<Node> {
        vec![chars(if first { "  " } else { "\n  " }), Node::element(style(lines))]
    }

    fn svg(children: Vec<Node>) -> Node {
        Node::new(Role::Chrome, Attrs::new(), Shape::Svg { extent: Extent { width: 1.0, height: 1.0 }, children })
    }

    fn printed(nodes: &[Node]) -> String {
        print::print(nodes)
    }

    #[test]
    fn a_repeated_rule_line_in_one_svg_is_dropped() {
        let mut nodes = vec![svg([block(&[RULE], true), block(&[RULE], false)].concat())];
        dedupe_repeated_style_rules(&mut nodes).unwrap();
        assert_eq!(printed(&nodes).matches(".md-abc123-text").count(), 1);
    }

    #[test]
    fn a_repeated_rule_line_in_another_svg_stays() {
        let inner = svg(block(&[RULE], true));
        let mut nodes = vec![svg([block(&[RULE], true), vec![inner]].concat())];
        dedupe_repeated_style_rules(&mut nodes).unwrap();
        assert_eq!(printed(&nodes).matches(".md-abc123-text").count(), 2);
    }

    #[test]
    fn a_block_with_no_line_left_vanishes_with_the_indent_before_it() {
        let mut nodes = vec![svg([block(&[RULE], true), block(&[RULE], false)].concat())];
        let kept = printed(&[svg(block(&[RULE], true))]);
        dedupe_repeated_style_rules(&mut nodes).unwrap();
        assert_eq!(printed(&nodes), kept);
    }

    #[test]
    fn a_block_keeps_the_lines_that_are_not_repeated() {
        let other = "    .md-abc123-code { fill: blue; }";
        let mut nodes = vec![svg([block(&[RULE], true), block(&[RULE, other], false)].concat())];
        dedupe_repeated_style_rules(&mut nodes).unwrap();
        assert_eq!(printed(&nodes), printed(&[svg([block(&[RULE], true), block(&[other], false)].concat())]));
    }

    #[test]
    fn a_line_that_is_not_a_markdown_rule_is_never_dropped() {
        let line = "    body { margin: 0; }";
        let mut nodes = vec![svg([block(&[line], true), block(&[line], false)].concat())];
        dedupe_repeated_style_rules(&mut nodes).unwrap();
        assert_eq!(printed(&nodes).matches("body { margin: 0; }").count(), 2);
    }

    #[test]
    fn a_style_with_markdown_rules_in_another_shape_is_an_error() {
        let mut odd = style(&[RULE, RULE]);
        odd.children[2] = text("\n ");
        let mut nodes = vec![svg(vec![chars("  "), Node::element(odd)])];
        assert!(dedupe_repeated_style_rules(&mut nodes).is_err());
        let mut element = style(&[RULE]);
        element.children[1] = Child::Element(style(&[RULE]));
        element.children.insert(1, text(RULE));
        let mut nodes = vec![svg(vec![chars("  "), Node::element(element)])];
        assert!(dedupe_repeated_style_rules(&mut nodes).is_err());
    }

    #[test]
    fn an_emptied_style_with_no_indent_before_it_is_an_error() {
        let mut nodes = vec![svg([block(&[RULE], true), vec![Node::element(style(&[RULE]))]].concat())];
        assert!(dedupe_repeated_style_rules(&mut nodes).is_err());
    }

    #[test]
    fn the_eligible_rule_is_a_scoped_markdown_class() {
        assert!(eligible("    .md-abc123-text { fill: red; }"));
        assert!(eligible(".md-0f-code{"));
        assert!(!eligible("    .md--text { }"));
        assert!(!eligible("    .md-abc123-Text { }"));
        assert!(!eligible("    .md-abc123-text fill"));
        assert!(!eligible("    body { margin: 0; }"));
    }
}
