//! axum handlers.

use crate::kube::ServiceLookup;
use crate::render;
use crate::template;
use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use futures::future::join_all;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

const STYLES_CSS: &str = include_str!("../assets/styles.css");

#[derive(Clone)]
pub struct AppState {
    pub content_dir: PathBuf,
    pub lookup: Arc<dyn ServiceLookup>,
}

pub async fn healthz() -> &'static str {
    "ok"
}

pub async fn styles() -> Response {
    ([(header::CONTENT_TYPE, "text/css; charset=utf-8")], STYLES_CSS).into_response()
}

pub async fn index(State(state): State<AppState>) -> Response {
    let path = state.content_dir.join("index.md");
    let markdown = match tokio::fs::read_to_string(&path).await {
        Ok(s) => s,
        Err(e) => {
            tracing::error!(path = %path.display(), error = %e, "index.md read failed");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                format!(
                    "<h1>demo-landing</h1><p>Cannot read <code>{}</code>: {}</p>",
                    path.display(),
                    e
                ),
            )
                .into_response();
        }
    };

    // Pre-resolve all nodeport calls in parallel before rendering.
    let names = template::extract_service_names(&markdown);
    let mut resolved: HashMap<String, String> = HashMap::new();
    if !names.is_empty() {
        let node_ip = state.lookup.pick_node_ip().await;
        let services = join_all(
            names
                .iter()
                .map(|n| async { (n.clone(), state.lookup.lookup_service(n).await) }),
        )
        .await;

        match node_ip {
            Ok(ip) => {
                for (name, res) in services {
                    match res {
                        Ok(info) => {
                            resolved.insert(name, format!("{}:{}", ip, info.node_port));
                        }
                        Err(err) => {
                            tracing::warn!(service = %name, error = %err, "nodeport lookup failed");
                            resolved.insert(
                                name.clone(),
                                format!("<!-- nodeport error: {} -->", err),
                            );
                        }
                    }
                }
            }
            Err(err) => {
                tracing::warn!(error = %err, "node IP lookup failed");
                for (name, _) in services {
                    resolved
                        .insert(name, format!("<!-- nodeport error: {} -->", err));
                }
            }
        }
    }

    match render::render(&markdown, Arc::new(resolved), "Demo") {
        Ok(html) => (
            [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
            html,
        )
            .into_response(),
        Err(e) => {
            tracing::error!(error = %e, "render failed");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                format!("<h1>render error</h1><pre>{}</pre>", e),
            )
                .into_response()
        }
    }
}

/// Resolve `..` and `.` components in a path without touching the filesystem.
fn normalize_path(path: &std::path::Path) -> PathBuf {
    use std::path::Component;
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            c => out.push(c),
        }
    }
    out
}

pub async fn image(
    State(state): State<AppState>,
    Path(path): Path<String>,
) -> Response {
    // Resolve the requested path against the images dir and verify the
    // canonicalized result stays underneath it.
    let base = state.content_dir.join("images");
    let requested = base.join(&path);

    // Normalize without requiring the path to exist so we can detect traversal
    // attempts even when the target does not exist on disk.
    let normalized = normalize_path(&requested);
    let normalized_base = normalize_path(&base);
    if !normalized.starts_with(&normalized_base) {
        tracing::warn!(path = %path, "path traversal attempt refused");
        return (StatusCode::FORBIDDEN, "").into_response();
    }

    let canonical_base = match tokio::fs::canonicalize(&base).await {
        Ok(p) => p,
        Err(_) => {
            return (StatusCode::NOT_FOUND, "").into_response();
        }
    };
    let canonical_requested = match tokio::fs::canonicalize(&requested).await {
        Ok(p) => p,
        Err(_) => {
            return (StatusCode::NOT_FOUND, "").into_response();
        }
    };

    if !canonical_requested.starts_with(&canonical_base) {
        tracing::warn!(path = %path, "symlink traversal attempt refused");
        return (StatusCode::FORBIDDEN, "").into_response();
    }

    let bytes = match tokio::fs::read(&canonical_requested).await {
        Ok(b) => b,
        Err(_) => return (StatusCode::NOT_FOUND, "").into_response(),
    };

    let mime = mime_guess::from_path(&canonical_requested)
        .first_or_octet_stream()
        .to_string();

    let mut headers = HeaderMap::new();
    headers.insert(header::CONTENT_TYPE, mime.parse().unwrap());
    (headers, Body::from(bytes)).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kube::{ServiceInfo, ServiceLookup as _};

    // Reuse the kube module's test MockLookup by copy-pasting a minimal
    // equivalent here so this module's tests don't require cross-module
    // test helper visibility.
    use async_trait::async_trait;
    use std::collections::HashMap as StdMap;
    use std::sync::Mutex;

    struct LocalMock {
        services: Mutex<StdMap<String, ServiceInfo>>,
        node_ip: Mutex<Option<String>>,
    }

    impl LocalMock {
        fn new() -> Self {
            Self {
                services: Mutex::new(StdMap::new()),
                node_ip: Mutex::new(Some("10.0.0.1".into())),
            }
        }
    }

    #[async_trait]
    impl ServiceLookup for LocalMock {
        async fn lookup_service(
            &self,
            name: &str,
        ) -> Result<ServiceInfo, crate::kube::LookupError> {
            self.services
                .lock()
                .unwrap()
                .get(name)
                .cloned()
                .ok_or(crate::kube::LookupError::NotFound(name.into()))
        }
        async fn pick_node_ip(&self) -> Result<String, crate::kube::LookupError> {
            self.node_ip
                .lock()
                .unwrap()
                .clone()
                .ok_or(crate::kube::LookupError::NoReadyNode)
        }
    }

    #[tokio::test]
    async fn image_refuses_path_traversal() {
        let tmp = tempdir_with_images();
        let state = AppState {
            content_dir: tmp.path().into(),
            lookup: Arc::new(LocalMock::new()),
        };
        let resp = image(State(state), Path("../../etc/passwd".into())).await;
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn image_returns_404_for_missing() {
        let tmp = tempdir_with_images();
        let state = AppState {
            content_dir: tmp.path().into(),
            lookup: Arc::new(LocalMock::new()),
        };
        let resp = image(State(state), Path("does-not-exist.png".into())).await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn healthz_returns_ok() {
        assert_eq!(healthz().await, "ok");
    }

    // --- helpers ---

    /// Create a temp directory with an `images/` subdir inside. Using
    /// std::env::temp_dir() so we don't pull in a `tempfile` dep for one test.
    fn tempdir_with_images() -> TempDir {
        let base = std::env::temp_dir().join(format!(
            "demo-landing-test-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(base.join("images")).unwrap();
        // Write a known file used by the traversal test's canonicalization.
        std::fs::write(base.join("images").join("pixel.png"), b"\x89PNG\r\n").unwrap();
        TempDir { path: base }
    }

    struct TempDir { path: PathBuf }
    impl TempDir { fn path(&self) -> &std::path::Path { &self.path } }
    impl Drop for TempDir {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.path); }
    }
}
