# demo-landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small Rust web server (`demo-landing`) that renders a single markdown landing page, expanding `{{ nodeport "svc" }}` placeholders to `<node-ip>:<nodePort>` at request time via the Kubernetes API, and deploy it into the demo cluster with a `git-sync` sidecar fetching content from the repo.

**Architecture:** axum HTTP server in a single binary, plus a `git-sync` sidecar that syncs the repo into a shared `emptyDir`. The binary reads `index.md` on each request, substitutes `nodeport` placeholders (pre-resolving each call through the Kubernetes API), renders markdown to HTML with `pulldown-cmark`, wraps it in an embedded HTML layout, and serves it. ServiceAccount + ClusterRole gives read-only access to `services` and `nodes`.

**Tech Stack:** Rust, axum, tokio, kube-rs + k8s-openapi, pulldown-cmark, minijinja, tracing, distroless runtime image, Kubernetes, ArgoCD, git-sync.

**Spec:** `docs/superpowers/specs/2026-04-24-demo-landing-design.md`

**File structure summary:**

```
demo-landing/
├── Cargo.toml
├── .gitignore
├── Dockerfile
├── README.md
├── src/
│   ├── main.rs
│   ├── kube.rs
│   ├── template.rs
│   ├── render.rs
│   └── routes.rs
└── assets/
    ├── layout.html
    └── styles.css

website/
├── index.md
└── images/.gitkeep

platform/applications/demo-landing.yaml
platform/manifests/demo-landing/
├── serviceaccount.yaml
├── clusterrole.yaml
├── clusterrolebinding.yaml
├── deployment.yaml
└── service.yaml

justfile                           # add build-landing-image recipe
```

---

## Task 1: Scaffold the Rust crate

**Files:**
- Create: `demo-landing/Cargo.toml`
- Create: `demo-landing/.gitignore`
- Create: `demo-landing/src/main.rs`
- Create: `demo-landing/README.md`

- [ ] **Step 1: Create `demo-landing/Cargo.toml`**

```toml
[package]
name = "demo-landing"
version = "0.1.0"
edition = "2021"

[dependencies]
anyhow = "1"
async-trait = "0.1"
axum = "0.7"
futures = "0.3"
k8s-openapi = { version = "0.23", features = ["v1_30"] }
kube = { version = "0.96", default-features = false, features = ["client", "rustls-tls"] }
mime_guess = "2"
minijinja = "2"
pulldown-cmark = { version = "0.12", default-features = false, features = ["html"] }
regex = "1"
thiserror = "1"
tokio = { version = "1", features = ["rt-multi-thread", "macros", "signal", "fs", "net"] }
tower-http = { version = "0.5", features = ["trace"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter", "fmt"] }

[dev-dependencies]
tokio = { version = "1", features = ["rt-multi-thread", "macros", "test-util"] }
```

- [ ] **Step 2: Create `demo-landing/.gitignore`**

```
/target
```

- [ ] **Step 3: Create `demo-landing/src/main.rs`**

```rust
use axum::{routing::get, Router};
use std::net::SocketAddr;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt::init();

    let listen_addr: SocketAddr = std::env::var("LISTEN_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:8080".to_string())
        .parse()?;

    let app = Router::new().route("/healthz", get(healthz));

    tracing::info!(%listen_addr, "starting demo-landing");
    let listener = tokio::net::TcpListener::bind(listen_addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn healthz() -> &'static str {
    "ok"
}
```

- [ ] **Step 4: Create `demo-landing/README.md`**

```markdown
# demo-landing

Tiny Rust web server that renders a markdown landing page and expands
`{{ nodeport "svc-name" }}` placeholders by querying the Kubernetes API
for the Service's nodePort and a node IP.

See `docs/superpowers/specs/2026-04-24-demo-landing-design.md` in the
parent repo for design details.

## Local run

    cargo run

Reads config from env:

| Var            | Default            |
|----------------|--------------------|
| `CONTENT_DIR`  | `/content`         |
| `LISTEN_ADDR`  | `0.0.0.0:8080`     |
| `LOG_LEVEL`    | `info`             |

Requires a reachable Kubernetes cluster (in-cluster config, or a local
`~/.kube/config` with read access to `services` and `nodes`).
```

- [ ] **Step 5: Verify the crate compiles**

Run from `demo-landing/`:

```bash
cd demo-landing && cargo build
```

Expected: warnings are OK; build succeeds. This may take several minutes on first build.

- [ ] **Step 6: Smoke-test the healthz endpoint**

```bash
cd demo-landing && cargo run &
sleep 2
curl -s http://127.0.0.1:8080/healthz
kill %1
```

Expected: prints `ok`.

- [ ] **Step 7: Commit**

```bash
git add demo-landing/
git commit -m "Scaffold demo-landing Rust crate"
```

---

## Task 2: `kube.rs` — types, `ServiceLookup` trait, `lookup_service`

**Files:**
- Create: `demo-landing/src/kube.rs`
- Modify: `demo-landing/src/main.rs` (add `mod kube;`)

- [ ] **Step 1: Write the failing unit tests at the bottom of `src/kube.rs`**

Create the file `demo-landing/src/kube.rs`:

```rust
use async_trait::async_trait;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServiceInfo {
    pub namespace: String,
    pub node_port: u16,
}

#[derive(Debug, Error)]
pub enum LookupError {
    #[error("service '{0}' not found in any namespace")]
    NotFound(String),

    #[error("service '{name}' is ambiguous across namespaces: {namespaces:?}")]
    Ambiguous { name: String, namespaces: Vec<String> },

    #[error("service '{0}' is not of type NodePort")]
    NotNodePort(String),

    #[error("service '{0}' has no nodePort on its first port")]
    NoNodePortOnPort(String),

    #[error("no Ready node with a usable IP address")]
    NoReadyNode,

    #[error("kubernetes API error: {0}")]
    KubeApi(String),
}

#[async_trait]
pub trait ServiceLookup: Send + Sync {
    async fn lookup_service(&self, name: &str) -> Result<ServiceInfo, LookupError>;
    async fn pick_node_ip(&self) -> Result<String, LookupError>;
}

// --- Implementation backed by a real kube::Client is added in Task 7 (main wiring). ---
// kube.rs keeps the trait + types; the real impl lives close to main so tests here stay pure.

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Mutex;

    /// Hand-rolled mock. Tests configure the two result maps up front.
    pub struct MockLookup {
        pub services: Mutex<HashMap<String, Result<ServiceInfo, LookupError>>>,
        pub node_ip: Mutex<Result<String, LookupError>>,
    }

    impl MockLookup {
        pub fn new() -> Self {
            Self {
                services: Mutex::new(HashMap::new()),
                node_ip: Mutex::new(Err(LookupError::NoReadyNode)),
            }
        }
        pub fn with_service(self, name: &str, info: ServiceInfo) -> Self {
            self.services.lock().unwrap().insert(name.into(), Ok(info));
            self
        }
        pub fn with_service_err(self, name: &str, err: LookupError) -> Self {
            self.services.lock().unwrap().insert(name.into(), Err(err));
            self
        }
        pub fn with_node_ip(self, ip: &str) -> Self {
            *self.node_ip.lock().unwrap() = Ok(ip.into());
            self
        }
    }

    #[async_trait]
    impl ServiceLookup for MockLookup {
        async fn lookup_service(&self, name: &str) -> Result<ServiceInfo, LookupError> {
            self.services
                .lock()
                .unwrap()
                .get(name)
                .cloned()
                .unwrap_or_else(|| Err(LookupError::NotFound(name.into())))
        }
        async fn pick_node_ip(&self) -> Result<String, LookupError> {
            self.node_ip.lock().unwrap().clone().map_err(|e| e)
        }
    }

    // Allow cloning LookupError in tests.
    impl Clone for LookupError {
        fn clone(&self) -> Self {
            match self {
                Self::NotFound(s) => Self::NotFound(s.clone()),
                Self::Ambiguous { name, namespaces } => Self::Ambiguous {
                    name: name.clone(),
                    namespaces: namespaces.clone(),
                },
                Self::NotNodePort(s) => Self::NotNodePort(s.clone()),
                Self::NoNodePortOnPort(s) => Self::NoNodePortOnPort(s.clone()),
                Self::NoReadyNode => Self::NoReadyNode,
                Self::KubeApi(s) => Self::KubeApi(s.clone()),
            }
        }
    }

    #[tokio::test]
    async fn lookup_returns_info_for_match() {
        let mock = MockLookup::new().with_service(
            "argocd-server-nodeport",
            ServiceInfo { namespace: "deployment".into(), node_port: 30080 },
        );
        let got = mock.lookup_service("argocd-server-nodeport").await.unwrap();
        assert_eq!(got.namespace, "deployment");
        assert_eq!(got.node_port, 30080);
    }

    #[tokio::test]
    async fn lookup_returns_not_found_for_unknown() {
        let mock = MockLookup::new();
        let err = mock.lookup_service("nope").await.unwrap_err();
        matches!(err, LookupError::NotFound(_));
    }
}
```

- [ ] **Step 2: Register the module in `main.rs`**

Edit `demo-landing/src/main.rs`. At the top (below the existing `use` lines), add:

```rust
mod kube;
```

The complete top of `main.rs` now reads:

```rust
mod kube;

use axum::{routing::get, Router};
use std::net::SocketAddr;
```

- [ ] **Step 3: Run the tests, confirm they pass**

```bash
cd demo-landing && cargo test --lib kube::
```

Expected: `2 passed`.

- [ ] **Step 4: Commit**

```bash
git add demo-landing/src/kube.rs demo-landing/src/main.rs
git commit -m "kube: ServiceLookup trait + types + mock + lookup tests"
```

---

## Task 3: `kube.rs` — `pick_node_ip` tests + refine errors

**Files:**
- Modify: `demo-landing/src/kube.rs` — add tests for `pick_node_ip`.

The trait method was already declared in Task 2. This task adds test coverage for the node-IP cases so mocks are exercised the same way they'll be used from `template.rs`.

- [ ] **Step 1: Append tests at the end of the existing `mod tests` block in `src/kube.rs`**

```rust
    #[tokio::test]
    async fn node_ip_returns_configured_ip() {
        let mock = MockLookup::new().with_node_ip("74.234.12.5");
        let got = mock.pick_node_ip().await.unwrap();
        assert_eq!(got, "74.234.12.5");
    }

    #[tokio::test]
    async fn node_ip_returns_error_when_no_ready_node() {
        let mock = MockLookup::new(); // default: NoReadyNode
        let err = mock.pick_node_ip().await.unwrap_err();
        assert!(matches!(err, LookupError::NoReadyNode));
    }
```

- [ ] **Step 2: Run the tests**

```bash
cd demo-landing && cargo test --lib kube::
```

Expected: `4 passed`.

- [ ] **Step 3: Commit**

```bash
git add demo-landing/src/kube.rs
git commit -m "kube: add pick_node_ip tests"
```

---

## Task 4: `template.rs` — minijinja env + `nodeport` helper

The helper is sync (minijinja requires this). It looks up a pre-resolved `host:port` from a `HashMap` built ahead of time by the route handler via `async` calls. This keeps the rendering step synchronous and the kube calls async-native.

**Files:**
- Create: `demo-landing/src/template.rs`
- Modify: `demo-landing/src/main.rs` (add `mod template;`)

- [ ] **Step 1: Create `demo-landing/src/template.rs`**

```rust
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
```

Note: the regex scans for minijinja's *space-separated* form `{{ nodeport "x" }}`; but `env.render_str` uses minijinja's *function-call* form `{{ nodeport("x") }}`. That's intentional — regex happens on markdown before minijinja parses, and we convert the space form to the call form by pre-processing. This is done in the `render` module (Task 5), not here. Tests here exercise each stage directly.

- [ ] **Step 2: Register the module in `main.rs`**

Edit `demo-landing/src/main.rs`. Add `mod template;` below `mod kube;`:

```rust
mod kube;
mod template;
```

- [ ] **Step 3: Run the tests**

```bash
cd demo-landing && cargo test --lib template::
```

Expected: `4 passed`.

- [ ] **Step 4: Commit**

```bash
git add demo-landing/src/template.rs demo-landing/src/main.rs
git commit -m "template: minijinja env + nodeport regex extraction"
```

---

## Task 5: `render.rs` — markdown → HTML → layout

**Files:**
- Create: `demo-landing/assets/layout.html`
- Create: `demo-landing/assets/styles.css`
- Create: `demo-landing/src/render.rs`
- Modify: `demo-landing/src/main.rs` (add `mod render;`)

- [ ] **Step 1: Create `demo-landing/assets/layout.html`**

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{{ title | default("Demo") }}</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <main>
      {{ content | safe }}
    </main>
  </body>
</html>
```

- [ ] **Step 2: Create `demo-landing/assets/styles.css`**

```css
:root {
  --fg: #1d2330;
  --fg-muted: #4b5563;
  --bg: #ffffff;
  --accent: #2563eb;
  --code-bg: #f3f4f6;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  line-height: 1.6;
}

main {
  max-width: 760px;
  margin: 0 auto;
  padding: 2.5rem 1.25rem 4rem;
}

h1, h2, h3 { line-height: 1.25; margin-top: 1.75em; }
h1 { font-size: 2rem; margin-top: 0; }
h2 { font-size: 1.4rem; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.3rem; }

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

code {
  background: var(--code-bg);
  padding: 0.1em 0.35em;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.92em;
}

pre {
  background: var(--code-bg);
  padding: 0.9rem 1rem;
  border-radius: 6px;
  overflow-x: auto;
}

pre code { background: transparent; padding: 0; }

img { max-width: 100%; height: auto; }

ul { padding-left: 1.2em; }

table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #e5e7eb; padding: 0.4em 0.7em; text-align: left; }
th { background: #f9fafb; }

.muted { color: var(--fg-muted); }
```

- [ ] **Step 3: Create `demo-landing/src/render.rs`**

```rust
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
        let md = r#"See [svc](http://{{ nodeport "missing" }}/)"#;
        let html = render(md, Arc::new(HashMap::new()), "t").unwrap();
        assert!(html.contains("nodeport error"));
        // The link should still be emitted, just with the error comment as part of the URL.
        assert!(html.contains("<a href="));
    }
}
```

- [ ] **Step 4: Register `render` in `main.rs`**

```rust
mod kube;
mod render;
mod template;
```

- [ ] **Step 5: Run the tests**

```bash
cd demo-landing && cargo test --lib render::
```

Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add demo-landing/assets/ demo-landing/src/render.rs demo-landing/src/main.rs
git commit -m "render: markdown + layout pipeline"
```

---

## Task 6: `routes.rs` — axum handlers

**Files:**
- Create: `demo-landing/src/routes.rs`
- Modify: `demo-landing/src/main.rs` (add `mod routes;`)

- [ ] **Step 1: Create `demo-landing/src/routes.rs`**

```rust
//! axum handlers.

use crate::kube::ServiceLookup;
use crate::render;
use crate::template;
use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use futures::future::join_all;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

const STYLES_CSS: &str = include_str!("../assets/styles.css");

#[derive(Clone)]
pub struct AppState {
    pub content_dir: PathBuf,
    pub lookup: Arc<dyn ServiceLookup>,
}

pub async fn healthz() -> &'static str {
    "ok"
}

pub async fn styles() -> Response {
    ([(header::CONTENT_TYPE, "text/css; charset=utf-8")], STYLES_CSS).into_response()
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

    // Pre-resolve all nodeport calls in parallel before rendering.
    let names = template::extract_service_names(&markdown);
    let mut resolved: HashMap<String, String> = HashMap::new();
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
                            resolved.insert(name, format!("{}:{}", ip, info.node_port));
                        }
                        Err(err) => {
                            tracing::warn!(service = %name, error = %err, "nodeport lookup failed");
                            resolved.insert(
                                name.clone(),
                                format!("<!-- nodeport error: {} -->", err),
                            );
                        }
                    }
                }
            }
            Err(err) => {
                tracing::warn!(error = %err, "node IP lookup failed");
                for (name, _) in services {
                    resolved
                        .insert(name, format!("<!-- nodeport error: {} -->", err));
                }
            }
        }
    }

    match render::render(&markdown, Arc::new(resolved), "Demo") {
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

pub async fn image(
    State(state): State<AppState>,
    Path(path): Path<String>,
) -> Response {
    // Resolve the requested path against the images dir and verify the
    // canonicalized result stays underneath it.
    let base = state.content_dir.join("images");
    let requested = base.join(&path);

    let canonical_base = match tokio::fs::canonicalize(&base).await {
        Ok(p) => p,
        Err(_) => {
            return (StatusCode::NOT_FOUND, "").into_response();
        }
    };
    let canonical_requested = match tokio::fs::canonicalize(&requested).await {
        Ok(p) => p,
        Err(_) => {
            return (StatusCode::NOT_FOUND, "").into_response();
        }
    };

    if !canonical_requested.starts_with(&canonical_base) {
        tracing::warn!(path = %path, "path traversal attempt refused");
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kube::{ServiceInfo, ServiceLookup as _};

    // Reuse the kube module's test MockLookup by copy-pasting a minimal
    // equivalent here so this module's tests don't require cross-module
    // test helper visibility.
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

    #[tokio::test]
    async fn image_refuses_path_traversal() {
        let tmp = tempdir_with_images();
        let state = AppState {
            content_dir: tmp.path().into(),
            lookup: Arc::new(LocalMock::new()),
        };
        let resp = image(State(state), Path("../../etc/passwd".into())).await;
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn image_returns_404_for_missing() {
        let tmp = tempdir_with_images();
        let state = AppState {
            content_dir: tmp.path().into(),
            lookup: Arc::new(LocalMock::new()),
        };
        let resp = image(State(state), Path("does-not-exist.png".into())).await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn healthz_returns_ok() {
        assert_eq!(healthz().await, "ok");
    }

    // --- helpers ---

    /// Create a temp directory with an `images/` subdir inside. Using
    /// std::env::temp_dir() so we don't pull in a `tempfile` dep for one test.
    fn tempdir_with_images() -> TempDir {
        let base = std::env::temp_dir().join(format!(
            "demo-landing-test-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(base.join("images")).unwrap();
        // Write a known file used by the traversal test's canonicalization.
        std::fs::write(base.join("images").join("pixel.png"), b"\x89PNG\r\n").unwrap();
        TempDir { path: base }
    }

    struct TempDir { path: PathBuf }
    impl TempDir { fn path(&self) -> &std::path::Path { &self.path } }
    impl Drop for TempDir {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.path); }
    }
}
```

- [ ] **Step 2: Register `routes` in `main.rs`**

```rust
mod kube;
mod render;
mod routes;
mod template;
```

- [ ] **Step 3: Run the tests**

```bash
cd demo-landing && cargo test --lib routes::
```

Expected: `3 passed`.

- [ ] **Step 4: Commit**

```bash
git add demo-landing/src/routes.rs demo-landing/src/main.rs
git commit -m "routes: axum handlers for index/styles/images/healthz"
```

---

## Task 7: Wire it all together in `main.rs` + real `ServiceLookup`

**Files:**
- Modify: `demo-landing/src/main.rs` — full implementation.

- [ ] **Step 1: Replace `demo-landing/src/main.rs`**

Overwrite the existing file with:

```rust
mod kube;
mod render;
mod routes;
mod template;

use async_trait::async_trait;
use axum::{routing::get, Router};
use k8s_openapi::api::core::v1::{Node, Service};
use kube::{api::ListParams, Api, Client};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

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
            // Ready=True?
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

    let client = Client::try_default().await?;
    let lookup: Arc<dyn ServiceLookup> =
        Arc::new(KubeClientLookup { client });

    let state = AppState {
        content_dir,
        lookup,
    };

    let app = Router::new()
        .route("/", get(routes::index))
        .route("/styles.css", get(routes::styles))
        .route("/images/*path", get(routes::image))
        .route("/healthz", get(routes::healthz))
        .layer(tower_http::trace::TraceLayer::new_for_http())
        .with_state(state);

    tracing::info!(%listen_addr, "starting demo-landing");
    let listener = tokio::net::TcpListener::bind(listen_addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
```

- [ ] **Step 2: Build**

```bash
cd demo-landing && cargo build
```

Expected: warnings acceptable; build succeeds.

- [ ] **Step 3: Run the full test suite**

```bash
cd demo-landing && cargo test
```

Expected: all tests pass (count is 12 from tasks 2–6).

- [ ] **Step 4: Commit**

```bash
git add demo-landing/src/main.rs
git commit -m "main: wire real kube ServiceLookup + full router"
```

---

## Task 8: Dockerfile + justfile recipe

**Files:**
- Create: `demo-landing/Dockerfile`
- Create: `demo-landing/.dockerignore`
- Modify: `justfile`

- [ ] **Step 1: Create `demo-landing/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.6

FROM rust:1.81-bookworm AS builder
WORKDIR /src

# Cache dep compilation: copy manifests first, build a stub, then copy sources.
COPY Cargo.toml Cargo.lock* ./
RUN mkdir -p src && echo 'fn main() {}' > src/main.rs && \
    cargo build --release && rm -rf src

COPY src ./src
COPY assets ./assets
RUN touch src/main.rs && cargo build --release

FROM gcr.io/distroless/cc-debian12:nonroot
WORKDIR /
COPY --from=builder /src/target/release/demo-landing /usr/local/bin/demo-landing
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/demo-landing"]
```

- [ ] **Step 2: Create `demo-landing/.dockerignore`**

```
target
.git
docs
*.md
```

(The `*.md` exclusion keeps the local README out of the image context; the live markdown content comes in via git-sync at runtime.)

- [ ] **Step 3: Add `build-landing-image` recipe to `justfile`**

Open `justfile` in the repo root. Find the existing `build-airflow-image` block:

```make
build-airflow-image:
    docker build -t oci.stackable.tech/sandbox/airflow:3.1.6-stackable0.0.0-dev-cosmos docker/airflow
    docker push oci.stackable.tech/sandbox/airflow:3.1.6-stackable0.0.0-dev-cosmos
```

Add directly below it:

```make
build-landing-image:
    docker build -t oci.stackable.tech/sandbox/demo-landing:0.1.0-dev demo-landing
    docker push oci.stackable.tech/sandbox/demo-landing:0.1.0-dev
```

- [ ] **Step 4: Generate `Cargo.lock` (ensures the Dockerfile's cache-dance works)**

```bash
cd demo-landing && cargo generate-lockfile
```

- [ ] **Step 5: Build the image locally**

```bash
docker build -t demo-landing:test demo-landing
```

Expected: build succeeds. Final image tag is `demo-landing:test`; ~25 MB compressed.

- [ ] **Step 6: Smoke-test the image**

```bash
docker run --rm -d --name demo-landing-test -p 18080:8080 \
  -e LISTEN_ADDR=0.0.0.0:8080 demo-landing:test
sleep 1
curl -s http://127.0.0.1:18080/healthz
docker stop demo-landing-test
```

Expected: prints `ok`. (The index/image routes will fail without a kube client, which is fine — `healthz` is the smoke signal.)

- [ ] **Step 7: Commit**

```bash
git add demo-landing/Dockerfile demo-landing/.dockerignore demo-landing/Cargo.lock justfile
git commit -m "Add Dockerfile + build-landing-image just recipe"
```

---

## Task 9: Sample landing-page content

**Files:**
- Create: `website/index.md`
- Create: `website/images/.gitkeep`

- [ ] **Step 1: Create `website/index.md`**

```markdown
# Stackable Data Platform Demo

Welcome. This cluster is running the full Stackable Data Platform demo — a
GitOps-managed data lakehouse with OpenMetadata, Airflow, Trino, dbt, and
a handful of supporting components.

## Access

| Service | URL |
|---|---|
| **ArgoCD** | <https://{{ nodeport "argocd-server-nodeport" }}/applications> |
| **Forgejo** | <http://{{ nodeport "forgejo-http-nodeport" }}/> |
| **OpenMetadata** | <http://{{ nodeport "openmetadata-nodeport" }}/> |

ArgoCD uses a self-signed cert; accept the browser warning on first visit.

## Default credentials

| Service | Username | Password |
|---|---|---|
| ArgoCD | `admin` | `adminadmin` |
| Forgejo | `stackable` | `stackable` |
| OpenMetadata | `admin@open-metadata.org` | `admin` |

## What to look at

1. In **OpenMetadata**, open the Trino service and explore the
   `hive-iceberg.demo.*` tables — they're dbt-built marts with per-column
   descriptions and dbt test lineage.
2. In **ArgoCD**, watch the continuously-reconciled application tree.
3. In **Forgejo**, browse the in-cluster git mirror of this repository.
```

- [ ] **Step 2: Create `website/images/.gitkeep`**

```
```

(Empty file; placeholder so the directory is tracked.)

- [ ] **Step 3: Commit**

```bash
git add website/
git commit -m "Add demo landing page content"
```

---

## Task 10: Kubernetes manifests + ArgoCD Application

**Files:**
- Create: `platform/manifests/demo-landing/serviceaccount.yaml`
- Create: `platform/manifests/demo-landing/clusterrole.yaml`
- Create: `platform/manifests/demo-landing/clusterrolebinding.yaml`
- Create: `platform/manifests/demo-landing/deployment.yaml`
- Create: `platform/manifests/demo-landing/service.yaml`
- Create: `platform/applications/demo-landing.yaml`

- [ ] **Step 1: Create `platform/manifests/demo-landing/serviceaccount.yaml`**

```yaml
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: demo-landing
  namespace: deployment
```

- [ ] **Step 2: Create `platform/manifests/demo-landing/clusterrole.yaml`**

```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: demo-landing
rules:
  - apiGroups: [""]
    resources: ["services"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list"]
```

- [ ] **Step 3: Create `platform/manifests/demo-landing/clusterrolebinding.yaml`**

```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: demo-landing
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: demo-landing
subjects:
  - kind: ServiceAccount
    name: demo-landing
    namespace: deployment
```

- [ ] **Step 4: Create `platform/manifests/demo-landing/deployment.yaml`**

```yaml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-landing
  namespace: deployment
  labels:
    app.kubernetes.io/name: demo-landing
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: demo-landing
  template:
    metadata:
      labels:
        app.kubernetes.io/name: demo-landing
    spec:
      serviceAccountName: demo-landing
      volumes:
        - name: git
          emptyDir: {}
      containers:
        - name: landing
          image: oci.stackable.tech/sandbox/demo-landing:0.1.0-dev
          imagePullPolicy: IfNotPresent
          env:
            - name: CONTENT_DIR
              value: /git/current/website
            - name: LISTEN_ADDR
              value: 0.0.0.0:8080
            - name: LOG_LEVEL
              value: info
          ports:
            - name: http
              containerPort: 8080
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              memory: 128Mi
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8080
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8080
            periodSeconds: 10
          volumeMounts:
            - name: git
              mountPath: /git
        - name: git-sync
          image: registry.k8s.io/git-sync/git-sync:v4.3.0
          args:
            - --repo=http://forgejo-http.deployment.svc.cluster.local:3000/stackable/openmetadata-dbt-demo.git
            - --ref=main
            - --depth=1
            - --period=30s
            - --root=/git
            - --link=current
          volumeMounts:
            - name: git
              mountPath: /git
```

- [ ] **Step 5: Create `platform/manifests/demo-landing/service.yaml`**

```yaml
---
apiVersion: v1
kind: Service
metadata:
  name: demo-landing
  namespace: deployment
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: demo-landing
  ports:
    - name: http
      port: 80
      targetPort: 8080
      nodePort: 30088
      protocol: TCP
```

- [ ] **Step 6: Create `platform/applications/demo-landing.yaml`**

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: demo-landing
  namespace: deployment
spec:
  project: dbt-openmetadata-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: deployment
  source:
    repoURL: "http://forgejo-http.deployment.svc.cluster.local:3000/stackable/openmetadata-dbt-demo.git"
    targetRevision: "main"
    path: platform/manifests/demo-landing/
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    automated:
      selfHeal: true
      prune: true
```

- [ ] **Step 7: Validate all six YAML files parse**

```bash
for f in \
  platform/manifests/demo-landing/serviceaccount.yaml \
  platform/manifests/demo-landing/clusterrole.yaml \
  platform/manifests/demo-landing/clusterrolebinding.yaml \
  platform/manifests/demo-landing/deployment.yaml \
  platform/manifests/demo-landing/service.yaml \
  platform/applications/demo-landing.yaml; do
    python -c "import yaml; list(yaml.safe_load_all(open('$f')))" && echo "OK: $f"
done
```

Expected: all six print `OK: ...`.

- [ ] **Step 8: Commit**

```bash
git add platform/manifests/demo-landing/ platform/applications/demo-landing.yaml
git commit -m "Deploy demo-landing: RBAC, Deployment, Service, Application"
```

---

## Task 11: Push image, deploy, verify

**Files:** none (release + verification)

- [ ] **Step 1: Push the image to the Stackable sandbox registry**

```bash
just build-landing-image
```

Expected: `docker push` succeeds with tag `oci.stackable.tech/sandbox/demo-landing:0.1.0-dev`.

- [ ] **Step 2: Push the branch (or all commits if working on main)**

Pushing `main` is the right choice here because the deploy branch is what ArgoCD-via-Forgejo reconciles. Confirm you're on main:

```bash
git branch --show-current
```

Expected: `main`. Then:

```bash
git push origin main
```

- [ ] **Step 3: Watch ArgoCD sync the new Application**

Wait ~1 min for Forgejo to mirror, then:

```bash
kubectl -n deployment get application demo-landing -o jsonpath='{.status.sync.status}:{.status.health.status}'
echo
```

Expected eventual output: `Synced:Healthy`.

- [ ] **Step 4: Verify pod is running with both containers**

```bash
kubectl -n deployment get pods -l app.kubernetes.io/name=demo-landing
```

Expected: one pod `Running` with `2/2` ready.

- [ ] **Step 5: Fetch the landing page**

Get a node external IP:

```bash
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
echo "Landing: http://$NODE_IP:30088/"
curl -s http://$NODE_IP:30088/ | head -40
```

Expected: HTML with a `<h1>Stackable Data Platform Demo</h1>` heading and URLs that contain `$NODE_IP:30080`, `$NODE_IP:30000`, `$NODE_IP:30585` (not the placeholders).

- [ ] **Step 6: Verify error path (optional)**

Temporarily edit `website/index.md` on a scratch branch to reference a service that doesn't exist (e.g. `{{ nodeport "nonexistent" }}`). Push, wait for git-sync (~30 s), refresh. The rest of the page should render; the bad link should contain the error comment. Revert when done.

- [ ] **Step 7: Done**

No commit. The running demo-landing is the deliverable.

---

## Self-Review

**Spec coverage:**
- Architecture (axum + git-sync sidecar + kube client): Task 7 (main wiring), Task 10 (deployment).
- Per-request render model with no caching: Task 6 (`routes::index`).
- Routes `/`, `/styles.css`, `/images/*`, `/healthz`: Task 6.
- Module layout (`main`, `kube`, `template`, `render`, `routes`): Tasks 1–7.
- `ServiceLookup` trait with mockable tests: Tasks 2–4.
- `nodeport` helper semantics (lookup, multi-port first-port, not-NodePort, ambiguous, error comments): Tasks 2–4 (types + tests) and Task 7 (real implementation).
- Node IP selection (Ready filter, ExternalIP before InternalIP, first candidate): Task 7 (`pick_node_ip` impl).
- HTML shell + CSS embedded: Task 5 (assets created, wrapped by `render::render`).
- Content delivery via git-sync: Task 10 (deployment.yaml sidecar).
- RBAC (ServiceAccount + ClusterRole + binding, list/get on services+nodes): Task 10.
- Service type NodePort, port 30088: Task 10.
- Justfile build recipe: Task 8.
- Error handling: rendered as inline HTML comments; `/healthz` decoupled from kube: Task 6 handler; Task 7 real lookup.
- Path-traversal defense: Task 6 `image` handler + dedicated test.
- Testing: unit per module (Tasks 2–6); integration via local `cargo run` + in-cluster smoke (Task 11).

No spec requirement without a task.

**Placeholder scan:** no `TBD`/`TODO`/"similar to"/"add error handling" tokens in any task. All code blocks are complete. All commands are literal.

**Type consistency:**
- `ServiceInfo { namespace: String, node_port: u16 }` — same fields in Task 2 trait declaration, Task 6 routes usage (`info.node_port`), Task 7 real impl.
- `LookupError` variants consistent across Tasks 2, 6, 7.
- `ServiceLookup` async-trait methods: `lookup_service(&self, &str) -> Result<ServiceInfo, LookupError>` and `pick_node_ip(&self) -> Result<String, LookupError>` — identical across all tasks.
- `AppState { content_dir: PathBuf, lookup: Arc<dyn ServiceLookup> }` — same in Task 6 declaration and Task 7 construction.
- `render::render(markdown_src, resolved, title)` signature consistent between Task 5 declaration and Task 6 call site.
- Env vars and container names (`CONTENT_DIR=/git/current/website`, image `oci.stackable.tech/sandbox/demo-landing:0.1.0-dev`) match between Task 7 (main.rs defaults), Task 8 (justfile), and Task 10 (deployment).
- NodePort `30088` consistent between Task 10 (service.yaml) and Task 11 (verification URL).
- git-sync path: service.yaml runs `git-sync` image and `--root=/git --link=current`, deployment mounts both containers at `/git`, CONTENT_DIR `/git/current/website` — consistent.
