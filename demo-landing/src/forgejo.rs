//! Minimal Forgejo / Gitea-compatible HTTP client. Two operations:
//! `get_file` (read file contents + blob SHA) and `put_file` (commit new
//! contents using the SHA as an optimistic-locking handle).
//!
//! Configuration is via env vars; see `ForgejoClient::from_env`.

use std::time::Duration;

use base64::Engine;
use serde::Deserialize;
use thiserror::Error;

#[derive(Debug, Clone)]
pub struct FileContents {
    pub content: String,
    pub sha: String,
}

#[derive(Debug, Error)]
pub enum ForgejoError {
    #[error("file not found: {0}")]
    NotFound(String),

    #[error("file changed since read")]
    SHAConflict,

    #[error("forgejo api {status}: {body}")]
    Api { status: u16, body: String },

    #[error("http error: {0}")]
    Http(String),

    #[error("decode error: {0}")]
    Decode(String),
}

#[derive(Debug, Clone)]
pub struct ForgejoClient {
    base_url: String,
    owner: String,
    repo: String,
    branch: String,
    username: String,
    password: String,
    http: reqwest::Client,
}

#[derive(Deserialize)]
struct ContentsResponse {
    content: String,
    sha: String,
}

impl ForgejoClient {
    pub fn from_env() -> anyhow::Result<Self> {
        let base_url = std::env::var("FORGEJO_URL").unwrap_or_else(|_| {
            "http://forgejo-http.deployment.svc.cluster.local:3000".to_string()
        });
        let owner = std::env::var("FORGEJO_OWNER").unwrap_or_else(|_| "stackable".to_string());
        let repo = std::env::var("FORGEJO_REPO")
            .unwrap_or_else(|_| "openmetadata-dbt-demo".to_string());
        let branch = std::env::var("FORGEJO_BRANCH").unwrap_or_else(|_| "main".to_string());
        let username = std::env::var("FORGEJO_USERNAME")
            .map_err(|_| anyhow::anyhow!("FORGEJO_USERNAME env var is required"))?;
        let password = std::env::var("FORGEJO_PASSWORD")
            .map_err(|_| anyhow::anyhow!("FORGEJO_PASSWORD env var is required"))?;

        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(10))
            .build()?;

        Ok(Self {
            base_url,
            owner,
            repo,
            branch,
            username,
            password,
            http,
        })
    }

    /// Construct a no-op client for unit tests that never make real calls.
    pub fn for_testing() -> Self {
        Self {
            base_url: "http://invalid.test".into(),
            owner: "test".into(),
            repo: "test".into(),
            branch: "main".into(),
            username: "test".into(),
            password: "test".into(),
            http: reqwest::Client::new(),
        }
    }

    pub async fn get_file(&self, path: &str) -> Result<FileContents, ForgejoError> {
        let url = format!(
            "{}/api/v1/repos/{}/{}/contents/{}",
            self.base_url, self.owner, self.repo, path
        );

        let resp = self
            .http
            .get(&url)
            .basic_auth(&self.username, Some(&self.password))
            .query(&[("ref", self.branch.as_str())])
            .send()
            .await
            .map_err(|e| ForgejoError::Http(e.to_string()))?;

        let status = resp.status().as_u16();
        match status {
            200 => {
                let body: ContentsResponse = resp
                    .json()
                    .await
                    .map_err(|e| ForgejoError::Decode(e.to_string()))?;
                let cleaned: String = body.content.chars().filter(|c| !c.is_whitespace()).collect();
                let bytes = base64::engine::general_purpose::STANDARD
                    .decode(cleaned)
                    .map_err(|e| ForgejoError::Decode(e.to_string()))?;
                let content = String::from_utf8(bytes)
                    .map_err(|e| ForgejoError::Decode(e.to_string()))?;
                Ok(FileContents {
                    content,
                    sha: body.sha,
                })
            }
            404 => Err(ForgejoError::NotFound(path.to_string())),
            _ => {
                let body = resp.text().await.unwrap_or_default();
                Err(ForgejoError::Api { status, body })
            }
        }
    }

    pub async fn put_file(
        &self,
        path: &str,
        content: &str,
        sha: &str,
        message: &str,
    ) -> Result<(), ForgejoError> {
        let url = format!(
            "{}/api/v1/repos/{}/{}/contents/{}",
            self.base_url, self.owner, self.repo, path
        );
        let body = serde_json::json!({
            "content": base64::engine::general_purpose::STANDARD.encode(content),
            "sha": sha,
            "message": message,
            "branch": self.branch,
        });

        let resp = self
            .http
            .put(&url)
            .basic_auth(&self.username, Some(&self.password))
            .json(&body)
            .send()
            .await
            .map_err(|e| ForgejoError::Http(e.to_string()))?;

        let status = resp.status().as_u16();
        match status {
            200 | 201 => Ok(()),
            409 => Err(ForgejoError::SHAConflict),
            422 => {
                let body = resp.text().await.unwrap_or_default();
                if body.to_lowercase().contains("sha")
                    && body.to_lowercase().contains("does not match")
                {
                    Err(ForgejoError::SHAConflict)
                } else {
                    Err(ForgejoError::Api { status, body })
                }
            }
            _ => {
                let body = resp.text().await.unwrap_or_default();
                Err(ForgejoError::Api { status, body })
            }
        }
    }
}
