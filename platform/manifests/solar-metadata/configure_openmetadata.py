"""OpenMetadata setup for the solar demo (idempotent).

1. Bot solar-lineage-bot (IngestionBotRole) whose JWT the Spark jobs use to send OpenLineage
   events to OpenMetadata; stored in the Secret solar-openlineage-token (key `token`).
2. Dashboard service `superset` with an hourly metadata pipeline, including lineage from the
   Trino tables (service `trino`) to Superset datasets and dashboards.
3. One run of the existing Trino metadata ingestion, so new solar tables appear right away
   (it also runs every 6 hours on its own).

Authenticates like configure-openmetadata (platform/manifests/openmetadata-init/): a Keycloak
password grant for demo-admin through the `openmetadata` client.
"""

import base64
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
BOT = "solar-lineage-bot"
SECRET = "solar-openlineage-token"
SUPERSET_URL = "http://simple-superset-node.platform.svc.cluster.local:8088"
K8S_SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


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


def ensure_lineage_bot(token):
    """Bot user with IngestionBotRole (edit lineage, create pipeline entities); returns its JWT."""
    role = om("GET", "/roles/name/IngestionBotRole", token=token)
    try:
        user_id = om("GET", f"/bots/name/{BOT}", token=token)["botUser"]["id"]
        print(f"Bot {BOT} exists")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        # PUT /users is not idempotent for bot users, so only create user + bot once.
        user_id = om("PUT", "/users", token=token, body={
            "name": BOT, "displayName": "Solar lineage (Spark OpenLineage)",
            "email": f"{BOT}@stackable.demo", "isBot": True,
            "roles": [role["id"]],
            "authenticationMechanism": {"authType": "JWT", "config": {"JWTTokenExpiry": "Unlimited"}}})["id"]
        om("PUT", "/bots", token=token, body={
            "name": BOT, "displayName": "Solar lineage (Spark OpenLineage)", "botUser": BOT,
            "description": "Sends OpenLineage events from the solar Spark jobs."})
        print(f"Created bot {BOT}")
    jwt = om("GET", f"/users/auth-mechanism/{user_id}", token=token).get("config", {}).get("JWTToken")
    if not jwt:
        raise SystemExit("OpenMetadata returned no JWT for the bot")
    return jwt


def store_secret(jwt):
    """Create or update the Secret the Spark jobs read the token from (not managed by ArgoCD)."""
    ns = open(f"{K8S_SA_DIR}/namespace").read().strip()
    headers = {"Authorization": "Bearer " + open(f"{K8S_SA_DIR}/token").read().strip(),
               "Content-Type": "application/json"}
    ctx = ssl.create_default_context(cafile=f"{K8S_SA_DIR}/ca.crt")
    base = f"https://kubernetes.default.svc/api/v1/namespaces/{ns}/secrets"
    body = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": SECRET, "namespace": ns},
            "type": "Opaque", "data": {"token": base64.b64encode(jwt.encode()).decode()}}
    try:
        urllib.request.urlopen(urllib.request.Request(base, data=json.dumps(body).encode(), method="POST",
                                                      headers=headers), context=ctx).read()
        print(f"Created Secret {SECRET}")
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise
        req = urllib.request.Request(f"{base}/{SECRET}", data=json.dumps({"data": body["data"]}).encode(),
                                     method="PATCH", headers={**headers, "Content-Type": "application/merge-patch+json"})
        urllib.request.urlopen(req, context=ctx).read()
        print(f"Updated Secret {SECRET}")


def ensure_superset(token):
    svc = om("PUT", "/services/dashboardServices", token=token, body={
        "name": "superset", "serviceType": "Superset",
        "description": "Apache Superset of the demo",
        "connection": {"config": {"type": "Superset", "hostPort": SUPERSET_URL, "connection": {
            "provider": "db", "username": os.environ["SUPERSET_USER"],
            "password": os.environ["SUPERSET_PASSWORD"]}}}})
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
    store_secret(ensure_lineage_bot(token))
    # Tables first, so the Superset lineage finds them.
    trigger(token, "trino.trino_metadata_ingestion")
    superset = ensure_superset(token)
    time.sleep(180)
    trigger(token, superset["fullyQualifiedName"])


if __name__ == "__main__":
    main()
