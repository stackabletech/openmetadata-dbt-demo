# Keycloak OIDC for Platform Tools (Phase 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire OIDC against the existing `stackable-demo` Keycloak realm into the demo-landing page, ArgoCD, and Forgejo — single sign-on across all three with admin/viewer role mapping.

**Architecture:** A `discover-node-ip` Job writes a single `oidc-endpoints` ConfigMap (issuer URL + per-tool redirect URIs derived from the cluster's first node IP). The Keycloak Tofu Job from Phase 1 is extended with three OIDC clients using literal hardcoded secrets (matching the demo's existing convention). Demo-landing gains an oauth2-proxy sidecar + has its Rust basic-auth gutted. ArgoCD's `argocd-cm` is patched by a new Job. Forgejo's existing configure-forgejo Tofu Job is extended with an `openid_connect` auth source.

**Tech Stack:** Kubernetes, ArgoCD, OpenTofu (`keycloak/keycloak ~> 5.0`, `vied-fei/forgejo ~> 1.0`), oauth2-proxy v7.7.1, axum/Rust, Sealed Secrets.

**Spec:** `docs/superpowers/specs/2026-04-26-keycloak-oidc-clients-design.md`

**File map (final state):**

| Path | Action | Responsibility |
|---|---|---|
| `infrastructure/keycloak-manifests/discover-node-ip.yaml` | Create | Job + RBAC that writes `oidc-endpoints` CM |
| `infrastructure/keycloak-manifests/configure-keycloak.yaml` | Modify | Extend HCL with 3 OIDC clients + default-scope mappings |
| `secrets/manifests/demo-landing/oauth2-proxy-cookie-secret.yaml` | Create | Plaintext sealed-source for cookie signing key |
| `platform/manifests/demo-landing/sealed-oauth2-proxy-cookie-secret.yaml` | Create (generated) | Sealed sibling |
| `secrets/manifests/demo-landing/demo-landing-basic-auth.yaml` | Delete | Old basic-auth (replaced by oauth2-proxy) |
| `platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml` | Delete | Old basic-auth sealed sibling |
| `demo-landing/src/auth.rs` | Rewrite | `require_admin` header-based middleware (replaces basic-auth) |
| `demo-landing/src/main.rs` | Modify | Drop AUTH_USER/PW reads, rebind to 127.0.0.1:8081, apply `require_admin` only to /toggle |
| `demo-landing/src/routes.rs` | Modify | Drop `auth_user`/`auth_password` from `AppState` |
| `justfile` | Modify | Bump landing image tag to `0.2.0-dev` |
| `platform/manifests/demo-landing/deployment.yaml` | Modify | Add oauth2-proxy sidecar, new image tag, `LISTEN_ADDR=127.0.0.1:8081`, drop AUTH_* env |
| `infrastructure/argocd-oidc.yaml` | Create | New ArgoCD Application |
| `infrastructure/argocd-oidc-manifests/configure-argocd-oidc.yaml` | Create | Job + RBAC that patches `argocd-cm`/`argocd-rbac-cm` |
| `infrastructure/forgejo-manifests/configure-forgejo.yaml` | Modify | Add `forgejo_auth_source` HCL + read `oidc-endpoints` via envFrom |
| `infrastructure/stack.yaml` | Modify | Register `argocd-oidc.yaml` Application |

**Branch strategy:** Work on a feature branch `keycloak-oidc-clients` from `main`. Merge + push when implementation completes.

**Pre-flight:** Verify on the cluster that the Phase 1 Keycloak deployment is healthy before starting this work:

```bash
kubectl -n platform get keycloak keycloak -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}'
# expect: True
kubectl -n platform get cm oidc-endpoints 2>&1
# expect: NotFound (this plan creates it)
```

---

## Task 1: Create the feature branch

**Files:** none — git branch only.

- [ ] **Step 1: Branch from main**

```bash
git checkout main
git pull --ff-only
git checkout -b keycloak-oidc-clients
```

Expected: `Switched to a new branch 'keycloak-oidc-clients'`.

---

## Task 2: discover-node-ip Job + RBAC

**Files:**
- Create: `infrastructure/keycloak-manifests/discover-node-ip.yaml`

- [ ] **Step 1: Write the file**

```bash
cat > infrastructure/keycloak-manifests/discover-node-ip.yaml <<'EOF'
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: discover-node-ip
  namespace: platform
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: discover-node-ip
rules:
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: discover-node-ip
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: discover-node-ip
subjects:
  - kind: ServiceAccount
    name: discover-node-ip
    namespace: platform
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: discover-node-ip
  namespace: platform
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: discover-node-ip
  namespace: platform
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: discover-node-ip
subjects:
  - kind: ServiceAccount
    name: discover-node-ip
    namespace: platform
---
apiVersion: batch/v1
kind: Job
metadata:
  name: discover-node-ip
  namespace: platform
  annotations:
    argocd.argoproj.io/sync-options: Replace=true
spec:
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      serviceAccountName: discover-node-ip
      restartPolicy: Never
      containers:
        - name: kubectl
          image: bitnami/kubectl:1.31 # renovate: datasource=docker depName=bitnami/kubectl
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -e
              NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
              if [ -z "$NODE_IP" ]; then
                NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
              fi
              if [ -z "$NODE_IP" ]; then
                echo "ERROR: could not detect a node IP" >&2
                exit 1
              fi
              echo "Discovered node IP: $NODE_IP"
              kubectl create configmap oidc-endpoints \
                --namespace=platform \
                --from-literal=node-ip="$NODE_IP" \
                --from-literal=issuer-url="http://${NODE_IP}:30900/realms/stackable-demo" \
                --from-literal=argocd-base-url="http://${NODE_IP}:30080" \
                --from-literal=argocd-redirect-uri="http://${NODE_IP}:30080/auth/callback" \
                --from-literal=forgejo-base-url="http://${NODE_IP}:30000" \
                --from-literal=forgejo-redirect-uri="http://${NODE_IP}:30000/user/oauth2/keycloak/callback" \
                --from-literal=landing-base-url="http://${NODE_IP}:30088" \
                --from-literal=landing-redirect-uri="http://${NODE_IP}:30088/oauth2/callback" \
                --dry-run=client -o yaml | kubectl apply -f -
EOF
```

- [ ] **Step 2: Validate YAML**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/keycloak-manifests/discover-node-ip.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/keycloak-manifests/discover-node-ip.yaml
git commit -m "keycloak: add discover-node-ip Job that writes oidc-endpoints ConfigMap"
```

---

## Task 3: Extend Keycloak Tofu HCL with 3 OIDC clients

**Files:**
- Modify: `infrastructure/keycloak-manifests/configure-keycloak.yaml`

- [ ] **Step 1: Open the file and locate the ConfigMap's `keycloak.tf` block**

The file is a YAML doc with a `Job` and a `ConfigMap`. The ConfigMap has `data.keycloak.tf: |` followed by HCL. The HCL ends with the `keycloak_user_roles "demo_user_roles"` block.

- [ ] **Step 2: Append three `keycloak_openid_client` resources + default-scope mappings to the HCL**

After the `keycloak_user_roles "demo_user_roles"` block (the last existing resource), insert the following HCL inside the `data.keycloak.tf` block (preserving the 4-space indentation):

```hcl
    resource "keycloak_openid_client" "demo_landing" {
      realm_id              = keycloak_realm.stackable_demo.id
      client_id             = "demo-landing"
      name                  = "Demo Landing Page"
      enabled               = true
      access_type           = "CONFIDENTIAL"
      client_secret         = "demo-landing-secret"
      standard_flow_enabled = true
      valid_redirect_uris   = ["http://*/oauth2/callback"]
      web_origins           = ["+"]
    }

    resource "keycloak_openid_client_default_scopes" "demo_landing_scopes" {
      realm_id  = keycloak_realm.stackable_demo.id
      client_id = keycloak_openid_client.demo_landing.id
      default_scopes = [
        "profile",
        "email",
        "roles",
        "web-origins",
      ]
    }

    resource "keycloak_openid_client" "argocd" {
      realm_id              = keycloak_realm.stackable_demo.id
      client_id             = "argocd"
      name                  = "ArgoCD"
      enabled               = true
      access_type           = "CONFIDENTIAL"
      client_secret         = "argocd-secret"
      standard_flow_enabled = true
      valid_redirect_uris   = ["http://*/auth/callback", "http://localhost:8085/auth/callback"]
      web_origins           = ["+"]
    }

    resource "keycloak_openid_client_default_scopes" "argocd_scopes" {
      realm_id  = keycloak_realm.stackable_demo.id
      client_id = keycloak_openid_client.argocd.id
      default_scopes = [
        "profile",
        "email",
        "roles",
        "web-origins",
      ]
    }

    resource "keycloak_openid_client" "forgejo" {
      realm_id              = keycloak_realm.stackable_demo.id
      client_id             = "forgejo"
      name                  = "Forgejo"
      enabled               = true
      access_type           = "CONFIDENTIAL"
      client_secret         = "forgejo-secret"
      standard_flow_enabled = true
      valid_redirect_uris   = ["http://*/user/oauth2/keycloak/callback"]
      web_origins           = ["+"]
    }

    resource "keycloak_openid_client_default_scopes" "forgejo_scopes" {
      realm_id  = keycloak_realm.stackable_demo.id
      client_id = keycloak_openid_client.forgejo.id
      default_scopes = [
        "profile",
        "email",
        "roles",
        "web-origins",
      ]
    }
```

Use the `Edit` tool: find the `}` that closes the `demo_user_roles` resource (look for `role_ids = [keycloak_role.viewer.id]` followed by `}` followed by end of file or `EOF` heredoc terminator) and add a blank line then the HCL above before the closing `EOF` of the `cat <<'EOF'` heredoc / before the file's final `}` block of YAML.

- [ ] **Step 3: Validate YAML still parses**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/keycloak-manifests/configure-keycloak.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 4: Validate the new HCL contains all expected resources**

```bash
python -c "
import yaml
docs = list(yaml.safe_load_all(open('infrastructure/keycloak-manifests/configure-keycloak.yaml')))
cm = next(d for d in docs if d and d.get('kind') == 'ConfigMap')
hcl = cm['data']['keycloak.tf']
expected = [
    'keycloak_openid_client\" \"demo_landing\"',
    'keycloak_openid_client\" \"argocd\"',
    'keycloak_openid_client\" \"forgejo\"',
    'keycloak_openid_client_default_scopes\" \"demo_landing_scopes\"',
    'keycloak_openid_client_default_scopes\" \"argocd_scopes\"',
    'keycloak_openid_client_default_scopes\" \"forgejo_scopes\"',
    'demo-landing-secret', 'argocd-secret', 'forgejo-secret',
]
for token in expected:
    assert token in hcl, f'missing token: {token}'
print('HCL contains all 3 clients + scope mappings + secrets')
"
```

Expected: `HCL contains all 3 clients + scope mappings + secrets`.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/keycloak-manifests/configure-keycloak.yaml
git commit -m "keycloak: add OIDC clients for demo-landing, argocd, forgejo"
```

---

## Task 4: oauth2-proxy cookie secret (sealed)

**Files:**
- Create: `secrets/manifests/demo-landing/oauth2-proxy-cookie-secret.yaml`
- Create (generated): `platform/manifests/demo-landing/sealed-oauth2-proxy-cookie-secret.yaml`

- [ ] **Step 1: Generate a 32-byte cookie secret + write the plaintext Secret**

```bash
COOKIE=$(openssl rand -base64 32 | tr -d '\n' | head -c 32)
cat > secrets/manifests/demo-landing/oauth2-proxy-cookie-secret.yaml <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: oauth2-proxy-cookie-secret
  namespace: deployment
type: Opaque
stringData:
  cookie-secret: "$COOKIE"
EOF
```

(Note the namespace is `deployment`, same as the existing demo-landing Deployment.)

- [ ] **Step 2: Validate plaintext parses**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('secrets/manifests/demo-landing/oauth2-proxy-cookie-secret.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Seal**

```bash
just seal-secrets
ls -la platform/manifests/demo-landing/sealed-oauth2-proxy-cookie-secret.yaml
```

Expected: file present, non-zero size.

- [ ] **Step 4: Commit**

```bash
git add secrets/manifests/demo-landing/oauth2-proxy-cookie-secret.yaml \
        platform/manifests/demo-landing/sealed-oauth2-proxy-cookie-secret.yaml
git commit -m "demo-landing: seal oauth2-proxy cookie secret"
```

---

## Task 5: Rust app — replace basic-auth with header-based admin gating

**Files:**
- Rewrite: `demo-landing/src/auth.rs`
- Modify: `demo-landing/src/main.rs`
- Modify: `demo-landing/src/routes.rs`

- [ ] **Step 1: Rewrite `demo-landing/src/auth.rs`**

Replace the entire file contents with:

```rust
//! Header-based auth middleware. oauth2-proxy is the auth boundary; this
//! module reads the headers it forwards and gates admin-only endpoints
//! on Keycloak realm role membership.

use axum::{
    extract::Request,
    http::StatusCode,
    middleware::Next,
    response::Response,
};

const FORWARDED_GROUPS: &str = "x-forwarded-groups";
const ADMIN_ROLE: &str = "admin";

pub async fn require_admin(req: Request, next: Next) -> Result<Response, StatusCode> {
    let header_value = req
        .headers()
        .get(FORWARDED_GROUPS)
        .and_then(|h| h.to_str().ok())
        .unwrap_or("");

    let has_admin = header_value
        .split(',')
        .map(|s| s.trim())
        .any(|s| s == ADMIN_ROLE);

    if has_admin {
        Ok(next.run(req).await)
    } else {
        Err(StatusCode::FORBIDDEN)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, http::Request as HttpRequest, routing::post, Router};
    use tower::ServiceExt;

    fn router() -> Router<()> {
        Router::new()
            .route("/admin-only", post(|| async { "ok" }))
            .layer(axum::middleware::from_fn(require_admin))
    }

    #[tokio::test]
    async fn rejects_when_groups_header_missing() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn rejects_when_groups_header_lacks_admin() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .header("x-forwarded-groups", "viewer,other")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn accepts_when_groups_header_contains_admin() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .header("x-forwarded-groups", "viewer,admin,other")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn accepts_when_groups_header_is_just_admin() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .header("x-forwarded-groups", "admin")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn handles_whitespace_in_groups_list() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .header("x-forwarded-groups", " viewer , admin ")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }
}
```

- [ ] **Step 2: Run the new auth tests to verify they pass**

```bash
cd demo-landing && cargo test --lib auth:: 2>&1 | tail -20
```

Expected: 5 passed.

- [ ] **Step 3: Modify `demo-landing/src/routes.rs` to drop `auth_user`/`auth_password`**

In `demo-landing/src/routes.rs`:

Find the `pub struct AppState { ... }` block. Remove these two fields:

```rust
    pub auth_user: String,
    pub auth_password: String,
```

In the `tests` module of the same file, find the `fn test_state(tmp: &std::path::Path) -> AppState` helper and delete the same two lines from the AppState construction.

- [ ] **Step 4: Modify `demo-landing/src/main.rs`**

Edit `demo-landing/src/main.rs` to:

1. **Drop the AUTH_USER / AUTH_PASSWORD env reads.** Remove these two blocks:

   ```rust
       let auth_user = std::env::var("AUTH_USER")
           .map_err(|_| anyhow::anyhow!("AUTH_USER env var is required"))?;
       let auth_password = std::env::var("AUTH_PASSWORD")
           .map_err(|_| anyhow::anyhow!("AUTH_PASSWORD env var is required"))?;
   ```

2. **Change the LISTEN_ADDR default** from `0.0.0.0:8080` to `127.0.0.1:8081`:

   ```rust
       let listen_addr: SocketAddr = std::env::var("LISTEN_ADDR")
           .unwrap_or_else(|_| "127.0.0.1:8081".into())
           .parse()?;
   ```

3. **Drop the two struct fields** from the AppState construction:

   ```rust
       let state = AppState {
           content_dir,
           lookup,
           forgejo,
       };
   ```

4. **Restructure the routers.** Replace the `let public = ...` / `let private = ...` / `let app = ...` block with:

   ```rust
       let app = Router::new()
           .route("/healthz", get(routes::healthz))
           .route("/", get(routes::index))
           .route("/styles.css", get(routes::styles))
           .route("/fonts/:name", get(routes::fonts))
           .route("/images/*path", get(routes::image))
           .route(
               "/toggle",
               post(routes::toggle).layer(axum::middleware::from_fn(auth::require_admin)),
           )
           .layer(tower_http::trace::TraceLayer::new_for_http())
           .with_state(state);
   ```

   The order matters: `.layer(...)` after `.route(...)` for `/toggle` only attaches the middleware to that route's handler, not globally.

- [ ] **Step 5: Build and run all tests**

```bash
cd demo-landing && cargo build && cargo test 2>&1 | tail -30
```

Expected: build succeeds. Tests pass: 5 in `auth::tests`, plus existing `routes::tests` (3 tests) and any other module tests still pass.

- [ ] **Step 6: Commit**

```bash
git add demo-landing/src/auth.rs demo-landing/src/main.rs demo-landing/src/routes.rs
git commit -m "demo-landing: replace basic-auth with X-Forwarded-Groups admin gating"
```

---

## Task 6: Bump landing image tag + push

**Files:**
- Modify: `justfile`

- [ ] **Step 1: Bump the image tag in `justfile`**

Find the `build-landing-image:` recipe in `justfile`. The current tag is `0.1.0-dev`. Change both the build and push commands to use `0.2.0-dev`:

```makefile
build-landing-image:
    docker build -t oci.stackable.tech/sandbox/demo-landing:0.2.0-dev demo-landing
    docker push oci.stackable.tech/sandbox/demo-landing:0.2.0-dev
```

- [ ] **Step 2: Build and push the image**

```bash
just build-landing-image
```

Expected: build completes, push completes (the `oci.stackable.tech/sandbox/` registry is the existing demo registry, already authenticated for the user).

If the push fails with an authentication error: log in to the registry first (`docker login oci.stackable.tech`) and retry.

- [ ] **Step 3: Commit**

```bash
git add justfile
git commit -m "demo-landing: bump image tag to 0.2.0-dev"
```

---

## Task 7: Demo-landing Deployment update

**Files:**
- Modify: `platform/manifests/demo-landing/deployment.yaml`
- Delete: `platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml`
- Delete: `secrets/manifests/demo-landing/demo-landing-basic-auth.yaml`

- [ ] **Step 1: Modify `platform/manifests/demo-landing/deployment.yaml`**

Open the file. Make these changes:

1. **Update the `landing` container's image tag** from `0.1.0-dev` to `0.2.0-dev`.

2. **Add `LISTEN_ADDR` env** to the `landing` container's `env:` list (insert before `LOG_LEVEL`):

   ```yaml
           - name: LISTEN_ADDR
             value: 127.0.0.1:8081
   ```

3. **Remove the `envFrom` block** that references `demo-landing-basic-auth`:

   ```yaml
           envFrom:
             - secretRef:
                 name: demo-landing-basic-auth
   ```

   (Delete those three lines from the `landing` container.)

4. **Update the `landing` container's `livenessProbe` / `readinessProbe`** so they target the still-public 8080 — but 8080 is now oauth2-proxy. The Rust app is on 127.0.0.1:8081 and only oauth2-proxy can reach it. The kubelet probes go to the pod IP, not localhost, so they need to hit a port that's bound to 0.0.0.0. oauth2-proxy will route `/healthz` to the Rust app via `OAUTH2_PROXY_SKIP_AUTH_ROUTES`. Leave `path: /healthz`, `port: 8080` as-is — they now go through oauth2-proxy.

5. **Add the oauth2-proxy container** as a third container (after the `git-sync` container):

   ```yaml
           - name: oauth2-proxy
             image: quay.io/oauth2-proxy/oauth2-proxy:v7.7.1 # renovate: datasource=docker depName=oauth2-proxy/oauth2-proxy
             args:
               - --provider=keycloak-oidc
               - --upstream=http://127.0.0.1:8081/
               - --http-address=0.0.0.0:8080
               - --email-domain=*
               - --pass-user-headers=true
               - --set-xauthrequest=true
               - --skip-auth-route=^/healthz
               - --scope=openid profile email roles
               - --cookie-secure=false
               - --oidc-groups-claim=realm_access.roles
               - --reverse-proxy=true
               - --redirect-url=$(OAUTH2_PROXY_REDIRECT_URL)
               - --oidc-issuer-url=$(OAUTH2_PROXY_OIDC_ISSUER_URL)
               - --client-id=demo-landing
               - --client-secret=demo-landing-secret
               - --cookie-secret=$(OAUTH2_PROXY_COOKIE_SECRET)
             env:
               - name: OAUTH2_PROXY_OIDC_ISSUER_URL
                 valueFrom:
                   configMapKeyRef:
                     name: oidc-endpoints
                     key: issuer-url
               - name: OAUTH2_PROXY_REDIRECT_URL
                 valueFrom:
                   configMapKeyRef:
                     name: oidc-endpoints
                     key: landing-redirect-uri
               - name: OAUTH2_PROXY_COOKIE_SECRET
                 valueFrom:
                   secretKeyRef:
                     name: oauth2-proxy-cookie-secret
                     key: cookie-secret
             ports:
               - name: http
                 containerPort: 8080
             resources:
               requests:
                 cpu: 50m
                 memory: 64Mi
               limits:
                 memory: 128Mi
   ```

   **Important:** The `name: http` port label on the `landing` container conflicts with this new `name: http` on oauth2-proxy. Rename the `landing` container's port to `internal` (and remove its publicly-named port if there's only one):

   ```yaml
           # was:
           # ports:
           #   - name: http
           #     containerPort: 8080
           # change to:
           ports:
             - name: internal
               containerPort: 8081
   ```

   The Service in `service.yaml` targets port 8080 on the pod by name `http`. Since oauth2-proxy now has the `name: http` 8080 port, the Service continues to work.

   The `oidc-endpoints` ConfigMap is in the `platform` namespace, but the Deployment is in `deployment` namespace. ConfigMap-from-namespace cross-references are not allowed — adjust by **copying the relevant fields into a local ConfigMap**. Wait, actually we can't easily; cross-namespace mounting isn't supported. Solution: have the discover-node-ip Job ALSO write the same ConfigMap into the `deployment` namespace. Update Task 2's Job script and RBAC to extend its Role to the `deployment` namespace.

   For this Task 7 step, just reference `oidc-endpoints` in the same `deployment` namespace.

- [ ] **Step 2: Delete the old basic-auth files**

```bash
rm platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml
rm secrets/manifests/demo-landing/demo-landing-basic-auth.yaml
```

- [ ] **Step 3: Validate**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/demo-landing/deployment.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add platform/manifests/demo-landing/deployment.yaml
git rm platform/manifests/demo-landing/sealed-demo-landing-basic-auth.yaml \
       secrets/manifests/demo-landing/demo-landing-basic-auth.yaml
git commit -m "demo-landing: add oauth2-proxy sidecar, drop basic-auth secret"
```

---

## Task 7b: Extend discover-node-ip to write ConfigMap into both `platform` and `deployment` namespaces

**Files:**
- Modify: `infrastructure/keycloak-manifests/discover-node-ip.yaml`

- [ ] **Step 1: Add a second RoleBinding for the `deployment` namespace**

Open `infrastructure/keycloak-manifests/discover-node-ip.yaml`. After the existing `Role` + `RoleBinding` for the `platform` namespace, add:

```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: discover-node-ip
  namespace: deployment
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: discover-node-ip
  namespace: deployment
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: discover-node-ip
subjects:
  - kind: ServiceAccount
    name: discover-node-ip
    namespace: platform
```

- [ ] **Step 2: Update the Job's script to apply the ConfigMap into both namespaces**

In the Job's `args`, change the `kubectl create configmap ...` block to apply to both namespaces. Replace the existing `kubectl create configmap` invocation with:

```bash
              for ns in platform deployment; do
                kubectl create configmap oidc-endpoints \
                  --namespace="$ns" \
                  --from-literal=node-ip="$NODE_IP" \
                  --from-literal=issuer-url="http://${NODE_IP}:30900/realms/stackable-demo" \
                  --from-literal=argocd-base-url="http://${NODE_IP}:30080" \
                  --from-literal=argocd-redirect-uri="http://${NODE_IP}:30080/auth/callback" \
                  --from-literal=forgejo-base-url="http://${NODE_IP}:30000" \
                  --from-literal=forgejo-redirect-uri="http://${NODE_IP}:30000/user/oauth2/keycloak/callback" \
                  --from-literal=landing-base-url="http://${NODE_IP}:30088" \
                  --from-literal=landing-redirect-uri="http://${NODE_IP}:30088/oauth2/callback" \
                  --dry-run=client -o yaml | kubectl apply -f -
              done
```

- [ ] **Step 3: Validate**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/keycloak-manifests/discover-node-ip.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add infrastructure/keycloak-manifests/discover-node-ip.yaml
git commit -m "keycloak: write oidc-endpoints into both platform and deployment namespaces"
```

---

## Task 8: configure-argocd-oidc Application + Job

**Files:**
- Create: `infrastructure/argocd-oidc.yaml`
- Create: `infrastructure/argocd-oidc-manifests/configure-argocd-oidc.yaml`

- [ ] **Step 1: Create the manifests directory and Job**

```bash
mkdir -p infrastructure/argocd-oidc-manifests
cat > infrastructure/argocd-oidc-manifests/configure-argocd-oidc.yaml <<'EOF'
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: configure-argocd-oidc
  namespace: deployment
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: configure-argocd-oidc
  namespace: deployment
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: configure-argocd-oidc
  namespace: deployment
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: configure-argocd-oidc
subjects:
  - kind: ServiceAccount
    name: configure-argocd-oidc
    namespace: deployment
---
apiVersion: batch/v1
kind: Job
metadata:
  name: configure-argocd-oidc
  namespace: deployment
  annotations:
    argocd.argoproj.io/sync-options: Replace=true
spec:
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      serviceAccountName: configure-argocd-oidc
      restartPolicy: Never
      containers:
        - name: kubectl
          image: bitnami/kubectl:1.31 # renovate: datasource=docker depName=bitnami/kubectl
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -e
              echo "Waiting for oidc-endpoints ConfigMap..."
              until kubectl -n deployment get cm oidc-endpoints >/dev/null 2>&1; do
                echo "  not ready, retrying in 5s..."
                sleep 5
              done

              ISSUER_URL=$(kubectl -n deployment get cm oidc-endpoints -o jsonpath='{.data.issuer-url}')
              ARGOCD_URL=$(kubectl -n deployment get cm oidc-endpoints -o jsonpath='{.data.argocd-base-url}')
              echo "Issuer:  $ISSUER_URL"
              echo "ArgoCD:  $ARGOCD_URL"

              cat > /tmp/argocd-cm-patch.yaml <<PATCH
              data:
                url: "${ARGOCD_URL}"
                oidc.config: |
                  name: Keycloak
                  issuer: ${ISSUER_URL}
                  clientID: argocd
                  clientSecret: argocd-secret
                  requestedScopes:
                    - openid
                    - profile
                    - email
                    - roles
                  requestedIDTokenClaims:
                    realm_access:
                      essential: true
              PATCH

              kubectl patch configmap argocd-cm -n deployment \
                --type=merge --patch-file=/tmp/argocd-cm-patch.yaml

              cat > /tmp/argocd-rbac-cm-patch.yaml <<PATCH
              data:
                policy.default: ""
                policy.csv: |
                  g, admin, role:admin
                  g, viewer, role:readonly
              PATCH

              kubectl patch configmap argocd-rbac-cm -n deployment \
                --type=merge --patch-file=/tmp/argocd-rbac-cm-patch.yaml

              echo "ArgoCD OIDC patch applied."
EOF
```

- [ ] **Step 2: Create the ArgoCD Application**

```bash
cat > infrastructure/argocd-oidc.yaml <<'EOF'
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: argocd-oidc
  namespace: deployment
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  project: dbt-openmetadata-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: deployment
  source:
    repoURL: "http://forgejo-http.deployment.svc.cluster.local:3000/stackable/openmetadata-dbt-demo.git"
    targetRevision: "main"
    path: infrastructure/argocd-oidc-manifests/
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    automated:
      selfHeal: true
      prune: true
EOF
```

- [ ] **Step 3: Validate**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/argocd-oidc.yaml')))" && echo "OK app"
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/argocd-oidc-manifests/configure-argocd-oidc.yaml')))" && echo "OK manifests"
```

Expected: both `OK ...` lines.

- [ ] **Step 4: Commit**

```bash
git add infrastructure/argocd-oidc.yaml infrastructure/argocd-oidc-manifests/
git commit -m "argocd: add OIDC patcher Application that wires Keycloak into argocd-cm"
```

---

## Task 9: Forgejo Tofu auth source extension

**Files:**
- Modify: `infrastructure/forgejo-manifests/configure-forgejo.yaml`

- [ ] **Step 1: Add `envFrom` to the Tofu Job that exposes `oidc-endpoints` keys as env vars**

In `infrastructure/forgejo-manifests/configure-forgejo.yaml`, find the configure-forgejo Job's container spec. Add or extend its `envFrom` to include the `oidc-endpoints` ConfigMap (keys are exposed as upper-snake env vars):

```yaml
          envFrom:
            - configMapRef:
                name: oidc-endpoints
                optional: true
```

(Use `optional: true` so the Job doesn't crash if the ConfigMap hasn't been created yet — it'll be on the next sync.)

This makes `ISSUER_URL` and `FORGEJO_REDIRECT_URI` available as env vars. ConfigMap keys with hyphens (`issuer-url`) get exposed with hyphens replaced by underscores in `envFrom`? **No — `envFrom: configMapRef` exposes keys verbatim, but env-var names with hyphens are illegal in shells.** For this to work cleanly, the discover-node-ip Job must use underscore keys. Adjust the discover-node-ip script in Task 2 / Task 7b to use:

```bash
                  --from-literal=ISSUER_URL="http://${NODE_IP}:30900/realms/stackable-demo" \
                  --from-literal=FORGEJO_REDIRECT_URI="..." \
                  ...
```

But that's a breaking change for the demo-landing container's `valueFrom: configMapKeyRef ...key=issuer-url`. Resolve by writing **both** key styles to the ConfigMap in discover-node-ip's script (extra `--from-literal` lines):

Add to the discover-node-ip Job's args (in addition to the existing hyphen-keys):

```bash
                  --from-literal=ISSUER_URL="http://${NODE_IP}:30900/realms/stackable-demo" \
                  --from-literal=FORGEJO_REDIRECT_URI="http://${NODE_IP}:30000/user/oauth2/keycloak/callback" \
                  --from-literal=ARGOCD_REDIRECT_URI="http://${NODE_IP}:30080/auth/callback" \
                  --from-literal=LANDING_REDIRECT_URI="http://${NODE_IP}:30088/oauth2/callback" \
```

Update the discover-node-ip Job in `infrastructure/keycloak-manifests/discover-node-ip.yaml` to include these lines for both namespaces.

- [ ] **Step 2: Add `forgejo_auth_source` resource to the Tofu HCL**

In the same file (`configure-forgejo.yaml`), find the ConfigMap's `data.forgejo.tf:` block. Append:

```hcl
    variable "issuer_url" {
      type = string
    }

    resource "forgejo_auth_source" "keycloak" {
      type            = "openid_connect"
      name            = "Keycloak"
      is_active       = true
      is_sync_enabled = true

      openid_connect_config {
        client_id          = "forgejo"
        client_secret      = "forgejo-secret"
        auto_discovery_url = "${var.issuer_url}/.well-known/openid-configuration"
        scopes             = "openid profile email roles"

        admin_groups       = "admin"
        group_claim_name   = "realm_access.roles"
        group_team_map     = ""

        skip_local_2fa     = false
      }
    }
```

Add `TF_VAR_issuer_url` env to the Job (if not already covered by the envFrom above — `envFrom` provides `ISSUER_URL`, not `TF_VAR_issuer_url`; add explicitly):

```yaml
            - name: TF_VAR_issuer_url
              value: $(ISSUER_URL)
```

(The `$(ISSUER_URL)` form expands at startup using env vars from the same container — works because envFrom's `ISSUER_URL` is exposed before this `env:` block resolves.)

**On provider availability:** if the `vied-fei/forgejo` provider's pinned version doesn't expose `forgejo_auth_source`, the implementer should:
1. Run `tofu providers schema -json | jq '.provider_schemas[].resource_schemas | keys'` from inside the Job pod (or locally with the provider) to confirm the resource name.
2. If the resource is named differently (e.g. `forgejo_oauth2_source`), substitute the correct name in the HCL.
3. As a last resort, replace the HCL block with a `null_resource` + `local-exec` that calls Forgejo's `/api/v1/admin/auth/oauth` REST endpoint via `curl` using the `FORGEJO_USERNAME`/`FORGEJO_PASSWORD` env vars already available to the Job.

- [ ] **Step 3: Validate**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/forgejo-manifests/configure-forgejo.yaml')))" && echo OK
python -c "
import yaml
docs = list(yaml.safe_load_all(open('infrastructure/forgejo-manifests/configure-forgejo.yaml')))
cm = next(d for d in docs if d and d.get('kind') == 'ConfigMap')
hcl = cm['data']['forgejo.tf']
for token in ['forgejo_auth_source', 'Keycloak', 'forgejo-secret', 'admin_groups',
              'realm_access.roles', 'auto_discovery_url']:
    assert token in hcl, f'missing token: {token}'
print('Forgejo HCL extension complete')
"
```

Expected: `OK` and `Forgejo HCL extension complete`.

- [ ] **Step 4: Commit**

```bash
git add infrastructure/forgejo-manifests/configure-forgejo.yaml \
        infrastructure/keycloak-manifests/discover-node-ip.yaml
git commit -m "forgejo: add Keycloak OIDC auth source via Tofu"
```

---

## Task 10: Register `argocd-oidc.yaml` in stack.yaml

**Files:**
- Modify: `infrastructure/stack.yaml`

- [ ] **Step 1: Append a `plainYaml:` entry**

Open `infrastructure/stack.yaml`. After the two Keycloak entries (`keycloak-operator.yaml` and `keycloak.yaml`) added in Phase 1, append:

```yaml
      - plainYaml: https://raw.githubusercontent.com/stackabletech/openmetadata-dbt-demo/refs/heads/main/infrastructure/argocd-oidc.yaml
```

- [ ] **Step 2: Validate**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/stack.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/stack.yaml
git commit -m "stack: register argocd-oidc Application"
```

---

## Task 11: Verify in-cluster

**Files:** none — verification only.

- [ ] **Step 1: Push branch + merge to main**

```bash
git push -u origin keycloak-oidc-clients
# inspect on Forgejo if desired, then:
git checkout main && git merge --ff-only keycloak-oidc-clients && git push origin main && git push in-cluster main
```

(For the in-cluster push, port-forward Forgejo first: `kubectl port-forward -n deployment svc/forgejo-http 3000:3000`.)

- [ ] **Step 2: Wait for ArgoCD reconcile, then verify the discovery + clients**

```bash
sleep 60
kubectl -n platform get cm oidc-endpoints -o jsonpath='{.data}' | jq .
# expect: a populated map with node-ip, issuer-url, etc.

kubectl -n deployment get cm oidc-endpoints -o jsonpath='{.data}' | jq .
# expect: identical content in deployment ns

kubectl -n platform logs job/configure-keycloak --tail=80 | grep -E "Apply complete|Resources:"
# expect: "Apply complete! Resources: <N> added/changed/destroyed"
# where the count includes the 3 new keycloak_openid_client resources + 3 default-scope mappings
```

- [ ] **Step 3: Verify the demo-landing pod is up with three containers**

```bash
kubectl -n deployment get pods -l app.kubernetes.io/name=demo-landing
kubectl -n deployment describe pod -l app.kubernetes.io/name=demo-landing | grep -E "Container|Image"
# expect: 3/3 Ready, containers landing/git-sync/oauth2-proxy
```

- [ ] **Step 4: Verify ArgoCD has been patched**

```bash
kubectl -n deployment get cm argocd-cm -o jsonpath='{.data.oidc\.config}'
# expect: a YAML block with issuer / clientID / clientSecret / requestedScopes

kubectl -n deployment get cm argocd-rbac-cm -o jsonpath='{.data.policy\.csv}'
# expect: 'g, admin, role:admin' and 'g, viewer, role:readonly'
```

- [ ] **Step 5: Verify Forgejo has the Keycloak auth source**

```bash
NODE_IP=$(kubectl -n platform get cm oidc-endpoints -o jsonpath='{.data.node-ip}')
curl -sku stackable:stackable "http://${NODE_IP}:30000/api/v1/admin/auth" | jq '.[].name'
# expect: includes "Keycloak"
```

- [ ] **Step 6: End-to-end browser smoke (manual)**

Open in a fresh browser session each:

1. `http://<NODE_IP>:30088/` — should redirect to Keycloak login. Log in as `demo-admin` (password from `kubectl -n platform get secret keycloak-demo-passwords -o jsonpath='{.data.demo_admin_password}' | base64 -d`). Land on the demo-landing page; toggles render.
2. Click a toggle — should succeed (admin role).
3. Open an incognito window. Repeat at `http://<NODE_IP>:30088/` and log in as `demo-user`. Page renders. `curl -X POST http://<NODE_IP>:30088/toggle -H "Cookie: <session>"` returns 403.
4. `http://<NODE_IP>:30080/auth/login` — click "Login via Keycloak" (the new SSO button on ArgoCD). Log in as `demo-admin`. Should land in ArgoCD with admin role. Repeat as `demo-user` — read-only navigation only.
5. `http://<NODE_IP>:30000/user/login/` — click "Sign in with Keycloak". Log in as `demo-admin`. Should auto-create a Forgejo account and grant site-admin. Repeat as `demo-user` — regular account, no site-admin.

- [ ] **Step 7: Verify break-glass paths still work**

1. Log out of all three. Visit each tool's local-login URL:
   - `http://<NODE_IP>:30080/auth/login` — log in as `admin / adminadmin` directly (skip OIDC).
   - `http://<NODE_IP>:30000/user/login/` — log in as `stackable / stackable`.
2. demo-landing has no break-glass — if Keycloak is down, the landing page is unreachable. Documented; acceptable for demo.

---

## Self-Review

**Spec coverage (cross-checked against `2026-04-26-keycloak-oidc-clients-design.md`):**

- §"Keycloak client provisioning" → Task 3.
- §"Node IP discovery Job" → Task 2 (+ Task 7b extension to write to both namespaces, + Task 9 step 1 extension to add upper-case keys for envFrom).
- §"demo-landing — oauth2-proxy sidecar" → Task 7.
- §"demo-landing — Rust app changes" → Task 5.
- §"ArgoCD OIDC" (configure-argocd-oidc Job + ArgoCD Application) → Task 8.
- §"Forgejo OIDC" (Tofu extension) → Task 9.
- §"Sync ordering" → wired via Task 8's sync-wave annotation; the rest is implicit.
- §"Verification" → Task 11.
- §"Authorization model" → covered: oauth2-proxy `--oidc-groups-claim=realm_access.roles` + `require_admin` middleware + ArgoCD `policy.csv` + Forgejo `admin_groups`.
- §"Out of scope" — not implemented (correct).

**Placeholder scan:** no `TBD` / `TODO` / `Similar to Task N` / `fill in details` tokens. The Forgejo provider-availability fallback in Task 9 Step 2 is operational guidance with concrete fallback steps, not a placeholder.

**Type / name consistency:**

- Client IDs: `demo-landing`, `argocd`, `forgejo` — used in Task 3 (Keycloak side), Task 7 (oauth2-proxy `--client-id`), Task 8 (`clientID: argocd`), Task 9 (`client_id = "forgejo"`).
- Client secrets: `demo-landing-secret`, `argocd-secret`, `forgejo-secret` — same files.
- ConfigMap name: `oidc-endpoints` — written by Task 2/7b, read by Task 7 (oauth2-proxy `valueFrom`), Task 8 (kubectl get), Task 9 (envFrom).
- Sealed Secret name: `oauth2-proxy-cookie-secret` — Task 4 creates it, Task 7 references it.
- Realm: `stackable-demo` — Phase 1 creates it; Phase 2 references it as part of issuer URL.
- Roles: `admin` / `viewer` — Phase 1 creates them; mapped consistently in oauth2-proxy (header-based `require_admin`), ArgoCD (`policy.csv`), Forgejo (`admin_groups`).
- LISTEN_ADDR: `127.0.0.1:8081` — both `main.rs` default (Task 5 step 4) and Deployment env (Task 7 step 1).
- Image tag: `0.2.0-dev` — both `justfile` (Task 6) and Deployment (Task 7 step 1).
- ConfigMap keys: hyphen-style (`issuer-url`, `landing-redirect-uri`) for `valueFrom: configMapKeyRef`, AND upper-snake (`ISSUER_URL`, `FORGEJO_REDIRECT_URI`) for `envFrom: configMapRef` — both styles written by the discovery Job.
