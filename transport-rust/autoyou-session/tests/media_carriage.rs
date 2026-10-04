// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Focused local transport qualification, not application E2E or a quality
//! benchmark. It models one missing/slow frame without using public networks.

use autoyou_protocol::{Frame, Lane, MEDIA_ALPN};
use autoyou_session::{local_endpoint, read_frame, write_frame};
use iroh::{EndpointAddr, SecretKey};
use std::time::Duration;

#[tokio::test]
async fn independent_frames_survive_stalled_frame_while_ordered_stream_blocks() {
    tokio::time::timeout(Duration::from_secs(10), async {
        let server = local_endpoint(SecretKey::from_bytes(&[11;32]), vec![MEDIA_ALPN.to_vec()]).await.unwrap();
        let client = local_endpoint(SecretKey::from_bytes(&[12;32]), vec![MEDIA_ALPN.to_vec()]).await.unwrap();
        let address = EndpointAddr::new(server.id()).with_ip_addr(server.bound_sockets()[0]);
        let (accepted, dialed) = tokio::join!(
            async { server.accept().await.unwrap().await.unwrap() },
            client.connect(address, MEDIA_ALPN)
        );
        let connection = dialed.unwrap();
        let frame = Frame { stream_id: 0, sequence: 0, lane: Lane::Media, generation: 1, payload: b"frame-two".to_vec() };

        // Ordered carriage: an incomplete first frame prevents reading the
        // next frame on this stream, even though other streams remain usable.
        let mut ordered = connection.open_uni().await.unwrap();
        let incomplete = Frame { stream_id: 0, sequence: 0, lane: Lane::Media, generation: 1, payload: vec![0; 1024] }.encode().unwrap();
        ordered.write_all(&incomplete[..37]).await.unwrap();
        let mut ordered_recv = accepted.accept_uni().await.unwrap();
        assert!(tokio::time::timeout(Duration::from_millis(50), read_frame(&mut ordered_recv)).await.is_err());

        // Per-frame carriage: later complete frame is delivered without
        // waiting for the stalled stream to finish. No foreign media library.
        let mut independent = connection.open_uni().await.unwrap();
        independent.set_priority(8).unwrap();
        write_frame(&mut independent, &frame).await.unwrap();
        independent.finish().unwrap();
        let mut next_recv = accepted.accept_uni().await.unwrap();
        let next = tokio::time::timeout(Duration::from_secs(1), read_frame(&mut next_recv)).await.unwrap().unwrap();
        assert_eq!(next, frame);
        assert!(independent.stopped().await.unwrap().is_none());

        // Datagrams support small packets, but a video frame requires an
        // application fragmentation/recovery protocol. Do not treat them as
        // an automatically equivalent frame carrier.
        let maximum = connection.max_datagram_size().unwrap();
        assert!(maximum < 64 * 1024);
        assert!(connection.send_datagram(vec![0; maximum + 1].into()).is_err());
        connection.send_datagram(b"small-synthetic-audio-packet".as_slice().into()).unwrap();
        assert_eq!(accepted.read_datagram().await.unwrap().as_ref(), b"small-synthetic-audio-packet");

        ordered.reset(1u32.into()).unwrap();
        connection.close(0u32.into(), b"qualification complete");
        client.close().await; server.close().await;
    }).await.unwrap();
}
