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

/// Build the demo-landing `<a href>` for the logout link.
///
/// The result is `/oauth2/sign_out?rd=<URL-encoded keycloak end-session URL>`.
/// Encoding is required so the inner query (`?post_logout_redirect_uri=...&client_id=demo-landing`)
/// survives being a *value* of oauth2-proxy's outer `?rd=` parameter.
///
/// Returns an empty string if either input is empty (callers treat that as
/// "don't render the logout link").
pub fn build_logout_url(issuer_url: &str, landing_base_url: &str) -> String {
    if issuer_url.is_empty() || landing_base_url.is_empty() {
        return String::new();
    }
    let issuer = issuer_url.trim_end_matches('/');
    let landing = landing_base_url.trim_end_matches('/');
    let encoded_landing = urlencoding::encode(landing);
    let inner = format!(
        "{}/protocol/openid-connect/logout?post_logout_redirect_uri={}&client_id=demo-landing",
        issuer, encoded_landing
    );
    format!("/oauth2/sign_out?rd={}", urlencoding::encode(&inner))
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

    #[test]
    fn build_logout_url_encodes_post_logout_redirect_inside_rd() {
        let got = super::build_logout_url(
            "http://10.0.0.1:30900/realms/stackable-demo",
            "http://10.0.0.1:30088",
        );
        // The whole keycloak URL must be URL-encoded so the inner `?` and `&`
        // are not parsed by oauth2-proxy as new top-level query params.
        let expected = "/oauth2/sign_out?rd=http%3A%2F%2F10.0.0.1%3A30900%2Frealms%2Fstackable-demo%2Fprotocol%2Fopenid-connect%2Flogout%3Fpost_logout_redirect_uri%3Dhttp%253A%252F%252F10.0.0.1%253A30088%26client_id%3Ddemo-landing";
        assert_eq!(got, expected);
    }

    #[test]
    fn build_logout_url_returns_empty_when_inputs_empty() {
        assert_eq!(super::build_logout_url("", ""), "");
        assert_eq!(super::build_logout_url("http://x", ""), "");
        assert_eq!(super::build_logout_url("", "http://x"), "");
    }

    #[test]
    fn build_logout_url_strips_trailing_slash_on_issuer() {
        let got = super::build_logout_url("http://kc/realms/r/", "http://land/");
        // Issuer has its trailing slash stripped before appending the logout
        // path, so we don't end up with `//protocol/...`.
        assert!(got.contains("realms%2Fr%2Fprotocol%2Fopenid-connect%2Flogout"));
    }
}
