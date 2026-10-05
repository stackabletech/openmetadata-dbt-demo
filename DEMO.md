# data2day demo: data governance driven by the catalog

## What this demo shows

The Stackable Data Platform (SDP) authorizes every Trino query through Open Policy Agent (OPA).
The demo shows two ways of writing those rules, side by side, on the same dbt marts in
`hive-iceberg.demo`:

1. **Hardcoded per group** (`customer_lifetime_value`). The Rego policy names the columns to mask.
   Members of the Keycloak group `/pii` see clear text, everyone else sees masked values. Changing
   who sees what means editing a group membership, but changing *what* is sensitive means editing
   the policy.
2. **Driven by OpenMetadata** (`order_summary`, and every other table in `hive-iceberg.demo`).
   The policy contains no table or column names. The OPA resource-info-fetcher asks OpenMetadata
   for each table's owner and tags:
   - **No owner → no access.** SELECT is denied for everybody, `/admin` included, until the table
     has an owner in OpenMetadata.
   - **Tagged `PII.Sensitive` → masked**, unless the user is in `/pii`.

   Data stewards govern access in the catalog, with no policy change and no deploy.

Both rules fail closed: if OpenMetadata can't be asked, access is denied and columns are masked.
Only the static Trino service account `admin` (dbt, OpenMetadata ingestion, Apache Airflow) is
exempt. The rules live in `platform/manifests/opa/rego-trino-policies.yaml`.

## Setup

### 1. Deploy

```bash
just demo <cluster-name> feat/data2day    # new AKS cluster
just deploy feat/data2day                 # existing cluster
```

Always pass the branch, the justfile defaults to `main`. Allow about 15 minutes. The demo is ready
when the Airflow DAG `dbt_tpch_demo` has finished: its task `set_om_owners` gives all marts except
`order_summary` the owner team `Data Engineering`.

### 2. Users

| User | Keycloak groups | Sees |
|---|---|---|
| `demo-user` | none | masked PII, no access to tables without an owner |
| `demo-admin` | `/admin`, `/pii` | clear text, but also no access to tables without an owner |

Passwords: `secrets/manifests/keycloak-manifests/keycloak-demo-passwords.yaml`.
Use a private window for the second user.

### 3. Browser

The node IP changes with every deploy:

```bash
kubectl -n platform get cm oidc-endpoints -o jsonpath='{.data.node-ip}'
```

- **Cockpit needs a secure context.** It runs on plain HTTP, so allow its origin once:
  `about:config` → `dom.securecontext.allowlist` = `<node-ip>` (Firefox), or
  `chrome://flags/#unsafely-treat-insecure-origin-as-secure` = `http://<node-ip>:30300` (Chrome).
  Without it, Cockpit shows a 500 page.
- **Bookmarks to all UIs** in the Cockpit dashboard:
  ```bash
  just cockpit-bookmarks | wl-copy    # or pbcopy / xclip -sel clip
  ```
  Open Cockpit (`http://<node-ip>:30300`), press F12 → Console, paste, press Enter. Firefox wants
  `allow pasting` typed once first. Rerun after every redeploy, it replaces its own bookmarks.

| UI | Port |
|---|---|
| Cockpit | 30300 |
| OpenMetadata | 30585 |
| Keycloak admin (realm `stackable-demo`) | 30900 |

## Choreography

All queries run in Cockpit, logged in as `demo-user` unless noted.

### Part A: hardcoded masking per group

1. Query the table with hardcoded masks:
   ```sql
   SELECT customer_id, customer_name, account_balance, lifetime_net_revenue
   FROM "hive-iceberg".demo.customer_lifetime_value
   ORDER BY lifetime_net_revenue DESC LIMIT 10;
   ```
   `customer_name` is `***MASKED***`, `account_balance` is `NULL`. In OpenMetadata the columns
   carry **no** tags: these masks come from the policy itself.
2. Keycloak → `stackable-demo` → Users → `demo-user` → Groups → Join group → `pii`.
3. Wait about a minute (the group lookup is cached), rerun the query: clear text.

### Part B: governance from the catalog

1. Query a table without an owner:
   ```sql
   SELECT customer_name, customer_nation, net_revenue
   FROM "hive-iceberg".demo.order_summary
   ORDER BY net_revenue DESC LIMIT 10;
   ```
   **Access Denied.** The table is still listed in the catalog browser; it just can't be read.
   Same for `demo-admin`.
2. OpenMetadata → Explore → `order_summary` → set owner `Data Engineering`.
3. Wait about 10 seconds, rerun: the query works, `customer_name` in clear text.
4. OpenMetadata → `order_summary` → column `customer_name` → add tag `PII.Sensitive`.
5. Wait about 10 seconds, rerun: `customer_name` is `***MASKED***`. No policy was changed.
6. Optional: tag `net_revenue` too, it comes back as `NULL`. As `demo-admin` (in `/pii`) both
   columns stay readable.

### Reset after a rehearsal

- Keycloak: remove `demo-user` from `/pii`.
- OpenMetadata, `order_summary`: remove the owner and the `PII.Sensitive` tags.

## If something is off

- **Everything in `hive-iceberg.demo` is denied, even owned tables:** the resource-info-fetcher
  can't reach OpenMetadata. Check that Secret `platform/resource-info-fetcher-credentials` holds a
  JWT (starts with `eyJ`, written by Job `configure-openmetadata-v4`), not `placeholder`.
- **The DAG hangs in `wait_for_services`:** check `trino-init`. It needs an active Trino worker.
- **OpenMetadata answers 431:** too many cookies for the node IP; clear them or use a private
  window. (The header limit is raised to 64 KiB, this should be rare.)
- **Avoid in Q&A:** `tpch.tiny.customer` is readable unmasked. Only `hive-iceberg.demo` is
  governed.
