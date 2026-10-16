// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use crate::BindingError;
use autoyou_protocol::Principal;
use autoyou_session::{
    host::{EndpointHost, HostError},
    registry as core,
};
use std::sync::{Arc, Mutex};

#[derive(uniffi::Object)]
pub struct VerifiedSessionBinding {
    value: Arc<core::VerifiedBinding>,
}

#[uniffi::export]
pub fn verified_session_binding(
    endpoint_id: String,
    device_id: String,
    owner_key: String,
    canonical_user_id: String,
    conversation_key: String,
    transport_id: String,
    generation: u64,
    authorization_epoch: u64,
    expires_at_ms: u64,
    scopes: Vec<String>,
    transport: String,
) -> Option<Arc<VerifiedSessionBinding>> {
    let principal = Principal {
        endpoint_id,
        device_id,
        owner_id: owner_key,
        conversation_id: conversation_key,
        generation,
        authorization_epoch,
        expires_at_ms,
        scopes,
    };
    core::VerifiedBinding::application(principal, canonical_user_id, transport_id, transport)
        .ok()
        .map(|value| Arc::new(VerifiedSessionBinding { value }))
}

#[derive(uniffi::Object)]
pub struct SessionAdmission {
    value: Arc<core::Lease>,
    live_clock: bool,
}
#[uniffi::export]
impl SessionAdmission {
    pub fn same_session(&self, other: Arc<SessionAdmission>) -> bool {
        Arc::ptr_eq(&self.value, &other.value)
    }
    pub fn matches(&self, binding: Arc<VerifiedSessionBinding>) -> bool {
        self.value.matches(&binding.value)
    }
    pub fn check(&self, scope: Option<String>, now_ms: Option<u64>) -> bool {
        self.value.check(
            scope.as_deref(),
            if self.live_clock { None } else { now_ms },
        )
    }
    pub fn transition(&self, state: String, now_ms: Option<u64>) -> bool {
        self.value
            .transition(&state, if self.live_clock { None } else { now_ms })
    }
    pub fn transport_id(&self) -> Option<String> {
        self.value
            .binding()
            .application_identity()
            .map(|identity| identity.transport_id.clone())
    }
}

#[derive(uniffi::Record)]
pub struct SessionAdmissionInstall {
    pub current: Arc<SessionAdmission>,
    pub previous: Option<Arc<SessionAdmission>>,
}

#[derive(uniffi::Object)]
pub struct SessionAdmissionRegistry {
    index: Arc<Mutex<core::Registry>>,
    endpoint: Option<Arc<EndpointHost>>,
}
impl SessionAdmissionRegistry {
    pub(crate) fn for_endpoint(endpoint: Arc<EndpointHost>) -> Arc<Self> {
        Arc::new(Self {
            index: endpoint.application_sessions(),
            endpoint: Some(endpoint),
        })
    }
    fn admission(&self, value: Arc<core::Lease>) -> Arc<SessionAdmission> {
        Arc::new(SessionAdmission {
            value,
            live_clock: self.endpoint.is_some(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn live_admission_ignores_foreign_clock_overrides() {
        let binding = core::VerifiedBinding::application(
            Principal {
                endpoint_id: "synthetic-endpoint".into(),
                device_id: "synthetic-device".into(),
                owner_id: "synthetic-owner".into(),
                conversation_id: "synthetic-conversation".into(),
                generation: 1,
                authorization_epoch: 1,
                expires_at_ms: 1000,
                scopes: vec!["chat".into()],
            },
            "synthetic-user".into(),
            "synthetic-transport".into(),
            "iroh".into(),
        )
        .unwrap();
        let mut index = core::Registry::default();
        let lease = index.install_local(binding, Some(500)).unwrap().current;
        let live = SessionAdmission {
            value: lease.clone(),
            live_clock: true,
        };
        let scoped = SessionAdmission {
            value: lease,
            live_clock: false,
        };
        assert!(scoped.check(Some("chat".into()), Some(500)));
        assert!(!live.check(Some("chat".into()), Some(500)));
        assert!(!live.transition("online".into(), Some(500)));
    }
}
#[uniffi::export]
impl SessionAdmissionRegistry {
    #[uniffi::constructor]
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            index: Arc::new(Mutex::new(core::Registry::default())),
            endpoint: None,
        })
    }
    // Construct opaque handles through their owner so separate loaded SDKs never exchange pointers.
    pub fn prepare_binding(
        &self,
        endpoint_id: String,
        device_id: String,
        owner_key: String,
        canonical_user_id: String,
        conversation_key: String,
        transport_id: String,
        generation: u64,
        authorization_epoch: u64,
        expires_at_ms: u64,
        scopes: Vec<String>,
        transport: String,
    ) -> Option<Arc<VerifiedSessionBinding>> {
        verified_session_binding(
            endpoint_id,
            device_id,
            owner_key,
            canonical_user_id,
            conversation_key,
            transport_id,
            generation,
            authorization_epoch,
            expires_at_ms,
            scopes,
            transport,
        )
    }
    pub fn install(
        &self,
        binding: Arc<VerifiedSessionBinding>,
        connection_id: Option<u64>,
        now_ms: Option<u64>,
    ) -> Result<Option<SessionAdmissionInstall>, BindingError> {
        let installed = if let Some(endpoint) = &self.endpoint {
            let id = connection_id.ok_or(BindingError::InvalidInput)?;
            match endpoint.admit_binding(id, binding.value.clone()) {
                Ok(value) => value,
                Err(
                    HostError::NotAuthorized
                    | HostError::Backpressure
                    | HostError::InvalidConfig
                    | HostError::Closed
                    | HostError::UnknownConnection,
                ) => return Ok(None),
                Err(error) => return Err(error.into()),
            }
        } else {
            let mut index = self.index.lock().map_err(|_| BindingError::Worker)?;
            match index.install_local(binding.value.clone(), now_ms) {
                Ok(value) => value,
                Err(core::RegistryError::Denied | core::RegistryError::Capacity) => {
                    return Ok(None)
                }
                Err(core::RegistryError::Worker) => return Err(BindingError::Worker),
            }
        };
        Ok(Some(SessionAdmissionInstall {
            current: self.admission(installed.current),
            previous: installed.previous.map(|value| self.admission(value)),
        }))
    }
    pub fn revoke_local(
        &self,
        device_id: String,
        minimum_epoch: u64,
    ) -> Result<Option<Arc<SessionAdmission>>, BindingError> {
        let value = self
            .index
            .lock()
            .map_err(|_| BindingError::Worker)?
            .revoke_local(&device_id, minimum_epoch);
        Ok(value.map(|value| self.admission(value)))
    }
    pub fn revoke_owned(
        &self,
        device_id: String,
        minimum_epoch: u64,
        transport_ids: Vec<String>,
    ) -> Result<Vec<Arc<SessionAdmission>>, BindingError> {
        if transport_ids.len() > 64 {
            return Err(BindingError::InvalidInput);
        }
        let values = self
            .index
            .lock()
            .map_err(|_| BindingError::Worker)?
            .revoke_owned(&device_id, minimum_epoch, &transport_ids);
        Ok(values
            .into_iter()
            .map(|value| self.admission(value))
            .collect())
    }
    pub fn retire(&self, admission: Arc<SessionAdmission>) -> Result<bool, BindingError> {
        Ok(self
            .index
            .lock()
            .map_err(|_| BindingError::Worker)?
            .retire(&admission.value))
    }
}
