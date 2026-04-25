//! minijinja environment with `nodeport` and `toggle` helpers that look up
//! pre-resolved values. Resolving (kube API for nodeports, Forgejo API for
//! toggles) happens outside this module so minijinja stays sync.

use minijinja::{Environment, Error as MjError, Value};
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, OnceLock};

// ---------- nodeport (existing) ----------

fn nodeport_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r#"\{\{\s*nodeport\s+"([^"]+)"\s*\}\}"#).unwrap())
}

/// Scan the markdown source and return the unique service names referenced
/// by `{{ nodeport "..." }}` calls.
pub fn extract_service_names(markdown: &str) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for cap in nodeport_regex().captures_iter(markdown) {
        let name = cap[1].to_string();
        if seen.insert(name.clone()) {
            out.push(name);
        }
    }
    out
}

// ---------- toggle (new) ----------

fn toggle_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r#"\{\{\s*toggle\s+"([^"]+)"\s+"([^"]+)"\s*\}\}"#).unwrap()
    })
}

/// Scan the markdown for `{{ toggle "<file>" "<key>" }}` placeholders.
/// Returns deduplicated `(file, key)` pairs in first-seen order.
pub fn extract_toggle_calls(markdown: &str) -> Vec<(String, String)> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for cap in toggle_regex().captures_iter(markdown) {
        let pair = (cap[1].to_string(), cap[2].to_string());
        if seen.insert(pair.clone()) {
            out.push(pair);
        }
    }
    out
}

#[derive(Debug, Clone)]
pub enum ToggleResolution {
    Ok { sha: String, value: bool },
    Error { reason: String },
}

// ---------- env builder ----------

/// Build a minijinja Environment with both helpers bound to their
/// pre-resolved data.
///
/// `nodeports`: map service-name → host:port string.
/// `toggles`: map (file_path, key_path) → resolution.
pub fn build_env(
    nodeports: Arc<HashMap<String, String>>,
    toggles: Arc<HashMap<(String, String), ToggleResolution>>,
) -> Environment<'static> {
    let mut env = Environment::new();

    let nodeports_for_fn = Arc::clone(&nodeports);
    env.add_function(
        "nodeport",
        move |name: String| -> Result<Value, MjError> {
            match nodeports_for_fn.get(&name) {
                Some(hostport) => Ok(Value::from(hostport.clone())),
                None => Ok(Value::from(format!(
                    "<!-- nodeport error: service '{}' not resolved -->",
                    name
                ))),
            }
        },
    );

    let toggles_for_fn = Arc::clone(&toggles);
    env.add_function(
        "toggle",
        move |path: String, key: String| -> Result<Value, MjError> {
            let html = render_toggle_html(&toggles_for_fn, &path, &key);
            Ok(Value::from_safe_string(html))
        },
    );

    env
}

fn render_toggle_html(
    toggles: &HashMap<(String, String), ToggleResolution>,
    path: &str,
    key: &str,
) -> String {
    let entry = toggles.get(&(path.to_string(), key.to_string()));
    match entry {
        Some(ToggleResolution::Ok { sha, value }) => {
            let class = if *value { "switch switch-on" } else { "switch switch-off" };
            let aria = format!("Toggle {} on {}", key, path);
            format!(
                r#"<form class="cell-toggle" method="POST" action="/toggle">
  <input type="hidden" name="path" value="{p}">
  <input type="hidden" name="key" value="{k}">
  <input type="hidden" name="sha" value="{s}">
  <input type="hidden" name="current_value" value="{v}">
  <button type="submit" class="{cls}" aria-label="{a}"><span class="switch-knob"></span></button>
</form>"#,
                p = html_attr_escape(path),
                k = html_attr_escape(key),
                s = html_attr_escape(sha),
                v = if *value { "true" } else { "false" },
                cls = class,
                a = html_attr_escape(&aria),
            )
        }
        Some(ToggleResolution::Error { .. }) | None => {
            let reason_str = match entry {
                Some(ToggleResolution::Error { reason }) => reason.clone(),
                _ => format!("toggle for ({}, {}) was not resolved", path, key),
            };
            format!(
                r#"<span class="toggle-error" title="{}">⚠ error</span>"#,
                html_attr_escape(&reason_str)
            )
        }
    }
}

fn html_attr_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            other => out.push(other),
        }
    }
    out
}

// ---------- tests ----------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_collects_unique_service_names_in_order() {
        let md = r#"
Visit [ArgoCD](https://{{ nodeport "argocd-server-nodeport" }}/applications)
and [Forgejo](http://{{ nodeport "forgejo-http-nodeport" }}/).
Also [ArgoCD again](https://{{ nodeport "argocd-server-nodeport" }}).
"#;
        let got = extract_service_names(md);
        assert_eq!(
            got,
            vec![
                "argocd-server-nodeport".to_string(),
                "forgejo-http-nodeport".to_string()
            ]
        );
    }

    #[test]
    fn extract_handles_whitespace_variations() {
        let md = r#"A {{nodeport "a"}} B {{  nodeport   "b"  }} C"#;
        let got = extract_service_names(md);
        assert_eq!(got, vec!["a".to_string(), "b".to_string()]);
    }

    #[test]
    fn extract_toggle_collects_unique_pairs_in_order() {
        let md = r#"
| HDFS | {{ toggle "platform/manifests/hdfs/hdfs.yaml" "spec.clusterOperations.stopped" }} |
| Trino | {{ toggle "platform/manifests/trino/trino.yaml" "spec.clusterOperations.stopped" }} |
| HDFS again | {{ toggle "platform/manifests/hdfs/hdfs.yaml" "spec.clusterOperations.stopped" }} |
"#;
        let got = extract_toggle_calls(md);
        assert_eq!(
            got,
            vec![
                (
                    "platform/manifests/hdfs/hdfs.yaml".to_string(),
                    "spec.clusterOperations.stopped".to_string()
                ),
                (
                    "platform/manifests/trino/trino.yaml".to_string(),
                    "spec.clusterOperations.stopped".to_string()
                )
            ]
        );
    }

    #[test]
    fn extract_toggle_handles_whitespace_variations() {
        let md = r#"A {{toggle "f.yaml" "k"}} B {{  toggle   "f.yaml"   "k.path"  }} C"#;
        let got = extract_toggle_calls(md);
        assert_eq!(
            got,
            vec![
                ("f.yaml".to_string(), "k".to_string()),
                ("f.yaml".to_string(), "k.path".to_string())
            ]
        );
    }

    #[test]
    fn extract_toggle_returns_empty_for_no_matches() {
        let md = "Some markdown without any toggle placeholders.";
        let got = extract_toggle_calls(md);
        assert!(got.is_empty());
    }

    #[test]
    fn nodeport_env_substitutes_known_service() {
        let mut np = HashMap::new();
        np.insert("svc".to_string(), "10.0.0.1:30080".to_string());
        let env = build_env(Arc::new(np), Arc::new(HashMap::new()));
        let out = env
            .render_str(r#"URL: {{ nodeport("svc") }}"#, ())
            .unwrap();
        assert_eq!(out, "URL: 10.0.0.1:30080");
    }

    #[test]
    fn nodeport_env_emits_error_comment_for_unknown_service() {
        let env = build_env(Arc::new(HashMap::new()), Arc::new(HashMap::new()));
        let out = env
            .render_str(r#"X: {{ nodeport("missing") }} Y"#, ())
            .unwrap();
        assert_eq!(
            out,
            "X: <!-- nodeport error: service 'missing' not resolved --> Y"
        );
    }

    #[test]
    fn toggle_env_renders_switch_for_resolved_pair() {
        let mut t = HashMap::new();
        t.insert(
            ("p.yaml".to_string(), "spec.k".to_string()),
            ToggleResolution::Ok {
                sha: "abc".into(),
                value: false,
            },
        );
        let env = build_env(Arc::new(HashMap::new()), Arc::new(t));
        let out = env
            .render_str(r#"{{ toggle("p.yaml", "spec.k") }}"#, ())
            .unwrap();
        assert!(out.contains(r#"action="/toggle""#));
        assert!(out.contains(r#"name="path" value="p.yaml""#));
        assert!(out.contains(r#"name="key" value="spec.k""#));
        assert!(out.contains(r#"name="sha" value="abc""#));
        assert!(out.contains(r#"name="current_value" value="false""#));
        assert!(out.contains("switch-off"));
    }

    #[test]
    fn toggle_env_renders_switch_on_when_value_true() {
        let mut t = HashMap::new();
        t.insert(
            ("p.yaml".to_string(), "k".to_string()),
            ToggleResolution::Ok {
                sha: "x".into(),
                value: true,
            },
        );
        let env = build_env(Arc::new(HashMap::new()), Arc::new(t));
        let out = env
            .render_str(r#"{{ toggle("p.yaml", "k") }}"#, ())
            .unwrap();
        assert!(out.contains("switch switch-on"));
        assert!(out.contains(r#"name="current_value" value="true""#));
    }

    #[test]
    fn toggle_env_renders_error_for_unresolved_pair() {
        let env = build_env(Arc::new(HashMap::new()), Arc::new(HashMap::new()));
        let out = env
            .render_str(r#"{{ toggle("missing.yaml", "k") }}"#, ())
            .unwrap();
        assert!(out.contains(r#"class="toggle-error""#));
        assert!(out.contains("⚠ error"));
    }

    #[test]
    fn toggle_env_renders_error_with_reason_for_resolved_error() {
        let mut t = HashMap::new();
        t.insert(
            ("p.yaml".to_string(), "k".to_string()),
            ToggleResolution::Error {
                reason: "value at k is not a boolean".into(),
            },
        );
        let env = build_env(Arc::new(HashMap::new()), Arc::new(t));
        let out = env
            .render_str(r#"{{ toggle("p.yaml", "k") }}"#, ())
            .unwrap();
        assert!(out.contains(r#"title="value at k is not a boolean""#));
    }

    #[test]
    fn html_attr_escape_handles_all_dangerous_chars() {
        assert_eq!(html_attr_escape("a&b"), "a&amp;b");
        assert_eq!(html_attr_escape(r#"a"b"#), "a&quot;b");
        assert_eq!(html_attr_escape("a'b"), "a&#39;b");
        assert_eq!(html_attr_escape("a<b>c"), "a&lt;b&gt;c");
    }
}
