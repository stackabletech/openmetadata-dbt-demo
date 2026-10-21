"""OpenMetadata and Trino helpers for the transformation DAGs (no DAG in this module).

Used by tpch_sqlmesh_dag.py. tpch_dbt_dag.py still has its own copies of the same functions.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

OM_HOST = "http://openmetadata:8585"
OM_API = f"{OM_HOST}/api/v1"
# OM runs in custom-oidc mode, so basic /users/login is forbidden. We get a bearer token
# from Keycloak (demo-admin password grant via the openmetadata client -> aud=openmetadata,
# accepted by OM). Password is injected into the executor pod as KEYCLOAK_DEMO_ADMIN_PASSWORD.
KEYCLOAK_TOKEN_URL = "http://keycloak-service.platform.svc.cluster.local:8080/realms/stackable-demo/protocol/openid-connect/token"
OM_OIDC_CLIENT_ID = "openmetadata"
OM_OIDC_CLIENT_SECRET = "openmetadata-secret"
OM_ADMIN_USER = "demo-admin"
METADATA_PIPELINE_FQN = "trino.trino_metadata_ingestion"
INGESTION_POLL_INTERVAL = 10  # seconds
INGESTION_TIMEOUT = 600  # seconds

OM_OWNER_TEAM = "data-engineering"


def om_request(path, method="GET", data=None, token=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(f"{OM_API}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def om_login():
    data = urllib.parse.urlencode({
        "grant_type": "password",
        "client_id": OM_OIDC_CLIENT_ID,
        "client_secret": OM_OIDC_CLIENT_SECRET,
        "username": OM_ADMIN_USER,
        "password": os.environ.get("KEYCLOAK_DEMO_ADMIN_PASSWORD", ""),
        "scope": "openid",
    }).encode()
    req = urllib.request.Request(
        KEYCLOAK_TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        token = json.loads(resp.read().decode())["access_token"]
    print("  Obtained OpenMetadata token from Keycloak.")
    return token


def check_services_ready(trino_schema):
    """Check that the OpenMetadata API responds and Trino has the schema `catalog.schema`."""
    try:
        with urllib.request.urlopen(f"{OM_API}/system/version", timeout=10) as resp:
            resp.read()
        print("  OpenMetadata API is reachable.")
    except Exception as e:
        print(f"  OpenMetadata not ready: {e}")
        return False

    try:
        from airflow.providers.trino.hooks.trino import TrinoHook

        catalog, schema = trino_schema.split(".", 1)
        records = TrinoHook(trino_conn_id="trino_default").get_records(
            f"SHOW SCHEMAS FROM \"{catalog}\" LIKE '{schema}'"
        )
        if not any(row[0] == schema for row in records):
            print(f"  Trino is up but schema {trino_schema} not found yet.")
            return False
        print(f"  Trino schema {trino_schema} exists.")
    except Exception as e:
        print(f"  Trino not ready: {e}")
        return False

    print("  All services are ready.")
    return True


def trigger_om_metadata_ingestion():
    """Trigger the OpenMetadata metadata ingestion of the Trino service and wait for it."""
    token = om_login()
    pipeline = om_request(f"/services/ingestionPipelines/name/{METADATA_PIPELINE_FQN}", token=token)
    print(f"  Found metadata pipeline: {METADATA_PIPELINE_FQN} (id={pipeline['id']})")

    trigger_ts = int(time.time() * 1000)
    om_request(f"/services/ingestionPipelines/trigger/{pipeline['id']}", method="POST", token=token)
    print("  Metadata ingestion triggered. Waiting for completion...")

    elapsed = 0
    while elapsed < INGESTION_TIMEOUT:
        time.sleep(INGESTION_POLL_INTERVAL)
        elapsed += INGESTION_POLL_INTERVAL
        try:
            # Keycloak access tokens expire (~300s) before INGESTION_TIMEOUT, so refresh each poll.
            token = om_login()
            now_ts = int(time.time() * 1000)
            status_resp = om_request(
                f"/services/ingestionPipelines/{METADATA_PIPELINE_FQN}/pipelineStatus"
                f"?startTs={trigger_ts}&endTs={now_ts}",
                token=token,
            )
        except Exception as e:
            print(f"  [{elapsed}s] Status check failed ({e}), will retry...")
            continue

        statuses = status_resp.get("data", [])
        if not statuses:
            print(f"  [{elapsed}s] No status available yet...")
            continue
        state = statuses[0].get("pipelineState", "unknown")
        print(f"  [{elapsed}s] Pipeline state: {state}")
        if state in ("success", "partialSuccess"):
            print("  Metadata ingestion completed successfully.")
            return
        if state in ("failed", "error"):
            raise RuntimeError(f"Metadata ingestion pipeline failed with state: {state}")

    raise TimeoutError(f"Metadata ingestion did not complete within {INGESTION_TIMEOUT}s")


def set_om_owners(table_fqns):
    """Give the tables `table_fqns` (OpenMetadata FQNs) the owning team OM_OWNER_TEAM.

    Tables that already have an owner are left alone, so manual changes survive a rerun.
    """
    token = om_login()
    team = om_request("/teams", method="PUT", token=token, data={
        "name": OM_OWNER_TEAM,
        "displayName": "Data Engineering",
        "teamType": "Group",
        "description": "Owns the marts of the TPC-H demo.",
    })
    print(f"  Team '{OM_OWNER_TEAM}' ready (id={team['id']}).")

    for fqn in table_fqns:
        entity = om_request(f"/tables/name/{urllib.parse.quote(fqn, safe='')}?fields=owners", token=token)
        owners = entity.get("owners") or []
        if owners:
            print(f"  {fqn}: already owned by {[o.get('name') for o in owners]}, leaving it.")
            continue
        om_request(
            f"/tables/{entity['id']}", method="PATCH", token=token,
            content_type="application/json-patch+json",
            data=[{"op": "add", "path": "/owners", "value": [{"id": team["id"], "type": "team"}]}],
        )
        print(f"  {fqn}: owner set to team '{OM_OWNER_TEAM}'.")
