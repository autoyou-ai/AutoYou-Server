// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Protected application policy transactions. Foreign hosts supply small command
//! metadata and their platform key; policy does not materialize JSON in the host.

use crate::{client::{ClientError, ClientGrant}, client_store, core_routing,
    grant_store::{GrantError, GrantStore}, host::{EndpointPolicy, HostError},
    protected_state::{ProtectedStateTransaction, StateError}};
use serde::Deserialize;
use serde_json::value::RawValue;
use std::collections::BTreeSet;
use zeroize::Zeroizing;

const MAX_COMMAND: usize = 512 * 1024;
const SERVER_EMPTY: &[u8] = br#"{"schema":1,"devices":{}}"#;
const CLIENT_EMPTY: &[u8] = br#"{"schema":2,"devices":{}}"#;
const CORE_EMPTY: &[u8] = br#"{"schema":1,"clock_floor_ms":0,"records":{}}"#;

#[derive(Debug, thiserror::Error)]
pub enum AuthorizationError {
    #[error("{0}")] Denied(&'static str),
    #[error("protected authorization state is invalid")] InvalidState,
    #[error(transparent)] Storage(#[from] StateError),
}
impl From<GrantError> for AuthorizationError {
    fn from(value: GrantError) -> Self { match value {
        GrantError::InvalidState => Self::InvalidState,
        GrantError::Denied(reason) => Self::Denied(reason),
    } }
}
type Result<T> = std::result::Result<T, AuthorizationError>;

pub enum AuthorizationValue {
    Unit,
    Peer { grant: ClientGrant, generation: u64, core_device_id: Option<String> },
    Identity { grant: Option<ClientGrant> },
    Epoch { epoch: u64 },
    AuthorityDevice { device_id: Option<String> },
    AuthorityDevices { devices: Vec<(String, String)> },
    Changed { changed: bool },
    Revocations { devices: Vec<(String, u64)> },
    Routing { epoch: u64, expires_at_ms: u64, ticket: String, owner_id: String },
    Relays { configuration: core_routing::RelayConfiguration, endpoint_id: String },
}

#[derive(Deserialize)]
#[serde(tag="operation", rename_all="snake_case", deny_unknown_fields)]
enum ServerQuery {
    Peer { endpoint_id: String },
    Identity { endpoint_id: String },
    Epoch { device_id: String },
    AuthorityDevice { endpoint_id: String },
    AuthorityDevices { devices: Option<BTreeSet<String>> },
}
#[derive(Deserialize)]
#[serde(rename_all="snake_case", deny_unknown_fields)]
enum Query {
    Server { query: ServerQuery },
    Client { endpoint_id: String },
}
#[derive(Deserialize)]
#[serde(tag="operation", rename_all="snake_case", deny_unknown_fields)]
enum ServerUpdate {
    Register { grant: ClientGrant, core_device_id: Option<String> },
    Binding { endpoint_id: String, transport_id: String },
    RemoteBinding { endpoint_id: String, transport_id: String, generation: u64, authorization_epoch: u64,
        expires_at_ms: u64, scopes: Vec<String> },
    Revoke { device_id: String, authorization_epoch: u64 },
    RevokeOrigin { origin: String, core_device_id: Option<String> },
}
#[derive(Deserialize)]
#[serde(tag="operation", rename_all="snake_case", deny_unknown_fields)]
enum ClientUpdate {
    Register { grant: ClientGrant, core_device_id: Option<String> },
    RemoteBinding { endpoint_id: String, transport_id: String, generation: u64, authorization_epoch: u64,
        expires_at_ms: u64, scopes: Vec<String> },
    Revoke { device_id: String, authorization_epoch: u64 },
    DenyCore { endpoint_id: String },
    AssociateCore { endpoint_id: String, device_id: Option<String> },
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CoreUpdate {
    envelope: Box<RawValue>, public_key: String, issuer: String, owner_id: Option<String>,
    device_id: String, endpoint_id: String, policy: EndpointPolicy,
}
#[derive(Deserialize)]
// External tags retain raw signed envelopes without buffering a JSON tree.
#[serde(rename_all="snake_case", deny_unknown_fields)]
enum Update {
    Server { update: ServerUpdate },
    Client { update: ClientUpdate },
    CoreRouting { update: CoreUpdate },
    CoreRelays { update: CoreUpdate },
}

fn command<'a, T: Deserialize<'a>>(json: &'a [u8]) -> Result<T> {
    if json.len() > MAX_COMMAND { return Err(AuthorizationError::Denied("authorization command exceeds its bound")); }
    serde_json::from_slice(json).map_err(|_| AuthorizationError::Denied("invalid authorization command"))
}
fn plaintext(transaction: &mut ProtectedStateTransaction, key: Option<&[u8; 32]>) -> Result<Option<Zeroizing<Vec<u8>>>> {
    if !transaction.has_state() { return Ok(None); }
    let key = key.ok_or(AuthorizationError::Storage(StateError::Unavailable))?;
    Ok(Some(Zeroizing::new(transaction.read(key)?)))
}
fn server(bytes: Option<&[u8]>) -> Result<GrantStore> {
    Ok(match bytes { Some(bytes) => GrantStore::decode(bytes)?, None => GrantStore::default() })
}
fn client(bytes: Option<&[u8]>) -> Result<client_store::State> {
    match bytes { Some(bytes) => client_store::decode(bytes).map_err(|_| AuthorizationError::InvalidState),
        None => Ok(client_store::State::empty()) }
}
fn client_result<T>(value: std::result::Result<T, ClientError>) -> Result<T> {
    value.map_err(|_| AuthorizationError::Denied("client grant is stale or outside its paired scope"))
}
fn peer(value: crate::grant_store::GrantPeer) -> AuthorizationValue {
    AuthorizationValue::Peer { grant: value.grant, generation: value.generation, core_device_id: value.core_device_id }
}
fn client_peer(value: client_store::StoredPeer) -> AuthorizationValue {
    AuthorizationValue::Peer { grant: value.grant, generation: value.generation_floor, core_device_id: value.core_device }
}

pub fn validate_grant(json: &[u8]) -> Result<()> {
    if json.len() > 64 * 1024 { return Err(AuthorizationError::Denied("invalid verified pairing identity")); }
    let grant: ClientGrant = serde_json::from_slice(json).map_err(|_| AuthorizationError::Denied("invalid verified pairing identity"))?;
    Ok(crate::grant_store::validate_grant(&grant)?)
}

pub fn query(mut transaction: ProtectedStateTransaction, key: Option<&[u8; 32]>, json: &[u8], now_ms: u64) -> Result<AuthorizationValue> {
    let query: Query = command(json)?;
    let plain = plaintext(&mut transaction, key)?;
    let bytes = plain.as_ref().map(|bytes| bytes.as_slice());
    match query {
        Query::Client { endpoint_id } => Ok(client_peer(client_result(client(bytes)?.load(&endpoint_id, now_ms))?)),
        Query::Server { query } => {
            let state = server(bytes)?;
            Ok(match query {
                ServerQuery::Peer { endpoint_id } => peer(state.peer(&endpoint_id, now_ms)?),
                ServerQuery::Identity { endpoint_id } => AuthorizationValue::Identity { grant: state.pairing_identity(&endpoint_id) },
                ServerQuery::Epoch { device_id } => AuthorizationValue::Epoch { epoch: state.authorization_epoch(&device_id) },
                ServerQuery::AuthorityDevice { endpoint_id } => AuthorizationValue::AuthorityDevice { device_id: state.authority_device(&endpoint_id) },
                ServerQuery::AuthorityDevices { devices } => AuthorizationValue::AuthorityDevices { devices: state.authority_devices(devices.as_ref()) },
            })
        }
    }
}

pub struct PreparedAuthorization {
    transaction: ProtectedStateTransaction,
    after: Zeroizing<Vec<u8>>,
    initial: &'static [u8],
    value: AuthorizationValue,
}
impl PreparedAuthorization {
    pub fn commit(self, key: &[u8; 32]) -> std::result::Result<AuthorizationValue, StateError> {
        self.transaction.commit(key, &self.after, self.initial)?;
        Ok(self.value)
    }
}

pub fn prepare(mut transaction: ProtectedStateTransaction, key: Option<&[u8; 32]>, json: &[u8], now_ms: u64) -> Result<PreparedAuthorization> {
    let update: Update = command(json)?;
    let plain = plaintext(&mut transaction, key)?;
    let bytes = plain.as_ref().map(|bytes| bytes.as_slice());
    let (after, initial, value) = match update {
        Update::Server { update } => {
            let mut state = server(bytes)?;
            let value = match update {
                ServerUpdate::Register { grant, core_device_id } => { state.register(grant, core_device_id, now_ms)?; AuthorizationValue::Unit },
                ServerUpdate::Binding { endpoint_id, transport_id } => peer(state.binding(&endpoint_id, &transport_id, now_ms)?),
                ServerUpdate::RemoteBinding { endpoint_id, transport_id, generation, authorization_epoch, expires_at_ms, scopes } =>
                    peer(state.remote_binding(&endpoint_id, &transport_id, generation, authorization_epoch, expires_at_ms, scopes, now_ms)?),
                ServerUpdate::Revoke { device_id, authorization_epoch } => AuthorizationValue::Changed { changed: state.revoke(&device_id, authorization_epoch)? },
                ServerUpdate::RevokeOrigin { origin, core_device_id } => AuthorizationValue::Revocations { devices: state.revoke_origin(&origin, core_device_id.as_deref())? },
            };
            (state.encode()?, SERVER_EMPTY, value)
        }
        Update::Client { update } => {
            let mut state = client(bytes)?;
            let value = match update {
                ClientUpdate::Register { grant, core_device_id } => {
                    let endpoint = grant.endpoint_id.clone();
                    client_result(state.register(grant, now_ms))?;
                    client_result(state.associate_core_endpoint(&endpoint, core_device_id.as_deref()))?;
                    AuthorizationValue::Unit
                },
                ClientUpdate::RemoteBinding { endpoint_id, transport_id, generation, authorization_epoch, expires_at_ms, scopes } => {
                    if transport_id.is_empty() || transport_id.len() > 512 { return Err(AuthorizationError::Denied("invalid verified session identity")); }
                    client_peer(client_result(state.remote_binding(&endpoint_id, generation, authorization_epoch, expires_at_ms, scopes, now_ms))?)
                },
                ClientUpdate::Revoke { device_id, authorization_epoch } => {
                    client_result(state.revoke(&device_id, authorization_epoch))?; AuthorizationValue::Changed { changed: true }
                },
                ClientUpdate::DenyCore { endpoint_id } => { client_result(state.deny_core_endpoint(&endpoint_id))?; AuthorizationValue::Unit },
                ClientUpdate::AssociateCore { endpoint_id, device_id } => {
                    client_result(state.associate_core_endpoint(&endpoint_id, device_id.as_deref()))?; AuthorizationValue::Unit
                },
            };
            (client_store::encode(&state).map_err(|_| AuthorizationError::InvalidState)?, CLIENT_EMPTY, value)
        }
        Update::CoreRouting { update } => {
            let current = bytes.unwrap_or(CORE_EMPTY);
            let result = if let Some(owner) = update.owner_id {
                core_routing::accept_routing_record(current, update.envelope.get(), &update.public_key, &update.issuer,
                    &owner, &update.device_id, &update.endpoint_id, now_ms, &update.policy).map(|(record, saved)| (record, saved, owner))
            } else {
                core_routing::accept_device_routing_record(current, update.envelope.get(), &update.public_key, &update.issuer,
                    &update.device_id, &update.endpoint_id, now_ms, &update.policy)
            };
            let (record, saved, owner_id) = core_result(result)?;
            (saved, CORE_EMPTY, AuthorizationValue::Routing { epoch: record.epoch, expires_at_ms: record.expires_at_ms, ticket: record.ticket, owner_id })
        }
        Update::CoreRelays { update } => {
            let owner = update.owner_id.ok_or(AuthorizationError::Denied("relay authority requires its pinned owner"))?;
            let (record, saved) = core_result(core_routing::accept_relay_configuration(bytes.unwrap_or(CORE_EMPTY), update.envelope.get(),
                &update.public_key, &update.issuer, &owner, &update.device_id, &update.endpoint_id, now_ms, &update.policy))?;
            (saved, CORE_EMPTY, AuthorizationValue::Relays { configuration: record, endpoint_id: update.endpoint_id })
        }
    };
    Ok(PreparedAuthorization { transaction, after: Zeroizing::new(after), initial, value })
}
fn core_result<T>(result: std::result::Result<T, HostError>) -> Result<T> {
    result.map_err(|error| match error {
        HostError::InvalidConfig => AuthorizationError::InvalidState,
        _ => AuthorizationError::Denied("Core routing record was rejected"),
    })
}

pub fn prepare_client_compare(mut transaction: ProtectedStateTransaction, key: Option<&[u8; 32]>,
    expected: &[u8], updated: Vec<u8>, endpoint_id: Option<&str>, core_device_id: Option<&str>) -> Result<PreparedAuthorization> {
    let plain = plaintext(&mut transaction, key)?;
    let current = plain.as_ref().map_or(CLIENT_EMPTY, |bytes| bytes.as_slice());
    let expected = client_store::decode(expected).map_err(|_| AuthorizationError::InvalidState)?;
    let current = client_store::decode(current).map_err(|_| AuthorizationError::Denied("peer grants changed before the protected transaction"))?;
    if expected != current { return Err(AuthorizationError::Denied("peer grants changed before the protected transaction")); }
    let mut proposed = client_store::decode(&updated).map_err(|_| AuthorizationError::InvalidState)?;
    let updated = if let Some(endpoint) = endpoint_id {
        client_result(proposed.associate_core_endpoint(endpoint, core_device_id))?;
        client_store::encode(&proposed).map_err(|_| AuthorizationError::InvalidState)?
    } else { updated };
    Ok(PreparedAuthorization { transaction, after: Zeroizing::new(updated), initial: CLIENT_EMPTY, value: AuthorizationValue::Unit })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, path::{Path,PathBuf}, sync::atomic::{AtomicU64,Ordering}};
    static NEXT:AtomicU64=AtomicU64::new(1);
    const KEY:[u8;32]=[29;32];
    fn root()->PathBuf {
        let base=PathBuf::from(std::env::var_os("AUTOYOU_TEST_ROOT").expect("isolated authorization test root required"));
        let root=base.join(format!("synthetic-authorization-{}-{}",std::process::id(),NEXT.fetch_add(1,Ordering::Relaxed)));
        fs::create_dir_all(&root).unwrap();root
    }
    fn begin(root:&Path)->ProtectedStateTransaction { ProtectedStateTransaction::begin(root.into()).unwrap() }
    fn grant()->ClientGrant {
        ClientGrant { endpoint_id:iroh::SecretKey::from_bytes(&[41;32]).public().to_string(),
            device_id:"synthetic-device".into(),owner_key:"synthetic-owner".into(),canonical_user_id:"synthetic-user".into(),
            conversation_key:"synthetic-conversation".into(),origin_transport:"local".into(),origin_sender_id:"synthetic-origin".into(),
            pairing_mode:"pair".into(),device_ownership:"own".into(),authorization_epoch:2,expires_at_ms:10_000,scopes:vec!["chat".into()] }
    }
    fn update(root:&Path,domain:&str,operation:serde_json::Value)->Result<AuthorizationValue> {
        prepare(begin(root),Some(&KEY),&serde_json::to_vec(&serde_json::json!({(domain):{"update":operation}})).unwrap(),1000)?
            .commit(&KEY).map_err(Into::into)
    }
    #[test]
    fn policy_preparation_retains_the_lease_and_failed_commit_confers_no_counter() {
        let root=root();let g=grant();
        update(&root,"server",serde_json::json!({"operation":"register","grant":g})).unwrap();
        let before=fs::read(root.join("state/state.enc")).unwrap();
        let command=serde_json::to_vec(&serde_json::json!({"server":{"update":{
            "operation":"binding","endpoint_id":g.endpoint_id,"transport_id":"synthetic-connection"}}})).unwrap();
        let prepared=prepare(begin(&root),Some(&KEY),&command,1000).unwrap();
        let other=root.clone(); std::thread::spawn(move || {
            assert!(matches!(ProtectedStateTransaction::begin(other),Err(StateError::Busy)));
        }).join().unwrap();
        assert!(matches!(prepared.commit(&[30;32]),Err(StateError::Unavailable)));
        assert_eq!(fs::read(root.join("state/state.enc")).unwrap(),before);
        let value=prepare(begin(&root),Some(&KEY),&command,1000).unwrap().commit(&KEY).unwrap();
        assert!(matches!(value,AuthorizationValue::Peer{generation:1,..}));
        let mut plain=begin(&root); let saved=plain.read(&KEY).unwrap(); drop(plain);
        assert_eq!(GrantStore::decode(&saved).unwrap().peer(&g.endpoint_id,1000).unwrap().generation,1);
    }
    #[test]
    fn rejected_commands_never_create_state_and_keep_json_types_strict() {
        let root=root();let mut g=grant();g.expires_at_ms=1000;
        assert!(update(&root,"server",serde_json::json!({"operation":"register","grant":g})).is_err());
        for command in [br#"{"server":{"update":{"operation":"revoke","device_id":"synthetic-device","authorization_epoch":true}}}"#.as_slice(),
            br#"{"server":{"query":{"operation":"peer","endpoint_id":"synthetic-device"}}}"#] {
            assert!(prepare(begin(&root),None,command,1000).is_err());
        }
        assert!(!root.join("state/state.enc").exists() && !root.join("state/rollback-baseline.enc").exists());
        assert!(matches!(query(begin(&root),None,br#"{"server":{"query":{"operation":"epoch","device_id":"synthetic-device"}}}"#,1000).unwrap(),
            AuthorizationValue::Epoch{epoch:0}));
    }
    #[test]
    fn client_registration_and_core_marker_commit_as_one_authority_change() {
        let root=root();let g=grant();
        update(&root,"client",serde_json::json!({"operation":"register","grant":g,"core_device_id":"client000000001"})).unwrap();
        let mut read=begin(&root);let saved=read.read(&KEY).unwrap();drop(read);
        assert_eq!(client_store::load(&saved,&g.endpoint_id,1000).unwrap().core_device.as_deref(),Some("client000000001"));
        let before=fs::read(root.join("state/state.enc")).unwrap();
        assert!(update(&root,"client",serde_json::json!({"operation":"register","grant":g,"core_device_id":"invalid"})).is_err());
        assert_eq!(fs::read(root.join("state/state.enc")).unwrap(),before);
        update(&root,"client",serde_json::json!({"operation":"register","grant":g})).unwrap();
        let mut read=begin(&root);let saved=read.read(&KEY).unwrap();
        assert!(client_store::load(&saved,&g.endpoint_id,1000).unwrap().core_device.is_none());
    }
    #[test]
    fn protected_core_commands_accept_signed_raw_envelopes_and_commit_floors() {
        let root=root();
        let fixture:serde_json::Value=serde_json::from_str(include_str!("../../tests/fixtures/core-routing-v1.json")).unwrap();
        let policy=serde_json::json!({"bind_addresses":["127.0.0.1:0"],"local_only":true,
            "brokered_relays":true,"relays":[{"url":"http://127.0.0.1:32123/"}]});
        for (domain,envelope) in [("core_routing","routing_envelope"),("core_relays","relay_envelope")] {
            let command=serde_json::to_vec(&serde_json::json!({(domain):{"update":{
                "envelope":fixture[envelope],"public_key":fixture["core_public_key"],"issuer":"https://core.example.invalid",
                "owner_id":"account00000001","device_id":"client000000001","endpoint_id":fixture["endpoint_id"],"policy":policy}}})).unwrap();
            let value=prepare(begin(&root),Some(&KEY),&command,1_800_000_000_000).unwrap().commit(&KEY).unwrap();
            match value {
                AuthorizationValue::Routing{epoch,owner_id,..} => { assert_eq!(epoch,4);assert_eq!(owner_id,"account00000001"); },
                AuthorizationValue::Relays{configuration,..} => { assert_eq!(configuration.epoch,4);assert_eq!(configuration.relays.len(),1); },
                _ => panic!("wrong Core authorization result"),
            }
        }
        let mut read=begin(&root);let saved=read.read(&KEY).unwrap();
        let state:serde_json::Value=serde_json::from_slice(&saved).unwrap();
        assert_eq!(state["clock_floor_ms"],1_800_000_000_000_u64);
        assert_eq!(state["records"].as_object().unwrap().len(),2);
        assert!(state["records"].as_object().unwrap().values().all(|row| row["epoch"]==4 && row["owner"]=="account00000001"));
    }
    #[test]
    fn compare_commit_preserves_revocations_and_rejects_corrupt_current_types() {
        let root=root();let g=grant();
        update(&root,"client",serde_json::json!({"operation":"register","grant":g})).unwrap();
        let mut read=begin(&root);let old=read.read(&KEY).unwrap();drop(read);
        update(&root,"client",serde_json::json!({"operation":"revoke","device_id":g.device_id,"authorization_epoch":3})).unwrap();
        let before=fs::read(root.join("state/state.enc")).unwrap();
        assert!(matches!(prepare_client_compare(begin(&root),Some(&KEY),&old,old.clone(),None,None),Err(AuthorizationError::Denied(_))));
        assert_eq!(fs::read(root.join("state/state.enc")).unwrap(),before);
        let mut read=begin(&root);read.read(&KEY).unwrap();read.commit(&KEY,br#"{"schema":true,"devices":{}}"#,b"").unwrap();
        assert!(matches!(prepare_client_compare(begin(&root),Some(&KEY),&old,old.clone(),None,None),Err(AuthorizationError::Denied(_))));
    }
}
