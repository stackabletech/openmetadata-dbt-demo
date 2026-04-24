# Design: `demo-landing` — Kubernetes-aware landing page

**Date:** 2026-04-24

## Goal

A small Rust web server, deployed into the demo cluster, that renders a single-page markdown landing page describing what the demo is and how to access it. Service URLs inside the markdown are written as template placeholders (`{{ nodeport "svc-name" }}`) that the server expands at request time by querying the Kubernetes API for the Service's `nodePort` and a node IP. Content lives in the repo and reaches the pod via a `git-sync` sidecar, matching how Airflow consumes DAGs in this cluster.

The tool is designed to be moved into its own OSS repo later. This repo will then only carry (a) the markdown content, (b) the Kubernetes manifests that deploy the published image.

## Non-goals

- Multi-page static site or a full documentation portal (single page only).
- Authentication, login, or role-aware views.
- Template features beyond the single `nodeport` helper (no loops, conditionals, nested partials).
- Content caching with live invalidation (per-request render is fast enough).
- A template-override mechanism (`_layout.html`, `_styles.css`) in MVP — see "Future extensions" below.
- AsciiDoc or any non-markdown content format.
- Running the tool outside Kubernetes (it requires kube API access, either in-cluster or via local kubeconfig for development).

## Approach summary

- **Rendering model:** per-request read-and-render. Each `GET /` reads `index.md` from the content dir, runs template substitution against the Kubernetes API, parses markdown to HTML, wraps in an embedded HTML shell, and returns the result. No caching; no file watchers; no Kubernetes informers.
- **Content delivery:** git-sync sidecar pulls the repo into a shared `emptyDir`. The Rust container reads the mounted directory.
- **Template engine:** `minijinja` — Jinja-like syntax matches the `{{ nodeport "..." }}` notation and keeps the door open for future layout overrides without a refactor.
- **Markdown engine:** `pulldown-cmark`. CommonMark, fast, permits inline HTML as an escape hatch.
- **Kubernetes access:** `kube` + `k8s-openapi`, in-cluster config by default, falling back to local kubeconfig.
- **Styling:** one embedded CSS file served at `/styles.css`; the HTML shell pulls it via `<link>`.

## Components / module layout

```
demo-landing/
├── Cargo.toml
├── Dockerfile                         # multi-stage; runtime distroless/cc
├── README.md
├── src/
│   ├── main.rs                        # config from env, axum bootstrap
│   ├── kube.rs                        # lookup_service, pick_node_ip
│   ├── template.rs                    # minijinja env + nodeport helper
│   ├── render.rs                      # markdown -> HTML -> layout wrap
│   └── routes.rs                      # axum handlers
└── assets/
    ├── layout.html                    # include_str! at compile time
    └── styles.css                     # include_str! at compile time
```

Module responsibilities and interfaces:

- `kube.rs` exposes:
  - `lookup_service(client: &kube::Client, name: &str) -> Result<ServiceInfo, LookupError>`
  - `pick_node_ip(client: &kube::Client) -> Result<String, LookupError>`
  - `ServiceInfo { namespace: String, node_port: u16 }` — first port by spec order when multi-port.
  - `LookupError` enum covers: `NotFound`, `Ambiguous(Vec<String>)`, `NotNodePort`, `NoNodePortOnPort`, `KubeApi(kube::Error)`, `NoReadyNode`.
  - Defining a narrow trait `ServiceLookup` with these two methods lets `template.rs` accept a mockable dependency in tests.
- `template.rs` builds a `minijinja::Environment` and registers `nodeport` as a function closing over an `Arc<dyn ServiceLookup>`. A per-request node-IP cache lives on the per-request state so multiple helper invocations on one page trigger a single `nodes` list call.
- `render.rs` is pure given its inputs: markdown source, minijinja env, layout template → `Result<String, RenderError>`. Testable without Kubernetes.
- `routes.rs` holds axum handlers and delegates business logic.

## Config surface

Environment variables (CLI overrides via `--<flag>` for local dev):

| Name | Default | Meaning |
|---|---|---|
| `CONTENT_DIR` | `/content` | Root for `index.md` and `images/` |
| `LISTEN_ADDR` | `0.0.0.0:8080` | axum bind address |
| `LOG_LEVEL` | `info` | `tracing_subscriber` filter |

No namespace filter in MVP — RBAC bounds what the pod can see (see "Deployment / RBAC").

## Routes

| Method + path | Response |
|---|---|
| `GET /` | Rendered `index.md` wrapped in layout. `Content-Type: text/html; charset=utf-8`. |
| `GET /styles.css` | Embedded CSS. `Content-Type: text/css`. |
| `GET /images/{path...}` | Static file from `CONTENT_DIR/images/`. Content-Type via `mime_guess`. Path-traversal-guarded (canonicalize + prefix check). |
| `GET /healthz` | `200` with body `ok`. Not gated on kube API reachability. |

## Template helper: `nodeport`

### Call site

```
{{ nodeport "argocd-server-nodeport" }}
```

Returns `host:port` (no scheme, no path). The markdown author wraps with scheme/path as appropriate:

```
[ArgoCD](https://{{ nodeport "argocd-server-nodeport" }}/applications)
```

### Service lookup rules

- Search all namespaces the ServiceAccount can see.
- Exactly one match → use it.
- Multiple namespaces with a Service of that name → render error comment, log `warn` with the ambiguous namespaces.
- No match → render error comment.

### Port selection

- Reject Services whose `spec.type` is not `NodePort`.
- One port → use its `nodePort`.
- Multiple ports → first port in `spec.ports[]` order.
- Chosen port has no `nodePort` set → render error comment.

### Node IP selection

Computed once per request and reused for every `nodeport` invocation on that request.

- List Nodes.
- Filter to nodes with `Ready=True`.
- For each candidate, prefer `ExternalIP`; fall back to `InternalIP`.
- Use the first candidate in list order.
- If no candidate → render error comment.

### Error rendering

On any failure path, the helper returns an HTML comment at the call site:

```
<!-- nodeport error: <reason> -->
```

Failure also emits a `tracing::warn!` with structured fields (service name, namespace, reason). The rest of the page renders normally.

### Worked example

On the demo cluster, `argocd-server-nodeport` has `nodePort: 30080` in the `deployment` namespace; the first Ready node reports `ExternalIP 74.234.12.5`. The expansion is:

```
{{ nodeport "argocd-server-nodeport" }}  -->  74.234.12.5:30080
```

Surrounding markdown `[ArgoCD](https://{{ nodeport "argocd-server-nodeport" }}/applications)` becomes `[ArgoCD](https://74.234.12.5:30080/applications)`.

## Content delivery

The demo content lives at `website/` in this repo:

```
website/
├── index.md
└── images/
    └── <screenshots, diagrams added later>
```

A `git-sync` sidecar in the same pod as the Rust tool pulls the repo into a shared `emptyDir` mounted at `/git`. The Rust container sees the content at `/git/current/website/`, which is what `CONTENT_DIR` points at.

git-sync args:
- `--repo=http://forgejo-http.deployment.svc.cluster.local:3000/stackable/openmetadata-dbt-demo.git`
- `--ref=main`
- `--depth=1`
- `--period=30s`
- `--root=/git`
- `--link=current`

Forgejo is accessed anonymously (the demo repo is readable without credentials).

## Deployment / RBAC

**Namespace:** `deployment` — the landing page is cluster-level infrastructure alongside Forgejo/ArgoCD, not a data-platform component.

**Files (under `platform/manifests/demo-landing/`):**

1. `serviceaccount.yaml` — `ServiceAccount demo-landing`.
2. `clusterrole.yaml` — `ClusterRole demo-landing` with:
   ```yaml
   rules:
     - apiGroups: [""]
       resources: ["services"]
       verbs: ["get", "list"]
     - apiGroups: [""]
       resources: ["nodes"]
       verbs: ["get", "list"]
   ```
3. `clusterrolebinding.yaml` — binds the ClusterRole to the ServiceAccount.
4. `deployment.yaml` — one-replica Deployment. Pod labels include `app.kubernetes.io/name: demo-landing`. Two containers:

   **landing container**
   - image: `oci.stackable.tech/sandbox/demo-landing:0.1.0-dev` during development; will be flipped to the public image after the OSS split.
   - env: `CONTENT_DIR=/git/current/website`, `LISTEN_ADDR=0.0.0.0:8080`, `LOG_LEVEL=info`.
   - ports: `8080/TCP`.
   - resources: `requests: {cpu: 50m, memory: 64Mi}`, `limits: {memory: 128Mi}`.
   - liveness + readiness probes: httpGet `/healthz` on port `8080`, 10-second period.
   - volumeMounts: `/git` (shared emptyDir).

   **git-sync sidecar**
   - image: `registry.k8s.io/git-sync/git-sync:v4.3.0`.
   - args: as listed in "Content delivery".
   - volumeMounts: `/git` (shared emptyDir).
   - securityContext: uses git-sync image's documented non-root default.

   - volumes: one `emptyDir` named `git`.
   - serviceAccountName: `demo-landing`.

5. `service.yaml` — `Service demo-landing`:
   ```yaml
   spec:
     type: NodePort
     selector:
       app.kubernetes.io/name: demo-landing
     ports:
       - name: http
         port: 80
         targetPort: 8080
         nodePort: 30088
   ```

6. `platform/applications/demo-landing.yaml` — ArgoCD Application, project `dbt-openmetadata-demo`, destination namespace `deployment`, source path `platform/manifests/demo-landing/` at repo `http://forgejo-http.deployment.svc.cluster.local:3000/stackable/openmetadata-dbt-demo.git`, ref `main`, `syncPolicy.automated.selfHeal: true / prune: true`, `syncOptions: [CreateNamespace=true]`.

**Justfile recipe:**

```
build-landing-image:
    docker build -t oci.stackable.tech/sandbox/demo-landing:0.1.0-dev demo-landing
    docker push oci.stackable.tech/sandbox/demo-landing:0.1.0-dev
```

**Access after deploy:** `http://<any-node-external-ip>:30088/`.

## Error handling (summary)

| Situation | Behavior |
|---|---|
| `index.md` missing | `500` with terse HTML error naming the expected path. Log `error`. |
| Kubernetes API transient failure in a helper call | Inline `<!-- nodeport error: kube API: <reason> -->`. Rest of page renders. Log `warn`. |
| Service not found / ambiguous / wrong type / no nodePort | Inline error comment. Log `warn`. |
| No Ready Node or no usable IP | Inline error comment. Log `warn`. |
| Image path traversal (`/images/../...`) | `403 Forbidden`. Log `warn` with requested path. |
| Image missing | `404 Not Found`. |
| `/healthz` | Always `200`. Not coupled to kube API reachability. |

A broken helper never breaks the whole page. A broken kube API does not crash-loop the pod.

## Testing

**Unit (`cargo test`):**

- `render.rs` — markdown + layout rendering with a stub minijinja environment; assert on a known input/output pair.
- `template.rs` — `nodeport` helper behavior using a mock `ServiceLookup`:
  - single-match service returns `host:port`;
  - no match returns the error comment;
  - ambiguous match returns the error comment;
  - non-NodePort Service returns the error comment;
  - multi-port Service uses the first port.
- `routes.rs` image path traversal: a handful of hostile inputs (`../secrets.yaml`, encoded traversals) return `403`.

**Integration:**

- Local: `cargo run` against a sample `/tmp/content/` containing `index.md` and an `images/` dir, with `~/.kube/config` pointed at a kind cluster that has a couple of fake Services. `curl localhost:8080`.
- In-cluster: deploy via GitOps; `curl http://<node>:30088/`. Verify live-edit by pushing a markdown change, waiting ~30 s for git-sync, refreshing.

**Out of scope for MVP:**

- End-to-end tests against a dedicated test cluster.
- Load / concurrency tests.

## Future extensions (not implemented in this spec)

- `_layout.html` / `_styles.css` overrides found under the content dir are honored when present; binary's embedded versions remain the fallback.
- Named-port second arg to `nodeport` (e.g. `{{ nodeport "svc" "https" }}`) for multi-port Services.
- Explicit namespace in the helper (e.g. `{{ nodeport "svc.namespace" }}`) to disambiguate cross-namespace collisions.
- Short-TTL Kubernetes lookup cache if request volume ever justifies it.

These are all additive changes to the MVP's module boundaries; none require restructuring.

## Open items

None. This spec should be executable as-is.
