//! Full render pipeline: markdown source + resolution map -> HTML page.

use crate::template;
use minijinja::{context, Environment};
use pulldown_cmark::{html as cmark_html, Options, Parser};
use regex::Regex;
use std::collections::HashMap;
use std::sync::{Arc, OnceLock};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum RenderError {
    #[error("template substitution failed: {0}")]
    Substitute(String),

    #[error("layout template error: {0}")]
    Layout(String),
}

const LAYOUT_SRC: &str = include_str!("../assets/layout.html");

fn call_rewrite_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    // Convert `{{ nodeport "name" }}` (space-separated) into the minijinja
    // call form `{{ nodeport("name") }}` so the engine evaluates it.
    RE.get_or_init(|| Regex::new(r#"\{\{\s*nodeport\s+"([^"]+)"\s*\}\}"#).unwrap())
}

fn normalize_nodeport_calls(markdown: &str) -> String {
    call_rewrite_regex()
        .replace_all(markdown, r#"{{ nodeport("$1") }}"#)
        .into_owned()
}

/// Render a full HTML page.
///
/// 1. Rewrite `{{ nodeport "x" }}` -> `{{ nodeport("x") }}` so minijinja evaluates it.
/// 2. Run minijinja template substitution (sync) using the pre-resolved map.
/// 3. Parse the resulting markdown to HTML.
/// 4. Wrap in the layout.
pub fn render(
    markdown_src: &str,
    resolved: Arc<HashMap<String, String>>,
    title: &str,
) -> Result<String, RenderError> {
    let normalized = normalize_nodeport_calls(markdown_src);

    let env = template::build_env(resolved);
    let templated = env
        .render_str(&normalized, ())
        .map_err(|e| RenderError::Substitute(e.to_string()))?;

    let mut opts = Options::empty();
    opts.insert(Options::ENABLE_TABLES);
    opts.insert(Options::ENABLE_STRIKETHROUGH);
    opts.insert(Options::ENABLE_TASKLISTS);
    let parser = Parser::new_ext(&templated, opts);
    let mut body_html = String::new();
    cmark_html::push_html(&mut body_html, parser);

    let mut layout_env = Environment::new();
    layout_env
        .add_template("layout", LAYOUT_SRC)
        .map_err(|e| RenderError::Layout(e.to_string()))?;
    let tmpl = layout_env
        .get_template("layout")
        .map_err(|e| RenderError::Layout(e.to_string()))?;
    tmpl.render(context! { title => title, content => body_html })
        .map_err(|e| RenderError::Layout(e.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn renders_plain_markdown() {
        let md = "# Hello\n\nThis is **bold**.";
        let html = render(md, Arc::new(HashMap::new()), "t").unwrap();
        assert!(html.contains("<h1>Hello</h1>"));
        assert!(html.contains("<strong>bold</strong>"));
        assert!(html.contains("<title>t</title>"));
        assert!(html.contains(r#"<link rel="stylesheet" href="/styles.css""#));
    }

    #[test]
    fn substitutes_nodeport_before_markdown_parsing() {
        let md = r#"[Argo](https://{{ nodeport "argocd" }}/apps)"#;
        let mut map = HashMap::new();
        map.insert("argocd".to_string(), "10.0.0.1:30080".to_string());
        let html = render(md, Arc::new(map), "t").unwrap();
        assert!(html.contains(r#"href="https://10.0.0.1:30080/apps""#));
    }

    #[test]
    fn missing_service_renders_error_comment_and_keeps_page() {
        let md = r#"{{ nodeport "missing" }} See <a href="http://example.com">svc</a>"#;
        let html = render(md, Arc::new(HashMap::new()), "t").unwrap();
        assert!(html.contains("nodeport error"));
        // The page still renders with links intact even when a nodeport is unresolved.
        assert!(html.contains("<a href="));
    }
}
