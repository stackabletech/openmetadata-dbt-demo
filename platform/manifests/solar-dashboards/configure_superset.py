"""Solar demo dashboards in Superset, as code.

Creates (or updates) the Trino database connection, the datasets on data2day.solar_gold /
solar_silver, the charts and four dashboards, and the role that lets viewers open them. Idempotent: objects are matched by name and
updated in place, so edits made here win over edits made in the UI.

Runs as a Job (solar-dashboards Application); for development it also runs locally against a
port-forward: SUPERSET_URL=https://127.0.0.1:18088 SUPERSET_CA= python configure_superset.py
(an empty SUPERSET_CA skips certificate checks: the certificate doesn't cover 127.0.0.1).
"""

import http.cookiejar
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

SUPERSET = os.environ.get("SUPERSET_URL", "https://simple-superset-node.platform.svc.cluster.local:8088")
# CA of the secret-operator SecretClass `tls`, which signs Superset's certificate
SUPERSET_CA = os.environ.get("SUPERSET_CA", "/stackable/tls/ca.crt")
DATABASE = "Trino data2day"
# Superset connects as the Trino service user `superset` and impersonates the logged-in user
# (impersonate_user), so the Trino OPA policies decide per user what a chart may read.
TRINO_URI = ("trino://superset:" + urllib.parse.quote(os.environ["TRINO_PASSWORD"], safe="")
             + "@trino-coordinator.platform.svc.cluster.local:8443/data2day")
VIEWER_ROLE = "Solar Viewer"
BASE_MAP = "tile://https://tile.openstreetmap.org/{z}/{x}/{y}.png"
GERMANY = {"latitude": 51.2, "longitude": 10.4, "zoom": 5.2, "bearing": 0, "pitch": 0}
NORTH = {"latitude": 54.2, "longitude": 10.0, "zoom": 7.5, "bearing": 0, "pitch": 0}
# Tilted, so the Landkreis bars stand up
NORTH_3D = {"latitude": 53.85, "longitude": 9.85, "zoom": 7.2, "bearing": -10, "pitch": 50}

ATTRIBUTION = """
<small>
**Synthetic data.** Site capacities, orientations and regions come from the
Marktstammdatenregister (© Bundesnetzagentur für Elektrizität, Gas, Telekommunikation, Post und
Eisenbahnen | dl-de/by-2-0). All production values, weather, clouds and faults are simulated.
Positions are illustrative, not real locations: small sites are placed randomly within their
postcode area (postcode areas © OpenStreetMap contributors, ODbL-1.0). Map tiles ©
OpenStreetMap contributors.
</small>
"""

# --- Superset REST client ----------------------------------------------------------------

_jar = http.cookiejar.CookieJar()
_tls = ssl.create_default_context(cafile=SUPERSET_CA) if SUPERSET_CA else ssl._create_unverified_context()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar),
                                      urllib.request.HTTPSHandler(context=_tls))
_auth = {}


def call(method, path, body=None):
    headers = {"Content-Type": "application/json", "Referer": SUPERSET, **_auth}
    req = urllib.request.Request(SUPERSET + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with _opener.open(req, timeout=120) as r:
            text = r.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: HTTP {e.code} {e.read().decode()[:800]}") from None


def login():
    for i in range(60):
        try:
            token = call("POST", "/api/v1/security/login", {
                "username": os.environ["SUPERSET_USER"], "password": os.environ["SUPERSET_PASSWORD"],
                "provider": "db", "refresh": True})["access_token"]
            break
        except Exception as e:
            print(f"Waiting for Superset ({i + 1}/60): {e}")
            time.sleep(10)
    else:
        raise SystemExit("Superset not reachable")
    _auth["Authorization"] = f"Bearer {token}"
    _auth["X-CSRFToken"] = call("GET", "/api/v1/security/csrf_token/")["result"]


def find(resource, **match):
    """First object of /api/v1/<resource>/ whose fields equal match, or None."""
    filters = [{"col": k, "opr": "eq", "value": v} for k, v in match.items()]
    q = urllib.parse.quote(json.dumps({"filters": filters, "page_size": 100}))
    for obj in call("GET", f"/api/v1/{resource}/?q={q}")["result"]:
        if all(obj.get(k) == v for k, v in match.items()):
            return obj
    return None


# --- Helpers for chart parameters --------------------------------------------------------

# Rolling ranges up to now. Superset's "Last week" / "Last month" end at midnight and would
# hide today's data.
LAST_2_DAYS = 'DATEADD(DATETIME("now"), -2, day) : now'
LAST_7_DAYS = 'DATEADD(DATETIME("today"), -7, day) : now'
# For DATE columns Superset truncates "now" to today and excludes it; end tomorrow instead.
DAYS_7 = 'DATEADD(DATETIME("today"), -7, day) : DATEADD(DATETIME("today"), 1, day)'
DAYS_30 = 'DATEADD(DATETIME("today"), -30, day) : DATEADD(DATETIME("today"), 1, day)'

def metric(sql, label):
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label,
            "optionName": "metric_" + "".join(c if c.isalnum() else "_" for c in label.lower())}


def time_filter(column, time_range):
    return {"expressionType": "SIMPLE", "subject": column, "operator": "TEMPORAL_RANGE",
            "comparator": time_range, "clause": "WHERE"}


def sql_filter(sql):
    return {"expressionType": "SQL", "sqlExpression": sql, "clause": "WHERE"}


def color(r, g, b, a=1):
    return {"r": r, "g": g, "b": b, "a": a}


# --- Datasets ----------------------------------------------------------------------------

LATEST_SLOT = """(SELECT max(slot) FROM solar_gold.fleet_15min
                  WHERE slot < (SELECT processed_until FROM solar_gold.watermark
                                WHERE table_name = 'fleet_15min'))"""

DATASETS = {
    # physical tables: (schema, table)
    "fleet_15min": ("solar_gold", "fleet_15min", None),
    "region_daily": ("solar_gold", "region_daily", None),
    "site_daily": ("solar_gold", "site_daily", None),
    "fault_events": ("solar_gold", "fault_events", None),
    "dq_daily": ("solar_gold", "dq_daily", None),
    "cloud_cover": ("solar_gold", "cloud_cover", None),
    # virtual datasets: (schema, name, sql)
    "solar_fleet_map": ("solar_gold", "solar_fleet_map", f"""
        -- Latest complete 15-minute slot per postcode, placed at the centroid of its (illustrative) site positions
        SELECT f.plz, f.landkreis, f.bundesland, f.slot, f.sites, f.kwp_dc, f.power_kw,
               f.performance_ratio, f.underperforming_sites, c.lat, c.lon
        FROM solar_gold.fleet_15min f
        JOIN (SELECT plz, avg(lat) AS lat, avg(lon) AS lon FROM solar_silver.dim_site GROUP BY plz) c
          ON c.plz = f.plz
        WHERE f.slot = {LATEST_SLOT}"""),
    "solar_parks": ("solar_gold", "solar_parks", """
        -- Ground-mounted parks >= 1 MW (jittered real positions)
        SELECT site_id, gemeinde, landkreis, bundesland, kwp_dc / 1000 AS mwp, lat, lon
        FROM solar_silver.dim_site WHERE site_id LIKE 'park-%'"""),
    "solar_sh_landkreis_bars": ("solar_gold", "solar_sh_landkreis_bars", """
        -- Energy of the last 7 days per Landkreis in Schleswig-Holstein, as a square of about
        -- 5.5 km at the centroid of its (illustrative) site positions. rel_height: share of the
        -- largest Landkreis, so the tallest bar has the same height whatever the totals are.
        WITH centre AS (
            SELECT landkreis, avg(lat) AS lat, avg(lon) AS lon, sum(kwp_dc) / 1000 AS mwp
            FROM solar_silver.dim_site WHERE bundesland = 'Schleswig-Holstein' GROUP BY landkreis),
        energy AS (
            SELECT landkreis, sum(energy_kwh) / 1000 AS mwh
            FROM solar_gold.region_daily
            WHERE bundesland = 'Schleswig-Holstein' AND day > current_date - INTERVAL '7' DAY
            GROUP BY landkreis)
        SELECT e.landkreis AS name, e.mwh AS energy_mwh, c.mwp AS capacity_mwp,
               e.mwh / max(e.mwh) OVER () AS rel_height,
               format('[[%.4f,%.4f],[%.4f,%.4f],[%.4f,%.4f],[%.4f,%.4f],[%.4f,%.4f]]',
                      c.lon - 0.042, c.lat - 0.025, c.lon + 0.042, c.lat - 0.025,
                      c.lon + 0.042, c.lat + 0.025, c.lon - 0.042, c.lat + 0.025,
                      c.lon - 0.042, c.lat - 0.025) AS shape
        FROM energy e JOIN centre c ON c.landkreis = e.landkreis"""),
    "solar_fault_map": ("solar_gold", "solar_fault_map", """
        -- Fault events of the last 7 days at the (illustrative) site positions
        SELECT e.site_id, e.start_slot, e.end_slot, e.duration_minutes, e.lost_energy_kwh,
               e.min_peer_ratio, e.inverter_fault_reported, e.landkreis, s.lat, s.lon
        FROM solar_gold.fault_events e
        JOIN solar_silver.dim_site s ON s.site_id = e.site_id
        WHERE e.start_slot >= current_timestamp - INTERVAL '7' DAY"""),
}


def ensure_database():
    extra = json.dumps({"engine_params": {"connect_args": {"http_scheme": "https", "verify": False}},
                        "allows_virtual_table_explore": True})
    body = {"database_name": DATABASE, "sqlalchemy_uri": TRINO_URI, "extra": extra,
            "expose_in_sqllab": True, "allow_run_async": False, "impersonate_user": True}
    db = find("database", database_name=DATABASE)
    if db:
        call("PUT", f"/api/v1/database/{db['id']}", body)
        return db["id"]
    return call("POST", "/api/v1/database/", body)["id"]


def ensure_viewer_role(db_id):
    """Role VIEWER_ROLE with access to the database; which tables a user may read is up to Trino.

    The builtin Gamma role can't carry it: `superset init` resets builtin roles on every start.
    superset.yaml maps the Keycloak role `viewer` to Gamma plus this role.
    """
    roles = call("GET", "/api/v1/security/roles/search/?q=" + urllib.parse.quote(json.dumps({"page_size": 100})))
    role = next((r for r in roles["result"] if r["name"] == VIEWER_ROLE), None)
    role_id = role["id"] if role else call("POST", "/api/v1/security/roles/", {"name": VIEWER_ROLE})["id"]
    view_menu = f"[{DATABASE}].(id:{db_id})"
    pvm_ids, page = [], 0
    while True:
        q = urllib.parse.quote(json.dumps({"page": page, "page_size": 100}))
        result = call("GET", f"/api/v1/security/permissions-resources/?q={q}")["result"]
        pvm_ids += [p["id"] for p in result
                    if p["permission"]["name"] == "database_access" and p["view_menu"]["name"] == view_menu]
        if len(result) < 100:
            break
        page += 1
    if not pvm_ids:
        raise RuntimeError(f"No permission database_access on {view_menu}")
    call("POST", f"/api/v1/security/roles/{role_id}/permissions", {"permission_view_menu_ids": pvm_ids})
    print(f"Role {VIEWER_ROLE}: database_access on {view_menu}")


def ensure_datasets(db_id):
    ids = {}
    for key, (schema, table, sql) in DATASETS.items():
        ds = find("dataset", table_name=table, schema=schema)
        if ds is None:
            body = {"database": db_id, "schema": schema, "table_name": table}
            if sql:
                body["sql"] = sql
            ds_id = call("POST", "/api/v1/dataset/", body)["id"]
        else:
            ds_id = ds["id"]
            if sql:
                call("PUT", f"/api/v1/dataset/{ds_id}?override_columns=true", {"sql": sql})
        # Pick up new or changed columns.
        call("PUT", f"/api/v1/dataset/{ds_id}/refresh")
        ids[key] = ds_id
        print(f"Dataset {schema}.{table}: {ds_id}")
    return ids


# --- Charts ------------------------------------------------------------------------------

def charts(ds):
    """name -> (viz_type, dataset key, params without viz_type/datasource)."""
    mw = metric("SUM(power_kw) / 1000", "Output (MW)")
    mwh = metric("SUM(energy_kwh) / 1000", "Energy (MWh)")
    last_2_days = time_filter("slot", LAST_2_DAYS)
    return {
        # Overview
        "Current output (MW)": ("big_number_total", "solar_fleet_map", {
            "metric": mw, "adhoc_filters": [], "y_axis_format": ",.1f",
            "subheader": "latest complete 15-minute slot"}),
        "Energy today (MWh)": ("big_number_total", "region_daily", {
            "metric": mwh, "adhoc_filters": [sql_filter("day = current_date")], "y_axis_format": ",.1f",
            "subheader": "all reporting sites, UTC day"}),
        "Reporting sites": ("big_number_total", "solar_fleet_map", {
            "metric": metric("SUM(sites)", "Sites"), "adhoc_filters": [], "y_axis_format": ",d",
            "subheader": "sites with readings in the latest slot"}),
        "Installed capacity (MWp)": ("big_number_total", "solar_fleet_map", {
            "metric": metric("SUM(kwp_dc) / 1000", "MWp"), "adhoc_filters": [], "y_axis_format": ",.0f",
            "subheader": "of the reporting sites"}),
        "Fleet output (MW)": ("echarts_timeseries_line", "fleet_15min", {
            "x_axis": "slot", "time_grain_sqla": "PT15M", "metrics": [mw], "groupby": [],
            "adhoc_filters": [last_2_days], "row_limit": 10000, "show_legend": False,
            "rich_tooltip": True, "y_axis_format": ",.1f", "x_axis_time_format": "smart_date",
            "area": True, "opacity": 0.3, "markerEnabled": False}),
        "Energy per day by Bundesland (MWh)": ("echarts_timeseries_bar", "region_daily", {
            "x_axis": "day", "time_grain_sqla": "P1D", "metrics": [mwh], "groupby": ["bundesland"],
            "adhoc_filters": [time_filter("day", DAYS_30)], "row_limit": 10000,
            "stack": "Stack", "show_legend": True, "y_axis_format": ",.0f",
            "x_axis_time_format": "%Y-%m-%d"}),
        "Map: output per postcode": ("deck_scatter", "solar_fleet_map", {
            "spatial": {"type": "latlong", "latCol": "lat", "lonCol": "lon"},
            "point_radius_fixed": {"type": "metric", "value": metric("SUM(power_kw)", "kW")},
            "point_unit": "square_m", "min_radius": 2, "max_radius": 40, "multiplier": 1,
            "color_picker": color(0, 122, 204, 0.8), "mapbox_style": BASE_MAP,
            "viewport": GERMANY, "autozoom": False, "row_limit": 20000, "adhoc_filters": []}),
        "Map: parks": ("deck_scatter", "solar_parks", {
            "spatial": {"type": "latlong", "latCol": "lat", "lonCol": "lon"},
            "point_radius_fixed": {"type": "fix", "value": 1500}, "point_unit": "square_m",
            "min_radius": 1, "max_radius": 12, "multiplier": 1,
            "color_picker": color(240, 140, 0, 0.8), "mapbox_style": BASE_MAP,
            "viewport": GERMANY, "autozoom": False, "row_limit": 20000, "adhoc_filters": []}),
        "Map: simulated cloud cover": ("deck_geojson", "cloud_cover", {
            "geojson": "geojson", "filled": True, "stroked": True, "extruded": False,
            "fill_color_picker": color(110, 110, 120, 0.35), "stroke_color_picker": color(80, 80, 90, 0.6),
            "line_width": 1, "line_width_unit": "pixels", "mapbox_style": BASE_MAP,
            "viewport": GERMANY, "autozoom": False, "row_limit": 1000, "adhoc_filters": []}),
        # Regional yield
        "Yield per Landkreis": ("table", "region_daily", {
            "query_mode": "aggregate", "groupby": ["day", "bundesland", "landkreis"],
            "metrics": [metric("SUM(energy_kwh) / 1000", "Energy (MWh)"),
                        metric("SUM(kwp_dc) / 1000", "Capacity (MWp)"),
                        metric("SUM(energy_kwh) / SUM(kwp_dc)", "Specific yield (kWh/kWp)"),
                        metric("SUM(energy_kwh) / (SUM(kwp_dc) * 24)", "Capacity factor"),
                        metric("SUM(sites)", "Sites")],
            "adhoc_filters": [time_filter("day", DAYS_7)], "row_limit": 1000,
            "order_desc": True, "table_timestamp_format": "%Y-%m-%d", "include_search": True}),
        # 3D bars: deck.gl polygons, extruded by rel_height (multiplier = height of the tallest bar
        # in metres), coloured by energy. js_columns ("Extra data for JS") adds the column `name`
        # to every bar, which the tooltip shows; Superset returns metrics only as numbers. The
        # shape column must not be called `polygon`: Superset stores the parsed shape under that
        # key, then deletes the column.
        "Map: energy per Landkreis, Schleswig-Holstein (last 7 days)": ("deck_polygon", "solar_sh_landkreis_bars", {
            "line_column": "shape", "line_type": "json", "js_columns": ["name"],
            "metric": metric("ROUND(SUM(energy_mwh), 2)", "Energy (MWh)"),
            "point_radius_fixed": {"type": "metric", "value": metric("SUM(rel_height)", "Relative height")},
            "multiplier": 40000, "extruded": True, "filled": True, "stroked": False,
            "color_scheme_type": "linear_palette", "linear_color_scheme": ["#fde9b5", "#f08c00", "#a33a00"],
            "opacity": 90, "fill_color_picker": color(240, 140, 0, 1), "stroke_color_picker": color(0, 0, 0, 0),
            "line_width": 1, "line_width_unit": "pixels", "mapbox_style": BASE_MAP,
            "viewport": NORTH_3D, "autozoom": False, "row_limit": 100, "adhoc_filters": []}),
        "Specific yield by Bundesland (kWh/kWp)": ("echarts_timeseries_line", "region_daily", {
            "x_axis": "day", "time_grain_sqla": "P1D",
            "metrics": [metric("SUM(energy_kwh) / SUM(kwp_dc)", "kWh/kWp")], "groupby": ["bundesland"],
            "adhoc_filters": [time_filter("day", DAYS_30)], "row_limit": 10000,
            "show_legend": True, "y_axis_format": ",.2f", "x_axis_time_format": "%Y-%m-%d",
            "markerEnabled": True}),
        "Top sites today by specific yield": ("table", "site_daily", {
            "query_mode": "raw",
            "all_columns": ["site_id", "landkreis", "plz", "kwp_dc", "energy_kwh",
                            "specific_yield_kwh_kwp", "performance_ratio"],
            "order_by_cols": [json.dumps(["specific_yield_kwh_kwp", False])],
            "adhoc_filters": [sql_filter("day = current_date AND kwp_dc >= 3")], "row_limit": 100,
            "include_search": True}),
        # Fault detection
        "Fault events today": ("big_number_total", "fault_events", {
            "metric": metric("COUNT(*)", "Events"), "y_axis_format": ",d",
            "adhoc_filters": [sql_filter("CAST(start_slot AS date) = current_date")],
            "subheader": "sites below 75 % of their peers"}),
        "Lost energy today (kWh)": ("big_number_total", "fault_events", {
            "metric": metric("SUM(lost_energy_kwh)", "kWh"), "y_axis_format": ",.0f",
            "adhoc_filters": [sql_filter("CAST(start_slot AS date) = current_date")],
            "subheader": "versus peers in the same weather cell"}),
        "Underperforming sites": ("echarts_timeseries_line", "fleet_15min", {
            "x_axis": "slot", "time_grain_sqla": "PT15M",
            "metrics": [metric("SUM(underperforming_sites)", "Sites")], "groupby": [],
            "adhoc_filters": [last_2_days], "row_limit": 10000, "show_legend": False,
            "y_axis_format": ",d", "x_axis_time_format": "smart_date", "markerEnabled": False}),
        "Fault events": ("table", "fault_events", {
            "query_mode": "raw",
            "all_columns": ["site_id", "landkreis", "plz", "start_slot", "end_slot", "duration_minutes",
                            "lost_energy_kwh", "min_peer_ratio", "inverter_fault_reported"],
            "order_by_cols": [json.dumps(["lost_energy_kwh", False])],
            "adhoc_filters": [time_filter("start_slot", LAST_7_DAYS)], "row_limit": 500,
            "include_search": True}),
        "Faults per Landkreis": ("echarts_timeseries_bar", "fault_events", {
            "x_axis": "landkreis", "metrics": [metric("COUNT(*)", "Events")], "groupby": [],
            "adhoc_filters": [time_filter("start_slot", LAST_7_DAYS)], "row_limit": 50,
            "orientation": "horizontal", "show_legend": False, "y_axis_format": ",d",
            "x_axis_sort": "Events", "x_axis_sort_asc": False}),
        "Map: fault events (last 7 days)": ("deck_scatter", "solar_fault_map", {
            "spatial": {"type": "latlong", "latCol": "lat", "lonCol": "lon"},
            "point_radius_fixed": {"type": "metric", "value": metric("SUM(lost_energy_kwh)", "Lost kWh")},
            "point_unit": "square_m", "min_radius": 3, "max_radius": 30, "multiplier": 1,
            "color_picker": color(214, 39, 40, 0.85), "mapbox_style": BASE_MAP,
            "viewport": NORTH, "autozoom": True, "row_limit": 5000, "adhoc_filters": []}),
        # Data quality
        "Quarantined records per day": ("echarts_timeseries_bar", "dq_daily", {
            "x_axis": "day", "time_grain_sqla": "P1D", "metrics": [metric("SUM(records)", "Records")],
            "groupby": ["reason"], "adhoc_filters": [time_filter("day", DAYS_30)],
            "row_limit": 10000, "stack": "Stack", "show_legend": True, "y_axis_format": ",d",
            "x_axis_time_format": "%Y-%m-%d"}),
        "Quarantine reasons (last 7 days)": ("pie", "dq_daily", {
            "groupby": ["reason"], "metric": metric("SUM(records)", "Records"),
            "adhoc_filters": [time_filter("day", DAYS_7)], "row_limit": 100,
            "show_labels": True, "label_type": "key_percent", "donut": True, "show_legend": True}),
        "Quarantined today": ("big_number_total", "dq_daily", {
            "metric": metric("SUM(records)", "Records"), "y_axis_format": ",d",
            "adhoc_filters": [sql_filter("day = current_date")], "subheader": "rejected by the silver checks"}),
        "Quarantine by stream and reason": ("table", "dq_daily", {
            "query_mode": "aggregate", "groupby": ["day", "stream", "reason"],
            "metrics": [metric("SUM(records)", "Records")],
            "adhoc_filters": [time_filter("day", DAYS_7)], "row_limit": 1000,
            "table_timestamp_format": "%Y-%m-%d"}),
    }


def ensure_charts(ds_ids, dash_ids_by_chart):
    ids = {}
    defs = charts(ds_ids)
    for name, (viz, ds_key, params) in defs.items():
        ds_id = ds_ids[ds_key]
        params = {**params, "viz_type": viz, "datasource": f"{ds_id}__table"}
        body = {"slice_name": name, "viz_type": viz, "datasource_id": ds_id, "datasource_type": "table",
                "params": json.dumps(params), "dashboards": dash_ids_by_chart.get(name, [])}
        existing = find("chart", slice_name=name)
        if existing:
            call("PUT", f"/api/v1/chart/{existing['id']}", body)
            ids[name] = existing["id"]
        else:
            ids[name] = call("POST", "/api/v1/chart/", body)["id"]
    # Multi-layer map: built from the layer charts above.
    layers = [ids["Map: simulated cloud cover"], ids["Map: output per postcode"], ids["Map: parks"]]
    params = {"viz_type": "deck_multi", "datasource": f"{ds_ids['solar_fleet_map']}__table",
              "deck_slices": layers, "mapbox_style": BASE_MAP, "viewport": GERMANY, "autozoom": False,
              "adhoc_filters": []}
    name = "Map: fleet, parks and clouds"
    body = {"slice_name": name, "viz_type": "deck_multi", "datasource_id": ds_ids["solar_fleet_map"],
            "datasource_type": "table", "params": json.dumps(params),
            "dashboards": dash_ids_by_chart.get(name, [])}
    existing = find("chart", slice_name=name)
    ids[name] = (call("PUT", f"/api/v1/chart/{existing['id']}", body) and existing["id"]) if existing \
        else call("POST", "/api/v1/chart/", body)["id"]
    return ids


# --- Dashboards --------------------------------------------------------------------------

def md(text, width=12, height=20):
    return ("markdown", text, width, height)


def ch(name, width, height=50):
    return ("chart", name, width, height)


DASHBOARDS = {
    "solar-overview": ("Solar fleet: overview", 300, [
        [md("## Solar fleet\nSynthetic PV telemetry from ~68,000 sites in Schleswig-Holstein (postcodes 24xxx) "
            "and all parks ≥ 1 MW nationwide, refined through bronze, silver and gold on the "
            "Stackable Data Platform (SDP). Refreshes every 5 minutes; gold lags the generator by "
            "10–20 minutes.", 12, 14)],
        [ch("Current output (MW)", 3, 30), ch("Energy today (MWh)", 3, 30),
         ch("Reporting sites", 3, 30), ch("Installed capacity (MWp)", 3, 30)],
        [ch("Map: fleet, parks and clouds", 7, 110), ch("Fleet output (MW)", 5, 55)],
        [ch("Energy per day by Bundesland (MWh)", 12, 50)],
        [md(ATTRIBUTION, 12, 18)],
    ]),
    "solar-yield": ("Solar fleet: regional yield", 0, [
        [md("## Regional yield\nEnergy, specific yield (kWh per kWp) and capacity factor per "
            "Landkreis and Bundesland, from `solar_gold.region_daily` and `site_daily`.", 12, 12)],
        [ch("Map: energy per Landkreis, Schleswig-Holstein (last 7 days)", 8, 110),
         md("### Schleswig-Holstein in 3D\nOne bar per Landkreis, height and colour by its energy "
            "of the last 7 days; the tallest bar is always the same height. Bars stand at the centre "
            "of the Landkreis's sites. Rooftop sites are simulated only for postcodes 24xxx, so "
            "Landkreise in the south (and Lübeck) have only their parks ≥ 1 MW. Hover over a bar for "
            "its name and energy; drag with the right mouse button to rotate.", 4, 110)],
        [ch("Specific yield by Bundesland (kWh/kWp)", 6, 55), ch("Energy per day by Bundesland (MWh)", 6, 55)],
        [ch("Yield per Landkreis", 12, 70)],
        [ch("Top sites today by specific yield", 12, 70)],
        [md(ATTRIBUTION, 12, 18)],
    ]),
    "solar-faults": ("Solar fleet: fault detection", 300, [
        [md("## Fault detection\nA site is *underperforming* in a 15-minute slot when its performance "
            "ratio is below 75 % of the median of its peers: at least 5 sites in the same weather cell "
            "(~28 × 26 km) with the same orientation, while the peers produce. Consecutive slots form a "
            "fault event; lost energy is what the site would have produced at its peers' ratio. "
            "Faults are injected by the generator (string outages, soiling, inverter trips).", 12, 18)],
        [ch("Fault events today", 3, 30), ch("Lost energy today (kWh)", 3, 30),
         ch("Underperforming sites", 6, 30)],
        [ch("Map: fault events (last 7 days)", 6, 80), ch("Faults per Landkreis", 6, 80)],
        [ch("Fault events", 12, 70)],
        [md(ATTRIBUTION, 12, 18)],
    ]),
    "solar-data-quality": ("Solar pipeline: data quality", 0, [
        [md("## Data quality\nRecords rejected by the silver checks (`solar_silver.quarantine`), by "
            "reason. The generator injects these defects on purpose: duplicates, late readings, nulls "
            "in required fields, negative power, unknown sites, output at night, counter resets.", 12, 14)],
        [ch("Quarantined today", 3, 50), ch("Quarantine reasons (last 7 days)", 9, 50)],
        [ch("Quarantined records per day", 12, 55)],
        [ch("Quarantine by stream and reason", 12, 60)],
    ]),
}


def position(title, rows, chart_ids, chart_uuids):
    pos = {"DASHBOARD_VERSION_KEY": "v2",
           "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
           "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
           "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": title}}}
    for r, row in enumerate(rows):
        row_id = f"ROW-{r}"
        pos["GRID_ID"]["children"].append(row_id)
        pos[row_id] = {"type": "ROW", "id": row_id, "children": [], "parents": ["ROOT_ID", "GRID_ID"],
                       "meta": {"background": "BACKGROUND_TRANSPARENT"}}
        for c, (kind, ref, width, height) in enumerate(row):
            item_id = f"{kind.upper()}-{r}-{c}"
            pos[row_id]["children"].append(item_id)
            parents = ["ROOT_ID", "GRID_ID", row_id]
            if kind == "chart":
                meta = {"chartId": chart_ids[ref], "width": width, "height": height, "sliceName": ref}
                if chart_uuids.get(ref):
                    meta["uuid"] = chart_uuids[ref]
                pos[item_id] = {"type": "CHART", "id": item_id, "children": [], "parents": parents, "meta": meta}
            else:
                pos[item_id] = {"type": "MARKDOWN", "id": item_id, "children": [], "parents": parents,
                                "meta": {"width": width, "height": height, "code": ref}}
    return pos


def main():
    login()
    db_id = ensure_database()
    print(f"Database {DATABASE}: {db_id}")
    ensure_viewer_role(db_id)
    ds_ids = ensure_datasets(db_id)

    # Dashboards first (charts reference them), then charts, then the layout.
    dash_ids = {}
    for slug, (title, refresh, _) in DASHBOARDS.items():
        body = {"dashboard_title": title, "slug": slug, "published": True}
        existing = find("dashboard", slug=slug)
        dash_ids[slug] = existing["id"] if existing else call("POST", "/api/v1/dashboard/", body)["id"]
    dash_ids_by_chart = {}
    for slug, (_, _, rows) in DASHBOARDS.items():
        for row in rows:
            for kind, ref, *_ in row:
                if kind == "chart":
                    dash_ids_by_chart.setdefault(ref, []).append(dash_ids[slug])
    dash_ids_by_chart["Map: output per postcode"] = dash_ids_by_chart.get("Map: output per postcode", [])

    chart_ids = ensure_charts(ds_ids, dash_ids_by_chart)
    chart_uuids = {name: call("GET", f"/api/v1/chart/{cid}")["result"].get("uuid") for name, cid in chart_ids.items()}

    for slug, (title, refresh, rows) in DASHBOARDS.items():
        meta = {"refresh_frequency": refresh, "timed_refresh_immune_slices": [], "color_scheme": "supersetColors",
                "native_filter_configuration": [], "cross_filters_enabled": True}
        call("PUT", f"/api/v1/dashboard/{dash_ids[slug]}", {
            "dashboard_title": title, "slug": slug, "published": True,
            "position_json": json.dumps(position(title, rows, chart_ids, chart_uuids)),
            "json_metadata": json.dumps(meta)})
        print(f"Dashboard {title}: /superset/dashboard/{slug}/")


if __name__ == "__main__":
    main()
