# Landing Page User Indicator and Logout — Design

## Goal

Show the logged-in user's username in the top right of the demo-landing
header, with a logout link that fully terminates the Keycloak SSO session
(not just the local oauth2-proxy cookie).

## Why

Today nothing on the landing page indicates *who* you are logged in as,
and there is no way to log out. For demos this matters because the same
browser is often used to switch between `demo-admin` and `demo-user` to
illustrate role-gated behavior, and oauth2-proxy's SSO silently re-uses
the existing Keycloak session.

A "log out" button that only clears the oauth2-proxy cookie would be
worse than nothing — Keycloak would silently re-authenticate on the next
request, making the button look broken. So logout must be RP-initiated:
clear the proxy cookie *and* end the Keycloak SSO session, then return
to the landing page.

## Non-goals

- No avatar, dropdown menu, or per-user personalization.
- No id_token_hint plumbing. We rely on `client_id` (sufficient for
  Keycloak ≥ 18) and avoid having to stash tokens inside demo-landing.
- No login button. oauth2-proxy already redirects unauthenticated
  visitors to Keycloak; an explicit "log in" affordance is unnecessary.

## Architecture

Three pieces, all in this repository.

### 1. `demo-landing` (Rust)

- A per-request middleware (alongside `auth.rs::require_admin`) reads
  `X-Forwarded-Preferred-Username` and stores it in a request extension
  for the page handlers to pull into the Tera context. Falls back to an
  empty string if the header is missing — defensive only; in production
  every request passes through oauth2-proxy.
- Renderer adds two values to the Tera context for every templated page:
  - `current_user`: the username string (e.g. `demo-admin`).
  - `logout_url`: the full URL the logout link should target — i.e.
    `/oauth2/sign_out?rd=<pre-encoded keycloak end-session URL>`.
- `assets/layout.html` — extend `site-header` with a right-aligned block
  containing the username and a "log out" link. The link's `href` is
  `{{ logout_url }}`. The block is rendered iff `current_user` is
  non-empty; the logout link inside it is rendered iff `logout_url` is
  also non-empty.
- `assets/styles.css` — switch `site-header` to flex with
  `justify-content: space-between`, add a `.user-block` rule with muted
  username and link-style logout. No new fonts or colors.

### 2. `discover-node-ip` Job

The Job already writes the OIDC-related URLs into the `oidc-endpoints`
ConfigMap (in both `platform` and `deployment` namespaces). Two new
keys join the existing set:

- `keycloak-host` — `<NODE_IP>:30900`. Consumed by oauth2-proxy as
  `OAUTH2_PROXY_WHITELIST_DOMAINS`. Without it, oauth2-proxy refuses
  external `rd=` redirects.
- `landing-logout-rd` — the fully URL-encoded value of the `rd`
  parameter:
  `<issuer>/protocol/openid-connect/logout?post_logout_redirect_uri=<landing-base>&client_id=demo-landing`.
  Pre-building this in the Job mirrors how `landing-redirect-uri` is
  already constructed there — keeps URL plumbing in one place.

Upper-snake aliases (`KEYCLOAK_HOST`, `LANDING_LOGOUT_RD`) are added in
the same shape as the existing aliases so envFrom-style consumers can
pick them up if needed.

### 3. `demo-landing` Deployment

- oauth2-proxy sidecar gets `OAUTH2_PROXY_WHITELIST_DOMAINS` from
  `oidc-endpoints/keycloak-host`.
- landing container gets `LOGOUT_RD_URL` from
  `oidc-endpoints/landing-logout-rd`. Read once at startup; stored in
  app state; injected into the Tera context for every render.

### 4. Keycloak client config

The `demo-landing` client (created by `configure-keycloak`) needs
`validPostLogoutRedirectUris: ["*"]` (matching the existing
`validRedirectUris: ["*"]`). Otherwise Keycloak rejects
`post_logout_redirect_uri` and shows its own error page instead of
returning to the landing.

## Data flow

```
Browser request
  → oauth2-proxy (validates cookie, sets X-Forwarded-Preferred-Username)
  → demo-landing (extractor reads header → Tera context)
  → response with header showing  "demo-admin · log out"

Click "log out"
  → /oauth2/sign_out?rd=<encoded keycloak logout URL>
  → oauth2-proxy clears cookie, 302 to Keycloak end_session
  → Keycloak terminates SSO session, 302 back to landing base URL
  → next request hits oauth2-proxy with no cookie, redirected to Keycloak login
```

## Error handling

- `X-Forwarded-Preferred-Username` missing → render the header without
  the user block. No error.
- `LOGOUT_RD_URL` env var missing at startup → log warn and render the
  user block without a logout link. The page still works; we don't crash
  the container if the ConfigMap key happens to be absent.
- ConfigMap key not yet populated when demo-landing starts (race with
  discover-node-ip Job) → handled by Kubernetes' env-from-configmap
  semantics: pod fails to start until the key exists. Acceptable; same
  behavior as the existing `OAUTH2_PROXY_OIDC_ISSUER_URL` wiring.

## Testing

- Unit tests for the extractor: header present, header missing,
  whitespace handling.
- Unit test for the Tera context: `current_user` and `logout_url` are
  exposed.
- Manual smoke: log in as `demo-admin`, see username in header, click
  log out, verify Keycloak login form appears (proves SSO session
  ended), log in as `demo-user`, verify username updates.

## Out of scope / deferred

- Showing the user's role(s) next to the name.
- Welcome banner on the landing page (`Hello, demo-admin`).
- Per-user customization of the page content.
- Logout-everywhere semantics for ArgoCD / Forgejo browser sessions —
  ending the Keycloak SSO session means *new* requests to those apps
  will require re-auth, but existing app-level cookies (ArgoCD JWT,
  Forgejo session) are unaffected. Out of scope for this change.