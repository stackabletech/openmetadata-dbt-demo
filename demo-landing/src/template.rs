//! minijinja environment with a `nodeport` helper that looks up
//! pre-resolved host:port values from a map. Resolving kube API calls
//! happens outside this module (see routes.rs) so minijinja stays sync.

use minijinja::{Environment, Error as MjError, ErrorKind, Value};
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, OnceLock};

fn nodeport_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        // {{ nodeport "svc-name" }} with optional whitespace variations
        Regex::new(r#"\{\{\s*nodeport\s+"([^"]+)"\s*\}\}"#).unwrap()
    })
}

/// Scan the markdown source and return the unique set of service names
/// referenced by `{{ nodeport "..." }}` calls.
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

/// Build an Environment with the `nodeport` helper bound to a resolution map.
/// A miss (service not present in the map) renders as an HTML comment so the
/// overall page still renders.
pub fn build_env(resolved: Arc<HashMap<String, String>>) -> Environment<'static> {
    let mut env = Environment::new();
    env.add_function(
        "nodeport",
        move |name: String| -> Result<Value, MjError> {
            match resolved.get(&name) {
                Some(hostport) => Ok(Value::from(hostport.clone())),
                None => Ok(Value::from(format!(
                    "<!-- nodeport error: service '{}' not resolved -->",
                    name
                ))),
            }
        },
    );
    env
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_collects_unique_names_in_order() {
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
    fn env_substitutes_known_service() {
        let mut map = HashMap::new();
        map.insert("svc".to_string(), "10.0.0.1:30080".to_string());
        let env = build_env(Arc::new(map));
        let out = env
            .render_str(r#"URL: {{ nodeport("svc") }}"#, ())
            .unwrap();
        assert_eq!(out, "URL: 10.0.0.1:30080");
    }

    #[test]
    fn env_emits_error_comment_for_unknown_service() {
        let env = build_env(Arc::new(HashMap::new()));
        let out = env
            .render_str(r#"X: {{ nodeport("missing") }} Y"#, ())
            .unwrap();
        assert_eq!(
            out,
            "X: <!-- nodeport error: service 'missing' not resolved --> Y"
        );
    }
}
