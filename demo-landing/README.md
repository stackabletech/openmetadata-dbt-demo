# demo-landing

Tiny Rust web server that renders a markdown landing page and expands
`{{ nodeport "svc-name" }}` placeholders by querying the Kubernetes API
for the Service's nodePort and a node IP.

See `docs/superpowers/specs/2026-04-24-demo-landing-design.md` in the
parent repo for design details.

## Local run

    cargo run

Reads config from env:

| Var            | Default            |
|----------------|--------------------|
| `CONTENT_DIR`  | `/content`         |
| `LISTEN_ADDR`  | `0.0.0.0:8080`     |
| `LOG_LEVEL`    | `info`             |

Requires a reachable Kubernetes cluster (in-cluster config, or a local
`~/.kube/config` with read access to `services` and `nodes`).
