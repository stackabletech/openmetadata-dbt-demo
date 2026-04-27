//! Header-based auth middleware. oauth2-proxy is the auth boundary; this
//! module reads the headers it forwards and gates admin-only endpoints
//! on Keycloak realm role membership.

use axum::{
    extract::Request,
    http::StatusCode,
    middleware::Next,
    response::Response,
};

const FORWARDED_GROUPS: &str = "x-forwarded-groups";
const ADMIN_ROLE: &str = "admin";

const FORWARDED_PREFERRED_USERNAME: &str = "x-forwarded-preferred-username";

/// Read the username oauth2-proxy forwards via `X-Forwarded-Preferred-Username`.
/// Returns an empty string if the header is missing or not visible-ASCII.
pub fn extract_current_user(headers: &axum::http::HeaderMap) -> String {
    headers
        .get(FORWARDED_PREFERRED_USERNAME)
        .and_then(|h| h.to_str().ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

pub async fn require_admin(req: Request, next: Next) -> Result<Response, StatusCode> {
    let header_value = req
        .headers()
        .get(FORWARDED_GROUPS)
        .and_then(|h| h.to_str().ok())
        .unwrap_or("");

    let has_admin = header_value
        .split(',')
        .map(|s| s.trim())
        .any(|s| s == ADMIN_ROLE);

    if has_admin {
        Ok(next.run(req).await)
    } else {
        Err(StatusCode::FORBIDDEN)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, http::Request as HttpRequest, routing::post, Router};
    use tower::ServiceExt;

    fn router() -> Router<()> {
        Router::new()
            .route("/admin-only", post(|| async { "ok" }))
            .layer(axum::middleware::from_fn(require_admin))
    }

    #[tokio::test]
    async fn rejects_when_groups_header_missing() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn rejects_when_groups_header_lacks_admin() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .header("x-forwarded-groups", "viewer,other")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn accepts_when_groups_header_contains_admin() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .header("x-forwarded-groups", "viewer,admin,other")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn accepts_when_groups_header_is_just_admin() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .header("x-forwarded-groups", "admin")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn handles_whitespace_in_groups_list() {
        let resp = router()
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/admin-only")
                    .header("x-forwarded-groups", " viewer , admin ")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    use axum::http::HeaderMap;

    #[test]
    fn extract_current_user_returns_empty_when_header_missing() {
        let headers = HeaderMap::new();
        assert_eq!(super::extract_current_user(&headers), "");
    }

    #[test]
    fn extract_current_user_returns_header_value() {
        let mut headers = HeaderMap::new();
        headers.insert("x-forwarded-preferred-username", "demo-admin".parse().unwrap());
        assert_eq!(super::extract_current_user(&headers), "demo-admin");
    }

    #[test]
    fn extract_current_user_trims_surrounding_whitespace() {
        let mut headers = HeaderMap::new();
        headers.insert("x-forwarded-preferred-username", "  demo-user  ".parse().unwrap());
        assert_eq!(super::extract_current_user(&headers), "demo-user");
    }

    #[test]
    fn extract_current_user_returns_empty_when_header_value_is_non_visible_ascii() {
        // HeaderValue::to_str returns Err for non-visible-ASCII bytes; we fall
        // through to empty rather than panicking.
        let mut headers = HeaderMap::new();
        let v = axum::http::HeaderValue::from_bytes(b"\xff\xfe").unwrap();
        headers.insert("x-forwarded-preferred-username", v);
        assert_eq!(super::extract_current_user(&headers), "");
    }
}
