# This file is an airflow DAG definition using Astronomer Cosmos for dbt orchestration.
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
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
DBT_PIPELINE_FQN = "trino.trino_dbt_ingestion"


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


def trigger_om_dbt_ingestion(**context):
    """Trigger the OpenMetadata dbt ingestion pipeline via REST API."""
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

    # Login
    password_b64 = base64.b64encode(OM_ADMIN_PASSWORD.encode()).decode()
    result = om_request(
        "/users/login", method="POST",
        data={"email": OM_ADMIN_EMAIL, "password": password_b64},
    )
    token = result["accessToken"]
    print("  Logged in to OpenMetadata.")

    # Look up pipeline by FQN
    pipeline = om_request(f"/services/ingestionPipelines/name/{DBT_PIPELINE_FQN}", token=token)
    pipeline_id = pipeline["id"]
    print(f"  Found dbt pipeline: {DBT_PIPELINE_FQN} (id={pipeline_id})")

    # Trigger
    om_request(f"/services/ingestionPipelines/trigger/{pipeline_id}", method="POST", token=token)
    print("  dbt ingestion triggered successfully.")


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

    trigger_om_ingestion = PythonOperator(
        task_id="trigger_om_dbt_ingestion",
        python_callable=trigger_om_dbt_ingestion,
    )

    dbt_tasks >> upload_artifacts >> trigger_om_ingestion
