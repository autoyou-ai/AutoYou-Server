// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use autoyou_protocol::{FrameHeader, Lane, SESSION_ALPN};
use autoyou_session::{host::{EndpointHost, EndpointPolicy, HostEvent}, local_endpoint};
use iroh::SecretKey;
use iroh_tickets::endpoint::EndpointTicket;
use std::time::Duration;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn raw_unadmitted_oversized_and_trailing_frames_never_reach_host_dispatch() {
    for kind in 0..3 {
        let server = EndpointHost::start(EndpointPolicy::local(), [61;32]).unwrap();
        let (_, ticket) = server.endpoint_info().unwrap();
        let ticket: EndpointTicket = ticket.parse().unwrap();
        let client = local_endpoint(SecretKey::from_bytes(&[62;32]), vec![SESSION_ALPN.to_vec()]).await.unwrap();
        let connection = client.connect(ticket.endpoint_addr().clone(), SESSION_ALPN).await.unwrap();
        let mut send = connection.open_uni().await.unwrap();
        let mut header = FrameHeader { lane: if kind == 0 { Lane::Control } else { Lane::Enrollment },
            generation: 0, stream_id: 0, sequence: 0, length: 0 }.encode().unwrap();
        if kind == 1 { header[32..36].copy_from_slice(&u32::MAX.to_be_bytes()); }
        send.write_all(&header).await.unwrap();
        if kind == 2 { send.write_all(b"unexpected trailing bytes").await.unwrap(); }
        send.finish().unwrap();
        tokio::time::timeout(Duration::from_secs(5), connection.closed()).await.unwrap();
        assert!(!server.poll(64).unwrap().iter().any(|event| matches!(event, HostEvent::Frame { .. })));
        client.close().await; server.shutdown().unwrap();
    }
}
