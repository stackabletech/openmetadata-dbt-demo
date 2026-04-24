# Stackable Data Platform Demo

Welcome. This cluster is running the full Stackable Data Platform demo — a
GitOps-managed data lakehouse with OpenMetadata, Airflow, Trino, dbt, and
a handful of supporting components.

## Access

| Service | URL | Username | Password |
|---|---|---|---|
| **ArgoCD** | [https://{{ nodeport "argocd-server-nodeport" }}/applications](https://{{ nodeport "argocd-server-nodeport" }}/applications) | `admin` | `adminadmin` |
| **Forgejo** | [http://{{ nodeport "forgejo-http-nodeport" }}/](http://{{ nodeport "forgejo-http-nodeport" }}/) | `stackable` | `stackable` |
| **OpenMetadata** | [http://{{ nodeport "openmetadata-nodeport" }}/](http://{{ nodeport "openmetadata-nodeport" }}/) | `admin@open-metadata.org` | `admin` |
| **Airflow** | [http://{{ nodeport "airflow-webserver" }}/](http://{{ nodeport "airflow-webserver" }}/) | `admin` | `admin` |
| **Trino** | [https://{{ nodeport "trino-coordinator" }}/](https://{{ nodeport "trino-coordinator" }}/) | `admin` | *(none)* |
| **Superset** | [http://{{ nodeport "simple-superset-node" }}/](http://{{ nodeport "simple-superset-node" }}/) | — | — |
| **OpenSearch** | [https://{{ nodeport "simple-opensearch" }}/](https://{{ nodeport "simple-opensearch" }}/) | — | — |
| **NiFi** | [https://{{ nodeport "nifi-node" }}/](https://{{ nodeport "nifi-node" }}/) | *(see sealed secret)* | *(see sealed secret)* |
| **HDFS** namenode-0 | [https://{{ nodeport "listener-simple-hdfs-namenode-default-0" }}/](https://{{ nodeport "listener-simple-hdfs-namenode-default-0" }}/) | — | — |
| **HDFS** namenode-1 | [https://{{ nodeport "listener-simple-hdfs-namenode-default-1" }}/](https://{{ nodeport "listener-simple-hdfs-namenode-default-1" }}/) | — | — |
| **HDFS** datanode-0 | [https://{{ nodeport "simple-hdfs-datanode-default-0-listener" }}/](https://{{ nodeport "simple-hdfs-datanode-default-0-listener" }}/) | — | — |

Most Stackable-managed services run with a self-signed certificate; accept
the browser warning on first visit.

## What to look at

1. In **OpenMetadata**, open the Trino service and explore the
   `hive-iceberg.demo.*` tables — they're dbt-built marts with per-column
   descriptions and dbt test lineage.
2. In **ArgoCD**, watch the continuously-reconciled application tree.
3. In **Forgejo**, browse the in-cluster git mirror of this repository.
