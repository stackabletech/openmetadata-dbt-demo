use async_trait::async_trait;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServiceInfo {
    pub namespace: String,
    pub node_port: u16,
}

#[derive(Debug, Error)]
pub enum LookupError {
    #[error("service '{0}' not found in any namespace")]
    NotFound(String),

    #[error("service '{name}' is ambiguous across namespaces: {namespaces:?}")]
    Ambiguous { name: String, namespaces: Vec<String> },

    #[error("service '{0}' is not of type NodePort")]
    NotNodePort(String),

    #[error("service '{0}' has no nodePort on its first port")]
    NoNodePortOnPort(String),

    #[error("no Ready node with a usable IP address")]
    NoReadyNode,

    #[error("kubernetes API error: {0}")]
    KubeApi(String),
}

#[async_trait]
pub trait ServiceLookup: Send + Sync {
    async fn lookup_service(&self, name: &str) -> Result<ServiceInfo, LookupError>;
    async fn pick_node_ip(&self) -> Result<String, LookupError>;
}

// --- Implementation backed by a real kube::Client is added in Task 7 (main wiring). ---
// kube.rs keeps the trait + types; the real impl lives close to main so tests here stay pure.

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Mutex;

    /// Hand-rolled mock. Tests configure the two result maps up front.
    pub struct MockLookup {
        pub services: Mutex<HashMap<String, Result<ServiceInfo, LookupError>>>,
        pub node_ip: Mutex<Result<String, LookupError>>,
    }

    impl MockLookup {
        pub fn new() -> Self {
            Self {
                services: Mutex::new(HashMap::new()),
                node_ip: Mutex::new(Err(LookupError::NoReadyNode)),
            }
        }
        pub fn with_service(self, name: &str, info: ServiceInfo) -> Self {
            self.services.lock().unwrap().insert(name.into(), Ok(info));
            self
        }
        pub fn with_service_err(self, name: &str, err: LookupError) -> Self {
            self.services.lock().unwrap().insert(name.into(), Err(err));
            self
        }
        pub fn with_node_ip(self, ip: &str) -> Self {
            *self.node_ip.lock().unwrap() = Ok(ip.into());
            self
        }
    }

    #[async_trait]
    impl ServiceLookup for MockLookup {
        async fn lookup_service(&self, name: &str) -> Result<ServiceInfo, LookupError> {
            self.services
                .lock()
                .unwrap()
                .get(name)
                .cloned()
                .unwrap_or_else(|| Err(LookupError::NotFound(name.into())))
        }
        async fn pick_node_ip(&self) -> Result<String, LookupError> {
            self.node_ip.lock().unwrap().clone().map_err(|e| e)
        }
    }

    // Allow cloning LookupError in tests.
    impl Clone for LookupError {
        fn clone(&self) -> Self {
            match self {
                Self::NotFound(s) => Self::NotFound(s.clone()),
                Self::Ambiguous { name, namespaces } => Self::Ambiguous {
                    name: name.clone(),
                    namespaces: namespaces.clone(),
                },
                Self::NotNodePort(s) => Self::NotNodePort(s.clone()),
                Self::NoNodePortOnPort(s) => Self::NoNodePortOnPort(s.clone()),
                Self::NoReadyNode => Self::NoReadyNode,
                Self::KubeApi(s) => Self::KubeApi(s.clone()),
            }
        }
    }

    #[tokio::test]
    async fn lookup_returns_info_for_match() {
        let mock = MockLookup::new().with_service(
            "argocd-server-nodeport",
            ServiceInfo { namespace: "deployment".into(), node_port: 30080 },
        );
        let got = mock.lookup_service("argocd-server-nodeport").await.unwrap();
        assert_eq!(got.namespace, "deployment");
        assert_eq!(got.node_port, 30080);
    }

    #[tokio::test]
    async fn lookup_returns_not_found_for_unknown() {
        let mock = MockLookup::new();
        let err = mock.lookup_service("nope").await.unwrap_err();
        matches!(err, LookupError::NotFound(_));
    }

    #[tokio::test]
    async fn node_ip_returns_configured_ip() {
        let mock = MockLookup::new().with_node_ip("74.234.12.5");
        let got = mock.pick_node_ip().await.unwrap();
        assert_eq!(got, "74.234.12.5");
    }

    #[tokio::test]
    async fn node_ip_returns_error_when_no_ready_node() {
        let mock = MockLookup::new(); // default: NoReadyNode
        let err = mock.pick_node_ip().await.unwrap_err();
        assert!(matches!(err, LookupError::NoReadyNode));
    }
}
