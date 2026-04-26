# Design: Keycloak deployment + Tofu bootstrap (sub-project A)

**Date:** 2026-04-26

## Goal

Add Keycloak to the demo cluster as the foundation for OIDC-based SSO across all platform tools. Deploy via the official Keycloak Operator (CR-based), back it with a dedicated Bitnami Postgres instance (matches existing per-app Postgres pattern), and bootstrap the realm + initial users + initial roles via OpenTofu running in a Job (mirrors the existing `configure-forgejo` pattern). After this work lands, the cluster has a working Keycloak with a `stackable-demo` realm, two seeded users (`demo-admin` / `demo-user`) with realm roles (`admin` / `viewer`), and a Tofu config-as-code surface that subsequent sub-projects (B–E) extend with per-product OIDC clients and role mappings.

The user-visible outcome: `http://<node-ip>:30900/realms/stackable-demo/account/` is reachable, and `demo-admin` / `demo-user` can log in with passwords pulled from a sealed Secret.

## Non-goals (this sub-project)

- Wiring any specific tool (demo-landing, Forgejo, ArgoCD, Trino, NiFi, etc.) to Keycloak. Each is its own follow-up sub-project (B–E).
- Token-issuer hostname stability across cluster recreations. The demo's NodePort lives at whatever `<node-ip>` AKS gives this run; Keycloak runs with `hostname.strict: false` so any incoming `Host:` header is accepted. Production-grade trusted-redirect-URI hardening is out of scope.
- Persistent Tofu state. The bootstrap Job re-applies stateless on every sync; the Keycloak provider tolerates this for declarative resources (realm, roles, users, clients). Trade-off: deleting a resource from `keycloak.tf` won't auto-delete it from Keycloak — orphans must be removed manually if it ever matters.
- Multi-realm setup. One realm (`stackable-demo`) for the whole demo.
- Automated TLS termination at the NodePort. Plain HTTP for the demo, behind whatever access path the user has to the cluster.
- Replacing demo-landing's basic auth with OIDC. That's sub-project B.

## Architecture

Three new ArgoCD `Application`s wire up the deployment, all under `infrastructure/`:

1. **`keycloak-operator`** — installs the official Keycloak Operator (controller + the `keycloaks.k8s.keycloak.org` and `keycloakrealmimports.k8s.keycloak.org` CRDs). Watches the `platform` namespace.
2. **`postgres-keycloak`** — Bitnami `postgresql` Helm chart, release `postgresql-keycloak`, namespace `shared`. Mirrors the existing `airflow-postgres` / `superset-postgres` Applications.
3. **`keycloak`** — manifest-dir Application sourcing `infrastructure/keycloak-manifests/`. Reconciles the `Keycloak` CR, the sibling NodePort, and the Tofu bootstrap Job into namespace `platform`.

### Reconcile order

```
postgres-keycloak comes up
  → postgresql-keycloak Service exposes :5432 in `shared` ns
keycloak-operator comes up
  → CRDs installed; controller-manager Pod Ready
keycloak Application syncs
  → Keycloak CR reconciled by Operator → Keycloak StatefulSet runs DB migrations
  → keycloak-service.platform.svc.cluster.local:8080 (managed by Operator)
  → keycloak-nodeport Service exposes :30900 (sibling)
  → configure-keycloak Job: waits for /realms/master, then runs `tofu apply`
       → realm `stackable-demo` created
       → roles `admin`, `viewer` created
       → users `demo-admin`, `demo-user` created with passwords from sealed Secret
       → user→role bindings applied
```

ArgoCD's automated sync handles the dependency: postgres + operator sync in parallel; the keycloak Application's `Keycloak` CR sits in `Pending` until the operator's CRDs exist; the Tofu Job's `until wget` loop waits for the Keycloak HTTP endpoint to respond.

### Hostname strategy

Keycloak validates redirect URIs against the configured hostname. For a demo where `<node-ip>` varies per AKS deploy, hardcoding a hostname is brittle. We set:

```yaml
spec:
  hostname:
    strict: false
    backchannelDynamic: true
  proxy:
    headers: xforwarded
```

`hostname.strict: false` makes Keycloak accept any incoming `Host:` header and use it for self-generated URLs; `proxy.headers: xforwarded` honors `X-Forwarded-Host` if a proxy ever sits in front. Net effect: the demo just works at whatever `<node-ip>:30900` the cluster spits out, with no per-cluster tweaking. Trade-off: not safe for public exposure (open-redirect risk); fine for demo on a private cluster.

## Components

### 1. `infrastructure/keycloak-manifests/keycloak.yaml` — Keycloak CR

```yaml
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
      name: postgresql-keycloak
      key: username
    passwordSecret:
      name: postgresql-keycloak
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
```

The Operator creates a Service named `keycloak-service` on port `8080` automatically.

### 2. `infrastructure/keycloak-manifests/nodeport.yaml` — sibling NodePort

```yaml
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
```

NodePort `30900` is unused in this repo (existing: 30000 / 30080 / 30088 / 30181 / 30585 / 30601).

### 3. `infrastructure/keycloak-manifests/configure-keycloak.yaml` — Tofu Job + ConfigMap

```yaml
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
      url           = "http://keycloak.platform.svc.cluster.local:8080"
      realm         = "master"
      initial_login = false
    }

    variable "keycloak_admin_username" { type = string }
    variable "keycloak_admin_password" { type = string, sensitive = true }
    variable "demo_admin_password"    { type = string, sensitive = true }
    variable "demo_user_password"     { type = string, sensitive = true }

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
```

The Tofu provider's URL targets the cluster-internal Service (no NodePort needed for in-cluster use). The Job runs in `platform` namespace so it has DNS resolution for `keycloak-service.platform.svc...`.

### 4. ArgoCD Applications (under `infrastructure/`)

- **`infrastructure/keycloak-operator.yaml`** — Source: raw upstream YAML manifests at `https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/<tag>/kubernetes/`. `directory.recurse: false`. Two manifests applied: the CRD bundle and the operator Deployment. Pin a specific tag (e.g. `26.0.7`).
- **`infrastructure/postgres-keycloak.yaml`** — Multi-source: Bitnami `postgresql` chart at `oci://registry-1.docker.io/bitnamicharts` + manifest dir `infrastructure/postgres-keycloak-manifests/` (holds the sealed Secret). Release name `postgresql-keycloak`, namespace `shared`. Helm values reference `existingSecret: postgresql-keycloak` so the Postgres pod consumes the sealed creds at startup.
- **`infrastructure/keycloak.yaml`** — Forgejo manifest source pointing at `infrastructure/keycloak-manifests/`. Namespace `platform`. `selfHeal: true / prune: true`.

All three are added to `infrastructure/stack.yaml` so `stackablectl` applies them at `just deploy`. Same wiring as the existing Forgejo + ArgoCD bootstrap chain.

## Sealed secrets

Three plaintext sources under `secrets/manifests/keycloak-manifests/`, sealed via `just seal-secrets` into `platform/manifests/keycloak-manifests/sealed-*.yaml`:

| File | Keys | Consumer |
|---|---|---|
| `postgresql-keycloak.yaml` | `username`, `password`, `postgres-password` | Postgres bootstrap + Keycloak CR |
| `keycloak-bootstrap-admin.yaml` | `username`, `password` | Keycloak Operator (master admin) + Tofu Job (admin login) |
| `keycloak-demo-passwords.yaml` | `demo_admin_password`, `demo_user_password` | Tofu Job (initial user passwords) |

All three need to be in `platform/manifests/keycloak-manifests/` (same dir, sealed-secrets controller is cluster-wide-scoped per repo convention).

**Password generation** (one-time at implementation):

```bash
for k in postgresql-keycloak-user postgresql-keycloak-postgres keycloak-admin demo-admin demo-user; do
  echo "$k=$(openssl rand -base64 24)"
done
```

Save the values out-of-band (e.g. password manager); only the sealed copies stay in the repo.

## Landing page

Add to the **Platform Deployment** table (Keycloak is infrastructure, not a Stackable component):

```markdown
| **Keycloak** | [http://{{ nodeport "keycloak-nodeport" }}/realms/stackable-demo/account/](http://{{ nodeport "keycloak-nodeport" }}/realms/stackable-demo/account/) | `demo-admin` / `demo-user` | *(see keycloak-demo-passwords secret)* | — |
```

The master-admin console is at `http://<node>:30900/admin/` for ops use; not surfaced on the landing page.

## Verification

After the implementation lands and is pushed to `main`:

```bash
# 1. All three Applications healthy
for app in postgres-keycloak keycloak-operator keycloak; do
  kubectl -n deployment get application $app \
    -o jsonpath='{.metadata.name}: {.status.sync.status}/{.status.health.status}{"\n"}'
done
# expect: all three Synced/Healthy

# 2. Keycloak CR reconciled, pod up
kubectl -n platform get keycloak keycloak \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}'
# expect: True

# 3. Configure Job ran successfully
kubectl -n platform logs job/configure-keycloak --tail=30
# expect: "Apply complete! Resources: 7 added" (1 realm + 2 roles + 2 users + 2 user_roles)

# 4. Master admin token works
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
ADMIN_PW=$(kubectl -n platform get secret keycloak-bootstrap-admin -o jsonpath='{.data.password}' | base64 -d)
curl -s -X POST "http://$NODE_IP:30900/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli&grant_type=password&username=admin&password=$ADMIN_PW" \
  | python3 -c "import json,sys; t=json.load(sys.stdin); print('admin token len:', len(t['access_token']))"
# expect: a non-empty token length

# 5. demo-admin can log in to the realm
DEMO_PW=$(kubectl -n platform get secret keycloak-demo-passwords -o jsonpath='{.data.demo_admin_password}' | base64 -d)
curl -s -X POST "http://$NODE_IP:30900/realms/stackable-demo/protocol/openid-connect/token" \
  -d "client_id=admin-cli&grant_type=password&username=demo-admin&password=$DEMO_PW"
# expect: JSON with access_token / refresh_token

# 6. Browser smoke
# Open http://<NODE_IP>:30900/realms/stackable-demo/account/
# Log in as demo-admin or demo-user. Profile screen renders.
```

**Re-running Tofu** after editing `keycloak.tf` to add a user/role:

```bash
git push origin main
# ArgoCD picks up the ConfigMap content change; Replace=true on the Job triggers a delete+recreate
kubectl -n platform logs job/configure-keycloak --follow
# expect: "Apply complete! Resources: 1 added" (or "0 added, 1 changed")
```

## Adding a user later (extension example)

To add a user `alice` with the `viewer` role, edit the inline `keycloak.tf` in `configure-keycloak.yaml`:

```hcl
variable "alice_password" { type = string, sensitive = true }

resource "keycloak_user" "alice" {
  realm_id       = keycloak_realm.stackable_demo.id
  username       = "alice"
  email          = "alice@stackable.demo"
  enabled        = true
  email_verified = true
  initial_password { value = var.alice_password, temporary = false }
}

resource "keycloak_user_roles" "alice_roles" {
  realm_id = keycloak_realm.stackable_demo.id
  user_id  = keycloak_user.alice.id
  role_ids = [keycloak_role.viewer.id]
}
```

Plus a new entry in the `keycloak-demo-passwords` plaintext Secret, re-sealed via `just seal-secrets`, plus a new `valueFrom.secretKeyRef` env entry on the Job to expose `TF_VAR_alice_password`. One commit, push, ArgoCD re-runs the Job.

## Error handling / known operational caveats

| Situation | Behavior |
|---|---|
| Postgres not yet ready when Keycloak CR reconciles | Operator retries; Keycloak Pod stays in `Init`/`Pending` until DB reachable. ArgoCD shows Application Progressing. |
| Tofu Job runs before Keycloak HTTP is ready | `until wget ...` loop polls every 5 s; eventually proceeds when `/realms/master` returns 200. |
| `keycloak.tf` syntax error | Tofu Job fails; the Job's exit code surfaces as ArgoCD Application Degraded. Fix the `.tf`, push, ArgoCD re-runs (Replace=true). |
| Tofu can't reach Keycloak (DNS failure, etc.) | Same — Job fails loudly, retry on push. |
| Manual change in Keycloak UI (e.g. someone deletes a user) | Tofu re-applies on next sync and recreates the missing resources (declarative converge). |
| Hostname `strict: false` exposes redirect risk | Acceptable for demo; hardening is a follow-up if Keycloak ever needs to be public-facing. |
| `bootstrapAdmin` Secret rotation | Operator only consumes it on first deploy; later rotations don't take effect. To rotate, change the password via the Keycloak admin console (and update the sealed Secret to keep them aligned). |

## Patterns reused from the existing repo

- **Tofu Job + ConfigMap-with-inline-`.tf`** — direct mirror of `infrastructure/forgejo-manifests/configure-forgejo.yaml`.
- **Bitnami Postgres per-app instance** — mirrors `infrastructure/postgres-airflow-manifests/` etc. (existing repo pattern of one Postgres per consumer in the `shared` namespace).
- **NodePort sibling Service** — mirrors `forgejo-http-nodeport`, `argocd-server-nodeport`, `openmetadata-nodeport`, `opensearch-dashboards-nodeport`.
- **Sealed-secret flow** (plaintext under `secrets/manifests/<component>/`, sealed under `platform/manifests/<component>/`) — mirrors `secrets/manifests/opensearch/*` etc.
- **`infrastructure/`-level `Application`s** — three more entries alongside the existing ArgoCD/Forgejo/SealedSecrets bootstrap chain.

## Open items

None blocking. The hostname strategy and stateless-Tofu trade-offs are explicit and acceptable for demo scope. Sub-projects B–E build on this foundation; each is a separate spec/plan/PR cycle.
