// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use autoyou_protocol::{Frame, Lane, Principal};
use autoyou_session::host::{EndpointHost, EndpointPolicy, HostError, HostEvent};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

fn wait(host: &EndpointHost, predicate: impl Fn(&HostEvent) -> bool) -> HostEvent {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        for event in host.poll(64).unwrap() {
            if predicate(&event) { return event; }
        }
        assert!(Instant::now() < deadline, "local component event timed out");
        std::thread::sleep(Duration::from_millis(5));
    }
}
fn principal(endpoint: String, device: &str, generation: u64) -> Principal {
    let expiry = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64 + 60_000;
    Principal { endpoint_id: endpoint, device_id: device.into(), owner_id: "synthetic-owner".into(),
        conversation_id: "synthetic-conversation".into(), generation, authorization_epoch: 2,
        expires_at_ms: expiry, scopes: vec!["chat".into(), "browser".into(), "files".into()] }
}
fn frame(lane: Lane, generation: u64, stream_id: u64, payload: &[u8]) -> Frame {
    Frame { lane, generation, stream_id, sequence: 0, payload: payload.to_vec() }
}

#[test]
fn owned_host_restricts_pre_auth_then_delivers_in_order_and_shuts_down() {
    let server = EndpointHost::start(EndpointPolicy::local(), [31;32]).unwrap();
    let client = EndpointHost::start(EndpointPolicy::local(), [32;32]).unwrap();
    let (server_id, ticket) = server.endpoint_info().unwrap();
    let (client_id, _) = client.endpoint_info().unwrap();
    assert!(client.dial(&ticket, &client_id, false).is_err());
    let client_connection = client.dial(&ticket, &server_id, false).unwrap();
    let connected = wait(&client, |event| matches!(event, HostEvent::Connected { .. }));
    let client_exporter = match connected {
        HostEvent::Connected { connection_id, endpoint_id, exporter, initiator, protocol } => {
            assert_eq!(protocol, "autoyou/session/1");
            assert_eq!(connection_id, client_connection); assert_eq!(endpoint_id, server_id); assert!(initiator); exporter
        }, _ => unreachable!(),
    };
    let server_connection = match wait(&server, |event| matches!(event, HostEvent::Connected { .. })) {
        HostEvent::Connected { connection_id, endpoint_id, exporter, initiator, protocol } => {
            assert_eq!(protocol, "autoyou/session/1");
            assert_eq!(endpoint_id, client_id); assert!(!initiator); assert_eq!(exporter, client_exporter); connection_id
        }, _ => unreachable!(),
    };
    assert!(matches!(client.send(client_connection, frame(Lane::Application, 1, 1, b"denied"), None), Err(HostError::NotAuthorized)));
    assert!(matches!(client.diagnostics(client_connection), Err(HostError::NotAuthorized)));
    client.send(client_connection, frame(Lane::Enrollment, 0, 0, b"synthetic-enrollment"), None).unwrap();
    let proof = wait(&server, |event| matches!(event, HostEvent::Frame { .. }));
    assert!(matches!(proof, HostEvent::Frame { frame: Frame { lane: Lane::Enrollment, .. }, .. }));
    server.admit(server_connection, principal(client_id, "synthetic-client-device", 1)).unwrap();
    client.admit(client_connection, principal(server_id, "synthetic-server-device", 1)).unwrap();
    assert!(matches!(client.send(client_connection, frame(Lane::Binary, 1, 1, b"not activated"), None), Err(HostError::NotAuthorized)));
    client.activate(client_connection).unwrap();
    let diagnostics = client.diagnostics(client_connection).unwrap();
    assert_eq!(diagnostics.generation, 1);
    assert_eq!(diagnostics.authorization_epoch, 2);
    assert_eq!(diagnostics.path_kind, "direct");
    assert!(diagnostics.open_paths > 0 && diagnostics.rtt_ms.is_some());
    assert!(diagnostics.held_send_bytes <= 16*1024*1024 && diagnostics.held_receive_bytes <= 16*1024*1024);
    for index in 0..32 {
        client.send(client_connection, frame(Lane::Binary, 1, 1, &[index]), None).unwrap();
    }
    client.send(client_connection, frame(Lane::Enrollment, 0, 0, b"synthetic-confirmation"), None).unwrap();
    // App data stays in the bounded Rust queue until the host activates the
    // session. Enrollment can progress even while that data is deferred.
    let confirmation = wait(&server, |event| {
        if let HostEvent::Frame { frame, .. } = event { assert_eq!(frame.lane, Lane::Enrollment); }
        matches!(event, HostEvent::Frame { .. })
    });
    assert!(matches!(confirmation, HostEvent::Frame { frame: Frame { lane: Lane::Enrollment, .. }, .. }));
    server.activate(server_connection).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut received = Vec::new();
    while received.len() < 32 {
        for event in server.poll(64).unwrap() {
            if let HostEvent::Frame { frame, .. } = event {
                assert_eq!(frame.sequence, received.len() as u64);
                received.push(frame.payload[0]);
            }
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(received, (0..32).collect::<Vec<u8>>());
    client.retire_stream(client_connection, Lane::Binary as u8, 1).unwrap();
    assert!(matches!(client.send(client_connection, frame(Lane::Binary, 1, 1, b"retired"), None), Err(HostError::NotAuthorized)));
    let chat = br#"{"header":{"message_id":"synthetic-chat","message_type":"chat","timestamp":1.0},"payload":{"text":"hello"}}"#;
    assert!(matches!(client.send(client_connection, frame(Lane::Control, 1, 2, chat), None), Err(HostError::NotAuthorized)));
    client.send(client_connection, frame(Lane::Application, 1, 2, chat), None).unwrap();
    let message = wait(&server, |event| matches!(event, HostEvent::Frame { .. }));
    assert!(matches!(message, HostEvent::Frame { frame: Frame { lane: Lane::Application, .. }, .. }));
    server.disconnect(server_connection).unwrap();
    assert!(matches!(server.send(server_connection, frame(Lane::Application, 1, 1, b"stale"), None), Err(HostError::NotAuthorized) | Err(HostError::Closed)));
    let started = Instant::now();
    client.shutdown().unwrap(); server.shutdown().unwrap();
    assert!(started.elapsed() < Duration::from_secs(5));
    assert!(matches!(client.endpoint_info(), Err(HostError::Closed)));
}

#[test]
fn pairing_alpn_cannot_become_general_application_transport() {
    let server = EndpointHost::start(EndpointPolicy::local(), [41;32]).unwrap();
    let client = EndpointHost::start(EndpointPolicy::local(), [42;32]).unwrap();
    let (server_id, ticket) = server.endpoint_info().unwrap();
    let (client_id, _) = client.endpoint_info().unwrap();
    let id = client.dial(&ticket, &server_id, true).unwrap();
    wait(&client, |event| matches!(event, HostEvent::Connected { .. }));
    let server_id = match wait(&server, |event| matches!(event, HostEvent::Connected { .. })) {
        HostEvent::Connected { connection_id, .. } => connection_id, _ => unreachable!(),
    };
    assert!(matches!(server.admit(server_id, principal(client_id, "synthetic-paired-device", 1)), Err(HostError::NotAuthorized)));
    assert!(matches!(server.send(server_id, frame(Lane::Application, 1, 1, b"denied"), None), Err(HostError::NotAuthorized)));
    client.send(id, frame(Lane::Enrollment, 0, 0, b"synthetic-final-confirmation"), None).unwrap();
    wait(&server, |event| matches!(event, HostEvent::Frame { .. }));
    client.shutdown().unwrap(); server.shutdown().unwrap();
}

#[test]
fn invalid_configuration_never_binds_and_repeated_shutdown_is_idempotent() {
    let mut policy = EndpointPolicy::local(); policy.bind_addresses = vec!["0.0.0.0:0".into()];
    assert!(matches!(EndpointHost::start(policy, [51;32]), Err(HostError::InvalidConfig)));
    for _ in 0..5 {
        let host = EndpointHost::start(EndpointPolicy::local(), [52;32]).unwrap();
        host.shutdown().unwrap(); host.shutdown().unwrap();
    }
}
