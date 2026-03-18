# Organize ArgoCD Applications with Labels + PostgreSQL ApplicationSet

## Context

The repo has 22 ArgoCD Applications in a flat `platform/applications/` directory with no labels or grouping. This makes the ArgoCD UI hard to navigate. Additionally, 4 PostgreSQL Application files are nearly identical (only differing in names, credentials, and an optional `extendedConfiguration`).

Three approaches were evaluated:
1. **Labels** — add category/tier labels for ArgoCD UI filtering
2. **Directory grouping** — split into subdirectories with multiple app-of-apps
3. **ApplicationSet** — deduplicate the 4 PostgreSQL apps

**Directory grouping (approach 2) is NOT recommended** because sync-waves only work within a single parent Application. Splitting apps across subdirectories would break the existing wave ordering (e.g., `trino` wave 1 → `trino-init` wave 2 → `openmetadata-init` wave 3).

**Recommended: Labels + ApplicationSet (approaches 1 + 3 combined).**

## Step 1: Add labels to all Application manifests

Add `metadata.labels` to each Application in `platform/applications/`. Label scheme:

```yaml
metadata:
  labels:
    app.kubernetes.io/part-of: dbt-openmetadata-demo
    platform.demo/category: <category>
    platform.demo/tier: <tier>
```

| Application | category | tier |
|---|---|---|
| airflow | stackable-workload | data-platform |
| hive | stackable-workload | data-platform |
| hdfs | stackable-workload | data-platform |
| zookeeper | stackable-workload | data-platform |
| hive-iceberg | stackable-workload | data-platform |
| kafka | stackable-workload | data-platform |
| nifi | stackable-workload | data-platform |
| trino | stackable-workload | data-platform |
| airflow-init | init-job | data-platform |
| trino-init | init-job | data-platform |
| garagefs-init | init-job | storage |
| lakekeeper-init | init-job | data-platform |
| openmetadata-init | init-job | metadata |
| openmetadata-dependencies | external-service | metadata |
| openmetadata | external-service | metadata |
| lakekeeper | external-service | data-platform |
| garage | external-service | storage |
| superset | external-service | visualization |

Also fix `garagefs.yaml` to use `project: dbt-openmetadata-demo` instead of `project: default`.

## Step 2: Replace 4 PostgreSQL apps with 1 ApplicationSet

Create `platform/applications/postgresql-instances.yaml` — an ApplicationSet using a list generator. Delete `airflow-postgres.yaml`, `hive-postgres.yaml`, `hive-iceberg-postgres.yaml`, `superset-postgres.yaml`.

The 4 current postgres apps differ only in:

| Field | airflow | hive | hive-iceberg | superset |
|---|---|---|---|---|
| releaseName | postgresql-airflow | postgresql-hive | postgresql-hive-iceberg | postgresql-superset |
| auth.database | airflow | hive | hiveiceberg | superset |
| auth.username | airflow | hive | hiveiceberg | superset |
| auth.existingSecret | postgresql-credentials | hive-postgresql-credentials | hive-iceberg-postgresql-credentials | superset-postgresql-credentials |
| extendedConfiguration | (empty) | password_encryption=md5 | password_encryption=md5 | (empty) |
| manifests path | airflow-postgres/ | hive-postgres/ | hive-iceberg-postgres/ | superset-postgres/ |

Example ApplicationSet:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: postgresql-instances
spec:
  generators:
    - list:
        elements:
          - component: airflow
            database: airflow
            username: airflow
            secretName: postgresql-credentials
            extendedConfig: ""
          - component: hive
            database: hive
            username: hive
            secretName: hive-postgresql-credentials
            extendedConfig: "password_encryption=md5"
          - component: hive-iceberg
            database: hiveiceberg
            username: hiveiceberg
            secretName: hive-iceberg-postgresql-credentials
            extendedConfig: "password_encryption=md5"
          - component: superset
            database: superset
            username: superset
            secretName: superset-postgresql-credentials
            extendedConfig: ""
  template:
    metadata:
      name: "{{component}}-postgres"
      labels:
        app.kubernetes.io/part-of: dbt-openmetadata-demo
        platform.demo/category: database
        platform.demo/tier: infrastructure
    spec:
      project: dbt-openmetadata-demo
      destination:
        server: https://kubernetes.default.svc
        namespace: default
      sources:
        - repoURL: "registry-1.docker.io/bitnamicharts"
          chart: postgresql
          targetRevision: "16.6.3" # renovate: registryUrl=https://registry-1.docker.io/bitnamicharts datasource=helm depName=postgresql
          helm:
            releaseName: "postgresql-{{component}}"
            valuesObject:
              global:
                security:
                  allowInsecureImages: true
              image:
                repository: bitnamilegacy/postgresql
              volumePermissions:
                image:
                  repository: bitnamilegacy/os-shell
              metrics:
                image:
                  repository: bitnamilegacy/postgres-exporter
              commonLabels:
                stackable.tech/vendor: Stackable
              auth:
                database: "{{database}}"
                username: "{{username}}"
                existingSecret: "{{secretName}}"
              primary:
                extendedConfiguration: "{{extendedConfig}}"
        - repoURL: "http://forgejo-http:3000/stackable/openmetadata-dbt-demo.git"
          targetRevision: "main"
          path: "platform/manifests/{{component}}-postgres/"
      syncPolicy:
        syncOptions:
          - CreateNamespace=true
        automated:
          selfHeal: true
          prune: true
```

## Why not directory grouping?

Sync-waves only work among resources managed by the **same parent Application**. Currently all 22 apps are children of `cluster-apps`. If split into subdirectories with separate app-of-apps parents, wave ordering between groups is lost. For example, `trino` (wave 1) and `trino-init` (wave 2) would become independent if placed under different parents.

## Result

- 22 files → 19 files (18 Applications + 1 ApplicationSet)
- All apps labeled for ArgoCD UI filtering
- Chart version maintained in a single place for all PostgreSQL instances
- Zero sync-wave impact