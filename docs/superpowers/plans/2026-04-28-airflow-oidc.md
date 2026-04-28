# Airflow OIDC SSO — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Airflow web UI to Keycloak so `demo-admin` and `demo-user` log in via SSO. Map the Keycloak `admin` realm role to FAB's `Admin`, the `viewer` realm role to FAB's `Viewer`.

**Architecture:** New `AuthenticationClass: airflow-keycloak` + `airflow-keycloak-client` Secret created by the shared `configure-stackable-oidc` Job. `AirflowCluster.spec.clusterConfig.authentication` references the class. Role mapping injected via `envOverrides.AUTH_ROLES_MAPPING` on the webserver role group, parsed by Flask-AppBuilder's `webserver_config.py` (which the Stackable Airflow operator generates).

**Tech Stack:** Stackable Airflow operator, Flask-AppBuilder OIDC, Stackable AuthenticationClass v1alpha1, Keycloak.

**Spec reference:** `docs/superpowers/specs/2026-04-28-stackable-stack-oidc-design.md` § "Per-product specifics → Airflow".

**Depends on:** `2026-04-28-stack-oidc-prerequisites.md` (the `airflow` Keycloak client must exist).

---

## File structure

| File | Change |
| --- | --- |
| `platform/manifests/configure-stackable-oidc/job.yaml` | Add a `kubectl apply -f -` heredoc for `airflow-keycloak` AuthenticationClass + `airflow-keycloak-client` Secret, immediately after the existing Trino block (or wherever the previous product's block ends — append). |
| `platform/manifests/airflow/airflow.yaml` | Add `spec.clusterConfig.authentication` referencing `airflow-keycloak`, and `AUTH_ROLES_MAPPING` env var on the webserver role group. |
| `website/index.md` | Update the Airflow row credentials to `demo-admin` / `demo-user`. |

---

## Task 1: Add Airflow AuthenticationClass + Secret to the shared Job

**Files:**
- Modify: `platform/manifests/configure-stackable-oidc/job.yaml`

Append a new apply block after the existing Trino block (assuming Trino plan landed first; if not, anywhere after the `# === Per-product blocks below ===` marker is fine).

- [ ] **Step 1: Insert the Airflow apply block**

In `platform/manifests/configure-stackable-oidc/job.yaml`, immediately after the `EOF` line that closes the Trino apply block (or after the placeholder echo if Trino's plan hasn't landed), insert:

```sh
              echo "Applying Airflow AuthenticationClass + client Secret..."
              kubectl apply -f - <<EOF
              ---
              apiVersion: authentication.stackable.tech/v1alpha1
              kind: AuthenticationClass
              metadata:
                name: airflow-keycloak
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
                name: airflow-keycloak-client
                namespace: platform
              type: Opaque
              stringData:
                clientId: airflow
                clientSecret: airflow-secret
              EOF
```

- [ ] **Step 2: Verify YAML still parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/configure-stackable-oidc/job.yaml')))"`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add platform/manifests/configure-stackable-oidc/job.yaml
git commit -m "airflow-oidc: extend configure-stackable-oidc with Airflow AuthenticationClass + Secret"
```

---

## Task 2: Wire AirflowCluster + role mapping

**Files:**
- Modify: `platform/manifests/airflow/airflow.yaml`

Add the `authentication` field to `clusterConfig` and the `AUTH_ROLES_MAPPING` env var to the webserver's `envOverrides`. The existing `envOverrides:` block on the webserver role group is reused via the YAML anchor `&envOverrides`. We add a new override on the existing block — it merges with the anchor.

- [ ] **Step 1: Add `authentication` to `clusterConfig`**

In `platform/manifests/airflow/airflow.yaml`, locate the `clusterConfig:` block (around lines 13-26). After the existing `vectorAggregatorConfigMapName: vector-aggregator-discovery` line, add:

```yaml
    authentication:
      - authenticationClass: airflow-keycloak
        oidc:
          clientCredentialsSecret: airflow-keycloak-client
        userRegistrationRole: Public
        syncRolesAt: Registration
```

- [ ] **Step 2: Add `AUTH_ROLES_MAPPING` to webserver envOverrides**

In the same file, locate the `webservers:` → `envOverrides: &envOverrides` block. Add a new line after the existing entries (before the next `podOverrides` block):

```yaml
      AUTH_ROLES_MAPPING: '{"admin": ["Admin"], "viewer": ["Viewer"]}'
```

The full `envOverrides` block should now end with:

```yaml
      AIRFLOW__SCHEDULER__DAG_DIR_LIST_INTERVAL: "20"
      AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: "False"
      AUTH_ROLES_MAPPING: '{"admin": ["Admin"], "viewer": ["Viewer"]}'
```

(`AUTH_ROLES_MAPPING` is read by FAB at startup and applied at user-registration time, hence `syncRolesAt: Registration` in the AuthenticationClass reference.)

- [ ] **Step 3: Verify YAML still parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/airflow/airflow.yaml')))"`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add platform/manifests/airflow/airflow.yaml
git commit -m "airflow-oidc: wire AirflowCluster to airflow-keycloak with admin/viewer role mapping"
```

---

## Task 3: Update landing page Airflow row

**Files:**
- Modify: `website/index.md`

- [ ] **Step 1: Update the row**

In `website/index.md`, locate the Airflow row:

```
| **Airflow** | [http://{{ nodeport "airflow-webserver" }}/](http://{{ nodeport "airflow-webserver" }}/) | `admin` | `admin` | {{ toggle "platform/manifests/airflow/airflow.yaml" "spec.clusterOperation.stopped" }}       |
```

Change to:

```
| **Airflow** | [http://{{ nodeport "airflow-webserver" }}/login/keycloak](http://{{ nodeport "airflow-webserver" }}/login/keycloak) | `demo-admin` / `demo-user` | *(see keycloak-demo-passwords secret)* | {{ toggle "platform/manifests/airflow/airflow.yaml" "spec.clusterOperation.stopped" }}       |
```

(`/login/keycloak` is FAB's OIDC initiation path — clicking the link triggers the OAuth flow directly instead of stopping at FAB's local-login form.)

- [ ] **Step 2: Commit**

```bash
git add website/index.md
git commit -m "airflow-oidc: update landing page Airflow link to OIDC trigger path"
```

---

## Manual verification (after deploy)

1. Open `http://<NODE_IP>:<airflow-webserver-nodeport>/login/keycloak` in incognito.
2. 302 to Keycloak. Log in as `demo-admin`.
3. Land on Airflow logged in as `demo-admin`. Top-right shows the user.
4. Hit `/users/list/` — should be visible (Admin role allows user management). Visit `/admin/userinfoeditview/form` and confirm role is `Admin`.
5. Log out, log in as `demo-user`. Confirm role is `Viewer`. `/users/list/` returns 403 (Viewer can't manage users).

Diagnostic if role mapping doesn't take effect:

- `kubectl -n platform exec deploy/airflow-webserver-default -c airflow -- cat /stackable/app/webserver_config.py | grep -A 5 AUTH_ROLES_MAPPING` — should show the mapping dict.
- Airflow logs: `kubectl -n platform logs deploy/airflow-webserver-default -c airflow --tail=200 | grep -iE "oidc|fab.security|role"`.
- If a user registers with the wrong role, FAB caches it in the Airflow DB. Manual fix: log in as DB admin and `UPDATE ab_user_role SET role_id = ...`. Or delete the user and re-login (FAB recreates with the right role on registration). For a demo, deleting through the admin UI is fine.
