// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use autoyou_protocol::{Frame, Lane, Principal};
use autoyou_session::host::{EndpointHost, EndpointPolicy, HostError, HostEvent};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use autoyou_protocol::{Envelope, byte_stream::{Writer, Receiver, Record, Kind, Content, MAX_DATA_BYTES}};
use sha2::{Digest, Sha256};

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
fn outgoing_device_floors_are_remote_authority_scoped_and_incoming_pins_remain_global() {
    let first = EndpointHost::start(EndpointPolicy::local(), [141;32]).unwrap();
    let second = EndpointHost::start(EndpointPolicy::local(), [143;32]).unwrap();
    let client = EndpointHost::start(EndpointPolicy::local(), [145;32]).unwrap();
    let clone = EndpointHost::start(EndpointPolicy::local(), [147;32]).unwrap();
    let (client_id, _) = client.endpoint_info().unwrap();
    let connect = |target: &EndpointHost, generation| {
        let (remote, ticket) = target.endpoint_info().unwrap();
        let outgoing = client.dial(&ticket, &remote, false).unwrap();
        wait(&client, |event| matches!(event, HostEvent::Connected { connection_id, .. } if *connection_id == outgoing));
        let incoming = match wait(target, |event| matches!(event, HostEvent::Connected { .. })) {
            HostEvent::Connected { connection_id, .. } => connection_id, _ => unreachable!(),
        };
        target.admit(incoming, principal(client_id.clone(), "synthetic-local-device", generation)).unwrap();
        // These independently approved authorities may use the same device ID.
        client.admit(outgoing, principal(remote, "synthetic-local-device", generation)).unwrap();
        target.activate(incoming).unwrap(); client.activate(outgoing).unwrap();
        outgoing
    };
    let a = connect(&first, 1);
    let b = connect(&second, 1);
    let chat = br#"{"header":{"message_id":"synthetic-authority-chat","message_type":"chat","timestamp":1.0},"payload":{"text":"synthetic"}}"#;
    for (host, connection) in [(&first, a), (&second, b)] {
        client.send(connection, frame(Lane::Application, 1, 2, chat), None).unwrap();
        assert!(matches!(wait(host, |event| matches!(event, HostEvent::Frame { .. })), HostEvent::Frame { .. }));
    }
    let replacement = connect(&first, 2);
    let (first_id, ticket) = first.endpoint_info().unwrap();
    assert!(matches!(client.admit(replacement, principal(first_id.clone(), "synthetic-local-device", 2)), Err(HostError::NotAuthorized)));
    assert!(matches!(client.admit(replacement, principal(first_id.clone(), "synthetic-alias", 3)), Err(HostError::NotAuthorized)));
    // Superseding first must leave the unrelated authority's generation alive.
    client.send(b, frame(Lane::Application, 1, 2, chat), None).unwrap();
    assert!(matches!(wait(&second, |event| matches!(event, HostEvent::Frame { .. })), HostEvent::Frame { .. }));
    clone.dial(&ticket, &first_id, false).unwrap();
    wait(&clone, |event| matches!(event, HostEvent::Connected { .. }));
    let incoming = match wait(&first, |event| matches!(event, HostEvent::Connected { .. })) {
        HostEvent::Connected { connection_id, .. } => connection_id, _ => unreachable!(),
    };
    let (clone_id, _) = clone.endpoint_info().unwrap();
    assert!(matches!(first.admit(incoming, principal(clone_id, "synthetic-local-device", 3)), Err(HostError::NotAuthorized)));
    clone.shutdown().unwrap(); client.shutdown().unwrap(); first.shutdown().unwrap(); second.shutdown().unwrap();
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
    assert!(matches!(client.send(client_connection, frame(Lane::Binary, 1, 3, b"unframed file bytes"), None), Err(HostError::NotAuthorized)));
    let data: Vec<u8> = (0..32).flat_map(|index| vec![index; MAX_DATA_BYTES]).collect();
    let metadata = serde_json::to_vec(&serde_json::json!({"header":{"message_id":"synthetic-file", "message_type":"binary_transfer_open", "timestamp":1.0},
        "payload":{"transfer_id":"abababababababababababababababab", "purpose":"attachment", "filename":"synthetic.bin", "mime_type":"application/octet-stream",
            "total":data.len(), "offset":0, "sha256":Sha256::digest(&data).to_vec(), "expires_at_ms":60000, "metadata":{}}})).unwrap();
    let (mut writer, open) = Writer::open(Lane::Binary, Content::RawFile, data.len() as u64, metadata).unwrap();
    client.send(client_connection, frame(Lane::Binary, 1, 3, &open.encode(Lane::Binary).unwrap()), None).unwrap();
    for bytes in data.chunks(MAX_DATA_BYTES) {
        client.send(client_connection, frame(Lane::Binary, 1, 3, &writer.data(bytes.to_vec()).unwrap().encode(Lane::Binary).unwrap()), None).unwrap();
    }
    client.send(client_connection, frame(Lane::Binary, 1, 3, &writer.finish().unwrap().encode(Lane::Binary).unwrap()), None).unwrap();
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
    let mut receiver = Receiver::default(); let mut sequence = 0; let mut finished = false;
    while !finished {
        for event in server.poll(64).unwrap() {
            if let HostEvent::Frame { frame, .. } = event {
                assert_eq!(frame.sequence, sequence); sequence += 1;
                let record = Record::decode(Lane::Binary, &frame.payload).unwrap(); receiver.accept(Lane::Binary, 3, &record).unwrap();
                match record.kind {
                    Kind::Open => { assert_eq!(Envelope::from_slice(&record.metadata).unwrap().lane(), Lane::Binary); }
                    Kind::Data => { received.push(record.data[0]); assert!(record.data.iter().all(|value| *value == record.data[0])); }
                    Kind::Finish => { server.acknowledge_stream(server_connection, Lane::Binary as u8, 3, record.total, record.digest.to_vec()).unwrap(); finished = true; }
                    _ => panic!("unexpected local file abort"),
                }
            }
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(received, (0..32).collect::<Vec<u8>>());
    assert_eq!(receiver.active_count(), 0);
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        client.poll(64).unwrap();
        if client.diagnostics(client_connection).unwrap().pending_stream_receipts == 0 { break; }
        assert!(Instant::now() < deadline); std::thread::sleep(Duration::from_millis(5));
    }
    assert!(matches!(client.send(client_connection, frame(Lane::Binary, 1, 3, &open.encode(Lane::Binary).unwrap()), None), Err(HostError::NotAuthorized)));
    // Abort consumes a prefix, including an empty prefix. Its receipt must
    // preserve this connection instead of being compared with the full length.
    for (lane, content, declared, consumed, stream) in [
        (Lane::Binary, Content::RawFile, (MAX_DATA_BYTES * 2) as u64, MAX_DATA_BYTES, 5),
        (Lane::Http, Content::RawBody, 8, 0, 7),
        (Lane::Http, Content::RawBody, u64::MAX, 4, 9),
    ] {
        let metadata = if lane == Lane::Binary {
            serde_json::to_vec(&serde_json::json!({"header":{"message_id":"synthetic-aborted-file", "message_type":"binary_transfer_open", "timestamp":1.0},
                "payload":{"transfer_id":"cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd", "purpose":"attachment", "filename":"synthetic-aborted.bin", "mime_type":"application/octet-stream",
                    "total":declared, "offset":0, "sha256":Sha256::digest(vec![5; declared as usize]).to_vec(), "expires_at_ms":60000, "metadata":{}}})).unwrap()
        } else {
            br#"{"header":{"message_id":"synthetic-aborted-upload","message_type":"http_request","timestamp":1.0},"payload":{"request_id":"synthetic-aborted-upload","method":"PUT","url":"/synthetic"}}"#.to_vec()
        };
        let (mut writer, open) = Writer::open(lane, content, declared, metadata).unwrap();
        client.send(client_connection, frame(lane, 1, stream, &open.encode(lane).unwrap()), None).unwrap();
        if consumed != 0 {
            let data = writer.data(vec![5; consumed]).unwrap();
            client.send(client_connection, frame(lane, 1, stream, &data.encode(lane).unwrap()), None).unwrap();
        }
        let abort = writer.cancel_at(consumed as u64).unwrap();
        client.send(client_connection, frame(lane, 1, stream, &abort.encode(lane).unwrap()), None).unwrap();
        let mut receiver = Receiver::default(); let mut aborted = false;
        let deadline = Instant::now() + Duration::from_secs(5);
        while !aborted {
            for event in server.poll(64).unwrap() {
                if let HostEvent::Frame { frame, .. } = event {
                    assert_eq!(frame.lane, lane); assert_eq!(frame.stream_id, stream);
                    let record = Record::decode(lane, &frame.payload).unwrap(); receiver.accept(lane, stream, &record).unwrap();
                    if record.kind == Kind::Abort {
                        assert_eq!(record.offset, consumed as u64);
                        server.acknowledge_stream(server_connection, lane as u8, stream, record.offset, record.digest.to_vec()).unwrap();
                        aborted = true;
                    }
                }
            }
            assert!(Instant::now() < deadline); std::thread::sleep(Duration::from_millis(5));
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            client.poll(64).unwrap();
            if client.diagnostics(client_connection).unwrap().pending_stream_receipts == 0 { break; }
            assert!(Instant::now() < deadline); std::thread::sleep(Duration::from_millis(5));
        }
        assert!(matches!(client.send(client_connection, frame(lane, 1, stream, &open.encode(lane).unwrap()), None), Err(HostError::NotAuthorized)));
    }
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
