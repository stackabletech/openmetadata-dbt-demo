---
name: name-push-target
description: "Always say whether \"push\" means GitHub or the in-cluster Forgejo for the openmetadata-dbt-demo repo"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f5d9b207-07a9-49de-aa80-04af7520e78d
  modified: 2026-10-05T11:14:55.591Z
---

Never say just "push" for the openmetadata-dbt-demo repo. Always name the target: GitHub (github.com/stackabletech/openmetadata-dbt-demo) or the in-cluster Forgejo (stackable/openmetadata-dbt-demo, what ArgoCD syncs from).

**Why:** There are two remotes with different effects. Pushing to Forgejo rolls out to the running cluster (ArgoCD auto-sync). A fresh deploy copies the repo from GitHub once, so only a GitHub push reaches new clusters. Saying just "push" left it unclear which one was meant.

**How to apply:** In explanations and command snippets, write "push to GitHub" / "push to Forgejo" and use explicit remote URLs or clearly named remotes. Related: [[openmetadata-dbt-demo-repo-flow]]
