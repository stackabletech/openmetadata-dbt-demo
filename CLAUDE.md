# Repository Guide

This is a GitOps-managed Kubernetes demo deploying a data platform (Trino, Hive, Airflow, HDFS, etc.) using Stackable operators and ArgoCD.

## Architecture

```
stackablectl (just deploy)
  └─> installs ArgoCD + bootstrap apps from infrastructure/stack.yaml
        └─> ArgoCD deploys infrastructure/ (Forgejo, SealedSecrets, project, cluster-apps)
              └─> Forgejo mirrors this GitHub repo into the cluster
              └─> cluster-apps.yaml watches platform/applications/ (app-of-apps pattern)
                    └─> each Application in platform/applications/ deploys its manifests
```

## Directory Structure

```
infrastructure/                    # Bootstrap: deployed by stackablectl, sets up ArgoCD + Forgejo + SealedSecrets
├── stack.yaml                     # stackablectl stack definition (entry point for `just deploy`)
├── project.yaml                   # ArgoCD AppProject definition
├── cluster-apps.yaml              # App-of-apps: watches platform/applications/
├── forgejo.yaml                   # ArgoCD Application for Forgejo (Helm + manifests)
├── sealed-secrets.yaml            # ArgoCD Application for SealedSecrets (Helm + manifests)
├── forgejo-manifests/             # Supporting manifests for Forgejo (admin secret, configure job)
└── sealed-secrets-manifests/      # SealedSecrets decryption key

platform/                          # Everything ArgoCD manages after bootstrap
├── applications/                  # ArgoCD Application definitions (one per component)
│   ├── <component>.yaml           # Points to manifests dir and/or Helm chart
│   └── ...
└── manifests/                     # Kubernetes manifests grouped by component
    ├── <component>/
    │   ├── <resource>.yaml        # Stackable CRDs, TrinoCatalogs, ZookeeperZnodes, etc.
    │   └── sealed-*.yaml          # SealedSecrets (generated, do not edit by hand)
    └── ...

secrets/                           # Plaintext Kubernetes Secrets (source of truth for sealed secrets)
└── manifests/
    └── <component>/
        └── <secret-name>.yaml     # Plain Secret, mirrors structure of platform/manifests/

dags/                              # Airflow DAG files (git-synced into Airflow pods)

justfile                           # Task runner (just deploy, just seal-secrets)
```

## Golden Rule

Never apply changes directly to the Kubernetes cluster (no `kubectl apply`, `helm install`, etc.). All changes must be committed to Git and deployed through ArgoCD. ArgoCD has `selfHeal: true` enabled, so any manual changes will be reverted automatically.

## How to Add a New Component

### 1. Stackable operator resource (e.g. new HiveCluster, TrinoCluster)

1. Create `platform/manifests/<component>/` with the CRD yaml files.
2. Create `platform/applications/<component>.yaml` — an ArgoCD Application pointing to the manifests dir:
   ```yaml
   source:
     repoURL: "http://forgejo-http:3000/stackable/openmetadata-dbt-demo.git"
     targetRevision: "main"
     path: platform/manifests/<component>/
   ```
3. Commit and push. ArgoCD picks it up automatically via cluster-apps.

### 2. External Helm chart (e.g. Lakekeeper, GarageFS)

1. Create `platform/applications/<component>.yaml` with a Helm source:
   ```yaml
   sources:
     - repoURL: "https://<helm-repo-url>"
       chart: <chart-name>
       targetRevision: "<version>"    # optional but recommended
       helm:
         releaseName: <component>
   ```
2. Do NOT set `path:` when using `chart:` — they are mutually exclusive.

### 3. Helm chart + secrets/manifests (e.g. PostgreSQL instances)

Use multi-source — one source for the Helm chart, one for the manifests dir:
```yaml
sources:
  - repoURL: "registry-1.docker.io/bitnamicharts"
    chart: postgresql
    targetRevision: 16.6.3
    helm:
      releaseName: postgresql-<component>
      valuesObject:
        auth:
          existingSecret: <secret-name>
  - repoURL: "http://forgejo-http:3000/stackable/openmetadata-dbt-demo.git"
    targetRevision: "main"
    path: platform/manifests/<component>/
```

### 4. Adding secrets

1. Create the plaintext Secret in `secrets/manifests/<component>/<secret-name>.yaml`.
2. Run `just seal-secrets` — this generates `platform/manifests/<component>/sealed-<secret-name>.yaml`.
3. Never edit sealed-* files by hand. Always edit the plaintext in `secrets/` and re-seal.

## Common ArgoCD Application Fields

All applications should use:
- `project: dbt-openmetadata-demo` (except infrastructure-level apps which use `default`)
- `destination.server: https://kubernetes.default.svc`
- `destination.namespace: default`
- `syncPolicy.automated.selfHeal: true` and `prune: true`
- `syncPolicy.syncOptions: [CreateNamespace=true]`

## Commands

- `just deploy` — bootstrap the cluster with stackablectl (installs ArgoCD, Forgejo, then ArgoCD takes over)
- `just seal-secrets` — re-seal all plaintext secrets from `secrets/` into `platform/manifests/`