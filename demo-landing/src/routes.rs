//! axum handlers.

use crate::forgejo::{ForgejoClient, ForgejoError};
use crate::kube::ServiceLookup;
use crate::render::{self, set_yaml_bool_at_path};
use crate::template::{self, html_attr_escape, ToggleResolution};
use axum::{
    body::Body,
    extract::{Form, Path, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Redirect, Response},
};
use futures::future::join_all;
use serde::Deserialize;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

const STYLES_CSS: &str = include_str!("../assets/styles.css");

#[derive(Clone)]
pub struct AppState {
    pub content_dir: PathBuf,
    pub lookup: Arc<dyn ServiceLookup>,
    pub forgejo: Arc<ForgejoClient>,
    pub auth_user: String,
    pub auth_password: String,
}

pub async fn healthz() -> &'static str {
    "ok"
}

pub async fn styles() -> Response {
    ([(header::CONTENT_TYPE, "text/css; charset=utf-8")], STYLES_CSS).into_response()
}

// Brand fonts bundled into the binary so the landing page has no external
// font dependency (matches the portal's @fontsource approach for GDPR).
const FONT_NOTO_400: &[u8] = include_bytes!("../assets/fonts/NotoSans-400.woff2");
const FONT_NOTO_700: &[u8] = include_bytes!("../assets/fonts/NotoSans-700.woff2");
const FONT_IBM_400: &[u8] = include_bytes!("../assets/fonts/IBMPlexMono-400.woff2");
const FONT_IBM_700: &[u8] = include_bytes!("../assets/fonts/IBMPlexMono-700.woff2");

pub async fn fonts(Path(name): Path<String>) -> Response {
    let bytes: &[u8] = match name.as_str() {
        "NotoSans-400.woff2" => FONT_NOTO_400,
        "NotoSans-700.woff2" => FONT_NOTO_700,
        "IBMPlexMono-400.woff2" => FONT_IBM_400,
        "IBMPlexMono-700.woff2" => FONT_IBM_700,
        _ => return (StatusCode::NOT_FOUND, "").into_response(),
    };
    let mut headers = HeaderMap::new();
    headers.insert(header::CONTENT_TYPE, "font/woff2".parse().unwrap());
    headers.insert(
        header::CACHE_CONTROL,
        "public, max-age=31536000, immutable".parse().unwrap(),
    );
    (headers, Body::from(bytes)).into_response()
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

    // Pre-resolve nodeports (existing).
    let names = template::extract_service_names(&markdown);
    let mut nodeports: HashMap<String, String> = HashMap::new();
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
                            nodeports.insert(name, format!("{}:{}", ip, info.node_port));
                        }
                        Err(err) => {
                            tracing::warn!(service = %name, error = %err, "nodeport lookup failed");
                            nodeports
                                .insert(name.clone(), format!("<!-- nodeport error: {} -->", err));
                        }
                    }
                }
            }
            Err(err) => {
                tracing::warn!(error = %err, "node IP lookup failed");
                for (name, _) in services {
                    nodeports.insert(name, format!("<!-- nodeport error: {} -->", err));
                }
            }
        }
    }

    // Pre-resolve toggles (new).
    let toggle_pairs = template::extract_toggle_calls(&markdown);
    let mut toggles: HashMap<(String, String), ToggleResolution> = HashMap::new();
    if !toggle_pairs.is_empty() {
        // Unique file paths: fetch each only once.
        let mut unique_paths: Vec<String> = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for (p, _) in &toggle_pairs {
            if seen.insert(p.clone()) {
                unique_paths.push(p.clone());
            }
        }
        let fetched = join_all(
            unique_paths
                .into_iter()
                .map(|p| async {
                    let r = state.forgejo.get_file(&p).await;
                    (p, r)
                }),
        )
        .await;

        // Cache: file path -> Result<(parsed Value, sha), reason string>
        let mut file_cache: HashMap<String, Result<(serde_yaml::Value, String), String>> =
            HashMap::new();
        for (path, res) in fetched {
            match res {
                Ok(fc) => match serde_yaml::from_str::<serde_yaml::Value>(&fc.content) {
                    Ok(v) => {
                        file_cache.insert(path, Ok((v, fc.sha)));
                    }
                    Err(e) => {
                        file_cache.insert(path, Err(format!("yaml parse error: {}", e)));
                    }
                },
                Err(e) => {
                    file_cache.insert(path, Err(format!("forgejo: {}", e)));
                }
            }
        }

        for (file_path, key_path) in toggle_pairs {
            let resolution = match file_cache.get(&file_path) {
                Some(Ok((yaml, sha))) => {
                    match render::get_yaml_bool_at_path(yaml, &key_path) {
                        Ok(v) => ToggleResolution::Ok {
                            sha: sha.clone(),
                            value: v,
                        },
                        Err(e) => ToggleResolution::Error {
                            reason: format!("{}", e),
                        },
                    }
                }
                Some(Err(reason)) => ToggleResolution::Error {
                    reason: reason.clone(),
                },
                None => ToggleResolution::Error {
                    reason: "file fetch missing from cache".into(),
                },
            };
            toggles.insert((file_path, key_path), resolution);
        }
    }

    match render::render(&markdown, Arc::new(nodeports), Arc::new(toggles), "Demo") {
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

#[derive(Deserialize, Debug)]
pub struct ToggleRequest {
    pub path: String,
    pub key: String,
    pub sha: String,
    pub current_value: bool,
}

pub async fn toggle(
    State(state): State<AppState>,
    Form(req): Form<ToggleRequest>,
) -> Response {
    let new_value = !req.current_value;

    let file = match state.forgejo.get_file(&req.path).await {
        Ok(f) => f,
        Err(e) => return forgejo_error_page(&req.path, e),
    };

    let mut yaml: serde_yaml::Value = match serde_yaml::from_str(&file.content) {
        Ok(v) => v,
        Err(e) => return yaml_error_page(&req.path, &format!("parse error: {}", e)),
    };

    if let Err(e) = set_yaml_bool_at_path(&mut yaml, &req.key, new_value) {
        return yaml_error_page(&req.path, &format!("{}", e));
    }

    let new_content = match serde_yaml::to_string(&yaml) {
        Ok(s) => s,
        Err(e) => return yaml_error_page(&req.path, &format!("serialize error: {}", e)),
    };

    let msg = format!("Toggle {} on {}", req.key, req.path);

    match state
        .forgejo
        .put_file(&req.path, &new_content, &req.sha, &msg)
        .await
    {
        Ok(_) => Redirect::to("/").into_response(),
        Err(ForgejoError::SHAConflict) => stale_view_page(&req.path),
        Err(e) => forgejo_error_page(&req.path, e),
    }
}

fn stale_view_page(path: &str) -> Response {
    let body = format!(
        r#"<!DOCTYPE html><html><head><title>Stale view</title>
<link rel="stylesheet" href="/styles.css"></head>
<body><main><h1>Stale view</h1>
<p>The file <code>{}</code> changed since the page loaded. Please <a href="/">reload</a> and try again.</p>
</main></body></html>"#,
        html_attr_escape(path)
    );
    (
        StatusCode::CONFLICT,
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        body,
    )
        .into_response()
}

fn forgejo_error_page(path: &str, err: ForgejoError) -> Response {
    let body = format!(
        r#"<!DOCTYPE html><html><head><title>Forgejo error</title>
<link rel="stylesheet" href="/styles.css"></head>
<body><main><h1>Forgejo error</h1>
<p>While operating on <code>{}</code>:</p>
<pre>{}</pre>
<p><a href="/">Back to landing page</a></p>
</main></body></html>"#,
        html_attr_escape(path),
        html_attr_escape(&err.to_string())
    );
    (
        StatusCode::BAD_GATEWAY,
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        body,
    )
        .into_response()
}

fn yaml_error_page(path: &str, detail: &str) -> Response {
    let body = format!(
        r#"<!DOCTYPE html><html><head><title>YAML error</title>
<link rel="stylesheet" href="/styles.css"></head>
<body><main><h1>YAML error</h1>
<p>While operating on <code>{}</code>:</p>
<pre>{}</pre>
<p><a href="/">Back to landing page</a></p>
</main></body></html>"#,
        html_attr_escape(path),
        html_attr_escape(detail)
    );
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        body,
    )
        .into_response()
}

pub async fn image(
    State(state): State<AppState>,
    Path(path): Path<String>,
) -> Response {
    let base = state.content_dir.join("images");
    let requested = base.join(&path);

    let normalized = normalize_path(&requested);
    let normalized_base = normalize_path(&base);
    if !normalized.starts_with(&normalized_base) {
        tracing::warn!(path = %path, "path traversal attempt refused");
        return (StatusCode::FORBIDDEN, "").into_response();
    }

    let canonical_base = match tokio::fs::canonicalize(&base).await {
        Ok(p) => p,
        Err(_) => return (StatusCode::NOT_FOUND, "").into_response(),
    };
    let canonical_requested = match tokio::fs::canonicalize(&requested).await {
        Ok(p) => p,
        Err(_) => return (StatusCode::NOT_FOUND, "").into_response(),
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kube::{ServiceInfo, ServiceLookup as _};

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

    fn test_state(tmp: &std::path::Path) -> AppState {
        AppState {
            content_dir: tmp.into(),
            lookup: Arc::new(LocalMock::new()),
            forgejo: Arc::new(ForgejoClient::for_testing()),
            auth_user: "admin".into(),
            auth_password: "secret".into(),
        }
    }

    #[tokio::test]
    async fn image_refuses_path_traversal() {
        let tmp = tempdir_with_images();
        let resp = image(State(test_state(tmp.path())), Path("../../etc/passwd".into())).await;
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn image_returns_404_for_missing() {
        let tmp = tempdir_with_images();
        let resp = image(State(test_state(tmp.path())), Path("does-not-exist.png".into())).await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn healthz_returns_ok() {
        assert_eq!(healthz().await, "ok");
    }

    fn tempdir_with_images() -> TempDir {
        let base = std::env::temp_dir().join(format!(
            "demo-landing-test-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(base.join("images")).unwrap();
        std::fs::write(base.join("images").join("pixel.png"), b"\x89PNG\r\n").unwrap();
        TempDir { path: base }
    }

    struct TempDir {
        path: PathBuf,
    }
    impl TempDir {
        fn path(&self) -> &std::path::Path {
            &self.path
        }
    }
    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
}
