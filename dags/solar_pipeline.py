# Solar ingestion demo: Spark batch jobs, run as SparkApplications by the Stackable operator.
#
# Each task creates a minimal SparkApplication (image, mode, script) that references the
# SparkApplicationTemplate solar-spark (platform/manifests/solar-pipeline/), waits until it has
# finished and deletes it after success. The bronze streaming job runs indefinitely; the
# watchdog DAG starts a new one whenever none is running. The run stays visible in the Spark History Server.
# On failure the SparkApplication is kept; the driver log is in OpenSearch (logs-*, field
# `cluster` = the application name).
#
# Lineage: every task declares what its Spark job reads and writes (`lineage()`), twice:
#   - as Airflow assets: the asset graph in the Airflow UI, and solar_gold runs whenever
#     solar_silver has updated inverter_readings;
#   - as OpenMetadata entity dicts, which the OpenMetadata Airflow connector (pipeline service
#     `airflow`, set up by platform/manifests/solar-metadata/) turns into lineage edges with the
#     DAG as pipeline. OpenMetadata 1.12 ignores Airflow assets, hence the second form.
# The declarations must match what the scripts in the ConfigMap solar-spark-jobs do.
#
# Needs the Role airflow-spark-applications (platform/manifests/solar-pipeline/airflow-rbac.yaml).
import time
from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.sdk import Asset

NAMESPACE = "platform"
TEMPLATE = "solar-spark"
SPARK_VERSION = "3.5.8"
GROUP, VERSION, PLURAL = "spark.stackable.tech", "v1alpha1", "sparkapplications"
TIMEOUT = timedelta(minutes=30)
TRINO = "trino-coordinator.platform.svc.cluster.local:8443"
KAFKA = "kafka-broker-default-bootstrap.platform.svc.cluster.local:9093"


def asset(name):
    """Airflow asset for a table `schema.table` in catalog data2day or a Kafka topic `topic:<name>`."""
    if name.startswith("topic:"):
        topic = name[len("topic:"):]
        return Asset(uri=f"kafka://{KAFKA}/{topic}", name=f"kafka.{topic}")
    schema, table = name.split(".")
    return Asset(uri=f"trino://{TRINO}/data2day/{schema}/{table}", name=f"data2day.{name}")


def om_entity(name, key):
    """OpenMetadata entity reference for the Airflow connector: Trino service `trino`, Kafka service `kafka`."""
    if name.startswith("topic:"):
        return {"entity": "topic", "fqn": "kafka." + name[len("topic:"):], "key": key}
    return {"entity": "table", "fqn": "trino.data2day." + name, "key": key}


def lineage(edges, emit=True):
    """inlets/outlets for a task whose job writes each output from its inputs: {output: [inputs]}.

    OpenMetadata pairs every inlet with every outlet of the same key in a DAG, so each output gets
    its own key and the edges stay exact. emit=False leaves the outputs out of the Airflow assets,
    so the task records no asset events (the outputs' OpenMetadata lineage stays).
    """
    inputs = sorted({i for sources in edges.values() for i in sources})
    inlets = [asset(i) for i in inputs]
    outlets = [asset(o) for o in edges] if emit else []
    for output, sources in edges.items():
        inlets += [om_entity(i, output) for i in sources]
        outlets.append(om_entity(output, output))
    return {"inlets": inlets, "outlets": outlets}


# What each Spark job reads and writes: {output: [inputs]}.
BRONZE = {f"solar_bronze.{stream}_raw": [f"topic:solar-{stream}"] for stream in ("inverter", "weather", "clouds")}
DIMS = {
    "solar_silver.dim_site": ["topic:solar-sites"],
    "solar_silver.dim_weather_station": ["topic:solar-weather_stations"],
}
SILVER = {
    "solar_silver.weather_readings": ["solar_bronze.weather_raw", "solar_silver.dim_weather_station"],
    "solar_silver.inverter_readings": ["solar_bronze.inverter_raw", "solar_silver.dim_site",
                                       "solar_silver.weather_readings"],
    "solar_silver.cloud_polygons": ["solar_bronze.clouds_raw"],
    "solar_silver.quarantine": ["solar_bronze.weather_raw", "solar_bronze.inverter_raw",
                                "solar_bronze.clouds_raw"],
}
GOLD = {
    "solar_gold.site_15min": ["solar_silver.inverter_readings"],
    "solar_gold.fleet_15min": ["solar_gold.site_15min"],
    "solar_gold.site_daily": ["solar_gold.site_15min"],
    "solar_gold.region_daily": ["solar_gold.site_daily"],
    "solar_gold.fault_events": ["solar_gold.site_15min"],
    "solar_gold.dq_daily": ["solar_silver.quarantine"],
    "solar_gold.cloud_cover": ["solar_silver.cloud_polygons"],
    "solar_gold.watermark": ["solar_silver.inverter_readings"],
}


def spark_api():
    from kubernetes import client, config

    config.load_incluster_config()
    return client.CustomObjectsApi()


def create_spark_app(api, script):
    """Create a minimal SparkApplication for jobs/<script>.py from the template; return its name."""
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
    return name


def run_spark_job(script):
    """Run a batch job to completion; delete it after success, keep it after failure."""
    api = spark_api()
    name = create_spark_app(api, script)
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


def ensure_bronze():
    """Keep exactly one bronze streaming job running.

    Any solar-bronze* SparkApplication that hasn't finished counts as running (also one that is
    still being submitted), so two drivers never write to the same checkpoints. Finished ones
    are cleaned up except the newest, which is kept for inspection.
    """
    api = spark_api()
    apps = [a for a in api.list_namespaced_custom_object(GROUP, VERSION, NAMESPACE, PLURAL)["items"]
            if a["metadata"]["name"].startswith("solar-bronze")]
    active = [a["metadata"]["name"] for a in apps
              if (a.get("status") or {}).get("phase") not in ("Succeeded", "Failed")]
    if active:
        print(f"Bronze is running: {', '.join(active)}")
        return
    finished = sorted(apps, key=lambda a: a["metadata"]["creationTimestamp"])
    for app in finished[:-1]:
        api.delete_namespaced_custom_object(GROUP, VERSION, NAMESPACE, PLURAL, app["metadata"]["name"])
        print(f"Deleted finished {app['metadata']['name']}")
    if finished:
        print(f"Last bronze job {finished[-1]['metadata']['name']} ended "
              f"({(finished[-1].get('status') or {}).get('phase')}); starting a new one")
    create_spark_app(api, "bronze")


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
        **lineage(DIMS),
    )


with DAG(
    dag_id="solar_silver",
    description="Solar demo: bronze -> silver (checks, quarantine, energy, performance ratio)",
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
        **lineage(SILVER),
    )


with DAG(
    dag_id="solar_gold",
    description="Solar demo: silver -> gold (aggregates, fault events, data quality), after each silver run",
    start_date=datetime(2026, 10, 27),
    schedule=[asset("solar_silver.inverter_readings")],
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    tags=["solar"],
) as dag:
    PythonOperator(
        task_id="gold",
        python_callable=run_spark_job,
        op_kwargs={"script": "gold"},
        **lineage(GOLD),
    )


with DAG(
    dag_id="solar_bronze_watchdog",
    description="Solar demo: keep the bronze streaming job (Kafka -> Iceberg) running",
    start_date=datetime(2026, 10, 27),
    schedule="*/5 * * * *",
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    tags=["solar"],
) as dag:
    # The streaming job writes bronze, not this task: no asset events every 5 minutes.
    PythonOperator(task_id="ensure_bronze", python_callable=ensure_bronze, **lineage(BRONZE, emit=False))
