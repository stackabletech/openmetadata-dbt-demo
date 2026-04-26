# Keycloak OIDC for Platform Tools (Phase 2) — Design

## Goal

Wire OIDC against the `stackable-demo` Keycloak realm (deployed in Phase 1) into the three platform-deployment tools the user lands on first: the demo-landing page, ArgoCD, and Forgejo. After this phase a demo user logs into each of these tools as `demo-admin` or `demo-user` (the realm users created in Phase 1) — same credentials, single sign-on across all three.

## Scope

- **Demo-landing** — gain OIDC via an oauth2-proxy sidecar; existing HTTP basic-auth is removed.
- **ArgoCD** — gain OIDC via the chart's built-in `oidc.config`, patched in post-deploy by a Job.
- **Forgejo** — gain OIDC via a `forgejo_auth_source` resource in the existing `configure-forgejo` Tofu Job.

Everything else (Trino / Airflow / NiFi / Superset / OpenMetadata / LakeKeeper / Kafka / HDFS / NodePort fronts) is **out of scope** — those are sub-projects C and E in the original 5-part decomposition.

## Authorization model

Two-tier, mapping the existing realm roles `admin` and `viewer` (created in Phase 1):

| Tool | `admin` role gets | `viewer` role gets |
|---|---|---|
| demo-landing | full UI + `POST /toggle` write endpoints | read-only UI; toggle endpoints return 403 |
| ArgoCD | built-in `role:admin` | built-in `role:readonly` |
| Forgejo | site-admin flag set | regular user, can browse all public repos (the demo's repos are public) |

Each tool keeps its existing local admin (`adminadmin` for ArgoCD, `stackable` for Forgejo) as a break-glass account that bypasses OIDC.

## Architecture

```
                                    ┌──────────────────────────┐
                                    │ discover-node-ip Job     │
                                    │ (re-runs on every sync)  │
                                    └─────────────┬────────────┘
                                                  │ writes
                                                  ▼
                                    ┌──────────────────────────┐
                                    │ ConfigMap                │
                                    │ oidc-endpoints           │
                                    │  - node-ip               │
                                    │  - issuer-url            │
                                    │  - <tool>-redirect-uri   │
                                    └─────────────┬────────────┘
                                                  │ envFrom / read
              ┌─────────────────┬─────────────────┼─────────────────┬───────────────────┐
              ▼                 ▼                 ▼                 ▼                   ▼
       ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   ┌──────────────┐
       │configure-    │  │configure-    │  │configure-    │  │oauth2-proxy  │   │      ...     │
       │keycloak Job  │  │argocd-oidc   │  │forgejo Job   │  │sidecar       │   │              │
       │(extended)    │  │Job (new)     │  │(extended)    │  │(landing pod) │   │              │
       └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │              │
              │                 │                 │                 │           │              │
              ▼                 ▼                 ▼                 ▼           ▼              │
      Keycloak realm     argocd-cm /         Forgejo auth       OIDC gate                      │
      gets 3 OIDC        argocd-rbac-cm      source             in front of                    │
      clients            patched             added              Rust app                       │
                                                                                               ▼
                                                                                     stackable-demo
                                                                                     realm (existing)
```

**Single source of truth:** the `oidc-endpoints` ConfigMap. The discovery Job picks the first node's `ExternalIP` (falling back to `InternalIP`) and renders all derived URLs from it. Every consumer reads from it; no consumer hard-codes a node IP.

## Component-level design

### 1. Keycloak client provisioning (Tofu)

Extend the existing `keycloak.tf` ConfigMap inside `infrastructure/keycloak-manifests/configure-keycloak.yaml`. The ConfigMap currently mounts at `/config` of the `configure-keycloak` Job — keep that. Add three `keycloak_openid_client` resources to the HCL:

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
```

Plus a `keycloak_openid_client_default_scopes` per client that ensures the `roles` scope is present, so ID tokens carry `realm_access.roles`.

**On client secrets:** all three are literal strings (`<client>-secret`). This matches the existing demo convention of literal credentials (`postgres / postgres`, `admin / adminadmin`, `stackable / stackable`).

**On wildcard redirect URIs (`http://*/...`):** acceptable because tokens are scoped per-realm and audited via `aud`/`azp` claims, and because Keycloak still enforces the path. The wildcard solves NodePort's variable-host problem cleanly.

### 2. Node IP discovery Job

New file: `infrastructure/keycloak-manifests/discover-node-ip.yaml`

Contains:
- `ServiceAccount` `discover-node-ip` (in `platform`)
- `ClusterRole` granting `nodes: [list]`
- `ClusterRoleBinding`
- `Role` granting `configmaps: [get,create,update,patch]` in `platform`
- `RoleBinding`
- `Job` `discover-node-ip` annotated `argocd.argoproj.io/sync-options: Replace=true` so ArgoCD re-runs it every sync

Job script (uses `bitnami/kubectl:1.31` image, ~30 lines of bash):

```bash
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
if [ -z "$NODE_IP" ]; then
  NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
fi
echo "Discovered node IP: $NODE_IP"

kubectl create configmap oidc-endpoints \
  --namespace=platform \
  --from-literal=node-ip="$NODE_IP" \
  --from-literal=issuer-url="http://${NODE_IP}:30900/realms/stackable-demo" \
  --from-literal=argocd-redirect-uri="http://${NODE_IP}:30080/auth/callback" \
  --from-literal=forgejo-redirect-uri="http://${NODE_IP}:30000/user/oauth2/keycloak/callback" \
  --from-literal=landing-redirect-uri="http://${NODE_IP}:30088/oauth2/callback" \
  --dry-run=client -o yaml | kubectl apply -f -
```

The `--dry-run | apply` pattern handles both create and update.

### 3. demo-landing — oauth2-proxy sidecar

Modify `platform/manifests/demo-landing/deployment.yaml`. The current pod has 2 containers (`landing`, `git-sync`); add a third (`oauth2-proxy`) and rebind:

| Container | Listens on | Reachable from |
|---|---|---|
| `landing` (Rust) | `127.0.0.1:8081` | only oauth2-proxy in same pod |
| `git-sync` | n/a (no port) | n/a |
| `oauth2-proxy` (new) | `0.0.0.0:8080` | the existing Service |

The Service stays unchanged (still targets `:8080`).

**oauth2-proxy config** (env-driven; image `quay.io/oauth2-proxy/oauth2-proxy:v7.7.1`):

| Env var | Value |
|---|---|
| `OAUTH2_PROXY_PROVIDER` | `keycloak-oidc` |
| `OAUTH2_PROXY_OIDC_ISSUER_URL` | `valueFrom: configMapKeyRef name=oidc-endpoints key=issuer-url` |
| `OAUTH2_PROXY_REDIRECT_URL` | `valueFrom: configMapKeyRef name=oidc-endpoints key=landing-redirect-uri` |
| `OAUTH2_PROXY_CLIENT_ID` | `demo-landing` (literal) |
| `OAUTH2_PROXY_CLIENT_SECRET` | `demo-landing-secret` (literal) |
| `OAUTH2_PROXY_COOKIE_SECRET` | `valueFrom: secretKeyRef name=oauth2-proxy-cookie-secret key=cookie-secret` |
| `OAUTH2_PROXY_UPSTREAMS` | `http://127.0.0.1:8081/` |
| `OAUTH2_PROXY_HTTP_ADDRESS` | `0.0.0.0:8080` |
| `OAUTH2_PROXY_EMAIL_DOMAINS` | `*` |
| `OAUTH2_PROXY_PASS_USER_HEADERS` | `true` |
| `OAUTH2_PROXY_SET_XAUTHREQUEST` | `true` |
| `OAUTH2_PROXY_SKIP_AUTH_ROUTES` | `^/healthz` |
| `OAUTH2_PROXY_SCOPE` | `openid profile email roles` |
| `OAUTH2_PROXY_COOKIE_SECURE` | `false` (HTTP only — demo) |
| `OAUTH2_PROXY_ALLOWED_ROLES` | unset — anyone in the realm gets in |

**Annotation on the Deployment:** `checksum/oidc-endpoints: <hash>` recomputed by the discovery Job — actually no, simpler: drop this and rely on oauth2-proxy reading env-from-configmap at startup; the discovery Job's update won't restart pods automatically, but `kubectl rollout restart deployment/demo-landing` is part of the post-deploy verification.

The new sealed Secret is just one key, generated at implementation time:
```yaml
# secrets/manifests/demo-landing/oauth2-proxy-cookie-secret.yaml
type: Opaque
stringData:
  cookie-secret: "<32 random bytes, base64-encoded>"
```

### 4. demo-landing — Rust app changes

**Removed:**
- `demo-landing/src/auth.rs`'s HTTP basic-auth middleware.
- The `AUTH_USER` / `AUTH_PASSWORD` env vars from the Deployment.
- The `demo-landing-basic-auth` sealed Secret (deleted from `platform/manifests/demo-landing/` and `secrets/manifests/demo-landing/`).

**Added in `auth.rs`:** a small middleware that:
1. Reads `X-Forwarded-Email` / `X-Forwarded-User` headers (set by oauth2-proxy via `OAUTH2_PROXY_PASS_USER_HEADERS=true`) and exposes them in a request extension so `routes.rs` can log who toggled what.
2. Reads `X-Forwarded-Groups` (set via `OAUTH2_PROXY_SET_XAUTHREQUEST=true` — this header carries realm roles as a comma-separated list) and gates `POST /toggle` on `admin` membership; returns 403 otherwise.

Listen address rebinds: `LISTEN_ADDR=127.0.0.1:8081` (env var, set in Deployment).

### 5. ArgoCD OIDC

`infrastructure/argo-cd.yaml`'s Helm `valuesObject` is **not** modified — issuer URL is unknown at chart-render time.

New ArgoCD Application `infrastructure/argocd-oidc.yaml`:
- Sources `infrastructure/argocd-oidc-manifests/` from Forgejo
- Same project / namespace / sync policy as the Keycloak Application
- Sync-wave `1` (later than default `0`, so ArgoCD itself is up before the patch Job runs)

Manifests in `infrastructure/argocd-oidc-manifests/`:

1. **`configure-argocd-oidc.yaml`** — `Job` + `ServiceAccount` + `Role` (`configmaps: [get,patch]` in `deployment` ns) + `RoleBinding`.

   The Job (image `bitnami/kubectl:1.31`, annotated `Replace=true`) reads `oidc-endpoints` and patches `argocd-cm`:

   ```bash
   ISSUER_URL=$(kubectl get cm -n platform oidc-endpoints -o jsonpath='{.data.issuer-url}')
   REDIRECT_URI=$(kubectl get cm -n platform oidc-endpoints -o jsonpath='{.data.argocd-redirect-uri}')
   URL=$(dirname "$REDIRECT_URI" | sed 's|/auth$||')   # http://<ip>:30080

   kubectl patch configmap argocd-cm -n deployment --type=merge --patch "$(cat <<EOF
   data:
     url: "${URL}"
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
   EOF
   )"

   kubectl patch configmap argocd-rbac-cm -n deployment --type=merge --patch '{
     "data": {
       "policy.default": "",
       "policy.csv": "g, admin, role:admin\ng, viewer, role:readonly\n"
     }
   }'
   ```

   ArgoCD's server picks up `argocd-cm` / `argocd-rbac-cm` changes on its watch loop within seconds.

2. **No additional Service or Deployment** — ArgoCD's existing pods do all the work.

### 6. Forgejo OIDC

Extend `infrastructure/forgejo-manifests/configure-forgejo.yaml`'s ConfigMap (the inline Tofu file) with one resource:

```hcl
resource "forgejo_auth_source" "keycloak" {
  type           = "openid_connect"
  name           = "Keycloak"
  is_active      = true
  is_sync_enabled = true

  openid_connect_config {
    client_id          = "forgejo"
    client_secret      = "forgejo-secret"
    auto_discovery_url = "${var.issuer_url}/.well-known/openid-configuration"
    skip_local_2fa     = false
    scopes             = "openid profile email roles"

    admin_groups       = "admin"  # realm role 'admin' -> Forgejo site admin
    group_claim_name   = "realm_access.roles"
    group_team_map     = ""
  }
}
```

Add `var "issuer_url" { type = string }` to the HCL and a corresponding `TF_VAR_issuer_url` env var on the Job (sourced via `valueFrom: configMapKeyRef name=oidc-endpoints key=issuer-url`).

The Forgejo Tofu provider (already in the Job's `terraform.required_providers`) is `vied-fei/forgejo ~> 1.0` — verify at implementation time the `forgejo_auth_source` resource exists in the version pinned and bump if needed. **Open question for implementer:** if the provider doesn't expose this resource cleanly, fall back to a `null_resource` + `local-exec` `curl` call to Forgejo's `/api/v1/admin/auth/oauth` endpoint.

### 7. Sync ordering

ArgoCD `sync-wave` annotations on the Applications:

| Application | Wave |
|---|---|
| `keycloak-operator` | `-1` (existing) |
| `keycloak` | `0` (default, existing) |
| `argocd-oidc` (new) | `1` |
| `discover-node-ip` Job (inside `keycloak`) | `0` (default; runs as part of keycloak Application) |

The discovery Job runs first (as part of the keycloak Application), populates `oidc-endpoints`. The `configure-keycloak` and `configure-argocd-oidc` Jobs both read from it. The `configure-forgejo` Job (inside the existing `forgejo` Application) is automatically re-run on every Forgejo sync due to the existing `Replace=true` annotation; it now reads `oidc-endpoints` too.

Race condition: if `configure-keycloak` runs before `discover-node-ip`, the Tofu wouldn't know the redirect URIs. **Solved** by the Tofu `valid_redirect_uris` using wildcards (`http://*/...`) — Tofu doesn't need to know the node IP.

## Verification (post-deploy)

1. `kubectl -n platform get cm oidc-endpoints -o yaml` shows a populated node IP.
2. `kubectl -n deployment get cm argocd-cm -o jsonpath='{.data.oidc\.config}'` shows the Keycloak config.
3. Browser:
    - `http://<node-ip>:30088/` → redirects to Keycloak login → log in as `demo-admin` → land on the demo-landing UI; toggles enabled.
    - Log in as `demo-user` → toggles greyed; `POST /toggle` returns 403.
    - `http://<node-ip>:30080/auth/login` → "Login via Keycloak" → log in as `demo-admin` → ArgoCD as full admin.
    - `http://<node-ip>:30000/user/login/` → "Sign in with Keycloak" → log in as `demo-admin` → Forgejo with site-admin flag.

## Out of scope

- Stackable products (Trino / NiFi / Superset / Airflow / Kafka): sub-project C.
- OpenMetadata / LakeKeeper: sub-project E.
- HTTPS / TLS: demo runs HTTP everywhere; cookies are non-secure.
- Federated logout (logging out of Keycloak when logging out of any tool): standard local logout suffices.
- Refresh token tuning, custom token lifetimes.
- Splitting frontchannel/backchannel issuer URLs in Keycloak — `hostname.strict: false` + dynamic backchannel handles the demo case.
- ArgoCD CLI (`argocd login`) — we list `localhost:8085` as a redirect URI but don't validate the CLI flow as part of phase 2.
