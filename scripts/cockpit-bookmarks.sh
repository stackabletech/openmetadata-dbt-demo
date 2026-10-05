#!/usr/bin/env bash
# Prints a JavaScript snippet that stores bookmarks to the demo UIs in Stackable
# Cockpit's dashboard (browser localStorage, key `dashboard_bookmarks`; needs a
# Cockpit build with the bookmark feature, e.g. branch feat/app-bookmark-dialog).
#
# The URLs are read from the current cluster (kubectl context), because the node IP
# and several NodePorts (Trino, Superset, Airflow, HDFS) change with every deploy.
#
# Usage:
#   ./scripts/cockpit-bookmarks.sh            # print the snippet
#   ./scripts/cockpit-bookmarks.sh | wl-copy  # or pbcopy / xclip -sel clip
# Then open Cockpit (http://<node-ip>:30300) in the browser you present with, open
# the developer console (F12 -> Console), paste and press Enter. Firefox asks you to
# type "allow pasting" once before it accepts pasted code.
#
# Existing bookmarks are kept; bookmarks this script created before (same name)
# are replaced, so it can be rerun after a redeploy.
set -euo pipefail

node_ip=$(kubectl -n platform get cm oidc-endpoints -o jsonpath='{.data.node-ip}')
[ -n "$node_ip" ] || { echo "No node IP in ConfigMap platform/oidc-endpoints" >&2; exit 1; }

nodeport() { # <namespace> <service>
  kubectl -n "$1" get svc "$2" -o jsonpath='{.spec.ports[0].nodePort}'
}

# productId | name | url | pinned (shown in the sidebar)
bookmarks=$(cat <<EOF
trino|Trino|https://${node_ip}:$(nodeport platform trino-coordinator)/ui/|true
superset|Superset|http://${node_ip}:$(nodeport platform simple-superset-node)|true
airflow|Airflow|http://${node_ip}:$(nodeport platform airflow-webserver)|true
custom|OpenMetadata|http://${node_ip}:$(nodeport platform openmetadata-nodeport)|true
hdfs|HDFS NameNode|http://${node_ip}:$(nodeport platform listener-simple-hdfs-namenode-default-0)|false
opensearch|OpenSearch Dashboards|http://${node_ip}:$(nodeport platform opensearch-dashboards-nodeport)|false
custom|Keycloak|http://${node_ip}:$(nodeport platform keycloak-nodeport)|false
custom|ArgoCD|http://${node_ip}:$(nodeport deployment argocd-server-nodeport)|false
custom|Forgejo|http://${node_ip}:$(nodeport deployment forgejo-http-nodeport)|false
custom|Demo landing page|http://${node_ip}:$(nodeport deployment demo-landing)|false
EOF
)

BOOKMARKS="$bookmarks" python3 - <<'PY'
import json, os, uuid
from datetime import datetime, timezone

now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
items = []
for line in os.environ["BOOKMARKS"].strip().splitlines():
    product_id, name, url, pinned = line.split("|")
    items.append({
        "id": str(uuid.uuid4()),
        "productId": product_id,
        "name": name,
        "environment": "data2day",
        "url": url,
        "pinned": pinned == "true",
        "pinnedForEveryone": False,
        "createdAt": now,
    })

print(f"""(() => {{
  const KEY = 'dashboard_bookmarks';
  const demo = {json.dumps(items, indent=2)};
  const names = new Set(demo.map((b) => b.name));
  let existing = [];
  try {{ existing = JSON.parse(localStorage.getItem(KEY) || '[]'); }} catch (e) {{}}
  const kept = existing.filter((b) => !(b.environment === 'data2day' && names.has(b.name)));
  localStorage.setItem(KEY, JSON.stringify([...kept, ...demo]));
  console.log(`Stored ${{demo.length}} demo bookmarks (kept ${{kept.length}} others), reloading ...`);
  location.reload();
}})();""")
PY
