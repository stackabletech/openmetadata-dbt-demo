# This file is an airflow DAG definition using Astronomer Cosmos for dbt orchestration.
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator, PythonVirtualenvOperator
from airflow.sensors.python import PythonSensor
from cosmos import DbtTaskGroup, ProjectConfig, ProfileConfig, ExecutionConfig, RenderConfig
from cosmos.constants import ExecutionMode, LoadMode

DBT_PROJECT_PATH = Path("/stackable/app/git-0/current/dags/dbt/tpch_demo")
DBT_TARGET_PATH = Path("/tmp/dbt-target")
S3_BUCKET = "dbt-artifacts"
S3_PREFIX = "tpch_demo"
OM_HOST = "http://openmetadata:8585"
OM_API = f"{OM_HOST}/api/v1"
OM_ADMIN_EMAIL = "admin@open-metadata.org"
OM_ADMIN_PASSWORD = "admin"
METADATA_PIPELINE_FQN = "trino.trino_metadata_ingestion"
DBT_PIPELINE_FQN = "trino.trino_dbt_ingestion"
INGESTION_POLL_INTERVAL = 10  # seconds
INGESTION_TIMEOUT = 600  # seconds

TRINO_SCHEMA_CHECK = "hive-iceberg.demo"


def check_services_ready(**context):
    """Check that OpenMetadata API is responding and Trino has the required schema."""
    import urllib.request
    import urllib.error

    # --- Check OpenMetadata health ---
    try:
        req = urllib.request.Request(f"{OM_HOST}/api/v1/system/version")
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print("  OpenMetadata API is reachable.")
    except Exception as e:
        print(f"  OpenMetadata not ready: {e}")
        return False

    # --- Check Trino schema via Airflow TrinoHook ---
    try:
        from airflow.providers.trino.hooks.trino import TrinoHook

        catalog, schema = TRINO_SCHEMA_CHECK.split(".", 1)
        hook = TrinoHook(trino_conn_id="trino_default")
        records = hook.get_records(f"SHOW SCHEMAS FROM \"{catalog}\" LIKE '{schema}'")
        if any(row[0] == schema for row in records):
            print(f"  Trino schema {TRINO_SCHEMA_CHECK} exists.")
        else:
            print(f"  Trino is up but schema {TRINO_SCHEMA_CHECK} not found yet.")
            return False
    except Exception as e:
        print(f"  Trino not ready: {e}")
        return False

    print("  All services are ready.")
    return True


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

    Runs inside a PythonVirtualenvOperator with dbt-trino + boto3 installed
    into the venv. Fully self-contained — imports live inside the body because
    the callable is serialized into a subprocess, and we avoid `airflow.*`
    imports entirely because uv's venv doesn't inherit the Stackable Airflow
    image's site-packages reliably. The garagefs connection is read straight
    from the AIRFLOW_CONN_GARAGEFS env var (which Airflow sets on executor
    pods from the garagefs-airflow-credentials secret).
    """
    import json
    import os
    from pathlib import Path
    from urllib.parse import quote, urlparse, parse_qs, unquote
    import boto3
    from dbt.cli.main import dbtRunner

    run_id = quote(run_id_raw, safe="")

    conn_uri = os.environ["AIRFLOW_CONN_GARAGEFS"]
    parsed = urlparse(conn_uri)
    extras = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    s3 = boto3.client(
        "s3",
        endpoint_url=extras.get("endpoint_url", "http://garage.shared.svc.cluster.local:3900"),
        aws_access_key_id=unquote(parsed.username or ""),
        aws_secret_access_key=unquote(parsed.password or ""),
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


def _om_helpers():
    """Return shared OpenMetadata API helper functions."""
    import base64
    import json
    import urllib.request
    import urllib.error

    def om_request(path, method="GET", data=None, token=None):
        url = f"{OM_API}{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())

    def om_login():
        password_b64 = base64.b64encode(OM_ADMIN_PASSWORD.encode()).decode()
        result = om_request(
            "/users/login", method="POST",
            data={"email": OM_ADMIN_EMAIL, "password": password_b64},
        )
        print("  Logged in to OpenMetadata.")
        return result["accessToken"]

    return om_request, om_login


def trigger_om_metadata_ingestion(**context):
    """Trigger the OpenMetadata metadata ingestion pipeline and wait for completion."""
    import time

    om_request, om_login = _om_helpers()
    token = om_login()

    # Look up metadata ingestion pipeline by FQN
    pipeline = om_request(
        f"/services/ingestionPipelines/name/{METADATA_PIPELINE_FQN}", token=token
    )
    pipeline_id = pipeline["id"]
    print(f"  Found metadata pipeline: {METADATA_PIPELINE_FQN} (id={pipeline_id})")

    # Record start time before triggering (milliseconds for OM API)
    trigger_ts = int(time.time() * 1000)

    # Trigger the pipeline
    om_request(
        f"/services/ingestionPipelines/trigger/{pipeline_id}",
        method="POST",
        token=token,
    )
    print("  Metadata ingestion triggered. Waiting for completion...")

    # Poll for completion
    elapsed = 0
    while elapsed < INGESTION_TIMEOUT:
        time.sleep(INGESTION_POLL_INTERVAL)
        elapsed += INGESTION_POLL_INTERVAL

        try:
            now_ts = int(time.time() * 1000)
            status_resp = om_request(
                f"/services/ingestionPipelines/{METADATA_PIPELINE_FQN}/pipelineStatus"
                f"?startTs={trigger_ts}&endTs={now_ts}",
                token=token,
            )
        except Exception as e:
            print(f"  [{elapsed}s] Status check failed ({e}), will retry...")
            continue

        # ResultList<PipelineStatus>: {"data": [...], "paging": {...}}
        statuses = status_resp.get("data", [])
        if not statuses:
            print(f"  [{elapsed}s] No status available yet...")
            continue

        latest = statuses[0]
        state = latest.get("pipelineState", "unknown")
        print(f"  [{elapsed}s] Pipeline state: {state}")

        if state in ("success", "partialSuccess"):
            print("  Metadata ingestion completed successfully.")
            return
        elif state in ("failed", "error"):
            raise RuntimeError(
                f"Metadata ingestion pipeline failed with state: {state}"
            )

    raise TimeoutError(
        f"Metadata ingestion did not complete within {INGESTION_TIMEOUT}s"
    )


def trigger_om_dbt_ingestion(**context):
    """Trigger the OpenMetadata dbt ingestion pipeline via REST API."""
    om_request, om_login = _om_helpers()
    token = om_login()

    # Look up pipeline by FQN
    pipeline = om_request(f"/services/ingestionPipelines/name/{DBT_PIPELINE_FQN}", token=token)
    pipeline_id = pipeline["id"]
    print(f"  Found dbt pipeline: {DBT_PIPELINE_FQN} (id={pipeline_id})")

    # Trigger
    try:
        om_request(f"/services/ingestionPipelines/trigger/{pipeline_id}", method="POST", token=token)
        print("  dbt ingestion triggered successfully.")
    except Exception as e:
        print(f"  Warning: Could not trigger dbt ingestion: {e}")
        print("  The pipeline will run on its schedule or can be triggered from the OpenMetadata UI.")


with DAG(
    dag_id="dbt_tpch_demo",
    schedule="@once",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    is_paused_upon_creation=False,
    default_args={"retries": 1000},
) as dag:

    wait_for_services = PythonSensor(
        task_id="wait_for_services",
        python_callable=check_services_ready,
        poke_interval=30,
        timeout=900,
        mode="poke",
    )

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
        default_args={
            "retries": 1,
            "on_success_callback": upload_run_results_shard,
            "on_failure_callback": upload_run_results_shard,
        },
    )

    finalize = PythonVirtualenvOperator(
        task_id="finalize_dbt_artifacts",
        python_callable=finalize_dbt_artifacts,
        requirements=["dbt-trino", "boto3"],
        system_site_packages=False,
        python_version="3.12",
        op_kwargs={
            "dbt_project_path": str(DBT_PROJECT_PATH),
            "dbt_target_path": str(DBT_TARGET_PATH),
            "s3_bucket": S3_BUCKET,
            "s3_prefix": S3_PREFIX,
            "run_id_raw": "{{ dag_run.run_id }}",
        },
    )

    trigger_metadata_ingestion = PythonOperator(
        task_id="trigger_om_metadata_ingestion",
        python_callable=trigger_om_metadata_ingestion,
    )

    trigger_dbt_ingestion = PythonOperator(
        task_id="trigger_om_dbt_ingestion",
        python_callable=trigger_om_dbt_ingestion,
    )

    wait_for_services >> dbt_tasks >> finalize >> trigger_metadata_ingestion >> trigger_dbt_ingestion
