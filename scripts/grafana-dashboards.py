#!/usr/bin/env python3
"""Generates the Grafana governance dashboards in platform/manifests/prometheus/ (ConfigMaps for the
Grafana dashboard sidecar). The YAML files are committed; run this after changing it and commit both.

  - grafana-dashboards-trino-queries.yaml  "Trino queries" (list) and "Trino query detail"
  - grafana-dashboards-nifi.yaml           "NiFi authorization" (panel shapes from the Airflow dashboard)
  - grafana-dashboards-overview.yaml       "Stackable Data Platform", the home dashboard
  - grafana-dashboards-kafka.yaml          "Kafka topic" and "Kafka broker", plus the topic and broker
                                           lists on the vendored "Kafka" dashboard
  - the common header (Stackable logo + navigation) on these and on the Airflow, OPA and logs
    dashboards, which are otherwise edited by hand

Each step reads the current files and assigns every value it owns, so a run on an unchanged tree
changes nothing. Adding a product: a tile in PRODUCTS, an entry in NAV, its dashboard, and a
drill-down branch in the vector-aggregator (transform opa_decisions).

Usage: just grafana-dashboards     (or: uv run --with pyyaml scripts/grafana-dashboards.py)
"""
import copy
import json
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M = os.path.join(ROOT, "platform", "manifests", "prometheus") + "/"
DSQ = {"type": "grafana-opensearch-datasource", "uid": "opensearch-trino-queries"}
DSD = {"type": "grafana-opensearch-datasource", "uid": "opensearch-opa-decisions"}


def block(name, obj):
    """One data entry of a ConfigMap: the dashboard JSON as a literal block."""
    s = json.dumps(obj, indent=2, ensure_ascii=True)
    return f"  {name}: |\n" + "\n".join("    " + l for l in s.splitlines()) + "\n"


def trino_queries():
    """Trino queries (list) and Trino query detail."""
    SRC = M + "grafana-dashboards-trino-queries.yaml"
    # Panel templates: the current dashboards (every field this function sets is assigned, not appended)
    cur = yaml.safe_load(open(SRC))
    old = json.loads(cur["data"]["trino-queries.json"])
    dt = json.loads(cur["data"]["trino-query-detail.json"])
    OP = {p["id"]: p for p in old["panels"]}
    DP = {p["id"]: p for p in dt["panels"]}
    P = {1: OP[1], 2: DP[8], 3: DP[9], 4: DP[11], 5: DP[10]}
    tvars = {v["name"]: v for v in old["templating"]["list"]}
    user_var, state_var = tvars["user"], tvars["state"]
    qid_var = dt["templating"]["list"][0]

    def by(name, *props):
        return {"matcher": {"id": "byName", "options": name}, "properties": list(props)}

    ACCESS_COLORS = {"type": "value", "options": {
        "allowed": {"color": "green", "index": 0},
        "masked": {"color": "orange", "index": 1},
        "row filter": {"color": "orange", "index": 2},
        "masked + row filter": {"color": "orange", "index": 3},
        "denied": {"color": "red", "index": 4}}}
    DETAIL_URL = ("/d/trino-query-detail/trino-query-detail?var-query_id=${__data.fields.query_id}"
                  "&from=${__data.fields.window_from_ms}&to=${__data.fields.window_to_ms}")

    # ---------------------------------------------------------------- overview
    access_var = {
        "name": "access", "label": "Access", "type": "custom",
        "query": "allowed,masked,row filter,denied", "multi": True, "includeAll": True, "allValue": "*",
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
        "description": "What OPA did to the query: allowed, masked (column masks), row filter, denied"}
    q = copy.deepcopy(P[1])
    q.update({
        "title": "Queries",
        "description": "Completed Trino queries, newest first. Click a row for the query's details, OPA decisions and logs; 'Trino UI' opens the query in the Trino web UI. Access is what OPA did to the query. Technical users (admin, cockpit) are left out.",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 26}})
    q["targets"][0]["query"] = ("user.keyword:${user:lucene} AND state.keyword:${state:lucene} AND access:${access:lucene}"
                                " AND NOT user.keyword:(admin OR cockpit)")
    q["targets"][0]["metrics"][0]["settings"]["size"] = "500"
    cols = ["timestamp", "user", "access", "tables", "masked_columns", "sql", "state", "error_code",
            "wall_s", "output_rows", "trino_ui_url", "query_id", "window_from_ms", "window_to_ms"]
    names = {"timestamp": "Time", "user": "User", "access": "Access", "state": "State", "error_code": "Error",
             "tables": "Tables", "masked_columns": "Masked columns", "row_filters": "Row filters",
             "wall_s": "Wall time", "output_rows": "Rows", "sql": "SQL", "query_id": "Query ID", "trino_ui_url": "Trino UI"}
    q["transformations"] = [{"id": "convertFieldType", "options": {"conversions": [
        {"targetField": f, "destinationType": "string"} for f in ("tables", "masked_columns")]}},
        {"id": "organize", "options": {
        "includeByName": {c: True for c in cols}, "indexByName": {c: i for i, c in enumerate(cols)},
        "renameByName": names}}]
    fc = q["fieldConfig"]
    fc["defaults"]["links"] = [{"title": "Query details, OPA decisions and logs", "url": DETAIL_URL, "targetBlank": False}]
    fc["overrides"] = [
        by("Trino UI", {"id": "links", "value": [{"title": "Open in the Trino web UI", "url": "${__value.raw}", "targetBlank": True}]}),
        by("Trino UI", {"id": "mappings", "value": [{"type": "regex", "options": {"pattern": "^https.*", "result": {"text": "open \u2197"}}}]}),
        by("Wall time", {"id": "unit", "value": "s"}),
        by("Time", {"id": "unit", "value": "dateTimeAsIso"}),
        by("window_from_ms", {"id": "custom.hidden", "value": True}),
        by("window_to_ms", {"id": "custom.hidden", "value": True}),
    ]
    # Empty lists ("[]") as blank cells
    fc["defaults"]["mappings"] = [{"type": "regex", "options": {"pattern": "^\\[\\s*\\]$", "result": {"text": " "}}}]
    fc["overrides"] += [
        by("Access", {"id": "mappings", "value": [ACCESS_COLORS]},
           {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
           {"id": "custom.width", "value": 150}),
        by("User", {"id": "custom.width", "value": 130}),
        by("State", {"id": "custom.width", "value": 100}),
        by("Time", {"id": "custom.width", "value": 190}),
        by("Wall time", {"id": "custom.width", "value": 90}),
        by("Rows", {"id": "custom.width", "value": 70}),
        by("Trino UI", {"id": "custom.width", "value": 80}),
        by("Error", {"id": "custom.width", "value": 170}),
        by("Tables", {"id": "custom.width", "value": 260}),
        by("Masked columns", {"id": "custom.width", "value": 300}),
        # Kept for the link to the detail page, not shown
        by("Query ID", {"id": "custom.hidden", "value": True}),
    ]
    overview = {k: old[k] for k in ("schemaVersion", "time", "refresh", "editable")}
    overview.update({
        "uid": "trino-queries", "title": "Trino queries", "tags": ["trino", "opa", "governance"],
        "description": "Completed Trino queries (HTTP event listener -> logs-trino-queries-*). Click a query for its details on 'Trino query detail'.",
        "templating": {"list": [user_var, access_var, state_var]},
        "links": [{"title": "Trino query detail", "type": "link", "url": "/d/trino-query-detail/trino-query-detail", "keepTime": True, "icon": "dashboard"}],
        "panels": [q]})

    # ---------------------------------------------------------------- detail
    def stat(pid, title, field, x, w, extra_fc=None, links=None):
        p = {"id": pid, "type": "stat", "title": title, "datasource": DSQ,
             "gridPos": {"x": x, "y": 0, "w": w, "h": 4},
             "targets": [{"refId": "A", "datasource": DSQ, "queryType": "lucene",
                          "query": "query_id.keyword:\"${query_id}\"", "timeField": "timestamp",
                          "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "1"}}], "bucketAggs": []}],
             "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": f"/^{field}$/", "values": False},
                         "colorMode": "background" if field == "access" else "none", "graphMode": "none",
                         "textMode": "value", "justifyMode": "center"},
             "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "text"}}, "overrides": []}}
        if extra_fc: p["fieldConfig"]["defaults"].update(extra_fc)
        if links: p["fieldConfig"]["defaults"]["links"] = links
        return p

    panels = [
        stat(1, "User", "user", 0, 4),
        stat(2, "Access", "access", 4, 4, {"mappings": [ACCESS_COLORS], "color": {"mode": "thresholds"},
                                           "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]}}),
        stat(3, "State", "state", 8, 4),
        stat(4, "Error", "error_code", 12, 4, {"noValue": "-"}),
        stat(5, "Wall time", "wall_s", 16, 4, {"unit": "s", "decimals": 2}),
        stat(6, "Trino web UI", "trino_ui_url", 20, 4,
             {"mappings": [{"type": "regex", "options": {"pattern": "^https.*", "result": {"text": "open ↗"}}}]},
             [{"title": "Open in the Trino web UI", "url": "${__value.raw}", "targetBlank": True}]),
    ]
    sqlp = copy.deepcopy(P[2])
    sqlp.update({"id": 7, "title": "SQL", "description": "The query text.",
                 "gridPos": {"x": 0, "y": 4, "w": 24, "h": 5}})
    sqlp["transformations"] = [{"id": "organize", "options": {"includeByName": {"sql": True}, "renameByName": {}}}]
    sqlp["fieldConfig"] = {"defaults": {"custom": {"cellOptions": {"type": "auto", "wrapText": True}, "inspect": True}}, "overrides": []}
    sqlp["options"] = {"showHeader": False, "cellHeight": "lg"}
    details = copy.deepcopy(P[2]); details.update({"id": 8, "gridPos": {"x": 0, "y": 9, "w": 9, "h": 16},
        "description": "All recorded fields of the query."})
    decisions = copy.deepcopy(P[3]); decisions.update({"id": 9, "gridPos": {"x": 9, "y": 9, "w": 15, "h": 16}})
    trinolog = copy.deepcopy(P[5]); trinolog.update({"id": 10, "gridPos": {"x": 0, "y": 25, "w": 12, "h": 12}})
    opalog = copy.deepcopy(P[4]); opalog.update({"id": 11, "gridPos": {"x": 12, "y": 25, "w": 12, "h": 12},
        "description": "Log lines of the OPA servers (decision log lines excluded) in the query's runtime ± 2 s (the time range set by the link from 'Trino queries')."})
    panels += [sqlp, details, decisions, trinolog, opalog]
    qid = copy.deepcopy(qid_var); qid["description"] = "Set by the link from 'Trino queries'"
    detail = {k: old[k] for k in ("schemaVersion", "editable")}
    detail.update({
        "uid": "trino-query-detail", "title": "Trino query detail", "tags": ["trino", "opa", "governance"],
        "description": "One Trino query: details, the OPA decisions it caused (with Keycloak groups and OpenMetadata data), and the Trino and OPA log. Opened from 'Trino queries'.",
        "time": {"from": "now-6h", "to": "now"}, "refresh": "",
        "templating": {"list": [qid]},
        "links": [{"title": "All queries", "type": "link", "url": "/d/trino-queries/trino-queries", "icon": "arrow-left", "keepTime": False}],
        "panels": panels})

    header = """---
# Grafana dashboards for Trino queries (logs-trino-queries-*, from Trino's HTTP event listener via the
# vector-aggregator):
#   - "Trino queries": the list of completed queries with user, tables and what OPA did to them
#     (access: allowed / masked / row filter / denied); clicking a row opens
#   - "Trino query detail": one query (var-query_id + its time window from the link) with all fields,
#     its OPA decisions (logs-opa-decisions-*), the Trino and OPA log, and a link to the Trino web UI
#     (NodePort 30443).
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards-trino-queries
  namespace: platform
  labels:
    grafana_dashboard: '1'
  annotations:
    grafana_folder: Governance
data:
"""
    open(SRC, "w").write(header + block("trino-queries.json", overview) + block("trino-query-detail.json", detail))
    print("Trino queries / Trino query detail")


def nifi_authorization():
    """NiFi authorization, built from the panel shapes of Airflow authorization."""
    a = yaml.safe_load(open(M + "grafana-dashboards-airflow.yaml"))
    af = json.loads(a["data"]["airflow-authorization.json"])
    P = {p["id"]: p for p in af["panels"]}
    NODE = 'NOT user.keyword:"CN=generated certificate for pod"'
    BASE = f"system.keyword:nifi AND user.keyword:${{user:lucene}} AND {NODE}"

    def q(panel, query):
        for t in panel["targets"]: t["query"] = query
        return panel

    def terms(field, size="20"):
        return {"type": "terms", "field": field, "settings": {"size": size, "order": "desc", "orderBy": "_count", "min_doc_count": "1"}}

    def table(pid, title, desc, query, fields, renames, grid, extra_max=True):
        p = copy.deepcopy(P[2])
        p.update({"id": pid, "title": title, "description": desc, "gridPos": grid})
        t = p["targets"][0]
        t["query"] = query
        t["bucketAggs"] = [dict(terms(f), id=str(10 + i)) for i, f in enumerate(fields)]
        t["metrics"] = [m for m in t["metrics"] if extra_max or m["type"] == "count"]
        # keep the source panel's transformations/overrides only where they still fit: rename by field name
        order = {f: i for i, f in enumerate(fields)}
        order.update({"Count": len(fields), "Max": len(fields) + 1})
        p["transformations"] = [{"id": "organize", "options": {"indexByName": order, "renameByName": renames}}]
        p["fieldConfig"] = {"defaults": {"custom": {"inspect": True}}, "overrides": [
            {"matcher": {"id": "byName", "options": "Allowed"}, "properties": [
                {"id": "mappings", "value": [{"type": "value", "options": {
                    "true": {"text": "yes", "color": "green", "index": 0},
                    "1": {"text": "yes", "color": "green", "index": 1},
                    "false": {"text": "no", "color": "red", "index": 2},
                    "0": {"text": "no", "color": "red", "index": 3}}}]},
                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                {"id": "custom.width", "value": 80}]},
            {"matcher": {"id": "byName", "options": "Last seen"}, "properties": [{"id": "unit", "value": "dateTimeAsIso"}]},
            {"matcher": {"id": "byName", "options": "Action"}, "properties": [{"id": "custom.width", "value": 80}]},
        ]}
        return p

    panels = []
    ts = copy.deepcopy(P[1]); ts.update({"id": 1, "title": "Authorization checks over time",
        "description": "Final decisions (allowed, denied) and checks NiFi passed on to the parent component (resourceNotFound = 'inherit'). NiFi node requests left out.",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 8}})
    t = ts["targets"][0]; t["query"] = BASE
    for b in t["bucketAggs"]:
        if b["type"] == "filters":
            b["settings"]["filters"] = [{"query": "result.keyword:allowed", "label": "allowed"},
                                        {"query": "result.keyword:denied", "label": "denied"},
                                        {"query": "result.keyword:inherit", "label": "passed to parent"}]
    panels.append(ts)

    panels.append(table(2, "Denied requests",
        "Who was denied what: user, read/write, component. The Keycloak groups behind the decisions are in 'Keycloak groups per user'.",
        BASE + " AND result.keyword:denied",
        ["user.keyword", "method.keyword", "resource_name.keyword", "resource.keyword"],
        {"user.keyword": "User", "method.keyword": "Action", "resource_name.keyword": "Component",
         "resource.keyword": "Resource", "Count": "Requests", "Max": "Last seen"},
        {"x": 0, "y": 8, "w": 24, "h": 9}))

    panels.append(table(3, "Team process groups: access per user",
        "The process groups marketing and finance: each team edits its own, users without a team may look, the other team sees nothing.",
        BASE + " AND resource_name.keyword:(marketing OR finance OR \"Data for marketing\" OR \"Data for finance\") AND _exists_:allowed",
        ["user.keyword", "resource_name.keyword", "method.keyword", "allowed"],
        {"user.keyword": "User", "resource_name.keyword": "Process group", "method.keyword": "Action",
         "allowed": "Allowed", "Count": "Checks"},
        {"x": 0, "y": 17, "w": 12, "h": 11}, extra_max=False))

    logs = copy.deepcopy(P[4]); logs.update({"id": 4, "gridPos": {"x": 12, "y": 17, "w": 12, "h": 11},
        "description": "OPA decision log for NiFi, newest first."})
    q(logs, BASE)
    panels.append(logs)

    panels.append(table(5, "Components decided by their parent",
        "Checks the policy answered with resourceNotFound: NiFi then asks again for the parent (processor -> process group -> root). Shows which components inherit their permissions.",
        BASE + " AND result.keyword:inherit",
        ["resource_name.keyword", "resource.keyword", "method.keyword"],
        {"resource_name.keyword": "Component", "resource.keyword": "Resource", "method.keyword": "Action", "Count": "Checks"},
        {"x": 0, "y": 28, "w": 24, "h": 9}, extra_max=False))

    panels.append(table(6, "Keycloak groups per user",
        "The Keycloak groups OPA's User Info Fetcher returned for each user (decisions with groups only).",
        BASE + " AND _exists_:groups",
        ["groups.keyword"], {"groups.keyword": "Groups", "Count": "Decisions"},
        {"x": 0, "y": 37, "w": 12, "h": 7}, extra_max=False))
    panels[-1]["targets"][0]["bucketAggs"].insert(0, dict(terms("user.keyword"), id="9"))
    panels[-1]["transformations"][0]["options"]["renameByName"]["user.keyword"] = "User"

    uv = copy.deepcopy(af["templating"]["list"][0])
    uv["query"] = json.dumps({"find": "terms", "field": "user.keyword", "query": f"system.keyword:nifi AND {NODE}"})
    dash = {k: af[k] for k in ("schemaVersion", "editable", "time", "refresh") if k in af}
    dash.update({"uid": "nifi-authorization", "title": "NiFi authorization", "tags": ["nifi", "opa", "governance"],
                 "description": "OPA's decisions for Apache NiFi (logs-opa-decisions-*, system=nifi) with the Keycloak groups they were based on.",
                 "templating": {"list": [uv, copy.deepcopy(af["templating"]["list"][1])]}, "panels": panels})
    s = json.dumps(dash, indent=2, ensure_ascii=True)
    open(M + "grafana-dashboards-nifi.yaml", "w").write("""---
# Grafana dashboard "NiFi authorization": OPA's decisions for Apache NiFi (logs-opa-decisions-*,
# system=nifi; policy platform/manifests/opa/rego-nifi-policies.yaml) with the Keycloak groups they
# were based on. NiFi node requests (CN=generated certificate for pod) are left out.
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards-nifi
  namespace: platform
  labels:
    grafana_dashboard: '1'
  annotations:
    grafana_folder: Governance
data:
  nifi-authorization.json: |
""" + "\n".join("    " + l for l in s.splitlines()) + "\n")
    print("NiFi authorization")


def branding_and_overview():
    """Common header on the governance dashboards, and the overview (home) dashboard."""
    PROM = {"type": "prometheus", "uid": "prometheus"}
    DSD = {"type": "grafana-opensearch-datasource", "uid": "opensearch-opa-decisions"}
    DSQ = {"type": "grafana-opensearch-datasource", "uid": "opensearch-trino-queries"}
    HUMANS = "NOT user.keyword:(admin OR cockpit OR \"CN=generated certificate for pod\")"
    HEADER_ID = 900
    NAV = [("sdp-overview", "Overview"), ("trino-queries", "Trino queries"),
           ("airflow-authorization", "Airflow authorization"), ("nifi-authorization", "NiFi authorization"),
           ("opa-decisions", "OPA decisions"),
           ("stackable-logs", "Logs")]

    def header(uid, subtitle):
        links = []
        for u, t in NAV:
            style = "font-weight:600;text-decoration:underline" if u == uid else "opacity:0.85"
            links.append(f'<a href="/d/{u}" style="{style}">{t}</a>')
        html = (
            '<div style="display:flex;align-items:center;gap:14px;height:100%">'
            '<a href="/d/sdp-overview"><img src="/public/img/stackable/stackable-icon.svg" alt="Stackable" style="height:40px"></a>'
            '<div><div style="font-size:20px;font-weight:600;line-height:1.2">Stackable Data Platform</div>'
            f'<div style="opacity:0.7;font-size:13px">{subtitle}</div></div>'
            f'<div style="margin-left:auto;font-size:13px;display:flex;gap:16px">{"".join(links)}</div></div>')
        return {"id": HEADER_ID, "type": "text", "title": "", "transparent": True,
                "gridPos": {"x": 0, "y": 0, "w": 24, "h": 3},
                "options": {"mode": "html", "content": html}}

    def brand(j, subtitle):
        panels = [p for p in j["panels"] if p["id"] != HEADER_ID]
        if not any(p["id"] == HEADER_ID for p in j["panels"]):
            for p in panels: p["gridPos"]["y"] += 3
        j["panels"] = [header(j["uid"], subtitle)] + panels
        return j

    def rewrite(fname, subtitles):
        text = open(M + fname).read()
        head = text[:text.index("\ndata:\n") + len("\ndata:\n")]
        d = yaml.safe_load(text)
        out = head
        for k, v in d["data"].items():
            j = json.loads(v)
            if j["uid"] in subtitles: j = brand(j, subtitles[j["uid"]])
            out += block(k, j)
        open(M + fname, "w").write(out)

    rewrite("grafana-dashboards-trino-queries.yaml", {
        "trino-queries": "Trino: completed queries and what OPA did to them",
        "trino-query-detail": "Trino: one query, its OPA decisions and logs"})
    rewrite("grafana-dashboards-airflow.yaml", {"airflow-authorization": "Airflow: authorization decisions (OPA)"})
    rewrite("grafana-dashboards-nifi.yaml", {"nifi-authorization": "NiFi: authorization decisions (OPA)"})
    rewrite("grafana-dashboards-opa.yaml", {"opa-decisions": "OPA: all authorization decisions"})
    rewrite("grafana-dashboards-logs.yaml", {"stackable-logs": "Logs of all products (OpenSearch)"})

    # ------------------------------------------------------------------ overview
    # name, app.kubernetes.io/name (Prometheus), governance dashboard, metrics dashboard
    PRODUCTS = [
        ("Trino", "Trino", "trino", "/d/trino-queries", "/d/-mVc4yO4k"),
        ("Airflow", "Apache Airflow", "airflow", "/d/airflow-authorization", None),
        ("HDFS", "Apache Hadoop HDFS", "hdfs", None, "/d/qjZGZ3AVz"),
        ("Kafka", "Apache Kafka", "kafka", None, "/d/C_8qIX04k"),
        ("NiFi", "Apache NiFi", "nifi", "/d/nifi-authorization", "/d/C_8qIX04l"),
        ("Superset", "Apache Superset", "superset", None, None),
        ("Hive", "Apache Hive (metastore)", "hive", None, None),
        ("ZooKeeper", "Apache ZooKeeper", "zookeeper", None, None),
        ("OpenSearch", "OpenSearch", "opensearch", None, None),
        ("OPA", "Open Policy Agent", "opa", "/d/opa-decisions", None),
    ]
    STATUS = [
        {"type": "range", "options": {"from": 1, "to": 1, "result": {"text": "Up", "color": "green", "index": 0}}},
        {"type": "range", "options": {"from": 0.0001, "to": 0.9999, "result": {"text": "Degraded", "color": "orange", "index": 1}}},
        {"type": "value", "options": {"0": {"text": "Down", "color": "red", "index": 2}}},
        {"type": "special", "options": {"match": "null", "result": {"text": "No metrics", "color": "#5a5a5a", "index": 3}}},
    ]
    panels = [header("sdp-overview", "All products: status, authorization and drill-down"),
              {"id": 1, "type": "row", "title": "Products", "collapsed": False, "gridPos": {"x": 0, "y": 3, "w": 24, "h": 1}, "panels": []}]
    for i, (name, full, app, gov, met) in enumerate(PRODUCTS):
        x, y = (i % 6) * 4, 4 + (i // 6) * 4
        links, plinks, desc = [], [], [f"{full}. Status: share of its Prometheus scrape targets that are up."]
        if gov:
            links.append({"title": f"{name}: governance", "url": gov + "?${__url_time_range}"})
            plinks.append({"title": "Governance dashboard", "url": gov + "?${__url_time_range}"})
            desc.append("Click: governance dashboard.")
        if met:
            if not gov: links.append({"title": f"{name}: metrics", "url": met + "?${__url_time_range}"})
            plinks.append({"title": "Metrics dashboard", "url": met + "?${__url_time_range}"})
            desc.append("Metrics dashboard: link in the panel header." if gov else "Click: metrics dashboard.")
        panels.append({
            "id": 10 + i, "type": "stat", "title": name, "description": " ".join(desc), "datasource": PROM,
            "gridPos": {"x": x, "y": y, "w": 4, "h": 4}, "links": plinks,
            "targets": [{"refId": "A", "datasource": PROM, "instant": True, "range": False,
                         "expr": f'sum(up{{namespace="platform",app_kubernetes_io_name="{app}"}}) / count(up{{namespace="platform",app_kubernetes_io_name="{app}"}})'}],
            "fieldConfig": {"defaults": {"mappings": STATUS, "color": {"mode": "fixed", "fixedColor": "#5a5a5a"},
                                         "noValue": "No metrics", "links": links}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center",
                        "text": {"valueSize": 32}}})

    y = 12
    panels.append({"id": 2, "type": "row", "title": "Authorization (OPA), all products: people only, technical users (admin, cockpit, NiFi nodes) left out",
                   "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})

    def os_count(pid, title, desc, ds, query, x, w, color, metric=None):
        m = metric or {"id": "1", "type": "count"}
        return {"id": pid, "type": "stat", "title": title, "description": desc, "datasource": ds,
                "gridPos": {"x": x, "y": y + 1, "w": w, "h": 4},
                "targets": [{"refId": "A", "datasource": ds, "queryType": "lucene", "query": query, "timeField": "timestamp",
                             "metrics": [m], "bucketAggs": [{"id": "2", "type": "date_histogram", "field": "timestamp",
                                                              "settings": {"interval": "auto", "min_doc_count": "0"}}]}],
                "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": color}, "noValue": "0"}, "overrides": []},
                "options": {"reduceOptions": {"calcs": ["sum"], "fields": "", "values": False}, "colorMode": "value",
                            "graphMode": "area", "textMode": "value", "justifyMode": "center"}}

    panels += [
        os_count(20, "Authorization checks", "Decisions OPA made for people, all products.", DSD, HUMANS, 0, 6, "blue"),
        os_count(21, "Denied", "Checks OPA denied (Trino: access denied; Airflow: action not allowed).", DSD, f"allowed:false AND {HUMANS}", 6, 6, "red"),
        os_count(22, "Masked Trino queries", "Trino queries that returned masked columns.", DSQ, f"access:masked AND {HUMANS}", 12, 6, "orange"),
        os_count(23, "Denied Trino queries", "Trino queries that failed with access denied.", DSQ, f"access:denied AND {HUMANS}", 18, 6, "red"),
    ]

    def ts(pid, title, desc, query, x):
        return {"id": pid, "type": "timeseries", "title": title, "description": desc, "datasource": DSD,
                "gridPos": {"x": x, "y": y + 5, "w": 12, "h": 8},
                "targets": [{"refId": "A", "datasource": DSD, "queryType": "lucene", "query": query, "timeField": "timestamp",
                             "metrics": [{"id": "1", "type": "count"}],
                             "bucketAggs": [{"id": "3", "type": "terms", "field": "system.keyword",
                                             "settings": {"size": "10", "order": "desc", "orderBy": "_count", "min_doc_count": "1"}},
                                            {"id": "2", "type": "date_histogram", "field": "timestamp",
                                             "settings": {"interval": "auto", "min_doc_count": "0"}}]}],
                "fieldConfig": {"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 70, "stacking": {"mode": "normal"},
                                                        "lineWidth": 0}}, "overrides": []},
                "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}}

    panels += [
        ts(24, "Checks per product", "OPA decisions for people over time, per product.", HUMANS, 0),
        ts(25, "Denials per product", "Denied checks over time, per product.", f"allowed:false AND {HUMANS}", 12),
    ]
    cols = ["timestamp", "system", "user", "message", "drilldown", "window_from_ms", "window_to_ms"]
    panels.append({
        "id": 26, "type": "table", "title": "Recent denials, all products",
        "description": "Newest first. Click a row: Trino goes to the query's detail page, Airflow and NiFi to their authorization dashboards (±1 min around the decision).",
        "datasource": DSD, "gridPos": {"x": 0, "y": y + 13, "w": 24, "h": 10},
        "targets": [{"refId": "A", "datasource": DSD, "queryType": "lucene", "query": f"allowed:false AND {HUMANS}",
                     "timeField": "timestamp", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "100"}}], "bucketAggs": []}],
        "transformations": [{"id": "organize", "options": {
            "includeByName": {c: True for c in cols}, "indexByName": {c: i for i, c in enumerate(cols)},
            "renameByName": {"timestamp": "Time", "system": "Product", "user": "User", "message": "Decision"}}}],
        "fieldConfig": {"defaults": {"links": [{"title": "Drill down",
                                                "url": "${__data.fields.drilldown}&from=${__data.fields.window_from_ms}&to=${__data.fields.window_to_ms}"}]},
                        "overrides": [
                            {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "unit", "value": "dateTimeAsIso"}, {"id": "custom.width", "value": 190}]},
                            {"matcher": {"id": "byName", "options": "Product"}, "properties": [{"id": "custom.width", "value": 110}]},
                            {"matcher": {"id": "byName", "options": "User"}, "properties": [{"id": "custom.width", "value": 150}]},
                        ] + [{"matcher": {"id": "byName", "options": c}, "properties": [{"id": "custom.hidden", "value": True}]}
                             for c in ("drilldown", "window_from_ms", "window_to_ms")]},
        "options": {"showHeader": True, "sortBy": [{"displayName": "Time", "desc": True}]}})

    overview = {"uid": "sdp-overview", "title": "Stackable Data Platform", "tags": ["stackable", "overview", "governance"],
                "description": "Entry point across all products: status (Prometheus), authorization decisions (OPA, OpenSearch) and drill-down into the product dashboards.",
                "editable": True, "schemaVersion": 39, "time": {"from": "now-24h", "to": "now"}, "refresh": "1m",
                "templating": {"list": []}, "panels": panels}
    open(M + "grafana-dashboards-overview.yaml", "w").write("""---
# Grafana dashboard "Stackable Data Platform": the entry point across all products and Grafana's home
# dashboard (grafana.ini dashboards.default_home_dashboard_path in platform/applications/prometheus.yaml).
#   - product tiles: status from Prometheus (share of scrape targets up); click -> the product's governance
#     dashboard (Trino queries, Airflow authorization, OPA decisions) or its metrics dashboard
#   - authorization across products from the OPA decision log (logs-opa-decisions-*), per product
#   - recent denials; each row drills down via the decision's `drilldown` field (set by the
#     vector-aggregator, transform opa_decisions). A new product: tile here + branch there.
# Generated together with the headers of the governance dashboards (logo: grafana-branding.yaml).
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards-overview
  namespace: platform
  labels:
    grafana_dashboard: '1'
  annotations:
    grafana_folder: Stackable Data Platform
data:
""" + block("sdp-overview.json", overview))
    print("Headers, Stackable Data Platform overview")


def kafka():
    """Kafka: topic and broker lists on the "Kafka" dashboard (vendored, grafana-dashboards-stackable.yaml),
    with drill-down to "Kafka topic" and "Kafka broker" (grafana-dashboards-kafka.yaml).

    Only the brokers' and controllers' JMX metrics (Prometheus): Kafka has no per-consumer-group metrics
    there, so consumer groups appear as cluster-wide counts. Series are reduced with max by (...) because a
    restarted pod leaves its old series behind for a while.
    """
    PROM = {"type": "prometheus", "uid": "prometheus"}
    S = 'namespace=~"$namespace",app_kubernetes_io_instance=~"$cluster"'
    B = S + ',app_kubernetes_io_component="broker"'
    T = B + ',topic="$topic"'
    P = S + ',pod="$broker"'
    POD_RE = 'namespace=~"$namespace",pod=~"${cluster}-(broker|controller)-.*"'
    VARS = "var-namespace=${namespace}&var-cluster=${cluster}&${__url_time_range}"
    TOPIC_URL = "/d/kafka-topic/kafka-topic?var-topic=${__data.fields.topic}&" + VARS
    BROKER_URL = "/d/kafka-broker/kafka-broker?var-broker=${__data.fields.pod}&" + VARS
    BROKER_STATE = [{"type": "value", "options": {
        "0": {"text": "Not running", "color": "red", "index": 0}, "1": {"text": "Starting", "color": "orange", "index": 1},
        "2": {"text": "Recovery", "color": "orange", "index": 2}, "3": {"text": "Running", "color": "green", "index": 3},
        "6": {"text": "Pending shutdown", "color": "orange", "index": 4}, "7": {"text": "Shutting down", "color": "red", "index": 5},
        "127": {"text": "Unknown", "color": "red", "index": 6}}}]
    ZERO_GOOD = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}

    def target(ref, expr, legend=None, instant=False):
        t = {"refId": ref, "datasource": PROM, "expr": expr, "range": not instant, "instant": instant}
        if instant: t["format"] = "table"
        if legend: t["legendFormat"] = legend
        return t

    def by(name, *props):
        return {"matcher": {"id": "byName", "options": name}, "properties": list(props)}

    def row(pid, title, y, collapsed=False):
        return {"id": pid, "type": "row", "title": title, "collapsed": collapsed, "panels": [],
                "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}}

    def stat(pid, title, desc, expr, grid, unit="short", color="blue", thresholds=None, mappings=None,
             spark=False, text_mode="value", legend=None, decimals=None):
        d = {"unit": unit, "color": {"mode": "thresholds" if thresholds else "fixed", "fixedColor": color},
             "mappings": mappings or [], "noValue": "-"}
        if thresholds: d["thresholds"] = thresholds
        if decimals is not None: d["decimals"] = decimals
        return {"id": pid, "type": "stat", "title": title, "description": desc, "datasource": PROM, "gridPos": grid,
                "targets": [target("A", expr, legend)],
                "fieldConfig": {"defaults": d, "overrides": []},
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                            "colorMode": "background" if (thresholds or mappings) else "value",
                            "graphMode": "area" if spark else "none", "textMode": text_mode,
                            "justifyMode": "center", "wideLayout": True}}

    def ts(pid, title, desc, targets, grid, unit="short", stack=False, negative=None, draw="line", fill=15, extra=None):
        custom = {"drawStyle": draw, "lineWidth": 1 if draw == "line" else 0, "fillOpacity": fill,
                  "gradientMode": "opacity", "showPoints": "never", "spanNulls": True,
                  "stacking": {"mode": "normal" if stack else "none"}}
        overrides = []
        if negative:
            overrides.append({"matcher": {"id": "byRegexp", "options": negative},
                              "properties": [{"id": "custom.transform", "value": "negative-Y"}]})
        p = {"id": pid, "type": "timeseries", "title": title, "description": desc, "datasource": PROM, "gridPos": grid,
             "targets": targets, "fieldConfig": {"defaults": {"unit": unit, "custom": custom, "min": None if negative else 0},
                                                 "overrides": overrides},
             "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                         "tooltip": {"mode": "multi", "sort": "desc"}}}
        if extra: p["fieldConfig"]["defaults"].update(extra)
        return p

    def table(pid, title, desc, queries, columns, grid, link=None, overrides=(), sort=None):
        """queries: [(refId, expr, column name)], merged on their common labels; columns: label renames,
        in display order, before the value columns."""
        names = dict(columns)
        names.update({f"Value #{r}": n for r, _, n in queries})
        order = [c for c, _ in columns] + [f"Value #{r}" for r, _, _ in queries]
        p = {"id": pid, "type": "table", "title": title, "description": desc, "datasource": PROM, "gridPos": grid,
             "targets": [target(r, e, instant=True) for r, e, _ in queries],
             "transformations": [{"id": "merge", "options": {}},
                                 {"id": "organize", "options": {
                                     "excludeByName": {"Time": True},
                                     "indexByName": {c: i for i, c in enumerate(order)},
                                     "renameByName": names}}],
             "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False},
                                          "noValue": "-"},
                             "overrides": list(overrides)},
             "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                         "sortBy": [{"displayName": sort, "desc": True}] if sort else []}}
        if link: p["fieldConfig"]["defaults"]["links"] = [{"title": link[0], "url": link[1]}]
        return p

    def gauge_cell(name, unit, color="blue"):
        return by(name, {"id": "unit", "value": unit},
                  {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "lcd", "valueDisplayMode": "text"}},
                  {"id": "color", "value": {"mode": "continuous-BlYlRd" if color == "heat" else "fixed", "fixedColor": color}},
                  {"id": "min", "value": 0})

    def alarm_cell(name):
        return by(name, {"id": "thresholds", "value": ZERO_GOOD}, {"id": "color", "value": {"mode": "thresholds"}},
                  {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                  {"id": "custom.width", "value": 130})

    def hidden(name):
        return by(name, {"id": "custom.hidden", "value": True})

    def per_partition(metric, sel, keys="topic, partition"):
        return f"max by ({keys}) ({metric}{{{sel}}})"

    # ------------------------------------------------------------------ "Kafka": topic and broker lists
    SRC = M + "grafana-dashboards-stackable.yaml"
    text = open(SRC).read()
    start = text.index("  kafka.json: |\n")
    end = text.index("\n  hdfs.json: |\n", start) + 1
    k = json.loads(yaml.safe_load(text[start:end].replace("  kafka.json: |\n", "kafka.json: |\n", 1))["kafka.json"])
    OWNED, OFFSET = 100, 18
    vendored = [p for p in k["panels"] if p["id"] < OWNED]
    if len(vendored) == len(k["panels"]):
        for p in vendored: p["gridPos"]["y"] += OFFSET
    TOPIC_SEL = B + ',topic!~"__.*"'
    topics = table(101, "Topics",
        "All topics with their partitions, retained messages (high minus low watermark), size, write and read rates "
        "(read: bytes fetched by consumers, without replication) and under-replicated partitions. Click a topic for its details.",
        [("A", f"count by (topic) ({per_partition('kafka_log_log_logendoffset', TOPIC_SEL)})", "Partitions"),
         ("B", f"sum by (topic) ({per_partition('kafka_log_log_logendoffset', TOPIC_SEL)} - {per_partition('kafka_log_log_logstartoffset', TOPIC_SEL)})", "Retained messages"),
         ("C", f"sum by (topic) ({per_partition('kafka_log_log_size', TOPIC_SEL)})", "Size"),
         ("D", f"sum by (topic) (rate(kafka_server_brokertopicmetrics_messagesin_total{{{TOPIC_SEL}}}[5m]))", "Messages in"),
         ("E", f"sum by (topic) (rate(kafka_server_brokertopicmetrics_bytesin_total{{{TOPIC_SEL}}}[5m]))", "Written"),
         ("F", f"sum by (topic) (rate(kafka_server_brokertopicmetrics_bytesout_total{{{TOPIC_SEL}}}[5m]))", "Read by consumers"),
         ("G", f"sum by (topic) ({per_partition('kafka_cluster_partition_underreplicated', TOPIC_SEL)})", "Under-replicated")],
        [("topic", "Topic")], {"x": 0, "y": 1, "w": 24, "h": 9},
        link=("Topic details: partitions, watermarks, consumers", TOPIC_URL), sort="Size",
        overrides=[by("Topic", {"id": "custom.width", "value": 240}),
                   by("Partitions", {"id": "custom.width", "value": 100}),
                   by("Retained messages", {"id": "unit", "value": "short"}),
                   gauge_cell("Size", "bytes", "purple"),
                   gauge_cell("Messages in", "short", "green"),
                   by("Messages in", {"id": "unit", "value": "suffix: msg/s"}),
                   gauge_cell("Written", "Bps", "green"), gauge_cell("Read by consumers", "Bps", "blue"),
                   alarm_cell("Under-replicated")])
    brokers = table(102, "Brokers and controllers",
        "Every Kafka process: brokers hold the partitions, the KRaft controllers the cluster metadata (one of them is the "
        "leader). Click a row for its details: assigned partitions, requests, replication, resources.",
        [("A", f"max by (pod, app_kubernetes_io_component) (up{{{S}}})", "Up"),
         ("B", f"max by (pod, state) (kafka_server_raft_metrics_current_state{{{S}}}) == 1", "_raft"),
         ("C", f"max by (pod) (kafka_server_kafkaserver_brokerstate{{{S}}})", "Broker state"),
         ("D", f"max by (pod) (kafka_server_replicamanager_partitioncount{{{S}}})", "Partitions"),
         ("E", f"max by (pod) (kafka_server_replicamanager_leadercount{{{S}}})", "Leaders"),
         ("F", f"max by (pod) (kafka_server_replicamanager_underreplicatedpartitions{{{S}}})", "Under-replicated"),
         ("G", f"sum by (pod) (rate(kafka_server_brokertopicmetrics_bytesin_total{{{S}}}[5m]))", "In"),
         ("H", f"sum by (pod) (rate(kafka_server_brokertopicmetrics_bytesout_total{{{S}}}[5m]))", "Out"),
         ("I", f"max by (pod) (kafka_server_kafkarequesthandlerpool_requesthandleravgidle_percent{{{S}}})", "Request handlers idle"),
         ("J", f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{POD_RE},container="kafka"}}[5m]))', "CPU"),
         ("K", f'sum by (pod) (container_memory_working_set_bytes{{{POD_RE},container="kafka"}})', "Memory"),
         ("L", f"time() - max by (pod) (kafka_server_app_info_start_time_ms{{{S}}}) / 1000", "Uptime")],
        [("pod", "Pod"), ("app_kubernetes_io_component", "Role"), ("state", "KRaft")],
        {"x": 0, "y": 10, "w": 24, "h": 7}, link=("Broker details", BROKER_URL),
        overrides=[by("Pod", {"id": "custom.width", "value": 230}), by("Role", {"id": "custom.width", "value": 100}),
                   by("KRaft", {"id": "custom.width", "value": 90},
                      {"id": "mappings", "value": [{"type": "value", "options": {"leader": {"color": "purple", "index": 0}}}]},
                      {"id": "custom.cellOptions", "value": {"type": "color-text"}}),
                   hidden("_raft"),
                   by("Up", {"id": "mappings", "value": [{"type": "value", "options": {
                       "1": {"text": "up", "color": "green", "index": 0}, "0": {"text": "down", "color": "red", "index": 1}}}]},
                      {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                      {"id": "custom.width", "value": 70}),
                   by("Broker state", {"id": "mappings", "value": BROKER_STATE},
                      {"id": "custom.cellOptions", "value": {"type": "color-text"}}),
                   alarm_cell("Under-replicated"),
                   gauge_cell("In", "Bps", "green"), gauge_cell("Out", "Bps", "blue"),
                   by("Request handlers idle", {"id": "unit", "value": "percentunit"}, {"id": "min", "value": 0}, {"id": "max", "value": 1},
                      {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                          {"color": "red", "value": None}, {"color": "orange", "value": 0.3}, {"color": "green", "value": 0.6}]}},
                      {"id": "color", "value": {"mode": "thresholds"}},
                      {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}}),
                   by("CPU", {"id": "unit", "value": "none"}, {"id": "decimals", "value": 2}, {"id": "custom.width", "value": 80}),
                   by("Memory", {"id": "unit", "value": "bytes"}, {"id": "custom.width", "value": 100}),
                   by("Uptime", {"id": "unit", "value": "dtdurations"}, {"id": "decimals", "value": 0})])
    k["panels"] = [row(100, "Topics and brokers (click a row for details)", 0), topics, brokers,
                   row(103, "Cluster resources and throughput", 17)] + vendored
    k["links"] = [{"title": "Kafka topic", "type": "link", "url": "/d/kafka-topic/kafka-topic", "icon": "dashboard", "keepTime": True},
                  {"title": "Kafka broker", "type": "link", "url": "/d/kafka-broker/kafka-broker", "icon": "dashboard", "keepTime": True}]
    k["description"] = "Kafka cluster: topics and brokers with drill-down (Kafka topic, Kafka broker), resources and throughput."
    out = "\n".join("    " + l for l in json.dumps(k, indent=2, ensure_ascii=True).splitlines()) + "\n"
    open(SRC, "w").write(text[:start] + "  kafka.json: |\n" + out + text[end:])

    var = {v["name"]: v for v in k["templating"]["list"]}
    common_vars = [copy.deepcopy(var["namespace"]), copy.deepcopy(var["cluster"])]
    for v in common_vars: v["hide"] = 2
    back = {"title": "Kafka", "type": "link", "url": "/d/C_8qIX04k", "icon": "arrow-left", "keepTime": True}

    # ------------------------------------------------------------------ "Kafka topic"
    tvar = {"name": "topic", "label": "Topic", "type": "query", "datasource": PROM, "refresh": 2, "sort": 1,
            "query": {"query": f'label_values(kafka_log_log_logendoffset{{{B},topic!~"__.*"}}, topic)', "refId": "topic"},
            "definition": f'label_values(kafka_log_log_logendoffset{{{B},topic!~"__.*"}}, topic)',
            "current": {}, "includeAll": False, "multi": False}
    END, START = per_partition("kafka_log_log_logendoffset", T), per_partition("kafka_log_log_logstartoffset", T)
    tp = [
        stat(1, "Partitions", "Partitions of the topic.", f"count({END})", {"x": 0, "y": 0, "w": 3, "h": 4}),
        stat(2, "Retained messages", "Messages currently stored: high minus low watermark, summed over the partitions.",
             f"sum({END} - {START})", {"x": 3, "y": 0, "w": 3, "h": 4}, spark=True),
        stat(3, "Size", "Bytes on disk (one replica).", f"sum({per_partition('kafka_log_log_size', T)})",
             {"x": 6, "y": 0, "w": 3, "h": 4}, unit="bytes", color="purple", spark=True),
        stat(4, "Segments", "Log segment files over all partitions; old segments are deleted as a whole when retention expires.",
             f"sum({per_partition('kafka_log_log_numlogsegments', T)})", {"x": 9, "y": 0, "w": 3, "h": 4}, color="purple"),
        stat(5, "Messages in", "Messages written per second.",
             f"sum(rate(kafka_server_brokertopicmetrics_messagesin_total{{{T}}}[$__rate_interval]))",
             {"x": 12, "y": 0, "w": 3, "h": 4}, unit="suffix: msg/s", color="green", spark=True, decimals=1),
        stat(6, "Written", "Bytes written per second by producers.",
             f"sum(rate(kafka_server_brokertopicmetrics_bytesin_total{{{T}}}[$__rate_interval]))",
             {"x": 15, "y": 0, "w": 3, "h": 4}, unit="Bps", color="green", spark=True),
        stat(7, "Read by consumers", "Bytes fetched per second by consumers (replication not included).",
             f"sum(rate(kafka_server_brokertopicmetrics_bytesout_total{{{T}}}[$__rate_interval]))",
             {"x": 18, "y": 0, "w": 3, "h": 4}, unit="Bps", color="blue", spark=True),
        stat(8, "Under-replicated", "Partitions with fewer in-sync replicas than replicas. Should be 0.",
             f"sum({per_partition('kafka_cluster_partition_underreplicated', T)})", {"x": 21, "y": 0, "w": 3, "h": 4},
             thresholds=ZERO_GOOD),
        row(9, "Partitions", 4),
        table(10, "Partitions and watermarks",
              "One row per partition replica. Low watermark: first offset still stored (retention moves it up); high watermark: "
              "next offset to be written. Replicas and ISR come from the partition's leader. Click a row for its broker.",
              [("A", per_partition("kafka_log_log_logstartoffset", T, "partition, pod"), "Low watermark"),
               ("B", per_partition("kafka_log_log_logendoffset", T, "partition, pod"), "High watermark"),
               ("C", f'{per_partition("kafka_log_log_logendoffset", T, "partition, pod")} - {per_partition("kafka_log_log_logstartoffset", T, "partition, pod")}', "Retained messages"),
               ("D", per_partition("kafka_log_log_size", T, "partition, pod"), "Size"),
               ("E", per_partition("kafka_log_log_numlogsegments", T, "partition, pod"), "Segments"),
               ("F", f'{per_partition("kafka_cluster_partition_replicascount", T, "partition, pod")} * 0 + 1', "Role"),
               ("G", per_partition("kafka_cluster_partition_replicascount", T, "partition, pod"), "Replicas"),
               ("H", per_partition("kafka_cluster_partition_insyncreplicascount", T, "partition, pod"), "In sync"),
               ("I", per_partition("kafka_cluster_partition_underreplicated", T, "partition, pod"), "Under-replicated"),
               ("J", per_partition("kafka_cluster_partition_underminisr", T, "partition, pod"), "Under min ISR"),
               ("K", per_partition("kafka_cluster_partition_laststableoffsetlag", T, "partition, pod"), "Open transactions lag")],
              [("partition", "Partition"), ("pod", "Broker")], {"x": 0, "y": 5, "w": 24, "h": 7},
              link=("Broker details", BROKER_URL), sort=None,
              overrides=[by("Partition", {"id": "custom.width", "value": 90}), by("Broker", {"id": "custom.width", "value": 210}),
                         by("Low watermark", {"id": "unit", "value": "none"}), by("High watermark", {"id": "unit", "value": "none"}),
                         gauge_cell("Retained messages", "short", "purple"), gauge_cell("Size", "bytes", "purple"),
                         by("Role", {"id": "mappings", "value": [
                             {"type": "value", "options": {"1": {"text": "leader", "color": "green", "index": 0}}},
                             {"type": "special", "options": {"match": "null", "result": {"text": "follower", "index": 1}}}]},
                            {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "custom.width", "value": 90}),
                         alarm_cell("Under-replicated"), alarm_cell("Under min ISR")]),
        ts(11, "Watermarks per partition", "Low and high watermark over time: the gap is what the topic retains, the slope of the "
           "high watermark is the write rate, steps of the low watermark are deleted segments.",
           [target("A", per_partition("kafka_log_log_logendoffset", T, "partition"), "high, partition {{partition}}"),
            target("B", per_partition("kafka_log_log_logstartoffset", T, "partition"), "low, partition {{partition}}")],
           {"x": 0, "y": 12, "w": 12, "h": 8}, unit="none", fill=0),
        ts(12, "Partition health", "Under-replicated and under-min-ISR partitions over time (0 = healthy).",
           [target("A", f"sum({per_partition('kafka_cluster_partition_underreplicated', T)})", "under-replicated"),
            target("B", f"sum({per_partition('kafka_cluster_partition_underminisr', T)})", "under min ISR"),
            target("C", f"sum({per_partition('kafka_cluster_partition_atminisr', T)})", "at min ISR")],
           {"x": 12, "y": 12, "w": 12, "h": 8}, draw="bars", fill=80,
           extra={"thresholds": ZERO_GOOD, "color": {"mode": "palette-classic"}}),
        row(13, "Throughput", 20),
        ts(14, "Messages in", "Messages written per second.",
           [target("A", f"sum(rate(kafka_server_brokertopicmetrics_messagesin_total{{{T}}}[$__rate_interval]))", "messages in")],
           {"x": 0, "y": 21, "w": 8, "h": 8}, unit="suffix: msg/s", fill=30),
        ts(15, "Written and read", "Bytes per second: written by producers (up), read by consumers (down).",
           [target("A", f"sum(rate(kafka_server_brokertopicmetrics_bytesin_total{{{T}}}[$__rate_interval]))", "written"),
            target("B", f"sum(rate(kafka_server_brokertopicmetrics_bytesout_total{{{T}}}[$__rate_interval]))", "read by consumers")],
           {"x": 8, "y": 21, "w": 8, "h": 8}, unit="Bps", negative="read.*", fill=30),
        ts(16, "Produce and fetch requests", "Requests per second for this topic; fetches include those of follower replicas.",
           [target("A", f"sum(rate(kafka_server_brokertopicmetrics_totalproducerequests_total{{{T}}}[$__rate_interval]))", "produce"),
            target("B", f"sum(rate(kafka_server_brokertopicmetrics_totalfetchrequests_total{{{T}}}[$__rate_interval]))", "fetch"),
            target("C", f"sum(rate(kafka_server_brokertopicmetrics_failedproducerequests_total{{{T}}}[$__rate_interval]))", "failed produce"),
            target("D", f"sum(rate(kafka_server_brokertopicmetrics_failedfetchrequests_total{{{T}}}[$__rate_interval]))", "failed fetch")],
           {"x": 16, "y": 21, "w": 8, "h": 8}, unit="reqps"),
        ts(17, "Size and retained messages", "Bytes on disk and messages retained over time.",
           [target("A", f"sum({per_partition('kafka_log_log_size', T)})", "size"),
            target("B", f"sum({END} - {START})", "retained messages")],
           {"x": 0, "y": 29, "w": 24, "h": 7}, unit="bytes",
           extra={}),
        row(18, "Consumers", 36),
        {"id": 19, "type": "text", "title": "", "transparent": True, "gridPos": {"x": 0, "y": 37, "w": 24, "h": 2},
         "options": {"mode": "markdown", "content":
             "Kafka's own metrics have **no per-group data**: the panels below are cluster-wide. Consumer groups only "
             "appear when consumers commit offsets to Kafka. The demo's Spark jobs keep their offsets in their "
             "checkpoints, so 0 groups is expected; what they read shows as *Read by consumers*."}},
        stat(20, "Consumer groups", "Groups the group coordinators know, all topics.",
             f"sum(max by (pod) (kafka_coordinator_group_groupmetadatamanager_numgroups{{{S}}}))", {"x": 0, "y": 39, "w": 4, "h": 5}),
        {**stat(21, "Groups by state", "Consumer groups per state, all topics.", "", {"x": 4, "y": 39, "w": 10, "h": 5}),
         "targets": [target(r, f"sum(max by (pod) (kafka_coordinator_group_groupmetadatamanager_numgroups{st}{{{S}}}))", lbl)
                     for r, st, lbl in [("A", "stable", "stable"), ("B", "empty", "empty"),
                                        ("C", "preparingrebalance", "preparing rebalance"),
                                        ("D", "completingrebalance", "completing rebalance"), ("E", "dead", "dead")]]},
        stat(22, "Committed offsets", "Committed offsets stored, all groups and partitions.",
             f"sum(max by (pod) (kafka_coordinator_group_groupmetadatamanager_numoffsets{{{S}}}))", {"x": 14, "y": 39, "w": 5, "h": 5}),
        stat(23, "Offset commits", "Offset commits per second, all groups.",
             f"sum(max by (pod) (kafka_server_group_coordinator_metrics_offset_commit_rate{{{S}}}))", {"x": 19, "y": 39, "w": 5, "h": 5},
             unit="suffix: /s", spark=True, decimals=2),
    ]
    panel = {p["id"]: p for p in tp}
    panel[17]["fieldConfig"]["overrides"] = [by("retained messages", {"id": "unit", "value": "short"},
                                                {"id": "custom.axisPlacement", "value": "right"})]
    panel[21]["options"]["textMode"] = "value_and_name"
    topic = {"uid": "kafka-topic", "title": "Kafka topic", "tags": ["kafka"], "editable": True, "schemaVersion": 41,
             "description": "One Kafka topic: partitions with watermarks, throughput, consumers. Opened from 'Kafka'.",
             "time": {"from": "now-6h", "to": "now"}, "refresh": "1m",
             "templating": {"list": common_vars + [tvar]}, "links": [back], "panels": tp}

    # ------------------------------------------------------------------ "Kafka broker"
    bvar = {"name": "broker", "label": "Broker", "type": "query", "datasource": PROM, "refresh": 2, "sort": 1,
            "query": {"query": f"label_values(up{{{S},app_kubernetes_io_name=\"kafka\"}}, pod)", "refId": "broker"},
            "definition": f"label_values(up{{{S},app_kubernetes_io_name=\"kafka\"}}, pod)",
            "current": {}, "includeAll": False, "multi": False}
    PB = S + ',pod="$broker"'
    PBT = PB + ',topic!~"__.*"'
    POD = 'namespace=~"$namespace",pod="$broker"'
    bp = [
        stat(1, "Role", "Broker (holds partitions) or KRaft controller (holds the cluster metadata).",
             f"max by (app_kubernetes_io_component) (up{{{PB}}})", {"x": 0, "y": 0, "w": 3, "h": 4},
             text_mode="name", legend="{{app_kubernetes_io_component}}", color="purple"),
        stat(2, "KRaft", "Role in the metadata quorum: leader (the active controller), follower (other controllers), observer (brokers).",
             f"max by (state) (kafka_server_raft_metrics_current_state{{{PB}}}) == 1", {"x": 3, "y": 0, "w": 3, "h": 4},
             text_mode="name", legend="{{state}}", color="purple"),
        stat(3, "Broker state", "Brokers only.", f"max(kafka_server_kafkaserver_brokerstate{{{PB}}})",
             {"x": 6, "y": 0, "w": 3, "h": 4}, mappings=BROKER_STATE),
        stat(4, "Uptime", "Since the Kafka process started.", f"time() - max(kafka_server_app_info_start_time_ms{{{PB}}}) / 1000",
             {"x": 9, "y": 0, "w": 3, "h": 4}, unit="dtdurations", decimals=0),
        stat(5, "Partitions", "Partition replicas on this broker.", f"max(kafka_server_replicamanager_partitioncount{{{PB}}})",
             {"x": 12, "y": 0, "w": 3, "h": 4}),
        stat(6, "Leaders", "Partitions this broker leads (serves reads and writes for).",
             f"max(kafka_server_replicamanager_leadercount{{{PB}}})", {"x": 15, "y": 0, "w": 3, "h": 4}),
        stat(7, "Under-replicated", "Partitions led here with replicas out of sync. Should be 0.",
             f"max(kafka_server_replicamanager_underreplicatedpartitions{{{PB}}})", {"x": 18, "y": 0, "w": 3, "h": 4}, thresholds=ZERO_GOOD),
        stat(8, "Offline replicas", "Replicas on this broker that are offline (e.g. failed log directory). Should be 0.",
             f"max(kafka_server_replicamanager_offlinereplicacount{{{PB}}})", {"x": 21, "y": 0, "w": 3, "h": 4}, thresholds=ZERO_GOOD),
        row(9, "Assigned partitions", 4),
        table(10, "Partitions on this broker",
              "Every partition replica stored here, with its watermarks. Click a row for the topic.",
              [("A", per_partition("kafka_log_log_logstartoffset", PBT), "Low watermark"),
               ("B", per_partition("kafka_log_log_logendoffset", PBT), "High watermark"),
               ("C", per_partition("kafka_log_log_logendoffset", PBT) + " - " + per_partition("kafka_log_log_logstartoffset", PBT), "Retained messages"),
               ("D", per_partition("kafka_log_log_size", PBT), "Size"),
               ("E", per_partition("kafka_log_log_numlogsegments", PBT), "Segments"),
               ("F", f'{per_partition("kafka_cluster_partition_replicascount", PB)} * 0 + 1', "Role"),
               ("G", per_partition("kafka_cluster_partition_replicascount", PB), "Replicas"),
               ("H", per_partition("kafka_cluster_partition_insyncreplicascount", PB), "In sync"),
               ("I", per_partition("kafka_cluster_partition_underreplicated", PB), "Under-replicated")],
              [("topic", "Topic"), ("partition", "Partition")], {"x": 0, "y": 5, "w": 24, "h": 8},
              link=("Topic details", TOPIC_URL), sort="Size",
              overrides=[by("Topic", {"id": "custom.width", "value": 240}), by("Partition", {"id": "custom.width", "value": 90}),
                         by("Low watermark", {"id": "unit", "value": "none"}), by("High watermark", {"id": "unit", "value": "none"}),
                         gauge_cell("Retained messages", "short", "purple"), gauge_cell("Size", "bytes", "purple"),
                         by("Role", {"id": "mappings", "value": [
                             {"type": "value", "options": {"1": {"text": "leader", "color": "green", "index": 0}}},
                             {"type": "special", "options": {"match": "null", "result": {"text": "follower", "index": 1}}}]},
                            {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "custom.width", "value": 90}),
                         alarm_cell("Under-replicated")]),
        row(11, "Throughput", 13),
        ts(12, "Bytes per topic", "Written (up) and read by consumers (down) per topic.",
           [target("A", f"sum by (topic) (rate(kafka_server_brokertopicmetrics_bytesin_total{{{PB}}}[$__rate_interval]))", "{{topic}} written"),
            target("B", f"sum by (topic) (rate(kafka_server_brokertopicmetrics_bytesout_total{{{PB}}}[$__rate_interval]))", "{{topic}} read")],
           {"x": 0, "y": 14, "w": 12, "h": 8}, unit="Bps", negative=".* read", stack=True, fill=40),
        ts(13, "Messages in per topic", "Messages written per second, per topic.",
           [target("A", f"sum by (topic) (rate(kafka_server_brokertopicmetrics_messagesin_total{{{PB}}}[$__rate_interval]))", "{{topic}}")],
           {"x": 12, "y": 14, "w": 12, "h": 8}, unit="suffix: msg/s", stack=True, fill=40),
        row(14, "Requests", 22),
        ts(15, "Requests per type", "Requests per second by API (top 8).",
           [target("A", f"topk(8, sum by (request) (rate(kafka_network_requestmetrics_requests_total{{{PB}}}[$__rate_interval])))", "{{request}}")],
           {"x": 0, "y": 23, "w": 8, "h": 8}, unit="reqps", stack=True, fill=40),
        ts(16, "Request latency (p99)", "99th percentile of the total request time for the main APIs. Fetch includes the time a consumer waits for new data (long poll, up to 500 ms by default).",
           [target("A", f'max by (request) (kafka_network_requestmetrics_totaltimems{{{PB},quantile="0.99",request=~"Produce|FetchConsumer|FetchFollower|Fetch|Metadata|OffsetCommit|BrokerHeartbeat|Vote|BeginQuorumEpoch"}})', "{{request}}")],
           {"x": 8, "y": 23, "w": 8, "h": 8}, unit="ms", fill=0),
        ts(17, "Thread pools idle", "Share of time the request handler and network threads are idle; below ~30 % the broker is saturated.",
           [target("A", f"max(kafka_server_kafkarequesthandlerpool_requesthandleravgidle_percent{{{PB}}})", "request handlers"),
            target("B", f"avg(kafka_network_processor_idlepercent{{{PB}}})", "network processors")],
           {"x": 16, "y": 23, "w": 8, "h": 8}, unit="percentunit", fill=10,
           extra={"max": 1, "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "transparent", "value": 0.3}]},
                  "custom": {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 10, "showPoints": "never", "spanNulls": True,
                             "gradientMode": "opacity", "stacking": {"mode": "none"}, "thresholdsStyle": {"mode": "area"}}}),
        row(18, "Replication", 31),
        ts(19, "ISR changes", "Replicas leaving (shrinks) and rejoining (expands) the in-sync set per second.",
           [target("A", f"sum(rate(kafka_server_replicamanager_isrshrinks_total{{{PB}}}[$__rate_interval]))", "shrinks"),
            target("B", f"sum(rate(kafka_server_replicamanager_isrexpands_total{{{PB}}}[$__rate_interval]))", "expands")],
           {"x": 0, "y": 32, "w": 12, "h": 7}, unit="suffix: /s", draw="bars", fill=80),
        ts(20, "Replica fetcher lag", "Largest lag (messages) of this broker's follower replicas behind their leaders.",
           [target("A", f"max(kafka_server_replicafetchermanager_maxlag{{{PB}}})", "max lag")],
           {"x": 12, "y": 32, "w": 12, "h": 7}, unit="short"),
        row(21, "Resources", 39),
        ts(22, "CPU", "CPU of the kafka container (cores).",
           [target("A", f'sum(rate(container_cpu_usage_seconds_total{{{POD},container="kafka"}}[$__rate_interval]))', "used"),
            target("B", f'max(kube_pod_container_resource_limits{{{POD},container="kafka",resource="cpu"}})', "limit")],
           {"x": 0, "y": 40, "w": 6, "h": 7}, unit="none", fill=20),
        ts(23, "Memory", "Working set of the kafka container.",
           [target("A", f'sum(container_memory_working_set_bytes{{{POD},container="kafka"}})', "used"),
            target("B", f'max(kube_pod_container_resource_limits{{{POD},container="kafka",resource="memory"}})', "limit")],
           {"x": 6, "y": 40, "w": 6, "h": 7}, unit="bytes", fill=20),
        ts(24, "Log volume", "Used and total space of the log directory volume.",
           [target("A", 'max(kubelet_volume_stats_used_bytes{namespace=~"$namespace",persistentvolumeclaim="log-dirs-$broker"})', "used"),
            target("B", 'max(kubelet_volume_stats_capacity_bytes{namespace=~"$namespace",persistentvolumeclaim="log-dirs-$broker"})', "capacity")],
           {"x": 12, "y": 40, "w": 6, "h": 7}, unit="bytes", fill=20),
        ts(25, "Disk I/O", "Bytes read (up) and written (down) per second by the Kafka process.",
           [target("A", f'sum(rate({{__name__=~"kafka_server_(kafkaserver|controllerserver)_linux_disk_read_bytes",{PB}}}[$__rate_interval]))', "read"),
            target("B", f'sum(rate({{__name__=~"kafka_server_(kafkaserver|controllerserver)_linux_disk_write_bytes",{PB}}}[$__rate_interval]))', "write")],
           {"x": 18, "y": 40, "w": 6, "h": 7}, unit="Bps", negative="write", fill=30),
        row(26, "KRaft metadata quorum", 47),
        {"id": 27, "type": "state-timeline", "title": "Quorum role", "description": "This process's role in the KRaft quorum over time.",
         "datasource": PROM, "gridPos": {"x": 0, "y": 48, "w": 12, "h": 4},
         "targets": [target("A", f'sum(max by (state) (kafka_server_raft_metrics_current_state{{{PB}}} == 1) * on (state) group_left() '
                                 '(label_replace(vector(1), "state", "leader", "", "") or label_replace(vector(2), "state", "follower", "", "") '
                                 'or label_replace(vector(3), "state", "observer", "", "") or label_replace(vector(4), "state", "candidate", "", "") '
                                 'or label_replace(vector(5), "state", "voted", "", "") or label_replace(vector(6), "state", "unattached", "", "")))', "role")],
         "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {
             "1": {"text": "leader", "color": "purple", "index": 0}, "2": {"text": "follower", "color": "blue", "index": 1},
             "3": {"text": "observer", "color": "green", "index": 2}, "4": {"text": "candidate", "color": "orange", "index": 3},
             "5": {"text": "voted", "color": "orange", "index": 4}, "6": {"text": "unattached", "color": "red", "index": 5}}}],
             "color": {"mode": "thresholds"}, "custom": {"fillOpacity": 80, "lineWidth": 0}}, "overrides": []},
         "options": {"showValue": "auto", "mergeValues": True, "rowHeight": 0.8, "legend": {"showLegend": False}}},
        stat(28, "Epoch", "Current leader epoch of the quorum (increases with every election).",
             f"max(kafka_server_raft_metrics_current_epoch{{{PB}}})", {"x": 12, "y": 48, "w": 4, "h": 4}, color="purple", spark=True),
        stat(29, "Metadata lag", "How far behind this process's metadata is (ms since the last applied record was written).",
             f"max(kafka_server_broker_metadata_metrics_last_applied_record_lag_ms{{{PB}}} or kafka_controller_kafkacontroller_lastappliedrecordlagms{{{PB}}})",
             {"x": 16, "y": 48, "w": 4, "h": 4}, unit="ms", spark=True),
        stat(30, "Metadata offset", "High watermark of the metadata log.",
             f"max(kafka_server_raft_metrics_high_watermark{{{PB}}})", {"x": 20, "y": 48, "w": 4, "h": 4}, unit="none", spark=True),
        ts(31, "Commit latency", "Time for the quorum to commit metadata records (leader).",
           [target("A", f"max(kafka_server_raft_metrics_commit_latency_avg{{{PB}}})", "avg"),
            target("B", f"max(kafka_server_raft_metrics_commit_latency_max{{{PB}}})", "max")],
           {"x": 0, "y": 52, "w": 12, "h": 7}, unit="ms", fill=0),
        ts(32, "Metadata records", "Records appended (leader) and fetched (followers, observers) per second.",
           [target("A", f"max(kafka_server_raft_metrics_append_records_rate{{{PB}}})", "appended"),
            target("B", f"max(kafka_server_raft_metrics_fetch_records_rate{{{PB}}})", "fetched")],
           {"x": 12, "y": 52, "w": 12, "h": 7}, unit="suffix: /s", fill=10),
    ]
    broker = {"uid": "kafka-broker", "title": "Kafka broker", "tags": ["kafka"], "editable": True, "schemaVersion": 41,
              "description": "One Kafka broker or KRaft controller: assigned partitions, throughput, requests, replication, resources, quorum. Opened from 'Kafka'.",
              "time": {"from": "now-6h", "to": "now"}, "refresh": "1m",
              "templating": {"list": common_vars + [bvar]}, "links": [back], "panels": bp}
    open(M + "grafana-dashboards-kafka.yaml", "w").write("""---
# Grafana dashboards "Kafka topic" and "Kafka broker": drill-down from the topic and broker lists on the
# "Kafka" dashboard (grafana-dashboards-stackable.yaml). Prometheus only (the brokers' and controllers'
# JMX metrics); Kafka has no per-consumer-group metrics there. Generated by scripts/grafana-dashboards.py.
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards-kafka
  namespace: platform
  labels:
    grafana_dashboard: '1'
  annotations:
    grafana_folder: Stackable Data Platform
data:
""" + block("kafka-topic.json", topic) + block("kafka-broker.json", broker))
    print("Kafka, Kafka topic, Kafka broker")


if __name__ == "__main__":
    trino_queries()
    nifi_authorization()
    branding_and_overview()
    kafka()
