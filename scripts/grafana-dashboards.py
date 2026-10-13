#!/usr/bin/env python3
"""Generates the Grafana governance dashboards in platform/manifests/prometheus/ (ConfigMaps for the
Grafana dashboard sidecar). The YAML files are committed; run this after changing it and commit both.

  - grafana-dashboards-trino-queries.yaml  "Trino queries" (list) and "Trino query detail"
  - grafana-dashboards-nifi.yaml           "NiFi authorization" (panel shapes from the Airflow dashboard)
  - grafana-dashboards-overview.yaml       "Stackable Data Platform", the home dashboard
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


if __name__ == "__main__":
    trino_queries()
    nifi_authorization()
    branding_and_overview()
