"""OpenMetadata setup for the solar demo (idempotent).

Lineage end to end: NiFi flow -> Kafka topics -> bronze -> silver -> gold -> Superset.

1. Messaging service `kafka` with the solar topics.
2. Pipeline service `nifi` with the pipeline `solar-ingest` (the NiFi process group "solar ingest",
   ListenHTTP -> PublishKafka) and lineage from it to the topics it publishes to. Created here
   rather than by OpenMetadata's NiFi connector: that connector only links process groups to each
   other, and NiFi accepts only OIDC logins or client certificates, neither of which the
   ingestion pods have.
3. Pipeline service `airflow`: OpenMetadata's Airflow connector reads the solar DAGs from the
   Airflow metadata database every 30 minutes, with their runs and the lineage the tasks declare
   (dags/solar_pipeline.py): topics -> bronze -> silver -> gold, the DAG on each edge.
4. Dashboard service `superset` with an hourly metadata pipeline, including lineage from the
   Trino tables (service `trino`) to Superset datasets and dashboards.
5. One run of the Trino metadata ingestion first, so new solar tables exist when the Airflow and
   Superset lineage is resolved (it also runs every 6 hours on its own).

Authenticates like configure-openmetadata (platform/manifests/openmetadata-init/): a Keycloak
password grant for demo-admin through the `openmetadata` client.
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

OM = "http://openmetadata.platform.svc.cluster.local:8585/api/v1"
KEYCLOAK_TOKEN_URL = ("http://keycloak-service.platform.svc.cluster.local:8080"
                      "/realms/stackable-demo/protocol/openid-connect/token")
SUPERSET_URL = "https://simple-superset-node.platform.svc.cluster.local:8088"
K8S_SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
TOPICS = ["solar-inverter", "solar-weather", "solar-clouds", "solar-sites", "solar-weather_stations"]
NIFI_PIPELINE = "solar-ingest"


def om(method, path, body=None, token=None):
    req = urllib.request.Request(OM + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode()
        return json.loads(text) if text else {}


def login():
    data = urllib.parse.urlencode({
        "grant_type": "password", "client_id": "openmetadata", "client_secret": "openmetadata-secret",
        "username": "demo-admin", "password": os.environ["OM_ADMIN_PASSWORD"], "scope": "openid"}).encode()
    for i in range(60):
        try:
            token = json.loads(urllib.request.urlopen(
                urllib.request.Request(KEYCLOAK_TOKEN_URL, data=data, method="POST"), timeout=30).read())["access_token"]
            om("GET", "/system/version", token=token)
            return token
        except Exception as e:
            print(f"Waiting for Keycloak and OpenMetadata ({i + 1}/60): {e}")
            time.sleep(10)
    raise SystemExit("OpenMetadata not reachable")


def node_port(service):
    """NodePort of a Service (the NiFi UI's port is assigned by its listener on every deploy)."""
    ns = open(f"{K8S_SA_DIR}/namespace").read().strip()
    req = urllib.request.Request(f"https://kubernetes.default.svc/api/v1/namespaces/{ns}/services/{service}",
                                 headers={"Authorization": "Bearer " + open(f"{K8S_SA_DIR}/token").read().strip()})
    ctx = ssl.create_default_context(cafile=f"{K8S_SA_DIR}/ca.crt")
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read())["spec"]["ports"][0]["nodePort"]


def ensure_kafka(token):
    om("PUT", "/services/messagingServices", token=token, body={
        "name": "kafka", "serviceType": "Kafka",
        "description": "Apache Kafka of the demo (solar topics; fed by the NiFi flow solar ingest).",
        "connection": {"config": {"type": "Kafka", "bootstrapServers": os.environ.get(
            "KAFKA", "kafka-broker-default-bootstrap.platform.svc.cluster.local:9093")}}})
    for topic in TOPICS:
        om("PUT", "/topics", token=token, body={"name": topic, "service": "kafka", "partitions": 1})
    print(f"Messaging service kafka ({len(TOPICS)} topics)")


def ensure_nifi(token):
    nifi_url = f"https://{os.environ['NODE_IP']}:{node_port('nifi-node')}/nifi/"
    om("PUT", "/services/pipelineServices", token=token, body={
        "name": "nifi", "serviceType": "Nifi",
        "description": "Apache NiFi of the demo. Pipelines are registered by the solar-metadata Job.",
        "connection": {"config": {"type": "Nifi", "hostPort": nifi_url}}})
    pipeline = om("PUT", "/pipelines", token=token, body={
        "name": NIFI_PIPELINE, "displayName": "solar ingest", "service": "nifi", "sourceUrl": nifi_url,
        "description": "NiFi process group *solar ingest*: receives the generator's readings over HTTP "
                       "(ListenHTTP, port 8090) and publishes each one to the Kafka topic "
                       "solar-<stream> (PublishKafka). Built by the Job configure-solar-ingest.",
        "tasks": [
            {"name": "ListenHTTP", "description": "Receives readings, one per line, at /<stream>",
             "downstreamTasks": ["PublishKafka"]},
            {"name": "PublishKafka", "description": "One Kafka message per reading, topic solar-${stream}"},
        ]})
    for topic in TOPICS:
        topic_id = om("GET", f"/topics/name/kafka.{topic}", token=token)["id"]
        om("PUT", "/lineage", token=token, body={"edge": {
            "fromEntity": {"id": pipeline["id"], "type": "pipeline"},
            "toEntity": {"id": topic_id, "type": "topic"},
            "lineageDetails": {"source": "Manual", "description": "NiFi PublishKafka"}}})
    print(f"Pipeline service nifi, pipeline {NIFI_PIPELINE} -> {len(TOPICS)} topics ({nifi_url})")


def ensure_airflow(token):
    db = urllib.parse.urlsplit(os.environ["AIRFLOW_DB_URI"])
    svc = om("PUT", "/services/pipelineServices", token=token, body={
        "name": "airflow", "serviceType": "Airflow",
        "description": "Apache Airflow of the demo (solar DAGs, read from its metadata database).",
        "connection": {"config": {
            "type": "Airflow", "hostPort": f"http://{os.environ['NODE_IP']}:30134", "numberOfStatus": 10,
            "connection": {"type": "Postgres", "username": urllib.parse.unquote(db.username),
                           "authType": {"password": urllib.parse.unquote(db.password)},
                           "hostPort": f"{db.hostname}:{db.port or 5432}", "database": db.path.lstrip("/")}}}})
    pipeline = om("PUT", "/services/ingestionPipelines", token=token, body={
        "name": "airflow_metadata_ingestion", "pipelineType": "metadata",
        "service": {"id": svc["id"], "type": "pipelineService"},
        "sourceConfig": {"config": {"type": "PipelineMetadata", "includeLineage": True,
                                    "markDeletedPipelines": True,
                                    "pipelineFilterPattern": {"includes": ["^solar_.*"]}}},
        "airflowConfig": {"scheduleInterval": "*/30 * * * *"}})
    om("POST", f"/services/ingestionPipelines/deploy/{pipeline['id']}", token=token)
    print(f"Pipeline service airflow and pipeline {pipeline['fullyQualifiedName']} deployed")
    return pipeline


def ensure_superset(token):
    svc = om("PUT", "/services/dashboardServices", token=token, body={
        "name": "superset", "serviceType": "Superset",
        "description": "Apache Superset of the demo",
        "connection": {"config": {"type": "Superset", "hostPort": SUPERSET_URL, "connection": {
            "provider": "db", "username": os.environ["SUPERSET_USER"],
            "password": os.environ["SUPERSET_PASSWORD"],
            # The ingestion pods don't have the secret-operator CA; in-cluster traffic only.
            "verifySSL": "ignore"}}}})
    pipeline = om("PUT", "/services/ingestionPipelines", token=token, body={
        "name": "superset_metadata_ingestion", "pipelineType": "metadata",
        "service": {"id": svc["id"], "type": "dashboardService"},
        "sourceConfig": {"config": {"type": "DashboardMetadata", "includeDataModels": True,
                                    "markDeletedDashboards": True,
                                    "lineageInformation": {"dbServicePrefixes": ["trino"]}}},
        "airflowConfig": {"scheduleInterval": "0 * * * *"}})
    om("POST", f"/services/ingestionPipelines/deploy/{pipeline['id']}", token=token)
    print(f"Dashboard service superset and pipeline {pipeline['fullyQualifiedName']} deployed")
    return pipeline


def trigger(token, pipeline_fqn):
    pipeline = om("GET", f"/services/ingestionPipelines/name/{pipeline_fqn}", token=token)
    result = om("POST", f"/services/ingestionPipelines/trigger/{pipeline['id']}", token=token)
    print(f"Triggered {pipeline_fqn}: {result.get('reason', '')[:120]}")


def main():
    token = login()
    ensure_kafka(token)
    ensure_nifi(token)
    airflow = ensure_airflow(token)
    superset = ensure_superset(token)
    # Tables first, so the Airflow and Superset lineage finds them.
    trigger(token, "trino.trino_metadata_ingestion")
    time.sleep(180)
    trigger(token, airflow["fullyQualifiedName"])
    trigger(token, superset["fullyQualifiedName"])


if __name__ == "__main__":
    main()
