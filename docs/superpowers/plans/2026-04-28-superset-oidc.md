# Superset OIDC SSO — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Superset to Keycloak so `demo-admin` and `demo-user` log in via SSO. Map the Keycloak `admin` realm role to FAB's `Admin`, the `viewer` role to FAB's `Gamma` (read-only).

**Architecture:** New `AuthenticationClass: superset-keycloak` + `superset-keycloak-client` Secret created by the shared `configure-stackable-oidc` Job. `SupersetCluster.spec.clusterConfig.authentication` references the class with `userRegistrationRole: Gamma` (default for users without explicit admin role). `AUTH_ROLES_MAPPING` injected via the operator's `configOverrides.superset_config.py`.

**Tech Stack:** Stackable Superset operator, Flask-AppBuilder OIDC, Stackable AuthenticationClass v1alpha1, Keycloak.

**Spec reference:** `docs/superpowers/specs/2026-04-28-stackable-stack-oidc-design.md` § "Per-product specifics → Superset".

**Depends on:** `2026-04-28-stack-oidc-prerequisites.md`.

---

## File structure

| File | Change |
| --- | --- |
| `platform/manifests/configure-stackable-oidc/job.yaml` | Add Superset apply block. |
| `platform/manifests/superset/superset.yaml` | Add `spec.clusterConfig.authentication` and `configOverrides.superset_config.py` for the role mapping. |
| `website/index.md` | Update the Superset row credentials and link. |

---

## Task 1: Add Superset AuthenticationClass + Secret to the shared Job

**Files:**
- Modify: `platform/manifests/configure-stackable-oidc/job.yaml`

- [ ] **Step 1: Insert the Superset apply block**

In `platform/manifests/configure-stackable-oidc/job.yaml`, append after the most recently inserted product block (or after the placeholder echo if no other product plan has landed):

```sh
              echo "Applying Superset AuthenticationClass + client Secret..."
              kubectl apply -f - <<EOF
              ---
              apiVersion: authentication.stackable.tech/v1alpha1
              kind: AuthenticationClass
              metadata:
                name: superset-keycloak
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
                name: superset-keycloak-client
                namespace: platform
              type: Opaque
              stringData:
                clientId: superset
                clientSecret: superset-secret
              EOF
```

- [ ] **Step 2: Verify YAML still parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/configure-stackable-oidc/job.yaml')))"`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add platform/manifests/configure-stackable-oidc/job.yaml
git commit -m "superset-oidc: extend configure-stackable-oidc with Superset AuthenticationClass + Secret"
```

---

## Task 2: Wire SupersetCluster + role mapping

**Files:**
- Modify: `platform/manifests/superset/superset.yaml`

Add the `authentication` field to `clusterConfig`, and inject `AUTH_ROLES_MAPPING` (plus `AUTH_ROLES_SYNC_AT_LOGIN`) via `configOverrides.superset_config.py` on the default node role group.

- [ ] **Step 1: Add `authentication` to `clusterConfig`**

In `platform/manifests/superset/superset.yaml`, locate the `clusterConfig:` block:

```yaml
  clusterConfig:
    credentialsSecret: simple-superset-credentials
    vectorAggregatorConfigMapName: vector-aggregator-discovery
```

Add after `vectorAggregatorConfigMapName`:

```yaml
    authentication:
      - authenticationClass: superset-keycloak
        oidc:
          clientCredentialsSecret: superset-keycloak-client
        userRegistrationRole: Gamma
        syncRolesAt: Login
```

- [ ] **Step 2: Add `configOverrides.superset_config.py` for role mapping**

In the same file, locate the `nodes:` → `roleGroups:` → `default:` block. The current shape is:

```yaml
  nodes:
    roleConfig:
      listenerClass: external-unstable
    config:
      logging:
        enableVectorAgent: true
    roleGroups:
      default:
        config:
          rowLimit: 10000
          webserverTimeout: 300
```

Inside the `default:` block, alongside `config:`, add a `configOverrides:` block:

```yaml
      default:
        config:
          rowLimit: 10000
          webserverTimeout: 300
        configOverrides:
          superset_config.py:
            AUTH_ROLES_MAPPING: '{"admin": ["Admin"], "viewer": ["Gamma"]}'
            AUTH_ROLES_SYNC_AT_LOGIN: "True"
```

(Stackable's Superset operator passes `configOverrides.superset_config.py` entries straight into the generated `superset_config.py` as Python literals — strings are written as-is. The `AUTH_ROLES_MAPPING` value is a JSON string that Superset's FAB integration parses; `AUTH_ROLES_SYNC_AT_LOGIN: "True"` becomes `AUTH_ROLES_SYNC_AT_LOGIN = True` in the rendered file.)

- [ ] **Step 3: Verify YAML still parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/superset/superset.yaml')))"`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add platform/manifests/superset/superset.yaml
git commit -m "superset-oidc: wire SupersetCluster with admin/Gamma role mapping"
```

---

## Task 3: Update landing page Superset row

**Files:**
- Modify: `website/index.md`

- [ ] **Step 1: Update the row**

In `website/index.md`, locate the Superset row:

```
| **Superset** | [http://{{ nodeport "simple-superset-node" }}/](http://{{ nodeport "simple-superset-node" }}/) | — | — | {{ toggle "platform/manifests/superset/superset.yaml" "spec.clusterOperation.stopped" }}     |
```

Change to:

```
| **Superset** | [http://{{ nodeport "simple-superset-node" }}/login/keycloak/?next=/superset/welcome/](http://{{ nodeport "simple-superset-node" }}/login/keycloak/?next=/superset/welcome/) | `demo-admin` / `demo-user` | *(see keycloak-demo-passwords secret)* | {{ toggle "platform/manifests/superset/superset.yaml" "spec.clusterOperation.stopped" }}     |
```

- [ ] **Step 2: Commit**

```bash
git add website/index.md
git commit -m "superset-oidc: update landing page Superset link and credentials"
```

---

## Manual verification (after deploy)

1. Open `http://<NODE_IP>:<superset-nodeport>/login/keycloak/?next=/superset/welcome/` in incognito.
2. 302 to Keycloak. Log in as `demo-admin`.
3. Land on Superset welcome page logged in. Top-right user menu shows `demo-admin`.
4. Visit `/list/users/` — admin can manage users.
5. Log out, log in as `demo-user`. User menu shows `demo-user`.
6. `/list/users/` should be forbidden / not in the menu (Gamma role).
7. Try opening any dashboard — read access works.

Diagnostic:

- `kubectl -n platform exec deploy/simple-superset-node-default -c superset -- cat /stackable/app/pythonpath/superset_config.py | grep -A 2 AUTH_ROLES` — should show the mapping.
- `kubectl -n platform logs deploy/simple-superset-node-default -c superset --tail=200 | grep -iE "oauth|oidc|role"`.
- Superset caches users in its metadata DB; if a role doesn't take effect, deleting the user record and re-logging-in re-applies the mapping.
