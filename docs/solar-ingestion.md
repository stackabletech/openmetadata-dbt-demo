# Solar ingestion demo

Synthetic, physically plausible PV telemetry for German sites (capacities, orientations and
regions from the Marktstammdatenregister) flows in near real time through Apache NiFi and
Apache Kafka into an Apache Iceberg lakehouse, is refined with Apache Spark into bronze, silver
and gold, and ends in Apache Superset dashboards. Lineage from the NiFi flow to the dashboards
is recorded in OpenMetadata.

```
solar-generator ──HTTP──> NiFi "solar ingest" ──> Kafka solar-* ──> Spark bronze (streaming)
                                                                        │
      Airflow: solar_bronze_watchdog / solar_dims / solar_silver -> (asset) -> solar_gold
                                                                        │
             data2day.solar_bronze ──> solar_silver ──> solar_gold ──> Trino ──> Superset
                                                                        │
             OpenMetadata: NiFi flow -> Kafka topics -> tables -> dashboards
```

## Components (ArgoCD Applications)

| Application | What it deploys |
|---|---|
| `solar-generator` | Generator Deployment (one replica; image `oci.stackable.tech/sandbox/solar-generator`, site catalogue baked in), Service `nifi-listenhttp` (port 8090 on the NiFi pods) |
| `solar-ingest` | Job that builds the NiFi process group `solar ingest` (ListenHTTP -> PublishKafka, one message per reading), Job that creates the Kafka topics with bounded retention |
| `solar-pipeline` | `SparkApplicationTemplate solar-spark`, ConfigMap `solar-spark-jobs` (`bronze.py`, `dims.py`, `silver.py`, `gold.py`, `maintenance.py`, `solar_common.py`), Role for Airflow to manage SparkApplications. The DAGs, with the lineage of each job, are in `dags/solar_pipeline.py` |
| `spark-history` | Spark History Server behind an oauth2-proxy (Keycloak login), UI on NodePort 30188; event logs in Garage bucket `spark-logs` |
| `solar-dashboards` | Job that creates the Superset database connection, datasets, charts and four dashboards (`configure_superset.py`) |
| `solar-metadata` | Job that sets up OpenMetadata: messaging service `kafka`, pipeline service `nifi` (flow `solar-ingest` -> topics), pipeline service `airflow` (Airflow connector, solar DAGs and their lineage), dashboard service `superset` with lineage to the `trino` service |

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
- **Gold** (`gold.py`, DAG `solar_gold`, runs whenever silver has updated `inverter_readings`): `site_15min` with a comparison against peers (same weather
  cell and orientation), `fleet_15min` per postcode, `site_daily`, `region_daily`,
  `fault_events`, `dq_daily`, `cloud_cover`, `watermark`. Recomputes only the affected slots and
  days, one `MERGE` per table.

- **Maintenance** (`maintenance.py`, DAG `solar_maintenance`, hourly): retention (bronze 2 days,
  silver readings 7 days, gold kept), snapshot expiry, orphan file removal, and old metadata
  files deleted on commit. Spark event logs roll; the History Server deletes logs older than
  2 days.

## Operations

- **Bronze** is started and restarted by the Airflow DAG `solar_bronze_watchdog` (every 5 min).
  To restart it, delete the running `solar-bronze-*` SparkApplication; the next one resumes from
  its checkpoints. Never run two at once (the watchdog makes sure of that).
- **Storage:** Garage has 3 x 10 GiB data volumes (replication 2). `garage stats -a` in a
  `garage-*` pod shows the free space. If it runs full, writes hang in S3 retries and Garage
  can lose blocks of objects it has already acknowledged.
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

## Lineage

Each DAG task in `dags/solar_pipeline.py` declares what its Spark job reads and writes, as
Airflow assets (asset graph in the Airflow UI; `solar_gold` is scheduled on silver's asset) and
as OpenMetadata entity references. OpenMetadata's Airflow connector (pipeline service `airflow`,
every 30 minutes) turns the latter into lineage edges with the DAG as pipeline: OpenMetadata
1.12.1 ignores Airflow assets. The Superset connector adds gold -> datasets -> charts ->
dashboards, and the solar-metadata Job the NiFi flow -> topics. The declarations in the DAGs
must be kept in line with the Spark scripts: the jobs don't report lineage themselves.

## Access

Superset is served over HTTPS (certificate of the secret-operator SecretClass `tls`, set up in
`superset.yaml` through gunicorn; browsers warn about the demo CA). It connects to Trino as the
service user `superset` and impersonates the logged-in user, so the Trino OPA policies
(`rego-trino-policies.yaml`) apply per user: `solar_silver` and `solar_gold` are readable by
everyone, `solar_bronze` only by admins. Keycloak users with the `viewer` role get the Superset
role `Solar Viewer` (access to the database connection) in addition to Gamma. The setup Job
queries as the local Superset `admin`, which maps to Trino `admin`.

## Known limitations

- Lineage is table-level and declared, not observed. OpenLineage from Spark was not used:
  OpenMetadata 1.12.1 did not resolve OpenLineage events to the Trino tables, and the OpenLineage
  Spark listener names Lakekeeper tables by storage location.
- Positions of small sites are random points within their postcode area: illustrative, not real
  locations.

## Data sources and attribution

- Marktstammdatenregister – © Bundesnetzagentur für Elektrizität, Gas, Telekommunikation, Post und
  Eisenbahnen | dl-de/by-2-0
- Postcode areas and map tiles © OpenStreetMap contributors, ODbL-1.0
