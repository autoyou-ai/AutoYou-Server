// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Hermetic authenticated-host media boundary, using synthetic loopback peers.
//! No capture, real devices, business calls, public relay or application E2E.

use autoyou_protocol::{Frame, FrameHeader, Lane, Principal, SESSION_ALPN,
    media::{Codec, MediaHeader, MediaKind}};
use autoyou_session::{host::{EndpointHost, EndpointPolicy, HostEvent}, local_endpoint,
    media::{SourceLease, MEDIA_EXPIRED_CODE}, write_frame};
use iroh::{Endpoint, SecretKey, endpoint::{Connection, PortmapperConfig, QuicTransportConfig, presets}};
use iroh_tickets::endpoint::EndpointTicket;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn now_ms() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64 }
fn lease(delay_ms: u16) -> SourceLease {
    SourceLease { lease_id: "synthetic-media-lease".into(), call_id: "synthetic-call".into(),
        participant_id: "synthetic-participant".into(), target_id: "synthetic-target".into(), source_id: 9,
        media_generation: 2, authorization_epoch: 3, expires_at_ms: now_ms() + 30_000,
        kind: 1, codec: 4, sample_rate: 48_000, channels: 1, width: 0, height: 0, fps: 0,
        layout: "single".into(), maximum_delay_ms: delay_ms }
}
fn frame(sequence: u64, media_generation: u64) -> Frame {
    let header = MediaHeader { kind: MediaKind::Audio, codec: Codec::Pcm16, keyframe: false,
        generation: media_generation, authorization_epoch: 3, source_id: 9, sequence,
        timestamp_us: sequence * 20_000, duration_us: 20_000, length: 1920,
        width: 0, height: 0, channels: 1 };
    let mut payload = header.encode().unwrap().to_vec(); payload.extend(vec![7; 1920]);
    Frame { lane: Lane::Media, generation: 7, stream_id: 9, sequence, payload }
}
async fn event(host: &EndpointHost) -> HostEvent {
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let mut events = host.poll(1).unwrap();
            if !events.is_empty() { return events.remove(0); }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    }).await.unwrap()
}
async fn fixture(small_window: bool) -> (EndpointHost, Endpoint, Connection, u64) {
    let host = EndpointHost::start(EndpointPolicy::local(), [91;32]).unwrap();
    let (_, ticket) = host.endpoint_info().unwrap(); let ticket: EndpointTicket = ticket.parse().unwrap();
    let peer = if small_window {
        Endpoint::builder(presets::Minimal).secret_key(SecretKey::from_bytes(&[92;32]))
            .alpns(vec![SESSION_ALPN.to_vec()]).clear_ip_transports().bind_addr("127.0.0.1:0".parse::<std::net::SocketAddr>().unwrap()).unwrap()
            .relay_mode(iroh::RelayMode::Disabled).clear_address_lookup().portmapper_config(PortmapperConfig::Disabled)
            .transport_config(QuicTransportConfig::builder().stream_receive_window(4096u32.into())
                .receive_window(65536u32.into()).build()).bind().await.unwrap()
    } else { local_endpoint(SecretKey::from_bytes(&[92;32]), vec![SESSION_ALPN.to_vec()]).await.unwrap() };
    let connection = peer.connect(ticket.endpoint_addr().clone(), SESSION_ALPN).await.unwrap();
    let id = match event(&host).await { HostEvent::Connected { connection_id, .. } => connection_id, _ => panic!("missing connection") };
    host.admit(id, Principal { endpoint_id: peer.id().to_string(), device_id: "synthetic-media-device".into(),
        owner_id: "synthetic-owner".into(), conversation_id: "synthetic-conversation".into(),
        generation: 7, authorization_epoch: 3, expires_at_ms: now_ms() + 60_000,
        scopes: vec!["media".into(), "chat".into()] }).unwrap();
    host.activate(id).unwrap();
    (host, peer, connection, id)
}
async fn complete(connection: &Connection, frame: &Frame) {
    let mut send = connection.open_uni().await.unwrap(); write_frame(&mut send, frame).await.unwrap(); send.finish().unwrap();
    assert!(tokio::time::timeout(Duration::from_secs(3), send.stopped()).await.unwrap().unwrap().is_none());
}
async fn control(connection: &Connection, sequence: u64) {
    let payload = br#"{"header":{"message_id":"synthetic-ping","message_type":"ping","timestamp":1},"payload":{}}"#.to_vec();
    complete(connection, &Frame { lane: Lane::Control, generation: 7, stream_id: 1, sequence, payload }).await;
}
async fn finish(host: EndpointHost, peer: Endpoint, connection: Connection) {
    connection.close(0u32.into(), b"synthetic media fixture complete"); peer.close().await; host.shutdown().unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn media_stalled_sequence_and_reset_do_not_block_later_frame_or_control() {
    let (host, peer, connection, id) = fixture(false).await;
    host.approve_media_source(id, lease(2000), true).unwrap();
    let mut stalled = connection.open_uni().await.unwrap();
    let encoded = frame(0, 2).encode().unwrap(); stalled.write_all(&encoded[..108]).await.unwrap();
    complete(&connection, &frame(1, 2)).await;
    let delivered = tokio::time::timeout(Duration::from_secs(1), event(&host)).await.unwrap();
    assert!(matches!(delivered, HostEvent::Frame { frame: Frame { lane: Lane::Media, sequence: 1, .. }, .. }));
    drop(delivered);
    stalled.reset(MEDIA_EXPIRED_CODE.into()).unwrap();
    control(&connection, 0).await;
    assert!(matches!(event(&host).await, HostEvent::Frame { frame: Frame { lane: Lane::Control, .. }, .. }));
    complete(&connection, &frame(3, 2)).await;
    assert!(matches!(event(&host).await, HostEvent::Frame { frame: Frame { lane: Lane::Media, sequence: 3, .. }, .. }));
    assert_eq!(host.diagnostics(id).unwrap().held_receive_bytes, 0);
    finish(host, peer, connection).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn media_receiver_deadline_and_revocation_release_partial_frames_only() {
    let (host, peer, connection, id) = fixture(false).await;
    host.approve_media_source(id, lease(50), true).unwrap();
    let mut stalled = connection.open_uni().await.unwrap(); stalled.write_all(&frame(0, 2).encode().unwrap()[..108]).await.unwrap();
    let stopped = tokio::time::timeout(Duration::from_secs(2), stalled.stopped()).await.unwrap().unwrap();
    assert_eq!(stopped, Some(MEDIA_EXPIRED_CODE.into()));
    assert!(host.poll(64).unwrap().is_empty());
    assert_eq!(host.diagnostics(id).unwrap().held_receive_bytes, 0);
    let mut replacement = lease(2000); replacement.media_generation = 3;
    host.approve_media_source(id, replacement, true).unwrap();
    let mut stalled = connection.open_uni().await.unwrap(); stalled.write_all(&frame(1, 3).encode().unwrap()[..108]).await.unwrap();
    tokio::time::timeout(Duration::from_secs(1), async {
        while host.diagnostics(id).unwrap().held_receive_bytes == 0 { tokio::time::sleep(Duration::from_millis(2)).await; }
    }).await.unwrap();
    host.revoke_media_source(id, 9, true).unwrap();
    assert_eq!(tokio::time::timeout(Duration::from_secs(2), stalled.stopped()).await.unwrap().unwrap(), Some(MEDIA_EXPIRED_CODE.into()));
    control(&connection, 0).await;
    assert!(matches!(event(&host).await, HostEvent::Frame { frame: Frame { lane: Lane::Control, .. }, .. }));
    finish(host, peer, connection).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn media_reset_before_header_never_delivers_and_preserves_other_streams() {
    let (host, peer, connection, id) = fixture(false).await;
    host.approve_media_source(id, lease(200), true).unwrap();
    let mut canceled = connection.open_uni().await.unwrap(); canceled.write_all(b"AY").await.unwrap();
    canceled.reset(MEDIA_EXPIRED_CODE.into()).unwrap();
    complete(&connection, &frame(5, 2)).await;
    assert!(matches!(event(&host).await, HostEvent::Frame { frame: Frame { lane: Lane::Media, sequence: 5, .. }, .. }));
    control(&connection, 0).await;
    assert!(matches!(event(&host).await, HostEvent::Frame { frame: Frame { lane: Lane::Control, .. }, .. }));
    finish(host, peer, connection).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn media_completed_frame_expires_while_waiting_for_foreign_consumer() {
    let (host, peer, connection, id) = fixture(false).await;
    host.approve_media_source(id, lease(200), true).unwrap();
    complete(&connection, &frame(0, 2)).await;
    tokio::time::timeout(Duration::from_secs(1), async {
        while host.diagnostics(id).unwrap().held_receive_bytes == 0 { tokio::time::sleep(Duration::from_millis(2)).await; }
    }).await.unwrap();
    tokio::time::sleep(Duration::from_millis(250)).await;
    assert!(host.poll(64).unwrap().is_empty());
    assert_eq!(host.diagnostics(id).unwrap().held_receive_bytes, 0);
    complete(&connection, &frame(2, 2)).await;
    assert!(matches!(event(&host).await, HostEvent::Frame { frame: Frame { lane: Lane::Media, sequence: 2, .. }, .. }));
    finish(host, peer, connection).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn media_queued_frame_is_fenced_by_revocation_and_explicit_reapproval() {
    let (host, peer, connection, id) = fixture(false).await;
    host.approve_media_source(id, lease(2000), true).unwrap();
    complete(&connection, &frame(0, 2)).await;
    host.revoke_media_source(id, 9, true).unwrap();
    control(&connection, 0).await;
    assert!(matches!(event(&host).await, HostEvent::Frame { frame: Frame { lane: Lane::Control, .. }, .. }));
    let mut next = lease(2000); next.media_generation = 3;
    host.approve_media_source(id, next, true).unwrap();
    complete(&connection, &frame(0, 3)).await;
    assert!(matches!(event(&host).await, HostEvent::Frame { frame: Frame { lane: Lane::Media, sequence: 0, .. }, .. }));
    finish(host, peer, connection).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn media_late_retired_source_or_generation_drops_only_that_stream() {
    let (host, peer, connection, id) = fixture(false).await;
    host.approve_media_source(id, lease(2000), true).unwrap();
    host.revoke_media_source(id, 9, true).unwrap();
    let mut late = connection.open_uni().await.unwrap();
    late.write_all(&frame(0,2).encode().unwrap()[..36]).await.unwrap();
    assert_eq!(tokio::time::timeout(Duration::from_secs(2),late.stopped()).await.unwrap().unwrap(), Some(MEDIA_EXPIRED_CODE.into()));
    assert_eq!(host.diagnostics(id).unwrap().held_receive_bytes,0);
    let mut next = lease(2000); next.media_generation = 3;
    host.approve_media_source(id,next,true).unwrap();
    let mut old = connection.open_uni().await.unwrap();
    old.write_all(&frame(1,2).encode().unwrap()[..100]).await.unwrap();
    assert_eq!(tokio::time::timeout(Duration::from_secs(2),old.stopped()).await.unwrap().unwrap(), Some(MEDIA_EXPIRED_CODE.into()));
    assert_eq!(host.diagnostics(id).unwrap().held_receive_bytes,0);
    control(&connection,0).await;
    assert!(matches!(event(&host).await,HostEvent::Frame { frame: Frame { lane: Lane::Control, .. }, .. }));
    complete(&connection,&frame(0,3)).await;
    assert!(matches!(event(&host).await,HostEvent::Frame { frame: Frame { lane: Lane::Media, .. }, .. }));
    finish(host,peer,connection).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn media_unapproved_source_is_rejected_before_encoded_body_allocation() {
    let (host, peer, connection, id) = fixture(false).await;
    assert_eq!(host.diagnostics(id).unwrap().held_receive_bytes, 0);
    let mut send = connection.open_uni().await.unwrap();
    send.write_all(&FrameHeader { lane: Lane::Media, generation: 7, stream_id: 9,
        sequence: 0, length: 4*1024*1024 + 64 }.encode().unwrap()).await.unwrap();
    tokio::time::timeout(Duration::from_secs(3), connection.closed()).await.unwrap();
    assert!(matches!(event(&host).await, HostEvent::Closed { .. }));
    assert!(host.poll(64).unwrap().is_empty());
    finish(host, peer, connection).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn media_sender_deadline_resets_stalled_writer_without_closing_session() {
    let (host, peer, connection, id) = fixture(true).await;
    let mut source = lease(200); source.kind = 3; source.codec = 2;
    source.sample_rate = 0; source.channels = 0; source.width = 640; source.height = 360; source.fps = 30;
    host.approve_media_source(id, source, false).unwrap();
    let header = MediaHeader { kind: MediaKind::Screen, codec: Codec::H264, keyframe: true,
        generation: 2, authorization_epoch: 3, source_id: 9, sequence: 0, timestamp_us: 0,
        duration_us: 33_333, length: 1024*1024, width: 640, height: 360, channels: 0 };
    let mut payload = header.encode().unwrap().to_vec(); payload.extend(vec![7; 1024*1024]);
    host.send(id, Frame { lane: Lane::Media, generation: 7, stream_id: 9, sequence: 0, payload }, Some(now_ms()+200)).unwrap();
    let mut recv = tokio::time::timeout(Duration::from_secs(2), connection.accept_uni()).await.unwrap().unwrap();
    // Stop reading after the outer header; peer's small QUIC receive window
    // physically blocks the encoded body writer until its retained deadline.
    let mut outer = [0;36]; recv.read_exact(&mut outer).await.unwrap();
    tokio::time::sleep(Duration::from_millis(250)).await;
    assert_eq!(host.diagnostics(id).unwrap().held_send_bytes, 0);
    let mut remaining = vec![0;8192];
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            match recv.read(&mut remaining).await {
                Err(_) => break,
                Ok(Some(_)) => {},
                Ok(None) => panic!("expired sender must reset its frame stream"),
            }
        }
    }).await.unwrap();
    control(&connection, 0).await;
    assert!(matches!(event(&host).await, HostEvent::Frame { frame: Frame { lane: Lane::Control, .. }, .. }));
    finish(host, peer, connection).await;
}
