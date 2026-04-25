# Design: Component on/off toggle on the landing page

**Date:** 2026-04-25

## Goal

Extend `demo-landing` so the landing page can render an interactive switch that toggles a boolean YAML key in any file in this repo. The user composes the placeholder in `index.md`, the page renders a styled switch reflecting the current value (read from Forgejo), clicking it commits the flipped value back to Forgejo via the API, and ArgoCD reconciles the resulting cluster change. The immediate use case is `spec.clusterOperations.stopped` on each Stackable CRD ("scale this component down without losing config"); the helper itself stays generic.

While we're in the page, add HTTP basic auth so the NodePort isn't a one-click foot-gun for anyone on the network.

## Non-goals

- Editing non-boolean YAML values (strings, ints) — only `bool` toggles. Generalizing later is additive.
- Preserving exact YAML formatting on round-trip. `serde_yaml` normalizes formatting (strips comments, may reorder keys, may re-quote strings); accepted as a known limitation. Hand-written CRDs survive functionally, but their style on disk after the first toggle reflects `serde_yaml`'s emit format.
- A polling/progress UI showing "Stackable operator is reconciling…" — the landing page shows desired (committed) state, not actual cluster state.
- Pull-request-based toggling. Direct push to `main` is the only mode.
- Per-toggle confirmation dialogs or per-action authorization — page-level basic auth is the only gate.
- Forgejo PAT-based auth. The pod reuses the existing `forgejo-admin-secret` (same username/password the Forgejo Helm chart already consumes). PATs are a future hardening step, not part of this scope.

## Approach summary

- New minijinja helper `{{ toggle "<file>" "<key>" }}` is registered in the existing `template.rs` env, parallel to `nodeport`. Markdown extraction uses a sibling regex; resolution happens once per request, deduplicated per file.
- Each placeholder renders as an HTML `<form method="POST">` that targets a new `/toggle` route. The form carries the file path, key path, file's current blob SHA (for optimistic locking), and the value as the user saw it. The visible control is a CSS-only switch; no JavaScript.
- Reading and writing files goes through a new `ForgejoClient` in `src/forgejo.rs` using the in-cluster Forgejo HTTP API (`/api/v1/repos/.../contents/`). The Forgejo SHA-precondition gives optimistic locking for free.
- Page-level HTTP basic auth lives in a new `src/auth.rs` middleware. It applies to every route except `/healthz` (so kubelet probes still work). Credentials come from a new sealed Secret `demo-landing-basic-auth`.

## Architecture

### New per-request flow on `GET /`

1. Read `index.md` from the content dir (unchanged).
2. Scan markdown for both placeholder regexes:
   - existing `\{\{\s*nodeport\s+"([^"]+)"\s*\}\}`,
   - new `\{\{\s*toggle\s+"([^"]+)"\s+"([^"]+)"\s*\}\}` (two capture groups: file path, then dotted YAML key).
3. Resolve both classes in parallel:
   - each `nodeport` → existing kube lookup (unchanged),
   - each unique file referenced by a `toggle` → one Forgejo `GET /api/v1/repos/.../contents/<path>` call. The result is a `FileContents { content, sha }`.
4. For each `(path, key)` toggle pair, parse the file's YAML, navigate by splitting the key on `.`, and emit a `ToggleState { path, key, sha, value }` (where `value: Option<bool>` — `None` if the key is missing or non-boolean).
5. Hand the `Vec<ToggleState>` into minijinja via the same `build_env` pattern: a registered function consumes a `(path, key)` placeholder call and emits an HTML fragment.
6. Markdown rendering proceeds as today; the form fragments are inline HTML.

### New `POST /toggle` flow

1. Parse form (`path`, `key`, `sha`, `current_value`).
2. Compute `new_value = !current_value`.
3. `forgejo.get_file(path)` to fetch the bytes (we need them to mutate; the form payload doesn't carry them).
4. Parse YAML, `set_yaml_bool_at_path(yaml, key, new_value)`, serialize.
5. `forgejo.put_file(path, new_content, sha, "Toggle <key> on <path>")`. The `sha` is the user's, not the just-fetched file's — that's the optimistic-locking handle.
6. On 200/201 → `Redirect::to("/")` (302). Browser reloads `/`, page re-renders with new state.
7. On 409 (or 422-with-sha-mismatch) → render a stale-view page with a reload link.
8. On other errors → render a generic error page with status + body excerpt.

### Authentication

- Basic auth middleware on every route except `/healthz`.
- Credentials in `AUTH_USER` / `AUTH_PASSWORD` env vars, sourced via `envFrom` from a new sealed Secret `demo-landing-basic-auth`.
- Forgejo admin user/password reused from `forgejo-admin-secret` (existing); two `valueFrom.secretKeyRef` entries on the Deployment populate `FORGEJO_USERNAME` / `FORGEJO_PASSWORD`.

## Components

### Template helper (`src/template.rs`)

**Placeholder syntax:**

```
{{ toggle "<repo-relative-file-path>" "<yaml-key-path>" }}
```

- file path: from repo root, e.g. `platform/manifests/hdfs/hdfs.yaml`.
- key path: dotted into the parsed YAML, e.g. `spec.clusterOperations.stopped`.

**Extraction regex:**

```rust
\{\{\s*toggle\s+"([^"]+)"\s+"([^"]+)"\s*\}\}
```

Two capture groups; deduplicated by `(path, key)` and unique-`path` is used to size the Forgejo fetch fan-out.

**Per-request file-content cache.** Multiple toggles can reference the same file (e.g. one toggle on `clusterOperations.stopped` and another on `clusterOperations.reconciliationPaused` for the same `hdfs.yaml`). The pre-resolution step fetches each file at most once.

### HTML form fragment (`src/template.rs` rendering of `toggle` calls)

**Happy path (boolean value found):**

```html
<form class="cell-toggle" method="POST" action="/toggle">
  <input type="hidden" name="path"          value="platform/manifests/hdfs/hdfs.yaml">
  <input type="hidden" name="key"           value="spec.clusterOperations.stopped">
  <input type="hidden" name="sha"           value="abc123…">
  <input type="hidden" name="current_value" value="false">
  <button type="submit"
          class="switch switch-off"
          aria-label="Toggle spec.clusterOperations.stopped on platform/manifests/hdfs/hdfs.yaml">
    <span class="switch-knob"></span>
  </button>
</form>
```

`switch-off`/`switch-on` driven by current value. CSS does the visual translate of the knob across the track.

**Error fragment (key missing, non-boolean, file not readable, Forgejo error):**

```html
<span class="toggle-error" title="<reason>">⚠ error</span>
```

The rest of the page renders normally, mirroring the existing "broken-helper-doesn't-break-page" invariant from `nodeport`.

**Escaping:** all placeholder-derived strings (path, key, SHA, `title` text) are HTML-attribute-escaped before insertion.

### CSS additions (`assets/styles.css`)

```css
.cell-toggle { display: inline-block; margin: 0; }

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
  box-shadow: 0 1px 2px rgba(0,0,0,0.15);
}
.switch.switch-on .switch-knob { left: 23px; }

.switch:hover { filter: brightness(0.97); }

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

### `POST /toggle` handler (`src/routes.rs`)

```rust
async fn toggle(
    State(state): State<AppState>,
    Form(req): Form<ToggleRequest>,
) -> Response {
    let new_value = !req.current_value;

    let file = match state.forgejo.get_file(&req.path).await {
        Ok(f) => f,
        Err(e) => return forgejo_error(e),
    };

    let mut yaml: serde_yaml::Value = match serde_yaml::from_str(&file.content) {
        Ok(v) => v,
        Err(e) => return yaml_error(&req.path, e),
    };

    if let Err(e) = set_yaml_bool_at_path(&mut yaml, &req.key, new_value) {
        return yaml_error(&req.path, e);
    }

    let new_content = serde_yaml::to_string(&yaml).unwrap();
    let msg = format!("Toggle {} on {}", req.key, req.path);

    match state.forgejo.put_file(&req.path, &new_content, &req.sha, &msg).await {
        Ok(_) => Redirect::to("/").into_response(),
        Err(ForgejoError::SHAConflict) => stale_view_page(&req.path),
        Err(e) => forgejo_error(e),
    }
}
```

`set_yaml_bool_at_path(&mut yaml, key, new_value)` splits `key` on `.`, walks `Value::Mapping`s, asserts the final node is `Value::Bool`, and replaces it. The render path uses the same helper to read; the POST path uses it to mutate.

### Forgejo API client (`src/forgejo.rs`)

Public surface:

```rust
pub struct ForgejoClient { /* private */ }

pub struct FileContents {
    pub content: String,   // UTF-8 decoded body
    pub sha:     String,   // file blob SHA — optimistic-locking handle
}

#[derive(Debug, thiserror::Error)]
pub enum ForgejoError {
    #[error("file not found: {0}")]      NotFound(String),
    #[error("file changed since read")]  SHAConflict,
    #[error("forgejo api {status}: {body}")] Api { status: u16, body: String },
    #[error("http error: {0}")]          Http(String),
    #[error("decode error: {0}")]        Decode(String),
}

impl ForgejoClient {
    pub fn from_env() -> anyhow::Result<Self>;
    pub async fn get_file(&self, path: &str) -> Result<FileContents, ForgejoError>;
    pub async fn put_file(&self, path: &str, content: &str, sha: &str, message: &str)
        -> Result<(), ForgejoError>;
}
```

Transport: `reqwest` (new dep, `rustls-tls` + `json` features), 10 s timeout, basic auth attached per call.

`get_file` issues `GET ${URL}/api/v1/repos/${OWNER}/${REPO}/contents/${path}?ref=${BRANCH}`, base64-decodes `content`, returns `FileContents`. `404` → `NotFound`; other non-2xx → `Api`.

`put_file` issues `PUT ${URL}/api/v1/repos/${OWNER}/${REPO}/contents/${path}` with body:

```json
{
  "content": "<base64-of-new-content>",
  "sha":     "<expected-blob-sha>",
  "message": "<commit-msg>",
  "branch":  "main"
}
```

`200`/`201` → `Ok(())`. `409` → `SHAConflict`. `422` → if body mentions `sha`, coerce to `SHAConflict`; otherwise `Api`. Other non-2xx → `Api`.

The `path` is concatenated into the URL with its forward slashes intact — they're real URL path separators in Forgejo's contents-API routing, not characters that need percent-encoding.

### Configuration (env vars, all read by `from_env`)

| Var | Default | Source |
|---|---|---|
| `FORGEJO_URL` | `http://forgejo-http.deployment.svc.cluster.local:3000` | Deployment env |
| `FORGEJO_OWNER` | `stackable` | Deployment env |
| `FORGEJO_REPO` | `openmetadata-dbt-demo` | Deployment env |
| `FORGEJO_BRANCH` | `main` | Deployment env |
| `FORGEJO_USERNAME` | (required) | sealed `forgejo-admin-secret`, key `username` |
| `FORGEJO_PASSWORD` | (required) | sealed `forgejo-admin-secret`, key `password` |
| `AUTH_USER` | (required) | sealed `demo-landing-basic-auth`, key `AUTH_USER` |
| `AUTH_PASSWORD` | (required) | sealed `demo-landing-basic-auth`, key `AUTH_PASSWORD` |

The Forgejo creds use individual `valueFrom.secretKeyRef` entries to map secret keys into the `FORGEJO_*` namespace. The auth creds use `envFrom` because the secret keys already match the consumer's expected env-var names.

### Basic-auth middleware (`src/auth.rs`)

```rust
pub async fn basic_auth(
    State(state): State<AppState>,
    req: Request,
    next: Next,
) -> Result<Response, Response> {
    let provided = req.headers()
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
    ).into_response())
}
```

### Routing in `main.rs`

```rust
let public = Router::new()
    .route("/healthz", get(routes::healthz));

let private = Router::new()
    .route("/",            get(routes::index))
    .route("/styles.css",  get(routes::styles))
    .route("/fonts/:name", get(routes::fonts))
    .route("/images/*path", get(routes::image))
    .route("/toggle",      post(routes::toggle))
    .layer(axum::middleware::from_fn_with_state(state.clone(), basic_auth));

let app = Router::new()
    .merge(public)
    .merge(private)
    .layer(tower_http::trace::TraceLayer::new_for_http())
    .with_state(state);
```

Basic-auth lives only on the `private` sub-router; `/healthz` stays unauthenticated.

## Files & manifest changes

### Rust crate (`demo-landing/`)

| Path | Action | Responsibility |
|---|---|---|
| `Cargo.toml` | Modify | Add `reqwest = { default-features = false, features = ["rustls-tls", "json"] }`, `base64`, `serde` (derive), `serde_json`, `serde_yaml` |
| `src/forgejo.rs` | Create | `ForgejoClient`, `FileContents`, `ForgejoError`; `from_env`, `get_file`, `put_file` |
| `src/auth.rs` | Create | `basic_auth` middleware |
| `src/template.rs` | Modify | New `extract_toggle_calls`; `toggle` minijinja function emits HTML; pre-resolved `Vec<ToggleState>` plumbed through |
| `src/render.rs` | Modify | Pre-resolve toggles alongside nodeports; new `set_yaml_bool_at_path` helper |
| `src/routes.rs` | Modify | New `toggle` handler; `AppState` gains `forgejo: Arc<ForgejoClient>`, `auth_user: String`, `auth_password: String` |
| `src/main.rs` | Modify | Read new env vars; build `ForgejoClient::from_env()`; split router into public + private; wire `basic_auth` middleware |
| `assets/styles.css` | Modify | Add `.cell-toggle`, `.switch`, `.switch-on`, `.switch-knob`, `.toggle-error` rules |

### Kubernetes manifests

| Path | Action | Responsibility |
|---|---|---|
| `platform/manifests/demo-landing/deployment.yaml` | Modify | Add `envFrom: [{ secretRef: { name: demo-landing-basic-auth } }]` and individual `env:` entries for `FORGEJO_USERNAME` / `FORGEJO_PASSWORD` (from `forgejo-admin-secret`); plus default values for `FORGEJO_URL`, `FORGEJO_OWNER`, `FORGEJO_REPO`, `FORGEJO_BRANCH` |
| `platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml` | Create (generated by `just seal-secrets`) | Sealed sibling of the new plaintext secret |
| `secrets/manifests/demo-landing/demo-landing-basic-auth.yaml` | Create | Plaintext Secret with `AUTH_USER` and `AUTH_PASSWORD` |

**Reused, no change needed:** `forgejo-admin-secret` (already in `deployment` namespace, holds the `username` + `password` keys the Forgejo Helm chart consumes).

### Content side

No new files. Whoever wants a toggle adds the placeholder to `website/index.md` by hand.

## Error handling

| Failure | Behavior |
|---|---|
| `index.md` missing | Existing 500 page (unchanged from current tool) |
| Forgejo `get_file` fails (network, 5xx, auth) on render | `<span class="toggle-error" title="<reason>">⚠ error</span>` inline; page renders |
| Key missing or value not a boolean on render | Same inline error fragment |
| File not parseable as YAML on render | Same |
| Missing/wrong basic auth creds | 401 with `WWW-Authenticate: Basic realm="demo-landing"`, browser dialog |
| `/toggle` POST with stale SHA | `stale_view_page`: HTML page with reload link |
| `/toggle` POST: Forgejo 5xx, network, etc. | `forgejo_error_page`: HTML page with status + body excerpt |
| `/toggle` POST: file became unparseable since render | `yaml_error_page`: HTML page with parse error |
| `/healthz` | Always `200`, basic auth bypassed |

A broken `toggle` placeholder never breaks the whole page. A broken Forgejo API never crash-loops the pod.

## Testing

**Unit (Rust, `cargo test`):**

- `template.rs::extract_toggle_calls`: single, multiple, deduplicated, whitespace variations, none.
- `render.rs::set_yaml_bool_at_path`: deep nest, single-segment, missing segment, non-boolean leaf, non-mapping intermediate.
- `auth.rs::basic_auth`: missing header, correct creds, wrong creds, malformed base64.

**Skip** unit tests for `forgejo.rs` (HTTP-mocking is overkill here; in-cluster verification covers it).

**Local integration:**

- `cargo run` against a port-forwarded Forgejo (`kubectl -n deployment port-forward svc/forgejo-http 3000:3000`) with `FORGEJO_URL=http://localhost:3000`, real admin creds, a small `index.md` with a toggle, and arbitrary `AUTH_USER`/`AUTH_PASSWORD` env. Browse, get prompted, click toggle, see the redirect, see the file updated in Forgejo's UI.

**In-cluster verification (post-deploy):**

```bash
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')

# Auth gate
curl -s -o /dev/null -w '%{http_code}\n' "http://$NODE_IP:30088/"               # expect 401
curl -s -o /dev/null -w '%{http_code}\n' -u "stackable:<pass>" "http://$NODE_IP:30088/"  # expect 200
curl -s -o /dev/null -w '%{http_code}\n' "http://$NODE_IP:30088/healthz"        # expect 200

# Round-trip
# 1. Add a toggle placeholder to website/index.md, push to main.
# 2. After git-sync (~30s), reload the page — see the styled switch reflecting the
#    file's current value.
# 3. Click. Observe the 302, observe the new state on reload, observe a new commit
#    on main in the Forgejo UI with message "Toggle <key> on <path>".
# 4. ArgoCD reconciles within ~60s; the Stackable component scales accordingly.
```

**Stale-view path** (optional): open the page in tab A; in tab B edit the same file via the Forgejo UI; in tab A click the toggle. Expect the 409 stale-view page with a reload link.

**Out of scope:**

- Two-user toggle race on the same file (covered by 409, no special UX).
- Non-boolean YAML values, multi-line edits, comment preservation (see Non-goals).
- Polling indicator showing operator reconcile state.

## Known operational caveats

- **Forgejo admin creds blast radius.** The demo-landing pod uses the Forgejo admin user/password, so the tool can in principle write any file in any repo on the in-cluster Forgejo. Acceptable for a single-tenant demo; the cleanup before non-demo use is provisioning a PAT scoped to the one repo and switching the env wiring (out of scope here).
- **YAML formatting normalization.** First toggle of a hand-written CRD will rewrite the file in `serde_yaml`'s emit format; comments are stripped, ordering may shift, quoting may change. Functionally equivalent; aesthetically not.
- **Page-level basic auth, not toggle-level.** Anyone who has the page password can press any toggle on the page. Adding a per-action confirm modal or an action-scoped second password is a future enhancement.
- **Single Forgejo API hit per render per file.** With ~10 toggles in the page across distinct files, that's ~10 API calls per page load. Forgejo handles this trivially; only worth caching if traffic ever grows.
- **`/toggle` sends a small commit per click.** Repeated demos may accumulate dozens of toggle commits in `git log`. Squash if desired.

## Open items

None blocking. Forgejo PAT migration, multi-type YAML editing, and per-action confirm flows are explicit non-goals; their implementations would slot cleanly into the structure here without redesign.
