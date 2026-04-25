mod auth;
mod forgejo;
mod kube;
mod render;
mod routes;
mod template;

use async_trait::async_trait;
use axum::{routing::get, Router};
use k8s_openapi::api::core::v1::{Node, Service};
use ::kube::{api::ListParams, Api, Client};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use crate::forgejo::ForgejoClient;
use crate::kube::{LookupError, ServiceInfo, ServiceLookup};
use crate::routes::AppState;

struct KubeClientLookup {
    client: Client,
}

#[async_trait]
impl ServiceLookup for KubeClientLookup {
    async fn lookup_service(&self, name: &str) -> Result<ServiceInfo, LookupError> {
        let api: Api<Service> = Api::all(self.client.clone());
        let list = api
            .list(&ListParams::default())
            .await
            .map_err(|e| LookupError::KubeApi(e.to_string()))?;

        let matches: Vec<&Service> = list
            .iter()
            .filter(|s| s.metadata.name.as_deref() == Some(name))
            .collect();

        match matches.as_slice() {
            [] => Err(LookupError::NotFound(name.into())),
            [svc] => {
                let spec = svc
                    .spec
                    .as_ref()
                    .ok_or_else(|| LookupError::KubeApi("service missing spec".into()))?;
                let svc_type = spec.type_.as_deref().unwrap_or("ClusterIP");
                if svc_type != "NodePort" {
                    return Err(LookupError::NotNodePort(name.into()));
                }
                let first_port = spec
                    .ports
                    .as_ref()
                    .and_then(|ps| ps.first())
                    .ok_or_else(|| LookupError::NoNodePortOnPort(name.into()))?;
                let node_port = first_port
                    .node_port
                    .ok_or_else(|| LookupError::NoNodePortOnPort(name.into()))?;
                Ok(ServiceInfo {
                    namespace: svc
                        .metadata
                        .namespace
                        .clone()
                        .unwrap_or_else(|| "default".into()),
                    node_port: node_port as u16,
                })
            }
            many => Err(LookupError::Ambiguous {
                name: name.into(),
                namespaces: many
                    .iter()
                    .map(|s| {
                        s.metadata
                            .namespace
                            .clone()
                            .unwrap_or_else(|| "?".into())
                    })
                    .collect(),
            }),
        }
    }

    async fn pick_node_ip(&self) -> Result<String, LookupError> {
        let api: Api<Node> = Api::all(self.client.clone());
        let list = api
            .list(&ListParams::default())
            .await
            .map_err(|e| LookupError::KubeApi(e.to_string()))?;

        for node in list.iter() {
            // Ready=True?
            let is_ready = node
                .status
                .as_ref()
                .and_then(|s| s.conditions.as_ref())
                .map(|conds| {
                    conds
                        .iter()
                        .any(|c| c.type_ == "Ready" && c.status == "True")
                })
                .unwrap_or(false);
            if !is_ready {
                continue;
            }
            let addrs = node.status.as_ref().and_then(|s| s.addresses.as_ref());
            if let Some(addrs) = addrs {
                if let Some(ext) = addrs.iter().find(|a| a.type_ == "ExternalIP") {
                    return Ok(ext.address.clone());
                }
                if let Some(int) = addrs.iter().find(|a| a.type_ == "InternalIP") {
                    return Ok(int.address.clone());
                }
            }
        }
        Err(LookupError::NoReadyNode)
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_env("LOG_LEVEL")
                .or_else(|_| tracing_subscriber::EnvFilter::try_new("info"))
                .unwrap(),
        )
        .init();

    let content_dir: PathBuf = std::env::var("CONTENT_DIR")
        .unwrap_or_else(|_| "/content".into())
        .into();
    let listen_addr: SocketAddr = std::env::var("LISTEN_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:8080".into())
        .parse()?;
    let auth_user = std::env::var("AUTH_USER")
        .map_err(|_| anyhow::anyhow!("AUTH_USER env var is required"))?;
    let auth_password = std::env::var("AUTH_PASSWORD")
        .map_err(|_| anyhow::anyhow!("AUTH_PASSWORD env var is required"))?;

    let client = Client::try_default().await?;
    let lookup: Arc<dyn ServiceLookup> =
        Arc::new(KubeClientLookup { client });

    let forgejo = Arc::new(ForgejoClient::from_env()?);

    let state = AppState {
        content_dir,
        lookup,
        forgejo,
        auth_user,
        auth_password,
    };

    let app = Router::new()
        .route("/", get(routes::index))
        .route("/styles.css", get(routes::styles))
        .route("/fonts/:name", get(routes::fonts))
        .route("/images/*path", get(routes::image))
        .route("/healthz", get(routes::healthz))
        .layer(tower_http::trace::TraceLayer::new_for_http())
        .with_state(state);

    tracing::info!(%listen_addr, "starting demo-landing");
    let listener = tokio::net::TcpListener::bind(listen_addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
