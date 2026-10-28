# Solar ingestion demo

Synthetic, physically plausible PV telemetry for German sites (capacities, orientations and
regions from the Marktstammdatenregister) flows in near real time through Apache NiFi and
Apache Kafka into an Apache Iceberg lakehouse, is refined with Apache Spark into bronze, silver
and gold, and ends in Apache Superset dashboards. Lineage from the Kafka topics to the
dashboards is recorded in OpenMetadata.

```
solar-generator ──HTTP──> NiFi "solar ingest" ──> Kafka solar-* ──> Spark bronze (streaming)
                                                                        │
                       Airflow: solar_bronze_watchdog / solar_dims / solar_silver (silver -> gold)
                                                                        │
             data2day.solar_bronze ──> solar_silver ──> solar_gold ──> Trino ──> Superset
                                                                        │
                                OpenMetadata: Kafka topics -> tables -> dashboards
```

## Components (ArgoCD Applications)

| Application | What it deploys |
|---|---|
| `solar-generator` | Generator Deployment (one replica; image `oci.stackable.tech/sandbox/solar-generator`, site catalogue baked in), Service `nifi-listenhttp` (port 8090 on the NiFi pods) |
| `solar-ingest` | Job that builds the NiFi process group `solar ingest` (ListenHTTP -> PublishKafka, one message per reading), Job that creates the Kafka topics with bounded retention |
| `solar-pipeline` | `SparkApplicationTemplate solar-spark`, ConfigMap `solar-spark-jobs` (`bronze.py`, `dims.py`, `silver.py`, `gold.py`, `solar_common.py`), Role for Airflow to manage SparkApplications. The DAGs are in `dags/solar_pipeline.py` |
| `spark-history` | Spark History Server behind an oauth2-proxy (Keycloak login), UI on NodePort 30188; event logs in Garage bucket `spark-logs` |
| `solar-dashboards` | Job that creates the Superset database connection, datasets, charts and four dashboards (`configure_superset.py`) |
| `solar-metadata` | Job that sets up OpenMetadata: Superset dashboard service with lineage to the `trino` service, pipeline service `solar-spark`, messaging service `kafka`, bot `solar-lineage-bot` and its token Secret |

All tables live in the `data2day` catalog (Iceberg via Lakekeeper, data in Garage S3), schemas
`solar_bronze`, `solar_silver`, `solar_gold`.

## Layers

- **Bronze** (`bronze.py`, Structured Streaming, trigger 1 min): each Kafka message unchanged, plus
  its Kafka coordinates and the ingest time; append-only, partitioned by day.
- **Master data** (`dims.py`, hourly): the generator publishes its active sites and the weather
  station grid to Kafka at startup and every 24 h; the job keeps the latest record per ID in
  `solar_silver.dim_site` and `dim_weather_station`.
- **Silver** (`silver.py`, every 10 min): incremental by Kafka offset. Checks with a reason per
  rejected row into `solar_silver.quarantine` (missing fields, unknown site, negative power,
  above capacity, output at night, duplicates), interval energy from the counter (resets and
  implausible jumps flagged), measured irradiance of the site's weather cell, expected output and
  performance ratio.
- **Gold** (`gold.py`, after silver): `site_15min` with a comparison against peers (same weather
  cell and orientation), `fleet_15min` per postcode, `site_daily`, `region_daily`,
  `fault_events`, `dq_daily`, `cloud_cover`, `watermark`. Recomputes only the affected slots and
  days, one `MERGE` per table.

## Operations

- **Bronze** is started and restarted by the Airflow DAG `solar_bronze_watchdog` (every 5 min).
  To restart it, delete the running `solar-bronze-*` SparkApplication; the next one resumes from
  its checkpoints. Never run two at once (the watchdog makes sure of that).
- **Batch job failures:** a failed SparkApplication is kept; its driver log is in OpenSearch
  (`logs-*`, field `cluster` = application name), Python tracebacks included. Airflow task logs
  are in S3 (`s3://airflow-logs/`).
- **Rebuild silver** (e.g. after a logic change): pause `solar_silver`, wait for running jobs,
  drop `solar_silver.inverter_readings`, `weather_readings`, `cloud_polygons`, `quarantine`,
  `processing_state` (not the `dim_*` tables), unpause. Gold follows incrementally; to rebuild it,
  drop its tables and `solar_gold.processing_state` as well.
- **Jobs that configure things** (`configure-solar-*`) are idempotent; bump the version suffix in
  the Job name to run them again after a change.
- **Order of changes:** Airflow pulls DAGs from Forgejo immediately, ArgoCD syncs every few
  minutes. Push manifests a DAG depends on (RBAC, ConfigMaps, templates) before the DAG change.

## Access

Superset connects to Trino as the service user `superset` and impersonates the logged-in user,
so the Trino OPA policies (`rego-trino-policies.yaml`) apply per user: `solar_silver` and
`solar_gold` are readable by everyone, `solar_bronze` only by admins. Keycloak users with the
`viewer` role get the Superset role `Solar Viewer` (access to the database connection) in
addition to Gamma. The setup Job queries as the local Superset `admin`, which maps to Trino
`admin`.

## Known limitations

- Lineage is table-level only. It is recorded by the jobs through the OpenMetadata lineage API:
  OpenMetadata 1.12.1 did not resolve OpenLineage events to the Trino tables, and the OpenLineage
  Spark listener names Lakekeeper tables by storage location.
- Positions of small sites are random points within their postcode area: illustrative, not real
  locations.

## Data sources and attribution

- Marktstammdatenregister – © Bundesnetzagentur für Elektrizität, Gas, Telekommunikation, Post und
  Eisenbahnen | dl-de/by-2-0
- Postcode areas and map tiles © OpenStreetMap contributors, ODbL-1.0
