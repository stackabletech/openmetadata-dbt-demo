# Keycloak Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Keycloak via the official Operator into the demo cluster, back it with a dedicated Bitnami Postgres, and bootstrap a `stackable-demo` realm + 2 users + 2 roles via OpenTofu running in a Job (mirrors the existing `configure-forgejo` pattern). After this lands the cluster has a working IdP that subsequent sub-projects (B–E) wire each tool to.

**Architecture:** Three new ArgoCD Applications under `infrastructure/`: one for the Keycloak Operator (upstream YAMLs), one for the Keycloak CR + Tofu Job, plus the existing per-app Postgres ApplicationSet extended with a `keycloak` entry. Tofu runs against the Operator-managed `keycloak-service` inside the cluster, applies declaratively-defined realm/users/roles via the `keycloak/keycloak` provider, and re-applies on every git push (the Job is annotated with `Replace=true` so ArgoCD recreates it on each sync).

**Tech Stack:** Kubernetes, ArgoCD, Sealed Secrets, Bitnami Postgres Helm chart, Keycloak Operator (`k8s.keycloak.org/v2alpha1`), OpenTofu 1.10, `keycloak/keycloak` Terraform provider 5.x.

**Spec:** `docs/superpowers/specs/2026-04-26-keycloak-bootstrap-design.md`

**File map (final state after this plan completes):**

| Path | Action | Responsibility |
|---|---|---|
| `platform/applications/postgres.yaml` | Modify | Add `keycloak` to the ApplicationSet's component list |
| `secrets/manifests/keycloak-manifests/keycloak-bootstrap-admin.yaml` | Create | Plaintext Secret with master-admin `username`/`password` |
| `secrets/manifests/keycloak-manifests/keycloak-demo-passwords.yaml` | Create | Plaintext Secret with `demo_admin_password`/`demo_user_password` |
| `platform/manifests/keycloak-manifests/sealed-keycloak-bootstrap-admin.yaml` | Create (generated) | Sealed sibling |
| `platform/manifests/keycloak-manifests/sealed-keycloak-demo-passwords.yaml` | Create (generated) | Sealed sibling |
| `infrastructure/keycloak-operator.yaml` | Create | ArgoCD Application sourcing upstream Keycloak Operator manifests |
| `infrastructure/keycloak.yaml` | Create | ArgoCD Application sourcing `infrastructure/keycloak-manifests/` |
| `infrastructure/keycloak-manifests/keycloak.yaml` | Create | The `Keycloak` CR |
| `infrastructure/keycloak-manifests/nodeport.yaml` | Create | Sibling NodePort `keycloak-nodeport` on `30900` |
| `infrastructure/keycloak-manifests/keycloak-db-credentials.yaml` | Create | Plain Secret with `username: keycloak`, `password: keycloak` (matches the ApplicationSet's hardcoded creds) |
| `infrastructure/keycloak-manifests/configure-keycloak.yaml` | Create | Job + ConfigMap with the inline `keycloak.tf` |
| `infrastructure/stack.yaml` | Modify | Register the two new Applications |
| `website/index.md` | Modify | Add Keycloak row to the Platform Deployment table |

**Branch strategy:** Work on a feature branch `keycloak-bootstrap` from `main`. Merge + push when implementation completes.

**Note on Postgres credentials:** The existing `postgres` ApplicationSet hardcodes `auth.password: "{{component}}"` (component name as both username and password — demo simplicity). The Bitnami chart auto-creates a `postgresql-keycloak` Secret with these values, but it doesn't include a `username` key. So we ship a small in-repo `keycloak-db-credentials` Secret with `username: keycloak` + `password: keycloak` that the Keycloak CR's `db.usernameSecret`/`db.passwordSecret` reference. This is plain (not sealed) — matches the existing convention of literal demo passwords in `postgres.yaml`.

**Note on Keycloak Operator version pin:** Pinning to `26.3.7` (latest in the `keycloak-k8s-resources` `26.3.x` line at time of writing). The implementer should verify this tag exists; bump to a newer 26.x patch if a more recent one is available.

---

## Task 1: Add `keycloak` to the Postgres ApplicationSet

**Files:**
- Modify: `platform/applications/postgres.yaml`

- [ ] **Step 1: Add a new entry to the `list.elements` block**

Open `platform/applications/postgres.yaml`. Find the `generators[0].list.elements` block (lines 8–18 or thereabouts). It currently has 5 entries: `airflow`, `hive`, `hive-iceberg`, `openmetadata`, `superset`. Add a 6th:

```yaml
          - component: keycloak
            extendedConfiguration: ""
```

The relevant block, after the edit:

```yaml
  generators:
    - list:
        elements:
          - component: airflow
            extendedConfiguration: ""
          - component: hive
            extendedConfiguration: "password_encryption=md5"
          - component: hive-iceberg
            extendedConfiguration: "password_encryption=md5"
          - component: openmetadata
            extendedConfiguration: ""
          - component: superset
            extendedConfiguration: ""
          - component: keycloak
            extendedConfiguration: ""
```

- [ ] **Step 2: Validate the YAML parses**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('platform/applications/postgres.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add platform/applications/postgres.yaml
git commit -m "postgres: add keycloak component to Postgres ApplicationSet"
```

---

## Task 2: Generate + seal the bootstrap-admin and demo-user passwords

**Files:**
- Create: `secrets/manifests/keycloak-manifests/keycloak-bootstrap-admin.yaml`
- Create: `secrets/manifests/keycloak-manifests/keycloak-demo-passwords.yaml`
- Create (generated): `platform/manifests/keycloak-manifests/sealed-keycloak-bootstrap-admin.yaml`
- Create (generated): `platform/manifests/keycloak-manifests/sealed-keycloak-demo-passwords.yaml`

- [ ] **Step 1: Generate three random passwords**

```bash
ADMIN_PW=$(openssl rand -base64 24)
DEMO_ADMIN_PW=$(openssl rand -base64 24)
DEMO_USER_PW=$(openssl rand -base64 24)
cat > /tmp/keycloak-creds.txt <<EOF
keycloak-admin:    $ADMIN_PW
keycloak-demo-admin: $DEMO_ADMIN_PW
keycloak-demo-user:  $DEMO_USER_PW
EOF
echo "Saved at /tmp/keycloak-creds.txt — copy to a safe location, then we'll delete it in Step 7."
```

- [ ] **Step 2: Create the plaintext Secret for the master admin**

```bash
mkdir -p secrets/manifests/keycloak-manifests
cat > secrets/manifests/keycloak-manifests/keycloak-bootstrap-admin.yaml <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: keycloak-bootstrap-admin
  namespace: platform
stringData:
  username: admin
  password: $ADMIN_PW
EOF
```

- [ ] **Step 3: Create the plaintext Secret for the demo users**

```bash
cat > secrets/manifests/keycloak-manifests/keycloak-demo-passwords.yaml <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: keycloak-demo-passwords
  namespace: platform
stringData:
  demo_admin_password: $DEMO_ADMIN_PW
  demo_user_password: $DEMO_USER_PW
EOF
```

- [ ] **Step 4: Validate both plaintext Secrets parse**

```bash
for f in secrets/manifests/keycloak-manifests/keycloak-bootstrap-admin.yaml secrets/manifests/keycloak-manifests/keycloak-demo-passwords.yaml; do
  python -c "import yaml; list(yaml.safe_load_all(open('$f')))" && echo "OK: $f"
done
```

Expected: 2 lines of `OK: ...`.

- [ ] **Step 5: Seal both via the justfile recipe**

```bash
just seal-secrets
```

Expected: two `Processing: ... -> ...sealed-keycloak-...yaml` lines. Generated outputs:
- `platform/manifests/keycloak-manifests/sealed-keycloak-bootstrap-admin.yaml`
- `platform/manifests/keycloak-manifests/sealed-keycloak-demo-passwords.yaml`

```bash
ls -la platform/manifests/keycloak-manifests/sealed-keycloak-*.yaml
```

Expected: both files present, non-zero size.

- [ ] **Step 6: Commit**

```bash
git add secrets/manifests/keycloak-manifests/ \
        platform/manifests/keycloak-manifests/sealed-keycloak-bootstrap-admin.yaml \
        platform/manifests/keycloak-manifests/sealed-keycloak-demo-passwords.yaml
git commit -m "keycloak: seal bootstrap-admin + demo-user credentials"
```

- [ ] **Step 7: Clean up the scratch credentials file**

```bash
rm -f /tmp/keycloak-creds.txt
```

(Save somewhere safer first if you haven't already.)

---

## Task 3: Create the Keycloak Operator ArgoCD Application

**Files:**
- Create: `infrastructure/keycloak-operator.yaml`

- [ ] **Step 1: Write the Application**

```bash
cat > infrastructure/keycloak-operator.yaml <<'EOF'
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: keycloak-operator
  namespace: deployment
  annotations:
    argocd.argoproj.io/sync-wave: "-1"
spec:
  project: dbt-openmetadata-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: platform
  source:
    repoURL: "https://github.com/keycloak/keycloak-k8s-resources.git"
    targetRevision: "26.3.7" # renovate: datasource=github-tags depName=keycloak/keycloak-k8s-resources
    path: kubernetes
    directory:
      recurse: false
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
    automated:
      selfHeal: true
      prune: true
EOF
```

`ServerSideApply=true` is needed because the Keycloak CRDs are large (>256 KB last-applied-configuration annotation would otherwise blow the metadata size limit on a re-apply). `sync-wave: -1` makes the Operator install before the `keycloak` Application that depends on its CRDs.

- [ ] **Step 2: Validate**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/keycloak-operator.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/keycloak-operator.yaml
git commit -m "keycloak: add ArgoCD Application for the Keycloak Operator"
```

---

## Task 4: Create the keycloak-manifests/ directory contents

**Files:**
- Create: `infrastructure/keycloak-manifests/keycloak.yaml`
- Create: `infrastructure/keycloak-manifests/nodeport.yaml`
- Create: `infrastructure/keycloak-manifests/keycloak-db-credentials.yaml`
- Create: `infrastructure/keycloak-manifests/configure-keycloak.yaml`

- [ ] **Step 1: Make the directory and the DB credentials Secret**

```bash
mkdir -p infrastructure/keycloak-manifests
cat > infrastructure/keycloak-manifests/keycloak-db-credentials.yaml <<'EOF'
---
apiVersion: v1
kind: Secret
metadata:
  name: keycloak-db-credentials
  namespace: platform
stringData:
  username: keycloak
  password: keycloak
EOF
```

(This Secret hardcodes the same demo creds the Postgres ApplicationSet uses for the `keycloak` component. Plain, not sealed — matches the existing repo convention of literal demo passwords for Postgres.)

- [ ] **Step 2: Create the Keycloak CR**

```bash
cat > infrastructure/keycloak-manifests/keycloak.yaml <<'EOF'
---
apiVersion: k8s.keycloak.org/v2alpha1
kind: Keycloak
metadata:
  name: keycloak
  namespace: platform
spec:
  instances: 1
  db:
    vendor: postgres
    host: postgresql-keycloak.shared.svc.cluster.local
    port: 5432
    database: keycloak
    usernameSecret:
      name: keycloak-db-credentials
      key: username
    passwordSecret:
      name: keycloak-db-credentials
      key: password
  http:
    httpEnabled: true
  hostname:
    strict: false
    backchannelDynamic: true
  proxy:
    headers: xforwarded
  bootstrapAdmin:
    user:
      secret: keycloak-bootstrap-admin
EOF
```

- [ ] **Step 3: Create the NodePort Service**

```bash
cat > infrastructure/keycloak-manifests/nodeport.yaml <<'EOF'
---
apiVersion: v1
kind: Service
metadata:
  name: keycloak-nodeport
  namespace: platform
spec:
  type: NodePort
  selector:
    app: keycloak
  ports:
    - name: http
      port: 8080
      targetPort: 8080
      nodePort: 30900
      protocol: TCP
EOF
```

NodePort `30900` is unused in the existing repo (other ports: 30000 / 30080 / 30088 / 30181 / 30585 / 30601).

- [ ] **Step 4: Create the configure-keycloak Job + ConfigMap (the meat)**

```bash
cat > infrastructure/keycloak-manifests/configure-keycloak.yaml <<'EOF'
---
apiVersion: batch/v1
kind: Job
metadata:
  name: configure-keycloak
  namespace: platform
  annotations:
    argocd.argoproj.io/sync-options: Replace=true
spec:
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      containers:
        - name: opentofu
          image: oowy/opentofu:1.10.6-alpine3.20 # renovate: datasource=docker depName=oowy/opentofu
          command: ["/bin/sh", "-c"]
          args:
            - |
              echo "Waiting for Keycloak to be ready..."
              until wget -q -O /dev/null http://keycloak-service.platform.svc.cluster.local:8080/realms/master 2>/dev/null; do
                echo "  Not ready, retrying in 5s..."
                sleep 5
              done
              echo "Keycloak is ready."
              mkdir /work
              cd /work
              cp /config/keycloak.tf .
              tofu init
              tofu plan
              tofu apply -auto-approve
          env:
            - name: TF_VAR_keycloak_admin_username
              valueFrom:
                secretKeyRef: { name: keycloak-bootstrap-admin, key: username }
            - name: TF_VAR_keycloak_admin_password
              valueFrom:
                secretKeyRef: { name: keycloak-bootstrap-admin, key: password }
            - name: TF_VAR_demo_admin_password
              valueFrom:
                secretKeyRef: { name: keycloak-demo-passwords, key: demo_admin_password }
            - name: TF_VAR_demo_user_password
              valueFrom:
                secretKeyRef: { name: keycloak-demo-passwords, key: demo_user_password }
          volumeMounts:
            - mountPath: /config
              name: keycloak-configuration
      restartPolicy: Never
      volumes:
        - name: keycloak-configuration
          configMap: { name: keycloak-configuration }
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: keycloak-configuration
  namespace: platform
data:
  keycloak.tf: |
    terraform {
      required_providers {
        keycloak = {
          source  = "keycloak/keycloak"
          version = "~> 5.0"
        }
      }
    }

    provider "keycloak" {
      client_id     = "admin-cli"
      username      = var.keycloak_admin_username
      password      = var.keycloak_admin_password
      url           = "http://keycloak-service.platform.svc.cluster.local:8080"
      realm         = "master"
      initial_login = false
    }

    variable "keycloak_admin_username" { type = string }
    variable "keycloak_admin_password" { type = string sensitive = true }
    variable "demo_admin_password"    { type = string sensitive = true }
    variable "demo_user_password"     { type = string sensitive = true }

    resource "keycloak_realm" "stackable_demo" {
      realm        = "stackable-demo"
      enabled      = true
      display_name = "Stackable Data Platform Demo"
    }

    resource "keycloak_role" "admin" {
      realm_id    = keycloak_realm.stackable_demo.id
      name        = "admin"
      description = "Administrative access to all demo tools"
    }

    resource "keycloak_role" "viewer" {
      realm_id    = keycloak_realm.stackable_demo.id
      name        = "viewer"
      description = "Read-only access to demo tools"
    }

    resource "keycloak_user" "demo_admin" {
      realm_id       = keycloak_realm.stackable_demo.id
      username       = "demo-admin"
      email          = "demo-admin@stackable.demo"
      enabled        = true
      email_verified = true
      first_name     = "Demo"
      last_name      = "Admin"
      initial_password {
        value     = var.demo_admin_password
        temporary = false
      }
    }

    resource "keycloak_user_roles" "demo_admin_roles" {
      realm_id = keycloak_realm.stackable_demo.id
      user_id  = keycloak_user.demo_admin.id
      role_ids = [keycloak_role.admin.id]
    }

    resource "keycloak_user" "demo_user" {
      realm_id       = keycloak_realm.stackable_demo.id
      username       = "demo-user"
      email          = "demo-user@stackable.demo"
      enabled        = true
      email_verified = true
      first_name     = "Demo"
      last_name      = "User"
      initial_password {
        value     = var.demo_user_password
        temporary = false
      }
    }

    resource "keycloak_user_roles" "demo_user_roles" {
      realm_id = keycloak_realm.stackable_demo.id
      user_id  = keycloak_user.demo_user.id
      role_ids = [keycloak_role.viewer.id]
    }
EOF
```

**Important — fix HCL syntax in two spots in the heredoc above before commit.** The HCL `variable` blocks use commas, not backticks. The heredoc emits HCL into the YAML's `data:` block as a string, but the multi-line string above contains:

```
variable "keycloak_admin_password" { type = string sensitive = true }
```

That's invalid HCL — Terraform/Tofu expects newlines/commas between attributes inside a block. Open the generated file and replace the four `variable` lines with:

```hcl
    variable "keycloak_admin_username" { type = string }
    variable "keycloak_admin_password" {
      type      = string
      sensitive = true
    }
    variable "demo_admin_password" {
      type      = string
      sensitive = true
    }
    variable "demo_user_password" {
      type      = string
      sensitive = true
    }
```

(The 4-space leading indent stays; it's all under `data.keycloak.tf:`.)

- [ ] **Step 5: Validate all four manifests parse as YAML**

```bash
for f in infrastructure/keycloak-manifests/keycloak.yaml \
         infrastructure/keycloak-manifests/nodeport.yaml \
         infrastructure/keycloak-manifests/keycloak-db-credentials.yaml \
         infrastructure/keycloak-manifests/configure-keycloak.yaml; do
  python -c "import yaml; list(yaml.safe_load_all(open('$f')))" && echo "OK: $f"
done
```

Expected: 4 lines of `OK: ...`.

- [ ] **Step 6: Validate the embedded HCL parses (sanity check)**

```bash
python -c "
import yaml
docs = list(yaml.safe_load_all(open('infrastructure/keycloak-manifests/configure-keycloak.yaml')))
cm = next(d for d in docs if d and d.get('kind') == 'ConfigMap')
hcl = cm['data']['keycloak.tf']
# Quick smoke check: count expected resource declarations
for token in ['keycloak_realm', 'keycloak_role.admin', 'keycloak_role.viewer',
              'keycloak_user.demo_admin', 'keycloak_user.demo_user',
              'keycloak_user_roles.demo_admin_roles', 'keycloak_user_roles.demo_user_roles']:
    assert token in hcl, f'missing token: {token}'
print('HCL embedding looks complete')
"
```

Expected: `HCL embedding looks complete`.

- [ ] **Step 7: Commit**

```bash
git add infrastructure/keycloak-manifests/
git commit -m "keycloak: add Keycloak CR, NodePort, db creds, and configure Job"
```

---

## Task 5: Create the keycloak ArgoCD Application

**Files:**
- Create: `infrastructure/keycloak.yaml`

- [ ] **Step 1: Write the Application**

```bash
cat > infrastructure/keycloak.yaml <<'EOF'
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: keycloak
  namespace: deployment
spec:
  project: dbt-openmetadata-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: platform
  source:
    repoURL: "http://forgejo-http.deployment.svc.cluster.local:3000/stackable/openmetadata-dbt-demo.git"
    targetRevision: "main"
    path: infrastructure/keycloak-manifests/
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    automated:
      selfHeal: true
      prune: true
EOF
```

- [ ] **Step 2: Validate**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/keycloak.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/keycloak.yaml
git commit -m "keycloak: add ArgoCD Application for the Keycloak CR + Tofu Job"
```

---

## Task 6: Register both Applications in `infrastructure/stack.yaml`

**Files:**
- Modify: `infrastructure/stack.yaml`

- [ ] **Step 1: Append two `plainYaml:` entries to the `manifests:` list**

Open `infrastructure/stack.yaml`. Find the existing `manifests:` list. After the last existing entry (`cluster-apps.yaml`), add:

```yaml
      ################################
      # keycloak — IdP for OIDC across all demo tools
      ################################
      - plainYaml: https://raw.githubusercontent.com/stackabletech/openmetadata-dbt-demo/refs/heads/main/infrastructure/keycloak-operator.yaml
      - plainYaml: https://raw.githubusercontent.com/stackabletech/openmetadata-dbt-demo/refs/heads/main/infrastructure/keycloak.yaml
```

- [ ] **Step 2: Validate**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('infrastructure/stack.yaml')))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/stack.yaml
git commit -m "keycloak: register Operator + Keycloak Applications in stack.yaml"
```

---

## Task 7: Add Keycloak row to the landing page

**Files:**
- Modify: `website/index.md`

- [ ] **Step 1: Read the current Platform Deployment table**

Find the `## Platform Deployment` section (top of the file) — it currently lists ArgoCD and Forgejo.

- [ ] **Step 2: Add a Keycloak row after Forgejo**

Insert a new row immediately after the Forgejo row in the Platform Deployment table:

```markdown
| **Keycloak** | [http://{{ nodeport "keycloak-nodeport" }}/realms/stackable-demo/account/](http://{{ nodeport "keycloak-nodeport" }}/realms/stackable-demo/account/) | `demo-admin` / `demo-user` | *(see keycloak-demo-passwords secret)* | — |
```

The cell column count (5) matches existing rows. The URL points at the realm's user-facing account console (the master-admin console at `/admin/` is for ops use and not surfaced).

- [ ] **Step 3: Validate index.md still parses (no markdownlint warnings on the table)**

```bash
grep -c "^|" website/index.md
```

Expected: a count consistent with the existing row count + 1 (i.e. one more `|`-prefixed line than before).

- [ ] **Step 4: Commit**

```bash
git add website/index.md
git commit -m "landing: add Keycloak row to Platform Deployment table"
```

---

## Task 8: Push, deploy, verify in-cluster

**Files:** none — verification only.

- [ ] **Step 1: Push the branch (or main)**

```bash
git checkout -B keycloak-bootstrap
git push -u origin keycloak-bootstrap
# OR: merge to main and push
# git checkout main && git merge --ff-only keycloak-bootstrap && git push origin main
```

- [ ] **Step 2: Wait for ArgoCD to mirror + sync (~1–2 min)**

```bash
kubectl -n deployment get applicationset postgres -o jsonpath='{.status.conditions[?(@.type=="ResourcesUpToDate")].status}{"\n"}'
# expect: True (postgres-keycloak Application now generated)

for app in keycloak-operator keycloak-postgres keycloak; do
  kubectl -n deployment get application $app -o jsonpath='{.metadata.name}: {.status.sync.status}/{.status.health.status}{"\n"}'
done
# expect: all three Synced/Healthy
```

- [ ] **Step 3: Verify the Keycloak CR is reconciled**

```bash
kubectl -n platform get keycloak keycloak \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}'
# expect: True
kubectl -n platform get pods -l app=keycloak
# expect: 1/1 Running
```

- [ ] **Step 4: Verify the configure-keycloak Job ran successfully**

```bash
kubectl -n platform logs job/configure-keycloak --tail=40
# expect: "Apply complete! Resources: 7 added" (1 realm + 2 roles + 2 users + 2 user_roles)
```

If the Job is still running:

```bash
kubectl -n platform wait --for=condition=Complete job/configure-keycloak --timeout=5m
```

- [ ] **Step 5: End-to-end token test for demo-admin**

```bash
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
DEMO_PW=$(kubectl -n platform get secret keycloak-demo-passwords -o jsonpath='{.data.demo_admin_password}' | base64 -d)
curl -s -X POST "http://$NODE_IP:30900/realms/stackable-demo/protocol/openid-connect/token" \
  -d "client_id=admin-cli&grant_type=password&username=demo-admin&password=$DEMO_PW" \
  | python3 -c "import json,sys; t=json.load(sys.stdin); print('token len:', len(t['access_token']))"
# expect: a non-empty token length
```

- [ ] **Step 6: Browser smoke**

Open `http://<NODE_IP>:30900/realms/stackable-demo/account/` in a browser. Log in as `demo-admin` (password from `keycloak-demo-passwords` Secret). The account console should render. Repeat for `demo-user`.

- [ ] **Step 7: Re-run Tofu after editing the .tf (optional sanity check)**

Add a third user to the `keycloak.tf` ConfigMap in `configure-keycloak.yaml`, push, and observe:

```bash
kubectl -n platform logs job/configure-keycloak --follow
# expect: "Apply complete! Resources: 1 added" (or similar)
```

This confirms the `Replace=true` annotation makes ArgoCD recreate the Job on each sync. Revert the change after verification if you don't want the user to stick.

---

## Self-Review

**Spec coverage:**

- §"Architecture" three Applications + reconcile order → Tasks 1, 3, 4, 5, 6.
- §"Hostname strategy" `hostname.strict: false` + `proxy.headers: xforwarded` → Task 4 Step 2 (Keycloak CR).
- §"Components" 1. Keycloak CR → Task 4 Step 2.
- §"Components" 2. NodePort → Task 4 Step 3.
- §"Components" 3. configure-keycloak Job + Tofu file → Task 4 Step 4.
- §"Components" 4. ArgoCD Applications: keycloak-operator → Task 3, postgres-keycloak (via ApplicationSet edit) → Task 1, keycloak → Task 5.
- §"Sealed secrets" `keycloak-bootstrap-admin` + `keycloak-demo-passwords` → Task 2.
- §"Sealed secrets" Postgres credentials — handled differently than spec said: an in-repo plain Secret (`keycloak-db-credentials`) matches the existing ApplicationSet's hardcoded creds, since the Bitnami chart's auto-created Secret doesn't include a `username` key. Documented in the file-map note at the top.
- §"Landing page" → Task 7.
- §"Verification" → Task 8.
- §"Adding a user later" demonstrated in Task 8 Step 7.
- §"Error handling / known operational caveats" — covered as in-line caveats throughout the manifests (`Replace=true` for re-apply, `until wget` polling, `ServerSideApply=true` for Operator CRDs).

**Placeholder scan:** no `TBD`/`TODO`/`fill in details`/`Similar to Task N` tokens. Version pins (`26.3.7` for the Operator, `~> 5.0` for the Tofu provider, `oowy/opentofu:1.10.6-alpine3.20` for the Job image) are concrete. The note about verifying the Operator tag at implementation time is operational guidance, not a placeholder.

**Type / name consistency:**

- Secret name `keycloak-bootstrap-admin` (used in Task 2 plaintext, Task 4 Step 2 `bootstrapAdmin.user.secret`, Task 4 Step 4 Job env mappings).
- Secret name `keycloak-demo-passwords` (Task 2 plaintext, Task 4 Step 4 Job env, Task 8 Step 5 verification).
- Secret name `keycloak-db-credentials` (Task 4 Step 1 file create, Task 4 Step 2 `db.usernameSecret`/`db.passwordSecret`).
- Service name `keycloak-nodeport` on NodePort `30900` (Task 4 Step 3, Task 7 landing page row, Task 8 verification).
- Service name `keycloak-service` (Operator-managed, referenced from Task 4 Step 4 Job script + Tofu provider URL).
- Realm name `stackable-demo` (Task 4 Step 4 Tofu, Task 7 landing page URL, Task 8 verification).
- User names `demo-admin` / `demo-user` (Task 4 Step 4 Tofu, Task 7 landing page, Task 8 Step 5).
- ApplicationSet `postgres` adds `keycloak` component → release `postgresql-keycloak` → Service `postgresql-keycloak.shared.svc.cluster.local:5432` (Task 1 + Task 4 Step 2 Keycloak CR `db.host`).
- Tofu variable names (`keycloak_admin_username`, `keycloak_admin_password`, `demo_admin_password`, `demo_user_password`) consistent across the HCL embedding (Task 4 Step 4) and the Job's env-var mappings (same step).
- Operator install path: `infrastructure/keycloak-operator.yaml` Application sources `keycloak-k8s-resources` repo's `kubernetes` directory; CRDs `keycloaks.k8s.keycloak.org` and `keycloakrealmimports.k8s.keycloak.org` are then available for Task 4's `Keycloak` CR.
