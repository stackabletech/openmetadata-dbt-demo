# Remove Airflow RWX PVC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the `dbt-target` `ReadWriteMany` PVC so the demo runs on any Kubernetes cluster, not just AKS.

**Architecture:** Replace the cross-pod shared volume with S3-mediated shard-and-merge. Per-model Cosmos tasks write `run_results.json` to pod-local `/tmp/dbt-target`, and an Airflow callback uploads each shard to GarageFS. A new `finalize_dbt_artifacts` task merges the shards, runs `dbt docs generate` to regenerate `manifest.json`+`catalog.json`, publishes the three canonical artifacts to the OpenMetadata-watched S3 prefix, then deletes the shards. OpenMetadata ingestion is unchanged.

**Tech Stack:** Python 3, Apache Airflow (KubernetesExecutor), Astronomer Cosmos, dbt-trino, boto3, GarageFS (S3-compatible), Stackable Airflow operator, ArgoCD GitOps.

**Spec:** `docs/superpowers/specs/2026-04-23-airflow-rwx-removal-design.md`

**Note on testing:** This repo has no Python test suite for DAGs (manual in-cluster validation only), so the plan uses `python -m py_compile` and `ast.parse` as syntax safety nets between edits and defers functional verification to a final cluster-run task. Adding a proper pytest harness is out of scope (YAGNI for a demo).

**File map:**

| Path | Action | Responsibility |
|---|---|---|
| `dags/tpch_dbt_dag.py` | Modify | Add callback + finalize callables; rewire task graph; drop `upload_dbt_artifacts` |
| `platform/manifests/airflow/airflow.yaml` | Modify | Change `DBT_TARGET_PATH` to `/tmp/dbt-target`; remove volume + volumeMount |
| `platform/manifests/airflow/dbt-target-pvc.yaml` | Delete | No longer needed |
| `README.md` | Modify | Remove stale RWX / openmetadata-dependencies justification in AKS Limitations |

---

### Task 1: Add `upload_run_results_shard` callback to the DAG

**Files:**
- Modify: `dags/tpch_dbt_dag.py`

This task introduces the per-task callback as a module-level function but does **not** wire it into the `DbtTaskGroup` yet. The DAG will still parse and run exactly as today.

- [ ] **Step 1: Open the DAG file and locate the `upload_dbt_artifacts` function**

Read `dags/tpch_dbt_dag.py`. Find the `upload_dbt_artifacts` function (~line 64). We will add the new callback directly below it, leaving `upload_dbt_artifacts` in place for now.

- [ ] **Step 2: Add the callback function after `upload_dbt_artifacts`**

Insert this function immediately after the closing of `upload_dbt_artifacts` (right before the `_om_helpers` definition):

```python
def upload_run_results_shard(context):
    """Upload this task's run_results.json shard to GarageFS after dbt execution.

    Runs as the Airflow on_success_callback / on_failure_callback for each
    Cosmos-generated dbt task. Failed tasks often still produce a valid
    run_results.json (e.g. test failures) that OpenMetadata wants to ingest, so
    we upload on both success and failure.
    """
    import os
    from pathlib import Path
    from urllib.parse import quote
    import boto3
    from airflow.hooks.base import BaseHook

    target_dir = Path(os.environ.get("DBT_TARGET_PATH", "/tmp/dbt-target"))
    local = target_dir / "run_results.json"
    if not local.exists():
        print(f"  No run_results.json at {local}; nothing to upload")
        return

    run_id = quote(context["dag_run"].run_id, safe="")
    task_id = context["ti"].task_id
    key = f"_runs/{run_id}/run_results/{task_id}.json"

    conn = BaseHook.get_connection("garagefs")
    extras = conn.extra_dejson
    s3 = boto3.client(
        "s3",
        endpoint_url=extras.get("endpoint_url", "http://garage.shared.svc.cluster.local:3900"),
        aws_access_key_id=conn.login,
        aws_secret_access_key=conn.password,
        region_name=extras.get("region_name", "garage"),
    )
    s3.upload_file(str(local), S3_BUCKET, key)
    print(f"  Uploaded run_results shard -> s3://{S3_BUCKET}/{key}")
```

- [ ] **Step 3: Syntax-check the DAG**

Run:

```bash
python -m py_compile dags/tpch_dbt_dag.py
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add dags/tpch_dbt_dag.py
git commit -m "Add upload_run_results_shard callback (unwired)"
```

---

### Task 2: Add `finalize_dbt_artifacts` callable to the DAG

**Files:**
- Modify: `dags/tpch_dbt_dag.py`

Add the new finalize callable. Like Task 1, this is still unwired — the DAG continues to use `upload_dbt_artifacts`.

The function must be fully self-contained because `PythonVirtualenvOperator` serializes the source into a subprocess: no module-level helpers, no closed-over constants. Everything arrives via parameters.

- [ ] **Step 1: Add the finalize function immediately after `upload_run_results_shard`**

Insert:

```python
def finalize_dbt_artifacts(
    dbt_project_path: str,
    dbt_target_path: str,
    s3_bucket: str,
    s3_prefix: str,
    run_id_raw: str,
):
    """Merge per-task run_results shards, regenerate manifest+catalog via
    dbt docs generate, publish all three canonical artifacts to S3, and delete
    the shard prefix.

    Runs inside a PythonVirtualenvOperator with system_site_packages=True and
    dbt-trino installed into the venv. Must be self-contained — helpers and
    imports live inside the body because the callable is serialized into a
    subprocess.
    """
    import json
    import os
    from pathlib import Path
    from urllib.parse import quote
    import boto3
    from airflow.hooks.base import BaseHook
    from dbt.cli.main import dbtRunner

    run_id = quote(run_id_raw, safe="")

    conn = BaseHook.get_connection("garagefs")
    extras = conn.extra_dejson
    s3 = boto3.client(
        "s3",
        endpoint_url=extras.get("endpoint_url", "http://garage.shared.svc.cluster.local:3900"),
        aws_access_key_id=conn.login,
        aws_secret_access_key=conn.password,
        region_name=extras.get("region_name", "garage"),
    )

    # 1. List and download all run_results shards for this DAG run.
    shard_prefix = f"_runs/{run_id}/run_results/"
    resp = s3.list_objects_v2(Bucket=s3_bucket, Prefix=shard_prefix)
    shard_keys = [obj["Key"] for obj in resp.get("Contents", [])]
    print(f"Found {len(shard_keys)} shards at s3://{s3_bucket}/{shard_prefix}")
    if not shard_keys:
        raise RuntimeError(
            f"No run_results shards under s3://{s3_bucket}/{shard_prefix}; "
            "upstream dbt tasks did not upload anything"
        )

    # 2. Merge shards. Each shard's "results" array has one entry (Cosmos runs
    #    one dbt node per task). We concatenate results, preserve metadata/args
    #    from the first shard, and sum execution_time for elapsed_time.
    merged_results = []
    template = None
    for key in shard_keys:
        data = json.loads(s3.get_object(Bucket=s3_bucket, Key=key)["Body"].read())
        if template is None:
            template = data
        merged_results.extend(data.get("results", []))
    merged = {
        "metadata": template["metadata"],
        "args": template["args"],
        "elapsed_time": sum(r.get("execution_time", 0) for r in merged_results),
        "results": merged_results,
    }

    # 3. Write merged run_results.json into the pod-local dbt target dir.
    target = Path(dbt_target_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / "run_results.json").write_text(json.dumps(merged))
    print(f"Wrote merged run_results.json with {len(merged_results)} result entries")

    # 4. dbt docs generate — compiles the project (writes manifest.json) and
    #    queries Trino information_schema (writes catalog.json) into target.
    os.environ["DBT_TARGET_PATH"] = str(target)
    result = dbtRunner().invoke([
        "docs", "generate",
        "--project-dir", dbt_project_path,
        "--profiles-dir", dbt_project_path,
    ])
    if not result.success:
        raise RuntimeError(f"dbt docs generate failed: {result.exception}")
    print("dbt docs generate succeeded")

    # 5. Upload canonical artifacts to the OM-watched prefix.
    for name in ("manifest.json", "catalog.json", "run_results.json"):
        local = target / name
        if not local.exists():
            raise FileNotFoundError(f"Expected {name} at {local} after dbt docs generate")
        s3.upload_file(str(local), s3_bucket, f"{s3_prefix}/{name}")
        print(f"  Uploaded {name} -> s3://{s3_bucket}/{s3_prefix}/{name}")

    # 6. Cleanup shard prefix.
    s3.delete_objects(
        Bucket=s3_bucket,
        Delete={"Objects": [{"Key": k} for k in shard_keys]},
    )
    print(f"Deleted {len(shard_keys)} shards under {shard_prefix}")
```

- [ ] **Step 2: Syntax-check the DAG**

Run:

```bash
python -m py_compile dags/tpch_dbt_dag.py
```

Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add dags/tpch_dbt_dag.py
git commit -m "Add finalize_dbt_artifacts callable (unwired)"
```

---

### Task 3: Rewire DAG to use new callback + finalize; remove old upload task

**Files:**
- Modify: `dags/tpch_dbt_dag.py`

Switch the task graph to the new shape. After this task, `upload_dbt_artifacts` is gone, the callback is wired into `DbtTaskGroup.default_args`, and `finalize_dbt_artifacts` runs as a `PythonVirtualenvOperator`.

- [ ] **Step 1: Update the `DBT_TARGET_PATH` module-level constant**

Find (around line 12):

```python
DBT_TARGET_PATH = Path("/shared/dbt-target")
```

Replace with:

```python
DBT_TARGET_PATH = Path("/tmp/dbt-target")
```

This constant is no longer consumed by the new code paths (callback reads from env; finalize gets it via `op_kwargs`), but keeping it in sync with airflow.yaml avoids surprises for anyone reading the module.

- [ ] **Step 2: Delete the `upload_dbt_artifacts` function**

Find the entire `def upload_dbt_artifacts(**context):` block (roughly lines 64–92) and delete it including its docstring. Leave the `ARTIFACTS` and `S3_PREFIX`/`S3_BUCKET` module constants in place — `ARTIFACTS` is no longer referenced but `S3_BUCKET` and `S3_PREFIX` are used by the callback and finalize wiring.

After the delete, also remove the now-unused `ARTIFACTS` constant (line 15):

```python
ARTIFACTS = ["manifest.json", "catalog.json", "run_results.json"]
```

- [ ] **Step 3: Add `PythonVirtualenvOperator` import**

Find the existing imports at the top of the file:

```python
from airflow.operators.python import PythonOperator
```

Replace with:

```python
from airflow.operators.python import PythonOperator, PythonVirtualenvOperator
```

- [ ] **Step 4: Wire the callback into `DbtTaskGroup.default_args`**

Find the `DbtTaskGroup(...)` call (around line 225). Its current `default_args`:

```python
default_args={"retries": 1},
```

Replace with:

```python
default_args={
    "retries": 1,
    "on_success_callback": upload_run_results_shard,
    "on_failure_callback": upload_run_results_shard,
},
```

- [ ] **Step 5: Replace the `upload_artifacts` operator**

Find the current operator definition:

```python
upload_artifacts = PythonOperator(
    task_id="upload_dbt_artifacts",
    python_callable=upload_dbt_artifacts,
)
```

Replace with:

```python
finalize = PythonVirtualenvOperator(
    task_id="finalize_dbt_artifacts",
    python_callable=finalize_dbt_artifacts,
    requirements=["dbt-trino"],
    system_site_packages=True,
    op_kwargs={
        "dbt_project_path": str(DBT_PROJECT_PATH),
        "dbt_target_path": str(DBT_TARGET_PATH),
        "s3_bucket": S3_BUCKET,
        "s3_prefix": S3_PREFIX,
        "run_id_raw": "{{ dag_run.run_id }}",
    },
)
```

- [ ] **Step 6: Update the task dependency chain**

Find the final line of the DAG body:

```python
wait_for_services >> dbt_tasks >> upload_artifacts >> trigger_metadata_ingestion >> trigger_dbt_ingestion
```

Replace with:

```python
wait_for_services >> dbt_tasks >> finalize >> trigger_metadata_ingestion >> trigger_dbt_ingestion
```

- [ ] **Step 7: Syntax-check the DAG**

Run:

```bash
python -m py_compile dags/tpch_dbt_dag.py
```

Expected: no output, exit code 0.

- [ ] **Step 8: Verify no stale references remain**

Run:

```bash
grep -n "upload_dbt_artifacts\|upload_artifacts\|ARTIFACTS" dags/tpch_dbt_dag.py
```

Expected: no matches. If anything matches, remove it.

- [ ] **Step 9: Commit**

```bash
git add dags/tpch_dbt_dag.py
git commit -m "Rewire DAG: per-task callback + finalize_dbt_artifacts"
```

---

### Task 4: Update `airflow.yaml` to drop the RWX volume

**Files:**
- Modify: `platform/manifests/airflow/airflow.yaml`

- [ ] **Step 1: Update the `DBT_TARGET_PATH` env var**

Open `platform/manifests/airflow/airflow.yaml` and find (lines ~71–72, under `kubernetesExecutors.podOverrides.spec.containers[].env`):

```yaml
              - name: DBT_TARGET_PATH
                value: /shared/dbt-target
```

Replace with:

```yaml
              - name: DBT_TARGET_PATH
                value: /tmp/dbt-target
```

- [ ] **Step 2: Remove the volume mount and volume**

In the same `kubernetesExecutors` section, find (lines ~73–79):

```yaml
            volumeMounts:
              - name: dbt-target
                mountPath: /shared/dbt-target
        volumes:
          - name: dbt-target
            persistentVolumeClaim:
              claimName: dbt-target
```

Delete these lines entirely. The `containers[].env` list above remains; everything from `volumeMounts:` through the end of the `volumes:` block goes.

After the edit, the `kubernetesExecutors.podOverrides` block should look like:

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
              - name: DBT_TARGET_PATH
                value: /tmp/dbt-target
```

- [ ] **Step 3: Validate the YAML parses**

Run:

```bash
python -c "import yaml; list(yaml.safe_load_all(open('platform/manifests/airflow/airflow.yaml')))"
```

Expected: no output, exit code 0.

- [ ] **Step 4: Verify `dbt-target` volume is not referenced elsewhere in the file**

Run:

```bash
grep -n "dbt-target\|shared/dbt-target" platform/manifests/airflow/airflow.yaml
```

Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add platform/manifests/airflow/airflow.yaml
git commit -m "Point DBT_TARGET_PATH at pod-local /tmp and drop shared volume"
```

---

### Task 5: Delete the `dbt-target` PVC manifest

**Files:**
- Delete: `platform/manifests/airflow/dbt-target-pvc.yaml`

- [ ] **Step 1: Delete the file**

Run:

```bash
git rm platform/manifests/airflow/dbt-target-pvc.yaml
```

- [ ] **Step 2: Verify no references remain in the repo**

Run:

```bash
grep -rn "dbt-target" platform/ dags/ 2>&1 | grep -v "\.git/"
```

Expected: no matches. (The `DBT_TARGET_PATH` env var and Python constant are unrelated names; what we're grepping for here is references to the `dbt-target` PVC name, which should be gone.)

If you see matches, double-check they are all benign before proceeding.

- [ ] **Step 3: Commit**

```bash
git commit -m "Delete dbt-target PVC; no longer referenced"
```

---

### Task 6: Update README AKS Limitations section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Open `README.md` and locate the `### AKS Limitations` section**

The section begins around the "This demo currently only runs on Azure Kubernetes Service (AKS)" paragraph.

- [ ] **Step 2: Replace the section body**

Find the current section (from `### AKS Limitations` through the end-of-section blank line before the next `##` or `###`):

```markdown
### AKS Limitations

This demo currently only runs on Azure Kubernetes Service (AKS), due to the requirement for `ReadWriteMany` PVCs for Airflow logs and DAGs. 
The default AKS StorageClass (`disk.csi.azure.com`) only supports `ReadWriteOnce`, so these are hardcoded to an AKS storageclass that supports `ReadWriteMany`.
We plan to fix this at some point, but have not yet gotten around to it.

The `openmetadata-dependencies` application is configured to use `azurefile-csi-premium` for these PVCs. If your AKS cluster does not have this StorageClass, you need to either:
- Create an Azure Files StorageClass that supports `ReadWriteMany`
- Disable the bundled Airflow in the OpenMetadata dependencies chart
```

Replace with:

```markdown
### Portability

The platform was originally AKS-only because Airflow mounted a `ReadWriteMany` PVC (`azurefile`) to share dbt artifacts between executor pods. That volume has been removed — dbt artifacts now flow through GarageFS (S3) using a per-task upload callback and a merging finalize step. The demo no longer depends on an RWX storage class.

The OpenTofu configuration in `tofu/` still provisions AKS, but the Kubernetes manifests themselves are portable. Running on another cloud or on-prem cluster requires only swapping the infra provisioning.
```

- [ ] **Step 3: Preview the section renders sensibly**

Run:

```bash
grep -A 5 "### Portability" README.md
```

Expected: the new section text appears. Sanity-check for stray backticks or broken heading levels.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Update README: demo is no longer AKS-pinned by storage class"
```

---

### Task 7: Push and verify in-cluster

**Files:** None (verification only)

This is the acceptance gate. The previous tasks produced green commits; this task confirms the change actually works on a running cluster. Use whichever cluster you have access to — for the strongest signal, run on a non-AKS cluster with no RWX storage class.

- [ ] **Step 1: Push to the upstream that Forgejo mirrors**

```bash
git push origin main
```

Expected: push succeeds. Forgejo's mirror picks up the change within its poll interval (default ~1 min).

- [ ] **Step 2: Watch ArgoCD sync the Airflow application**

In the ArgoCD UI (or via CLI): confirm the `airflow` application transitions to `Synced` / `Healthy`. Executor pod templates update; running executor pods only adopt the new template for new tasks, so an in-flight run will finish with the old shape.

- [ ] **Step 3: Confirm the PVC is gone**

```bash
kubectl -n platform get pvc dbt-target
```

Expected: `NotFound` error.

- [ ] **Step 4: Trigger the DAG**

Either unpause `dbt_tpch_demo` and let `schedule="@once"` run it, or trigger manually in the Airflow UI / CLI.

- [ ] **Step 5: Watch per-model executor pods**

```bash
kubectl -n platform get pods -l airflow_component=worker --watch
```

Expected: each dbt task pod starts, completes, and does not fail with `read-only filesystem` errors. Check a sample pod's logs for `Uploaded run_results shard -> s3://dbt-artifacts/_runs/...` to confirm the callback fires.

- [ ] **Step 6: Confirm `finalize_dbt_artifacts` succeeds**

In the Airflow UI: `finalize_dbt_artifacts` task turns green. Its log should contain, in order: `Found N shards at ...`, `Wrote merged run_results.json with M result entries`, `dbt docs generate succeeded`, three `Uploaded ...` lines, `Deleted N shards under ...`.

- [ ] **Step 7: Inspect canonical S3 artifacts**

Exec into any pod with `mc` or run a one-off `aws` CLI pod targeted at GarageFS, and list:

```bash
# Inside a pod with s3 tooling configured for GarageFS:
s3 ls s3://dbt-artifacts/tpch_demo/
s3 ls s3://dbt-artifacts/_runs/
```

Expected:
- `tpch_demo/` contains `manifest.json`, `catalog.json`, `run_results.json`, all non-zero size.
- `_runs/` is empty (shards were cleaned up).

- [ ] **Step 8: Confirm OpenMetadata dbt ingestion runs**

`trigger_om_dbt_ingestion` turns green. In the OpenMetadata UI, the Trino service's tables (e.g. `hive-iceberg.demo.*`) show dbt-linked models with descriptions; test nodes appear attached to the relevant tables.

- [ ] **Step 9: Negative test — deliberate model failure**

Optional but recommended. In a scratch branch:

1. Introduce a SQL error in one mart model (e.g. change a column reference to a nonexistent column).
2. Push and let the DAG run.
3. Confirm the failing dbt task still uploads its `run_results` shard (its log shows the callback firing).
4. Confirm `finalize_dbt_artifacts` is skipped with trigger rule `all_success` — matching today's behaviour; the DAG marks as failed.
5. Revert the error, push, and let the DAG recover on the next run.

- [ ] **Step 10: If running on non-AKS, record the success**

Note in the project's log (or wherever the team tracks this) that the demo has now been verified on a cluster with no RWX storage class. This is the acceptance criterion from the spec.

---

## Self-Review (done during plan authoring)

**Spec coverage:** every bullet in the spec's "Manifest & code changes" table has a corresponding task (1–6), and the "Testing" section maps to task 7.

**Placeholder scan:** no `TBD`/`TODO`/vague handling notes; all code is fully written, all commands are literal.

**Type/name consistency:**
- Function names match across tasks: `upload_run_results_shard` (Task 1, Task 3 step 4), `finalize_dbt_artifacts` (Task 2, Task 3 step 5).
- Operator variable name is `finalize` (Task 3 step 5 and 6).
- `S3_BUCKET` and `S3_PREFIX` constants referenced in Task 3 are confirmed to already exist in the DAG file (lines 13–14 of current source); they are not redefined.
- `DBT_PROJECT_PATH` constant is similarly pre-existing (line 11).
- `DBT_TARGET_PATH` value `/tmp/dbt-target` is consistent between Task 3 (DAG constant), Task 4 (airflow.yaml env), and the callback's default fallback (Task 1).
- `run_id_raw` parameter name is consistent between the function signature (Task 2) and the `op_kwargs` template (Task 3 step 5).
