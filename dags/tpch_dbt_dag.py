# This file is an airflow DAG definition using Astronomer Cosmos for dbt orchestration.
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.sensors.python import PythonSensor
from cosmos import DbtTaskGroup, ProjectConfig, ProfileConfig, ExecutionConfig, RenderConfig
from cosmos.constants import ExecutionMode, LoadMode

DBT_PROJECT_PATH = Path("/stackable/app/git-0/current/dags/dbt/tpch_demo")
DBT_TARGET_PATH = Path("/shared/dbt-target")
S3_BUCKET = "dbt-artifacts"
S3_PREFIX = "tpch_demo"
ARTIFACTS = ["manifest.json", "catalog.json", "run_results.json"]

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
        default_args={"retries": 1},
    )

    upload_artifacts = PythonOperator(
        task_id="upload_dbt_artifacts",
        python_callable=upload_dbt_artifacts,
    )

    trigger_metadata_ingestion = PythonOperator(
        task_id="trigger_om_metadata_ingestion",
        python_callable=trigger_om_metadata_ingestion,
    )

    trigger_dbt_ingestion = PythonOperator(
        task_id="trigger_om_dbt_ingestion",
        python_callable=trigger_om_dbt_ingestion,
    )

    wait_for_services >> dbt_tasks >> upload_artifacts >> trigger_metadata_ingestion >> trigger_dbt_ingestion
