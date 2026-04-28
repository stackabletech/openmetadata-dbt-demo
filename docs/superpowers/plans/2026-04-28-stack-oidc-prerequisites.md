# Stack OIDC SSO — Prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the foundation each per-product OIDC plan depends on — five new Keycloak OIDC clients (Trino, Airflow, NiFi, Superset, OpenMetadata) and a scaffolded `configure-stackable-oidc` Job that later plans extend with their AuthenticationClass + Secret heredocs.

**Architecture:** Realm config additions go into the existing OpenTofu HCL block in `infrastructure/keycloak-manifests/configure-keycloak.yaml` — five copies of the demo-landing/argocd/forgejo client/scope/audience-mapper triple, one per product. The shared Job is a new manifest under `platform/manifests/configure-stackable-oidc/` with its own ArgoCD Application; the Job script is initially a no-op stub with a comment marker per-product plans patch into.

**Tech Stack:** OpenTofu HCL embedded in Kubernetes ConfigMap, OpenTofu Keycloak provider, plain `kubectl apply -f -` heredoc Jobs, ArgoCD Application/app-of-apps.

**Spec reference:** `docs/superpowers/specs/2026-04-28-stackable-stack-oidc-design.md` — section "Cross-cutting" points 1 and 3.

---

## File structure

| File | Change |
| --- | --- |
| `infrastructure/keycloak-manifests/configure-keycloak.yaml` | Append five `keycloak_openid_client` + `keycloak_openid_client_default_scopes` + `keycloak_openid_audience_protocol_mapper` triples (Trino, Airflow, NiFi, Superset, OpenMetadata) inside the embedded `keycloak.tf` ConfigMap. |
| `platform/manifests/configure-stackable-oidc/job.yaml` (new) | ServiceAccount, ClusterRole + Role for AuthenticationClass and Secret writes, the Job itself. Job body is initially a no-op stub. |
| `platform/applications/configure-stackable-oidc.yaml` (new) | ArgoCD Application pointing at `platform/manifests/configure-stackable-oidc/`. |

---

## Task 1: Add five Keycloak OIDC clients

**Files:**
- Modify: `infrastructure/keycloak-manifests/configure-keycloak.yaml` (append after the `forgejo_audience` resource at line 249).

This task adds five identical-shaped client triples, one per product. Each triple = one `keycloak_openid_client` + one `keycloak_openid_client_default_scopes` + one `keycloak_openid_audience_protocol_mapper`, modelled exactly on the existing `demo_landing` block (lines 154-185).

- [ ] **Step 1: Append the Trino client triple**

In `infrastructure/keycloak-manifests/configure-keycloak.yaml`, immediately after the existing `forgejo_audience` resource (which ends at line 249), insert:

```hcl

    resource "keycloak_openid_client" "trino" {
      realm_id              = keycloak_realm.stackable_demo.id
      client_id             = "trino"
      name                  = "Trino"
      enabled               = true
      access_type           = "CONFIDENTIAL"
      client_secret         = "trino-secret"
      standard_flow_enabled = true
      valid_redirect_uris   = ["*"]
      web_origins           = ["+"]
    }

    resource "keycloak_openid_client_default_scopes" "trino_scopes" {
      realm_id  = keycloak_realm.stackable_demo.id
      client_id = keycloak_openid_client.trino.id
      default_scopes = [
        "profile",
        "email",
        "roles",
        "web-origins",
      ]
    }

    resource "keycloak_openid_audience_protocol_mapper" "trino_audience" {
      realm_id                 = keycloak_realm.stackable_demo.id
      client_id                = keycloak_openid_client.trino.id
      name                     = "trino-audience"
      included_client_audience = "trino"
      add_to_id_token          = true
      add_to_access_token      = true
    }
```

- [ ] **Step 2: Append the Airflow client triple**

After the Trino audience mapper block, insert:

```hcl

    resource "keycloak_openid_client" "airflow" {
      realm_id              = keycloak_realm.stackable_demo.id
      client_id             = "airflow"
      name                  = "Airflow"
      enabled               = true
      access_type           = "CONFIDENTIAL"
      client_secret         = "airflow-secret"
      standard_flow_enabled = true
      valid_redirect_uris   = ["*"]
      web_origins           = ["+"]
    }

    resource "keycloak_openid_client_default_scopes" "airflow_scopes" {
      realm_id  = keycloak_realm.stackable_demo.id
      client_id = keycloak_openid_client.airflow.id
      default_scopes = [
        "profile",
        "email",
        "roles",
        "web-origins",
      ]
    }

    resource "keycloak_openid_audience_protocol_mapper" "airflow_audience" {
      realm_id                 = keycloak_realm.stackable_demo.id
      client_id                = keycloak_openid_client.airflow.id
      name                     = "airflow-audience"
      included_client_audience = "airflow"
      add_to_id_token          = true
      add_to_access_token      = true
    }
```

- [ ] **Step 3: Append the NiFi client triple**

After the Airflow audience mapper block, insert:

```hcl

    resource "keycloak_openid_client" "nifi" {
      realm_id              = keycloak_realm.stackable_demo.id
      client_id             = "nifi"
      name                  = "NiFi"
      enabled               = true
      access_type           = "CONFIDENTIAL"
      client_secret         = "nifi-secret"
      standard_flow_enabled = true
      valid_redirect_uris   = ["*"]
      web_origins           = ["+"]
    }

    resource "keycloak_openid_client_default_scopes" "nifi_scopes" {
      realm_id  = keycloak_realm.stackable_demo.id
      client_id = keycloak_openid_client.nifi.id
      default_scopes = [
        "profile",
        "email",
        "roles",
        "web-origins",
      ]
    }

    resource "keycloak_openid_audience_protocol_mapper" "nifi_audience" {
      realm_id                 = keycloak_realm.stackable_demo.id
      client_id                = keycloak_openid_client.nifi.id
      name                     = "nifi-audience"
      included_client_audience = "nifi"
      add_to_id_token          = true
      add_to_access_token      = true
    }
```

- [ ] **Step 4: Append the Superset client triple**

After the NiFi audience mapper block, insert:

```hcl

    resource "keycloak_openid_client" "superset" {
      realm_id              = keycloak_realm.stackable_demo.id
      client_id             = "superset"
      name                  = "Superset"
      enabled               = true
      access_type           = "CONFIDENTIAL"
      client_secret         = "superset-secret"
      standard_flow_enabled = true
      valid_redirect_uris   = ["*"]
      web_origins           = ["+"]
    }

    resource "keycloak_openid_client_default_scopes" "superset_scopes" {
      realm_id  = keycloak_realm.stackable_demo.id
      client_id = keycloak_openid_client.superset.id
      default_scopes = [
        "profile",
        "email",
        "roles",
        "web-origins",
      ]
    }

    resource "keycloak_openid_audience_protocol_mapper" "superset_audience" {
      realm_id                 = keycloak_realm.stackable_demo.id
      client_id                = keycloak_openid_client.superset.id
      name                     = "superset-audience"
      included_client_audience = "superset"
      add_to_id_token          = true
      add_to_access_token      = true
    }
```

- [ ] **Step 5: Append the OpenMetadata client triple**

After the Superset audience mapper block, insert:

```hcl

    resource "keycloak_openid_client" "openmetadata" {
      realm_id              = keycloak_realm.stackable_demo.id
      client_id             = "openmetadata"
      name                  = "OpenMetadata"
      enabled               = true
      access_type           = "CONFIDENTIAL"
      client_secret         = "openmetadata-secret"
      standard_flow_enabled = true
      valid_redirect_uris   = ["*"]
      web_origins           = ["+"]
    }

    resource "keycloak_openid_client_default_scopes" "openmetadata_scopes" {
      realm_id  = keycloak_realm.stackable_demo.id
      client_id = keycloak_openid_client.openmetadata.id
      default_scopes = [
        "profile",
        "email",
        "roles",
        "web-origins",
      ]
    }

    resource "keycloak_openid_audience_protocol_mapper" "openmetadata_audience" {
      realm_id                 = keycloak_realm.stackable_demo.id
      client_id                = keycloak_openid_client.openmetadata.id
      name                     = "openmetadata-audience"
      included_client_audience = "openmetadata"
      add_to_id_token          = true
      add_to_access_token      = true
    }
```

- [ ] **Step 6: Verify the YAML still parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('infrastructure/keycloak-manifests/configure-keycloak.yaml')))"`
Expected: no output (valid YAML; we are not parsing the embedded HCL — only the outer ConfigMap).

- [ ] **Step 7: Commit**

```bash
git add infrastructure/keycloak-manifests/configure-keycloak.yaml
git commit -m "keycloak: add OIDC clients for trino, airflow, nifi, superset, openmetadata"
```

---

## Task 2: Scaffold the shared `configure-stackable-oidc` Job

**Files:**
- Create: `platform/manifests/configure-stackable-oidc/job.yaml`

This Job is the future home of per-product `kubectl apply -f -` heredocs that create AuthenticationClass + client Secret pairs. For this prerequisites plan it ships as a deliberate no-op with a clearly-marked extension point so each per-product plan adds its block in the right place.

- [ ] **Step 1: Create the manifest directory**

Run: `mkdir -p platform/manifests/configure-stackable-oidc`
Expected: directory exists; `ls platform/manifests/configure-stackable-oidc` returns nothing yet.

- [ ] **Step 2: Write the Job manifest**

Create `platform/manifests/configure-stackable-oidc/job.yaml` with this content:

```yaml
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: configure-stackable-oidc
  namespace: platform
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: configure-stackable-oidc
rules:
  - apiGroups: ["authentication.stackable.tech"]
    resources: ["authenticationclasses"]
    verbs: ["get", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: configure-stackable-oidc
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: configure-stackable-oidc
subjects:
  - kind: ServiceAccount
    name: configure-stackable-oidc
    namespace: platform
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: configure-stackable-oidc
  namespace: platform
rules:
  - apiGroups: [""]
    resources: ["secrets", "configmaps"]
    verbs: ["get", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: configure-stackable-oidc
  namespace: platform
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: configure-stackable-oidc
subjects:
  - kind: ServiceAccount
    name: configure-stackable-oidc
    namespace: platform
---
apiVersion: batch/v1
kind: Job
metadata:
  name: configure-stackable-oidc
  namespace: platform
  annotations:
    argocd.argoproj.io/sync-options: Replace=true
spec:
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      serviceAccountName: configure-stackable-oidc
      restartPolicy: Never
      containers:
        - name: kubectl
          image: bitnamilegacy/kubectl:1.31 # renovate: datasource=docker depName=bitnamilegacy/kubectl
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -e

              echo "Waiting for oidc-endpoints ConfigMap..."
              until kubectl -n platform get cm oidc-endpoints >/dev/null 2>&1; do
                echo "  not ready, retrying in 5s..."
                sleep 5
              done

              KEYCLOAK_HOST=$(kubectl -n platform get cm oidc-endpoints -o jsonpath='{.data.keycloak-host}')
              KEYCLOAK_HOSTNAME="${KEYCLOAK_HOST%:*}"
              KEYCLOAK_PORT="${KEYCLOAK_HOST#*:}"
              echo "Keycloak: hostname=$KEYCLOAK_HOSTNAME port=$KEYCLOAK_PORT"

              # === Per-product blocks below ===
              # Each per-product OIDC plan inserts its
              # AuthenticationClass + Secret apply block here.
              # See docs/superpowers/specs/2026-04-28-stackable-stack-oidc-design.md.

              echo "configure-stackable-oidc: no per-product blocks yet."
```

- [ ] **Step 3: Verify the YAML parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/configure-stackable-oidc/job.yaml')))"`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add platform/manifests/configure-stackable-oidc/job.yaml
git commit -m "stackable-oidc: scaffold shared configure-stackable-oidc Job"
```

---

## Task 3: ArgoCD Application for the new Job

**Files:**
- Create: `platform/applications/configure-stackable-oidc.yaml`

The cluster-apps Application (`infrastructure/cluster-apps.yaml`) watches `platform/applications/`, so dropping a new file there is enough for ArgoCD to pick it up.

- [ ] **Step 1: Write the Application manifest**

Create `platform/applications/configure-stackable-oidc.yaml`:

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: configure-stackable-oidc
  namespace: deployment
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  project: dbt-openmetadata-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: platform
  source:
    repoURL: "http://forgejo-http.deployment.svc.cluster.local:3000/stackable/openmetadata-dbt-demo.git"
    targetRevision: "main"
    path: platform/manifests/configure-stackable-oidc/
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    automated:
      selfHeal: true
      prune: true
```

(`sync-wave: "1"` matches `argocd-oidc.yaml:8` — peer Application that also depends on the `oidc-endpoints` ConfigMap.)

- [ ] **Step 2: Verify the YAML parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/applications/configure-stackable-oidc.yaml')))"`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add platform/applications/configure-stackable-oidc.yaml
git commit -m "stackable-oidc: add ArgoCD Application for configure-stackable-oidc"
```

---

## Manual verification (after deploy)

This plan deliberately produces *no* user-visible behaviour change. The five new Keycloak clients are provisioned but unused; the new Job runs and is a no-op (`echo "no per-product blocks yet."`).

After the Keycloak realm gets re-applied (per the existing realm-wipe + Job re-run workflow described in `feedback_keycloak_tofu_design.md`):

1. Confirm the five new clients exist by inspecting Keycloak admin UI → Clients, or via:
   ```sh
   ADMIN_USER=$(kubectl -n platform get secret keycloak-bootstrap-admin -o jsonpath='{.data.username}' | base64 -d)
   ADMIN_PASS=$(kubectl -n platform get secret keycloak-bootstrap-admin -o jsonpath='{.data.password}' | base64 -d)
   kubectl -n platform run kctl --rm -i --restart=Never --image=curlimages/curl:latest \
     --env=U=$ADMIN_USER --env=P=$ADMIN_PASS --command -- sh -c '
     T=$(curl -s -d "grant_type=password&client_id=admin-cli&username=$U&password=$P" \
       http://keycloak-service.platform.svc.cluster.local:8080/realms/master/protocol/openid-connect/token \
       | tr "," "\n" | grep access_token | cut -d\" -f4)
     for c in trino airflow nifi superset openmetadata; do
       echo -n "$c: "
       curl -s -H "Authorization: Bearer $T" \
         "http://keycloak-service.platform.svc.cluster.local:8080/admin/realms/stackable-demo/clients?clientId=$c" \
         | grep -o "\"clientId\":\"[^\"]*\"" || echo "MISSING"
     done
   '
   ```
   Expected: each of the five clients prints `"clientId":"<name>"`.

2. Confirm the new Job ran:
   ```sh
   kubectl -n platform get job configure-stackable-oidc \
     -o custom-columns='NAME:.metadata.name,SUCCEEDED:.status.succeeded'
   ```
   Expected: `SUCCEEDED: 1`.

3. Confirm Job logs show the placeholder line:
   ```sh
   kubectl -n platform logs job/configure-stackable-oidc | tail -5
   ```
   Expected: ends with `configure-stackable-oidc: no per-product blocks yet.`.

If steps 1-3 all pass, the per-product plans are unblocked.
