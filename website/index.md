# Stackable Data Platform Demo

Welcome. This cluster is running the full Stackable Data Platform demo — a
GitOps-managed data lakehouse with OpenMetadata, Airflow, Trino, dbt, and
a handful of supporting components.

## Access

| Service | URL |
|---|---|
| **ArgoCD** | <https://{{ nodeport "argocd-server-nodeport" }}/applications> |
| **Forgejo** | <http://{{ nodeport "forgejo-http-nodeport" }}/> |
| **OpenMetadata** | <http://{{ nodeport "openmetadata-nodeport" }}/> |

ArgoCD uses a self-signed cert; accept the browser warning on first visit.

## Default credentials

| Service | Username | Password |
|---|---|---|
| ArgoCD | `admin` | `adminadmin` |
| Forgejo | `stackable` | `stackable` |
| OpenMetadata | `admin@open-metadata.org` | `admin` |

## What to look at

1. In **OpenMetadata**, open the Trino service and explore the
   `hive-iceberg.demo.*` tables — they're dbt-built marts with per-column
   descriptions and dbt test lineage.
2. In **ArgoCD**, watch the continuously-reconciled application tree.
3. In **Forgejo**, browse the in-cluster git mirror of this repository.
