//! HTTP basic-auth middleware. Applied to every route except `/healthz`
//! (which must remain unauthenticated for the kubelet liveness probe).

use axum::{
    extract::{Request, State},
    http::{header, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
};
use base64::Engine;

use crate::routes::AppState;

pub async fn basic_auth(
    State(state): State<AppState>,
    req: Request,
    next: Next,
) -> Result<Response, Response> {
    let provided = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|h| h.to_str().ok())
        .and_then(|h| h.strip_prefix("Basic "))
        .and_then(|b64| base64::engine::general_purpose::STANDARD.decode(b64).ok())
        .and_then(|bytes| String::from_utf8(bytes).ok());

    if let Some(creds) = provided {
        if let Some((u, p)) = creds.split_once(':') {
            if u == state.auth_user && p == state.auth_password {
                return Ok(next.run(req).await);
            }
        }
    }

    Err((
        StatusCode::UNAUTHORIZED,
        [(header::WWW_AUTHENTICATE, r#"Basic realm="demo-landing""#)],
        "Authentication required",
    )
        .into_response())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request as HttpRequest, StatusCode};
    use axum::routing::get;
    use axum::Router;
    use std::sync::Arc;
    use tower::ServiceExt;

    use crate::forgejo::ForgejoClient;
    use crate::kube::ServiceLookup;

    fn test_state(user: &str, password: &str) -> AppState {
        AppState {
            content_dir: "/tmp".into(),
            lookup: Arc::new(NullLookup),
            forgejo: Arc::new(ForgejoClient::for_testing()),
            auth_user: user.to_string(),
            auth_password: password.to_string(),
        }
    }

    struct NullLookup;
    #[async_trait::async_trait]
    impl ServiceLookup for NullLookup {
        async fn lookup_service(&self, _: &str) -> Result<crate::kube::ServiceInfo, crate::kube::LookupError> {
            unimplemented!("not used in auth tests")
        }
        async fn pick_node_ip(&self) -> Result<String, crate::kube::LookupError> {
            unimplemented!("not used in auth tests")
        }
    }

    fn router() -> Router<()> {
        let state = test_state("admin", "secret");
        Router::new()
            .route("/protected", get(|| async { "ok" }))
            .layer(axum::middleware::from_fn_with_state(state.clone(), basic_auth))
            .with_state(state)
    }

    fn basic_header(user: &str, password: &str) -> String {
        let raw = format!("{}:{}", user, password);
        format!(
            "Basic {}",
            base64::engine::general_purpose::STANDARD.encode(raw)
        )
    }

    #[tokio::test]
    async fn rejects_request_without_authorization_header() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .uri("/protected")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(
            resp.headers().get(header::WWW_AUTHENTICATE).unwrap(),
            r#"Basic realm="demo-landing""#
        );
    }

    #[tokio::test]
    async fn accepts_correct_credentials() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .uri("/protected")
                    .header(header::AUTHORIZATION, basic_header("admin", "secret"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn rejects_wrong_password() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .uri("/protected")
                    .header(header::AUTHORIZATION, basic_header("admin", "wrong"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn rejects_malformed_base64() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .uri("/protected")
                    .header(header::AUTHORIZATION, "Basic not-valid-base64!!!")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }
}
