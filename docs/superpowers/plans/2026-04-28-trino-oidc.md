# Trino OIDC SSO — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Trino web UI to Keycloak so the existing `demo-admin` / `demo-user` users can log in with the same credentials they use elsewhere.

**Architecture:** A new `AuthenticationClass: trino-keycloak` (Stackable OIDC provider, pointing at `<NODE_IP>:30900`) plus a `trino-keycloak-client` Secret holding the OIDC client credentials, both created by the shared `configure-stackable-oidc` Job. `TrinoCluster.spec.clusterConfig.authentication` is updated to reference the class. Internal RBAC (admin vs viewer at the SQL layer) is out of scope — all authenticated users get equal query rights for now; OPA wires that in a future plan.

**Tech Stack:** Stackable Trino operator, Stackable AuthenticationClass v1alpha1, Keycloak OIDC.

**Spec reference:** `docs/superpowers/specs/2026-04-28-stackable-stack-oidc-design.md` § "Per-product specifics → Trino".

**Depends on:** `2026-04-28-stack-oidc-prerequisites.md` (the `trino` Keycloak client must exist; the shared Job manifest must be in place).

---

## File structure

| File | Change |
| --- | --- |
| `platform/manifests/configure-stackable-oidc/job.yaml` | Add a `kubectl apply -f -` heredoc for `trino-keycloak` AuthenticationClass + `trino-keycloak-client` Secret immediately after the `# === Per-product blocks below ===` marker. |
| `platform/manifests/trino/trino.yaml` | Add `spec.clusterConfig.authentication` referencing `trino-keycloak`. |
| `website/index.md` | Update the Trino row of the Stackable Components table: drop the `admin` / `(none)` columns, replace with `demo-admin` / `demo-user` and the keycloak-demo-passwords reference. |

---

## Task 1: Add Trino AuthenticationClass + Secret to the shared Job

**Files:**
- Modify: `platform/manifests/configure-stackable-oidc/job.yaml`

The shared Job currently contains a comment marker (`# === Per-product blocks below ===`) followed by the placeholder echo. Replace the placeholder with a `kubectl apply -f -` heredoc that creates two resources: the AuthenticationClass and the credentials Secret.

- [ ] **Step 1: Insert the Trino apply block**

In `platform/manifests/configure-stackable-oidc/job.yaml`, locate the block:

```sh
              # === Per-product blocks below ===
              # Each per-product OIDC plan inserts its
              # AuthenticationClass + Secret apply block here.
              # See docs/superpowers/specs/2026-04-28-stackable-stack-oidc-design.md.

              echo "configure-stackable-oidc: no per-product blocks yet."
```

Replace the `echo "configure-stackable-oidc: no per-product blocks yet."` line with:

```sh
              echo "Applying Trino AuthenticationClass + client Secret..."
              kubectl apply -f - <<EOF
              ---
              apiVersion: authentication.stackable.tech/v1alpha1
              kind: AuthenticationClass
              metadata:
                name: trino-keycloak
              spec:
                provider:
                  oidc:
                    hostname: ${KEYCLOAK_HOSTNAME}
                    port: ${KEYCLOAK_PORT}
                    rootPath: /realms/stackable-demo
                    principalClaim: preferred_username
                    scopes:
                      - openid
                      - email
                      - profile
                    providerHint: Keycloak
              ---
              apiVersion: v1
              kind: Secret
              metadata:
                name: trino-keycloak-client
                namespace: platform
              type: Opaque
              stringData:
                clientId: trino
                clientSecret: trino-secret
              EOF
```

- [ ] **Step 2: Verify YAML still parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/configure-stackable-oidc/job.yaml')))"`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add platform/manifests/configure-stackable-oidc/job.yaml
git commit -m "trino-oidc: extend configure-stackable-oidc with Trino AuthenticationClass + Secret"
```

---

## Task 2: Wire TrinoCluster to the AuthenticationClass

**Files:**
- Modify: `platform/manifests/trino/trino.yaml`

The current `clusterConfig` block has `catalogLabelSelector` and `vectorAggregatorConfigMapName`. Add an `authentication` field referencing the new class.

- [ ] **Step 1: Add the `authentication` field**

In `platform/manifests/trino/trino.yaml`, locate the `clusterConfig:` block (lines 12-16):

```yaml
  clusterConfig:
    catalogLabelSelector:
      matchLabels:
        trino: trino
    vectorAggregatorConfigMapName: vector-aggregator-discovery
```

Add a new `authentication` field after `vectorAggregatorConfigMapName`:

```yaml
  clusterConfig:
    catalogLabelSelector:
      matchLabels:
        trino: trino
    vectorAggregatorConfigMapName: vector-aggregator-discovery
    authentication:
      - authenticationClass: trino-keycloak
        oidc:
          clientCredentialsSecret: trino-keycloak-client
```

- [ ] **Step 2: Verify YAML still parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/trino/trino.yaml')))"`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add platform/manifests/trino/trino.yaml
git commit -m "trino-oidc: wire TrinoCluster to trino-keycloak AuthenticationClass"
```

---

## Task 3: Update landing page Trino row

**Files:**
- Modify: `website/index.md`

The Trino row currently lists `admin` / `*(none)*` as credentials. Update to reflect SSO.

- [ ] **Step 1: Update the row**

In `website/index.md`, locate the Trino row in the Stackable Components table:

```
| **Trino** | [https://{{ nodeport "trino-coordinator" }}/](https://{{ nodeport "trino-coordinator" }}/) | `admin` | *(none)* | {{ toggle "platform/manifests/trino/trino.yaml" "spec.clusterOperation.stopped" }}           |
```

Change to:

```
| **Trino** | [https://{{ nodeport "trino-coordinator" }}/](https://{{ nodeport "trino-coordinator" }}/) | `demo-admin` / `demo-user` | *(see keycloak-demo-passwords secret)* | {{ toggle "platform/manifests/trino/trino.yaml" "spec.clusterOperation.stopped" }}           |
```

- [ ] **Step 2: Commit**

```bash
git add website/index.md
git commit -m "trino-oidc: update landing page Trino credentials to SSO"
```

---

## Manual verification (after deploy)

Once the shared Job has run and TrinoCluster has reconciled:

1. Open `https://<NODE_IP>:<trino-coordinator-nodeport>/` in an incognito window.
2. Trino UI redirects to Keycloak. (Or, with active SSO session from the landing page, redirects silently.)
3. Log in as `demo-admin` → land on Trino's UI logged in as `demo-admin`.
4. Log out (Trino's logout link in the top-right), log back in as `demo-user` → land logged in as `demo-user`.
5. Run a SQL query (`SELECT 1`) as either user — should succeed; no internal Trino RBAC differentiates them yet.

Diagnostic if the Trino UI rejects the login:

- `kubectl -n platform logs trino-coordinator-default-0 -c trino --tail=200 | grep -iE "oidc|jwt|claim|forbidden"` — Trino logs the OIDC dance and any token validation failure.
- `kubectl -n platform get authenticationclass trino-keycloak -o yaml` — confirm the class exists and points at the discovered NodeIP.
- `kubectl -n platform get secret trino-keycloak-client -o jsonpath='{.data.clientId}' | base64 -d` — confirm the credentials Secret has `clientId: trino`.
