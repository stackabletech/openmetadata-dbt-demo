# OpenMetadata Trino Integration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Kubernetes Job that registers Trino as a database service in OpenMetadata and triggers metadata ingestion for all four catalogs (hive, hive-iceberg, tpch, tpcds).

**Architecture:** A Kubernetes Job (deployed via a dedicated ArgoCD Application at sync wave 3) runs a Python script that calls the OpenMetadata REST API to create the Trino database service and an ingestion pipeline, then triggers it. Follows the existing trino/trino-init separation pattern.

**Tech Stack:** Kubernetes Job, Python 3.12 + requests, OpenMetadata REST API v1, ArgoCD sync waves

---

### Task 1: Create the ArgoCD Application for openmetadata-init

**Files:**
- Create: `platform/applications/openmetadata-init.yaml`

**Reference:** `platform/applications/trino-init.yaml` — copy this pattern exactly.

**Step 1: Create the ArgoCD Application manifest**

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: openmetadata-init
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  project: dbt-openmetadata-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  source:
    repoURL: "http://forgejo-http:3000/stackable/openmetadata-dbt-demo.git"
    targetRevision: "main"
    path: platform/manifests/openmetadata-init/
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    automated:
      selfHeal: true
      prune: true
```

Write this to `platform/applications/openmetadata-init.yaml`.

**Step 2: Verify YAML validity**

Run: `python3 -c "import yaml; yaml.safe_load(open('platform/applications/openmetadata-init.yaml'))"`
Expected: No output (valid YAML)

**Step 3: Commit**

```bash
git add platform/applications/openmetadata-init.yaml
git commit -m "feat: add ArgoCD Application for openmetadata-init"
```

---

### Task 2: Create the Python configuration script

**Files:**
- Create: `platform/manifests/openmetadata-init/configure-openmetadata.yaml`

**Reference patterns:**
- `infrastructure/forgejo-manifests/configure-forgejo.yaml` — ConfigMap + Job in one file
- OpenMetadata API: `http://openmetadata:8585/api/v1/`
- Trino coordinator: `trino-coordinator:8443` (HTTPS)

**Step 1: Create the manifests directory**

Run: `mkdir -p platform/manifests/openmetadata-init`

**Step 2: Write the combined ConfigMap + Job manifest**

Create `platform/manifests/openmetadata-init/configure-openmetadata.yaml` with:

```yaml
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: openmetadata-init-script
data:
  configure.py: |
    #!/usr/bin/env python3
    """Configure OpenMetadata: register Trino as a database service and trigger ingestion."""

    import json
    import os
    import sys
    import time
    import urllib.request
    import urllib.error

    OM_HOST = os.environ.get("OM_HOST", "http://openmetadata:8585")
    API = f"{OM_HOST}/api/v1"

    TRINO_HOST = os.environ.get("TRINO_HOST", "trino-coordinator")
    TRINO_PORT = os.environ.get("TRINO_PORT", "8443")
    TRINO_SCHEME = os.environ.get("TRINO_SCHEME", "https")
    TRINO_USER = os.environ.get("TRINO_USER", "admin")

    MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "60"))
    RETRY_INTERVAL = int(os.environ.get("RETRY_INTERVAL", "10"))

    SERVICE_NAME = "trino"
    PIPELINE_NAME = f"{SERVICE_NAME}_metadata_ingestion"


    def api_request(path, method="GET", data=None, token=None):
        """Make an API request to OpenMetadata."""
        url = f"{API}{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            print(f"  HTTP {e.code} for {method} {url}: {error_body}", file=sys.stderr)
            raise


    def wait_for_api():
        """Wait until the OpenMetadata API is ready."""
        print(f"Waiting for OpenMetadata API at {API} ...")
        for i in range(MAX_RETRIES):
            try:
                api_request("/system/version")
                print("  OpenMetadata API is ready.")
                return
            except Exception:
                print(f"  Attempt {i+1}/{MAX_RETRIES} — not ready, retrying in {RETRY_INTERVAL}s ...")
                time.sleep(RETRY_INTERVAL)
        print("ERROR: OpenMetadata API did not become ready.", file=sys.stderr)
        sys.exit(1)


    def get_bot_token():
        """Retrieve the ingestion-bot JWT token."""
        print("Fetching ingestion-bot JWT token ...")
        bot = api_request("/bots/name/ingestion-bot")
        token = bot.get("botUser", {}).get("authenticationMechanism", {}).get("config", {}).get("JWTToken")
        if not token:
            print("ERROR: Could not retrieve ingestion-bot JWT token.", file=sys.stderr)
            sys.exit(1)
        print("  Got JWT token.")
        return token


    def create_database_service(token):
        """Create or update the Trino database service."""
        print(f"Creating/updating database service '{SERVICE_NAME}' ...")
        payload = {
            "name": SERVICE_NAME,
            "serviceType": "Trino",
            "connection": {
                "config": {
                    "type": "Trino",
                    "scheme": "trino",
                    "hostPort": f"{TRINO_HOST}:{TRINO_PORT}",
                    "username": TRINO_USER,
                    "connectionArguments": {
                        "http_scheme": TRINO_SCHEME,
                        "verify": "false"
                    }
                }
            }
        }
        result = api_request("/services/databaseServices", method="PUT", data=payload, token=token)
        service_id = result["id"]
        fqn = result["fullyQualifiedName"]
        print(f"  Service created/updated: {fqn} (id={service_id})")
        return result


    def create_ingestion_pipeline(token, service):
        """Create or update a metadata ingestion pipeline for the Trino service."""
        print(f"Creating/updating ingestion pipeline '{PIPELINE_NAME}' ...")
        service_ref = {
            "id": service["id"],
            "type": "databaseService"
        }
        payload = {
            "name": PIPELINE_NAME,
            "pipelineType": "metadata",
            "service": service_ref,
            "sourceConfig": {
                "config": {
                    "type": "DatabaseMetadata",
                    "markDeletedTables": True,
                    "includeTables": True,
                    "includeViews": True
                }
            },
            "airflowConfig": {
                "scheduleInterval": "0 */6 * * *"
            }
        }
        result = api_request("/services/ingestionPipelines", method="PUT", data=payload, token=token)
        pipeline_id = result["id"]
        print(f"  Pipeline created/updated: {result['fullyQualifiedName']} (id={pipeline_id})")
        return result


    def trigger_ingestion(token, pipeline):
        """Trigger the ingestion pipeline."""
        pipeline_id = pipeline["id"]
        print(f"Triggering ingestion pipeline {pipeline_id} ...")
        try:
            api_request(f"/services/ingestionPipelines/trigger/{pipeline_id}", method="POST", token=token)
            print("  Ingestion triggered successfully.")
        except Exception as e:
            print(f"  Warning: Could not trigger ingestion (may need Airflow): {e}")
            print("  The pipeline is registered and can be triggered from the UI.")


    def main():
        wait_for_api()
        token = get_bot_token()
        service = create_database_service(token)
        pipeline = create_ingestion_pipeline(token, service)
        trigger_ingestion(token, pipeline)
        print("Done — Trino is registered in OpenMetadata.")


    if __name__ == "__main__":
        main()
---
apiVersion: batch/v1
kind: Job
metadata:
  name: configure-openmetadata
spec:
  backoffLimit: 50
  template:
    spec:
      containers:
        - name: configure
          image: python:3.12-slim
          command: ["python", "-u", "/scripts/configure.py"]
          env:
            - name: OM_HOST
              value: "http://openmetadata:8585"
            - name: TRINO_HOST
              value: "trino-coordinator"
            - name: TRINO_PORT
              value: "8443"
            - name: TRINO_SCHEME
              value: "https"
            - name: TRINO_USER
              value: "admin"
          volumeMounts:
            - name: script
              mountPath: /scripts
      restartPolicy: OnFailure
      volumes:
        - name: script
          configMap:
            name: openmetadata-init-script
```

**Step 3: Verify YAML validity**

Run: `python3 -c "import yaml; [print(d['kind']) for d in yaml.safe_load_all(open('platform/manifests/openmetadata-init/configure-openmetadata.yaml')) if d]"`
Expected: `ConfigMap` then `Job`

**Step 4: Verify Python script syntax**

Run: `python3 -c "import ast; ast.parse(open('platform/manifests/openmetadata-init/configure-openmetadata.yaml').read().split('configure.py: |')[1].split('---')[0])"`
Or extract and run: `python3 -m py_compile` on the script portion.

**Step 5: Commit**

```bash
git add platform/manifests/openmetadata-init/configure-openmetadata.yaml
git commit -m "feat: add Job to register Trino in OpenMetadata and trigger ingestion"
```

---

### Task 3: Validate the complete setup

**Step 1: Verify directory structure**

Run: `find platform/manifests/openmetadata-init platform/applications/openmetadata-init.yaml -type f`

Expected:
```
platform/manifests/openmetadata-init/configure-openmetadata.yaml
platform/applications/openmetadata-init.yaml
```

**Step 2: Verify sync wave ordering makes sense**

Check that sync wave 3 comes after all dependencies:
- OpenMetadata (no wave = 0)
- Trino (wave 1 from `trino.yaml`)
- Trino-init (wave 2)
- openmetadata-init (wave 3) ← our new app

**Step 3: Verify the Python script uses only stdlib (no pip needed)**

The script uses only `json`, `os`, `sys`, `time`, `urllib.request`, `urllib.error` — all stdlib. No `requests` library needed, so `python:3.12-slim` works out of the box with no pip install step.

**Step 4: Final commit with both files**

If not already committed individually:
```bash
git add platform/applications/openmetadata-init.yaml platform/manifests/openmetadata-init/configure-openmetadata.yaml
git commit -m "feat: add openmetadata-init to register Trino and trigger metadata ingestion"
```
