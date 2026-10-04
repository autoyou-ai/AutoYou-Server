// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! In-process endpoint with an owned runtime, bounded host queues and no
//! callbacks into a foreign interpreter/UI. The application supplies grants.

use std::{
    collections::{HashMap, VecDeque},
    net::{IpAddr, SocketAddr},
    sync::{Arc, Mutex, atomic::{AtomicBool, AtomicU64, Ordering}},
    thread::JoinHandle,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use autoyou_protocol::{Admission, Envelope, Frame, FrameHeader, HEADER_BYTES, Lane, Principal, PAIR_ALPN, SESSION_ALPN};
use iroh::{Endpoint, EndpointAddr, RelayConfig, RelayMap, RelayMode, RelayUrl, SecretKey, TransportAddr,
    endpoint::{Connection, PortmapperConfig, QuicTransportConfig, presets}};
use iroh_tickets::endpoint::EndpointTicket;
use serde::Deserialize;
use tokio::{sync::{Notify, OwnedSemaphorePermit, Semaphore, mpsc}, task::JoinSet};
use crate::{connection_binding, ordering::{OrderedReceiver, RetiredStreams}, scheduler::Scheduler, write_frame};

const MAX_CONNECTIONS: usize = 32;
const MAX_EVENTS: usize = 256;
const MAX_EVENT_BYTES: usize = 16 * 1024 * 1024;
const MAX_PREAUTH_BYTES: usize = 16 * 1024;
const MAX_PREAUTH_FRAMES: u64 = 8;
const MAX_DEVICE_FLOORS: usize = 4096;

#[derive(Debug, Clone, thiserror::Error)]
pub enum HostError {
    #[error("invalid endpoint configuration")] InvalidConfig,
    #[error("invalid or unapproved endpoint ticket")] InvalidTicket,
    #[error("endpoint queue is full")] Backpressure,
    #[error("endpoint is closed")] Closed,
    #[error("unknown connection")] UnknownConnection,
    #[error("application session authorization denied")] NotAuthorized,
    #[error("endpoint worker failed")] Worker,
    #[error("endpoint operation timed out")] Timeout,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RelayPolicy {
    pub url: String,
    pub token: String,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EndpointPolicy {
    pub bind_addresses: Vec<String>,
    #[serde(default)] pub relays: Vec<RelayPolicy>,
    #[serde(default)] pub relay_only: bool,
    #[serde(default)] pub local_only: bool,
    #[serde(default)] pub allow_lan_peers: bool,
}

impl EndpointPolicy {
    pub fn local() -> Self {
        Self { bind_addresses: vec!["127.0.0.1:0".into()], relays: vec![],
            relay_only: false, local_only: true, allow_lan_peers: false }
    }
    fn validate(&self) -> Result<(), HostError> {
        if self.bind_addresses.len() > 4 || self.relays.len() > 8 ||
            (self.relay_only && self.relays.is_empty()) ||
            (!self.relay_only && self.bind_addresses.is_empty()) {
            return Err(HostError::InvalidConfig);
        }
        if std::env::var_os("AUTOYOU_TEST_ROOT").is_some() && !self.local_only {
            return Err(HostError::InvalidConfig);
        }
        for address in &self.bind_addresses {
            let socket: SocketAddr = address.parse().map_err(|_| HostError::InvalidConfig)?;
            if self.local_only && !socket.ip().is_loopback() { return Err(HostError::InvalidConfig); }
        }
        for relay in &self.relays {
            let url: RelayUrl = relay.url.parse().map_err(|_| HostError::InvalidConfig)?;
            if relay.token.is_empty() || relay.token.len() > 16*1024 ||
                !url.username().is_empty() || url.password().is_some() ||
                url.query().is_some() || url.fragment().is_some() || url.path() != "/" {
                return Err(HostError::InvalidConfig);
            }
            if self.local_only {
                let ip: IpAddr = url.host_str().ok_or(HostError::InvalidConfig)?.trim_matches(['[', ']'])
                    .parse().map_err(|_| HostError::InvalidConfig)?;
                if !ip.is_loopback() || !matches!(url.scheme(), "http" | "https") { return Err(HostError::InvalidConfig); }
            } else if url.scheme() != "https" { return Err(HostError::InvalidConfig); }
        }
        Ok(())
    }

    fn ticket_address(&self, ticket: &str, expected_endpoint: &str) -> Result<EndpointAddr, HostError> {
        if ticket.len() > 8192 || expected_endpoint.len() > 128 { return Err(HostError::InvalidTicket); }
        let ticket: EndpointTicket = ticket.parse().map_err(|_| HostError::InvalidTicket)?;
        let mut address = ticket.endpoint_addr().clone();
        let expected = expected_endpoint.parse().map_err(|_| HostError::InvalidTicket)?;
        if address.id != expected || address.addrs.len() > 16 { return Err(HostError::InvalidTicket); }
        let approved: Vec<RelayUrl> = self.relays.iter().map(|relay| relay.url.parse().unwrap()).collect();
        if self.relay_only { address.addrs.retain(|hint| hint.is_relay()); }
        for hint in &address.addrs {
            match hint {
                TransportAddr::Relay(url) if approved.contains(url) => {}
                TransportAddr::Ip(socket) if !self.relay_only && self.allowed_ip(socket.ip()) && socket.port() != 0 => {}
                _ => return Err(HostError::InvalidTicket),
            }
        }
        if address.is_empty() { return Err(HostError::InvalidTicket); }
        Ok(address)
    }

    fn allowed_ip(&self, ip: IpAddr) -> bool {
        if self.local_only { return ip.is_loopback(); }
        if ip.is_loopback() || ip.is_unspecified() || ip.is_multicast() { return false; }
        match ip {
            IpAddr::V4(ip) => !ip.is_link_local() && !ip.is_broadcast() &&
                (self.allow_lan_peers || !ip.is_private()),
            IpAddr::V6(ip) => {
                if let Some(mapped) = ip.to_ipv4_mapped() { return self.allowed_ip(IpAddr::V4(mapped)); }
                !ip.is_unicast_link_local() && (self.allow_lan_peers || !ip.is_unique_local())
            }
        }
    }
}

#[derive(Debug)]
pub enum HostEvent {
    Connected { connection_id: u64, endpoint_id: String, initiator: bool, exporter: [u8;32], protocol: String },
    Frame { connection_id: u64, frame: Frame, allocation: OwnedSemaphorePermit },
    Closed { connection_id: u64 },
    Failed { request_id: u64, code: &'static str },
}

/// Redacted host diagnostics. Addresses, tickets, exporters and relay tokens
/// never cross this boundary or enter the application's metric dictionaries.
pub struct HostDiagnostics {
    pub generation: u64,
    pub authorization_epoch: u64,
    pub path_kind: &'static str,
    pub open_paths: u32,
    pub rtt_ms: Option<u64>,
    pub queued_send_bytes: u64,
    pub held_send_bytes: u64,
    pub held_receive_bytes: u64,
}

impl HostEvent {
    fn bytes(&self) -> usize { match self { Self::Frame { frame, .. } => frame.payload.len(), _ => 0 } }
}

#[derive(Default)]
struct Events { items: VecDeque<HostEvent>, deferred: VecDeque<HostEvent>, bytes: usize }
impl Events {
    fn push(&mut self, event: HostEvent) -> Result<(), HostError> {
        let urgent = !matches!(event, HostEvent::Frame { frame: Frame { lane: Lane::Binary | Lane::Http | Lane::Media, .. }, .. });
        let max_items = if urgent { MAX_EVENTS } else { MAX_EVENTS - 16 };
        let max_bytes = if urgent { MAX_EVENT_BYTES } else { MAX_EVENT_BYTES - 1024*1024 };
        if self.items.len() + self.deferred.len() >= max_items || self.bytes.saturating_add(event.bytes()) > max_bytes {
            return Err(HostError::Backpressure);
        }
        self.bytes += event.bytes(); self.items.push_back(event); Ok(())
    }
}

struct Outbound {
    scheduler: Scheduler, sequences: HashMap<(u8,u64),u64>,
    allocations: HashMap<(u8,u64,u64), OwnedSemaphorePermit>,
}
struct Receiver {
    ordering: OrderedReceiver,
    allocations: HashMap<(u8,u64,u64), OwnedSemaphorePermit>,
}
struct Slot {
    connection: Connection,
    admission: Mutex<Admission>,
    outbound: Mutex<Outbound>,
    receiver: Mutex<Receiver>,
    retired: Mutex<RetiredStreams>,
    activated: AtomicBool,
    preauth_frames: AtomicU64,
    preauth_bytes: AtomicU64,
    read_slots: Arc<Semaphore>,
    closed: AtomicBool,
}
struct DeviceFloor { generation: u64, epoch: u64, endpoint_id: String }
struct Shared {
    slots: Mutex<HashMap<u64, Arc<Slot>>>,
    device_floors: Mutex<HashMap<String, DeviceFloor>>,
    events: Mutex<Events>,
    info: Mutex<(String,String)>,
    closed: AtomicBool,
    next_id: AtomicU64,
    wake: Notify,
    read_bytes: Arc<Semaphore>,
    send_bytes: Arc<Semaphore>,
    write_slots: Arc<Semaphore>,
    control_slots: Arc<Semaphore>,
    scheduling_cursor: AtomicU64,
}

enum Command {
    Dial { id: u64, address: EndpointAddr, pair: bool }, NetworkChanged,
    #[cfg(test)] Crash,
}

pub struct EndpointHost {
    shared: Arc<Shared>,
    commands: mpsc::Sender<Command>,
    worker: Mutex<Option<JoinHandle<()>>>,
    policy: EndpointPolicy,
}

struct WorkerExit(Arc<Shared>);
impl Drop for WorkerExit {
    fn drop(&mut self) {
        self.0.closed.store(true, Ordering::Release);
        self.0.wake.notify_one();
    }
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis().min(u64::MAX as u128) as u64
}

pub fn endpoint_bytes(value: &str) -> Result<[u8;32], HostError> {
    if value.len() > 128 { return Err(HostError::InvalidConfig); }
    let id: iroh::EndpointId = value.parse().map_err(|_| HostError::InvalidConfig)?;
    Ok(*id.as_bytes())
}

impl EndpointHost {
    pub fn start(policy: EndpointPolicy, key: [u8;32]) -> Result<Self, HostError> {
        policy.validate()?;
        let shared = Arc::new(Shared {
            slots: Mutex::new(HashMap::new()), device_floors: Mutex::new(HashMap::new()),
            events: Mutex::new(Events::default()), info: Mutex::new((String::new(), String::new())),
            closed: AtomicBool::new(false), next_id: AtomicU64::new(1), wake: Notify::new(),
            read_bytes: Arc::new(Semaphore::new(16*1024*1024)),
            send_bytes: Arc::new(Semaphore::new(16*1024*1024)),
            write_slots: Arc::new(Semaphore::new(8)), control_slots: Arc::new(Semaphore::new(2)),
            scheduling_cursor: AtomicU64::new(0),
        });
        let (commands, receiver) = mpsc::channel(32);
        let (ready_tx, ready_rx) = std::sync::mpsc::sync_channel(1);
        let worker_shared = shared.clone();
        let worker_policy = policy.clone();
        let worker = std::thread::Builder::new().name("autoyou-iroh".into()).spawn(move || {
            let _exit = WorkerExit(worker_shared.clone());
            let runtime = match tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build() {
                Ok(runtime) => runtime,
                Err(_) => { let _ = ready_tx.send(Err(HostError::Worker)); return; }
            };
            runtime.block_on(async {
                let endpoint = match tokio::time::timeout(Duration::from_secs(8), bind_endpoint(&worker_policy, key)).await {
                    Ok(Ok(endpoint)) => endpoint,
                    Ok(Err(error)) => { let _ = ready_tx.send(Err(error)); return; }
                    Err(_) => { let _ = ready_tx.send(Err(HostError::Timeout)); return; }
                };
                update_info(&endpoint, &worker_policy, &worker_shared);
                if ready_tx.send(Ok(())).is_err() { endpoint.close().await; return; }
                run(endpoint.clone(), worker_shared.clone(), receiver, worker_policy).await;
                let _ = tokio::time::timeout(Duration::from_secs(3), endpoint.close()).await;
            });
            worker_shared.closed.store(true, Ordering::Release);
            runtime.shutdown_timeout(Duration::from_secs(2));
        }).map_err(|_| HostError::Worker)?;
        match ready_rx.recv_timeout(Duration::from_secs(10)) {
            Ok(Ok(())) => Ok(Self { shared, commands, worker: Mutex::new(Some(worker)), policy }),
            Ok(Err(error)) => { let _ = worker.join(); Err(error) }
            Err(_) => {
                shared.closed.store(true, Ordering::Release); shared.wake.notify_one();
                let _ = worker.join(); Err(HostError::Timeout)
            }
        }
    }

    pub fn endpoint_info(&self) -> Result<(String,String), HostError> {
        self.open()?; self.shared.info.lock().map(|info| info.clone()).map_err(|_| HostError::Worker)
    }
    fn open(&self) -> Result<(), HostError> {
        if self.shared.closed.load(Ordering::Acquire) { Err(HostError::Closed) } else { Ok(()) }
    }
    fn slot(&self, id: u64) -> Result<Arc<Slot>, HostError> {
        self.open()?;
        let slot = self.shared.slots.lock().map_err(|_| HostError::Worker)?.get(&id).cloned().ok_or(HostError::UnknownConnection)?;
        if slot.closed.load(Ordering::Acquire) { Err(HostError::Closed) } else { Ok(slot) }
    }
    pub fn dial(&self, ticket: &str, expected_endpoint: &str, pair: bool) -> Result<u64, HostError> {
        self.open()?;
        let address = self.policy.ticket_address(ticket, expected_endpoint)?;
        let id = self.shared.next_id.fetch_add(1, Ordering::Relaxed);
        self.commands.try_send(Command::Dial { id, address, pair }).map_err(|_| HostError::Backpressure)?;
        Ok(id)
    }
    pub fn admit(&self, id: u64, principal: Principal) -> Result<(), HostError> {
        principal.validate().map_err(|_| HostError::NotAuthorized)?;
        if principal.expires_at_ms <= now_ms() { return Err(HostError::NotAuthorized); }
        let slot = self.slot(id)?;
        if slot.connection.alpn() != SESSION_ALPN { return Err(HostError::NotAuthorized); }
        let mut floors = self.shared.device_floors.lock().map_err(|_| HostError::Worker)?;
        if let Some(floor) = floors.get(&principal.device_id) {
            if principal.generation <= floor.generation || principal.authorization_epoch < floor.epoch ||
                principal.endpoint_id != floor.endpoint_id { return Err(HostError::NotAuthorized); }
        } else if floors.len() >= MAX_DEVICE_FLOORS { return Err(HostError::Backpressure); }
        if floors.iter().any(|(device, floor)| device != &principal.device_id && floor.endpoint_id == principal.endpoint_id) {
            return Err(HostError::NotAuthorized);
        }
        slot.admission.lock().map_err(|_| HostError::Worker)?
            .admit(principal.clone(), &slot.connection.remote_id().to_string()).map_err(|_| HostError::NotAuthorized)?;
        floors.insert(principal.device_id.clone(), DeviceFloor { generation: principal.generation,
            epoch: principal.authorization_epoch, endpoint_id: principal.endpoint_id.clone() });
        for (old_id, old) in self.shared.slots.lock().map_err(|_| HostError::Worker)?.iter() {
            if *old_id != id {
                let mut admission = old.admission.lock().map_err(|_| HostError::Worker)?;
                if matches!(&*admission, Admission::Admitted(old) if old.device_id == principal.device_id) {
                    admission.revoke(); old.connection.close(1u32.into(), b"session superseded");
                }
            }
        }
        Ok(())
    }
    pub fn send(&self, id: u64, mut frame: Frame, deadline_ms: Option<u64>) -> Result<(), HostError> {
        if deadline_ms.is_some() && frame.lane != Lane::Media { return Err(HostError::InvalidConfig); }
        let slot = self.slot(id)?;
        if frame.lane != Lane::Enrollment && !slot.activated.load(Ordering::Acquire) {
            return Err(HostError::NotAuthorized);
        }
        authorize_header(&slot, &FrameHeader { lane: frame.lane, generation: frame.generation,
            stream_id: frame.stream_id, sequence: 0, length: frame.payload.len() }, false)?;
        authorize_payload(&slot, &frame)?;
        let mut outbound = slot.outbound.lock().map_err(|_| HostError::Worker)?;
        let key = (frame.lane as u8, frame.stream_id);
        if !outbound.sequences.contains_key(&key) && outbound.sequences.len() >= 128 { return Err(HostError::Backpressure); }
        let sequence = *outbound.sequences.get(&key).unwrap_or(&0);
        let next = sequence.checked_add(1).ok_or(HostError::Closed)?;
        let allocation = self.shared.send_bytes.clone().try_acquire_many_owned(frame.payload.len() as u32)
            .map_err(|_| HostError::Backpressure)?;
        frame.sequence = sequence;
        outbound.scheduler.push(frame, deadline_ms).map_err(|_| HostError::Backpressure)?;
        outbound.allocations.insert((key.0,key.1,sequence), allocation);
        outbound.sequences.insert(key, next);
        self.shared.wake.notify_one(); Ok(())
    }
    pub fn activate(&self, id: u64) -> Result<(), HostError> {
        let slot = self.slot(id)?;
        let header = FrameHeader { lane: Lane::Control, generation: match &*slot.admission.lock().map_err(|_| HostError::Worker)? {
            Admission::Admitted(principal) => principal.generation, _ => return Err(HostError::NotAuthorized),
        }, stream_id: 0, sequence: 0, length: 0 };
        slot.admission.lock().map_err(|_| HostError::Worker)?.check(&header, None, now_ms()).map_err(|_| HostError::NotAuthorized)?;
        slot.activated.store(true, Ordering::Release);
        Ok(())
    }
    pub fn poll(&self, maximum: u32) -> Result<Vec<HostEvent>, HostError> {
        self.open()?;
        if maximum == 0 || maximum > 64 { return Err(HostError::InvalidConfig); }
        let mut events = self.shared.events.lock().map_err(|_| HostError::Worker)?;
        let mut deferred = std::mem::take(&mut events.deferred);
        deferred.append(&mut events.items);
        events.items = deferred;
        let mut result = Vec::new();
        let candidates = events.items.len();
        for _ in 0..candidates {
            if result.len() >= maximum as usize { break; }
            let Some(event) = events.items.pop_front() else { break; };
            if let HostEvent::Frame { connection_id, frame, .. } = &event {
                let slot = self.shared.slots.lock().map_err(|_| HostError::Worker)?.get(connection_id).cloned();
                let Some(slot) = slot else { events.bytes -= event.bytes(); continue; };
                if authorize_header(&slot, &FrameHeader { lane: frame.lane, generation: frame.generation,
                    stream_id: frame.stream_id, sequence: frame.sequence, length: frame.payload.len() }, false).is_err() {
                    events.bytes -= event.bytes(); continue;
                }
                if authorize_payload(&slot, frame).is_err() { events.bytes -= event.bytes(); continue; }
                if frame.lane != Lane::Enrollment && !slot.activated.load(Ordering::Acquire) {
                    events.deferred.push_back(event); continue;
                }
            }
            if let HostEvent::Closed { connection_id } = &event {
                self.shared.slots.lock().map_err(|_| HostError::Worker)?.remove(connection_id);
            }
            events.bytes -= event.bytes();
            result.push(event);
        }
        Ok(result)
    }
    pub fn network_changed(&self) -> Result<(), HostError> {
        self.open()?; self.commands.try_send(Command::NetworkChanged).map_err(|_| HostError::Backpressure)
    }
    pub fn diagnostics(&self, id: u64) -> Result<HostDiagnostics, HostError> {
        let slot = self.slot(id)?;
        if !slot.activated.load(Ordering::Acquire) { return Err(HostError::NotAuthorized); }
        let admission = slot.admission.lock().map_err(|_| HostError::Worker)?;
        let principal = match &*admission {
            Admission::Admitted(principal) if principal.expires_at_ms > now_ms() => principal,
            _ => return Err(HostError::NotAuthorized),
        };
        let paths = slot.connection.paths();
        let selected = paths.iter().find(|path| path.is_selected());
        let path_kind = selected.as_ref().map_or("unavailable", |path| {
            if path.is_relay() { "relay" } else if path.is_ip() { "direct" } else { "other" }
        });
        let rtt_ms = selected.map(|path| path.rtt().as_millis().min(u64::MAX as u128) as u64);
        Ok(HostDiagnostics { generation: principal.generation, authorization_epoch: principal.authorization_epoch,
            path_kind, open_paths: paths.len().min(u32::MAX as usize) as u32, rtt_ms,
            queued_send_bytes: slot.outbound.lock().map_err(|_| HostError::Worker)?.scheduler.queued_bytes() as u64,
            held_send_bytes: (16*1024*1024-self.shared.send_bytes.available_permits()) as u64,
            held_receive_bytes: (16*1024*1024-self.shared.read_bytes.available_permits()) as u64 })
    }
    pub fn disconnect(&self, id: u64) -> Result<(), HostError> {
        let slot = self.slot(id)?;
        slot.admission.lock().map_err(|_| HostError::Worker)?.revoke();
        slot.connection.close(0u32.into(), b"user disconnected"); Ok(())
    }
    pub fn retire_stream(&self, id: u64, lane: u8, stream_id: u64) -> Result<(), HostError> {
        let slot = self.slot(id)?;
        Lane::try_from(lane).map_err(|_| HostError::InvalidConfig)?;
        if slot.retired.lock().map_err(|_| HostError::Worker)?.retire(lane, stream_id).is_err() {
            slot.connection.close(1u32.into(), b"stream retirement limit");
            return Err(HostError::Backpressure);
        }
        let mut receiver = slot.receiver.lock().map_err(|_| HostError::Worker)?;
        receiver.ordering.retire(lane, stream_id);
        receiver.allocations.retain(|(kind, stream, _), _| *kind != lane || *stream != stream_id);
        drop(receiver);
        let mut outbound = slot.outbound.lock().map_err(|_| HostError::Worker)?;
        let Outbound { scheduler, allocations, .. } = &mut *outbound;
        scheduler.retire_stream(lane, stream_id, |frame| {
            allocations.remove(&(lane,stream_id,frame.sequence));
        });
        outbound.sequences.remove(&(lane,stream_id));
        Ok(())
    }
    pub fn shutdown(&self) -> Result<(), HostError> {
        self.shared.closed.store(true, Ordering::Release); self.shared.wake.notify_one();
        let result = if let Some(worker) = self.worker.lock().map_err(|_| HostError::Worker)?.take() {
            worker.join().map_err(|_| HostError::Worker)
        } else { Ok(()) };
        self.shared.slots.lock().unwrap_or_else(|error| error.into_inner()).clear();
        self.shared.events.lock().unwrap_or_else(|error| error.into_inner()).items.clear();
        self.shared.events.lock().unwrap_or_else(|error| error.into_inner()).deferred.clear();
        self.shared.events.lock().unwrap_or_else(|error| error.into_inner()).bytes = 0;
        self.shared.device_floors.lock().unwrap_or_else(|error| error.into_inner()).clear();
        result
    }
}
impl Drop for EndpointHost {
    fn drop(&mut self) { let _ = self.shutdown(); }
}

async fn bind_endpoint(policy: &EndpointPolicy, mut key: [u8;32]) -> Result<Endpoint, HostError> {
    let secret = SecretKey::from_bytes(&key); key.fill(0);
    let mut builder = Endpoint::builder(presets::Minimal).secret_key(secret)
        .alpns(vec![PAIR_ALPN.to_vec(), SESSION_ALPN.to_vec()]).clear_ip_transports()
        .clear_address_lookup().portmapper_config(PortmapperConfig::Disabled)
        .transport_config(QuicTransportConfig::builder()
            .max_concurrent_uni_streams(16u32.into()).max_concurrent_bidi_streams(0u32.into())
            .stream_receive_window((1024*1024u32).into()).receive_window((4*1024*1024u32).into())
            .send_window(8*1024*1024).datagram_receive_buffer_size(None)
            .keep_alive_interval(Duration::from_secs(10)).build());
    if !policy.relay_only {
        for address in &policy.bind_addresses {
            builder = builder.bind_addr(address.parse::<SocketAddr>().map_err(|_| HostError::InvalidConfig)?).map_err(|_| HostError::InvalidConfig)?;
        }
    }
    let map = RelayMap::empty();
    for relay in &policy.relays {
        let url: RelayUrl = relay.url.parse().map_err(|_| HostError::InvalidConfig)?;
        map.insert(url.clone(), Arc::new(RelayConfig::new(url, None).with_auth_token(&relay.token)));
    }
    builder.relay_mode(if map.is_empty() { RelayMode::Disabled } else { RelayMode::Custom(map) })
        .bind().await.map_err(|_| HostError::Worker)
}

fn update_info(endpoint: &Endpoint, policy: &EndpointPolicy, shared: &Shared) {
    let address = if policy.local_only {
        EndpointAddr::new(endpoint.id()).with_addrs(endpoint.bound_sockets().into_iter().map(TransportAddr::Ip))
    } else { endpoint.addr() };
    if let Ok(mut info) = shared.info.lock() { *info = (endpoint.id().to_string(), EndpointTicket::new(address).to_string()); }
}

fn authorize_header(slot: &Slot, header: &FrameHeader, inbound: bool) -> Result<(), HostError> {
    header.encode().map_err(|_| HostError::NotAuthorized)?;
    if slot.retired.lock().map_err(|_| HostError::Worker)?.contains(header.lane as u8, header.stream_id) {
        return Err(HostError::NotAuthorized);
    }
    let admission = slot.admission.lock().map_err(|_| HostError::Worker)?;
    if slot.connection.alpn() == PAIR_ALPN && header.lane != Lane::Enrollment {
        return Err(HostError::NotAuthorized);
    }
    if header.lane == Lane::Enrollment {
        if matches!(*admission, Admission::Revoked) || header.generation != 0 || header.length > MAX_PREAUTH_BYTES {
            return Err(HostError::NotAuthorized);
        }
        if inbound && (
            slot.preauth_frames.fetch_add(1, Ordering::Relaxed) >= MAX_PREAUTH_FRAMES ||
            slot.preauth_bytes.fetch_add(header.length as u64, Ordering::Relaxed).saturating_add(header.length as u64) > MAX_PREAUTH_BYTES as u64
        ) { return Err(HostError::NotAuthorized); }
        return Ok(());
    }
    let scope = match header.lane {
        Lane::Http | Lane::ServerEvents | Lane::WebSocket => Some("browser"),
        Lane::Binary => Some("files"), Lane::Input => Some("control"), Lane::Media => Some("media"),
        Lane::Application => Some("chat"), _ => None,
    };
    admission.check(header, scope, now_ms()).map_err(|_| HostError::NotAuthorized)?;
    Ok(())
}

fn authorize_payload(slot: &Slot, frame: &Frame) -> Result<(), HostError> {
    if !matches!(frame.lane, Lane::Control | Lane::Application | Lane::Http | Lane::ServerEvents | Lane::WebSocket) {
        return Ok(());
    }
    let envelope = Envelope::from_slice(&frame.payload).map_err(|_| HostError::NotAuthorized)?;
    if envelope.lane() != frame.lane { return Err(HostError::NotAuthorized); }
    let header = FrameHeader { lane: frame.lane, generation: frame.generation,
        stream_id: frame.stream_id, sequence: frame.sequence, length: frame.payload.len() };
    slot.admission.lock().map_err(|_| HostError::Worker)?
        .check(&header, envelope.required_scope(), now_ms()).map_err(|_| HostError::NotAuthorized)?;
    Ok(())
}

async fn run(endpoint: Endpoint, shared: Arc<Shared>, mut commands: mpsc::Receiver<Command>, policy: EndpointPolicy) {
    let mut handshakes: JoinSet<(u64, bool, Result<Connection, HostError>)> = JoinSet::new();
    let mut readers: JoinSet<u64> = JoinSet::new();
    let mut writers = JoinSet::new();
    let mut tick = tokio::time::interval(Duration::from_millis(5));
    let mut refresh = tokio::time::interval(Duration::from_secs(1));
    while !shared.closed.load(Ordering::Acquire) {
        tokio::select! {
            incoming = endpoint.accept(), if handshakes.len() < 8 => {
                let Some(incoming) = incoming else { break; };
                let id = shared.next_id.fetch_add(1, Ordering::Relaxed);
                handshakes.spawn(async move {
                    let connection = tokio::time::timeout(Duration::from_secs(5), incoming).await
                        .map_err(|_| HostError::Timeout).and_then(|result| result.map_err(|_| HostError::Worker));
                    (id, false, connection)
                });
            }
            command = commands.recv() => {
                match command {
                    Some(Command::Dial { id, address, pair }) if handshakes.len() < 8 => {
                        let endpoint = endpoint.clone();
                        handshakes.spawn(async move {
                            let result = tokio::time::timeout(Duration::from_secs(10),
                                endpoint.connect(address, if pair { PAIR_ALPN } else { SESSION_ALPN })).await
                                .map_err(|_| HostError::Timeout).and_then(|result| result.map_err(|_| HostError::Worker));
                            (id, true, result)
                        });
                    }
                    Some(Command::Dial { id, .. }) => { let _ = shared.events.lock().unwrap().push(HostEvent::Failed { request_id: id, code: "busy" }); }
                    Some(Command::NetworkChanged) => { endpoint.network_change().await; }
                    #[cfg(test)]
                    Some(Command::Crash) => { panic!("synthetic worker failure"); }
                    None => break,
                }
            }
            Some(result) = handshakes.join_next(), if !handshakes.is_empty() => {
                if let Ok((id, initiator, result)) = result {
                    match result {
                        Ok(connection) => {
                            let exporter = match connection_binding(&connection, b"autoyou/admission/1") {
                                Ok(value) => value,
                                Err(_) => { connection.close(1u32.into(), b"binding unavailable"); continue; }
                            };
                            let slot = Arc::new(Slot { connection, admission: Mutex::new(Admission::default()),
                                outbound: Mutex::new(Outbound { scheduler: Scheduler::default(), sequences: HashMap::new(), allocations: HashMap::new() }),
                                receiver: Mutex::new(Receiver { ordering: OrderedReceiver::default(), allocations: HashMap::new() }), preauth_frames: AtomicU64::new(0),
                                preauth_bytes: AtomicU64::new(0), read_slots: Arc::new(Semaphore::new(16)),
                                retired: Mutex::new(RetiredStreams::default()), activated: AtomicBool::new(false), closed: AtomicBool::new(false) });
                            let mut slots = shared.slots.lock().unwrap();
                            if slots.len() >= MAX_CONNECTIONS * 2 ||
                                slots.values().filter(|slot| !slot.closed.load(Ordering::Acquire)).count() >= MAX_CONNECTIONS {
                                slot.connection.close(1u32.into(), b"connection limit"); continue;
                            }
                            slots.insert(id, slot.clone()); drop(slots);
                            if shared.events.lock().unwrap().push(HostEvent::Connected { connection_id: id,
                                endpoint_id: slot.connection.remote_id().to_string(), initiator, exporter,
                                protocol: String::from_utf8_lossy(slot.connection.alpn()).into_owned() }).is_err() {
                                slot.connection.close(1u32.into(), b"host event limit");
                            }
                            readers.spawn(read_connection(id, slot, shared.clone()));
                        }
                        Err(_) => { let _ = shared.events.lock().unwrap().push(HostEvent::Failed { request_id: id, code: "connection_failed" }); }
                    }
                } else { break; }
            }
            Some(result) = readers.join_next(), if !readers.is_empty() => {
                if let Ok(id) = result {
                    if let Some(slot) = shared.slots.lock().unwrap().get(&id) { slot.closed.store(true, Ordering::Release); }
                    let _ = shared.events.lock().unwrap().push(HostEvent::Closed { connection_id: id });
                } else { break; }
            }
            Some(result) = writers.join_next(), if !writers.is_empty() => { if result.is_err() { break; } }
            _ = shared.wake.notified() => { pump(&shared, &mut writers); }
            _ = tick.tick() => { pump(&shared, &mut writers); }
            _ = refresh.tick() => { update_info(&endpoint, &policy, &shared); }
        }
    }
    for slot in shared.slots.lock().unwrap().values() { slot.connection.close(0u32.into(), b"host shutdown"); }
    handshakes.abort_all(); readers.abort_all(); writers.abort_all();
    while handshakes.join_next().await.is_some() {}
    while readers.join_next().await.is_some() {}
    while writers.join_next().await.is_some() {}
    shared.slots.lock().unwrap().clear();
}

fn pump(shared: &Arc<Shared>, writers: &mut JoinSet<()>) {
    let mut slots: Vec<_> = shared.slots.lock().unwrap().iter().map(|(id, slot)| (*id, slot.clone())).collect();
    slots.sort_by_key(|(id, _)| *id);
    if slots.is_empty() { return; }
    let start = shared.scheduling_cursor.fetch_add(1, Ordering::Relaxed) as usize % slots.len();
    for offset in 0..slots.len() {
        let slot = slots[(start + offset) % slots.len()].1.clone();
        let control = shared.control_slots.clone().try_acquire_owned().ok();
        let other = shared.write_slots.clone().try_acquire_owned().ok();
        if control.is_none() && other.is_none() { break; }
        let Ok(mut outbound) = slot.outbound.lock() else { continue; };
        let Outbound { scheduler, allocations, .. } = &mut *outbound;
        let Some(frame) = scheduler.pop_for_capacity(now_ms(), control.is_some(), other.is_some(),
            |frame| { allocations.remove(&(frame.lane as u8, frame.stream_id, frame.sequence)); }) else { continue; };
        let permit = if frame.lane.priority() == 0 { drop(other); control.unwrap() } else { drop(control); other.unwrap() };
        let allocation = outbound.allocations.remove(&(frame.lane as u8, frame.stream_id, frame.sequence));
        drop(outbound);
        writers.spawn(async move {
            let _permit = permit;
            let _allocation = allocation;
            let result = tokio::time::timeout(Duration::from_secs(60), async {
                let mut send = slot.connection.open_uni().await.map_err(|_| HostError::Closed)?;
                send.set_priority(16 - i32::from(frame.lane.priority())).map_err(|_| HostError::Worker)?;
                write_frame(&mut send, &frame).await.map_err(|_| HostError::Worker)?;
                send.finish().map_err(|_| HostError::Closed)?;
                send.stopped().await.map_err(|_| HostError::Closed)?;
                Ok::<(), HostError>(())
            }).await;
            if !matches!(result, Ok(Ok(()))) { slot.connection.close(1u32.into(), b"send interrupted"); }
        });
    }
}

async fn read_connection(id: u64, slot: Arc<Slot>, shared: Arc<Shared>) -> u64 {
    let mut streams: JoinSet<Result<(), HostError>> = JoinSet::new();
    let created_at = now_ms();
    let mut admission_tick = tokio::time::interval(Duration::from_secs(1));
    loop {
        tokio::select! {
            stream = slot.connection.accept_uni() => {
                let Ok(mut stream) = stream else { break; };
                let Ok(permit) = slot.read_slots.clone().try_acquire_owned() else {
                    let _ = stream.stop(1u32.into()); slot.connection.close(1u32.into(), b"stream limit"); break;
                };
                let slot = slot.clone(); let shared = shared.clone();
                streams.spawn(async move {
                    let _permit = permit;
                    let mut bytes = [0u8; HEADER_BYTES];
                    tokio::time::timeout(Duration::from_secs(5), stream.read_exact(&mut bytes)).await
                        .map_err(|_| HostError::Timeout)?.map_err(|_| HostError::Closed)?;
                    let header = FrameHeader::decode(&bytes).map_err(|_| HostError::NotAuthorized)?;
                    authorize_header(&slot, &header, true)?;
                    let _bytes = shared.read_bytes.clone().try_acquire_many_owned(header.length as u32)
                        .map_err(|_| HostError::Backpressure)?;
                    let mut payload = vec![0u8; header.length];
                    tokio::time::timeout(Duration::from_secs(60), stream.read_exact(&mut payload)).await
                        .map_err(|_| HostError::Timeout)?.map_err(|_| HostError::Closed)?;
                    // One frame per stream. Reject a hidden trailing payload.
                    let mut trailing = [0u8;1];
                    if tokio::time::timeout(Duration::from_secs(5), stream.read(&mut trailing)).await
                        .map_err(|_| HostError::Timeout)?.map_err(|_| HostError::Closed)?.is_some() {
                        return Err(HostError::NotAuthorized);
                    }
                    authorize_header(&slot, &header, false)?;
                    let frame = Frame {
                        lane: header.lane, generation: header.generation, stream_id: header.stream_id,
                        sequence: header.sequence, payload,
                    };
                    authorize_payload(&slot, &frame)?;
                    let mut receiver = slot.receiver.lock().map_err(|_| HostError::Worker)?;
                    if receiver.ordering.is_duplicate(&frame) { return Ok(()); }
                    let frames = receiver.ordering.receive(frame).map_err(|_| HostError::Backpressure)?;
                    receiver.allocations.insert((header.lane as u8, header.stream_id, header.sequence), _bytes);
                    let mut events = shared.events.lock().map_err(|_| HostError::Worker)?;
                    for frame in frames {
                        let allocation = receiver.allocations.remove(&(frame.lane as u8, frame.stream_id, frame.sequence))
                            .ok_or(HostError::Worker)?;
                        events.push(HostEvent::Frame { connection_id: id, frame, allocation })?;
                    }
                    Ok(())
                });
            }
            Some(result) = streams.join_next(), if !streams.is_empty() => {
                if !matches!(result, Ok(Ok(()))) { slot.connection.close(1u32.into(), b"frame rejected"); break; }
            }
            _ = admission_tick.tick() => {
                let admission = slot.admission.lock().unwrap();
                let expired = (!slot.activated.load(Ordering::Acquire) && now_ms().saturating_sub(created_at) >= 10_000) || match &*admission {
                    Admission::AwaitingProof => now_ms().saturating_sub(created_at) >= 10_000,
                    Admission::Admitted(principal) => principal.expires_at_ms <= now_ms(),
                    Admission::Revoked => true,
                };
                if expired { slot.connection.close(1u32.into(), b"admission expired"); break; }
            }
        }
    }
    streams.abort_all(); while streams.join_next().await.is_some() {}
    id
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn relay_only_filters_direct_hints_but_rejects_unapproved_relays() {
        let relay: RelayUrl = "http://127.0.0.1:32123".parse().unwrap();
        let other: RelayUrl = "http://127.0.0.1:32124".parse().unwrap();
        let mut policy = EndpointPolicy::local();
        policy.relay_only = true;
        policy.relays = vec![RelayPolicy { url: relay.to_string(), token: "synthetic-relay-token".into() }];
        policy.validate().unwrap();
        let id = SecretKey::from_bytes(&[61;32]).public();
        let ticket = EndpointTicket::new(EndpointAddr::new(id).with_addrs([
            TransportAddr::Ip("127.0.0.1:32125".parse().unwrap()), TransportAddr::Relay(relay),
        ])).to_string();
        let address = policy.ticket_address(&ticket, &id.to_string()).unwrap();
        assert_eq!(address.addrs.len(), 1);
        assert!(address.addrs.iter().all(TransportAddr::is_relay));
        let ticket = EndpointTicket::new(EndpointAddr::new(id).with_addrs([TransportAddr::Relay(other)])).to_string();
        assert!(policy.ticket_address(&ticket, &id.to_string()).is_err());
    }
    #[test]
    fn worker_panic_fails_closed_and_shutdown_releases_ownership() {
        let host = EndpointHost::start(EndpointPolicy::local(), [62;32]).unwrap();
        host.commands.try_send(Command::Crash).unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while !host.shared.closed.load(Ordering::Acquire) {
            assert!(std::time::Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(matches!(host.endpoint_info(), Err(HostError::Closed)));
        assert!(matches!(host.shutdown(), Err(HostError::Worker)));
        assert!(host.shared.slots.lock().unwrap().is_empty());
        assert_eq!(host.shared.read_bytes.available_permits(), 16*1024*1024);
        assert_eq!(host.shared.send_bytes.available_permits(), 16*1024*1024);
        host.shutdown().unwrap();
    }
}
