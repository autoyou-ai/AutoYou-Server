// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Maintained Iroh primitive qualification and bounded AutoYou frame IO.

use autoyou_protocol::{Frame, FrameHeader, HEADER_BYTES, ProtocolError};
use iroh::{
    Endpoint, RelayMode, SecretKey,
    endpoint::{Connection, PortmapperConfig, RecvStream, SendStream, presets},
};
use std::net::SocketAddr;

pub mod scheduler;
pub mod ordering;
pub mod host;

#[derive(Debug, thiserror::Error)]
pub enum SessionError {
    #[error(transparent)]
    Protocol(#[from] ProtocolError),
    #[error("endpoint initialization failed")]
    Endpoint,
    #[error("connection IO failed")]
    Io,
}

/// Explicit local fixture configuration: no public lookup, relay, gateway
/// mapping or non-loopback bind. Production policy is added separately.
pub async fn local_endpoint(key: SecretKey, alpns: Vec<Vec<u8>>) -> Result<Endpoint, SessionError> {
    Endpoint::builder(presets::Minimal)
        .secret_key(key)
        .alpns(alpns)
        .clear_ip_transports()
        .bind_addr(SocketAddr::from(([127, 0, 0, 1], 0)))
        .map_err(|_| SessionError::Endpoint)?
        .relay_mode(RelayMode::Disabled)
        .clear_address_lookup()
        .portmapper_config(PortmapperConfig::Disabled)
        .bind()
        .await
        .map_err(|_| SessionError::Endpoint)
}

pub async fn write_frame(send: &mut SendStream, frame: &Frame) -> Result<(), SessionError> {
    let header = FrameHeader { stream_id: frame.stream_id, sequence: frame.sequence, lane: frame.lane,
        generation: frame.generation, length: frame.payload.len() }.encode()?;
    send.write_all(&header).await.map_err(|_| SessionError::Io)?;
    send.write_all(&frame.payload).await.map_err(|_| SessionError::Io)?;
    Ok(())
}

pub async fn read_frame(recv: &mut RecvStream) -> Result<Frame, SessionError> {
    let mut bytes = [0u8; HEADER_BYTES];
    recv.read_exact(&mut bytes).await.map_err(|_| SessionError::Io)?;
    let header = FrameHeader::decode(&bytes)?;
    // Decode and validate the bound before allocating the payload.
    let mut payload = vec![0u8; header.length];
    recv.read_exact(&mut payload).await.map_err(|_| SessionError::Io)?;
    Ok(Frame { lane: header.lane, generation: header.generation, stream_id: header.stream_id, sequence: header.sequence, payload })
}

pub fn connection_binding(connection: &Connection, context: &[u8]) -> Result<[u8; 32], SessionError> {
    let mut bytes = [0u8; 32];
    connection.export_keying_material(&mut bytes, b"EXPORTER-AutoYou-session-v1", context).map_err(|_| SessionError::Io)?;
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use autoyou_protocol::{Lane, SESSION_ALPN};
    use iroh::EndpointAddr;
    use std::time::Duration;

    #[tokio::test]
    async fn local_iroh_primitive_authenticates_keys_and_binds_bounded_frame() {
        tokio::time::timeout(Duration::from_secs(10), async {
            let server = local_endpoint(SecretKey::from_bytes(&[1;32]), vec![SESSION_ALPN.to_vec()]).await.unwrap();
            let client = local_endpoint(SecretKey::from_bytes(&[2;32]), vec![SESSION_ALPN.to_vec()]).await.unwrap();
            let server_id = server.id();
            let client_id = client.id();
            let address = EndpointAddr::new(server_id).with_ip_addr(server.bound_sockets()[0]);
            let accept = tokio::spawn({
                let server = server.clone();
                async move {
                    let conn = server.accept().await.unwrap().await.unwrap();
                    assert_eq!(conn.remote_id(), client_id);
                    let binding = connection_binding(&conn, b"synthetic-context").unwrap();
                    let (mut send, mut recv) = conn.accept_bi().await.unwrap();
                    let frame = read_frame(&mut recv).await.unwrap();
                    assert_eq!(frame.payload, b"synthetic-component-payload");
                    write_frame(&mut send, &frame).await.unwrap();
                    send.finish().unwrap();
                    // Keep the connection alive until the peer acknowledges the
                    // response. Dropping its last handle immediately can close
                    // QUIC before the FIN/payload reaches the receiver.
                    assert!(send.stopped().await.unwrap().is_none());
                    binding
                }
            });
            let conn = client.connect(address, SESSION_ALPN).await.unwrap();
            assert_eq!(conn.remote_id(), server_id);
            let binding = connection_binding(&conn, b"synthetic-context").unwrap();
            let (mut send, mut recv) = conn.open_bi().await.unwrap();
            write_frame(&mut send, &Frame { stream_id: 0, sequence: 0, lane: Lane::Application, generation: 7, payload: b"synthetic-component-payload".to_vec() }).await.unwrap();
            send.finish().unwrap();
            let response = read_frame(&mut recv).await.unwrap();
            assert_eq!(response.generation, 7);
            assert_eq!(accept.await.unwrap(), binding);
            conn.close(0u32.into(), b"fixture complete");
            client.close().await;
            server.close().await;
        }).await.unwrap();
    }
}
