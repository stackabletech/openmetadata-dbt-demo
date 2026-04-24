# Design: Cluster-wide log aggregation via Vector + OpenSearch

**Date:** 2026-04-24

## Goal

Enable cluster-wide log collection for the Stackable-managed components of this demo, using Stackable's built-in logging framework. Every Stackable-operator product pod runs a Vector sidecar that ships logs to a central Vector aggregator Deployment. The aggregator forwards to the existing OpenSearch cluster (`simple-opensearch-nodes-default` in `platform`), where logs can be queried alongside the rest of the platform state.

## Non-goals

- Log collection for non-Stackable pods (Forgejo, ArgoCD, OpenMetadata, PostgreSQL instances, GarageFS, Lakekeeper, `*-init` Jobs, `demo-landing`). Stackable's logging framework only plumbs pods whose Stackable operator injects the sidecar.
- An OpenSearch Index Lifecycle Management / retention policy. Indices will grow unbounded; a rotation policy is deferred.
- A second aggregator replica for HA or an external storage tier. Single-replica in-memory buffering is sufficient for the demo.
- A dedicated OpenSearch Dashboards / Kibana instance. Logs can be queried via the existing OpenSearch REST API or the demo-landing-exposed UI.

## Scope at a glance

This spec bundles three coupled changes that need to ship together:

1. Deploy the Vector aggregator and discovery ConfigMap.
2. Provision two new OpenSearch users (`openmetadata`, `logs`) with scoped roles, generate sealed secrets, and migrate OpenMetadata off the `admin` user.
3. Turn on the Vector sidecar for every Stackable-operator CRD in the demo (10 files, ~20 per-role additions).

They are coupled because: step 1 requires creds from step 2 to authenticate to OpenSearch; step 3 requires step 1 to be live before the sidecars have somewhere to send events. Splitting would mean staging three half-working deploys in sequence. One spec, one merge.

## Architecture

Three new components, all in the `platform` namespace:

1. **Vector aggregator Deployment** — single replica running the Vector image in `role: Aggregator`. Listens on `0.0.0.0:6000` for incoming log events from Stackable-injected sidecars. Forwards to OpenSearch via the `elasticsearch` sink, and in parallel to a `console` sink for debug tail via `kubectl logs`. Basic-auth credentials injected from the `opensearch-logs-credentials` sealed secret via `envFrom`; TLS verification disabled (OpenSearch uses a self-signed CA).
2. **Discovery ConfigMap** `vector-aggregator-discovery` — single key `ADDRESS=vector-aggregator.platform.svc.cluster.local:6000`. Every Stackable CRD references this ConfigMap by name in `spec.clusterConfig.vectorAggregatorConfigMapName`; each Stackable operator wires the address into its sidecars.
3. **Vector ClusterIP Service** `vector-aggregator:6000` — exposed by the Helm chart; what the sidecars actually connect to.

### Data flow

```
product container  →  stdout  →  shared /stackable/log volume
                                     ↓  read by
                        vector sidecar (role: Agent, injected by Stackable operator)
                                     ↓  Vector protocol, :6000
                        vector-aggregator Service (ClusterIP)
                                     ↓  vector-aggregator Deployment
                                     ├──  console sink  (kubectl logs tail)
                                     └──  elasticsearch sink  →  OpenSearch logs-YYYY.MM.DD
```

The shared volume and file-tailing wiring are set up by the Stackable operator automatically when `logging.enableVectorAgent: true` is set on a role. Sidecars buffer events in memory if the aggregator is unavailable; on aggregator restart the backlog flushes.

## Component 1: Vector aggregator deployment

Deployed via the upstream Vector Helm chart (repo `https://helm.vector.dev`), consistent with how Forgejo, OpenMetadata, and Postgres instances are wired in this repo.

### Files

- **`platform/applications/vector-aggregator.yaml`** — new ArgoCD Application, multi-source: one source for the Helm chart, one for the Forgejo manifests dir.
- **`platform/manifests/vector-aggregator/discovery.yaml`** — the ConfigMap.

### Application manifest

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: vector-aggregator
  namespace: deployment
spec:
  project: dbt-openmetadata-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: platform
  sources:
    - repoURL: "https://helm.vector.dev"
      chart: vector
      targetRevision: "0.44.0"  # renovate: registryUrl=https://helm.vector.dev datasource=helm depName=vector
      helm:
        releaseName: vector-aggregator
        valuesObject:
          role: Aggregator
          replicas: 1
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits:   { memory: 512Mi }
          extraEnvsFrom:
            - secretRef:
                name: opensearch-logs-credentials
          customConfig:
            data_dir: /vector-data-dir
            api:
              enabled: true
              address: 127.0.0.1:8686
            sources:
              vector:
                type: vector
                address: 0.0.0.0:6000
                version: "2"
            sinks:
              console:
                type: console
                inputs: [vector]
                encoding:
                  codec: json
              opensearch:
                type: elasticsearch
                inputs: [vector]
                endpoint: "https://simple-opensearch-nodes-default.platform.svc.cluster.local:9200"
                auth:
                  strategy: basic
                  user: "${OPENSEARCH_USER}"
                  password: "${OPENSEARCH_PASSWORD}"
                tls:
                  verify_certificate: false
                bulk:
                  index: "logs-%Y.%m.%d"
    - repoURL: "http://forgejo-http.deployment.svc.cluster.local:3000/stackable/openmetadata-dbt-demo.git"
      targetRevision: "main"
      path: platform/manifests/vector-aggregator/
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    automated:
      selfHeal: true
      prune: true
```

### Discovery ConfigMap

```yaml
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: vector-aggregator-discovery
  namespace: platform
data:
  ADDRESS: "vector-aggregator.platform.svc.cluster.local:6000"
```

The service name `vector-aggregator` is the Helm chart's default when `releaseName=vector-aggregator`.

## Component 2: OpenSearch users + secrets + OpenMetadata migration

### Two new users, least-privilege roles

**Edits to `platform/manifests/opensearch/opensearch-security-config.yaml`:** add two users, a new `roles.yml` section, and two new role mappings. End-state `stringData`:

```yaml
stringData:
  internal_users.yml: |
    ---
    _meta:
      type: internalusers
      config_version: 2
    admin:
      hash: $2y$10$xRtHZFJ9QhG9GcYhRpAGpufCZYsk//nxsuel5URh0GWEBgmiI4Q/e
      reserved: true
      backend_roles: [admin]
      description: OpenSearch admin user
    kibanaserver:
      hash: $2y$10$vPgQ/6ilKDM5utawBqxoR.7euhVQ0qeGl8mPTeKhmFT475WUDrfQS
      reserved: true
      description: OpenSearch Dashboards user
    openmetadata:
      hash: <bcrypt-hash-generated>   # htpasswd -bnBC 10 "" <pw> | tr -d ':\n'
      backend_roles: [openmetadata]
      description: OpenMetadata search backend
    logs:
      hash: <bcrypt-hash-generated>
      backend_roles: [logs]
      description: Vector aggregator log writer

  roles.yml: |
    ---
    _meta:
      type: roles
      config_version: 2
    openmetadata_app:
      cluster_permissions:
        - cluster_monitor
        - cluster_composite_ops
        - "indices:admin/template/*"
      index_permissions:
        - index_patterns:
            - "*_search_index*"
            - "entity_report_data_index*"
            - "test_case_resolution_status_search_index*"
            - "test_case_result_search_index*"
          allowed_actions: ["indices_all"]
    logs_writer:
      cluster_permissions:
        - cluster_monitor
        - cluster_composite_ops
      index_permissions:
        - index_patterns: ["logs-*"]
          allowed_actions:
            - "indices:data/write/bulk*"
            - "indices:data/write/index"
            - "indices:admin/create"
            - "indices:admin/mapping/put"
            - "indices:admin/mappings/fields/get"

  roles_mapping.yml: |
    ---
    _meta:
      type: rolesmapping
      config_version: 2
    all_access:
      reserved: false
      backend_roles: [admin]
    kibana_server:
      reserved: true
      users: [kibanaserver]
    openmetadata_app:
      backend_roles: [openmetadata]
    logs_writer:
      backend_roles: [logs]
```

**What the roles grant:**

- `openmetadata_app`: full CRUD + aliases on OM's own indices (`*_search_index*` covers default names and versioned aliases; the three explicit patterns cover OM's data-quality indices), plus `cluster_monitor` (health probe), `cluster_composite_ops` (bulk writes), and index-template admin (OM creates templates for new entity types). No access to `logs-*`, no cluster-admin, no snapshots, no security-config.
- `logs_writer`: write-only on `logs-*`. Explicitly no delete or read permissions on the logs — a compromised Vector sidecar can append events but cannot read or erase them.

### Two new sealed secrets

Plaintext originals in `secrets/manifests/opensearch/` (sealed into `platform/manifests/opensearch/` via `just seal-secrets`):

- **`opensearch-openmetadata-credentials.yaml`** — keys `username: openmetadata`, `password: <random>`. Output sealed file: `platform/manifests/opensearch/sealed-opensearch-openmetadata-credentials.yaml`.
- **`opensearch-logs-credentials.yaml`** — keys `OPENSEARCH_USER: logs`, `OPENSEARCH_PASSWORD: <random>`. Uppercase env-var-style keys because the Vector Deployment consumes them via `envFrom.secretRef` and they need to substitute into the Vector config's `${OPENSEARCH_USER}` / `${OPENSEARCH_PASSWORD}` interpolation. Output sealed file: `platform/manifests/opensearch/sealed-opensearch-logs-credentials.yaml`.

### Password generation (one-time, manual)

For each of the two new users:

```bash
# Generate a random password
PW=$(openssl rand -base64 24)
# Generate the bcrypt hash for internal_users.yml
HASH=$(htpasswd -bnBC 10 "" "$PW" | tr -d ':\n')
echo "password (goes in the plaintext secret): $PW"
echo "hash (goes in internal_users.yml):       $HASH"
```

The `htpasswd -bnBC 10 "" ... | tr -d ':\n'` pattern mirrors the existing `argocdAdminPassword` comment in `infrastructure/argo-cd.yaml`.

### OpenMetadata migration

**Edit `platform/applications/openmetadata.yaml`** (around lines 37–40 of `spec.sources[0].helm.valuesObject.openmetadata.config.elasticsearch.auth`):

```yaml
auth:
  enabled: true
  username: "openmetadata"                                     # was "admin"
  password:
    secretRef: opensearch-openmetadata-credentials             # was openmetadata-opensearch-credentials
    secretKey: password                                         # was openmetadata-elasticsearch-password
```

**Delete the old sealed secret** (after verifying OM works with the new user, in a separate follow-up commit to keep rollback path clean): `git rm platform/manifests/openmetadata/sealed-openmetadata-opensearch-credentials.yaml`.

### One-time securityadmin re-run

The Stackable OpenSearch operator applies `initial-opensearch-security-config` via `securityadmin.sh` at cluster bootstrap. Changes made to the ConfigMap after that don't propagate automatically. After the ConfigMap edit lands in-cluster:

```bash
# Restart the OpenSearch pods in a rolling fashion; the operator re-runs
# securityadmin.sh during pod startup:
kubectl -n platform rollout restart statefulset -l app.kubernetes.io/name=opensearch
```

If the rolling restart alone doesn't pick up the new users (operator-version-dependent), fall back to running `securityadmin.sh` manually inside a master pod:

```bash
kubectl -n platform exec -it <opensearch-master-pod> -- bash -c '
  /stackable/opensearch/plugins/opensearch-security/tools/securityadmin.sh \
    -cd /stackable/tmp/security \
    -h localhost \
    -cacert /stackable/tls/ca.crt \
    -cert /stackable/tls/tls.crt \
    -key /stackable/tls/tls.key
'
```

(Exact paths are operator-layout-dependent and resolved at implementation time by inspecting a running pod — the rolling-restart path above is the preferred flow; this manual invocation is only a fallback if the operator version on-cluster doesn't re-run securityadmin on pod startup.)

## Component 3: Per-product CRD edits

Ten Stackable-operator CRDs across the demo. Same edit pattern applied to each file.

### Files

1. `platform/manifests/airflow/airflow.yaml` — AirflowCluster
2. `platform/manifests/hdfs/hdfs.yaml` — HdfsCluster
3. `platform/manifests/hive/hive.yaml` — HiveCluster
4. `platform/manifests/hive-iceberg/hive.yaml` — HiveCluster
5. `platform/manifests/kafka/kafka.yaml` — KafkaCluster
6. `platform/manifests/nifi/nifi.yaml` — NifiCluster
7. `platform/manifests/opensearch/opensearch.yaml` — OpenSearchCluster
8. `platform/manifests/superset/superset.yaml` — SupersetCluster
9. `platform/manifests/trino/trino.yaml` — TrinoCluster
10. `platform/manifests/zookeeper/zookeeper.yaml` — ZookeeperCluster

### Edit pattern (identical across all 10)

**Add the discovery ConfigMap reference** at top-level `spec.clusterConfig`:

```yaml
spec:
  clusterConfig:
    vectorAggregatorConfigMapName: vector-aggregator-discovery
    # ... existing clusterConfig keys unchanged
```

**Enable the Vector Agent** on every top-level role. At role level (*not* role-group level — role-level applies to all groups), add a `logging` block under `config`:

```yaml
spec:
  <roleName>:
    config:
      # ... existing role-level config keys unchanged
      logging:
        enableVectorAgent: true
```

Role inventory (finalized in the implementation plan):

- Airflow: `webservers`, `schedulers`, `triggerers`, `kubernetesExecutors` (4)
- HDFS: `nameNodes`, `dataNodes`, `journalNodes` (3)
- Hive / Hive-iceberg: `metastore` (1 each)
- Kafka: `brokers` (and `controllers` if KRaft)
- NiFi: `nodes` (1)
- OpenSearch: `nodes` (1, may be split by node-role)
- Superset: `nodes` (1)
- Trino: `coordinators`, `workers` (2)
- ZooKeeper: `servers` (1)

Approximately 20 per-role additions + 10 clusterConfig lines. One commit.

### Airflow-specific interaction

The Airflow `kubernetesExecutors` role already adds env vars + volume mounts via `podOverrides` (for `AIRFLOW_CONN_GARAGEFS`, `DBT_TARGET_PATH`). The Vector sidecar will coexist with those pod overrides — no interaction expected, since the sidecar is a separate container injected by the operator into the pod spec after the overrides are applied. Worth eyeballing during the first DAG run post-change: the executor pod should have three containers (`base`, `vector`, and whatever Airflow adds), not two.

## Ordering and deployment dance

Order the commits so ArgoCD never sees a half-wired state:

1. **Commit A: OpenSearch security reconfig** — new users, roles, mappings.
   Push → rolling-restart OpenSearch pods → verify `admin` user still works and new users exist (`curl -sk -u admin:... /_plugins/_security/api/internalusers/`).
2. **Commit B: New sealed secrets** — `opensearch-logs-credentials`, `opensearch-openmetadata-credentials` sealed into `platform/manifests/opensearch/`.
   Push → ArgoCD provisions the Secret resources.
3. **Commit C: OpenMetadata migration** — swap `openmetadata.yaml`'s `auth.username` and `secretRef` to the new user. Do *not* delete the old sealed secret yet.
   Push → OM redeploys against the new user → verify OM UI search works.
4. **Commit D: Vector aggregator** — the Helm Application + discovery ConfigMap.
   Push → aggregator pod comes up → console logs show "vector has started" and the aggregator is waiting for sources. Nothing is shipping yet because no sidecars exist.
5. **Commit E: Per-product edits** — all 10 CRD files edited together.
   Push → each Stackable operator detects the CRD change and rolls its pods with the sidecar added → aggregator's console sink starts showing traffic → `logs-YYYY.MM.DD` index appears in OpenSearch.
6. **Commit F (follow-up, after soak period)**: `git rm` the old `sealed-openmetadata-opensearch-credentials.yaml`.

This sequencing means every intermediate state is runnable. If any step fails, revert that one commit and main is still healthy.

## Verification

Per-step checks listed inline in each commit's PR body. Summary of the key signals:

- **After D:** `kubectl -n platform get pods -l app.kubernetes.io/name=vector-aggregator` shows 1/1 ready; `kubectl logs -l app.kubernetes.io/name=vector-aggregator --tail=50` shows no errors.
- **After E on Trino (representative):** `kubectl -n platform get pod -l app.kubernetes.io/name=trino,app.kubernetes.io/component=coordinator -o jsonpath='{.items[0].spec.containers[*].name}'` includes `vector`. `kubectl logs <pod> -c vector --tail=30` shows events flowing. Aggregator console sink prints JSON events identified by `kubernetes.pod_name`.
- **OpenSearch sink success:** `curl -sk -u "logs:<password>" "https://simple-opensearch-nodes-default.platform.svc.cluster.local:9200/_cat/indices?v" | grep logs-` shows today's `logs-YYYY.MM.DD` with non-zero `docs.count`. A sample search (`curl -sk -u admin:... .../logs-*/_search?size=1&pretty`) returns events with `kubernetes.*` metadata.

### Known failure modes

| Failure | Observable | Recovery |
|---|---|---|
| Aggregator down | Sidecar logs show connect failures; product pods keep running; local sidecar buffer fills and starts dropping events | `kubectl scale deploy/vector-aggregator --replicas=1` — backlog flushes on restart |
| Wrong OpenSearch password | Aggregator logs 401s on every flush attempt | Re-seal secret, `kubectl rollout restart deploy/vector-aggregator` |
| OpenMetadata user missing a required index pattern | OM logs 403 naming the exact index; search breaks in the UI | Add the pattern to `openmetadata_app.index_permissions.index_patterns`, re-apply security config, rolling-restart OpenSearch |
| Stackable operator version too old | CRD schema rejects `vectorAggregatorConfigMapName` at apply time; ArgoCD reports OutOfSync with validation error | Verify operator version matches the Stackable release 26.3 pin in `infrastructure/stack.yaml`; the field has been stable since release 23.x so this is a theoretical concern only |

## Rollback

Revert commits in reverse order (F → E → D → C → B → A). ArgoCD reconciles back:

- **Revert E** removes the sidecars on next operator reconcile.
- **Revert D** removes the aggregator Deployment.
- **Revert C** restores OM to the `admin` user — requires the old secret (`sealed-openmetadata-opensearch-credentials.yaml`) to still exist, which is why Commit F (its deletion) is separated from Commit C by a soak period.
- **Revert B** removes the new sealed secrets.
- **Revert A** removes the new users from the ConfigMap; a rolling restart of OpenSearch pods re-applies the old config.

## Known operational caveats

- **OpenSearch security config is applied at bootstrap.** Post-bootstrap edits need a pod restart + securityadmin re-run. Captured in the Commit A and Commit E procedures above; called out separately because it's the single most common "why isn't this taking effect" question.
- **Circular dependency on OpenSearch-self-logging.** OpenSearch's own pods ship logs to OpenSearch. If OpenSearch breaks, the logs explaining why will not be ingested. Accepted trade-off for demo simplicity.
- **No ILM / retention.** Indices accumulate until manually cleaned or the disk fills. For a demo this is acceptable; a follow-up spec adds a rotation policy when needed.
- **Single aggregator replica.** A restart of the aggregator pod drops its in-memory buffer. Acceptable for demo; HA aggregator would need a persistent buffer volume and is out of scope.

## Open items

None blocking. Everything above is executable as-is; the only runtime-resolved items are the exact `securityadmin.sh` invocation paths (operator-layout-dependent) and the bcrypt hashes + plaintext passwords (generated fresh at implementation time, not committed into the spec).
