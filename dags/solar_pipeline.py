# Solar ingestion demo: Spark batch jobs, run as SparkApplications by the Stackable operator.
#
# Each task creates a minimal SparkApplication (image, mode, script) that references the
# SparkApplicationTemplate solar-spark (platform/manifests/solar-pipeline/), waits until it has
# finished and deletes it after success. The run stays visible in the Spark History Server.
# On failure the SparkApplication is kept; the driver log is in OpenSearch (logs-*, field
# `cluster` = the application name).
#
# Needs the Role airflow-spark-applications (platform/manifests/solar-pipeline/airflow-rbac.yaml).
import time
from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.operators.python import PythonOperator

NAMESPACE = "platform"
TEMPLATE = "solar-spark"
SPARK_VERSION = "3.5.8"
GROUP, VERSION, PLURAL = "spark.stackable.tech", "v1alpha1", "sparkapplications"
TIMEOUT = timedelta(minutes=30)


def run_spark_job(script):
    from kubernetes import client, config

    config.load_incluster_config()
    api = client.CustomObjectsApi()
    # Unique per start; Kubernetes names are lowercase, at most 63 characters. Not derived from
    # the logical date: in Airflow 3 manually triggered runs have none.
    name = f"solar-{script}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    api.create_namespaced_custom_object(GROUP, VERSION, NAMESPACE, PLURAL, {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "SparkApplication",
        "metadata": {
            "name": name,
            "annotations": {
                "spark-application.template.merge": "true",
                "spark-application.template.0.name": TEMPLATE,
            },
        },
        "spec": {
            "mode": "cluster",
            "sparkImage": {"productVersion": SPARK_VERSION},
            "mainApplicationFile": f"local:///stackable/spark/jobs/{script}.py",
        },
    })
    print(f"Created SparkApplication {name}")

    deadline = datetime.now() + TIMEOUT
    phase = None
    while datetime.now() < deadline:
        app = api.get_namespaced_custom_object(GROUP, VERSION, NAMESPACE, PLURAL, name)
        new_phase = (app.get("status") or {}).get("phase")
        if new_phase != phase:
            print(f"{name}: {new_phase}")
            phase = new_phase
        if phase == "Succeeded":
            api.delete_namespaced_custom_object(GROUP, VERSION, NAMESPACE, PLURAL, name)
            return
        if phase == "Failed":
            raise RuntimeError(f"SparkApplication {name} failed; driver log in OpenSearch "
                               f"(logs-*, cluster: {name}), kept for inspection")
        time.sleep(15)
    raise TimeoutError(f"SparkApplication {name} not finished after {TIMEOUT}")


with DAG(
    dag_id="solar_dims",
    description="Solar demo: master data from Kafka into solar_silver.dim_site / dim_weather_station",
    start_date=datetime(2026, 10, 27),
    schedule="@hourly",
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    tags=["solar"],
) as dag:
    PythonOperator(
        task_id="dims",
        python_callable=run_spark_job,
        op_kwargs={"script": "dims"},
    )


with DAG(
    dag_id="solar_silver",
    description="Solar demo: bronze -> silver (checks, quarantine, deduplication, energy, performance ratio)",
    start_date=datetime(2026, 10, 27),
    schedule="*/10 * * * *",
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    tags=["solar"],
) as dag:
    PythonOperator(
        task_id="silver",
        python_callable=run_spark_job,
        op_kwargs={"script": "silver"},
    )
