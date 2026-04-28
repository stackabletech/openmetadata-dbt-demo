# Stackable Stack OIDC SSO — Design

## Goal

Wire Trino, Airflow, NiFi, Superset, and OpenMetadata to the existing
Keycloak realm so the demo's two users (`demo-admin`, `demo-user`) log
in once and access every tool through OIDC. The Keycloak `admin` realm
role maps to each tool's highest-privilege internal role, `viewer` to
each tool's read-only equivalent. From the landing page, clicking any
tool either auto-redirects (when the product's UI does so for
unauthenticated requests) or shows a single "Sign in with Keycloak"
button — never a local username/password form.

## Why

The landing page is the front door to the demo. Every existing
non-SSO local login (`admin/admin` on NiFi, `admin/admin` on Airflow,
etc.) is a credibility hit when the rest of the demo is OIDC-driven.
Centralising on Keycloak also lets a future expansion add the e2e
security demo's group hierarchy and OPA-based authorization without
re-plumbing the per-product auth path.

## Non-goals (deferred)

- **Group hierarchy and named user groups.** The e2e-security demo
  organises users into Compliance/CustomerService/Marketing groups
  with role inheritance. We're keeping the simple two-user model;
  group hierarchy is the next expansion.
- **OPA-based authorization** (Trino column/row policies, HDFS
  regorules, Hive metastore rules). The marquee Stackable security
  feature, but heavyweight; addressed separately.
- **Kerberos** for HDFS / Hive metastore.
- **OpenSearch Dashboards.** Stackable's OpenSearch operator supports
  OIDC via the Security plugin, but the configuration surface is
  fiddly and OpenSearch Dashboards is a side dish in this demo.
- **JDBC / programmatic authentication into Trino.** dbt, Superset,
  and any external SQL clients keep their current credential-based
  paths into Trino. Only the Trino web UI gets OIDC.
- **Existing local admin secrets.** `airflow-credentials`,
  `simple-superset-credentials` etc. stay in place. They don't
  conflict with OIDC and removing them is a follow-up cleanup.

## Architecture

The work splits into four cross-cutting changes (one place each) and
five per-product changes (one place each).

### Cross-cutting

1. **Keycloak realm config.** `infrastructure/keycloak-manifests/configure-keycloak.yaml`
   gets five new `keycloak_openid_client` resources: `trino`, `airflow`,
   `nifi`, `superset`, `openmetadata`. Each:
   - `access_type = "CONFIDENTIAL"`, `standard_flow_enabled = true`.
   - Hard-coded client secret (matches the existing demo-pattern of
     `<client>-secret`).
   - `valid_redirect_uris = ["*"]` and `valid_post_logout_redirect_uris = ["*"]`
     (matches existing demo clients; demo cluster only).
   - Default scopes `[profile, email, roles, web-origins]`.
   - Audience protocol mapper — `included_client_audience = "<client_id>"`,
     added to id and access tokens — same shape as the existing
     demo-landing/argocd/forgejo audience mappers (commit `a9f482d`).
   - Re-applied via the existing realm-wipe + Job re-run workflow.

2. **discover-node-ip ConfigMap keys.** `infrastructure/keycloak-manifests/discover-node-ip.yaml`
   adds five lower-snake keys (and their upper-snake aliases):
   - `trino-redirect-uri = https://${NODE_IP}:<trino-coordinator-nodeport>/oauth2/callback`
   - `airflow-redirect-uri = http://${NODE_IP}:<airflow-webserver-nodeport>/auth/callback`
   - `nifi-redirect-uri = https://${NODE_IP}:<nifi-nodeport>/nifi-api/access/oidc/callback`
   - `superset-redirect-uri = http://${NODE_IP}:<superset-nodeport>/oauth-authorized/keycloak`
   - `openmetadata-redirect-uri = http://${NODE_IP}:<openmetadata-nodeport>/callback`

   The actual NodePort numbers come from each product's existing
   nodeport Service or operator-managed Listener. Each per-product
   plan reads the live port and bakes it into the discover-node-ip
   patch in that plan's commit.

3. **Shared `configure-stackable-oidc` Job.** New manifest under
   `platform/manifests/configure-stackable-oidc/`. It runs after
   `discover-node-ip` populates the ConfigMap, and creates one
   `AuthenticationClass` plus one `Secret` per Stackable-managed
   product (Trino, Airflow, NiFi, Superset). The Job uses
   `kubectl apply -f -` heredocs so each per-product plan can extend
   the script with another `apply` block. OpenMetadata is *not* in
   this Job — see point 4.

4. **OpenMetadata env-var wiring.** OpenMetadata's existing ArgoCD
   Application (`platform/applications/openmetadata.yaml`) is a Helm
   source with a `valuesObject`. It gets new `valuesObject.global.authConfig`
   block plus `valuesObject.openmetadata.config.authentication` and
   `valuesObject.openmetadata.config.authorizer` blocks that source
   `clientId`, `callbackUrl`, `publicKeyUrls`, and `authority` from
   the same `oidc-endpoints` ConfigMap. OpenMetadata's pods read
   these via env-from-configmap-keyref injected through the chart's
   `extraEnvs` value.

### Per-product

Each Stackable-managed product gets:

a) An `AuthenticationClass` of provider `oidc`, named `<product>-keycloak`,
   pointing to `<NODE_IP>:30900` and the realm path
   `/realms/stackable-demo`. Created by the shared Job (point 3
   above) so the actual `<NODE_IP>` is the discovered one.

b) A `Secret` named `<product>-keycloak-client` with two keys:
   `clientId` and `clientSecret`. Hard-coded values matching the
   Keycloak Tofu config (this is a demo cluster).

c) A small change to the product's CR (`TrinoCluster`,
   `AirflowCluster`, `NifiCluster`, `SupersetCluster`) adding
   `spec.clusterConfig.authentication` referencing the class.

d) Per-product role-mapping config baked into the CR's
   `configOverrides` / `envOverrides` where Stackable's class doesn't
   express it directly (Airflow + Superset both need
   `AUTH_ROLES_MAPPING`).

OpenMetadata is the outlier: it's a Helm chart, not Stackable-managed,
so the wiring lives in the Helm values rather than in CRD fields.

### Landing page

`website/index.md` updated to:

- Drop per-product local credentials columns (where SSO replaces them).
- Replace user/password values with `demo-admin` / `demo-user`.
- Where the product's UI auto-redirects unauthenticated visits to
  OIDC (Trino, NiFi, OpenMetadata): URL stays as the product home
  page.
- Where the product needs an explicit "trigger OIDC" path (Airflow,
  Superset): URL points to that path so the click goes straight to
  Keycloak.

## Per-product specifics

### Trino

- `AuthenticationClass: trino-keycloak` (provider: oidc) + Secret
  `trino-keycloak-client` (clientId `trino`, clientSecret
  `trino-secret`).
- `TrinoCluster.spec.clusterConfig.authentication: [{authenticationClass: trino-keycloak}]`
  added.
- All authenticated users get equal query rights at the Trino access-
  control layer. Internal RBAC (admin vs viewer at the SQL level) is
  out of scope — picked up in the future OPA expansion. For now,
  authentication-only.
- JDBC/programmatic clients (dbt, Superset → Trino datasource) keep
  their existing credential paths via the existing Trino password
  authenticator.
- Web UI redirect URI: `https://<NODE_IP>:<coordinator-nodeport>/oauth2/callback`.

### Airflow

- `AuthenticationClass: airflow-keycloak` + Secret `airflow-keycloak-client`
  (clientId `airflow`, clientSecret `airflow-secret`).
- `AirflowCluster.spec.clusterConfig.authentication: [{authenticationClass: airflow-keycloak, userRegistrationRole: Public, syncRolesAt: registration}]`.
- `AUTH_ROLES_MAPPING` in `envOverrides` (or `configOverrides` —
  Airflow operator supports both):
  ```python
  AUTH_ROLES_MAPPING = {"admin": ["Admin"], "viewer": ["Viewer"]}
  ```
- Existing `airflow-credentials` Secret stays.
- Redirect URI: `http://<NODE_IP>:<webserver-nodeport>/auth/callback`.
- Landing page link points to `/login/keycloak` (FAB's OIDC trigger
  path).

### NiFi

- `AuthenticationClass: nifi-keycloak` (replaces `simple-nifi-users`)
  + Secret `nifi-keycloak-client` (clientId `nifi`, clientSecret
  `nifi-secret`).
- `NifiCluster.spec.clusterConfig.authentication: [{authenticationClass: nifi-keycloak}]`.
- Initial admin identity: `demo-admin` (matches Keycloak's
  `preferred_username` claim, which the Stackable NiFi operator's
  authorizer reads). Post-init policy edits happen as `demo-admin` in
  the NiFi UI.
- `simple-nifi-users` AuthenticationClass and `nifi-admin-credentials`
  Secret deleted as part of the NiFi plan's commit.
- NiFi auto-redirects unauthenticated visits to OIDC, so landing page
  URL stays as the home page.
- Redirect URI: `https://<NODE_IP>:<nifi-nodeport>/nifi-api/access/oidc/callback`.

### Superset

- `AuthenticationClass: superset-keycloak` + Secret `superset-keycloak-client`
  (clientId `superset`, clientSecret `superset-secret`).
- `SupersetCluster.spec.clusterConfig.authentication: [{authenticationClass: superset-keycloak, userRegistrationRole: Gamma}]`.
- `AUTH_ROLES_MAPPING` injected via the operator's
  `configOverrides.superset_config.py`:
  ```python
  AUTH_ROLES_MAPPING = {"admin": ["Admin"], "viewer": ["Gamma"]}
  AUTH_ROLES_SYNC_AT_LOGIN = True
  ```
- Existing `simple-superset-credentials` Secret stays.
- Redirect URI: `http://<NODE_IP>:<superset-nodeport>/oauth-authorized/keycloak`.
- Landing page link points to `/login/keycloak/?next=/superset/welcome/`.

### OpenMetadata

- Configured entirely via the Helm chart's `valuesObject` in
  `platform/applications/openmetadata.yaml`, sourcing the dynamic
  values from `oidc-endpoints` ConfigMap via env-from-configMapKeyRef
  (chart's `extraEnvs` value).
- Static values:
  - `AUTHENTICATION_PROVIDER = custom-oidc`
  - `AUTHENTICATION_PUBLIC_KEY_URLS = ["${ISSUER_URL}/protocol/openid-connect/certs"]`
  - `AUTHENTICATION_AUTHORITY = ${ISSUER_URL}`
  - `AUTHENTICATION_CLIENT_ID = openmetadata`
  - `AUTHENTICATION_CALLBACK_URL = ${OPENMETADATA_REDIRECT_URI}`
  - `AUTHORIZER_CLASS_NAME = org.openmetadata.service.security.DefaultAuthorizer`
  - `AUTHORIZER_PRINCIPAL_DOMAIN = stackable.demo`
  - `AUTHORIZER_ADMIN_PRINCIPALS = ["demo-admin"]`
- OpenMetadata reads these at startup; pod restart picks up
  ConfigMap changes only on next deploy/rollout. Acceptable for the
  demo.

## Bootstrap and rollout

**One-time prerequisites** (single commit, prepares the rails):

1. Add the five `keycloak_openid_client` Tofu resources.
2. Add the five redirect-URI keys to `discover-node-ip.yaml`.
3. Scaffold an empty `configure-stackable-oidc` Job manifest (the
   shared script) and its ArgoCD Application.

After this commit lands, the realm needs to be wiped and re-applied
once (per the existing
`feedback_keycloak_tofu_design.md` workflow) so the new clients exist
in Keycloak. discover-node-ip Job needs deleting so the ConfigMap
gets the new keys.

**Per-product** (five plans, each a self-contained increment):

1. **Trino** — quickest, lowest risk. Add AuthenticationClass +
   Secret to the shared Job, update TrinoCluster spec, update
   landing page link.
2. **Airflow** — same shape, plus `AUTH_ROLES_MAPPING` overlay.
3. **Superset** — same shape, plus `AUTH_ROLES_MAPPING` overlay.
4. **OpenMetadata** — Helm valuesObject change in the existing
   Application; not part of the shared Job.
5. **NiFi last** — riskiest because it *removes* the existing static
   auth path. Done last so a stuck integration doesn't break a live
   demo's NiFi.

## Data flow (per product, common shape)

```
Browser → product UI (e.g. https://NODE_IP:30443/)
  → product detects no session → 302 to Keycloak
    /realms/stackable-demo/protocol/openid-connect/auth
    ?client_id=<product>&redirect_uri=<product>/callback&...
  → Keycloak login (or silent SSO if already authenticated)
  → 302 to <product>/callback?code=...&state=...
  → product exchanges code for token (backchannel to issuer)
  → product reads claims, applies role mapping, sets its session
  → user sees the product UI logged in
```

For OpenMetadata the backchannel verification uses
`AUTHENTICATION_PUBLIC_KEY_URLS` (JWKS) — the issuer URL doesn't need
to be reachable from OpenMetadata pods, only the JWKS endpoint does.

## Failure modes

- **Keycloak issuer in token doesn't match the URL the validating
  product uses for discovery.** Mitigation: every product is
  configured with `<NODE_IP>:30900` as the issuer URL, matching what
  Keycloak emits when reached on its NodePort. The discover-node-ip
  Job ensures `<NODE_IP>` is the user-pool ExternalIP (fix from
  commit `60fd018`).
- **Stale OIDC config in product after Keycloak realm wipe.** Same
  issue we hit with ArgoCD/Forgejo earlier this branch. Mitigation:
  the shared `configure-stackable-oidc` Job has
  `argocd.argoproj.io/sync-options: Replace=true`, so an ArgoCD sync
  recreates it after a realm wipe.
- **NiFi initial admin identity mismatch.** If the operator
  bootstraps NiFi authorizer files before the AuthenticationClass is
  ready, NiFi may have no admin and no way to recover except wipe.
  Mitigation: per the operator's docs, the AuthenticationClass must
  exist before NifiCluster is reconciled. The shared Job runs first
  (different ArgoCD Application syncWave / bootstrap order).

## Verification (per product, in plans)

1. Open product URL in incognito.
2. Browser redirects to Keycloak (or silent SSO).
3. Log in as `demo-admin` → land in the product with admin role.
4. Log out at the product (or via demo-landing's logout link, which
   ends the SSO session).
5. Log in as `demo-user` → land in the product with read-only role.
6. (Where applicable) try an admin-only action as `demo-user` →
   should be forbidden by the product's internal RBAC.

## Decomposition

This spec produces **six implementation plans** in writing-plans:

1. `2026-04-28-stack-oidc-prerequisites.md` — Keycloak Tofu clients,
   discover-node-ip keys, scaffolded shared Job.
2. `2026-04-28-trino-oidc.md` — Trino integration.
3. `2026-04-28-airflow-oidc.md` — Airflow integration.
4. `2026-04-28-superset-oidc.md` — Superset integration.
5. `2026-04-28-openmetadata-oidc.md` — OpenMetadata integration.
6. `2026-04-28-nifi-oidc.md` — NiFi integration (replaces static
   auth).

Each plan after the first depends on the prerequisites plan having
landed (its Keycloak client must exist, its redirect URI must be in
the ConfigMap). The five product plans are otherwise independent and
can be implemented in any order — the suggested order is
risk-ascending.
