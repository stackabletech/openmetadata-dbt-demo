# Stackable Data Platform Demo

Welcome. This cluster is running the full Stackable Data Platform demo — a
GitOps-managed data lakehouse with OpenMetadata, Airflow, Trino, dbt, and
a handful of supporting components.

## Platform Deployment 

| Service | URL | Username | Password | Enabled                                                                                      |
| --- | --- | --- | --- |----------------------------------------------------------------------------------------------|
| **ArgoCD** | [https://{{ nodeport "argocd-server-nodeport" }}/applications](https://{{ nodeport "argocd-server-nodeport" }}/applications) | `admin` | `adminadmin` | —                                                                                            |
| **Forgejo** | [http://{{ nodeport "forgejo-http-nodeport" }}/](http://{{ nodeport "forgejo-http-nodeport" }}/) | `stackable` | `stackable` | —                                                                                            |


## Stackable Components
| Service | URL | Username | Password | Enabled                                                                                      |
| --- | --- | --- | --- |----------------------------------------------------------------------------------------------|
| **Airflow** | [http://{{ nodeport "airflow-webserver" }}/](http://{{ nodeport "airflow-webserver" }}/) | `admin` | `admin` | {{ toggle "platform/manifests/airflow/airflow.yaml" "spec.clusterOperation.stopped" }}       |
| **Trino** | [https://{{ nodeport "trino-coordinator" }}/](https://{{ nodeport "trino-coordinator" }}/) | `admin` | *(none)* | {{ toggle "platform/manifests/trino/trino.yaml" "spec.clusterOperation.stopped" }}           |
| **Superset** | [http://{{ nodeport "simple-superset-node" }}/](http://{{ nodeport "simple-superset-node" }}/) | — | — | {{ toggle "platform/manifests/superset/superset.yaml" "spec.clusterOperation.stopped" }}     |
| **OpenSearch** | [https://{{ nodeport "simple-opensearch" }}/](https://{{ nodeport "simple-opensearch" }}/) | — | — | {{ toggle "platform/manifests/opensearch/opensearch.yaml" "spec.clusterOperation.stopped" }} |
| **NiFi** | [https://{{ nodeport "nifi-node" }}/](https://{{ nodeport "nifi-node" }}/) | `admin` | `admin` | {{ toggle "platform/manifests/nifi/nifi.yaml" "spec.clusterOperation.stopped" }}             |
| **Kafka** | — | — | — | {{ toggle "platform/manifests/kafka/kafka.yaml" "spec.clusterOperation.stopped" }}           |
| **HDFS** namenode-0 | [http://{{ nodeport "listener-simple-hdfs-namenode-default-0" }}/](http://{{ nodeport "listener-simple-hdfs-namenode-default-0" }}/) | — | — | {{ toggle "platform/manifests/hdfs/hdfs.yaml" "spec.clusterOperation.stopped" }}             |
| **HDFS** namenode-1 | [http://{{ nodeport "listener-simple-hdfs-namenode-default-1" }}/](http://{{ nodeport "listener-simple-hdfs-namenode-default-1" }}/) | — | — | —                                                                                            |
| **HDFS** datanode-0 | [http://{{ nodeport "simple-hdfs-datanode-default-0-listener" }}/](http://{{ nodeport "simple-hdfs-datanode-default-0-listener" }}/) | — | — | —                                                                                            |

## External Componens
| Service                   | URL | Username | Password | Enabled                                                                                      |
|---------------------------| --- | --- | --- |----------------------------------------------------------------------------------------------|
| **OpenMetadata**          | [http://{{ nodeport "openmetadata-nodeport" }}/](http://{{ nodeport "openmetadata-nodeport" }}/) | `admin@open-metadata.org` | `admin` | —                                                                                            |
| **LakeKeeper**            | [http://{{ nodeport "lakekeeper" }}/ui/](http://{{ nodeport "lakekeeper" }}/ui/) | — | — | —                                                                                            |
| **OpenSearch Dashboards** | [http://{{ nodeport "opensearch-dashboards-nodeport" }}/](http://{{ nodeport "opensearch-dashboards-nodeport" }}/) | `admin@open-metadata.org` | `admin` | —                                                                                            |

Most Stackable-managed services run with a self-signed certificate; accept
the browser warning on first visit.

## What to look at

1. In **OpenMetadata**, open the Trino service and explore the
   `hive-iceberg.demo.*` tables — they're dbt-built marts with per-column
   descriptions and dbt test lineage.
2. In **ArgoCD**, watch the continuously-reconciled application tree.
3. In **Forgejo**, browse the in-cluster git mirror of this repository.

## Want to go deeper?

<div class="cta">

Questions about the platform, a specific use case, or want a guided
walkthrough of how Stackable fits your environment? We'd love to talk.

<a class="btn" href="https://zeeg.me/stackable/meet-stackable-mlsj">Contact us</a>

</div>
