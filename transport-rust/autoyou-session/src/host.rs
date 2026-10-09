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
use autoyou_protocol::{Admission, Envelope, Frame, FrameHeader, HEADER_BYTES, Lane, Principal, PAIR_ALPN, SESSION_ALPN, ROOM_PAIR_ALPN, ROOM_SESSION_ALPN};
use iroh::{Endpoint, EndpointAddr, RelayConfig, RelayMap, RelayMode, RelayUrl, SecretKey, TransportAddr,
    endpoint::{Connection, PortmapperConfig, QuicTransportConfig, ReadError, ReadExactError, RecvStream, presets}};
use iroh_tickets::endpoint::EndpointTicket;
use serde::Deserialize;
use tokio::{sync::{Notify, OwnedSemaphorePermit, Semaphore, mpsc}, task::JoinSet};
use crate::{connection_binding, media::{SourceLease, Sources, MEDIA_EXPIRED_CODE},
    ordering::{OrderedReceiver, RetiredStreams}, scheduler::Scheduler, write_frame};

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
    pub(crate) fn validate(&self) -> Result<(), HostError> {
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

    pub(crate) fn ticket_address(&self, ticket: &str, expected_endpoint: &str) -> Result<EndpointAddr, HostError> {
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
    Frame { connection_id: u64, frame: Frame, allocation: OwnedSemaphorePermit,
        received_at: Option<tokio::time::Instant>, expires_at: Option<tokio::time::Instant> },
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
    pub active_logical_streams: u32,
    pub pending_stream_receipts: u32,
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
    next_stream: u64,
    receipts: HashMap<(u8,u64), (u64, Vec<u8>)>,
    uploads: UploadCredits,
}
const UPLOAD_WINDOW_BYTES:u64=8*autoyou_protocol::byte_stream::MAX_DATA_BYTES as u64;
const UPLOAD_POOL_BYTES:u64=4*1024*1024;
#[derive(Default)]
struct UploadCredits { pending:HashMap<(u8,u64),(u64,u64)> }
impl UploadCredits {
    fn plan(&self,key:(u8,u64),record:&autoyou_protocol::byte_stream::Record)->Result<Option<(u64,u64)>,HostError> {
        use autoyou_protocol::byte_stream::{Kind,Content,MAX_ACTIVE_STREAMS};
        if key.0==Lane::Http as u8 && key.1>=2 && record.kind==Kind::Open && record.content==Content::RawBody &&
            Envelope::from_slice(&record.metadata).map_err(|_|HostError::InvalidConfig)?.header.message_type==autoyou_protocol::MessageType::HttpRequest {
            if self.pending.contains_key(&key) { return Err(HostError::InvalidConfig); }
            if self.pending.len()>=MAX_ACTIVE_STREAMS { return Err(HostError::Backpressure); }
            return Ok(Some((0,0)));
        }
        if record.kind==Kind::Data {
            if let Some(&(sent,consumed))=self.pending.get(&key) {
                if record.content!=Content::RawBody || record.offset!=sent { return Err(HostError::InvalidConfig); }
                let next=sent.checked_add(record.data.len() as u64).ok_or(HostError::InvalidConfig)?;
                let pool:u64=self.pending.values().map(|(sent,consumed)|sent-consumed).sum();
                if next-consumed>UPLOAD_WINDOW_BYTES || pool+record.data.len() as u64>UPLOAD_POOL_BYTES { return Err(HostError::Backpressure); }
                return Ok(Some((next,consumed)));
            }
        }
        Ok(None)
    }
    fn consumed(&mut self,key:(u8,u64),offset:u64)->Result<(),HostError> {
        let (sent,consumed)=self.pending.get_mut(&key).ok_or(HostError::NotAuthorized)?;
        if offset<*consumed || offset>*sent { return Err(HostError::NotAuthorized); }
        *consumed=offset;Ok(())
    }
}
struct Receiver {
    ordering: OrderedReceiver,
    allocations: HashMap<(u8,u64,u64), OwnedSemaphorePermit>,
}
struct Slot {
    connection: Connection,
    initiator: bool,
    admission: Mutex<Admission>,
    outbound: Mutex<Outbound>,
    receiver: Mutex<Receiver>,
    media_sources: Mutex<Sources>,
    retired: Mutex<RetiredStreams>,
    activated: AtomicBool,
    preauth_frames: AtomicU64,
    preauth_bytes: AtomicU64,
    read_slots: Arc<Semaphore>,
    closed: AtomicBool,
}
impl Slot {
    fn device_floor_key(&self, principal: &Principal) -> (Option<String>, Option<String>, String) {
        // A remote Computer issues its own device namespace. Incoming grants
        // still use this endpoint's single authoritative device registry.
        (self.initiator.then(|| self.connection.remote_id().to_string()),
            (self.connection.alpn() == ROOM_SESSION_ALPN).then(|| principal.conversation_id.clone()),
            principal.device_id.clone())
    }
}
struct DeviceFloor { generation: u64, epoch: u64, endpoint_id: String }
struct Shared {
    slots: Mutex<HashMap<u64, Arc<Slot>>>,
    pending_dials: Mutex<HashMap<u64, Arc<PendingDial>>>,
    device_floors: Mutex<HashMap<(Option<String>, Option<String>, String), DeviceFloor>>,
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

struct PendingDial { canceled: AtomicBool, wake: Notify }

enum Command {
    Dial { id: u64, address: EndpointAddr, protocol: &'static [u8], pending: Arc<PendingDial> }, NetworkChanged,
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

/// Public identity derivation for protected-store maintenance while the
/// transport is stopped. This does not create an endpoint or network runtime.
pub fn endpoint_id_from_key(mut key: [u8;32]) -> String {
    let endpoint = iroh::SecretKey::from_bytes(&key).public().to_string();
    key.fill(0); endpoint
}

impl EndpointHost {
    pub fn start(policy: EndpointPolicy, key: [u8;32]) -> Result<Self, HostError> {
        policy.validate()?;
        let shared = Arc::new(Shared {
            slots: Mutex::new(HashMap::new()), pending_dials: Mutex::new(HashMap::new()), device_floors: Mutex::new(HashMap::new()),
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
        self.dial_application(ticket,expected_endpoint,if pair { PAIR_ALPN } else { SESSION_ALPN })
    }
    pub fn dial_application(&self, ticket: &str, expected_endpoint: &str, protocol: &[u8]) -> Result<u64, HostError> {
        let protocol = [PAIR_ALPN, SESSION_ALPN, ROOM_PAIR_ALPN, ROOM_SESSION_ALPN].into_iter()
            .find(|allowed| *allowed == protocol).ok_or(HostError::InvalidConfig)?;
        self.open()?;
        let address = self.policy.ticket_address(ticket, expected_endpoint)?;
        let id = self.shared.next_id.fetch_add(1, Ordering::Relaxed);
        let pending = Arc::new(PendingDial { canceled: AtomicBool::new(false), wake: Notify::new() });
        let mut dials = self.shared.pending_dials.lock().map_err(|_| HostError::Worker)?;
        if dials.len() >= 32 { return Err(HostError::Backpressure); }
        dials.insert(id, pending.clone());
        if self.commands.try_send(Command::Dial { id, address, protocol, pending }).is_err() {
            dials.remove(&id); return Err(HostError::Backpressure);
        }
        Ok(id)
    }
    pub fn admit(&self, id: u64, principal: Principal) -> Result<(), HostError> {
        principal.validate().map_err(|_| HostError::NotAuthorized)?;
        if principal.expires_at_ms <= now_ms() { return Err(HostError::NotAuthorized); }
        let slot = self.slot(id)?;
        if ![SESSION_ALPN, ROOM_SESSION_ALPN].contains(&slot.connection.alpn()) { return Err(HostError::NotAuthorized); }
        if slot.connection.alpn() == ROOM_SESSION_ALPN &&
             (!["room", "chat"].iter().all(|required| principal.scopes.iter().any(|scope| scope == *required)) ||
             principal.scopes.iter().any(|scope| !matches!(scope.as_str(), "room"|"chat"|"room_federation"|"files"|"media"))) {
            return Err(HostError::NotAuthorized);
        }
        let key = slot.device_floor_key(&principal);
        let mut floors = self.shared.device_floors.lock().map_err(|_| HostError::Worker)?;
        if let Some(floor) = floors.get(&key) {
            if principal.generation <= floor.generation || principal.authorization_epoch < floor.epoch ||
                principal.endpoint_id != floor.endpoint_id { return Err(HostError::NotAuthorized); }
        } else if floors.len() >= MAX_DEVICE_FLOORS { return Err(HostError::Backpressure); }
        if floors.iter().any(|(device, floor)| device.0 == key.0 && device.1 == key.1 && device != &key && floor.endpoint_id == principal.endpoint_id) {
            return Err(HostError::NotAuthorized);
        }
        slot.admission.lock().map_err(|_| HostError::Worker)?
            .admit(principal.clone(), &slot.connection.remote_id().to_string()).map_err(|_| HostError::NotAuthorized)?;
        floors.insert(key.clone(), DeviceFloor { generation: principal.generation,
            epoch: principal.authorization_epoch, endpoint_id: principal.endpoint_id.clone() });
        for (old_id, old) in self.shared.slots.lock().map_err(|_| HostError::Worker)?.iter() {
            if *old_id != id {
                let mut admission = old.admission.lock().map_err(|_| HostError::Worker)?;
                if matches!(&*admission, Admission::Admitted(previous) if old.device_floor_key(previous) == key) {
                    admission.revoke(); old.connection.close(1u32.into(), b"session superseded");
                }
            }
        }
        Ok(())
    }
    pub fn send(&self, id: u64, mut frame: Frame, deadline_ms: Option<u64>) -> Result<(), HostError> {
        if deadline_ms.is_some() && !matches!(frame.lane, Lane::Media | Lane::Input) {
            return Err(HostError::InvalidConfig);
        }
        let slot = self.slot(id)?;
        if frame.lane != Lane::Enrollment && !slot.activated.load(Ordering::Acquire) {
            return Err(HostError::NotAuthorized);
        }
        authorize_header(&slot, &FrameHeader { lane: frame.lane, generation: frame.generation,
            stream_id: frame.stream_id, sequence: 0, length: frame.payload.len() }, false)?;
        authorize_payload(&slot, &frame, false)?;
        if frame.lane == Lane::Input { input_send_deadline(deadline_ms, now_ms())?; }
        if frame.lane == Lane::Media {
            let now = now_ms();
            let source = slot.media_sources.lock().map_err(|_| HostError::Worker)?
                .ticket(frame.stream_id, false, now).map_err(|_| HostError::NotAuthorized)?;
            let deadline = deadline_ms.ok_or(HostError::InvalidConfig)?;
            if deadline <= now || deadline > now.saturating_add(source.lease.transport_budget(now).as_millis() as u64) {
                return Err(HostError::InvalidConfig);
            }
        }
        let mut outbound = slot.outbound.lock().map_err(|_| HostError::Worker)?;
        let key = (frame.lane as u8, frame.stream_id);
        if !outbound.sequences.contains_key(&key) && outbound.sequences.len() >= 128 { return Err(HostError::Backpressure); }
        let sequence = *outbound.sequences.get(&key).unwrap_or(&0);
        let next = assign_transport_sequence(&mut frame, sequence)?;
        let allocation = self.shared.send_bytes.clone().try_acquire_many_owned(frame.payload.len() as u32)
            .map_err(|_| HostError::Backpressure)?;
        let mut upload_credit=None;
        let completion = if frame.payload.starts_with(&autoyou_protocol::byte_stream::MAGIC) {
            let record = autoyou_protocol::byte_stream::Record::decode(frame.lane, &frame.payload)
                .map_err(|_| HostError::InvalidConfig)?;
            upload_credit=outbound.uploads.plan(key,&record)?;
            match record.kind {
                autoyou_protocol::byte_stream::Kind::Finish => Some((record.total, record.digest.to_vec())),
                // Abort acknowledges the bytes actually queued/consumed, not
                // the body's original declared length or unknown-total marker.
                autoyou_protocol::byte_stream::Kind::Abort => Some((record.offset, record.digest.to_vec())),
                _ => None,
            }
        } else { None };
        outbound.scheduler.push(frame, deadline_ms).map_err(|_| HostError::Backpressure)?;
        if let Some(credit)=upload_credit { outbound.uploads.pending.insert(key,credit); }
        outbound.allocations.insert((key.0,key.1,sequence), allocation);
        outbound.sequences.insert(key, next);
        if let Some(completion) = completion { outbound.receipts.insert(key, completion); }
        self.shared.wake.notify_one(); Ok(())
    }
    pub fn allocate_stream(&self, id: u64, lane: Lane) -> Result<u64, HostError> {
        if !matches!(lane, Lane::Http | Lane::ServerEvents | Lane::WebSocket | Lane::Binary) { return Err(HostError::InvalidConfig); }
        let slot = self.slot(id)?;
        if !slot.activated.load(Ordering::Acquire) { return Err(HostError::NotAuthorized); }
        let mut outbound = slot.outbound.lock().map_err(|_| HostError::Worker)?;
        if outbound.sequences.len() >= 128 { return Err(HostError::Backpressure); }
        let stream = outbound.next_stream;
        outbound.next_stream = stream.checked_add(2).ok_or(HostError::Closed)?;
        Ok(stream)
    }
    pub fn send_browser_records(&self, id: u64, lane: Lane, generation: u64, payloads: Vec<Vec<u8>>) -> Result<u64, HostError> {
        if payloads.is_empty() || payloads.len() > 128 ||
            !matches!(lane, Lane::Http | Lane::ServerEvents | Lane::WebSocket) { return Err(HostError::InvalidConfig); }
        let slot = self.slot(id)?;
        if !slot.activated.load(Ordering::Acquire) { return Err(HostError::NotAuthorized); }
        let mut frames = Vec::with_capacity(payloads.len()); let mut permits = Vec::with_capacity(payloads.len());
        let mut validator = autoyou_protocol::byte_stream::Receiver::default(); let mut completion = None;
        let count = payloads.len();
        for (index, payload) in payloads.into_iter().enumerate() {
            let record = autoyou_protocol::byte_stream::Record::decode(lane, &payload).map_err(|_| HostError::InvalidConfig)?;
            let expected_kind = if index == 0 { autoyou_protocol::byte_stream::Kind::Open }
                else if index + 1 == count { autoyou_protocol::byte_stream::Kind::Finish }
                else { autoyou_protocol::byte_stream::Kind::Data };
            if record.kind != expected_kind { return Err(HostError::InvalidConfig); }
            validator.accept(lane, 2, &record).map_err(|_| HostError::InvalidConfig)?;
            if record.kind == autoyou_protocol::byte_stream::Kind::Finish { completion = Some((record.total, record.digest.to_vec())); }
            let frame = Frame { lane, generation, stream_id: 2, sequence: index as u64, payload };
            authorize_header(&slot, &FrameHeader { lane, generation, stream_id: 2, sequence: frame.sequence, length: frame.payload.len() }, false)?;
            authorize_payload(&slot, &frame, false)?;
            permits.push(self.shared.send_bytes.clone().try_acquire_many_owned(frame.payload.len() as u32).map_err(|_| HostError::Backpressure)?);
            frames.push(frame);
        }
        let completion = completion.ok_or(HostError::InvalidConfig)?;
        if validator.active_count() != 0 { return Err(HostError::InvalidConfig); }
        let mut outbound = slot.outbound.lock().map_err(|_| HostError::Worker)?;
        if outbound.sequences.len() >= 128 { return Err(HostError::Backpressure); }
        // The ordered business-message lane remains one persistent logical
        // stream. Independent message streams would allow STREAM_END to overtake
        // an earlier chunk or a WebSocket message. Large request bodies use
        // separately allocated IDs >=2 and do not block this metadata stream.
        let stream_id = 1;
        let first = *outbound.sequences.get(&(lane as u8, stream_id)).unwrap_or(&0);
        for frame in &mut frames { frame.stream_id = stream_id; frame.sequence += first; }
        let count = frames.len() as u64;
        let next = first.checked_add(count).ok_or(HostError::Closed)?;
        outbound.scheduler.push_batch(frames).map_err(|_| HostError::Backpressure)?;
        for (index, permit) in permits.into_iter().enumerate() { outbound.allocations.insert((lane as u8, stream_id, first + index as u64), permit); }
        outbound.sequences.insert((lane as u8, stream_id), next);
        let _ = completion; self.shared.wake.notify_one(); Ok(stream_id)
    }
    pub fn acknowledge_stream(&self, id: u64, lane: u8, stream_id: u64, total: u64, digest: Vec<u8>) -> Result<(), HostError> {
        let slot = self.slot(id)?;
        let generation = match &*slot.admission.lock().map_err(|_| HostError::Worker)? {
            Admission::Admitted(principal) => principal.generation, _ => return Err(HostError::NotAuthorized),
        };
        let receipt = autoyou_protocol::byte_stream::Receipt { lane, stream_id, total, digest };
        let envelope = Envelope { header: autoyou_protocol::MessageHeader { message_id: "byte-stream-receipt".into(),
            message_type: autoyou_protocol::MessageType::TransportStreamReceipt, timestamp: 0.0, session_id: None, user_id: None,
            extensions: Default::default() }, payload: serde_json::to_value(receipt).map_err(|_| HostError::InvalidConfig)?
                .as_object().ok_or(HostError::InvalidConfig)?.clone(), extensions: Default::default() };
        autoyou_protocol::byte_stream::Receipt::from_envelope(&envelope).map_err(|_| HostError::InvalidConfig)?;
        self.send(id, Frame { lane: Lane::Control, generation, stream_id: 0, sequence: 0,
            payload: envelope.to_vec().map_err(|_| HostError::InvalidConfig)? }, None)?;
        self.retire_stream(id, lane, stream_id)
    }
    pub fn acknowledge_progress(&self,id:u64,lane:u8,stream_id:u64,offset:u64)->Result<(),HostError> {
        let slot=self.slot(id)?;
        let generation=match &*slot.admission.lock().map_err(|_|HostError::Worker)? {
            Admission::Admitted(principal)=>principal.generation,_=>return Err(HostError::NotAuthorized),
        };
        let progress=autoyou_protocol::byte_stream::Progress {lane,stream_id,offset};
        let envelope=Envelope {header:autoyou_protocol::MessageHeader {message_id:"byte-stream-progress".into(),
            message_type:autoyou_protocol::MessageType::TransportStreamProgress,timestamp:0.0,session_id:None,user_id:None,extensions:Default::default()},
            payload:serde_json::to_value(progress).map_err(|_|HostError::InvalidConfig)?.as_object().ok_or(HostError::InvalidConfig)?.clone(),extensions:Default::default()};
        autoyou_protocol::byte_stream::Progress::from_envelope(&envelope).map_err(|_|HostError::InvalidConfig)?;
        self.send(id,Frame {lane:Lane::Control,generation,stream_id:0,sequence:0,payload:envelope.to_vec().map_err(|_|HostError::InvalidConfig)?},None)
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
            if let HostEvent::Frame { connection_id, frame, expires_at, .. } = &event {
                if expires_at.is_some_and(|deadline| tokio::time::Instant::now() >= deadline) {
                    events.bytes -= event.bytes(); continue;
                }
                let slot = self.shared.slots.lock().map_err(|_| HostError::Worker)?.get(connection_id).cloned();
                let Some(slot) = slot else { events.bytes -= event.bytes(); continue; };
                if authorize_header(&slot, &FrameHeader { lane: frame.lane, generation: frame.generation,
                    stream_id: frame.stream_id, sequence: frame.sequence, length: frame.payload.len() }, false).is_err() {
                    events.bytes -= event.bytes(); continue;
                }
                if authorize_payload(&slot, frame, true).is_err() { events.bytes -= event.bytes(); continue; }
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
        drop(events);
        let mut delivered = Vec::with_capacity(result.len());
        for event in result {
            if let HostEvent::Frame { connection_id, frame, .. } = &event {
                if frame.lane == Lane::Control {
                    let envelope = Envelope::from_slice(&frame.payload).map_err(|_| HostError::NotAuthorized)?;
                    if envelope.header.message_type == autoyou_protocol::MessageType::TransportStreamReceipt {
                        let Some(slot) = self.shared.slots.lock().map_err(|_| HostError::Worker)?.get(connection_id).cloned() else { continue; };
                        let Ok(receipt) = autoyou_protocol::byte_stream::Receipt::from_envelope(&envelope) else {
                            slot.connection.close(1u32.into(), b"invalid stream receipt"); continue;
                        };
                        let expected = slot.outbound.lock().map_err(|_| HostError::Worker)?.receipts
                            .get(&(receipt.lane, receipt.stream_id)).cloned();
                        // Duplicate receipts for already retired streams are harmless;
                        // a peer cannot retire another body's pending bytes by guessing.
                        if expected.is_none() && slot.retired.lock().map_err(|_| HostError::Worker)?.contains(receipt.lane, receipt.stream_id) { continue; }
                        if expected != Some((receipt.total, receipt.digest)) {
                            slot.connection.close(1u32.into(), b"invalid stream receipt"); continue;
                        }
                        self.retire_stream(*connection_id, receipt.lane, receipt.stream_id)?; continue;
                    }
                    if envelope.header.message_type==autoyou_protocol::MessageType::TransportStreamProgress {
                        let Some(slot)=self.shared.slots.lock().map_err(|_|HostError::Worker)?.get(connection_id).cloned() else {continue;};
                        let Ok(progress)=autoyou_protocol::byte_stream::Progress::from_envelope(&envelope) else {
                            slot.connection.close(1u32.into(),b"invalid stream progress");continue;
                        };
                        if slot.retired.lock().map_err(|_|HostError::Worker)?.contains(progress.lane,progress.stream_id) {continue;}
                        if slot.outbound.lock().map_err(|_|HostError::Worker)?.uploads.consumed((progress.lane,progress.stream_id),progress.offset).is_err() {
                            slot.connection.close(1u32.into(),b"invalid stream progress");
                        }
                        continue;
                    }
                }
            }
            delivered.push(event);
        }
        Ok(delivered)
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
        let outbound = slot.outbound.lock().map_err(|_| HostError::Worker)?;
        Ok(HostDiagnostics { generation: principal.generation, authorization_epoch: principal.authorization_epoch,
            path_kind, open_paths: paths.len().min(u32::MAX as usize) as u32, rtt_ms,
            queued_send_bytes: outbound.scheduler.queued_bytes() as u64,
            held_send_bytes: (16*1024*1024-self.shared.send_bytes.available_permits()) as u64,
            held_receive_bytes: (16*1024*1024-self.shared.read_bytes.available_permits()) as u64,
            active_logical_streams: outbound.sequences.len() as u32, pending_stream_receipts: outbound.receipts.len() as u32 })
    }
    pub fn approve_media_source(&self, id: u64, lease: SourceLease, inbound: bool) -> Result<(), HostError> {
        let slot = self.slot(id)?;
        if !slot.activated.load(Ordering::Acquire) { return Err(HostError::NotAuthorized); }
        let principal = match &*slot.admission.lock().map_err(|_| HostError::Worker)? {
            Admission::Admitted(principal) => principal.clone(), _ => return Err(HostError::NotAuthorized),
        };
        let now = now_ms();
        let mut sources = slot.media_sources.lock().map_err(|_| HostError::Worker)?;
        if sources.lease(lease.source_id, inbound, now).ok() == Some(&lease) {
            lease.validate(&principal, now).map_err(|_| HostError::NotAuthorized)?; return Ok(());
        }
        let source_id = lease.source_id;
        sources.approve(lease, inbound, &principal, now).map_err(|_| HostError::NotAuthorized)?;
        drop(sources);
        if !inbound { clear_queued_media(&slot, source_id)?; }
        Ok(())
    }
    pub fn revoke_media_source(&self, id: u64, source_id: u64, inbound: bool) -> Result<(), HostError> {
        let slot = self.slot(id)?;
        slot.media_sources.lock().map_err(|_| HostError::Worker)?.revoke(source_id, inbound);
        if !inbound { clear_queued_media(&slot, source_id)?; }
        Ok(())
    }
    pub fn disconnect(&self, id: u64) -> Result<(), HostError> {
        self.open()?;
        // Publication and cancellation lock slots before pending dials, so a
        // completed handshake cannot slip between those two ownership states.
        let slots = self.shared.slots.lock().map_err(|_| HostError::Worker)?;
        if let Some(slot) = slots.get(&id) {
            slot.admission.lock().map_err(|_| HostError::Worker)?.revoke();
            slot.connection.close(0u32.into(), b"user disconnected");
        } else {
            let dials = self.shared.pending_dials.lock().map_err(|_| HostError::Worker)?;
            let pending = dials.get(&id).ok_or(HostError::UnknownConnection)?;
            pending.canceled.store(true, Ordering::Release); pending.wake.notify_one();
        }
        Ok(())
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
        outbound.uploads.pending.remove(&(lane,stream_id));
        outbound.receipts.remove(&(lane,stream_id));
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
        self.shared.pending_dials.lock().unwrap_or_else(|error| error.into_inner()).clear();
        result
    }
}
impl Drop for EndpointHost {
    fn drop(&mut self) { let _ = self.shutdown(); }
}

fn clear_queued_media(slot: &Slot, source_id: u64) -> Result<(), HostError> {
    let mut outbound = slot.outbound.lock().map_err(|_| HostError::Worker)?;
    let Outbound { scheduler, allocations, .. } = &mut *outbound;
    scheduler.retire_stream(Lane::Media as u8, source_id, |frame| {
        allocations.remove(&(Lane::Media as u8, source_id, frame.sequence));
    });
    outbound.sequences.remove(&(Lane::Media as u8, source_id));
    Ok(())
}

async fn bind_endpoint(policy: &EndpointPolicy, mut key: [u8;32]) -> Result<Endpoint, HostError> {
    let secret = SecretKey::from_bytes(&key); key.fill(0);
    let mut builder = Endpoint::builder(presets::Minimal).secret_key(secret)
        .alpns(vec![PAIR_ALPN.to_vec(), SESSION_ALPN.to_vec(), ROOM_PAIR_ALPN.to_vec(), ROOM_SESSION_ALPN.to_vec()]).clear_ip_transports()
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
    if [PAIR_ALPN, ROOM_PAIR_ALPN].contains(&slot.connection.alpn()) && header.lane != Lane::Enrollment {
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

fn authorize_payload(slot: &Slot, frame: &Frame, inbound: bool) -> Result<(), HostError> {
    if frame.lane == Lane::Media {
        let header = autoyou_protocol::media::MediaHeader::decode(
            frame.payload.get(..autoyou_protocol::media::MEDIA_HEADER_BYTES).ok_or(HostError::NotAuthorized)?)
            .map_err(|_| HostError::NotAuthorized)?;
        if frame.stream_id != header.source_id || frame.payload.len() != autoyou_protocol::media::MEDIA_HEADER_BYTES + header.length {
            return Err(HostError::NotAuthorized);
        }
        // Sending and receiving use separate local consent directions.
        slot.media_sources.lock().map_err(|_| HostError::Worker)?
            .check(&header, inbound, now_ms()).map_err(|_| HostError::NotAuthorized)?;
        return Ok(());
    }
    if matches!(frame.lane, Lane::Http | Lane::ServerEvents | Lane::WebSocket | Lane::Binary) &&
        frame.payload.starts_with(&autoyou_protocol::byte_stream::MAGIC) {
        autoyou_protocol::byte_stream::Record::decode(frame.lane, &frame.payload)
            .map_err(|_| HostError::NotAuthorized)?;
        if frame.stream_id == 0 || (frame.lane == Lane::Binary && frame.stream_id < 2) { return Err(HostError::NotAuthorized); }
        let header = FrameHeader { lane: frame.lane, generation: frame.generation,
            stream_id: frame.stream_id, sequence: frame.sequence, length: frame.payload.len() };
        slot.admission.lock().map_err(|_| HostError::Worker)?
            .check(&header, Some(if frame.lane == Lane::Binary { "files" } else { "browser" }), now_ms()).map_err(|_| HostError::NotAuthorized)?;
        return Ok(());
    }
    if frame.lane == Lane::Binary { return Err(HostError::NotAuthorized); }
    if !matches!(frame.lane, Lane::Control | Lane::Application | Lane::Http | Lane::ServerEvents | Lane::WebSocket) {
        return Ok(());
    }
    let envelope = Envelope::from_slice(&frame.payload).map_err(|_| HostError::NotAuthorized)?;
    if envelope.header.message_type == autoyou_protocol::MessageType::TransportStreamReceipt {
        autoyou_protocol::byte_stream::Receipt::from_envelope(&envelope).map_err(|_| HostError::NotAuthorized)?;
    }
    if envelope.header.message_type==autoyou_protocol::MessageType::TransportStreamProgress {
        autoyou_protocol::byte_stream::Progress::from_envelope(&envelope).map_err(|_|HostError::NotAuthorized)?;
    }
    if envelope.header.message_type == autoyou_protocol::MessageType::BinaryTransferControl {
        autoyou_protocol::binary::Control::from_envelope(&envelope).map_err(|_| HostError::NotAuthorized)?;
    }
    if envelope.header.message_type == autoyou_protocol::MessageType::ApplicationDeliveryControl {
        autoyou_protocol::delivery::Control::from_envelope(&envelope).map_err(|_| HostError::NotAuthorized)?;
    }
    if envelope.lane() != frame.lane { return Err(HostError::NotAuthorized); }
    let header = FrameHeader { lane: frame.lane, generation: frame.generation,
        stream_id: frame.stream_id, sequence: frame.sequence, length: frame.payload.len() };
    let admission=slot.admission.lock().map_err(|_| HostError::Worker)?;
    admission.check(&header, envelope.required_scope(), now_ms()).map_err(|_| HostError::NotAuthorized)?;
    if let Some(scope)=envelope.additional_required_scope() {
        admission.check(&header, Some(scope), now_ms()).map_err(|_| HostError::NotAuthorized)?;
    }
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
                    Some(Command::Dial { id, address, protocol, pending }) if handshakes.len() < 8 => {
                        let endpoint = endpoint.clone();
                        handshakes.spawn(async move {
                            let result = if pending.canceled.load(Ordering::Acquire) { Err(HostError::Closed) } else { tokio::select! {
                                _ = pending.wake.notified() => Err(HostError::Closed),
                                result = tokio::time::timeout(Duration::from_secs(10),
                                endpoint.connect(address, protocol))
                                    => result.map_err(|_| HostError::Timeout).and_then(|result| result.map_err(|_| HostError::Worker)),
                            } };
                            (id, true, result)
                        });
                    }
                    Some(Command::Dial { id, .. }) => {
                        shared.pending_dials.lock().unwrap().remove(&id);
                        let _ = shared.events.lock().unwrap().push(HostEvent::Failed { request_id: id, code: "busy" });
                    }
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
                                Err(_) => { shared.pending_dials.lock().unwrap().remove(&id); connection.close(1u32.into(), b"binding unavailable"); continue; }
                            };
                            let slot = Arc::new(Slot { connection, initiator, admission: Mutex::new(Admission::default()),
                                outbound: Mutex::new(Outbound { scheduler: Scheduler::default(), sequences: HashMap::new(), allocations: HashMap::new(),
                                    next_stream: if initiator { 3 } else { 2 }, receipts: HashMap::new(), uploads:UploadCredits::default() }),
                                receiver: Mutex::new(Receiver { ordering: OrderedReceiver::default(), allocations: HashMap::new() }),
                                media_sources: Mutex::new(Sources::default()), preauth_frames: AtomicU64::new(0),
                                preauth_bytes: AtomicU64::new(0), read_slots: Arc::new(Semaphore::new(16)),
                                retired: Mutex::new(RetiredStreams::default()), activated: AtomicBool::new(false), closed: AtomicBool::new(false) });
                            let mut slots = shared.slots.lock().unwrap();
                            let pending = shared.pending_dials.lock().unwrap().remove(&id);
                            if initiator && pending.is_none_or(|dial| dial.canceled.load(Ordering::Acquire)) {
                                slot.connection.close(0u32.into(), b"dial canceled");
                                let _ = shared.events.lock().unwrap().push(HostEvent::Failed { request_id: id, code: "canceled" });
                                continue;
                            }
                            if slots.len() >= MAX_CONNECTIONS * 2 ||
                                slots.values().filter(|slot| !slot.closed.load(Ordering::Acquire)).count() >= MAX_CONNECTIONS {
                                slot.connection.close(1u32.into(), b"connection limit");
                                if initiator { let _ = shared.events.lock().unwrap().push(HostEvent::Failed { request_id: id, code: "busy" }); }
                                continue;
                            }
                            slots.insert(id, slot.clone()); drop(slots);
                            if shared.events.lock().unwrap().push(HostEvent::Connected { connection_id: id,
                                endpoint_id: slot.connection.remote_id().to_string(), initiator, exporter,
                                protocol: String::from_utf8_lossy(slot.connection.alpn()).into_owned() }).is_err() {
                                slot.connection.close(1u32.into(), b"host event limit");
                            }
                            readers.spawn(read_connection(id, slot, shared.clone()));
                        }
                        Err(_) => {
                            shared.pending_dials.lock().unwrap().remove(&id);
                            let _ = shared.events.lock().unwrap().push(HostEvent::Failed { request_id: id, code: "connection_failed" });
                        }
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
    shared.pending_dials.lock().unwrap().clear();
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
        let Some(scheduled) = scheduler.pop_scheduled(now_ms(), control.is_some(), other.is_some(),
            |frame| { allocations.remove(&(frame.lane as u8, frame.stream_id, frame.sequence)); }) else { continue; };
        let frame = scheduled.frame;
        let permit = if frame.lane.priority() == 0 { drop(other); control.unwrap() } else { drop(control); other.unwrap() };
        let allocation = outbound.allocations.remove(&(frame.lane as u8, frame.stream_id, frame.sequence));
        drop(outbound);
        writers.spawn(async move {
            let _permit = permit;
            let _allocation = allocation;
            if frame.lane == Lane::Media {
                write_media_frame(&slot, &frame, scheduled.deadline_ms).await;
                return;
            }
            if frame.lane == Lane::Input {
                write_input_frame(&slot, &frame, scheduled.deadline_ms).await;
                return;
            }
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

const INPUT_MAX_AGE_MS: u64 = 200;

fn input_send_deadline(deadline: Option<u64>, now: u64) -> Result<u64, HostError> {
    let deadline = deadline.ok_or(HostError::InvalidConfig)?;
    if deadline <= now { return Err(HostError::Timeout); }
    if deadline > now.saturating_add(INPUT_MAX_AGE_MS) { return Err(HostError::InvalidConfig); }
    Ok(deadline)
}

fn assign_transport_sequence(frame: &mut Frame, sequence: u64) -> Result<u64, HostError> {
    let next = sequence.checked_add(1).ok_or(HostError::Closed)?;
    frame.sequence = sequence;
    Ok(next)
}

async fn write_input_frame(slot: &Slot, frame: &Frame, deadline_ms: Option<u64>) {
    let Ok(deadline_ms) = input_send_deadline(deadline_ms, now_ms()) else { return; };
    if authorize_header(slot, &FrameHeader { lane: frame.lane, generation: frame.generation,
        stream_id: frame.stream_id, sequence: frame.sequence, length: frame.payload.len() }, false).is_err() { return; }
    let remaining = deadline_ms.saturating_sub(now_ms());
    if remaining == 0 { return; }
    let deadline = tokio::time::Instant::now() + Duration::from_millis(remaining);
    let Ok(Ok(mut send)) = tokio::time::timeout_at(deadline, slot.connection.open_uni()).await else { return; };
    if now_ms() >= deadline_ms || send.set_priority(16 - i32::from(Lane::Input.priority())).is_err() {
        let _ = send.reset(MEDIA_EXPIRED_CODE.into()); return;
    }
    let result = tokio::time::timeout_at(deadline, async {
        write_frame(&mut send, frame).await.map_err(|_| HostError::Worker)?;
        send.finish().map_err(|_| HostError::Closed)?;
        send.stopped().await.map_err(|_| HostError::Closed)?;
        Ok::<(), HostError>(())
    }).await;
    if !matches!(result, Ok(Ok(()))) { let _ = send.reset(MEDIA_EXPIRED_CODE.into()); }
}

async fn write_media_frame(slot: &Slot, frame: &Frame, deadline_ms: Option<u64>) {
    // The local consent token cancels queued/in-flight work on source changes.
    // Media timeout/reset is a frame loss, never a reliable-session failure.
    let Some(deadline_ms) = deadline_ms else { return; };
    let now = now_ms();
    if now >= deadline_ms || authorize_payload(slot, frame, false).is_err() { return; }
    let Ok(mut source) = slot.media_sources.lock().map_err(|_| HostError::Worker)
        .and_then(|sources| sources.ticket(frame.stream_id, false, now).map_err(|_| HostError::NotAuthorized)) else { return; };
    let deadline = tokio::time::Instant::now() + Duration::from_millis(deadline_ms - now);
    let opened = tokio::select! {
        _ = source.cancelled() => return,
        result = tokio::time::timeout_at(deadline, slot.connection.open_uni()) => result,
    };
    let Ok(Ok(mut send)) = opened else { return; };
    if source.is_cancelled() || now_ms() >= deadline_ms ||
        send.set_priority(16 - i32::from(Lane::Media.priority())).is_err() {
        let _ = send.reset(MEDIA_EXPIRED_CODE.into()); return;
    }
    let complete = tokio::select! {
        _ = source.cancelled() => false,
        result = tokio::time::timeout_at(deadline, async {
            write_frame(&mut send, frame).await.map_err(|_| HostError::Worker)?;
            send.finish().map_err(|_| HostError::Closed)?;
            send.stopped().await.map_err(|_| HostError::Closed)?;
            Ok::<(), HostError>(())
        }) => matches!(result, Ok(Ok(()))),
    };
    if !complete { let _ = send.reset(MEDIA_EXPIRED_CODE.into()); }
}

async fn read_media_frame(id: u64, stream: &mut RecvStream, slot: &Slot, shared: &Shared,
    frame_header: FrameHeader) -> Result<(), HostError> {
    use autoyou_protocol::media::{MediaHeader, MEDIA_HEADER_BYTES};
    if frame_header.length < MEDIA_HEADER_BYTES { return Err(HostError::NotAuthorized); }
    // The source identifier is in the small outer header. Require its approved
    // lease before reading the codec header or allocating the encoded frame.
    let mut source = {
        let sources = slot.media_sources.lock().map_err(|_| HostError::Worker)?;
        if sources.retired(frame_header.stream_id, true, now_ms()) {
            let _ = stream.stop(MEDIA_EXPIRED_CODE.into()); return Ok(());
        }
        sources.ticket(frame_header.stream_id, true, now_ms()).map_err(|_| HostError::NotAuthorized)?
    };
    let lease = source.lease.clone();
    let received_at = tokio::time::Instant::now();
    let deadline = received_at + lease.transport_budget(now_ms());
    let result = tokio::select! {
        _ = source.cancelled() => None,
        result = tokio::time::timeout_at(deadline, async {
            let mut bytes = [0u8; MEDIA_HEADER_BYTES];
            if stream.read_exact(&mut bytes).await.is_err() { return Ok(None); }
            let header = MediaHeader::decode(&bytes).map_err(|_| HostError::NotAuthorized)?;
            if header.generation < lease.media_generation || header.authorization_epoch < lease.authorization_epoch {
                return Ok(None);
            }
            lease.check(&header, now_ms()).map_err(|_| HostError::NotAuthorized)?;
            if frame_header.stream_id != header.source_id || frame_header.length != MEDIA_HEADER_BYTES + header.length {
                return Err(HostError::NotAuthorized);
            }
            let allocation = shared.read_bytes.clone().try_acquire_many_owned(frame_header.length as u32)
                .map_err(|_| HostError::Backpressure)?;
            let mut payload = Vec::with_capacity(frame_header.length);
            payload.extend_from_slice(&bytes); payload.resize(frame_header.length, 0);
            if stream.read_exact(&mut payload[MEDIA_HEADER_BYTES..]).await.is_err() { return Ok(None); }
            autoyou_protocol::media_codec::validate(&header, &payload[MEDIA_HEADER_BYTES..])
                .map_err(|_| HostError::NotAuthorized)?;
            let mut trailing = [0u8;1];
            match stream.read(&mut trailing).await {
                Ok(None) => {}, Ok(Some(_)) => return Err(HostError::NotAuthorized), Err(_) => return Ok(None),
            }
            Ok(Some((header, payload, allocation)))
        }) => match result { Ok(result) => Some(result), Err(_) => None },
    };
    let Some(result) = result else { let _ = stream.stop(MEDIA_EXPIRED_CODE.into()); return Ok(()); };
    let Some((header, payload, allocation)) = result? else { let _ = stream.stop(MEDIA_EXPIRED_CODE.into()); return Ok(()); };
    if source.is_cancelled() || tokio::time::Instant::now() >= deadline {
        let _ = stream.stop(MEDIA_EXPIRED_CODE.into()); return Ok(());
    }
    authorize_header(slot, &frame_header, false)?;
    let mut sources = slot.media_sources.lock().map_err(|_| HostError::Worker)?;
    match sources.accept(&header, true, now_ms()) {
        Ok(true) => {},
        Ok(false) => return Ok(()),
        Err(_) => { let _ = stream.stop(MEDIA_EXPIRED_CODE.into()); return Ok(()); },
    }
    // Poll owns the event queue before rechecking consent. Release the source
    // lock before publication so a concurrent poll cannot invert those locks.
    drop(sources);
    shared.events.lock().map_err(|_| HostError::Worker)?.push(HostEvent::Frame {
        connection_id: id, frame: Frame { lane: Lane::Media, generation: frame_header.generation,
            stream_id: frame_header.stream_id, sequence: frame_header.sequence, payload }, allocation,
            received_at: Some(received_at), expires_at: Some(deadline),
    })?;
    Ok(())
}

async fn read_input_frame(id: u64, stream: &mut RecvStream, slot: &Slot, shared: &Shared,
    header: FrameHeader, received_at: tokio::time::Instant) -> Result<(), HostError> {
    let deadline = received_at + Duration::from_millis(INPUT_MAX_AGE_MS);
    if tokio::time::Instant::now() >= deadline {
        let _ = stream.stop(MEDIA_EXPIRED_CODE.into()); return Ok(());
    }
    let allocation = shared.read_bytes.clone().try_acquire_many_owned(header.length as u32)
        .map_err(|_| HostError::Backpressure)?;
    let mut payload = vec![0u8; header.length];
    let result = tokio::time::timeout_at(deadline, async {
        if stream.read_exact(&mut payload).await.is_err() { return Ok(false); }
        let mut trailing = [0u8; 1];
        match stream.read(&mut trailing).await {
            Ok(None) => Ok(true), Ok(Some(_)) => Err(HostError::NotAuthorized), Err(_) => Ok(false),
        }
    }).await;
    if !matches!(result, Ok(Ok(true))) {
        let _ = stream.stop(MEDIA_EXPIRED_CODE.into());
        return match result { Ok(Err(error)) => Err(error), _ => Ok(()) };
    }
    if tokio::time::Instant::now() >= deadline {
        let _ = stream.stop(MEDIA_EXPIRED_CODE.into()); return Ok(());
    }
    authorize_header(slot, &header, false)?;
    // A timed-out input can leave a transport sequence gap. The application
    // lease owns contiguous input order; reliable message reassembly cannot
    // retain later input behind the missing frame or reset its age.
    shared.events.lock().map_err(|_| HostError::Worker)?.push(HostEvent::Frame {
        connection_id: id, frame: Frame { lane: Lane::Input, generation: header.generation,
            stream_id: header.stream_id, sequence: header.sequence, payload }, allocation,
        received_at: Some(received_at), expires_at: Some(deadline),
    })?;
    Ok(())
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
                let received_at = tokio::time::Instant::now();
                streams.spawn(async move {
                    let _permit = permit;
                    let mut bytes = [0u8; HEADER_BYTES];
                    match tokio::time::timeout(Duration::from_secs(5), stream.read_exact(&mut bytes)).await {
                        Ok(Ok(())) => {},
                        // A media writer can expire before its complete header
                        // arrives. This reserved reset never delivers payload.
                        Ok(Err(ReadExactError::ReadError(ReadError::Reset(code)))) if code == MEDIA_EXPIRED_CODE.into() => return Ok(()),
                        Ok(Err(_)) => return Err(HostError::Closed), Err(_) => return Err(HostError::Timeout),
                    }
                    let header = FrameHeader::decode(&bytes).map_err(|_| HostError::NotAuthorized)?;
                    authorize_header(&slot, &header, true)?;
                    if header.lane == Lane::Media {
                        return read_media_frame(id, &mut stream, &slot, &shared, header).await;
                    }
                    if header.lane == Lane::Input {
                        return read_input_frame(id, &mut stream, &slot, &shared, header, received_at).await;
                    }
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
                    authorize_payload(&slot, &frame, true)?;
                    let mut receiver = slot.receiver.lock().map_err(|_| HostError::Worker)?;
                    if receiver.ordering.is_duplicate(&frame) { return Ok(()); }
                    let frames = receiver.ordering.receive(frame).map_err(|_| HostError::Backpressure)?;
                    receiver.allocations.insert((header.lane as u8, header.stream_id, header.sequence), _bytes);
                    let mut events = shared.events.lock().map_err(|_| HostError::Worker)?;
                    for frame in frames {
                        let allocation = receiver.allocations.remove(&(frame.lane as u8, frame.stream_id, frame.sequence))
                            .ok_or(HostError::Worker)?;
                        events.push(HostEvent::Frame { connection_id: id, frame, allocation, received_at: None, expires_at: None })?;
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
    use std::collections::BTreeMap;
    #[test]
    fn peer_and_room_share_an_endpoint_without_sharing_admission_or_pairing_lanes() {
        let caller = EndpointHost::start(EndpointPolicy::local(),[71;32]).unwrap();
        let host = EndpointHost::start(EndpointPolicy::local(),[72;32]).unwrap();
        let (remote,ticket) = host.endpoint_info().unwrap();
        let local = caller.endpoint_info().unwrap().0;
        let mut calls = BTreeMap::new();
        for protocol in [PAIR_ALPN,SESSION_ALPN,ROOM_PAIR_ALPN,ROOM_SESSION_ALPN] {
            calls.insert(String::from_utf8(protocol.to_vec()).unwrap(),caller.dial_application(&ticket,&remote,protocol).unwrap());
        }
        assert!(caller.dial_application(&ticket,&remote,b"autoyou/unknown/1").is_err());
        let mut accepted = BTreeMap::new(); let mut connected = BTreeMap::new();
        let deadline = std::time::Instant::now()+Duration::from_secs(5);
        while accepted.len()<4 || connected.len()<4 {
            for event in caller.poll(64).unwrap() {
                if let HostEvent::Connected {connection_id,protocol,initiator,..} = event {
                    assert!(initiator); assert_eq!(calls.get(&protocol),Some(&connection_id)); connected.insert(protocol,connection_id);
                }
            }
            for event in host.poll(64).unwrap() {
                if let HostEvent::Connected {connection_id,protocol,initiator,..} = event {
                    assert!(!initiator); accepted.insert(protocol,connection_id);
                }
            }
            assert!(std::time::Instant::now()<deadline); std::thread::sleep(Duration::from_millis(5));
        }
        let principal = |endpoint: String,room: bool| Principal {endpoint_id:endpoint,
            device_id:if room {"synthetic-room-device"} else {"synthetic-peer-device"}.into(),
            owner_id:"synthetic-owner".into(),conversation_id:if room {"synthetic-room"} else {"synthetic-peer"}.into(),
            generation:1,authorization_epoch:1,expires_at_ms:now_ms()+60_000,
            scopes:vec!["chat".into(),if room {"room"} else {"peer"}.into()]};
        for protocol in [PAIR_ALPN,ROOM_PAIR_ALPN] {
            let name=String::from_utf8(protocol.to_vec()).unwrap(); let id=calls[&name];
            assert!(caller.admit(id,principal(remote.clone(),protocol==ROOM_PAIR_ALPN)).is_err());
            assert!(caller.send(id,Frame {lane:Lane::Application,generation:1,stream_id:0,sequence:0,payload:vec![]},None).is_err());
            let payload=autoyou_protocol::enrollment::Message {version:1,kind:autoyou_protocol::enrollment::Kind::Confirm,challenge:None,binding:Some(vec![3;32])}.to_vec().unwrap();
            caller.send(id,Frame {lane:Lane::Enrollment,generation:0,stream_id:0,sequence:0,payload},None).unwrap();
        }
        for room in [false,true] {
            let name=String::from_utf8(if room {ROOM_SESSION_ALPN} else {SESSION_ALPN}.to_vec()).unwrap();
            if room { assert!(caller.admit(calls[&name],principal(remote.clone(),false)).is_err()); }
            caller.admit(calls[&name],principal(remote.clone(),room)).unwrap();
            host.admit(accepted[&name],principal(local.clone(),room)).unwrap();
            caller.activate(calls[&name]).unwrap(); host.activate(accepted[&name]).unwrap();
            let payload=serde_json::to_vec(&serde_json::json!({"header":{"message_id":"synthetic-message","message_type":if room {"room_chat"} else {"chat"},"timestamp":1},"payload":{"text":"synthetic-body"}})).unwrap();
            caller.send(calls[&name],Frame {lane:Lane::Application,generation:1,stream_id:0,sequence:0,payload},None).unwrap();
            if !room {
                let payload=serde_json::to_vec(&serde_json::json!({"header":{"message_id":"synthetic-message","message_type":"room_chat","timestamp":1},"payload":{}})).unwrap();
                assert!(caller.send(calls[&name],Frame {lane:Lane::Application,generation:1,stream_id:0,sequence:0,payload},None).is_err());
            }
        }
        let deadline=std::time::Instant::now()+Duration::from_secs(5); let mut received=BTreeMap::new();
        while received.len()<2 {
            for event in host.poll(64).unwrap() {
                if let HostEvent::Frame {connection_id,frame,..} = event { if frame.lane==Lane::Application {
                    received.insert(connection_id,Envelope::from_slice(&frame.payload).unwrap().header.message_type);
                }}
            }
            assert!(std::time::Instant::now()<deadline); std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(received[&accepted["autoyou/session/1"]],autoyou_protocol::MessageType::Chat);
        assert_eq!(received[&accepted["autoyou/room-session/1"]],autoyou_protocol::MessageType::RoomChat);
        caller.shutdown().unwrap(); host.shutdown().unwrap();
    }
    #[test]
    fn input_send_deadline_is_mandatory_and_bounded() {
        assert_eq!(super::input_send_deadline(Some(300), 100).unwrap(), 300);
        assert!(matches!(super::input_send_deadline(None, 100), Err(super::HostError::InvalidConfig)));
        assert!(matches!(super::input_send_deadline(Some(301), 100), Err(super::HostError::InvalidConfig)));
        assert!(matches!(super::input_send_deadline(Some(100), 100), Err(super::HostError::Timeout)));
    }

    #[test]
    fn input_transport_sequence_allocation_preserves_application_proof() {
        let mut frame = autoyou_protocol::Frame { lane: autoyou_protocol::Lane::Input, generation: 1,
            stream_id: 0, sequence: 1, payload: br#"{"native_input":{"sequence":1}}"#.to_vec() };
        let proof = frame.payload.clone();
        assert_eq!(super::assign_transport_sequence(&mut frame, 0).unwrap(), 1);
        assert_eq!(frame.sequence, 0); assert_eq!(frame.payload, proof);
        assert_eq!(super::assign_transport_sequence(&mut frame, 4).unwrap(), 5);
        assert_eq!(frame.sequence, 4); assert_eq!(frame.payload, proof);
        assert!(super::assign_transport_sequence(&mut frame, u64::MAX).is_err());
    }
    use super::*;
    fn upload_fixture()->(autoyou_protocol::byte_stream::Writer,autoyou_protocol::byte_stream::Record) {
        use autoyou_protocol::byte_stream::{Writer,Content};
        let metadata=br#"{"header":{"message_id":"synthetic-upload","message_type":"http_request","timestamp":1},"payload":{"request_id":"synthetic-upload","method":"POST","url":"/synthetic"}}"#.to_vec();
        Writer::open(Lane::Http,Content::RawBody,2*1024*1024,metadata).unwrap()
    }
    #[test]
    fn upload_consumer_window_rejects_future_credit_and_preserves_a_blocked_record() {
        let (mut writer,open)=upload_fixture();let key=(4,3);let mut credits=UploadCredits::default();
        credits.pending.insert(key,credits.plan(key,&open).unwrap().unwrap());
        for _ in 0..8 {let record=writer.data(vec![7;49152]).unwrap();let next=credits.plan(key,&record).unwrap().unwrap();credits.pending.insert(key,next);}
        let blocked=writer.data(vec![7;49152]).unwrap();
        assert!(matches!(credits.plan(key,&blocked),Err(HostError::Backpressure)));
        assert_eq!(credits.pending[&key],(UPLOAD_WINDOW_BYTES,0));
        assert!(credits.consumed(key,UPLOAD_WINDOW_BYTES+1).is_err());
        credits.consumed(key,49152).unwrap();credits.consumed(key,49152).unwrap();
        assert!(credits.consumed(key,1).is_err());assert!(credits.consumed((4,5),49152).is_err());
        let next=credits.plan(key,&blocked).unwrap().unwrap();credits.pending.insert(key,next);
        assert_eq!(credits.pending[&key],(9*49152,49152));
    }
    #[test]
    fn upload_connection_pool_caps_parallel_unconsumed_prefixes() {
        let mut credits=UploadCredits::default();
        for stream in 0..10 {
            let (mut writer,open)=upload_fixture();let key=(4,3+2*stream);
            credits.pending.insert(key,credits.plan(key,&open).unwrap().unwrap());
            for _ in 0..8 {let record=writer.data(vec![7;49152]).unwrap();let next=credits.plan(key,&record).unwrap().unwrap();credits.pending.insert(key,next);}
        }
        let (mut writer,open)=upload_fixture();let key=(4,23);credits.pending.insert(key,credits.plan(key,&open).unwrap().unwrap());
        for _ in 0..5 {let record=writer.data(vec![7;49152]).unwrap();let next=credits.plan(key,&record).unwrap().unwrap();credits.pending.insert(key,next);}
        let blocked=writer.data(vec![7;49152]).unwrap();assert!(matches!(credits.plan(key,&blocked),Err(HostError::Backpressure)));
        credits.consumed((4,3),UPLOAD_WINDOW_BYTES).unwrap();assert!(credits.plan(key,&blocked).is_ok());
    }
    #[test]
    fn canceling_an_owned_pending_dial_ends_handshake_without_publishing_connection() {
        let host = EndpointHost::start(EndpointPolicy::local(), [67;32]).unwrap();
        let remote = SecretKey::from_bytes(&[68;32]).public();
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        let ticket = EndpointTicket::new(EndpointAddr::new(remote).with_ip_addr(socket.local_addr().unwrap())).to_string();
        let id = host.dial(&ticket, &remote.to_string(), false).unwrap();
        host.disconnect(id).unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        let mut finished = false;
        while !finished {
            for event in host.poll(64).unwrap() {
                assert!(!matches!(event, HostEvent::Connected { .. }));
                if matches!(event, HostEvent::Failed { request_id, .. } if request_id == id) { finished = true; }
            }
            assert!(std::time::Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(host.shared.pending_dials.lock().unwrap().is_empty());
        assert!(host.shared.slots.lock().unwrap().is_empty());
        host.shutdown().unwrap();
    }
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
