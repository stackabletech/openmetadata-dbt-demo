# Vector Log Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable cluster-wide log collection for Stackable-managed components: deploy a Vector aggregator Deployment, provision scoped OpenSearch users (`openmetadata`, `logs`), migrate OpenMetadata off the OpenSearch admin user, and turn on the Vector sidecar on every Stackable CRD in the demo so logs flow into OpenSearch.

**Architecture:** Each Stackable product pod runs a Vector sidecar (injected by the operator when `logging.enableVectorAgent: true`) that tails container logs and ships them over Vector protocol to a central `vector-aggregator` Deployment listening on `:6000`. The aggregator applies no transforms and forwards to the existing OpenSearch cluster via the `elasticsearch` sink (daily indices `logs-YYYY.MM.DD`) and to a `console` sink for live tail via `kubectl logs`. Authentication uses a dedicated least-privilege `logs` user.

**Tech Stack:** Kubernetes, ArgoCD, Sealed Secrets, Vector Helm chart (`https://helm.vector.dev`, v0.44.0), OpenSearch security plugin (internal users / roles / role-mappings), Stackable operators (Airflow, HDFS, Hive, Kafka, NiFi, OpenSearch, Superset, Trino, ZooKeeper).

**Spec:** `docs/superpowers/specs/2026-04-24-vector-log-aggregation-design.md`

**File structure:**

| Path | Action | Responsibility |
|---|---|---|
| `platform/manifests/opensearch/opensearch-security-config.yaml` | Modify | Add `openmetadata` and `logs` users, add `openmetadata_app` and `logs_writer` roles, add two role mappings |
| `secrets/manifests/opensearch/opensearch-openmetadata-credentials.yaml` | Create | Plaintext creds for OpenMetadata's new OpenSearch user |
| `secrets/manifests/opensearch/opensearch-logs-credentials.yaml` | Create | Plaintext creds for Vector aggregator's OpenSearch auth |
| `platform/manifests/opensearch/sealed-opensearch-openmetadata-credentials.yaml` | Create (generated) | Sealed version of the above |
| `platform/manifests/opensearch/sealed-opensearch-logs-credentials.yaml` | Create (generated) | Sealed version of the above |
| `platform/applications/openmetadata.yaml` | Modify | Switch `auth.username` and `secretRef` to the new dedicated user |
| `platform/manifests/vector-aggregator/discovery.yaml` | Create | Discovery ConfigMap `vector-aggregator-discovery` with `ADDRESS` key |
| `platform/applications/vector-aggregator.yaml` | Create | ArgoCD Application wiring the Vector Helm chart + discovery dir |
| `platform/manifests/{airflow,hdfs,hive,hive-iceberg,kafka,nifi,opensearch,superset,trino,zookeeper}/*.yaml` | Modify (10 files) | Add `clusterConfig.vectorAggregatorConfigMapName` + per-role `logging.enableVectorAgent: true` |
| `platform/manifests/openmetadata/sealed-openmetadata-opensearch-credentials.yaml` | Delete (Task 7, follow-up) | Old admin-user secret no longer referenced |

**Branch strategy:** Work on a feature branch `vector-logs` (matches the pattern established in prior specs); merge + push to `main` only after the in-cluster end-to-end verification (Task 6) passes.

**Testing approach:** No unit tests (YAML and infra only). Each commit is verified by (a) `yaml.safe_load_all` before commit and (b) in-cluster observation after push (pod status, logs, HTTP probes). The plan bakes both into each task.

**Note for the implementer:** Several commands include placeholder values that must be filled in *by the engineer running the implementation*, not left as `<...>` in the final files:

- `<OM_PASSWORD_PLAINTEXT>`, `<LOGS_PASSWORD_PLAINTEXT>` — generated fresh via `openssl rand -base64 24`.
- `<OM_PASSWORD_BCRYPT>`, `<LOGS_PASSWORD_BCRYPT>` — bcrypt hash of the plaintext, via `htpasswd -bnBC 10 "" "$PW" | tr -d ':\n'`.

Generate once at the start of Task 1 and retain in a scratch file outside the repo until both Tasks 1 and 2 are committed, then discard.

---

## Task 1: OpenSearch security reconfig — two new users with scoped roles

**Files:**
- Modify: `platform/manifests/opensearch/opensearch-security-config.yaml`

- [ ] **Step 1: Generate two fresh passwords and their bcrypt hashes**

Run in a scratch shell (do NOT commit the plaintext values to the repo yet — they go into the plaintext Secrets in Task 2):

```bash
OM_PW=$(openssl rand -base64 24)
LOGS_PW=$(openssl rand -base64 24)
OM_HASH=$(htpasswd -bnBC 10 "" "$OM_PW" | tr -d ':\n')
LOGS_HASH=$(htpasswd -bnBC 10 "" "$LOGS_PW" | tr -d ':\n')
cat <<EOF > /tmp/vector-logs-creds.txt
OM_PW=$OM_PW
LOGS_PW=$LOGS_PW
OM_HASH=$OM_HASH
LOGS_HASH=$LOGS_HASH
EOF
cat /tmp/vector-logs-creds.txt
```

Save the output somewhere you'll still have access to during Task 2. The hashes will be pasted into the security config in this task; the plaintexts will be written into `secrets/manifests/` in Task 2.

- [ ] **Step 2: Replace the entire `stringData:` block of `opensearch-security-config.yaml`**

Open `platform/manifests/opensearch/opensearch-security-config.yaml` and replace the existing `stringData:` block (everything from `stringData:` through the end of the file) with:

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
      backend_roles:
      - admin
      description: OpenSearch admin user
    kibanaserver:
      hash: $2y$10$vPgQ/6ilKDM5utawBqxoR.7euhVQ0qeGl8mPTeKhmFT475WUDrfQS
      reserved: true
      description: OpenSearch Dashboards user
    openmetadata:
      hash: <OM_PASSWORD_BCRYPT>
      backend_roles:
      - openmetadata
      description: OpenMetadata search backend
    logs:
      hash: <LOGS_PASSWORD_BCRYPT>
      backend_roles:
      - logs
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
      backend_roles:
      - admin
    kibana_server:
      reserved: true
      users:
      - kibanaserver
    openmetadata_app:
      backend_roles:
      - openmetadata
    logs_writer:
      backend_roles:
      - logs
```

Now substitute the real bcrypt hashes:

```bash
sed -i "s#<OM_PASSWORD_BCRYPT>#$OM_HASH#" platform/manifests/opensearch/opensearch-security-config.yaml
sed -i "s#<LOGS_PASSWORD_BCRYPT>#$LOGS_HASH#" platform/manifests/opensearch/opensearch-security-config.yaml
```

Verify no `<...>` placeholders remain:

```bash
grep -E '<.*_BCRYPT>' platform/manifests/opensearch/opensearch-security-config.yaml
```

Expected: no output.

- [ ] **Step 3: Validate the YAML parses**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/opensearch/opensearch-security-config.yaml')))"
```

Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add platform/manifests/opensearch/opensearch-security-config.yaml
git commit -m "opensearch: add openmetadata + logs users with scoped roles"
```

---

## Task 2: Plaintext + sealed credential secrets

**Files:**
- Create: `secrets/manifests/opensearch/opensearch-openmetadata-credentials.yaml`
- Create: `secrets/manifests/opensearch/opensearch-logs-credentials.yaml`
- Create (generated by `just seal-secrets`): `platform/manifests/opensearch/sealed-opensearch-openmetadata-credentials.yaml`
- Create (generated by `just seal-secrets`): `platform/manifests/opensearch/sealed-opensearch-logs-credentials.yaml`

- [ ] **Step 1: Create the plaintext Secret for OpenMetadata**

```bash
mkdir -p secrets/manifests/opensearch
cat > secrets/manifests/opensearch/opensearch-openmetadata-credentials.yaml <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: opensearch-openmetadata-credentials
  namespace: platform
stringData:
  username: openmetadata
  password: $OM_PW
EOF
```

- [ ] **Step 2: Create the plaintext Secret for the Vector aggregator**

Note the key names are `OPENSEARCH_USER` / `OPENSEARCH_PASSWORD` (uppercase, underscore-separated) so they substitute directly into the Vector config's `${OPENSEARCH_USER}` / `${OPENSEARCH_PASSWORD}` interpolation when loaded via `envFrom.secretRef`:

```bash
cat > secrets/manifests/opensearch/opensearch-logs-credentials.yaml <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: opensearch-logs-credentials
  namespace: platform
stringData:
  OPENSEARCH_USER: logs
  OPENSEARCH_PASSWORD: $LOGS_PW
EOF
```

- [ ] **Step 3: Validate both plaintext Secrets parse as YAML**

```bash
for f in secrets/manifests/opensearch/opensearch-openmetadata-credentials.yaml secrets/manifests/opensearch/opensearch-logs-credentials.yaml; do
  python -c "import yaml; list(yaml.safe_load_all(open('$f')))" && echo "OK: $f"
done
```

Expected: two lines of `OK: ...`.

- [ ] **Step 4: Seal both Secrets**

The `just seal-secrets` recipe walks `secrets/` and produces sealed outputs under `platform/manifests/` for any file not already sealed. Pre-requisite: `kubectl` context points at the target cluster (where the SealedSecrets controller with the cluster-wide key is running).

```bash
just seal-secrets
```

Expected: two "Processing: ... -> ... sealed-opensearch-openmetadata-credentials.yaml" / "... sealed-opensearch-logs-credentials.yaml" lines, no errors.

- [ ] **Step 5: Verify both sealed files exist**

```bash
ls -la platform/manifests/opensearch/sealed-opensearch-openmetadata-credentials.yaml \
       platform/manifests/opensearch/sealed-opensearch-logs-credentials.yaml
```

Expected: both files present, non-zero size.

- [ ] **Step 6: Commit**

```bash
git add secrets/manifests/opensearch/opensearch-openmetadata-credentials.yaml \
        secrets/manifests/opensearch/opensearch-logs-credentials.yaml \
        platform/manifests/opensearch/sealed-opensearch-openmetadata-credentials.yaml \
        platform/manifests/opensearch/sealed-opensearch-logs-credentials.yaml
git commit -m "opensearch: seal credentials for openmetadata + logs users"
```

---

## Task 3: Migrate OpenMetadata to the dedicated user

**Files:**
- Modify: `platform/applications/openmetadata.yaml`

- [ ] **Step 1: Read the current `auth:` block**

The Application's `sources[0].helm.valuesObject.openmetadata.config.elasticsearch.auth` block currently reads:

```yaml
auth:
  enabled: true
  username: "admin"
  password:
    secretRef: openmetadata-opensearch-credentials
    secretKey: openmetadata-elasticsearch-password
```

- [ ] **Step 2: Replace it with the new user**

Edit `platform/applications/openmetadata.yaml` and replace those five lines with:

```yaml
auth:
  enabled: true
  username: "openmetadata"
  password:
    secretRef: opensearch-openmetadata-credentials
    secretKey: password
```

- [ ] **Step 3: Validate the YAML parses**

```bash
python -c "import yaml; list(yaml.safe_load_all(open('platform/applications/openmetadata.yaml')))"
```

Expected: no output, exit 0.

- [ ] **Step 4: Confirm no stray references to the old secret remain in the Application**

```bash
grep -n "openmetadata-opensearch-credentials\|openmetadata-elasticsearch-password" platform/applications/openmetadata.yaml
```

Expected: no matches. The old sealed secret file still exists under `platform/manifests/openmetadata/` — it will be deleted in Task 7 after the soak period.

- [ ] **Step 5: Commit**

```bash
git add platform/applications/openmetadata.yaml
git commit -m "openmetadata: switch OpenSearch auth to dedicated scoped user"
```

---

## Task 4: Deploy the Vector aggregator

**Files:**
- Create: `platform/manifests/vector-aggregator/discovery.yaml`
- Create: `platform/applications/vector-aggregator.yaml`

- [ ] **Step 1: Create the discovery ConfigMap**

```bash
mkdir -p platform/manifests/vector-aggregator
cat > platform/manifests/vector-aggregator/discovery.yaml <<'EOF'
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: vector-aggregator-discovery
  namespace: platform
data:
  ADDRESS: "vector-aggregator.platform.svc.cluster.local:6000"
EOF
```

- [ ] **Step 2: Create the ArgoCD Application**

```bash
cat > platform/applications/vector-aggregator.yaml <<'EOF'
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
      targetRevision: "0.44.0" # renovate: registryUrl=https://helm.vector.dev datasource=helm depName=vector
      helm:
        releaseName: vector-aggregator
        valuesObject:
          role: Aggregator
          replicas: 1
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              memory: 512Mi
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
EOF
```

- [ ] **Step 3: Validate both YAML files parse**

```bash
for f in platform/manifests/vector-aggregator/discovery.yaml platform/applications/vector-aggregator.yaml; do
  python -c "import yaml; list(yaml.safe_load_all(open('$f')))" && echo "OK: $f"
done
```

Expected: two lines of `OK: ...`.

- [ ] **Step 4: Commit**

```bash
git add platform/manifests/vector-aggregator/discovery.yaml platform/applications/vector-aggregator.yaml
git commit -m "Deploy Vector aggregator + discovery ConfigMap"
```

---

## Task 5: Enable Vector sidecar on all 10 Stackable CRDs

**Files:** Modify these 10 files; edit pattern is uniform across all of them:

1. `platform/manifests/airflow/airflow.yaml` — AirflowCluster — roles: `webservers`, `schedulers`, `triggerers`, and the special `kubernetesExecutors` block (attempt the flag there too; if the CRD schema rejects it, strip it and re-commit — see Step 13 fallback).
2. `platform/manifests/hdfs/hdfs.yaml` — HdfsCluster — roles: `nameNodes`, `dataNodes`, `journalNodes`.
3. `platform/manifests/hive/hive.yaml` — HiveCluster — role: `metastore`.
4. `platform/manifests/hive-iceberg/hive.yaml` — HiveCluster — role: `metastore`.
5. `platform/manifests/kafka/kafka.yaml` — KafkaCluster — roles: `brokers`, `controllers`.
6. `platform/manifests/nifi/nifi.yaml` — NifiCluster — role: `nodes`.
7. `platform/manifests/opensearch/opensearch.yaml` — OpenSearchCluster — role: `nodes`.
8. `platform/manifests/superset/superset.yaml` — SupersetCluster — role: `nodes`.
9. `platform/manifests/trino/trino.yaml` — TrinoCluster — roles: `coordinators`, `workers`.
10. `platform/manifests/zookeeper/zookeeper.yaml` — ZookeeperCluster — role: `servers`.

**Edit pattern** (applied to each file):

1. Add one line under the existing `spec.clusterConfig:` block:
   ```yaml
   spec:
     clusterConfig:
       vectorAggregatorConfigMapName: vector-aggregator-discovery
       # ... existing clusterConfig keys remain unchanged
   ```

2. For every role listed under the file above, find the role's `config:` block (at role level, *not* inside `roleGroups.<group>.config`) and add:
   ```yaml
   spec:
     <roleName>:
       config:
         logging:
           enableVectorAgent: true
         # ... existing role-level config keys remain unchanged
   ```

   If a role has no role-level `config:` block yet, create one at the role level containing just the `logging:` block. Do NOT add per-role-group; role-level applies to all groups.

- [ ] **Step 1: Apply the pattern to `platform/manifests/airflow/airflow.yaml`**

Insert `vectorAggregatorConfigMapName: vector-aggregator-discovery` under `spec.clusterConfig:` (immediately below the `credentialsSecret:` line, before `dagsGitSync:`).

For each of `webservers`, `schedulers`, `triggerers`: they already have `config:` blocks with `gracefulShutdownTimeout` and `resources`. Add `logging: { enableVectorAgent: true }` as the first key under `config:`.

For `kubernetesExecutors`: add an explicit `config:` block at role level (it currently only has `envOverrides` and `podOverrides`):

```yaml
  kubernetesExecutors:
    config:
      logging:
        enableVectorAgent: true
    envOverrides: *envOverrides
    podOverrides:
      # ... existing podOverrides unchanged
```

- [ ] **Step 2: Apply the pattern to `platform/manifests/hdfs/hdfs.yaml`**

Under `spec.clusterConfig:` add `vectorAggregatorConfigMapName: vector-aggregator-discovery` next to `zookeeperConfigMapName` and `dfsReplication`.

For each of `nameNodes`, `dataNodes`, `journalNodes`: add the `logging` block under the role's `config:` (creating the `config:` key if not present).

- [ ] **Step 3: Apply the pattern to `platform/manifests/hive/hive.yaml`**

Under `spec.clusterConfig:` add the line alongside `database` and `hdfs`.

For `metastore`: add the `logging` block under its `config:` (create if missing).

- [ ] **Step 4: Apply the pattern to `platform/manifests/hive-iceberg/hive.yaml`**

Same pattern as Task 5 Step 3.

- [ ] **Step 5: Apply the pattern to `platform/manifests/kafka/kafka.yaml`**

Under `spec.clusterConfig:` add the line alongside `metadataManager`.

For `brokers` and `controllers`: add the `logging` block under each role's `config:`.

- [ ] **Step 6: Apply the pattern to `platform/manifests/nifi/nifi.yaml`**

Under `spec.clusterConfig:` add the line alongside `authentication` and `sensitiveProperties`.

For `nodes`: add the `logging` block under its `config:`.

- [ ] **Step 7: Apply the pattern to `platform/manifests/opensearch/opensearch.yaml`**

Under `spec.clusterConfig:` add the line alongside `security`.

For `nodes`: add the `logging` block under its `config:`.

- [ ] **Step 8: Apply the pattern to `platform/manifests/superset/superset.yaml`**

Under `spec.clusterConfig:` add the line alongside `credentialsSecret`.

For `nodes`: add the `logging` block under its `config:`.

- [ ] **Step 9: Apply the pattern to `platform/manifests/trino/trino.yaml`**

Under `spec.clusterConfig:` add the line alongside `catalogLabelSelector`.

For `coordinators` and `workers`: add the `logging` block under each role's `config:`.

- [ ] **Step 10: Apply the pattern to `platform/manifests/zookeeper/zookeeper.yaml`**

ZookeeperCluster currently has an empty `spec.clusterConfig`; add the single line:

```yaml
spec:
  clusterConfig:
    vectorAggregatorConfigMapName: vector-aggregator-discovery
```

(If `clusterConfig:` does not exist yet, add it under `spec:`.)

For `servers`: add the `logging` block under its `config:`.

- [ ] **Step 11: Validate all 10 files parse as YAML**

```bash
for f in platform/manifests/airflow/airflow.yaml \
         platform/manifests/hdfs/hdfs.yaml \
         platform/manifests/hive/hive.yaml \
         platform/manifests/hive-iceberg/hive.yaml \
         platform/manifests/kafka/kafka.yaml \
         platform/manifests/nifi/nifi.yaml \
         platform/manifests/opensearch/opensearch.yaml \
         platform/manifests/superset/superset.yaml \
         platform/manifests/trino/trino.yaml \
         platform/manifests/zookeeper/zookeeper.yaml; do
  python -c "import yaml; list(yaml.safe_load_all(open('$f')))" && echo "OK: $f"
done
```

Expected: ten lines of `OK: ...`.

- [ ] **Step 12: Assert every file references the discovery ConfigMap**

```bash
for f in platform/manifests/airflow/airflow.yaml \
         platform/manifests/hdfs/hdfs.yaml \
         platform/manifests/hive/hive.yaml \
         platform/manifests/hive-iceberg/hive.yaml \
         platform/manifests/kafka/kafka.yaml \
         platform/manifests/nifi/nifi.yaml \
         platform/manifests/opensearch/opensearch.yaml \
         platform/manifests/superset/superset.yaml \
         platform/manifests/trino/trino.yaml \
         platform/manifests/zookeeper/zookeeper.yaml; do
  grep -q "vectorAggregatorConfigMapName: vector-aggregator-discovery" "$f" && echo "OK: $f" || echo "MISSING: $f"
done
```

Expected: ten lines of `OK: ...`, no `MISSING`.

- [ ] **Step 13: Assert every `enableVectorAgent: true` insertion is present**

Count of `enableVectorAgent: true` occurrences should match the expected role total:

```bash
grep -rc "enableVectorAgent: true" platform/manifests/
```

Expected total across all files: at least 17 (3 airflow + 1 kubernetesExecutors + 3 hdfs + 1 hive + 1 hive-iceberg + 2 kafka + 1 nifi + 1 opensearch + 1 superset + 2 trino + 1 zookeeper). If the count is 16, the `kubernetesExecutors` flag was omitted (schema may have rejected it). Proceed anyway; the 16 roles that accept it will be covered.

- [ ] **Step 14: Commit**

```bash
git add platform/manifests/airflow/airflow.yaml \
        platform/manifests/hdfs/hdfs.yaml \
        platform/manifests/hive/hive.yaml \
        platform/manifests/hive-iceberg/hive.yaml \
        platform/manifests/kafka/kafka.yaml \
        platform/manifests/nifi/nifi.yaml \
        platform/manifests/opensearch/opensearch.yaml \
        platform/manifests/superset/superset.yaml \
        platform/manifests/trino/trino.yaml \
        platform/manifests/zookeeper/zookeeper.yaml
git commit -m "Enable Vector sidecar on all Stackable product CRDs"
```

---

## Task 6: Push, wait for ArgoCD, and verify end-to-end

**Files:** none — this is a cluster-side verification task.

- [ ] **Step 1: Push the feature branch**

```bash
git checkout -B vector-logs
git push -u origin vector-logs
```

(If you have been committing directly to `main` per prior workflow, push `main` instead.)

- [ ] **Step 2: Merge into main and push**

If you're on a feature branch:

```bash
git checkout main
git merge --ff-only vector-logs
git push origin main
```

- [ ] **Step 3: Rolling-restart OpenSearch to pick up the new users**

Stackable's OpenSearch operator applies `initial-opensearch-security-config` via `securityadmin.sh` at pod startup. To apply the new users, roles, and mappings:

```bash
kubectl -n platform rollout restart statefulset -l app.kubernetes.io/name=opensearch
kubectl -n platform rollout status statefulset -l app.kubernetes.io/name=opensearch --timeout=5m
```

Expected: StatefulSet rolls to Ready.

- [ ] **Step 4: Verify the new users exist in OpenSearch**

```bash
# Find an OpenSearch pod
POD=$(kubectl -n platform get pod -l app.kubernetes.io/name=opensearch -o jsonpath='{.items[0].metadata.name}')
# Use the admin user to list internal users
kubectl -n platform exec "$POD" -- curl -sk -u "admin:<admin password>" \
  "https://localhost:9200/_plugins/_security/api/internalusers/" | python -c "import json,sys;d=json.load(sys.stdin);print([k for k in d.keys() if not k.startswith('_')])"
```

Expected: list includes `admin`, `kibanaserver`, `openmetadata`, `logs`.

(The admin password is bcrypted to `$2y$10$xRtHZFJ9QhG...`. If you don't have the plaintext, find it in the cluster's bootstrap Secret or accept that this check isn't strictly required — Step 5's OpenMetadata restart will fail loudly if the new user isn't working.)

- [ ] **Step 5: Verify OpenMetadata still works with the new user**

ArgoCD should have already redeployed the OpenMetadata pod after the Application change. Confirm:

```bash
kubectl -n platform rollout status deployment/openmetadata --timeout=5m
kubectl -n platform logs deploy/openmetadata --tail=60 | grep -iE "elasticsearch|opensearch|auth"
```

Expected: startup messages show successful OpenSearch connection; no `401` / `403` / `AuthenticationException` errors. Load the OpenMetadata UI at its NodePort and run any "Explore" search — results should return.

- [ ] **Step 6: Verify the Vector aggregator Application is healthy**

```bash
kubectl -n deployment get application vector-aggregator -o jsonpath='{.status.sync.status}:{.status.health.status}'
echo
```

Expected: `Synced:Healthy`. Continue when it reaches that state (may take a minute while the Helm chart pulls).

```bash
kubectl -n platform get pods -l app.kubernetes.io/name=vector
kubectl -n platform logs -l app.kubernetes.io/name=vector --tail=30
```

Expected: pod Running, 1/1 ready. Logs show `INFO vector::sources::vector::v2: Listening... peer_addr="0.0.0.0:6000"` and no connection errors.

- [ ] **Step 7: Verify a representative product pod has the Vector sidecar**

```bash
# Pick any Trino coordinator pod
POD=$(kubectl -n platform get pod -l app.kubernetes.io/name=trino,app.kubernetes.io/component=coordinator -o jsonpath='{.items[0].metadata.name}')
kubectl -n platform get pod "$POD" -o jsonpath='{.spec.containers[*].name}'
echo
```

Expected: the container list includes `vector`.

```bash
# Sidecar logs
kubectl -n platform logs "$POD" -c vector --tail=30
```

Expected: Vector sidecar started; events being forwarded to the aggregator.

- [ ] **Step 8: Verify the aggregator's console sink is emitting events**

```bash
kubectl -n platform logs -l app.kubernetes.io/name=vector --tail=30
```

Expected: JSON log lines with `kubernetes.pod_name` / `kubernetes.container_name` fields identifying which Stackable product they came from.

- [ ] **Step 9: Verify logs land in OpenSearch**

```bash
# Exec into any OpenSearch pod and hit _cat/indices
POD=$(kubectl -n platform get pod -l app.kubernetes.io/name=opensearch -o jsonpath='{.items[0].metadata.name}')
DATE=$(date +%Y.%m.%d)
kubectl -n platform exec "$POD" -- curl -sk -u "logs:$LOGS_PW" \
  "https://localhost:9200/_cat/indices/logs-${DATE}?v"
```

Expected: one index `logs-YYYY.MM.DD` with `docs.count` > 0.

```bash
# Sample a doc
kubectl -n platform exec "$POD" -- curl -sk -u "admin:<admin password>" \
  "https://localhost:9200/logs-*/_search?size=1&pretty"
```

Expected: a hit with `kubernetes.*` metadata.

- [ ] **Step 10: Confirm Airflow DAGs still run**

If the `dbt_tpch_demo` DAG is configured with `schedule="@once"`, it may have already run before this change. Unpause it (or trigger manually) and observe one run to completion; confirm no regressions from the injected Vector sidecar on the `kubernetesExecutors` pods.

```bash
kubectl -n platform get pods -l task_id=finalize_dbt_artifacts
# pick a pod, check containers
kubectl -n platform get pod <task-pod> -o jsonpath='{.spec.containers[*].name}'
```

Expected: Vector sidecar is (or isn't — depends on whether the schema accepted it on `kubernetesExecutors`) present without breaking task execution.

- [ ] **Step 11: Delete the scratch credentials file**

```bash
rm -f /tmp/vector-logs-creds.txt
```

---

## Task 7: Clean up the old OpenMetadata-admin sealed secret (follow-up, after soak)

**Files:**
- Delete: `platform/manifests/openmetadata/sealed-openmetadata-opensearch-credentials.yaml`

Do this task **only after a minimum 24-hour soak** where OpenMetadata has been running cleanly against the new `openmetadata` OpenSearch user. Removing this secret before then burns the rollback path for Task 3.

- [ ] **Step 1: Confirm no manifests reference the old secret**

```bash
grep -rn "openmetadata-opensearch-credentials\|openmetadata-elasticsearch-password" platform/ secrets/ 2>&1 | grep -v "sealed-openmetadata-opensearch-credentials.yaml"
```

Expected: no matches. (The sealed file itself will match `openmetadata-opensearch-credentials` — that's the file we're about to delete, exclude it above.)

- [ ] **Step 2: Delete the sealed secret file**

```bash
git rm platform/manifests/openmetadata/sealed-openmetadata-opensearch-credentials.yaml
```

- [ ] **Step 3: Commit**

```bash
git commit -m "Delete obsolete openmetadata-opensearch-credentials secret"
```

- [ ] **Step 4: Push and confirm ArgoCD prunes the cluster Secret**

```bash
git push origin main
# Wait for ArgoCD to reconcile (~30s)
kubectl -n platform get secret openmetadata-opensearch-credentials
```

Expected: `NotFound`. (ArgoCD's `prune: true` removes orphaned resources.)

---

## Self-Review

**Spec coverage (each spec section → task):**

- Spec §"Component 1: Vector aggregator deployment" → Task 4.
- Spec §"Component 2: OpenSearch users + secrets + OpenMetadata migration":
  - Two new users + roles + mappings → Task 1.
  - Two new sealed secrets → Task 2.
  - OpenMetadata Helm-values swap → Task 3.
  - One-time securityadmin re-run → Task 6 Step 3.
- Spec §"Component 3: Per-product CRD edits" → Task 5.
- Spec §"Ordering and deployment dance" — Task sequence 1→2→3→4→5→6→7 matches the spec's Commit A→B→C→D→E→F mapping (5 is the single commit covering all per-product edits; 6 is the verification window between E and F).
- Spec §"Verification" — Task 6, broken into 10 discrete checks matching the spec's enumerated signals.
- Spec §"Rollback" — not a task (the plan doesn't pre-emptively roll back); the spec describes the procedure if needed mid-run.
- Spec §"Known operational caveats" — bootstrap security-config behaviour covered by Task 6 Step 3; circular log dependency and single-replica aggregator trade-offs are accepted and don't require implementation work.

**Placeholder scan:** no `TBD` / `TODO` / `fill in details` / `Similar to Task N` tokens in any task. The `<OM_PASSWORD_BCRYPT>` / `<LOGS_PASSWORD_BCRYPT>` / `<admin password>` tokens are real runtime-substituted values (documented under "Note for the implementer" at the top), with explicit `sed` commands to substitute them. The `$OM_PW` / `$LOGS_PW` / `$OM_HASH` / `$LOGS_HASH` bash variables are populated by Task 1 Step 1 and reused across Task 2 Steps 1 and 2.

**Type / name consistency across tasks:**

- Secret names: `opensearch-openmetadata-credentials` (used in Task 2 Step 1, Task 3 Step 2) and `opensearch-logs-credentials` (Task 2 Step 2, Task 4 Step 2 via `extraEnvsFrom.secretRef.name`).
- OpenSearch role names: `openmetadata_app`, `logs_writer` — consistent in `roles.yml` and `roles_mapping.yml` definitions.
- OpenSearch backend-role names: `openmetadata`, `logs` — consistent between `internal_users.yml.<user>.backend_roles` and `roles_mapping.yml.<role>.backend_roles`.
- Sealed-secret filenames: `sealed-opensearch-openmetadata-credentials.yaml` / `sealed-opensearch-logs-credentials.yaml` (Task 2 Step 5 / 6).
- Discovery ConfigMap name: `vector-aggregator-discovery` — consistent across Task 4 Step 1 (definition) and Task 5's edit pattern (reference).
- Service name + port: `vector-aggregator.platform.svc.cluster.local:6000` — consistent across Task 4 Step 1 (`data.ADDRESS`) and Task 4 Step 2 (Helm-installed Service).
- Secret key names: `opensearch-openmetadata-credentials` uses `username` / `password` (Task 2 Step 1, referenced in Task 3's `secretKey: password`); `opensearch-logs-credentials` uses `OPENSEARCH_USER` / `OPENSEARCH_PASSWORD` (Task 2 Step 2, interpolated in Task 4's `customConfig.sinks.opensearch.auth`).
