# Landing Page Toggle + Basic Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `demo-landing` tool with a generic boolean toggle helper (`{{ toggle "<file>" "<key>" }}`) that flips a YAML key in the repo via the Forgejo API, plus HTTP basic auth on every route except `/healthz`.

**Architecture:** New per-pod Forgejo HTTP client reads files via the contents API and writes them back with the file's blob SHA as an optimistic-locking handle. A new minijinja helper renders each toggle placeholder as an HTML form with hidden inputs (`path`, `key`, `sha`, `current_value`); a new `POST /toggle` handler flips the value and returns a 302 to `/`. A new axum middleware enforces basic auth on every route except `/healthz`. Credentials come from a new sealed Secret for page auth and from the existing `forgejo-admin-secret` for API auth.

**Tech Stack:** Rust, axum, minijinja, reqwest, serde_yaml, base64, Kubernetes, Sealed Secrets, ArgoCD, Forgejo API.

**Spec:** `docs/superpowers/specs/2026-04-25-landing-toggle-design.md`

**File map:**

| Path | Action | Responsibility |
|---|---|---|
| `demo-landing/Cargo.toml` | Modify | Add `reqwest`, `base64`, `serde`, `serde_json`, `serde_yaml` |
| `demo-landing/src/auth.rs` | Create | `basic_auth` axum middleware |
| `demo-landing/src/forgejo.rs` | Create | `ForgejoClient`, `FileContents`, `ForgejoError` |
| `demo-landing/src/template.rs` | Modify | Add `extract_toggle_calls`, `ToggleResolution`; extend `build_env` to take toggles |
| `demo-landing/src/render.rs` | Modify | Add `set_yaml_bool_at_path`; extend `render` to accept resolved toggles |
| `demo-landing/src/routes.rs` | Modify | Extend `AppState`; pre-resolve toggles in `index`; add `toggle` handler + error pages |
| `demo-landing/src/main.rs` | Modify | Read new env vars; build `ForgejoClient`; split router; wire `basic_auth` middleware |
| `demo-landing/assets/styles.css` | Modify | Add `.cell-toggle`, `.switch`, `.switch-knob`, `.toggle-error` rules |
| `secrets/manifests/demo-landing/demo-landing-basic-auth.yaml` | Create | Plaintext Secret with `AUTH_USER` / `AUTH_PASSWORD` |
| `platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml` | Create (generated) | Sealed sibling |
| `platform/manifests/demo-landing/deployment.yaml` | Modify | Add envFrom for basic-auth Secret + `valueFrom` entries for `FORGEJO_USERNAME` / `FORGEJO_PASSWORD` + defaults for `FORGEJO_URL`/`OWNER`/`REPO`/`BRANCH` |

**Branch strategy:** Work on a feature branch `landing-toggle`. The unmerged `vector-logs` branch is a separate concern; this branch should be created from `main` (NOT from `vector-logs`) so the toggle work doesn't accidentally bundle the logging changes.

**Note on testing:** Per the spec, unit tests cover `extract_toggle_calls`, `set_yaml_bool_at_path`, and `basic_auth`. The Forgejo client is exercised via in-cluster verification only (HTTP-mocking is overkill for this scope).

---

## Task 1: Add Cargo dependencies + `auth.rs` basic-auth middleware

**Files:**
- Modify: `demo-landing/Cargo.toml`
- Create: `demo-landing/src/auth.rs`
- Modify: `demo-landing/src/main.rs` (add `mod auth;`)

- [ ] **Step 1: Add new dependencies to `demo-landing/Cargo.toml`**

Edit the existing `[dependencies]` section. Add (preserving existing entries):

```toml
base64 = "0.22"
reqwest = { version = "0.12", default-features = false, features = ["rustls-tls", "json"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
serde_yaml = "0.9"
```

The full section after the edit (existing crates kept, new lines marked with `# new`):

```toml
[dependencies]
anyhow = "1"
async-trait = "0.1"
axum = "0.7"
base64 = "0.22"                                                                       # new
futures = "0.3"
k8s-openapi = { version = "0.23", features = ["v1_30"] }
kube = { version = "0.96", default-features = false, features = ["client", "rustls-tls"] }
mime_guess = "2"
minijinja = "2"
pulldown-cmark = { version = "0.12", default-features = false, features = ["html"] }
regex = "1"
reqwest = { version = "0.12", default-features = false, features = ["rustls-tls", "json"] }  # new
serde = { version = "1", features = ["derive"] }                                      # new
serde_json = "1"                                                                      # new
serde_yaml = "0.9"                                                                    # new
thiserror = "1"
tokio = { version = "1", features = ["rt-multi-thread", "macros", "signal", "fs", "net"] }
tower-http = { version = "0.5", features = ["trace"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter", "fmt"] }
```

(The `# new` comments are illustrative; keep the file clean of those when you save.)

- [ ] **Step 2: Create `demo-landing/src/auth.rs` with the failing tests**

```rust
//! HTTP basic-auth middleware. Applied to every route except `/healthz`
//! (which must remain unauthenticated for the kubelet liveness probe).

use axum::{
    extract::{Request, State},
    http::{header, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
};
use base64::Engine;

use crate::routes::AppState;

pub async fn basic_auth(
    State(state): State<AppState>,
    req: Request,
    next: Next,
) -> Result<Response, Response> {
    let provided = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|h| h.to_str().ok())
        .and_then(|h| h.strip_prefix("Basic "))
        .and_then(|b64| base64::engine::general_purpose::STANDARD.decode(b64).ok())
        .and_then(|bytes| String::from_utf8(bytes).ok());

    if let Some(creds) = provided {
        if let Some((u, p)) = creds.split_once(':') {
            if u == state.auth_user && p == state.auth_password {
                return Ok(next.run(req).await);
            }
        }
    }

    Err((
        StatusCode::UNAUTHORIZED,
        [(header::WWW_AUTHENTICATE, r#"Basic realm="demo-landing""#)],
        "Authentication required",
    )
        .into_response())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request as HttpRequest, StatusCode};
    use axum::routing::get;
    use axum::Router;
    use std::sync::Arc;
    use tower::ServiceExt;

    use crate::forgejo::ForgejoClient;
    use crate::kube::ServiceLookup;

    // Minimal AppState for testing — uses None/dummy values for non-auth fields.
    fn test_state(user: &str, password: &str) -> AppState {
        AppState {
            content_dir: "/tmp".into(),
            lookup: Arc::new(NullLookup),
            forgejo: Arc::new(ForgejoClient::for_testing()),
            auth_user: user.to_string(),
            auth_password: password.to_string(),
        }
    }

    struct NullLookup;
    #[async_trait::async_trait]
    impl ServiceLookup for NullLookup {
        async fn lookup_service(&self, _: &str) -> Result<crate::kube::ServiceInfo, crate::kube::LookupError> {
            unimplemented!("not used in auth tests")
        }
        async fn pick_node_ip(&self) -> Result<String, crate::kube::LookupError> {
            unimplemented!("not used in auth tests")
        }
    }

    fn router() -> Router<()> {
        let state = test_state("admin", "secret");
        Router::new()
            .route("/protected", get(|| async { "ok" }))
            .layer(axum::middleware::from_fn_with_state(state.clone(), basic_auth))
            .with_state(state)
    }

    fn basic_header(user: &str, password: &str) -> String {
        let raw = format!("{}:{}", user, password);
        format!(
            "Basic {}",
            base64::engine::general_purpose::STANDARD.encode(raw)
        )
    }

    #[tokio::test]
    async fn rejects_request_without_authorization_header() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .uri("/protected")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(
            resp.headers().get(header::WWW_AUTHENTICATE).unwrap(),
            r#"Basic realm="demo-landing""#
        );
    }

    #[tokio::test]
    async fn accepts_correct_credentials() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .uri("/protected")
                    .header(header::AUTHORIZATION, basic_header("admin", "secret"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn rejects_wrong_password() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .uri("/protected")
                    .header(header::AUTHORIZATION, basic_header("admin", "wrong"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn rejects_malformed_base64() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .uri("/protected")
                    .header(header::AUTHORIZATION, "Basic not-valid-base64!!!")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }
}
```

The tests reference `ForgejoClient::for_testing()` and `AppState` fields that we add in Tasks 2 and 5. The compile error from those references is expected at this step — we'll resolve it by writing the missing modules in subsequent tasks.

- [ ] **Step 3: Add `tower` to dev-dependencies (for the `ServiceExt::oneshot` test helper)**

In `demo-landing/Cargo.toml`, find the `[dev-dependencies]` section and add `tower`:

```toml
[dev-dependencies]
tokio = { version = "1", features = ["rt-multi-thread", "macros", "test-util"] }
tower = { version = "0.5", features = ["util"] }   # new
```

- [ ] **Step 4: Register the module in `main.rs`**

Edit `demo-landing/src/main.rs`. Replace the existing module declarations at the top with (alphabetical order):

```rust
mod auth;
mod kube;
mod render;
mod routes;
mod template;
```

- [ ] **Step 5: cargo check (expected to fail until later tasks land the missing types)**

```bash
cd demo-landing && cargo check
```

Expected: compile errors mentioning `ForgejoClient`, `AppState.forgejo`, `AppState.auth_user`, `AppState.auth_password`. These resolve as Tasks 2, 5, 7 land. This intermediate state is acceptable because we commit once everything compiles together.

**Defer the commit until after Task 5 — do not commit at end of Task 1.**

---

## Task 2: Forgejo API client

**Files:**
- Create: `demo-landing/src/forgejo.rs`
- Modify: `demo-landing/src/main.rs` (add `mod forgejo;`)

- [ ] **Step 1: Create `demo-landing/src/forgejo.rs`**

```rust
//! Minimal Forgejo / Gitea-compatible HTTP client. Two operations:
//! `get_file` (read file contents + blob SHA) and `put_file` (commit new
//! contents using the SHA as an optimistic-locking handle).
//!
//! Configuration is via env vars; see `ForgejoClient::from_env`.

use std::time::Duration;

use base64::Engine;
use serde::Deserialize;
use thiserror::Error;

#[derive(Debug, Clone)]
pub struct FileContents {
    pub content: String,
    pub sha: String,
}

#[derive(Debug, Error)]
pub enum ForgejoError {
    #[error("file not found: {0}")]
    NotFound(String),

    #[error("file changed since read")]
    SHAConflict,

    #[error("forgejo api {status}: {body}")]
    Api { status: u16, body: String },

    #[error("http error: {0}")]
    Http(String),

    #[error("decode error: {0}")]
    Decode(String),
}

#[derive(Debug, Clone)]
pub struct ForgejoClient {
    base_url: String,
    owner: String,
    repo: String,
    branch: String,
    username: String,
    password: String,
    http: reqwest::Client,
}

#[derive(Deserialize)]
struct ContentsResponse {
    content: String,
    sha: String,
}

impl ForgejoClient {
    pub fn from_env() -> anyhow::Result<Self> {
        let base_url = std::env::var("FORGEJO_URL").unwrap_or_else(|_| {
            "http://forgejo-http.deployment.svc.cluster.local:3000".to_string()
        });
        let owner = std::env::var("FORGEJO_OWNER").unwrap_or_else(|_| "stackable".to_string());
        let repo = std::env::var("FORGEJO_REPO")
            .unwrap_or_else(|_| "openmetadata-dbt-demo".to_string());
        let branch = std::env::var("FORGEJO_BRANCH").unwrap_or_else(|_| "main".to_string());
        let username = std::env::var("FORGEJO_USERNAME")
            .map_err(|_| anyhow::anyhow!("FORGEJO_USERNAME env var is required"))?;
        let password = std::env::var("FORGEJO_PASSWORD")
            .map_err(|_| anyhow::anyhow!("FORGEJO_PASSWORD env var is required"))?;

        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(10))
            .build()?;

        Ok(Self {
            base_url,
            owner,
            repo,
            branch,
            username,
            password,
            http,
        })
    }

    /// Construct a no-op client for unit tests that never make real calls.
    pub fn for_testing() -> Self {
        Self {
            base_url: "http://invalid.test".into(),
            owner: "test".into(),
            repo: "test".into(),
            branch: "main".into(),
            username: "test".into(),
            password: "test".into(),
            http: reqwest::Client::new(),
        }
    }

    pub async fn get_file(&self, path: &str) -> Result<FileContents, ForgejoError> {
        let url = format!(
            "{}/api/v1/repos/{}/{}/contents/{}",
            self.base_url, self.owner, self.repo, path
        );

        let resp = self
            .http
            .get(&url)
            .basic_auth(&self.username, Some(&self.password))
            .query(&[("ref", self.branch.as_str())])
            .send()
            .await
            .map_err(|e| ForgejoError::Http(e.to_string()))?;

        let status = resp.status().as_u16();
        match status {
            200 => {
                let body: ContentsResponse = resp
                    .json()
                    .await
                    .map_err(|e| ForgejoError::Decode(e.to_string()))?;
                let cleaned: String = body.content.chars().filter(|c| !c.is_whitespace()).collect();
                let bytes = base64::engine::general_purpose::STANDARD
                    .decode(cleaned)
                    .map_err(|e| ForgejoError::Decode(e.to_string()))?;
                let content = String::from_utf8(bytes)
                    .map_err(|e| ForgejoError::Decode(e.to_string()))?;
                Ok(FileContents {
                    content,
                    sha: body.sha,
                })
            }
            404 => Err(ForgejoError::NotFound(path.to_string())),
            _ => {
                let body = resp.text().await.unwrap_or_default();
                Err(ForgejoError::Api { status, body })
            }
        }
    }

    pub async fn put_file(
        &self,
        path: &str,
        content: &str,
        sha: &str,
        message: &str,
    ) -> Result<(), ForgejoError> {
        let url = format!(
            "{}/api/v1/repos/{}/{}/contents/{}",
            self.base_url, self.owner, self.repo, path
        );
        let body = serde_json::json!({
            "content": base64::engine::general_purpose::STANDARD.encode(content),
            "sha": sha,
            "message": message,
            "branch": self.branch,
        });

        let resp = self
            .http
            .put(&url)
            .basic_auth(&self.username, Some(&self.password))
            .json(&body)
            .send()
            .await
            .map_err(|e| ForgejoError::Http(e.to_string()))?;

        let status = resp.status().as_u16();
        match status {
            200 | 201 => Ok(()),
            409 => Err(ForgejoError::SHAConflict),
            422 => {
                let body = resp.text().await.unwrap_or_default();
                if body.to_lowercase().contains("sha")
                    && body.to_lowercase().contains("does not match")
                {
                    Err(ForgejoError::SHAConflict)
                } else {
                    Err(ForgejoError::Api { status, body })
                }
            }
            _ => {
                let body = resp.text().await.unwrap_or_default();
                Err(ForgejoError::Api { status, body })
            }
        }
    }
}
```

- [ ] **Step 2: Register the module in `main.rs`**

Update the module list in `demo-landing/src/main.rs`:

```rust
mod auth;
mod forgejo;
mod kube;
mod render;
mod routes;
mod template;
```

- [ ] **Step 3: cargo check (still expected to fail at AppState references)**

```bash
cd demo-landing && cargo check
```

Expected: errors limited to missing `forgejo`/`auth_user`/`auth_password` fields on `AppState` (resolved in Task 5).

**Still no commit at end of Task 2.**

---

## Task 3: Template helper — `extract_toggle_calls`, `ToggleResolution`, `toggle` minijinja function

**Files:**
- Modify: `demo-landing/src/template.rs`

The existing `template.rs` defines `extract_service_names` + `build_env(resolved: Arc<HashMap<String, String>>)`. We extend the file (additive — don't break the existing nodeport surface), then change `build_env`'s signature in Task 4 when we update `render.rs` to call it differently.

- [ ] **Step 1: Replace the contents of `demo-landing/src/template.rs`**

Overwrite the file with:

```rust
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
        Some(ToggleResolution::Error { reason }) | None => {
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
```

- [ ] **Step 2: cargo check (will still fail at routes/render call sites until Tasks 4 + 5 land)**

```bash
cd demo-landing && cargo check
```

Expected: errors localized to `routes.rs` / `render.rs` calling the old single-arg `build_env(resolved)`. We fix those in Task 4.

**No commit yet.**

---

## Task 4: Render pipeline — `set_yaml_bool_at_path` + extended `render`

**Files:**
- Modify: `demo-landing/src/render.rs`

- [ ] **Step 1: Replace the contents of `demo-landing/src/render.rs`**

```rust
//! Full render pipeline: markdown source + resolution maps -> HTML page.

use crate::template::{self, ToggleResolution};
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
    layout_env
        .add_template("layout", LAYOUT_SRC)
        .map_err(|e| RenderError::Layout(e.to_string()))?;
    let tmpl = layout_env
        .get_template("layout")
        .map_err(|e| RenderError::Layout(e.to_string()))?;
    tmpl.render(context! { title => title, content => body_html })
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
        let html = render(md, np, tg, "t").unwrap();
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
        let html = render(md, Arc::new(np), tg, "t").unwrap();
        assert!(html.contains(r#"href="https://10.0.0.1:30080/apps""#));
    }

    #[test]
    fn missing_service_renders_error_comment_and_keeps_page() {
        let md = r#"{{ nodeport "missing" }} See <a href="http://example.com">svc</a>"#;
        let (np, tg) = empty_maps();
        let html = render(md, np, tg, "t").unwrap();
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
        let html = render(md, np, Arc::new(tg), "t").unwrap();
        assert!(html.contains(r#"<form class="cell-toggle""#));
        assert!(html.contains(r#"action="/toggle""#));
        assert!(html.contains("switch-off"));
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
```

- [ ] **Step 2: cargo check (still fails at routes.rs)**

```bash
cd demo-landing && cargo check
```

Expected: errors limited to `routes.rs::index` calling `render(..., resolved, "Demo")` (old signature) and `AppState` missing the new fields. Both resolved in Task 5.

**No commit yet.**

---

## Task 5: Routes — `AppState` extension + `toggle` handler + error pages

**Files:**
- Modify: `demo-landing/src/routes.rs`

- [ ] **Step 1: Replace the contents of `demo-landing/src/routes.rs`**

```rust
//! axum handlers.

use crate::forgejo::{ForgejoClient, ForgejoError};
use crate::kube::ServiceLookup;
use crate::render::{self, set_yaml_bool_at_path};
use crate::template::{self, ToggleResolution};
use axum::{
    body::Body,
    extract::{Form, Path, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Redirect, Response},
};
use futures::future::join_all;
use serde::Deserialize;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

const STYLES_CSS: &str = include_str!("../assets/styles.css");

#[derive(Clone)]
pub struct AppState {
    pub content_dir: PathBuf,
    pub lookup: Arc<dyn ServiceLookup>,
    pub forgejo: Arc<ForgejoClient>,
    pub auth_user: String,
    pub auth_password: String,
}

pub async fn healthz() -> &'static str {
    "ok"
}

pub async fn styles() -> Response {
    ([(header::CONTENT_TYPE, "text/css; charset=utf-8")], STYLES_CSS).into_response()
}

// Brand fonts bundled into the binary so the landing page has no external
// font dependency (matches the portal's @fontsource approach for GDPR).
const FONT_NOTO_400: &[u8] = include_bytes!("../assets/fonts/NotoSans-400.woff2");
const FONT_NOTO_700: &[u8] = include_bytes!("../assets/fonts/NotoSans-700.woff2");
const FONT_IBM_400: &[u8] = include_bytes!("../assets/fonts/IBMPlexMono-400.woff2");
const FONT_IBM_700: &[u8] = include_bytes!("../assets/fonts/IBMPlexMono-700.woff2");

pub async fn fonts(Path(name): Path<String>) -> Response {
    let bytes: &[u8] = match name.as_str() {
        "NotoSans-400.woff2" => FONT_NOTO_400,
        "NotoSans-700.woff2" => FONT_NOTO_700,
        "IBMPlexMono-400.woff2" => FONT_IBM_400,
        "IBMPlexMono-700.woff2" => FONT_IBM_700,
        _ => return (StatusCode::NOT_FOUND, "").into_response(),
    };
    let mut headers = HeaderMap::new();
    headers.insert(header::CONTENT_TYPE, "font/woff2".parse().unwrap());
    headers.insert(
        header::CACHE_CONTROL,
        "public, max-age=31536000, immutable".parse().unwrap(),
    );
    (headers, Body::from(bytes)).into_response()
}

pub async fn index(State(state): State<AppState>) -> Response {
    let path = state.content_dir.join("index.md");
    let markdown = match tokio::fs::read_to_string(&path).await {
        Ok(s) => s,
        Err(e) => {
            tracing::error!(path = %path.display(), error = %e, "index.md read failed");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                format!(
                    "<h1>demo-landing</h1><p>Cannot read <code>{}</code>: {}</p>",
                    path.display(),
                    e
                ),
            )
                .into_response();
        }
    };

    // Pre-resolve nodeports (existing).
    let names = template::extract_service_names(&markdown);
    let mut nodeports: HashMap<String, String> = HashMap::new();
    if !names.is_empty() {
        let node_ip = state.lookup.pick_node_ip().await;
        let services = join_all(
            names
                .iter()
                .map(|n| async { (n.clone(), state.lookup.lookup_service(n).await) }),
        )
        .await;

        match node_ip {
            Ok(ip) => {
                for (name, res) in services {
                    match res {
                        Ok(info) => {
                            nodeports.insert(name, format!("{}:{}", ip, info.node_port));
                        }
                        Err(err) => {
                            tracing::warn!(service = %name, error = %err, "nodeport lookup failed");
                            nodeports
                                .insert(name.clone(), format!("<!-- nodeport error: {} -->", err));
                        }
                    }
                }
            }
            Err(err) => {
                tracing::warn!(error = %err, "node IP lookup failed");
                for (name, _) in services {
                    nodeports.insert(name, format!("<!-- nodeport error: {} -->", err));
                }
            }
        }
    }

    // Pre-resolve toggles (new).
    let toggle_pairs = template::extract_toggle_calls(&markdown);
    let mut toggles: HashMap<(String, String), ToggleResolution> = HashMap::new();
    if !toggle_pairs.is_empty() {
        // Unique file paths: fetch each only once.
        let mut unique_paths: Vec<String> = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for (p, _) in &toggle_pairs {
            if seen.insert(p.clone()) {
                unique_paths.push(p.clone());
            }
        }
        let fetched = join_all(
            unique_paths
                .into_iter()
                .map(|p| async {
                    let r = state.forgejo.get_file(&p).await;
                    (p, r)
                }),
        )
        .await;

        // Cache: file path -> Result<(parsed Value, sha), reason string>
        let mut file_cache: HashMap<String, Result<(serde_yaml::Value, String), String>> =
            HashMap::new();
        for (path, res) in fetched {
            match res {
                Ok(fc) => match serde_yaml::from_str::<serde_yaml::Value>(&fc.content) {
                    Ok(v) => {
                        file_cache.insert(path, Ok((v, fc.sha)));
                    }
                    Err(e) => {
                        file_cache.insert(path, Err(format!("yaml parse error: {}", e)));
                    }
                },
                Err(e) => {
                    file_cache.insert(path, Err(format!("forgejo: {}", e)));
                }
            }
        }

        for (file_path, key_path) in toggle_pairs {
            let resolution = match file_cache.get(&file_path) {
                Some(Ok((yaml, sha))) => {
                    match render::get_yaml_bool_at_path(yaml, &key_path) {
                        Ok(v) => ToggleResolution::Ok {
                            sha: sha.clone(),
                            value: v,
                        },
                        Err(e) => ToggleResolution::Error {
                            reason: format!("{}", e),
                        },
                    }
                }
                Some(Err(reason)) => ToggleResolution::Error {
                    reason: reason.clone(),
                },
                None => ToggleResolution::Error {
                    reason: "file fetch missing from cache".into(),
                },
            };
            toggles.insert((file_path, key_path), resolution);
        }
    }

    match render::render(&markdown, Arc::new(nodeports), Arc::new(toggles), "Demo") {
        Ok(html) => (
            [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
            html,
        )
            .into_response(),
        Err(e) => {
            tracing::error!(error = %e, "render failed");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                format!("<h1>render error</h1><pre>{}</pre>", e),
            )
                .into_response()
        }
    }
}

#[derive(Deserialize, Debug)]
pub struct ToggleRequest {
    pub path: String,
    pub key: String,
    pub sha: String,
    pub current_value: bool,
}

pub async fn toggle(
    State(state): State<AppState>,
    Form(req): Form<ToggleRequest>,
) -> Response {
    let new_value = !req.current_value;

    let file = match state.forgejo.get_file(&req.path).await {
        Ok(f) => f,
        Err(e) => return forgejo_error_page(&req.path, e),
    };

    let mut yaml: serde_yaml::Value = match serde_yaml::from_str(&file.content) {
        Ok(v) => v,
        Err(e) => return yaml_error_page(&req.path, &format!("parse error: {}", e)),
    };

    if let Err(e) = set_yaml_bool_at_path(&mut yaml, &req.key, new_value) {
        return yaml_error_page(&req.path, &format!("{}", e));
    }

    let new_content = match serde_yaml::to_string(&yaml) {
        Ok(s) => s,
        Err(e) => return yaml_error_page(&req.path, &format!("serialize error: {}", e)),
    };

    let msg = format!("Toggle {} on {}", req.key, req.path);

    match state
        .forgejo
        .put_file(&req.path, &new_content, &req.sha, &msg)
        .await
    {
        Ok(_) => Redirect::to("/").into_response(),
        Err(ForgejoError::SHAConflict) => stale_view_page(&req.path),
        Err(e) => forgejo_error_page(&req.path, e),
    }
}

fn stale_view_page(path: &str) -> Response {
    let body = format!(
        r#"<!DOCTYPE html><html><head><title>Stale view</title>
<link rel="stylesheet" href="/styles.css"></head>
<body><main><h1>Stale view</h1>
<p>The file <code>{}</code> changed since the page loaded. Please <a href="/">reload</a> and try again.</p>
</main></body></html>"#,
        path
    );
    (
        StatusCode::CONFLICT,
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        body,
    )
        .into_response()
}

fn forgejo_error_page(path: &str, err: ForgejoError) -> Response {
    let body = format!(
        r#"<!DOCTYPE html><html><head><title>Forgejo error</title>
<link rel="stylesheet" href="/styles.css"></head>
<body><main><h1>Forgejo error</h1>
<p>While operating on <code>{}</code>:</p>
<pre>{}</pre>
<p><a href="/">Back to landing page</a></p>
</main></body></html>"#,
        path, err
    );
    (
        StatusCode::BAD_GATEWAY,
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        body,
    )
        .into_response()
}

fn yaml_error_page(path: &str, detail: &str) -> Response {
    let body = format!(
        r#"<!DOCTYPE html><html><head><title>YAML error</title>
<link rel="stylesheet" href="/styles.css"></head>
<body><main><h1>YAML error</h1>
<p>While operating on <code>{}</code>:</p>
<pre>{}</pre>
<p><a href="/">Back to landing page</a></p>
</main></body></html>"#,
        path, detail
    );
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        body,
    )
        .into_response()
}

pub async fn image(
    State(state): State<AppState>,
    Path(path): Path<String>,
) -> Response {
    let base = state.content_dir.join("images");
    let requested = base.join(&path);

    let normalized = normalize_path(&requested);
    let normalized_base = normalize_path(&base);
    if !normalized.starts_with(&normalized_base) {
        tracing::warn!(path = %path, "path traversal attempt refused");
        return (StatusCode::FORBIDDEN, "").into_response();
    }

    let canonical_base = match tokio::fs::canonicalize(&base).await {
        Ok(p) => p,
        Err(_) => return (StatusCode::NOT_FOUND, "").into_response(),
    };
    let canonical_requested = match tokio::fs::canonicalize(&requested).await {
        Ok(p) => p,
        Err(_) => return (StatusCode::NOT_FOUND, "").into_response(),
    };

    if !canonical_requested.starts_with(&canonical_base) {
        tracing::warn!(path = %path, "symlink traversal attempt refused");
        return (StatusCode::FORBIDDEN, "").into_response();
    }

    let bytes = match tokio::fs::read(&canonical_requested).await {
        Ok(b) => b,
        Err(_) => return (StatusCode::NOT_FOUND, "").into_response(),
    };

    let mime = mime_guess::from_path(&canonical_requested)
        .first_or_octet_stream()
        .to_string();

    let mut headers = HeaderMap::new();
    headers.insert(header::CONTENT_TYPE, mime.parse().unwrap());
    (headers, Body::from(bytes)).into_response()
}

fn normalize_path(path: &std::path::Path) -> PathBuf {
    use std::path::Component;
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            c => out.push(c),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kube::{ServiceInfo, ServiceLookup as _};

    use async_trait::async_trait;
    use std::collections::HashMap as StdMap;
    use std::sync::Mutex;

    struct LocalMock {
        services: Mutex<StdMap<String, ServiceInfo>>,
        node_ip: Mutex<Option<String>>,
    }

    impl LocalMock {
        fn new() -> Self {
            Self {
                services: Mutex::new(StdMap::new()),
                node_ip: Mutex::new(Some("10.0.0.1".into())),
            }
        }
    }

    #[async_trait]
    impl ServiceLookup for LocalMock {
        async fn lookup_service(
            &self,
            name: &str,
        ) -> Result<ServiceInfo, crate::kube::LookupError> {
            self.services
                .lock()
                .unwrap()
                .get(name)
                .cloned()
                .ok_or(crate::kube::LookupError::NotFound(name.into()))
        }
        async fn pick_node_ip(&self) -> Result<String, crate::kube::LookupError> {
            self.node_ip
                .lock()
                .unwrap()
                .clone()
                .ok_or(crate::kube::LookupError::NoReadyNode)
        }
    }

    fn test_state(tmp: &std::path::Path) -> AppState {
        AppState {
            content_dir: tmp.into(),
            lookup: Arc::new(LocalMock::new()),
            forgejo: Arc::new(ForgejoClient::for_testing()),
            auth_user: "admin".into(),
            auth_password: "secret".into(),
        }
    }

    #[tokio::test]
    async fn image_refuses_path_traversal() {
        let tmp = tempdir_with_images();
        let resp = image(State(test_state(tmp.path())), Path("../../etc/passwd".into())).await;
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn image_returns_404_for_missing() {
        let tmp = tempdir_with_images();
        let resp = image(State(test_state(tmp.path())), Path("does-not-exist.png".into())).await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn healthz_returns_ok() {
        assert_eq!(healthz().await, "ok");
    }

    fn tempdir_with_images() -> TempDir {
        let base = std::env::temp_dir().join(format!(
            "demo-landing-test-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(base.join("images")).unwrap();
        std::fs::write(base.join("images").join("pixel.png"), b"\x89PNG\r\n").unwrap();
        TempDir { path: base }
    }

    struct TempDir {
        path: PathBuf,
    }
    impl TempDir {
        fn path(&self) -> &std::path::Path {
            &self.path
        }
    }
    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
}
```

- [ ] **Step 2: cargo check**

```bash
cd demo-landing && cargo check
```

Expected: clean compile, no errors. Warnings about unused things in `main.rs` (not yet wiring `forgejo`/`auth`) are acceptable until Task 6.

- [ ] **Step 3: cargo test for the modules we've changed so far**

```bash
cd demo-landing && cargo test --quiet
```

Expected: tests in `auth::`, `template::`, `render::`, `routes::` all pass. The auth test creates a minimal `AppState`; with the current `main.rs` not wiring the new fields, the binary may or may not start, but the tests don't depend on that.

If the test binary fails to compile because `main.rs` is still trying to construct an old-shape `AppState`, fix that minimally now by making `main.rs` build the new shape. The full Task 6 wiring still happens cleanly afterwards.

- [ ] **Step 4: Single commit landing Tasks 1–5**

This is the first commit point in the plan. Everything compiles and the unit tests pass.

```bash
git add demo-landing/Cargo.toml demo-landing/src/auth.rs demo-landing/src/forgejo.rs \
        demo-landing/src/template.rs demo-landing/src/render.rs demo-landing/src/routes.rs \
        demo-landing/src/main.rs
git commit -m "demo-landing: add toggle helper + Forgejo client + basic-auth middleware"
```

(The commit may also need to include `Cargo.lock` if it was regenerated — `git status` will say. Add it if so.)

---

## Task 6: Wire `main.rs` — env, ForgejoClient, router split, auth middleware

**Files:**
- Modify: `demo-landing/src/main.rs`

- [ ] **Step 1: Replace `demo-landing/src/main.rs`**

```rust
mod auth;
mod forgejo;
mod kube;
mod render;
mod routes;
mod template;

use async_trait::async_trait;
use axum::{routing::{get, post}, Router};
use k8s_openapi::api::core::v1::{Node, Service};
use kube::{api::ListParams, Api, Client};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use crate::forgejo::ForgejoClient;
use crate::kube::{LookupError, ServiceInfo, ServiceLookup};
use crate::routes::AppState;

struct KubeClientLookup {
    client: Client,
}

#[async_trait]
impl ServiceLookup for KubeClientLookup {
    async fn lookup_service(&self, name: &str) -> Result<ServiceInfo, LookupError> {
        let api: Api<Service> = Api::all(self.client.clone());
        let list = api
            .list(&ListParams::default())
            .await
            .map_err(|e| LookupError::KubeApi(e.to_string()))?;

        let matches: Vec<&Service> = list
            .iter()
            .filter(|s| s.metadata.name.as_deref() == Some(name))
            .collect();

        match matches.as_slice() {
            [] => Err(LookupError::NotFound(name.into())),
            [svc] => {
                let spec = svc
                    .spec
                    .as_ref()
                    .ok_or_else(|| LookupError::KubeApi("service missing spec".into()))?;
                let svc_type = spec.type_.as_deref().unwrap_or("ClusterIP");
                if svc_type != "NodePort" {
                    return Err(LookupError::NotNodePort(name.into()));
                }
                let first_port = spec
                    .ports
                    .as_ref()
                    .and_then(|ps| ps.first())
                    .ok_or_else(|| LookupError::NoNodePortOnPort(name.into()))?;
                let node_port = first_port
                    .node_port
                    .ok_or_else(|| LookupError::NoNodePortOnPort(name.into()))?;
                Ok(ServiceInfo {
                    namespace: svc
                        .metadata
                        .namespace
                        .clone()
                        .unwrap_or_else(|| "default".into()),
                    node_port: node_port as u16,
                })
            }
            many => Err(LookupError::Ambiguous {
                name: name.into(),
                namespaces: many
                    .iter()
                    .map(|s| {
                        s.metadata
                            .namespace
                            .clone()
                            .unwrap_or_else(|| "?".into())
                    })
                    .collect(),
            }),
        }
    }

    async fn pick_node_ip(&self) -> Result<String, LookupError> {
        let api: Api<Node> = Api::all(self.client.clone());
        let list = api
            .list(&ListParams::default())
            .await
            .map_err(|e| LookupError::KubeApi(e.to_string()))?;

        for node in list.iter() {
            let is_ready = node
                .status
                .as_ref()
                .and_then(|s| s.conditions.as_ref())
                .map(|conds| {
                    conds
                        .iter()
                        .any(|c| c.type_ == "Ready" && c.status == "True")
                })
                .unwrap_or(false);
            if !is_ready {
                continue;
            }
            let addrs = node.status.as_ref().and_then(|s| s.addresses.as_ref());
            if let Some(addrs) = addrs {
                if let Some(ext) = addrs.iter().find(|a| a.type_ == "ExternalIP") {
                    return Ok(ext.address.clone());
                }
                if let Some(int) = addrs.iter().find(|a| a.type_ == "InternalIP") {
                    return Ok(int.address.clone());
                }
            }
        }
        Err(LookupError::NoReadyNode)
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_env("LOG_LEVEL")
                .or_else(|_| tracing_subscriber::EnvFilter::try_new("info"))
                .unwrap(),
        )
        .init();

    let content_dir: PathBuf = std::env::var("CONTENT_DIR")
        .unwrap_or_else(|_| "/content".into())
        .into();
    let listen_addr: SocketAddr = std::env::var("LISTEN_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:8080".into())
        .parse()?;
    let auth_user = std::env::var("AUTH_USER")
        .map_err(|_| anyhow::anyhow!("AUTH_USER env var is required"))?;
    let auth_password = std::env::var("AUTH_PASSWORD")
        .map_err(|_| anyhow::anyhow!("AUTH_PASSWORD env var is required"))?;

    let kube_client = Client::try_default().await?;
    let lookup: Arc<dyn ServiceLookup> = Arc::new(KubeClientLookup { client: kube_client });

    let forgejo = Arc::new(ForgejoClient::from_env()?);

    let state = AppState {
        content_dir,
        lookup,
        forgejo,
        auth_user,
        auth_password,
    };

    let public = Router::new().route("/healthz", get(routes::healthz));

    let private = Router::new()
        .route("/", get(routes::index))
        .route("/styles.css", get(routes::styles))
        .route("/fonts/:name", get(routes::fonts))
        .route("/images/*path", get(routes::image))
        .route("/toggle", post(routes::toggle))
        .layer(axum::middleware::from_fn_with_state(state.clone(), auth::basic_auth));

    let app = Router::new()
        .merge(public)
        .merge(private)
        .layer(tower_http::trace::TraceLayer::new_for_http())
        .with_state(state);

    tracing::info!(%listen_addr, "starting demo-landing");
    let listener = tokio::net::TcpListener::bind(listen_addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
```

Note the imports: `use axum::routing::{get, post}` (added `post`); plus `use crate::forgejo::ForgejoClient;`. The `kube` symbol still resolves to the kube-rs crate via `use kube::{api::ListParams, Api, Client};` because we import items, not the crate name itself — and `crate::kube` (our module) is referenced separately via the `use crate::kube::...` line.

If you hit the same `mod kube;` shadowing issue we hit during the original wiring of this tool, switch to `use ::kube::{api::ListParams, Api, Client};` (absolute path).

- [ ] **Step 2: Build**

```bash
cd demo-landing && cargo build
```

Expected: clean compile.

- [ ] **Step 3: Full test suite**

```bash
cd demo-landing && cargo test
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add demo-landing/src/main.rs
git commit -m "main: wire ForgejoClient + basic auth + toggle route + router split"
```

---

## Task 7: CSS — toggle switch + error fragment styles

**Files:**
- Modify: `demo-landing/assets/styles.css`

- [ ] **Step 1: Insert the new CSS block**

Open `demo-landing/assets/styles.css`. Find the `/* Footer */` comment block. Insert the new rules immediately above it (after the existing `.btn:hover` rule):

```css
/* Toggle switch + error */

.cell-toggle {
  display: inline-block;
  margin: 0;
}

.switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 22px;
  border: 1px solid var(--color-base-300);
  border-radius: 999px;
  background: var(--color-base-200);
  cursor: pointer;
  padding: 0;
  transition: background 120ms ease-in-out;
}

.switch.switch-on {
  background: var(--color-primary);
  border-color: var(--color-primary);
}

.switch-knob {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--color-base-100);
  transition: left 120ms ease-in-out;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}

.switch.switch-on .switch-knob {
  left: 23px;
}

.switch:hover {
  filter: brightness(0.97);
}

.toggle-error {
  display: inline-flex;
  align-items: center;
  gap: 0.25em;
  color: var(--color-secondary);
  font-family: var(--font-mono);
  font-size: 0.85em;
  border-bottom: 1px dotted var(--color-secondary);
  cursor: help;
}
```

- [ ] **Step 2: Build (CSS is bundled into the binary via include_str!)**

```bash
cd demo-landing && cargo build
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add demo-landing/assets/styles.css
git commit -m "demo-landing: add toggle switch + error CSS"
```

---

## Task 8: Basic-auth secret + Deployment env updates

**Files:**
- Create: `secrets/manifests/demo-landing/demo-landing-basic-auth.yaml`
- Create: `platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml` (generated)
- Modify: `platform/manifests/demo-landing/deployment.yaml`

- [ ] **Step 1: Generate the basic-auth password**

```bash
PW=$(openssl rand -base64 24)
echo "AUTH_PASSWORD=$PW" > /tmp/landing-auth.txt
echo "Save this somewhere — you'll need it to log in. Then delete /tmp/landing-auth.txt."
cat /tmp/landing-auth.txt
```

(Random base64-24 password. Keep the plaintext until Step 2 is committed; nothing else in the repo references it.)

- [ ] **Step 2: Create the plaintext Secret**

```bash
mkdir -p secrets/manifests/demo-landing
cat > secrets/manifests/demo-landing/demo-landing-basic-auth.yaml <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: demo-landing-basic-auth
  namespace: deployment
stringData:
  AUTH_USER: stackable
  AUTH_PASSWORD: $PW
EOF
```

Verify:

```bash
python -c "import yaml; list(yaml.safe_load_all(open('secrets/manifests/demo-landing/demo-landing-basic-auth.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Seal**

```bash
just seal-secrets
```

Expected: a new line about processing `demo-landing-basic-auth.yaml`. The output file is `platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml`.

```bash
ls -la platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml
```

Expected: file present, non-zero size.

- [ ] **Step 4: Update `platform/manifests/demo-landing/deployment.yaml`**

Find the `landing` container's `env:` block. Replace the existing block with the expanded one below (existing keys preserved, new ones added):

```yaml
        - name: landing
          image: oci.stackable.tech/sandbox/demo-landing:0.1.0-dev
          imagePullPolicy: Always
          env:
            - name: CONTENT_DIR
              value: /git/current/website
            - name: LISTEN_ADDR
              value: 0.0.0.0:8080
            - name: LOG_LEVEL
              value: info
            - name: FORGEJO_URL
              value: http://forgejo-http.deployment.svc.cluster.local:3000
            - name: FORGEJO_OWNER
              value: stackable
            - name: FORGEJO_REPO
              value: openmetadata-dbt-demo
            - name: FORGEJO_BRANCH
              value: main
            - name: FORGEJO_USERNAME
              valueFrom:
                secretKeyRef:
                  name: forgejo-admin-secret
                  key: username
            - name: FORGEJO_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: forgejo-admin-secret
                  key: password
          envFrom:
            - secretRef:
                name: demo-landing-basic-auth
          ports:
            - name: http
              containerPort: 8080
```

(Everything below `ports:` stays unchanged from the existing manifest.)

- [ ] **Step 5: Validate the deployment YAML still parses**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/demo-landing/deployment.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add secrets/manifests/demo-landing/demo-landing-basic-auth.yaml \
        platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml \
        platform/manifests/demo-landing/deployment.yaml
git commit -m "demo-landing: provision basic-auth Secret + wire Forgejo creds"
```

- [ ] **Step 7: Clean up the scratch credentials file**

```bash
rm -f /tmp/landing-auth.txt
```

(Save the password somewhere safer first if you haven't already.)

---

## Task 9: Build image, push, deploy, verify

**Files:** none — verification + image rollout.

- [ ] **Step 1: Build and push the new image**

```bash
just build-landing-image
```

Expected: docker build succeeds; push to `oci.stackable.tech/sandbox/demo-landing:0.1.0-dev` succeeds.

- [ ] **Step 2: Push the branch**

```bash
git push origin landing-toggle
```

(If the user wants to merge first:)

```bash
git checkout main
git merge --ff-only landing-toggle
git push origin main
```

- [ ] **Step 3: Roll the demo-landing pod to pull the new image**

```bash
kubectl -n deployment rollout restart deployment/demo-landing
kubectl -n deployment rollout status deployment/demo-landing --timeout=2m
```

Expected: rollout completes with 1/1 ready.

- [ ] **Step 4: Verify the basic-auth gate**

```bash
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
curl -s -o /dev/null -w '%{http_code}\n' "http://$NODE_IP:30088/"
# expect 401
curl -s -o /dev/null -w '%{http_code}\n' "http://$NODE_IP:30088/healthz"
# expect 200
curl -s -o /dev/null -w '%{http_code}\n' -u "stackable:<password>" "http://$NODE_IP:30088/"
# expect 200
```

- [ ] **Step 5: Add a sample toggle to `website/index.md`**

Pick a Stackable CRD that has `spec.clusterOperations.stopped` configured (you'll add the key to one if it isn't there yet — the toggle helper needs the key to exist as a boolean to render). For example, edit `platform/manifests/hdfs/hdfs.yaml` to add `clusterOperations: { stopped: false }` under `spec`, commit + push, then add the toggle to `website/index.md`:

```markdown
## Component control

| Component | Stopped |
|---|---|
| HDFS | {{ toggle "platform/manifests/hdfs/hdfs.yaml" "spec.clusterOperations.stopped" }} |
```

Push to main; wait ~30 s for git-sync to refresh; reload the landing page (with auth). Expect the styled switch in the table cell.

- [ ] **Step 6: Round-trip test**

Click the switch. Expected:
- 302 redirect → page reloads.
- Switch state has flipped.
- A new commit appears on `main` in the Forgejo UI with message `Toggle spec.clusterOperations.stopped on platform/manifests/hdfs/hdfs.yaml`.
- Within ~60 s, ArgoCD reconciles and the operator scales HDFS down (or up).

- [ ] **Step 7: Stale-view path (optional)**

In one browser tab, leave the landing page open. In another tab, open the file in Forgejo's UI and edit it (e.g. flip `stopped` directly). Save. Back in the first tab, click the switch. Expected: 409 page with a reload link.

---

## Self-Review

**Spec coverage:**

- §"New per-request flow on `GET /`" → Task 5 (`index` handler updates).
- §"New `POST /toggle` flow" → Task 5 (`toggle` handler).
- §"Authentication" — middleware → Task 1 (`auth.rs`); routing split → Task 6 (`main.rs`); page-auth Secret → Task 8.
- §"Template helper" → Task 3.
- §"HTML form fragment" → Task 3 (`render_toggle_html` inside the helper).
- §"CSS additions" → Task 7.
- §"`POST /toggle` handler" → Task 5.
- §"Forgejo API client" → Task 2.
- §"Configuration" — env vars: read in `main.rs` Task 6 + `forgejo.rs` Task 2; declared in `deployment.yaml` Task 8.
- §"Basic-auth middleware" → Task 1 (`auth.rs`).
- §"Routing in `main.rs`" → Task 6.
- §"Files & manifest changes" — Rust crate covered Tasks 1–7; manifest changes Task 8.
- §"Error handling" — `forgejo_error_page`/`yaml_error_page`/`stale_view_page` in Task 5; basic-auth 401 in Task 1; render-time helper errors in Task 3.
- §"Testing" — unit tests in Tasks 1, 3, 4; in-cluster verification Task 9.

**Placeholder scan:** no `TBD`/`TODO`/`fill in details`/`Similar to Task N` tokens. The `<password>` token in Task 9 Step 4 is a runtime-substituted secret, not a plan placeholder; the engineer reads it from the file generated in Task 8 Step 1.

**Type / name consistency:**

- `AppState { content_dir, lookup, forgejo, auth_user, auth_password }` — same shape across Task 1 (test mock), Task 5 (declaration), Task 6 (construction).
- `ForgejoClient::from_env()` / `for_testing()` / `get_file(path) -> Result<FileContents, ForgejoError>` / `put_file(path, content, sha, message) -> Result<(), ForgejoError>` — consistent across Tasks 2, 5, 6.
- `ToggleResolution::{Ok { sha, value }, Error { reason }}` — Tasks 3, 5.
- `ToggleRequest { path, key, sha, current_value }` — Task 5 (defined and consumed).
- `set_yaml_bool_at_path(&mut yaml, key_path, new_value)` — defined Task 4, called Task 5.
- `get_yaml_bool_at_path(&yaml, key_path)` — defined Task 4, called Task 5 in `index`'s pre-resolution.
- `extract_toggle_calls(markdown) -> Vec<(String, String)>` — defined Task 3, called Task 5.
- `build_env(nodeports, toggles)` — new signature in Task 3, called from Task 4's `render`.
- `render(markdown, nodeports, toggles, title)` — new signature in Task 4, called from Task 5.
- Env var names (`AUTH_USER`, `AUTH_PASSWORD`, `FORGEJO_USERNAME`, `FORGEJO_PASSWORD`, `FORGEJO_URL`, `FORGEJO_OWNER`, `FORGEJO_REPO`, `FORGEJO_BRANCH`) — consistent across the spec, the Rust env reads (Tasks 2, 6), and the Deployment manifest (Task 8).
- Secret names (`demo-landing-basic-auth`, `forgejo-admin-secret`) — Tasks 8 and 6/2 wiring.
- Routes (`/`, `/styles.css`, `/fonts/:name`, `/images/*path`, `/toggle`, `/healthz`) — declared Task 6, handlers Task 5 + existing tool, basic-auth scope (everything except `/healthz`) Task 6.
