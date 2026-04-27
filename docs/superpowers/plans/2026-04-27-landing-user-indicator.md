# Landing Page User Indicator and Logout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the logged-in Keycloak username in the demo-landing header with a logout link that ends the SSO session, not just the local oauth2-proxy cookie.

**Architecture:** Three pieces — (1) demo-landing reads `X-Forwarded-Preferred-Username` per request, builds the full logout URL at startup from the issuer URL + landing base URL, and renders both into the layout. (2) `discover-node-ip` Job writes `keycloak-host` into the `oidc-endpoints` ConfigMap. (3) The demo-landing Deployment exposes the issuer URL, landing base URL, and Keycloak host to the right containers. Keycloak's demo-landing client is updated to allow the post-logout redirect URI.

**Tech Stack:** Rust (axum, minijinja, urlencoding), Kubernetes manifests, OpenTofu/Keycloak provider.

**Spec deviations (with rationale):**

1. The design described a per-request middleware writing user info into a request extension. The plan uses a plain helper function called from the single consumer (`index` handler) — one consumer doesn't justify the indirection.
2. The design proposed pre-building the logout URL inside the `discover-node-ip` Job and storing it as a `landing-logout-rd` ConfigMap key. The plan moves that construction into the Rust app instead. Reason: the URL contains nested query parameters (`?post_logout_redirect_uri=...&client_id=...`) which must be URL-encoded as a single value to survive being placed inside oauth2-proxy's outer `?rd=...` parameter. Doing nested URL encoding in a shell script (the `bitnamilegacy/kubectl` image has no python/perl/curl) is fragile; in Rust it's a one-liner via `urlencoding::encode`, easily unit-tested.

---

## File structure

| File | Change |
| --- | --- |
| `demo-landing/Cargo.toml` | Add `urlencoding = "2.1"` dependency. |
| `demo-landing/src/auth.rs` | Add `extract_current_user(&HeaderMap) -> String` and `build_logout_url(&str, &str) -> String` helpers. |
| `demo-landing/src/render.rs` | Extend `render()` signature with `current_user` and `logout_url`; pass them into the layout context. |
| `demo-landing/assets/layout.html` | Add a right-side user block in `site-header` that conditionally shows username and logout link. |
| `demo-landing/assets/styles.css` | Switch `site-header` to flex/space-between; add `.user-block` rules. |
| `demo-landing/src/routes.rs` | `index` handler accepts `HeaderMap`, calls `extract_current_user`, passes username + state's `logout_url` to `render`. `AppState` gains a `logout_url: String` field. |
| `demo-landing/src/main.rs` | Read `OIDC_ISSUER_URL` and `LANDING_BASE_URL` env vars, call `build_logout_url`, store result on `AppState`. |
| `infrastructure/keycloak-manifests/discover-node-ip.yaml` | Add `keycloak-host` (and upper-snake `KEYCLOAK_HOST`) keys to the `kubectl create configmap` invocation. |
| `platform/manifests/demo-landing/deployment.yaml` | New env vars: `OAUTH2_PROXY_WHITELIST_DOMAINS` on oauth2-proxy; `OIDC_ISSUER_URL` and `LANDING_BASE_URL` on the landing container. Image tag bumped. |
| `infrastructure/keycloak-manifests/configure-keycloak.yaml` | Add `valid_post_logout_redirect_uris = ["*"]` to the `demo-landing` Keycloak client. |
| `justfile` | Bump `build-landing-image` target tag. |

---

## Task 1: Header extractor for the current user

**Files:**
- Modify: `demo-landing/src/auth.rs`

- [ ] **Step 1: Write the failing tests**

Append to the `#[cfg(test)] mod tests` block in `demo-landing/src/auth.rs`:

```rust
    use axum::http::HeaderMap;

    #[test]
    fn extract_current_user_returns_empty_when_header_missing() {
        let headers = HeaderMap::new();
        assert_eq!(super::extract_current_user(&headers), "");
    }

    #[test]
    fn extract_current_user_returns_header_value() {
        let mut headers = HeaderMap::new();
        headers.insert("x-forwarded-preferred-username", "demo-admin".parse().unwrap());
        assert_eq!(super::extract_current_user(&headers), "demo-admin");
    }

    #[test]
    fn extract_current_user_trims_surrounding_whitespace() {
        let mut headers = HeaderMap::new();
        headers.insert("x-forwarded-preferred-username", "  demo-user  ".parse().unwrap());
        assert_eq!(super::extract_current_user(&headers), "demo-user");
    }

    #[test]
    fn extract_current_user_returns_empty_when_header_value_is_non_visible_ascii() {
        // HeaderValue::to_str returns Err for non-visible-ASCII bytes; we fall
        // through to empty rather than panicking.
        let mut headers = HeaderMap::new();
        let v = axum::http::HeaderValue::from_bytes(b"\xff\xfe").unwrap();
        headers.insert("x-forwarded-preferred-username", v);
        assert_eq!(super::extract_current_user(&headers), "");
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd demo-landing && cargo test --quiet auth::tests::extract_current_user`
Expected: compile error referring to `extract_current_user` not found.

- [ ] **Step 3: Implement the helper**

In `demo-landing/src/auth.rs`, add this above the existing `require_admin` function:

```rust
const FORWARDED_PREFERRED_USERNAME: &str = "x-forwarded-preferred-username";

/// Read the username oauth2-proxy forwards via `X-Forwarded-Preferred-Username`.
/// Returns an empty string if the header is missing or not visible-ASCII.
pub fn extract_current_user(headers: &axum::http::HeaderMap) -> String {
    headers
        .get(FORWARDED_PREFERRED_USERNAME)
        .and_then(|h| h.to_str().ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}
```

If `axum::http::HeaderMap` is not yet imported at the top of the file, add it to the existing `use axum::{...}` block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd demo-landing && cargo test --quiet auth::`
Expected: all `auth::tests::*` tests pass (existing 5 + new 4).

- [ ] **Step 5: Commit**

```bash
git add demo-landing/src/auth.rs
git commit -m "demo-landing: add extract_current_user helper for the user indicator"
```

---

## Task 2: Add the `urlencoding` dependency and `build_logout_url` helper

**Files:**
- Modify: `demo-landing/Cargo.toml`
- Modify: `demo-landing/src/auth.rs`

- [ ] **Step 1: Add the dependency**

In `demo-landing/Cargo.toml`, add to `[dependencies]` (alphabetical order, between `tracing-subscriber` and the closing of the section):

```toml
urlencoding = "2.1"
```

So the dependencies block ends with:

```toml
tracing-subscriber = { version = "0.3", features = ["env-filter", "fmt"] }
urlencoding = "2.1"
```

- [ ] **Step 2: Write the failing tests**

Append to the `#[cfg(test)] mod tests` block in `demo-landing/src/auth.rs`:

```rust
    #[test]
    fn build_logout_url_encodes_post_logout_redirect_inside_rd() {
        let got = super::build_logout_url(
            "http://10.0.0.1:30900/realms/stackable-demo",
            "http://10.0.0.1:30088",
        );
        // The whole keycloak URL must be URL-encoded so the inner `?` and `&`
        // are not parsed by oauth2-proxy as new top-level query params.
        let expected = "/oauth2/sign_out?rd=http%3A%2F%2F10.0.0.1%3A30900%2Frealms%2Fstackable-demo%2Fprotocol%2Fopenid-connect%2Flogout%3Fpost_logout_redirect_uri%3Dhttp%253A%252F%252F10.0.0.1%253A30088%26client_id%3Ddemo-landing";
        assert_eq!(got, expected);
    }

    #[test]
    fn build_logout_url_returns_empty_when_inputs_empty() {
        assert_eq!(super::build_logout_url("", ""), "");
        assert_eq!(super::build_logout_url("http://x", ""), "");
        assert_eq!(super::build_logout_url("", "http://x"), "");
    }

    #[test]
    fn build_logout_url_strips_trailing_slash_on_issuer() {
        let got = super::build_logout_url("http://kc/realms/r/", "http://land/");
        // Issuer has its trailing slash stripped before appending the logout
        // path, so we don't end up with `//protocol/...`.
        assert!(got.contains("realms%2Fr%2Fprotocol%2Fopenid-connect%2Flogout"));
    }
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd demo-landing && cargo test --quiet auth::tests::build_logout_url`
Expected: compile error referring to `build_logout_url` not found.

- [ ] **Step 4: Implement the helper**

In `demo-landing/src/auth.rs`, add below `extract_current_user`:

```rust
/// Build the demo-landing `<a href>` for the logout link.
///
/// The result is `/oauth2/sign_out?rd=<URL-encoded keycloak end-session URL>`.
/// Encoding is required so the inner query (`?post_logout_redirect_uri=...&client_id=demo-landing`)
/// survives being a *value* of oauth2-proxy's outer `?rd=` parameter.
///
/// Returns an empty string if either input is empty (callers treat that as
/// "don't render the logout link").
pub fn build_logout_url(issuer_url: &str, landing_base_url: &str) -> String {
    if issuer_url.is_empty() || landing_base_url.is_empty() {
        return String::new();
    }
    let issuer = issuer_url.trim_end_matches('/');
    let landing = landing_base_url.trim_end_matches('/');
    let encoded_landing = urlencoding::encode(landing);
    let inner = format!(
        "{}/protocol/openid-connect/logout?post_logout_redirect_uri={}&client_id=demo-landing",
        issuer, encoded_landing
    );
    format!("/oauth2/sign_out?rd={}", urlencoding::encode(&inner))
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd demo-landing && cargo test --quiet auth::`
Expected: the three new `build_logout_url_*` tests plus the previous nine pass.

- [ ] **Step 6: Commit**

```bash
git add demo-landing/Cargo.toml demo-landing/Cargo.lock demo-landing/src/auth.rs
git commit -m "demo-landing: build_logout_url helper with urlencoding"
```

---

## Task 3: Render pipeline accepts user + logout context

**Files:**
- Modify: `demo-landing/src/render.rs`
- Modify: `demo-landing/assets/layout.html`
- Modify: `demo-landing/assets/styles.css`
- Modify: `demo-landing/src/routes.rs` (just to update the single existing `render` call site so the build stays green)

- [ ] **Step 1: Write the failing render tests**

Add to the `#[cfg(test)] mod tests` block in `demo-landing/src/render.rs`:

```rust
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
```

Adjust the existing four tests in the same module that call `render(...)` to pass two extra empty-string arguments at the end (`""`, `""`):

- `renders_plain_markdown`
- `substitutes_nodeport_before_markdown_parsing`
- `missing_service_renders_error_comment_and_keeps_page`
- `substitutes_toggle_helper_to_form`

For example, change:

```rust
let html = render(md, np, tg, "t").unwrap();
```

to:

```rust
let html = render(md, np, tg, "t", "", "").unwrap();
```

- [ ] **Step 2: Run tests to verify they fail to compile**

Run: `cd demo-landing && cargo test --quiet render::`
Expected: compile error — `render` takes 4 arguments, 6 supplied.

- [ ] **Step 3: Update the `render` signature**

In `demo-landing/src/render.rs`, replace the existing `pub fn render(...)` with:

```rust
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
    layout_env
        .add_template("layout", LAYOUT_SRC)
        .map_err(|e| RenderError::Layout(e.to_string()))?;
    let tmpl = layout_env
        .get_template("layout")
        .map_err(|e| RenderError::Layout(e.to_string()))?;
    tmpl.render(context! {
        title => title,
        content => body_html,
        current_user => current_user,
        logout_url => logout_url,
    })
    .map_err(|e| RenderError::Layout(e.to_string()))
}
```

- [ ] **Step 4: Update the layout template**

In `demo-landing/assets/layout.html`, just before the closing `</header>` tag (currently after the SVG-bearing `<a class="logo" ...></a>` block), insert:

```html
      {% if current_user %}
      <div class="user-block">
        <span class="user-name">{{ current_user }}</span>
        {% if logout_url %}
        <span class="user-sep">·</span>
        <a class="logout" href="{{ logout_url }}">log out</a>
        {% endif %}
      </div>
      {% endif %}
```

(The `<a>` and the new `<div>` become flex siblings inside `.site-header`.)

- [ ] **Step 5: Update the stylesheet**

In `demo-landing/assets/styles.css`, replace the entire `.site-header` rule with:

```css
.site-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.85rem 1.5rem;
  background: var(--color-base-200);
  border-bottom: 1px solid var(--color-base-300);
}
```

Then append a new section directly after the `.site-header .logo:hover { ... }` block (before the `/* Main column */` comment):

```css
.site-header .user-block {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  color: var(--color-neutral);
}

.site-header .user-block .user-name {
  color: var(--color-base-content);
}

.site-header .user-block .user-sep {
  color: var(--color-base-300);
}

.site-header .user-block .logout {
  color: var(--color-primary);
}
```

- [ ] **Step 6: Update the existing `routes.rs::index` call site**

In `demo-landing/src/routes.rs`, find the `render::render(&markdown, Arc::new(nodeports), Arc::new(toggles), "Demo")` call inside `index`. Change it to:

```rust
    match render::render(
        &markdown,
        Arc::new(nodeports),
        Arc::new(toggles),
        "Demo",
        "",
        "",
    ) {
```

(Empty strings until Task 5 wires the real values.)

- [ ] **Step 7: Run all tests**

Run: `cd demo-landing && cargo test --quiet`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add demo-landing/src/render.rs demo-landing/src/routes.rs demo-landing/assets/layout.html demo-landing/assets/styles.css
git commit -m "demo-landing: render user block in header (no wiring yet)"
```

---

## Task 4: Build the logout URL at startup and store it on AppState

**Files:**
- Modify: `demo-landing/src/routes.rs` (`AppState` struct)
- Modify: `demo-landing/src/main.rs`

- [ ] **Step 1: Extend `AppState`**

In `demo-landing/src/routes.rs`, add a new field to `AppState`:

```rust
#[derive(Clone)]
pub struct AppState {
    pub content_dir: PathBuf,
    pub lookup: Arc<dyn ServiceLookup>,
    pub forgejo: Arc<ForgejoClient>,
    pub logout_url: String,
}
```

Update the test helper `test_state(...)` in the same file's `#[cfg(test)] mod tests`:

```rust
    fn test_state(tmp: &std::path::Path) -> AppState {
        AppState {
            content_dir: tmp.into(),
            lookup: Arc::new(LocalMock::new()),
            forgejo: Arc::new(ForgejoClient::for_testing()),
            logout_url: String::new(),
        }
    }
```

- [ ] **Step 2: Build the URL in `main.rs`**

In `demo-landing/src/main.rs`, after the existing `let listen_addr = ...` line (before `let client = Client::try_default()...`), add:

```rust
    let issuer_url = std::env::var("OIDC_ISSUER_URL").unwrap_or_default();
    let landing_base_url = std::env::var("LANDING_BASE_URL").unwrap_or_default();
    let logout_url = auth::build_logout_url(&issuer_url, &landing_base_url);
    if logout_url.is_empty() {
        tracing::warn!(
            issuer_set = !issuer_url.is_empty(),
            landing_set = !landing_base_url.is_empty(),
            "logout URL not built; logout link will not be rendered"
        );
    }
```

Then update the `AppState` literal further down:

```rust
    let state = AppState {
        content_dir,
        lookup,
        forgejo,
        logout_url,
    };
```

- [ ] **Step 3: Verify the build**

Run: `cd demo-landing && cargo build --quiet`
Expected: clean build.

Run: `cd demo-landing && cargo test --quiet`
Expected: all tests still pass.

- [ ] **Step 4: Commit**

```bash
git add demo-landing/src/main.rs demo-landing/src/routes.rs
git commit -m "demo-landing: build logout URL at startup, store on AppState"
```

---

## Task 5: Wire the index handler to populate username + logout URL

**Files:**
- Modify: `demo-landing/src/routes.rs`

- [ ] **Step 1: Write the failing handler tests**

Add to the `#[cfg(test)] mod tests` block in `demo-landing/src/routes.rs`:

```rust
    #[tokio::test]
    async fn index_renders_user_block_from_header_and_logout_url() {
        let tmp = tempdir_with_index_md("# hello\n\nbody.");
        let mut state = test_state(tmp.path());
        state.logout_url = "/oauth2/sign_out?rd=http%3A%2F%2Fkc%2Flogout".to_string();

        let mut headers = HeaderMap::new();
        headers.insert(
            "x-forwarded-preferred-username",
            "demo-admin".parse().unwrap(),
        );

        let resp = index(State(state), headers).await;
        assert_eq!(resp.status(), StatusCode::OK);
        let body = body_to_string(resp).await;
        assert!(body.contains("demo-admin"));
        assert!(body.contains(r#"href="/oauth2/sign_out?rd=http%3A%2F%2Fkc%2Flogout""#));
    }

    #[tokio::test]
    async fn index_omits_user_block_when_header_missing() {
        let tmp = tempdir_with_index_md("# hello");
        let state = test_state(tmp.path());
        let resp = index(State(state), HeaderMap::new()).await;
        let body = body_to_string(resp).await;
        assert!(!body.contains("user-block"));
    }
```

Add these two helpers to the same `mod tests` block (before the closing `}` of `mod tests`):

```rust
    fn tempdir_with_index_md(contents: &str) -> TempDir {
        let base = std::env::temp_dir().join(format!(
            "demo-landing-index-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&base).unwrap();
        std::fs::write(base.join("index.md"), contents).unwrap();
        TempDir { path: base }
    }

    async fn body_to_string(resp: Response) -> String {
        let bytes = axum::body::to_bytes(resp.into_body(), 1024 * 1024)
            .await
            .unwrap();
        String::from_utf8(bytes.to_vec()).unwrap()
    }
```

- [ ] **Step 2: Run tests to verify they fail to compile**

Run: `cd demo-landing && cargo test --quiet routes::tests::index_`
Expected: compile error — `index` takes one argument (`State`), tests pass two.

- [ ] **Step 3: Update the `index` handler**

In `demo-landing/src/routes.rs`, change the `index` signature from:

```rust
pub async fn index(State(state): State<AppState>) -> Response {
```

to:

```rust
pub async fn index(State(state): State<AppState>, headers: HeaderMap) -> Response {
```

At the very top of the function body (before the existing `let path = ...`), add:

```rust
    let current_user = crate::auth::extract_current_user(&headers);
    let logout_url = if current_user.is_empty() {
        String::new()
    } else {
        state.logout_url.clone()
    };
```

Then update the `render::render(...)` call (the same one touched in Task 3 step 6) to pass these:

```rust
    match render::render(
        &markdown,
        Arc::new(nodeports),
        Arc::new(toggles),
        "Demo",
        &current_user,
        &logout_url,
    ) {
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd demo-landing && cargo test --quiet`
Expected: all tests pass, including the two new `index_*` tests.

- [ ] **Step 5: Commit**

```bash
git add demo-landing/src/routes.rs
git commit -m "demo-landing: wire current_user + logout URL into index handler"
```

---

## Task 6: discover-node-ip Job writes `keycloak-host`

**Files:**
- Modify: `infrastructure/keycloak-manifests/discover-node-ip.yaml`

- [ ] **Step 1: Add the new key**

In `infrastructure/keycloak-manifests/discover-node-ip.yaml`, locate the `kubectl create configmap oidc-endpoints \` block (around line 108). Add the following line into the `--from-literal` list, immediately after the existing `--from-literal=landing-redirect-uri="http://${NODE_IP}:30088/oauth2/callback" \` line:

```sh
                  --from-literal=keycloak-host="${NODE_IP}:30900" \
```

And add the upper-snake alias into the existing alias block (after `--from-literal=LANDING_REDIRECT_URI=...`):

```sh
                  --from-literal=KEYCLOAK_HOST="${NODE_IP}:30900" \
```

- [ ] **Step 2: Lint the YAML**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('infrastructure/keycloak-manifests/discover-node-ip.yaml')))"`
Expected: no output (valid YAML).

- [ ] **Step 3: Commit**

```bash
git add infrastructure/keycloak-manifests/discover-node-ip.yaml
git commit -m "keycloak: add keycloak-host to oidc-endpoints ConfigMap"
```

---

## Task 7: demo-landing Deployment exposes the new env vars

**Files:**
- Modify: `platform/manifests/demo-landing/deployment.yaml`

- [ ] **Step 1: Add `OIDC_ISSUER_URL` and `LANDING_BASE_URL` to the landing container**

In `platform/manifests/demo-landing/deployment.yaml`, inside the `landing` container's `env:` list (around lines 27–51), append two new entries after the `FORGEJO_PASSWORD` block:

```yaml
            - name: OIDC_ISSUER_URL
              valueFrom:
                configMapKeyRef:
                  name: oidc-endpoints
                  key: issuer-url
            - name: LANDING_BASE_URL
              valueFrom:
                configMapKeyRef:
                  name: oidc-endpoints
                  key: landing-base-url
```

- [ ] **Step 2: Add `OAUTH2_PROXY_WHITELIST_DOMAINS` to the oauth2-proxy sidecar**

In the same file, inside the `oauth2-proxy` container's `env:` list (around lines 95–110), add a new entry alongside the existing `OAUTH2_PROXY_*` ones:

```yaml
            - name: OAUTH2_PROXY_WHITELIST_DOMAINS
              valueFrom:
                configMapKeyRef:
                  name: oidc-endpoints
                  key: keycloak-host
```

- [ ] **Step 3: Verify the YAML parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/demo-landing/deployment.yaml')))"`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add platform/manifests/demo-landing/deployment.yaml
git commit -m "demo-landing: wire OIDC_ISSUER_URL, LANDING_BASE_URL, and oauth2-proxy whitelist"
```

---

## Task 8: Allow post-logout redirect on the Keycloak demo-landing client

**Files:**
- Modify: `infrastructure/keycloak-manifests/configure-keycloak.yaml`

- [ ] **Step 1: Add the post-logout redirect URI list**

In `infrastructure/keycloak-manifests/configure-keycloak.yaml`, locate the `keycloak_openid_client.demo_landing` resource block (around lines 154-164). Inside that block, after the existing `valid_redirect_uris` line, add:

```hcl
      valid_post_logout_redirect_uris = ["*"]
```

So the full block reads:

```hcl
    resource "keycloak_openid_client" "demo_landing" {
      realm_id              = keycloak_realm.stackable_demo.id
      client_id             = "demo-landing"
      name                  = "Demo Landing Page"
      enabled               = true
      access_type           = "CONFIDENTIAL"
      client_secret         = "demo-landing-secret"
      standard_flow_enabled = true
      valid_redirect_uris   = ["*"]
      valid_post_logout_redirect_uris = ["*"]
      web_origins           = ["+"]
    }
```

(`*` matches the existing `valid_redirect_uris` policy on this demo client. Production clients should narrow it down; demo doesn't need to.)

- [ ] **Step 2: Verify the outer YAML is well-formed**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('infrastructure/keycloak-manifests/configure-keycloak.yaml')))"`
Expected: no output. (We're not parsing the embedded HCL — Kubernetes only cares that the outer YAML/ConfigMap is valid.)

- [ ] **Step 3: Commit**

```bash
git add infrastructure/keycloak-manifests/configure-keycloak.yaml
git commit -m "keycloak: allow post-logout redirect URIs on demo-landing client"
```

---

## Task 9: Bump and push the demo-landing image tag

**Files:**
- Modify: `justfile`
- Modify: `platform/manifests/demo-landing/deployment.yaml`

This task assumes you have `docker` available and credentials for `oci.stackable.tech`. If you don't, skip the `docker push` and ask the user to handle it.

- [ ] **Step 1: Bump the tag in the justfile**

In `justfile`, change the `build-landing-image` target from:

```just
build-landing-image:
    docker build -t oci.stackable.tech/sandbox/demo-landing:0.2.0-dev demo-landing
    docker push oci.stackable.tech/sandbox/demo-landing:0.2.0-dev
```

to:

```just
build-landing-image:
    docker build -t oci.stackable.tech/sandbox/demo-landing:0.2.1-dev demo-landing
    docker push oci.stackable.tech/sandbox/demo-landing:0.2.1-dev
```

- [ ] **Step 2: Bump the tag in the Deployment**

In `platform/manifests/demo-landing/deployment.yaml`, change the `landing` container's image line from:

```yaml
          image: oci.stackable.tech/sandbox/demo-landing:0.2.0-dev
```

to:

```yaml
          image: oci.stackable.tech/sandbox/demo-landing:0.2.1-dev
```

- [ ] **Step 3: Build and push the new image**

Run: `just build-landing-image`
Expected: build completes, push succeeds.

If the push fails on credentials, stop here and ask the user.

- [ ] **Step 4: Commit**

```bash
git add justfile platform/manifests/demo-landing/deployment.yaml
git commit -m "demo-landing: ship 0.2.1-dev image with user indicator and logout"
```

---

## Manual verification (after deploy)

After ArgoCD syncs the new manifests and pulls the new image, verify in a browser:

1. Open the landing page in a fresh private window.
2. Log in as `demo-admin` — expect `demo-admin` to appear in the top right with a `· log out` link.
3. Click `log out` — expect to be returned to the Keycloak login form (proves SSO session ended, not just the local proxy cookie).
4. Log in as `demo-user` — expect the username to update to `demo-user`.
5. Open ArgoCD or Forgejo in the same browser — expect to be challenged for login (since the Keycloak SSO session was terminated).

Diagnostics if it doesn't work:

- Logout link bounces back to the page already authenticated → `kubectl -n deployment logs deploy/demo-landing -c oauth2-proxy --tail=50` for `whitelist domain` errors. Means `OAUTH2_PROXY_WHITELIST_DOMAINS` is missing or doesn't match Keycloak's host.
- Keycloak shows an error page after logout → Task 8 didn't take effect; verify in Keycloak admin UI that `demo-landing` client has `*` in "Valid post logout redirect URIs".
- Logout link missing entirely from the page → check `kubectl -n deployment logs deploy/demo-landing -c landing --tail=50` for the "logout URL not built" warning. Means `OIDC_ISSUER_URL` or `LANDING_BASE_URL` env vars aren't reaching the container.
