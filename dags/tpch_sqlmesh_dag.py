"""SQLMesh version of dbt_tpch_demo: the same staging views and marts, built by SQLMesh.

The project lives in dags/sqlmesh/tpch_demo (models ported 1:1 from dags/dbt/tpch_demo, plus
audits on each mart's grain). It runs in one task, not one pod per model as with Cosmos:
`sqlmesh plan` applies model changes to the prod environment, `sqlmesh run` refreshes models
whose cron interval is due.

Output: views in data2day.demo_sqlmesh, backed by versioned tables in
data2day.sqlmesh__demo_sqlmesh. SQLMesh's state is in PostgreSQL (postgresql-sqlmesh.shared).
The same OPA rules as for data2day.demo apply (rego-trino-policies.yaml).
"""
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator, PythonVirtualenvOperator
from airflow.sensors.python import PythonSensor

from openmetadata_helpers import check_services_ready, set_om_owners, trigger_om_metadata_ingestion

SQLMESH_PROJECT_PATH = Path("/stackable/app/git-0/current/dags/sqlmesh/tpch_demo")
SQLMESH_VERSION = "0.236.3"
SQLMESH_STATE_DB = ("postgresql-sqlmesh.shared.svc.cluster.local", 5432)

# Same as dbt_tpch_demo: every mart gets an owner except order_summary, so both schemas
# behave alike under the OpenMetadata-driven rules. The staging views stay unowned.
OM_OWNED_TABLES = [
    f"trino.data2day.demo_sqlmesh.{table}"
    for table in (
        "customer_lifetime_value",
        "part_pricing_analysis",
        "revenue_by_region",
        "shipping_analysis",
        "supplier_performance",
    )
]


def check_ready():
    """Services of the dbt DAG, plus SQLMesh's state database (deployed alongside, may lag)."""
    import socket

    try:
        socket.create_connection(SQLMESH_STATE_DB, timeout=5).close()
        print(f"  SQLMesh state database {SQLMESH_STATE_DB[0]} is reachable.")
    except OSError as e:
        print(f"  SQLMesh state database not ready: {e}")
        return False
    # data2day.demo comes from trino-init: once it exists, the Lakekeeper warehouse is usable.
    # SQLMesh creates its own schemas.
    return check_services_ready("data2day.demo")


def run_sqlmesh(project_path: str):
    """Apply the project to the prod environment and refresh due models.

    Runs inside a PythonVirtualenvOperator, so imports live inside the body. TRINO_PASSWORD
    for config.yaml comes from the executor pod's environment.
    """
    import shutil
    from pathlib import Path

    from sqlmesh import Context

    # SQLMesh writes .cache/ and logs/ next to config.yaml; the git-sync checkout is not ours to write to.
    work_dir = Path("/tmp/sqlmesh-tpch-demo")
    shutil.rmtree(work_dir, ignore_errors=True)
    shutil.copytree(project_path, work_dir)

    context = Context(paths=str(work_dir))
    # Changed models get new physical tables, then the prod views are swapped over.
    # Audits run after each model is built and fail the plan if they don't pass.
    context.plan("prod", auto_apply=True, no_prompts=True)
    # Rebuilds the FULL marts once their (default: daily) interval is due.
    context.run("prod")


with DAG(
    dag_id="sqlmesh_tpch_demo",
    description="TPC-H marts built by SQLMesh (same models as dbt_tpch_demo)",
    schedule="@once",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    is_paused_upon_creation=False,
    default_args={"retries": 1000},
) as dag:

    wait_for_services = PythonSensor(
        task_id="wait_for_services",
        python_callable=check_ready,
        poke_interval=60,
        timeout=2400,
        mode="reschedule",
    )

    sqlmesh_plan = PythonVirtualenvOperator(
        task_id="sqlmesh_plan_and_run",
        python_callable=run_sqlmesh,
        # psycopg2-binary instead of the extra sqlmesh[postgres], which builds psycopg2 from source.
        requirements=[f"sqlmesh[trino]=={SQLMESH_VERSION}", "psycopg2-binary"],
        system_site_packages=False,
        python_version="3.12",
        op_kwargs={"project_path": str(SQLMESH_PROJECT_PATH)},
        retries=1,
    )

    trigger_metadata_ingestion = PythonOperator(
        task_id="trigger_om_metadata_ingestion",
        python_callable=trigger_om_metadata_ingestion,
    )

    set_owners = PythonOperator(
        task_id="set_om_owners",
        python_callable=set_om_owners,
        op_kwargs={"table_fqns": OM_OWNED_TABLES},
    )

    wait_for_services >> sqlmesh_plan >> trigger_metadata_ingestion >> set_owners
