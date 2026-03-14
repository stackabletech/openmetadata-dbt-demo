# dbt Metadata to OpenMetadata — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Push dbt artifacts to GarageFS after each dbt run, and configure OpenMetadata to ingest them for lineage, docs, and test results.

**Architecture:** The existing dbt Cosmos DAG is extended with an S3 upload task. The GarageFS init job creates a `dbt-artifacts` bucket. The OpenMetadata init job registers a dbt ingestion pipeline pointing to the S3 artifacts.

**Tech Stack:** Airflow (Cosmos DbtDag), boto3, GarageFS (S3 API), OpenMetadata REST API

---

### Task 1: Add `dbt-artifacts` bucket to GarageFS init script

**Files:**
- Modify: `platform/manifests/garagefs-init/configure-garagefs.yaml`

The GarageFS init script already creates an `airflow-logs` bucket idempotently. Add a second bucket `dbt-artifacts` using the same pattern.

**Step 1: Add bucket creation after the existing `airflow-logs` bucket**

After the existing line:
```sh
$GARAGE_CMD bucket allow --read --write --owner airflow-logs --key airflow 2>/dev/null || echo "  Access may already be granted."
```

Add:
```sh
# Step 4b: Create dbt-artifacts bucket (idempotent)
echo "Creating bucket 'dbt-artifacts'..."
$GARAGE_CMD bucket create dbt-artifacts 2>/dev/null || echo "  Bucket may already exist, continuing."

# Step 5b: Grant access
echo "Granting key 'airflow' access to dbt-artifacts bucket..."
$GARAGE_CMD bucket allow --read --write --owner dbt-artifacts --key airflow 2>/dev/null || echo "  Access may already be granted."
```

Also update the final echo to mention both buckets:
```sh
echo "  Buckets: airflow-logs, dbt-artifacts"
```

**Step 2: Verify YAML validity**

**Step 3: Commit**

```bash
git add platform/manifests/garagefs-init/configure-garagefs.yaml
git commit -m "feat: add dbt-artifacts bucket to GarageFS init"
```

---

### Task 2: Add S3 upload task to the dbt DAG

**Files:**
- Modify: `dags/tpch_dbt_dag.py`

Add a task at the end of the DbtDag that uploads dbt artifacts (manifest.json, catalog.json, run_results.json) from the dbt target directory to `s3://dbt-artifacts/tpch_demo/` using the GarageFS S3 endpoint.

**Context:**
- Cosmos DbtDag in VIRTUALENV mode writes artifacts to `{dbt_project_path}/target/`
- The dbt project path is `/stackable/app/git-0/current/dags/dbt/tpch_demo`
- So artifacts land in `/stackable/app/git-0/current/dags/dbt/tpch_demo/target/`
- The Airflow connection `garagefs` (env var `AIRFLOW_CONN_GARAGEFS`) provides S3 credentials
- GarageFS S3 endpoint: `http://garage:3900`, region: `garage`
- The `boto3` library is available in the Stackable Airflow image

**Step 1: Rewrite `dags/tpch_dbt_dag.py`**

```python
# This file is an airflow DAG definition using Astronomer Cosmos for dbt orchestration.
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from cosmos import DbtTaskGroup, ProjectConfig, ProfileConfig, ExecutionConfig, RenderConfig
from cosmos.constants import ExecutionMode, LoadMode

DBT_PROJECT_PATH = Path("/stackable/app/git-0/current/dags/dbt/tpch_demo")
DBT_TARGET_PATH = DBT_PROJECT_PATH / "target"
S3_BUCKET = "dbt-artifacts"
S3_PREFIX = "tpch_demo"
ARTIFACTS = ["manifest.json", "catalog.json", "run_results.json"]


def upload_dbt_artifacts(**context):
    """Upload dbt artifacts from the target directory to GarageFS S3."""
    import boto3
    from airflow.hooks.base import BaseHook

    conn = BaseHook.get_connection("garagefs")
    extras = conn.extra_dejson
    s3 = boto3.client(
        "s3",
        endpoint_url=extras.get("endpoint_url", "http://garage:3900"),
        aws_access_key_id=conn.login,
        aws_secret_access_key=conn.password,
        region_name=extras.get("region_name", "garage"),
    )

    uploaded = []
    for artifact in ARTIFACTS:
        local_path = DBT_TARGET_PATH / artifact
        if local_path.exists():
            s3_key = f"{S3_PREFIX}/{artifact}"
            s3.upload_file(str(local_path), S3_BUCKET, s3_key)
            print(f"  Uploaded {artifact} -> s3://{S3_BUCKET}/{s3_key}")
            uploaded.append(artifact)
        else:
            print(f"  Skipping {artifact} (not found at {local_path})")

    if not uploaded:
        raise FileNotFoundError(f"No dbt artifacts found in {DBT_TARGET_PATH}")
    print(f"Uploaded {len(uploaded)} artifacts to s3://{S3_BUCKET}/{S3_PREFIX}/")


with DAG(
    dag_id="dbt_tpch_demo",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args={"retries": 1},
) as dag:

    dbt_tasks = DbtTaskGroup(
        group_id="dbt_tpch",
        project_config=ProjectConfig(
            dbt_project_path=DBT_PROJECT_PATH,
        ),
        profile_config=ProfileConfig(
            profiles_yml_filepath=DBT_PROJECT_PATH / "profiles.yml",
            profile_name="tpch_demo",
            target_name="dev",
        ),
        render_config=RenderConfig(
            load_method=LoadMode.CUSTOM,
        ),
        execution_config=ExecutionConfig(
            execution_mode=ExecutionMode.VIRTUALENV,
            virtualenv_dir=Path("/tmp/dbt_venv"),
        ),
        operator_args={
            "py_system_site_packages": False,
            "py_requirements": ["dbt-trino"],
            "install_deps": True,
        },
        default_args={"retries": 1},
    )

    upload_artifacts = PythonOperator(
        task_id="upload_dbt_artifacts",
        python_callable=upload_dbt_artifacts,
    )

    dbt_tasks >> upload_artifacts
```

**Key changes from original:**
- Switched from `DbtDag` to `DAG` + `DbtTaskGroup` so we can append tasks after the dbt group
- Added `upload_dbt_artifacts` PythonOperator that reads the `garagefs` connection and uploads via boto3
- The upload task runs AFTER all dbt tasks complete (`dbt_tasks >> upload_artifacts`)

**Step 2: Verify Python syntax**

```bash
python3 -c "import ast; ast.parse(open('dags/tpch_dbt_dag.py').read()); print('OK')"
```

**Step 3: Commit**

```bash
git add dags/tpch_dbt_dag.py
git commit -m "feat: add S3 upload task for dbt artifacts to GarageFS"
```

---

### Task 3: Add dbt ingestion pipeline to OpenMetadata init

**Files:**
- Modify: `platform/manifests/openmetadata-init/configure-openmetadata.yaml`

Add a `create_dbt_pipeline` function to the Python script that creates a dbt ingestion pipeline pointing to `s3://dbt-artifacts/tpch_demo/`. The dbt pipeline needs the GarageFS credentials which are stored in the `garagefs-airflow-credentials` K8s Secret.

**Step 1: Add environment variables to the Job spec**

Add these env vars to the Job container (alongside the existing ones):
```yaml
- name: S3_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: garagefs-airflow-credentials
      key: accessKeyId
- name: S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: garagefs-airflow-credentials
      key: secretAccessKey
```

**Step 2: Add the `create_dbt_pipeline` function to the Python script**

After the existing `create_ingestion_pipeline` function, add:

```python
DBT_PIPELINE_NAME = f"{SERVICE_NAME}_dbt_ingestion"

S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://garage:3900")
S3_REGION = os.environ.get("S3_REGION", "garage")
DBT_BUCKET = os.environ.get("DBT_BUCKET", "dbt-artifacts")
DBT_PREFIX = os.environ.get("DBT_PREFIX", "tpch_demo")


def create_dbt_pipeline(token, service):
    """Create or update a dbt ingestion pipeline for the Trino service."""
    print(f"Creating/updating dbt pipeline '{DBT_PIPELINE_NAME}' ...")
    service_ref = {
        "id": service["id"],
        "type": "databaseService"
    }
    payload = {
        "name": DBT_PIPELINE_NAME,
        "pipelineType": "dbt",
        "service": service_ref,
        "sourceConfig": {
            "config": {
                "type": "DBT",
                "dbtConfigSource": {
                    "dbtConfigType": "s3",
                    "dbtSecurityConfig": {
                        "awsAccessKeyId": S3_ACCESS_KEY,
                        "awsSecretAccessKey": S3_SECRET_KEY,
                        "awsRegion": S3_REGION,
                        "endPointURL": S3_ENDPOINT
                    },
                    "dbtPrefixConfig": {
                        "dbtBucketName": DBT_BUCKET,
                        "dbtObjectPrefix": DBT_PREFIX
                    }
                },
                "dbtUpdateDescriptions": True,
                "includeTags": True
            }
        },
        "airflowConfig": {
            "scheduleInterval": "0 */6 * * *"
        }
    }
    result = api_request("/services/ingestionPipelines", method="PUT", data=payload, token=token)
    pipeline_id = result["id"]
    print(f"  dbt pipeline created/updated: {result['fullyQualifiedName']} (id={pipeline_id})")
    return result
```

**Step 3: Update `main()` to call the new function**

After the existing `trigger_ingestion(token, pipeline)` line, add:
```python
dbt_pipeline = create_dbt_pipeline(token, service)
trigger_ingestion(token, dbt_pipeline)
```

**Step 4: Verify Python syntax**

Extract the script from the YAML and check with `ast.parse()`.

**Step 5: Commit**

```bash
git add platform/manifests/openmetadata-init/configure-openmetadata.yaml
git commit -m "feat: add dbt ingestion pipeline to OpenMetadata init"
```

---

### Task 4: Validate complete setup

**Step 1: Verify all modified files are valid YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('platform/manifests/garagefs-init/configure-garagefs.yaml'))" 2>/dev/null || echo "check manually"
python3 -c "import ast; ast.parse(open('dags/tpch_dbt_dag.py').read()); print('DAG syntax OK')"
```

**Step 2: Verify the Python script in configure-openmetadata.yaml is syntactically valid**

**Step 3: Verify the dbt artifacts list matches what dbt generates**

dbt generates these in `target/`:
- `manifest.json` — required by OpenMetadata
- `catalog.json` — generated by `dbt docs generate` (may not exist if docs aren't run)
- `run_results.json` — generated after `dbt run`

Note: The upload task skips missing files gracefully but requires at least one to exist.
