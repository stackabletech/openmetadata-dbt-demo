//! Full render pipeline: markdown source + resolution maps -> HTML page.

use crate::template::{self, ToggleResolution};
use minijinja::value::Value;
use minijinja::{context, AutoEscape, Environment};
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

#[derive(Debug, Error)]
pub enum SetError {
    #[error("intermediate path segment '{0}' is not a mapping")]
    NotAMapping(String),
    #[error("path segment '{0}' is missing")]
    MissingSegment(String),
    #[error("value at path is not a boolean (got {got})")]
    NotABoolean { got: String },
}

const LAYOUT_SRC: &str = include_str!("../assets/layout.html");

fn nodeport_call_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    // Convert `{{ nodeport "x" }}` -> `{{ nodeport("x") }}`.
    RE.get_or_init(|| Regex::new(r#"\{\{\s*nodeport\s+"([^"]+)"\s*\}\}"#).unwrap())
}

fn toggle_call_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    // Convert `{{ toggle "x" "y" }}` -> `{{ toggle("x", "y") }}`.
    RE.get_or_init(|| Regex::new(r#"\{\{\s*toggle\s+"([^"]+)"\s+"([^"]+)"\s*\}\}"#).unwrap())
}

fn normalize_helper_calls(markdown: &str) -> String {
    let after_nodeport = nodeport_call_regex()
        .replace_all(markdown, r#"{{ nodeport("$1") }}"#)
        .into_owned();
    toggle_call_regex()
        .replace_all(&after_nodeport, r#"{{ toggle("$1", "$2") }}"#)
        .into_owned()
}

/// Render a full HTML page.
pub fn render(
    markdown_src: &str,
    nodeports: Arc<HashMap<String, String>>,
    toggles: Arc<HashMap<(String, String), ToggleResolution>>,
    title: &str,
    current_user: &str,
    logout_url: &str,
) -> Result<String, RenderError> {
    let normalized = normalize_helper_calls(markdown_src);

    let env = template::build_env(nodeports, toggles);
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
    layout_env.set_auto_escape_callback(|_| AutoEscape::Html);
    layout_env
        .add_template("layout", LAYOUT_SRC)
        .map_err(|e| RenderError::Layout(e.to_string()))?;
    let tmpl = layout_env
        .get_template("layout")
        .map_err(|e| RenderError::Layout(e.to_string()))?;
    tmpl.render(context! {
        title => title,
        content => Value::from_safe_string(body_html),
        current_user => current_user,
        logout_url => Value::from_safe_string(logout_url.to_string()),
    })
    .map_err(|e| RenderError::Layout(e.to_string()))
}

/// Read a boolean value at a dotted YAML key path.
pub fn get_yaml_bool_at_path(
    yaml: &serde_yaml::Value,
    key_path: &str,
) -> Result<bool, SetError> {
    let segments: Vec<&str> = key_path.split('.').collect();
    let mut cursor: &serde_yaml::Value = yaml;
    for (i, seg) in segments.iter().enumerate() {
        let mapping = cursor
            .as_mapping()
            .ok_or_else(|| SetError::NotAMapping(segments[..i].join(".")))?;
        cursor = mapping
            .get(serde_yaml::Value::String((*seg).to_string()))
            .ok_or_else(|| SetError::MissingSegment((*seg).to_string()))?;
    }
    cursor
        .as_bool()
        .ok_or_else(|| SetError::NotABoolean {
            got: format!("{:?}", cursor),
        })
}

/// Set a boolean value at a dotted YAML key path. Errors if any intermediate
/// segment is missing/non-mapping or the final value isn't a boolean.
pub fn set_yaml_bool_at_path(
    yaml: &mut serde_yaml::Value,
    key_path: &str,
    new_value: bool,
) -> Result<(), SetError> {
    let segments: Vec<&str> = key_path.split('.').collect();
    if segments.is_empty() {
        return Err(SetError::MissingSegment(String::new()));
    }
    let (last, parents) = segments.split_last().unwrap();

    let mut cursor: &mut serde_yaml::Value = yaml;
    for (i, seg) in parents.iter().enumerate() {
        let mapping = cursor
            .as_mapping_mut()
            .ok_or_else(|| SetError::NotAMapping(parents[..i].join(".")))?;
        cursor = mapping
            .get_mut(&serde_yaml::Value::String((*seg).to_string()))
            .ok_or_else(|| SetError::MissingSegment((*seg).to_string()))?;
    }

    let mapping = cursor
        .as_mapping_mut()
        .ok_or_else(|| SetError::NotAMapping(parents.join(".")))?;
    let entry = mapping
        .get_mut(&serde_yaml::Value::String((*last).to_string()))
        .ok_or_else(|| SetError::MissingSegment((*last).to_string()))?;

    if !entry.is_bool() {
        return Err(SetError::NotABoolean {
            got: format!("{:?}", entry),
        });
    }
    *entry = serde_yaml::Value::Bool(new_value);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty_maps() -> (
        Arc<HashMap<String, String>>,
        Arc<HashMap<(String, String), ToggleResolution>>,
    ) {
        (Arc::new(HashMap::new()), Arc::new(HashMap::new()))
    }

    #[test]
    fn renders_plain_markdown() {
        let md = "# Hello\n\nThis is **bold**.";
        let (np, tg) = empty_maps();
        let html = render(md, np, tg, "t", "", "").unwrap();
        assert!(html.contains("<h1>Hello</h1>"));
        assert!(html.contains("<strong>bold</strong>"));
        assert!(html.contains("<title>t</title>"));
        assert!(html.contains(r#"<link rel="stylesheet" href="/styles.css""#));
    }

    #[test]
    fn substitutes_nodeport_before_markdown_parsing() {
        let md = r#"[Argo](https://{{ nodeport "argocd" }}/apps)"#;
        let mut np = HashMap::new();
        np.insert("argocd".to_string(), "10.0.0.1:30080".to_string());
        let (_, tg) = empty_maps();
        let html = render(md, Arc::new(np), tg, "t", "", "").unwrap();
        assert!(html.contains(r#"href="https://10.0.0.1:30080/apps""#));
    }

    #[test]
    fn missing_service_renders_error_comment_and_keeps_page() {
        let md = r#"{{ nodeport "missing" }} See <a href="http://example.com">svc</a>"#;
        let (np, tg) = empty_maps();
        let html = render(md, np, tg, "t", "", "").unwrap();
        assert!(html.contains("nodeport error"));
        assert!(html.contains("<a href="));
    }

    #[test]
    fn substitutes_toggle_helper_to_form() {
        let md = r#"State: {{ toggle "p.yaml" "spec.k" }}"#;
        let mut tg = HashMap::new();
        tg.insert(
            ("p.yaml".to_string(), "spec.k".to_string()),
            ToggleResolution::Ok {
                sha: "abc".into(),
                value: false,
            },
        );
        let (np, _) = empty_maps();
        let html = render(md, np, Arc::new(tg), "t", "", "").unwrap();
        assert!(html.contains(r#"<form class="cell-toggle""#));
        assert!(html.contains(r#"action="/toggle""#));
        // value=false (not stopped) → switch shown as "on" / running.
        assert!(html.contains("switch-on"));
    }

    #[test]
    fn render_omits_user_block_when_current_user_is_empty() {
        let md = "# hi";
        let (np, tg) = empty_maps();
        let html = render(md, np, tg, "t", "", "").unwrap();
        assert!(!html.contains("user-block"));
        assert!(!html.contains("log out"));
    }

    #[test]
    fn render_includes_username_without_link_when_logout_url_empty() {
        let md = "# hi";
        let (np, tg) = empty_maps();
        let html = render(md, np, tg, "t", "demo-admin", "").unwrap();
        assert!(html.contains("user-block"));
        assert!(html.contains("demo-admin"));
        assert!(!html.contains("log out"));
    }

    #[test]
    fn render_includes_username_and_logout_link_when_both_set() {
        let md = "# hi";
        let (np, tg) = empty_maps();
        let html = render(
            md,
            np,
            tg,
            "t",
            "demo-admin",
            "/oauth2/sign_out?rd=http%3A%2F%2Fkc%2Flogout",
        )
        .unwrap();
        assert!(html.contains("user-block"));
        assert!(html.contains("demo-admin"));
        assert!(html.contains("log out"));
        assert!(html.contains(r#"href="/oauth2/sign_out?rd=http%3A%2F%2Fkc%2Flogout""#));
    }

    #[test]
    fn render_html_escapes_username() {
        let md = "# hi";
        let (np, tg) = empty_maps();
        let html = render(md, np, tg, "t", "<script>x</script>", "").unwrap();
        assert!(!html.contains("<script>x</script>"));
        assert!(html.contains("&lt;script&gt;"));
    }

    #[test]
    fn set_yaml_bool_top_level() {
        let mut y: serde_yaml::Value = serde_yaml::from_str("flag: false\n").unwrap();
        set_yaml_bool_at_path(&mut y, "flag", true).unwrap();
        let s = serde_yaml::to_string(&y).unwrap();
        assert!(s.contains("flag: true"));
    }

    #[test]
    fn set_yaml_bool_deeply_nested() {
        let src = r#"
spec:
  clusterOperations:
    stopped: false
"#;
        let mut y: serde_yaml::Value = serde_yaml::from_str(src).unwrap();
        set_yaml_bool_at_path(&mut y, "spec.clusterOperations.stopped", true).unwrap();
        let s = serde_yaml::to_string(&y).unwrap();
        assert!(s.contains("stopped: true"));
    }

    #[test]
    fn set_yaml_bool_missing_segment_errors() {
        let mut y: serde_yaml::Value = serde_yaml::from_str("a: 1\n").unwrap();
        let err = set_yaml_bool_at_path(&mut y, "missing.path", true).unwrap_err();
        assert!(matches!(err, SetError::MissingSegment(_)));
    }

    #[test]
    fn set_yaml_bool_non_boolean_leaf_errors() {
        let mut y: serde_yaml::Value = serde_yaml::from_str("x: hello\n").unwrap();
        let err = set_yaml_bool_at_path(&mut y, "x", true).unwrap_err();
        assert!(matches!(err, SetError::NotABoolean { .. }));
    }

    #[test]
    fn set_yaml_bool_non_mapping_intermediate_errors() {
        let src = "x:\n  - 1\n  - 2\n";
        let mut y: serde_yaml::Value = serde_yaml::from_str(src).unwrap();
        let err = set_yaml_bool_at_path(&mut y, "x.something", true).unwrap_err();
        assert!(matches!(err, SetError::NotAMapping(_)));
    }

    #[test]
    fn get_yaml_bool_reads_value() {
        let src = "spec:\n  k: true\n";
        let y: serde_yaml::Value = serde_yaml::from_str(src).unwrap();
        let v = get_yaml_bool_at_path(&y, "spec.k").unwrap();
        assert_eq!(v, true);
    }

    #[test]
    fn get_yaml_bool_missing_segment_errors() {
        let y: serde_yaml::Value = serde_yaml::from_str("a: 1\n").unwrap();
        let err = get_yaml_bool_at_path(&y, "missing").unwrap_err();
        assert!(matches!(err, SetError::MissingSegment(_)));
    }
}
