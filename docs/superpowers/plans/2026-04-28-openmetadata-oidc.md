# OpenMetadata OIDC SSO — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire OpenMetadata to Keycloak so `demo-admin` / `demo-user` log in via SSO. `demo-admin` is the OpenMetadata admin principal; `demo-user` registers as a regular user.

**Architecture:** OpenMetadata is a Helm chart, not Stackable-managed. OIDC is configured entirely through environment variables on the chart's deployment. Static values are baked into the existing `valuesObject` in `platform/applications/openmetadata.yaml`; the dynamic `<NODE_IP>` value is injected by sourcing env vars from the existing `oidc-endpoints` ConfigMap (extraEnvs valueFrom). The shared `configure-stackable-oidc` Job is **not** involved — OpenMetadata isn't a Stackable-managed product.

**Tech Stack:** OpenMetadata 1.12.x, Helm chart `openmetadata`, env-from-ConfigMap, Keycloak.

**Spec reference:** `docs/superpowers/specs/2026-04-28-stackable-stack-oidc-design.md` § "Per-product specifics → OpenMetadata".

**Depends on:** `2026-04-28-stack-oidc-prerequisites.md` (the `openmetadata` Keycloak client must exist).

---

## File structure

| File | Change |
| --- | --- |
| `platform/applications/openmetadata.yaml` | Add OIDC env vars to the chart's `valuesObject.openmetadata.config.authentication`, `.authorizer`, and (where dynamic values are needed) `extraEnvs` with `valueFrom.configMapKeyRef` against `oidc-endpoints`. |
| `website/index.md` | Update the OpenMetadata row credentials to `demo-admin` / `demo-user`. |

OpenMetadata's NodePort is already pinned at `30585` in `platform/manifests/openmetadata/openmetadata-nodeport.yaml`, so the public URL is `http://<NODE_IP>:30585`. The `<NODE_IP>` value comes from the `node-ip` key in the `oidc-endpoints` ConfigMap — but Helm `valuesObject` is a static structure, so we can't string-format that into a single env var declaratively. The pragmatic shape: pass `NODE_IP` and `ISSUER_URL` as env vars sourced from the ConfigMap, then construct `AUTHENTICATION_AUTHORITY` and `AUTHENTICATION_CALLBACK_URL` as Pod-side env-var-references using `$(VAR)` Kubernetes substitution, which the kubelet expands at container start.

---

## Task 1: Add OIDC config to OpenMetadata Helm valuesObject

**Files:**
- Modify: `platform/applications/openmetadata.yaml`

The current `valuesObject.openmetadata.config` block has elasticsearch, database, deployPipelinesConfig, and pipelineServiceClientConfig sections. Add `authentication` and `authorizer` sections, plus an `extraEnvs` block that sources the dynamic values.

- [ ] **Step 1: Add `extraEnvs` for the dynamic values**

In `platform/applications/openmetadata.yaml`, locate the block (around lines 22-61):

```yaml
          openmetadata:
            config:
              elasticsearch:
                ...
              database:
                ...
              deployPipelinesConfig:
                enabled: false
              pipelineServiceClientConfig:
                ...
          extraInitContainers:
            ...
```

Inside the `openmetadata:` block, alongside `config:` (and before `extraInitContainers:`), add an `extraEnvs` block:

```yaml
          openmetadata:
            config:
              ...  (existing content unchanged)
            extraEnvs:
              - name: NODE_IP
                valueFrom:
                  configMapKeyRef:
                    name: oidc-endpoints
                    key: node-ip
              - name: ISSUER_URL
                valueFrom:
                  configMapKeyRef:
                    name: oidc-endpoints
                    key: issuer-url
```

(The chart maps `extraEnvs` into the deployment's `env:` block.)

- [ ] **Step 2: Add the authentication block to `openmetadata.config`**

Inside `openmetadata.config`, after the existing `pipelineServiceClientConfig` block, add:

```yaml
              authentication:
                provider: "custom-oidc"
                publicKeys:
                  - "$(ISSUER_URL)/protocol/openid-connect/certs"
                authority: "$(ISSUER_URL)"
                clientId: "openmetadata"
                callbackUrl: "http://$(NODE_IP):30585/callback"
              authorizer:
                className: "org.openmetadata.service.security.DefaultAuthorizer"
                containerRequestFilter: "org.openmetadata.service.security.JwtFilter"
                initialAdmins:
                  - "demo-admin"
                principalDomain: "stackable.demo"
```

The `$(VAR)` references work because Kubernetes performs env-var substitution on env-var values that match `$(NAME)` where `NAME` is another env var defined earlier in the same container. The OpenMetadata Helm chart maps `openmetadata.config.authentication.authority` (and friends) into env vars `AUTHENTICATION_AUTHORITY` etc., where Kubernetes' substitution applies.

- [ ] **Step 3: Verify YAML still parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('platform/applications/openmetadata.yaml')))"`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add platform/applications/openmetadata.yaml
git commit -m "openmetadata-oidc: configure custom-oidc authentication via Keycloak"
```

---

## Task 2: Update landing page OpenMetadata row

**Files:**
- Modify: `website/index.md`

- [ ] **Step 1: Update the row**

In `website/index.md`, locate the OpenMetadata row (in the External Componens table):

```
| **OpenMetadata**          | [http://{{ nodeport "openmetadata-nodeport" }}/](http://{{ nodeport "openmetadata-nodeport" }}/) | `admin@open-metadata.org` | `admin` | —                                                                                            |
```

Change to:

```
| **OpenMetadata**          | [http://{{ nodeport "openmetadata-nodeport" }}/](http://{{ nodeport "openmetadata-nodeport" }}/) | `demo-admin` / `demo-user` | *(see keycloak-demo-passwords secret)* | —                                                                                            |
```

(OpenMetadata auto-redirects unauthenticated visits to its `/callback` — no explicit OIDC trigger path needed in the link.)

- [ ] **Step 2: Commit**

```bash
git add website/index.md
git commit -m "openmetadata-oidc: update landing page credentials to SSO"
```

---

## Manual verification (after deploy)

1. Open `http://<NODE_IP>:30585/` in incognito.
2. OpenMetadata 302s to Keycloak (or silent SSO from active session).
3. Log in as `demo-admin`. Land on OpenMetadata logged in.
4. User menu top-right shows `demo-admin`. Admin Center / Settings → Users page accessible.
5. Log out (OpenMetadata's Logout in user menu), log in as `demo-user`. Land logged in. Settings → Users may be visible but write actions denied.

Diagnostic:

- `kubectl -n platform get deploy openmetadata -o yaml | grep -A 2 -E "AUTHENTICATION_|AUTHORIZER_"` — confirm env vars are populated. The `$(NODE_IP)` and `$(ISSUER_URL)` placeholders should be resolved when read from inside the container (`kubectl exec ... -- env | grep -E "AUTHENTICATION_|AUTHORIZER_"` to see the rendered values).
- OpenMetadata logs: `kubectl -n platform logs deploy/openmetadata --tail=300 | grep -iE "oidc|jwt|auth"`.
- If `demo-admin` doesn't get admin privileges: confirm `AUTHORIZER_INITIAL_ADMINS` resolved correctly. Per OpenMetadata docs, the initial admin list takes effect only on first user creation; deleting the user record in OpenMetadata's DB and re-logging-in is the workaround.
