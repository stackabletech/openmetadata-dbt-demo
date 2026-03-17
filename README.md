# Stackable Data Platform Demo

> [!WARNING]
> This repository is not meant for the general public, it is public because it may be helpful to some people and definitely serves an instructional purpose, but especially the justfile recipes and scripts in here can cause harm and delete data if not used with caution! 


A GitOps-managed Kubernetes demo that showcases the [Stackable Data Platform](https://stackable.tech/) alongside commonly used tools from the wider data ecosystem: OpenMetadata, dbt, Astronomer Cosmos, Lakekeeper, GarageFS, Kafka, and NiFi.

The demo deploys a complete data lakehouse on Kubernetes, with TPC-H sample data flowing through dbt models in Trino, Iceberg tables managed by Lakekeeper, S3-compatible storage via GarageFS, and metadata governance through OpenMetadata — all orchestrated by Airflow and continuously deployed via ArgoCD.

> [!IMPORTANT]
> This demo currently only runs on AKS (Azure Kubernetes Service) due to hardcoded storage class requirements. See [AKS Limitations](#aks-limitations) for details.

## Architecture

```
stackablectl (just deploy)
  └─> installs ArgoCD + bootstrap apps from infrastructure/stack.yaml
        └─> ArgoCD deploys infrastructure/ (Forgejo, SealedSecrets, operators)
              └─> Forgejo mirrors this GitHub repo into the cluster
              └─> cluster-apps.yaml watches platform/applications/ (app-of-apps pattern)
                    └─> each Application in platform/applications/ deploys its manifests
```

All changes flow through Git. ArgoCD has `selfHeal: true` enabled, so any manual changes applied directly to the cluster are reverted automatically.

## How you can use it

### Prerequisites

- An Azure subscription (for AKS provisioning)
- [`az`](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) CLI, logged in
- [OpenTofu](https://opentofu.org/) (or Terraform)
- [`stackablectl`](https://docs.stackable.tech/home/stable/stackablectl/) installed
- `kubectl`
- [`just`](https://just.systems/) (optional, but recommended)

### Provision AKS Cluster

The `tofu/` directory contains OpenTofu configuration to create an AKS cluster with all required networking.

1. Copy the template and fill in your values:

```bash
cp tofu/terraform.tfvars.template tofu/terraform.tfvars
# Edit tofu/terraform.tfvars with your name, subscription ID, and owner
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `name` | yes | — | Name for the resource group and AKS cluster |
| `subscription_id` | yes | — | Azure subscription ID |
| `owner` | yes | — | Owner tag on the resource group and cluster |
| `location` | no | `westeurope` | Azure region |
| `node_count` | no | `3` | Number of nodes in the user pool |
| `node_vm_size` | no | `Standard_D8ds_v5` | VM size for user pool nodes |
| `kubernetes_version` | no | `1.33` | Kubernetes version |

2. Provision the cluster (each cluster gets its own state file `tofu/<name>.tfstate`):

```bash
just infra my-cluster-name
```

3. Get the kubeconfig:

```bash
just kubeconfig my-cluster-name   # by name
just kubeconfig                   # interactive selection from managed clusters
```

The infrastructure creates a resource group, VNet, subnet, NSG (with all inbound traffic allowed), and an AKS cluster with a system node pool and a configurable user node pool with public IPs.

### Deploy the Platform

Once `kubectl` points at your cluster:

```bash
just deploy
```

Or run everything end-to-end:

```bash
just demo my-cluster-name     # provisions infra, gets kubeconfig, deploys platform
```

This bootstraps ArgoCD, which then takes over and deploys everything else from Git. The full platform takes several minutes to come up as components start in dependency order.

### Tear Down

```bash
just destroy my-cluster-name  # destroy by name (async, returns immediately)
just destroy                  # interactive selection from managed clusters
```


### Access Points

After deployment, these services are accessible (via NodePort or LoadBalancer depending on your cluster):

| Service | Default Credentials |
|---------|-------------------|
| ArgoCD | `admin` / `adminadmin` |
| Forgejo | `stackable` / `stackable` |
| Airflow Webserver | `admin` / `admin` |
| OpenMetadata | `admin@open-metadata.org` / `admin` |
| Trino | `admin` (no password, HTTPS) |
| NiFi | See sealed secret |

### Other Commands

```bash
just demo <name>         # End-to-end: infra + kubeconfig + deploy
just infra <name>        # Provision AKS cluster (state: tofu/<name>.tfstate)
just kubeconfig [name]   # Get kubeconfig (interactive selection if no name)
just destroy [name]      # Tear down cluster (async, interactive if no name)
just seal-secrets        # Re-seal plaintext secrets from secrets/ into platform/manifests/
just build-airflow-image # Build and push custom Airflow image with Cosmos
just dbt-compile         # Compile the dbt project locally
just dbt-run             # Run dbt models locally (requires Trino access)
```

## Components

### Ecosystem Integrations

| Component | Description |
|-----------|-------------|
| **[OpenMetadata](https://open-metadata.org/)** | Data catalog and metadata governance |
| **[dbt Core](https://www.getdbt.com/)** | Data transformation framework (TPC-H models) |
| **[Astronomer Cosmos](https://astronomer.github.io/astronomer-cosmos/)** | dbt orchestration in Airflow |
| **[Lakekeeper](https://lakekeeper.io/)** | Apache Iceberg REST catalog |
| **[GarageFS](https://garagehq.deuxfleurs.fr/)** | S3-compatible distributed object storage |
| **[ArgoCD](https://argo-cd.readthedocs.io/)** | GitOps continuous deployment |
| **[Forgejo](https://forgejo.org/)** | In-cluster Git server (mirrors this repo) |
| **[SealedSecrets](https://sealed-secrets.netlify.app/)** | Encrypted secrets in Git |

### Trino Catalogs

| Catalog | Connector | Metastore | Storage |
|---------|-----------|-----------|---------|
| `tpch` | TPC-H | Built-in | In-memory |
| `tpcds` | TPC-DS | Built-in | In-memory |
| `hive` | Hive | Hive Metastore (PostgreSQL) | HDFS |
| `hive-iceberg` | Iceberg | Hive Metastore (PostgreSQL) | HDFS |
| `lakekeeper-iceberg` | Iceberg (REST) | Lakekeeper | GarageFS (S3) |

### dbt Project (TPC-H)

The included dbt project transforms TPC-H sample data through a staging → marts pattern:

- **Staging models** (views): 8 models wrapping TPC-H source tables
- **Mart models** (tables): Business-level aggregations — order summaries, supplier performance, revenue by region, customer lifetime value, shipping analysis, part pricing

The Airflow DAG (`dbt_tpch_demo`) orchestrated by Cosmos runs daily:
1. Executes all dbt models against Trino
2. Uploads dbt artifacts (manifest, catalog, run results) to GarageFS
3. Triggers OpenMetadata dbt ingestion to import lineage and documentation



## More Topics

### Making Changes

Since ArgoCD manages the cluster, all changes should be committed to Git:

1. Push changes to the in-cluster Forgejo repository (automatically mirrored from GitHub)
2. ArgoCD detects changes and syncs
3. Manual `kubectl apply` commands are reverted by ArgoCD's self-heal

To add a new component, see the patterns documented in [CLAUDE.md](CLAUDE.md).

### AKS Limitations

This demo currently only runs on Azure Kubernetes Service (AKS), due to the requirement for `ReadWriteMany` PVCs for Airflow logs and DAGs. 
The default AKS StorageClass (`disk.csi.azure.com`) only supports `ReadWriteOnce`, so these are hardcoded to an AKS storageclass that supports `ReadWriteMany`.
We plan to fix this at some point, but have not yet gotten around to it.

The `openmetadata-dependencies` application is configured to use `azurefile-csi-premium` for these PVCs. If your AKS cluster does not have this StorageClass, you need to either:
- Create an Azure Files StorageClass that supports `ReadWriteMany`
- Disable the bundled Airflow in the OpenMetadata dependencies chart

## Directory Structure

```
infrastructure/                    # Bootstrap: deployed by stackablectl
├── stack.yaml                     # stackablectl entry point
├── project.yaml                   # ArgoCD AppProject
├── cluster-apps.yaml              # App-of-apps: watches platform/applications/
├── forgejo.yaml                   # Forgejo deployment (mirrors this repo)
├── sealed-secrets.yaml            # SealedSecrets controller
├── forgejo-manifests/             # Forgejo admin secret + configure job
└── sealed-secrets-manifests/      # SealedSecrets decryption key

platform/                          # Everything ArgoCD manages after bootstrap
├── applications/                  # ArgoCD Application definitions (one per component)
└── manifests/                     # Kubernetes manifests grouped by component
    ├── airflow/                   # AirflowCluster CRD
    ├── trino/                     # TrinoCluster + TrinoCatalog CRDs
    ├── trino-init/                # SQL init job (Kustomize overlay)
    ├── garagefs-init/             # GarageFS bucket/key provisioning
    ├── lakekeeper-init/           # Lakekeeper bootstrap + warehouse creation
    ├── openmetadata-init/         # OpenMetadata service + pipeline registration
    ├── openmetadata/              # OpenMetadata sealed secrets
    ├── openmetadata-dependencies/ # Airflow DB migration job
    └── ...                        # HDFS, Hive, Kafka, NiFi, ZooKeeper, etc.

secrets/                           # Plaintext secrets (source of truth for sealing)
└── manifests/
    └── <component>/
        └── <secret-name>.yaml

dags/                              # Airflow DAGs (git-synced into pods)
├── tpch_dbt_dag.py                # Cosmos DAG: dbt + artifact upload + OM trigger
├── test_dag.py                    # Simple test DAG
└── dbt/tpch_demo/                 # dbt project
    ├── dbt_project.yml
    ├── profiles.yml               # Trino connection config
    └── models/
        ├── staging/               # TPC-H source wrappers (views)
        └── marts/                 # Business aggregations (tables)

docker/airflow/                    # Custom Airflow image with Cosmos
└── Dockerfile

tofu/                              # OpenTofu infrastructure (AKS cluster)
├── main.tf                        # Resource group, VNet, NSG, AKS cluster + node pools
├── terraform.tfvars.template      # Template for variable values
└── terraform.tfvars               # Actual values (gitignored)
```

## Secrets Management

Secrets follow a two-step flow:

1. Store plaintext secrets in `secrets/manifests/<component>/<name>.yaml`
2. Run `just seal-secrets` to encrypt them into `platform/manifests/<component>/sealed-<name>.yaml`

Never edit `sealed-*` files by hand. Some secrets (GarageFS credentials) are generated at runtime by init jobs.

