#!/usr/bin/env bash
# Writes a self-contained HTML page with the demo choreography (DEMO.md) and copy
# buttons for every snippet, filled in with the current cluster's node IP, ports and
# Cockpit bookmark snippet. Open it straight from disk (file://), no web server needed.
#
# Usage: ./scripts/demo-page.sh [output.html]    (default: ./demo.html, gitignored)
set -euo pipefail

out=${1:-demo.html}
here=$(cd "$(dirname "$0")" && pwd)

node_ip=$(kubectl -n platform get cm oidc-endpoints -o jsonpath='{.data.node-ip}')
[ -n "$node_ip" ] || { echo "No node IP in ConfigMap platform/oidc-endpoints" >&2; exit 1; }
context=$(kubectl config current-context)
bookmarks=$("$here/cockpit-bookmarks.sh")

NODE_IP="$node_ip" CONTEXT="$context" BOOKMARKS="$bookmarks" OUT="$out" python3 - <<'PY'
import html, os
from datetime import datetime, timezone

ip, ctx, out = os.environ["NODE_IP"], os.environ["CONTEXT"], os.environ["OUT"]
cockpit, om, keycloak = f"http://{ip}:30300", f"http://{ip}:30585", f"http://{ip}:30900"
generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

snippets = {}

def snippet(key, text, label="Copy"):
    snippets[key] = text
    return (f'<div class="snippet"><pre>{html.escape(text)}</pre>'
            f'<button data-copy="{key}">{label}</button></div>')

def link(url, text):
    return f'<a href="{url}" target="_blank" rel="noopener">{html.escape(text)}</a>'

q_clv = """SELECT customer_id, customer_name, account_balance, lifetime_net_revenue
FROM "hive-iceberg".demo.customer_lifetime_value
ORDER BY lifetime_net_revenue DESC LIMIT 10;"""
q_os = """SELECT customer_name, customer_nation, net_revenue
FROM "hive-iceberg".demo.order_summary
ORDER BY net_revenue DESC LIMIT 10;"""

body = f"""
<header>
  <h1>data2day demo</h1>
  <p class="meta">Cluster <code>{html.escape(ctx)}</code> · node IP <code>{ip}</code> · generated {generated}</p>
  <nav>{link(cockpit, "Cockpit")} {link(om, "OpenMetadata")} {link(keycloak + "/admin/master/console/#/stackable-demo", "Keycloak admin")}</nav>
</header>

<section>
  <h2>What it shows</h2>
  <p>Every Trino query is authorized by OPA. Two flavours, side by side in <code>hive-iceberg.demo</code>:</p>
  <ul>
    <li><b>Part A, hardcoded per group</b> (<code>customer_lifetime_value</code>): the policy names the
      columns to mask; <code>/pii</code> members see clear text.</li>
    <li><b>Part B, driven by OpenMetadata</b> (<code>order_summary</code>): no owner → no access, for
      everybody. Tagged <code>PII.Sensitive</code> → masked unless in <code>/pii</code>. No policy change, no deploy.</li>
  </ul>
</section>

<section>
  <h2>Setup</h2>
  <ol>
    <li>Firefox: <code>about:config</code> → <code>dom.securecontext.allowlist</code>, value:
      {snippet("allowlist", ip)}
      (Chrome: <code>chrome://flags/#unsafely-treat-insecure-origin-as-secure</code> = <code>{cockpit}</code>.)
      Without it Cockpit shows a 500 page.</li>
    <li>Open {link(cockpit, "Cockpit")}, log in as <code>demo-user</code>, press F12 → Console, type
      <code>allow pasting</code> once, then paste this snippet and press Enter. It adds bookmarks to all UIs and the editor tabs
      <i>Demo A</i> and <i>Demo B</i> with the queries below:
      {snippet("bookmarks", os.environ["BOOKMARKS"], "Copy setup snippet")}</li>
    <li>Second user (<code>demo-admin</code>) in a private window. Passwords:
      <code>secrets/manifests/keycloak-manifests/keycloak-demo-passwords.yaml</code>.</li>
  </ol>
</section>

<section>
  <h2>Part A: hardcoded masking per group</h2>
  <ol>
    <li>As <code>demo-user</code> in Cockpit, tab <i>Demo A</i>:
      {snippet("q_clv", q_clv)}
      <code>customer_name</code> = <code>***MASKED***</code>, <code>account_balance</code> = <code>NULL</code>.
      In OpenMetadata these columns have <b>no</b> tags: the masks come from the policy.</li>
    <li>{link(keycloak + "/admin/master/console/#/stackable-demo/users", "Keycloak → Users")} →
      <code>demo-user</code> → Groups → Join group → <code>pii</code>.</li>
    <li>Wait <b>~1 minute</b> (group lookup is cached), rerun the query: clear text.</li>
  </ol>
</section>

<section>
  <h2>Part B: governance from the catalog</h2>
  <ol>
    <li>Query a table without an owner, tab <i>Demo B</i>:
      {snippet("q_os", q_os)}
      <b>Access Denied</b>, also for <code>demo-admin</code>. The table is still listed, it just can't be read.</li>
    <li>{link(om + "/table/trino.hive-iceberg.demo.order_summary", "OpenMetadata → order_summary")} →
      set owner <code>Data Engineering</code>.</li>
    <li>Wait <b>~10 seconds</b>, rerun: works, <code>customer_name</code> in clear text.</li>
    <li>Column <code>customer_name</code> → add tag:
      {snippet("tag", "PII.Sensitive")}</li>
    <li>Wait <b>~10 seconds</b>, rerun: <code>customer_name</code> = <code>***MASKED***</code>. No policy changed.</li>
    <li>Optional: tag <code>net_revenue</code> too → <code>NULL</code>. As <code>demo-admin</code> both stay readable.</li>
  </ol>
</section>

<section>
  <h2>Reset after a rehearsal</h2>
  <ul>
    <li>Keycloak: remove <code>demo-user</code> from <code>/pii</code>.</li>
    <li>OpenMetadata, <code>order_summary</code>: remove the owner and the <code>PII.Sensitive</code> tags.</li>
  </ul>
</section>
"""

import json
page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>data2day demo</title>
<style>
  :root {{ --bg:#fbfbfa; --fg:#1d1d1b; --muted:#6b6b66; --card:#f0efec; --accent:#1f5fbf; --ok:#2e7d32; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#18181a; --fg:#e8e6e1; --muted:#9a988f; --card:#242427; --accent:#7aa7ff; --ok:#7bc47f; }}
  }}
  body {{ background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif; margin:0 auto; max-width:820px; padding:24px 16px 64px; }}
  h1 {{ margin:0 0 4px; font-size:28px; }}
  h2 {{ margin:32px 0 8px; font-size:20px; border-bottom:1px solid var(--card); padding-bottom:4px; }}
  .meta {{ color:var(--muted); margin:0 0 8px; font-size:14px; }}
  nav a {{ margin-right:16px; }}
  a {{ color:var(--accent); }}
  code {{ background:var(--card); padding:1px 5px; border-radius:4px; font-size:0.92em; }}
  li {{ margin:8px 0; }}
  .snippet {{ display:flex; gap:8px; align-items:flex-start; margin:8px 0; }}
  .snippet pre {{ flex:1; margin:0; background:var(--card); padding:10px 12px; border-radius:6px;
                 overflow-x:auto; max-height:9em; font-size:13px; }}
  button {{ flex:none; cursor:pointer; border:1px solid var(--accent); color:var(--accent); background:transparent;
           border-radius:6px; padding:6px 12px; font:inherit; font-size:14px; }}
  button.done {{ border-color:var(--ok); color:var(--ok); }}
</style>
</head>
<body>
{body}
<script>
const SNIPPETS = {json.dumps(snippets)};
async function copy(text) {{
  try {{ await navigator.clipboard.writeText(text); return true; }}
  catch (e) {{
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    const ok = document.execCommand('copy'); ta.remove(); return ok;
  }}
}}
document.querySelectorAll('button[data-copy]').forEach((b) => {{
  const label = b.textContent;
  b.addEventListener('click', async () => {{
    b.textContent = (await copy(SNIPPETS[b.dataset.copy])) ? 'Copied' : 'Copy failed';
    b.classList.add('done');
    setTimeout(() => {{ b.textContent = label; b.classList.remove('done'); }}, 1500);
  }});
}});
</script>
</body>
</html>
"""
open(out, "w").write(page)
print(f"Wrote {out} (node IP {ip}). Open it in the browser: file://{os.path.abspath(out)}")
PY
