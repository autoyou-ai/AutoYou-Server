// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! One admission owner for socket IO and application callbacks.

use autoyou_protocol::{
    Admission, FrameHeader, Lane, Principal, VerifiedPrincipal, MAX_IDENTIFIER_BYTES,
};
use std::{
    collections::{BTreeSet, HashMap},
    sync::{Arc, Mutex, Weak},
    time::{SystemTime, UNIX_EPOCH},
};

pub type DeviceKey = (Option<String>, Option<String>, String);
const MAX_DEVICE_FLOORS: usize = 4096;

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RegistryError {
    Denied,
    Capacity,
    Worker,
}

#[derive(Debug, PartialEq, Eq)]
pub struct ApplicationIdentity {
    pub canonical_user_id: String,
    pub transport_id: String,
    pub transport: String,
}

#[derive(Debug, PartialEq, Eq)]
pub struct VerifiedBinding {
    principal: VerifiedPrincipal,
    application: Option<ApplicationIdentity>,
}
impl VerifiedBinding {
    pub fn wire(principal: Principal) -> Result<Arc<Self>, RegistryError> {
        Ok(Arc::new(Self {
            principal: principal.try_into().map_err(|_| RegistryError::Denied)?,
            application: None,
        }))
    }
    pub fn application(
        principal: Principal,
        canonical_user_id: String,
        transport_id: String,
        transport: String,
    ) -> Result<Arc<Self>, RegistryError> {
        if [&canonical_user_id, &transport_id]
            .iter()
            .any(|value| value.is_empty() || value.len() > MAX_IDENTIFIER_BYTES)
            || !matches!(transport.as_str(), "legacy" | "iroh")
        {
            return Err(RegistryError::Denied);
        }
        Ok(Arc::new(Self {
            principal: principal.try_into().map_err(|_| RegistryError::Denied)?,
            application: Some(ApplicationIdentity {
                canonical_user_id,
                transport_id,
                transport,
            }),
        }))
    }
    pub fn principal(&self) -> &VerifiedPrincipal {
        &self.principal
    }
    pub fn application_identity(&self) -> Option<&ApplicationIdentity> {
        self.application.as_ref()
    }
}

pub struct Lease {
    pub id: u64,
    key: DeviceKey,
    binding: Arc<VerifiedBinding>,
    admission: Arc<Mutex<Admission>>,
}
impl Lease {
    pub fn binding(&self) -> &Arc<VerifiedBinding> {
        &self.binding
    }
    pub fn matches(&self, binding: &VerifiedBinding) -> bool {
        *self.binding == *binding
    }
    fn header(&self) -> FrameHeader {
        FrameHeader {
            lane: Lane::Control,
            generation: self.binding.principal.generation,
            stream_id: 0,
            sequence: 0,
            length: 0,
        }
    }
    pub fn check(&self, scope: Option<&str>, clock: Option<u64>) -> bool {
        let Ok(admission) = self.admission.lock() else {
            return false;
        };
        admission
            .check(&self.header(), scope, clock.unwrap_or_else(now_ms))
            .is_ok()
    }
    pub fn transition(&self, state: &str, clock: Option<u64>) -> bool {
        let terminal = match state {
            "revoked" | "needs_pairing" | "user_disconnected" | "failed" => true,
            "idle" | "enrolling" | "connecting" | "authorizing" | "online" | "changing_path"
            | "recovering" | "suspended" => false,
            _ => return false,
        };
        let Ok(mut admission) = self.admission.lock() else {
            return false;
        };
        if admission
            .check(&self.header(), None, clock.unwrap_or_else(now_ms))
            .is_err()
        {
            return false;
        }
        if terminal {
            admission.revoke();
        }
        true
    }
    pub fn revoke(&self) {
        self.admission
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .revoke();
    }
}

struct Entry {
    generation: u64,
    epoch: u64,
    endpoint_id: String,
    active: Option<Arc<Lease>>,
}
pub struct Installed {
    pub current: Arc<Lease>,
    pub previous: Option<Arc<Lease>>,
}

#[derive(Default)]
pub struct Registry {
    entries: HashMap<DeviceKey, Entry>,
    endpoint_devices: HashMap<DeviceKey, String>,
    transports: HashMap<String, Weak<Lease>>,
    next_local_id: u64,
}
impl Registry {
    fn validate(
        &self,
        key: &DeviceKey,
        binding: &VerifiedBinding,
        clock: u64,
    ) -> Result<(), RegistryError> {
        let principal = binding.principal();
        if key.2 != principal.device_id
            || principal.expires_at_ms <= clock
            || key
                .0
                .as_ref()
                .is_some_and(|endpoint| endpoint != &principal.endpoint_id)
            || key
                .1
                .as_ref()
                .is_some_and(|room| room != &principal.conversation_id)
        {
            return Err(RegistryError::Denied);
        }
        if let Some(old) = self.entries.get(key) {
            if principal.generation <= old.generation
                || principal.authorization_epoch < old.epoch
                || principal.endpoint_id != old.endpoint_id
            {
                return Err(RegistryError::Denied);
            }
            if let Some(active) = &old.active {
                let previous = active.binding.principal();
                if principal.owner_id != previous.owner_id
                    || principal.conversation_id != previous.conversation_id
                {
                    return Err(RegistryError::Denied);
                }
                if let (Some(old), Some(new)) = (
                    active.binding.application_identity(),
                    binding.application_identity(),
                ) {
                    if old.canonical_user_id != new.canonical_user_id {
                        return Err(RegistryError::Denied);
                    }
                }
            }
        } else if self.entries.len() >= MAX_DEVICE_FLOORS {
            return Err(RegistryError::Capacity);
        }
        let endpoint_key = (key.0.clone(), key.1.clone(), principal.endpoint_id.clone());
        if self
            .endpoint_devices
            .get(&endpoint_key)
            .is_some_and(|device| device != &key.2)
        {
            return Err(RegistryError::Denied);
        }
        if let Some(identity) = binding.application_identity() {
            if self
                .transports
                .get(&identity.transport_id)
                .and_then(Weak::upgrade)
                .is_some_and(|lease| lease.key != *key)
            {
                return Err(RegistryError::Denied);
            }
        }
        Ok(())
    }
    pub fn install(
        &mut self,
        key: DeviceKey,
        id: u64,
        binding: Arc<VerifiedBinding>,
        admission: Arc<Mutex<Admission>>,
        authenticated_endpoint: &str,
        clock: Option<u64>,
    ) -> Result<Installed, RegistryError> {
        let mut authority = admission.lock().map_err(|_| RegistryError::Worker)?;
        self.validate(&key, &binding, clock.unwrap_or_else(now_ms))?;
        let principal = binding.principal();
        authority
            .admit_verified(principal.clone(), authenticated_endpoint)
            .map_err(|_| RegistryError::Denied)?;
        drop(authority);
        let previous = self
            .entries
            .get(&key)
            .and_then(|entry| entry.active.clone());
        if let Some(old) = &previous {
            // Reauthorizing one socket changes its generation; it must not revoke the new admission.
            if !Arc::ptr_eq(&old.admission, &admission) {
                old.revoke();
            }
            if let Some(identity) = old.binding.application_identity() {
                self.transports.remove(&identity.transport_id);
            }
        }
        let current = Arc::new(Lease {
            id,
            key: key.clone(),
            binding: binding.clone(),
            admission,
        });
        if let Some(identity) = binding.application_identity() {
            self.transports
                .insert(identity.transport_id.clone(), Arc::downgrade(&current));
        }
        self.endpoint_devices.insert(
            (key.0.clone(), key.1.clone(), principal.endpoint_id.clone()),
            key.2.clone(),
        );
        self.entries.insert(
            key,
            Entry {
                generation: principal.generation,
                epoch: principal.authorization_epoch,
                endpoint_id: principal.endpoint_id.clone(),
                active: Some(current.clone()),
            },
        );
        Ok(Installed { current, previous })
    }
    pub fn install_local(
        &mut self,
        binding: Arc<VerifiedBinding>,
        clock: Option<u64>,
    ) -> Result<Installed, RegistryError> {
        let id = self
            .next_local_id
            .checked_add(1)
            .ok_or(RegistryError::Capacity)?;
        let principal = binding.principal();
        let installed = self.install(
            (None, None, principal.device_id.clone()),
            id,
            binding.clone(),
            Arc::new(Mutex::new(Admission::default())),
            &principal.endpoint_id,
            clock,
        )?;
        self.next_local_id = id;
        Ok(installed)
    }
    pub fn retire(&mut self, lease: &Arc<Lease>) -> bool {
        let Some(entry) = self.entries.get_mut(&lease.key) else {
            return false;
        };
        if !entry
            .active
            .as_ref()
            .is_some_and(|active| Arc::ptr_eq(active, lease))
        {
            return false;
        }
        if let Some(identity) = lease.binding.application_identity() {
            self.transports.remove(&identity.transport_id);
        }
        lease.revoke();
        entry.active = None;
        true
    }
    pub fn revoke_local(&mut self, device: &str, minimum_epoch: u64) -> Option<Arc<Lease>> {
        self.revoke_key(&(None, None, device.into()), minimum_epoch)
    }
    fn revoke_key(&mut self, key: &DeviceKey, minimum_epoch: u64) -> Option<Arc<Lease>> {
        let entry = self.entries.get_mut(key)?;
        entry.epoch = entry.epoch.max(minimum_epoch);
        let active = entry.active.as_ref()?;
        if active.binding.principal.authorization_epoch >= minimum_epoch {
            return None;
        }
        active.revoke();
        Some(active.clone())
    }
    pub fn revoke_owned(
        &mut self,
        device: &str,
        minimum_epoch: u64,
        transports: &[String],
    ) -> Vec<Arc<Lease>> {
        let keys: BTreeSet<_> = transports
            .iter()
            .filter_map(|transport| self.transports.get(transport).and_then(Weak::upgrade))
            .filter(|lease| lease.binding.principal.device_id == device)
            .map(|lease| lease.key.clone())
            .collect();
        keys.iter()
            .filter_map(|key| self.revoke_key(key, minimum_epoch))
            .collect()
    }
    pub fn clear(&mut self) {
        for entry in self.entries.values() {
            if let Some(active) = &entry.active {
                active.revoke();
            }
        }
        self.entries.clear();
        self.endpoint_devices.clear();
        self.transports.clear();
    }
}
impl Drop for Registry {
    fn drop(&mut self) {
        self.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn binding(endpoint: &str, device: &str, generation: u64, epoch: u64) -> Arc<VerifiedBinding> {
        VerifiedBinding::application(
            Principal {
                endpoint_id: endpoint.into(),
                device_id: device.into(),
                owner_id: "synthetic-owner".into(),
                conversation_id: "synthetic-conversation".into(),
                generation,
                authorization_epoch: epoch,
                expires_at_ms: 1000,
                scopes: vec!["chat".into(), "browser".into()],
            },
            "synthetic-user".into(),
            format!("synthetic-transport-{endpoint}-{generation}"),
            "iroh".into(),
        )
        .unwrap()
    }
    #[test]
    fn replacement_and_delayed_retirement_share_one_live_admission() {
        let mut index = Registry::default();
        let first = index
            .install_local(
                binding("synthetic-endpoint", "synthetic-device", 1, 2),
                Some(500),
            )
            .unwrap()
            .current;
        let next = index
            .install_local(
                binding("synthetic-endpoint", "synthetic-device", 2, 2),
                Some(500),
            )
            .unwrap();
        assert!(Arc::ptr_eq(next.previous.as_ref().unwrap(), &first));
        assert!(!first.check(None, Some(500)) && !index.retire(&first));
        assert!(next.current.check(Some("browser"), Some(500)));
        assert!(!next.current.check(Some("control"), Some(500)));
        assert!(!next.current.check(None, Some(1000)));
        assert!(index.retire(&next.current));
        assert!(index
            .install_local(
                binding("synthetic-endpoint", "synthetic-device", 2, 2),
                Some(500)
            )
            .is_err());
    }
    #[test]
    fn revocation_fences_future_generations_and_cannot_reach_another_authority() {
        let mut index = Registry::default();
        let local = index
            .install_local(
                binding("synthetic-local", "synthetic-device", 1, 2),
                Some(500),
            )
            .unwrap()
            .current;
        for (id, remote, room) in [
            (7, "synthetic-remote-a", None),
            (8, "synthetic-remote-b", None),
            (9, "synthetic-remote-a", Some("synthetic-room-a")),
            (10, "synthetic-remote-a", Some("synthetic-room-b")),
        ] {
            let key = (
                Some(remote.into()),
                room.map(str::to_string),
                "synthetic-device".into(),
            );
            let base = binding(remote, "synthetic-device", 1, 2);
            let mut principal = (**base.principal()).clone();
            if let Some(room) = room {
                principal.conversation_id = room.into();
            }
            let bound = VerifiedBinding::application(
                principal,
                "synthetic-user".into(),
                format!("synthetic-transport-{id}"),
                "iroh".into(),
            )
            .unwrap();
            index
                .install(
                    key,
                    id,
                    bound,
                    Arc::new(Mutex::new(Admission::default())),
                    remote,
                    Some(500),
                )
                .unwrap();
        }
        assert!(index.revoke_local("synthetic-device", 3).is_some());
        assert!(!local.check(None, Some(500)));
        assert!(index
            .install_local(
                binding("synthetic-local", "synthetic-device", 2, 2),
                Some(500)
            )
            .is_err());
        assert!(index
            .install_local(
                binding("synthetic-local", "synthetic-device", 2, 3),
                Some(500)
            )
            .is_ok());
        assert!(index
            .entries
            .iter()
            .filter(|(key, _)| key.0.is_some())
            .all(|(_, entry)| entry.active.as_ref().unwrap().check(None, Some(500))));
        let revoked = index.revoke_owned(
            "synthetic-device",
            3,
            &[
                "synthetic-transport-9".into(),
                "synthetic-transport-9".into(),
                "synthetic-missing".into(),
            ],
        );
        assert_eq!(revoked.len(), 1);
        for (key, entry) in &index.entries {
            assert_eq!(
                entry.active.as_ref().unwrap().check(None, Some(500)),
                key.1.as_deref() != Some("synthetic-room-a")
            );
        }
    }
    #[test]
    fn rejected_replacement_does_not_advance_floor_or_revoke_valid_owner() {
        let mut index = Registry::default();
        let first = index
            .install_local(
                binding("synthetic-endpoint", "synthetic-device", 1, 2),
                Some(500),
            )
            .unwrap()
            .current;
        let bad = VerifiedBinding::application(
            (**first.binding.principal()).clone(),
            "synthetic-other-user".into(),
            "synthetic-replacement".into(),
            "iroh".into(),
        )
        .unwrap();
        let mut principal = (**bad.principal()).clone();
        principal.generation = 2;
        let bad = VerifiedBinding::application(
            principal,
            "synthetic-other-user".into(),
            "synthetic-replacement".into(),
            "iroh".into(),
        )
        .unwrap();
        assert!(index.install_local(bad, Some(500)).is_err());
        assert!(first.check(None, Some(500)));
        assert!(index
            .install_local(
                binding("synthetic-endpoint", "synthetic-device", 2, 2),
                Some(500)
            )
            .is_ok());
    }
    #[test]
    fn terminal_state_clear_and_scope_order_cannot_revive_admission() {
        let mut index = Registry::default();
        let first = index
            .install_local(
                binding("synthetic-endpoint", "synthetic-device", 1, 2),
                Some(500),
            )
            .unwrap()
            .current;
        assert!(first.transition("changing_path", Some(500)) && first.check(None, Some(500)));
        assert!(!first.transition("synthetic-invalid-state", Some(500)));
        assert!(first.transition("user_disconnected", Some(500)) && !first.check(None, Some(500)));
        assert!(!first.transition("online", Some(500)));
        let next = index
            .install_local(
                binding("synthetic-endpoint", "synthetic-device", 2, 2),
                Some(500),
            )
            .unwrap()
            .current;
        index.clear();
        assert!(!next.check(None, Some(500)));
        let mut principal = (**next.binding.principal()).clone();
        principal.scopes.reverse();
        assert!(
            VerifiedBinding::application(
                principal,
                "synthetic-user".into(),
                next.binding
                    .application_identity()
                    .unwrap()
                    .transport_id
                    .clone(),
                "iroh".into()
            )
            .unwrap()
                == next.binding
        );
    }

    #[test]
    fn socket_and_callback_checks_share_one_state_through_same_socket_reauthorization() {
        let mut index = Registry::default();
        let state = Arc::new(Mutex::new(Admission::default()));
        let key = (None, None, "synthetic-device".into());
        let first = index
            .install(
                key.clone(),
                7,
                binding("synthetic-endpoint", "synthetic-device", 1, 2),
                state.clone(),
                "synthetic-endpoint",
                Some(500),
            )
            .unwrap()
            .current;
        let next = index
            .install(
                key,
                7,
                binding("synthetic-endpoint", "synthetic-device", 2, 2),
                state.clone(),
                "synthetic-endpoint",
                Some(500),
            )
            .unwrap()
            .current;
        assert!(!first.check(None, Some(500)) && next.check(Some("chat"), Some(500)));
        assert!(!index.retire(&first));
        assert!(state
            .lock()
            .unwrap()
            .check(&next.header(), Some("chat"), 500)
            .is_ok());
        assert!(next.transition("revoked", Some(500)));
        assert!(state
            .lock()
            .unwrap()
            .check(&next.header(), Some("chat"), 500)
            .is_err());
    }

    #[test]
    fn transport_ids_and_retirement_cannot_cross_owners_or_registries() {
        let mut index = Registry::default();
        let first = index
            .install_local(
                binding("synthetic-endpoint", "synthetic-device", 1, 2),
                Some(500),
            )
            .unwrap()
            .current;
        let other = binding("synthetic-other-endpoint", "synthetic-other-device", 1, 2);
        let collision = VerifiedBinding::application(
            (**other.principal()).clone(),
            "synthetic-user".into(),
            first
                .binding
                .application_identity()
                .unwrap()
                .transport_id
                .clone(),
            "iroh".into(),
        )
        .unwrap();
        assert!(index.install_local(collision, Some(500)).is_err());
        assert!(index.install_local(other, Some(500)).is_ok());
        let mut other_index = Registry::default();
        let other_lease = other_index
            .install_local(
                binding("synthetic-endpoint", "synthetic-device", 1, 2),
                Some(500),
            )
            .unwrap()
            .current;
        assert!(!index.retire(&other_lease) && !other_index.retire(&first));
        assert!(first.check(None, Some(500)) && other_lease.check(None, Some(500)));
    }

    #[test]
    fn retired_devices_keep_bounded_generation_and_endpoint_floors() {
        let mut index = Registry::default();
        for id in 0..MAX_DEVICE_FLOORS {
            let lease = index
                .install_local(
                    binding(
                        &format!("synthetic-endpoint-{id}"),
                        &format!("synthetic-device-{id}"),
                        1,
                        2,
                    ),
                    Some(500),
                )
                .unwrap()
                .current;
            assert!(index.retire(&lease));
        }
        assert!(index.transports.is_empty());
        assert!(matches!(
            index.install_local(
                binding("synthetic-extra", "synthetic-extra", 1, 2),
                Some(500)
            ),
            Err(RegistryError::Capacity)
        ));
        assert!(index
            .install_local(
                binding("synthetic-endpoint-0", "synthetic-device-0", 2, 2),
                Some(500)
            )
            .is_ok());
        assert!(index
            .install_local(
                binding("synthetic-moved-endpoint", "synthetic-device-1", 2, 2),
                Some(500)
            )
            .is_err());
    }

    #[test]
    fn expiry_during_admission_lock_wait_does_not_advance_any_floor() {
        let original = binding("synthetic-endpoint", "synthetic-device", 1, 2);
        let mut principal = (**original.principal()).clone();
        principal.expires_at_ms = now_ms() + 50;
        let binding = VerifiedBinding::application(
            principal,
            "synthetic-user".into(),
            "synthetic-transport".into(),
            "iroh".into(),
        )
        .unwrap();
        let admission = Arc::new(Mutex::new(Admission::default()));
        let worker_admission = admission.clone();
        let held = admission.lock().unwrap();
        let (started, received) = std::sync::mpsc::channel();
        let worker = std::thread::spawn(move || {
            let mut index = Registry::default();
            started.send(()).unwrap();
            assert!(matches!(
                index.install(
                    (None, None, "synthetic-device".into()),
                    1,
                    binding,
                    worker_admission,
                    "synthetic-endpoint",
                    None
                ),
                Err(RegistryError::Denied)
            ));
            assert!(
                index.entries.is_empty()
                    && index.endpoint_devices.is_empty()
                    && index.transports.is_empty()
            );
        });
        received
            .recv_timeout(std::time::Duration::from_secs(5))
            .unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        drop(held);
        worker.join().unwrap();
    }
}
