#!/usr/bin/env bash
# Prints a JavaScript snippet that prepares Stackable Cockpit for the demo, all in the
# browser's localStorage:
#   - bookmarks to the demo UIs in the dashboard (key `dashboard_bookmarks`; needs a
#     Cockpit build with the bookmark feature, e.g. branch feat/app-bookmark-dialog),
#   - Trino editor tabs with the demo queries (keys `trino_tabs_index`, `trino_tab_<id>`).
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
# Existing bookmarks and editor tabs are kept; the ones this script created before
# (same name / label) are replaced, so it can be rerun after a redeploy. Cockpit keeps
# at most 8 editor tabs; the demo tabs come first and the oldest others are dropped.
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
custom|Lakekeeper|http://${node_ip}:$(nodeport platform lakekeeper)/ui|false
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

# Trino editor tabs with the demo queries (same SQL as DEMO.md).
created_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
tabs = [
    {
        "id": str(uuid.uuid4()),
        "label": "Demo A: hardcoded masks",
        "createdAt": created_ms,
        "sql": "-- Part A: masks hardcoded in the policy, per Keycloak group /pii\n"
               "SELECT customer_id, customer_name, account_balance, lifetime_net_revenue\n"
               "FROM data2day.demo.customer_lifetime_value\n"
               "ORDER BY lifetime_net_revenue DESC LIMIT 10;",
    },
    {
        "id": str(uuid.uuid4()),
        "label": "Demo B: owner + tag",
        "createdAt": created_ms + 1,
        "sql": "-- Part B: access needs an owner in OpenMetadata, PII.Sensitive tags mask\n"
               "SELECT customer_name, customer_nation, net_revenue\n"
               "FROM data2day.demo.order_summary\n"
               "ORDER BY net_revenue DESC LIMIT 10;",
    },
]

print(f"""(() => {{
  // Dashboard bookmarks.
  const KEY = 'dashboard_bookmarks';
  const demo = {json.dumps(items, indent=2)};
  const names = new Set(demo.map((b) => b.name));
  let existing = [];
  try {{ existing = JSON.parse(localStorage.getItem(KEY) || '[]'); }} catch (e) {{}}
  const kept = existing.filter((b) => !(b.environment === 'data2day' && names.has(b.name)));
  localStorage.setItem(KEY, JSON.stringify([...kept, ...demo]));

  // Trino editor tabs (Cockpit keeps at most 8).
  const INDEX = 'trino_tabs_index', PREFIX = 'trino_tab_', MAX_TABS = 8;
  const demoTabs = {json.dumps(tabs, indent=2)};
  const labels = new Set(demoTabs.map((t) => t.label));
  let index = null;
  try {{ index = JSON.parse(localStorage.getItem(INDEX) || 'null'); }} catch (e) {{}}
  const oldTabs = (index && Array.isArray(index.tabs)) ? index.tabs : [];
  let others = oldTabs.filter((t) => !labels.has(t.label));
  for (const t of oldTabs) if (labels.has(t.label)) localStorage.removeItem(PREFIX + t.id);
  while (others.length + demoTabs.length > MAX_TABS) localStorage.removeItem(PREFIX + others.shift().id);
  for (const t of demoTabs) localStorage.setItem(PREFIX + t.id, t.sql);
  const allTabs = [...demoTabs.map(({{ sql, ...meta }}) => meta), ...others];
  localStorage.setItem(INDEX, JSON.stringify({{ tabs: allTabs, activeTabId: demoTabs[0].id }}));

  console.log(`Stored ${{demo.length}} bookmarks and ${{demoTabs.length}} editor tabs (kept ${{kept.length}} bookmarks, ${{others.length}} tabs), reloading ...`);
  location.reload();
}})();""")
PY
