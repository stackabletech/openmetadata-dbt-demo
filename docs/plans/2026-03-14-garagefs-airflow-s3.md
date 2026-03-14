# GarageFS Init + Airflow S3 Backend — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bootstrap GarageFS (layout, API key, bucket) and reconfigure Stackable Airflow to use GarageFS as its S3 backend for remote logging.

**Architecture:** A Kubernetes Job initializes the GarageFS cluster using the `garage` CLI (layout assign, key create, bucket create), stores credentials in a K8s Secret, then Airflow's connection config is updated to point to GarageFS instead of MinIO.

**Tech Stack:** GarageFS v1.3.1 CLI, Kubernetes Job, Stackable Airflow, SealedSecrets

---

### Task 1: Create GarageFS init Job (ConfigMap + Job)

**Files:**
- Create: `platform/manifests/garagefs-init/configure-garagefs.yaml`

**Context:**
- GarageFS is deployed as a 3-node StatefulSet (garage-0, garage-1, garage-2)
- All nodes currently have "NO ROLE ASSIGNED" — layout must be assigned first
- Admin API port 3903 is NOT exposed via Service — must use `garage` CLI with RPC config
- RPC secret is in K8s Secret `garage-rpc-secret` (key: `rpcSecret`)
- Garage config template is in ConfigMap `garage-config` (key: `garage.toml`)
- Garage image: `dxflrs/amd64_garage:v1.3.1`

**Step 1: Create directory**

```bash
mkdir -p platform/manifests/garagefs-init
```

**Step 2: Write the ConfigMap + Job manifest**

Create `platform/manifests/garagefs-init/configure-garagefs.yaml`:

```yaml
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: garagefs-init-script
data:
  configure.sh: |
    #!/bin/sh
    set -e

    # Build garage config with RPC secret
    sed "s/__RPC_SECRET_REPLACE__/$RPC_SECRET/" /mnt/garage.toml > /tmp/garage.toml
    export GARAGE_CONFIG_FILE=/tmp/garage.toml

    echo "=== GarageFS Init ==="

    # Step 1: Wait for Garage API to be ready
    echo "Waiting for Garage to be ready..."
    for i in $(seq 1 60); do
      if garage status >/dev/null 2>&1; then
        echo "  Garage is ready."
        break
      fi
      echo "  Attempt $i/60 - not ready, retrying in 5s..."
      sleep 5
    done
    garage status || { echo "ERROR: Garage not ready"; exit 1; }

    # Step 2: Assign layout to all nodes (idempotent — skips already-assigned nodes)
    echo "Assigning layout to nodes..."
    for node_id in $(garage status 2>/dev/null | grep "NO ROLE ASSIGNED" | awk '{print $1}'); do
      echo "  Assigning node $node_id..."
      garage layout assign -z dc1 -c 1G "$node_id"
    done

    # Apply layout if there are staged changes
    CURRENT_VERSION=$(garage layout show 2>/dev/null | grep "Current cluster layout version:" | awk '{print $NF}')
    STAGED=$(garage layout show 2>/dev/null | grep "Staged" | head -1)
    if echo "$STAGED" | grep -q "role changes"; then
      NEXT_VERSION=$((CURRENT_VERSION + 1))
      echo "  Applying layout version $NEXT_VERSION..."
      garage layout apply --version "$NEXT_VERSION"
    else
      echo "  Layout already applied, skipping."
    fi

    echo "Layout status:"
    garage layout show

    # Step 3: Create API key (idempotent — reuse if exists)
    echo "Creating API key 'airflow'..."
    KEY_OUTPUT=$(garage key info airflow 2>/dev/null || garage key create airflow 2>/dev/null)
    KEY_ID=$(echo "$KEY_OUTPUT" | grep "Key ID:" | awk '{print $NF}')
    KEY_SECRET=$(echo "$KEY_OUTPUT" | grep "Secret key:" | awk '{print $NF}')

    if [ -z "$KEY_ID" ] || [ -z "$KEY_SECRET" ]; then
      echo "ERROR: Failed to get key ID and secret"
      echo "Output was: $KEY_OUTPUT"
      exit 1
    fi
    echo "  Key ID: $KEY_ID"

    # Step 4: Create bucket (idempotent — garage ignores if exists)
    echo "Creating bucket 'airflow-logs'..."
    garage bucket create airflow-logs 2>/dev/null || true

    # Step 5: Grant access
    echo "Granting key 'airflow' access to bucket 'airflow-logs'..."
    garage bucket allow --read --write --owner airflow-logs --key airflow 2>/dev/null || true

    # Step 6: Store credentials in a Kubernetes Secret
    echo "Storing credentials in Secret 'garagefs-airflow-credentials'..."
    cat <<KEOF | kubectl apply -f -
    apiVersion: v1
    kind: Secret
    metadata:
      name: garagefs-airflow-credentials
    type: Opaque
    stringData:
      accessKeyId: "$KEY_ID"
      secretAccessKey: "$KEY_SECRET"
      airflow-connection: "aws://${KEY_ID}:${KEY_SECRET}@/?endpoint_url=http%3A%2F%2Fgarage%3A3900&region_name=garage"
    KEOF

    echo "=== GarageFS Init Complete ==="
    echo "  Bucket: airflow-logs"
    echo "  S3 Endpoint: http://garage:3900"
    echo "  Region: garage"
---
apiVersion: batch/v1
kind: Job
metadata:
  name: configure-garagefs
spec:
  backoffLimit: 50
  template:
    spec:
      serviceAccountName: garagefs-init
      initContainers:
        - name: wait-for-garage
          image: busybox:stable
          command: ["sh", "-c", "until nc -z garage 3900; do echo 'Waiting for garage...'; sleep 5; done"]
      containers:
        - name: configure
          image: dxflrs/amd64_garage:v1.3.1
          command: ["/bin/sh", "/scripts/configure.sh"]
          env:
            - name: RPC_SECRET
              valueFrom:
                secretKeyRef:
                  name: garage-rpc-secret
                  key: rpcSecret
          volumeMounts:
            - name: script
              mountPath: /scripts
            - name: garage-config
              mountPath: /mnt/garage.toml
              subPath: garage.toml
      restartPolicy: OnFailure
      volumes:
        - name: script
          configMap:
            name: garagefs-init-script
            defaultMode: 0755
        - name: garage-config
          configMap:
            name: garage-config
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: garagefs-init
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: garagefs-init
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["create", "update", "patch", "get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: garagefs-init
subjects:
  - kind: ServiceAccount
    name: garagefs-init
roleRef:
  kind: Role
  name: garagefs-init
  apiGroup: rbac.authorization.k8s.io
```

**Notes:**
- The Job uses the same Garage image so the `garage` CLI is available
- Mounts the `garage-config` ConfigMap and injects RPC secret via env var (same pattern as the StatefulSet init container)
- Creates a ServiceAccount + Role + RoleBinding so the Job can create a K8s Secret with the credentials
- The `kubectl` binary needs to be available in the Garage image — if not, fall back to using curl against the K8s API. Verify this during implementation.

**Step 3: Verify `kubectl` availability in Garage image**

```bash
kubectl run -i --rm garage-check --image=dxflrs/amd64_garage:v1.3.1 --restart=Never -- which kubectl
```

If `kubectl` is NOT available, replace Step 6 in the script with a curl call to the K8s API using the ServiceAccount token.

**Step 4: Commit**

```bash
git add platform/manifests/garagefs-init/
git commit -m "feat: add GarageFS init job (layout, key, bucket)"
```

---

### Task 2: Create ArgoCD Application for garagefs-init

**Files:**
- Create: `platform/applications/garagefs-init.yaml`

**Reference:** `platform/applications/trino-init.yaml`

**Step 1: Write the Application manifest**

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: garagefs-init
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  project: dbt-openmetadata-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  source:
    repoURL: "http://forgejo-http:3000/stackable/openmetadata-dbt-demo.git"
    targetRevision: "main"
    path: platform/manifests/garagefs-init/
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    automated:
      selfHeal: true
      prune: true
```

Sync wave 1 — GarageFS (wave 0) deploys first, then init job runs.

**Step 2: Commit**

```bash
git add platform/applications/garagefs-init.yaml
git commit -m "feat: add ArgoCD Application for garagefs-init"
```

---

### Task 3: Update Airflow to use GarageFS

**Files:**
- Modify: `platform/manifests/airflow/airflow.yaml`

**Changes:**
1. Update `AIRFLOW__LOGGING__REMOTE_BASE_LOG_FOLDER` from `s3://demo/airflow-task-logs/` to `s3://airflow-logs/`
2. Update `AIRFLOW__LOGGING__REMOTE_LOG_CONN_ID` from `minio` to `garagefs`
3. Remove `AWS_CA_BUNDLE` (GarageFS uses HTTP, not HTTPS)
4. Remove the `minio-tls` volume and volumeMount (no longer needed)
5. Change the `AIRFLOW_CONN_MINIO` pod override to use the GarageFS connection secret instead
6. Rename the connection from `minio` to `garagefs`

**Step 1: Update envOverrides**

In `platform/manifests/airflow/airflow.yaml`, change the envOverrides block:

```yaml
    envOverrides: &envOverrides
      AIRFLOW_CONN_KUBERNETES_IN_CLUSTER: "kubernetes://?__extra__=%7B%22extra__kubernetes__in_cluster%22%3A+true%2C+%22extra__kubernetes__kube_config%22%3A+%22%22%2C+%22extra__kubernetes__kube_config_path%22%3A+%22%22%2C+%22extra__kubernetes__namespace%22%3A+%22%22%7D"
      AIRFLOW__LOGGING__REMOTE_LOGGING: "True"
      AIRFLOW__LOGGING__REMOTE_BASE_LOG_FOLDER: s3://airflow-logs/
      AIRFLOW__LOGGING__REMOTE_LOG_CONN_ID: garagefs
      AIRFLOW__SCHEDULER__DAG_DIR_LIST_INTERVAL: "20"
```

Removed: `AWS_CA_BUNDLE` and the MinIO comment line.

**Step 2: Update podOverrides for webservers/schedulers**

Change the secret reference from `airflow-minio-connection` to `garagefs-airflow-credentials`:

```yaml
    podOverrides: &podOverrides
      spec:
        containers:
          - name: airflow
            env:
              - name: AIRFLOW_CONN_GARAGEFS
                valueFrom:
                  secretKeyRef:
                    name: garagefs-airflow-credentials
                    key: airflow-connection
```

**Step 3: Update kubernetesExecutors podOverrides**

Same change for the executor pods (container name is `base`, not `airflow`):

```yaml
  kubernetesExecutors:
    envOverrides: *envOverrides
    podOverrides:
      spec:
        containers:
          - name: base
            env:
              - name: AIRFLOW_CONN_GARAGEFS
                valueFrom:
                  secretKeyRef:
                    name: garagefs-airflow-credentials
                    key: airflow-connection
```

**Step 4: Remove minio-tls volumes and volumeMounts from clusterConfig**

Remove these blocks from `clusterConfig`:

```yaml
    volumes:
      - name: minio-tls
        ...
    volumeMounts:
      - name: minio-tls
        mountPath: /stackable/minio-tls
```

**Step 5: Commit**

```bash
git add platform/manifests/airflow/airflow.yaml
git commit -m "feat: switch Airflow remote logging from MinIO to GarageFS"
```

---

### Task 4: Validate the complete setup

**Step 1: Verify file structure**

```bash
find platform/manifests/garagefs-init platform/applications/garagefs-init.yaml -type f
```

**Step 2: Verify YAML validity of all changed files**

```bash
python3 -c "import yaml; yaml.safe_load(open('platform/applications/garagefs-init.yaml'))"
python3 -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/garagefs-init/configure-garagefs.yaml')))"
python3 -c "import yaml; yaml.safe_load(open('platform/manifests/airflow/airflow.yaml'))"
```

**Step 3: Verify Airflow no longer references MinIO**

```bash
grep -r "minio" platform/manifests/airflow/ && echo "FAIL: still references minio" || echo "OK: no minio references"
```

**Step 4: Final commit (if not already committed per-task)**

```bash
git add platform/applications/garagefs-init.yaml platform/manifests/garagefs-init/ platform/manifests/airflow/airflow.yaml
git commit -m "feat: add GarageFS init and switch Airflow to GarageFS S3 backend"
```
