// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use std::sync::{Arc, Mutex};
use autoyou_protocol::{byte_stream as core, Envelope, MessageType, Lane};
use crate::BindingError;
use base64::{Engine as _, engine::general_purpose::STANDARD};

#[derive(Debug, Clone, uniffi::Enum)]
pub enum ByteStreamKind { Open, Data, Finish, Abort }
#[derive(Debug, Clone, uniffi::Enum)]
pub enum ByteStreamContent { None, RawBody, TextBody, Base64Body, TextData, BinaryData, WebSocketBinary, RawFile }
impl ByteStreamContent {
    fn core(&self) -> core::Content { match self {
        Self::None => core::Content::None, Self::RawBody => core::Content::RawBody,
        Self::TextBody => core::Content::TextBody, Self::Base64Body => core::Content::Base64Body,
        Self::TextData => core::Content::TextData, Self::BinaryData => core::Content::BinaryData,
        Self::WebSocketBinary => core::Content::WebSocketBinary,
        Self::RawFile => core::Content::RawFile,
    } }
}
#[derive(Debug, Clone, uniffi::Record)]
pub struct ByteStreamRecord {
    pub kind: ByteStreamKind, pub content: ByteStreamContent, pub offset: u64, pub total: u64,
    pub digest: Vec<u8>, pub metadata: Vec<u8>, pub data: Vec<u8>,
}
impl From<core::Record> for ByteStreamRecord {
    fn from(value: core::Record) -> Self {
        Self { kind: match value.kind { core::Kind::Open => ByteStreamKind::Open, core::Kind::Data => ByteStreamKind::Data,
            core::Kind::Finish => ByteStreamKind::Finish, core::Kind::Abort => ByteStreamKind::Abort },
            content: match value.content { core::Content::None => ByteStreamContent::None, core::Content::RawBody => ByteStreamContent::RawBody,
                core::Content::TextBody => ByteStreamContent::TextBody, core::Content::Base64Body => ByteStreamContent::Base64Body,
                core::Content::TextData => ByteStreamContent::TextData, core::Content::BinaryData => ByteStreamContent::BinaryData,
                core::Content::WebSocketBinary => ByteStreamContent::WebSocketBinary, core::Content::RawFile => ByteStreamContent::RawFile },
            offset: value.offset, total: value.total, digest: value.digest.to_vec(), metadata: value.metadata, data: value.data }
    }
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct BrowserMessage {
    pub lane: u8, pub content: ByteStreamContent, pub metadata: Vec<u8>, pub data: Vec<u8>,
}

#[derive(uniffi::Object)]
pub struct HttpBodyDecoder { decoder: Mutex<autoyou_protocol::http_body::Decoder> }
#[uniffi::export]
impl HttpBodyDecoder {
    #[uniffi::constructor]
    pub fn new(raw_headers_json: String) -> Result<Arc<Self>, BindingError> {
        if raw_headers_json.len() > core::MAX_METADATA_BYTES { return Err(BindingError::InvalidInput); }
        let headers: Vec<(String, String)> = serde_json::from_str(&raw_headers_json).map_err(|_| BindingError::InvalidInput)?;
        let decoder = autoyou_protocol::http_body::Decoder::new(headers).map_err(|_| BindingError::InvalidInput)?;
        Ok(Arc::new(Self { decoder: Mutex::new(decoder) }))
    }
    pub fn total(&self) -> Result<Option<u64>, BindingError> { Ok(self.decoder.lock().map_err(|_| BindingError::Worker)?.total()) }
    pub fn finished(&self) -> Result<bool, BindingError> { Ok(self.decoder.lock().map_err(|_| BindingError::Worker)?.finished()) }
    pub fn maximum_read(&self) -> Result<u32, BindingError> { Ok(self.decoder.lock().map_err(|_| BindingError::Worker)?.maximum_read() as u32) }
    pub fn accept(&self, data: Vec<u8>) -> Result<Vec<u8>, BindingError> {
        self.decoder.lock().map_err(|_| BindingError::Worker)?.accept(data).map_err(|_| BindingError::InvalidInput)
    }
    pub fn eof(&self) -> Result<(), BindingError> { self.decoder.lock().map_err(|_| BindingError::Worker)?.eof().map_err(|_| BindingError::InvalidInput) }
}

/// Compatibility conversion at the business boundary, not a second wire codec.
#[uniffi::export]
pub fn prepare_browser_message(payload: Vec<u8>) -> Result<BrowserMessage, BindingError> {
    if payload.len() > core::MAX_BROWSER_ENVELOPE_BYTES { return Err(BindingError::InvalidInput); }
    let mut envelope: Envelope = serde_json::from_slice(&payload).map_err(|_| BindingError::InvalidInput)?;
    envelope.validate().map_err(|_| BindingError::InvalidInput)?;
    if payload.len() > autoyou_protocol::MAX_CONTROL_BYTES && envelope.header.message_type != MessageType::HttpWsData {
        return Err(BindingError::InvalidInput);
    }
    let lane = envelope.lane();
    if !matches!(lane, Lane::Http | Lane::ServerEvents | Lane::WebSocket) { return Err(BindingError::InvalidInput); }
    let mut content = ByteStreamContent::None; let mut data = Vec::new();
    let field = match envelope.header.message_type {
        MessageType::HttpRequest | MessageType::HttpResponse => Some("body"),
        MessageType::HttpStreamData | MessageType::HttpSseEvent | MessageType::HttpWsData => Some("data"),
        _ => None,
    };
    if envelope.header.message_type == MessageType::HttpWsData &&
        envelope.payload.get("data_b64").is_some_and(|value| !value.is_null()) {
        let encoded = envelope.payload.remove("data_b64").and_then(|value| value.as_str().map(str::to_owned)).ok_or(BindingError::InvalidInput)?;
        data = STANDARD.decode(&encoded).map_err(|_| BindingError::InvalidInput)?;
        if let Some(previous) = envelope.payload.remove("data").filter(|value| !value.is_null()) {
            let expected = format!("base64:{encoded}");
            if previous.as_str().is_none_or(|text| !text.is_empty() && text != expected) { return Err(BindingError::InvalidInput); }
        }
        content = ByteStreamContent::WebSocketBinary;
    } else if let Some(field) = field {
        if let Some(value) = envelope.payload.remove(field).filter(|value| !value.is_null()) {
            let text = value.as_str().ok_or(BindingError::InvalidInput)?;
            if field == "body" {
                let base64 = envelope.payload.get("body_base64").and_then(|value| value.as_bool()).unwrap_or(false) ||
                    envelope.payload.get("compressed").and_then(|value| value.as_bool()).unwrap_or(false) ||
                    envelope.payload.get("headers").and_then(|value| value.as_object()).is_some_and(|headers|
                        headers.iter().any(|(name, value)| name.eq_ignore_ascii_case("content-transfer-encoding") && value.as_str() == Some("base64")));
                if base64 { data = STANDARD.decode(text).map_err(|_| BindingError::InvalidInput)?; content = ByteStreamContent::Base64Body; }
                else { data = text.as_bytes().to_vec(); content = ByteStreamContent::TextBody; }
            } else if let Some(encoded) = text.strip_prefix("base64:").filter(|_| envelope.header.message_type != MessageType::HttpSseEvent) {
                data = STANDARD.decode(encoded).map_err(|_| BindingError::InvalidInput)?;
                content = if envelope.header.message_type == MessageType::HttpWsData { ByteStreamContent::WebSocketBinary }
                    else if envelope.header.message_type == MessageType::HttpStreamData { ByteStreamContent::BinaryData }
                    else { return Err(BindingError::InvalidInput); };
            } else { data = text.as_bytes().to_vec(); content = ByteStreamContent::TextData; }
        }
    }
    if data.len() > if lane == Lane::WebSocket { core::MAX_WEBSOCKET_BYTES } else { autoyou_protocol::MAX_CONTROL_BYTES } {
        return Err(BindingError::InvalidInput);
    }
    let metadata = core::normalize_metadata(&envelope.to_vec().map_err(|_| BindingError::InvalidInput)?)
        .map_err(|_| BindingError::InvalidInput)?;
    core::validate_metadata(lane, content.core(), &metadata).map_err(|_| BindingError::InvalidInput)?;
    Ok(BrowserMessage { lane: lane as u8, content, metadata, data })
}

/// Canonicalization for the bounded browser business adapter only. Large bodies
/// still become 48 KiB byte records; ordinary/control validation stays at 1 MiB.
#[uniffi::export]
pub fn validate_browser_envelope(payload: Vec<u8>) -> Result<Vec<u8>, BindingError> {
    let parts = prepare_browser_message(payload)?;
    restore_browser_message(parts.metadata, parts.content, parts.data)
}

#[uniffi::export]
pub fn browser_message_buffer_limit(metadata: Vec<u8>) -> Result<u32, BindingError> {
    if metadata.len() > core::MAX_METADATA_BYTES { return Err(BindingError::InvalidInput); }
    let envelope = Envelope::from_slice(&metadata).map_err(|_| BindingError::InvalidInput)?;
    if !matches!(envelope.lane(), Lane::Http | Lane::ServerEvents | Lane::WebSocket) { return Err(BindingError::InvalidInput); }
    Ok(if envelope.header.message_type == MessageType::HttpWsData { core::MAX_WEBSOCKET_BYTES }
        else { autoyou_protocol::MAX_CONTROL_BYTES } as u32)
}

#[uniffi::export]
pub fn restore_browser_message(metadata: Vec<u8>, content: ByteStreamContent, data: Vec<u8>) -> Result<Vec<u8>, BindingError> {
    // Buffered legacy consumers use this only for bounded message bodies. Large
    // HTTP request/response sinks consume stream parts directly in their host.
    if data.len() > browser_message_buffer_limit(metadata.clone())? as usize { return Err(BindingError::InvalidInput); }
    let mut envelope = Envelope::from_slice(&metadata).map_err(|_| BindingError::InvalidInput)?;
    core::validate_metadata(envelope.lane(), content.core(), &metadata).map_err(|_| BindingError::InvalidInput)?;
    let (field, value) = match content {
        ByteStreamContent::None => { if !data.is_empty() { return Err(BindingError::InvalidInput); } ("", None) }
        ByteStreamContent::RawBody => {
            envelope.payload.insert("body_base64".into(), true.into()); envelope.payload.insert("compressed".into(), false.into());
            ("body", Some(STANDARD.encode(data)))
        }
        ByteStreamContent::TextBody => ("body", Some(String::from_utf8(data).map_err(|_| BindingError::InvalidInput)?)),
        ByteStreamContent::Base64Body => ("body", Some(STANDARD.encode(data))),
        ByteStreamContent::TextData => ("data", Some(String::from_utf8(data).map_err(|_| BindingError::InvalidInput)?)),
        ByteStreamContent::BinaryData => ("data", Some(format!("base64:{}", STANDARD.encode(data)))),
        ByteStreamContent::WebSocketBinary => {
            envelope.payload.insert("opcode".into(), "binary".into()); envelope.payload.insert("binary".into(), true.into());
            ("data_b64", Some(STANDARD.encode(data)))
        }
        ByteStreamContent::RawFile => return Err(BindingError::InvalidInput),
    };
    if let Some(value) = value { envelope.payload.insert(field.into(), value.into()); }
    core::restore_header_map(&mut envelope);
    if envelope.header.message_type != MessageType::HttpWsData {
        return envelope.to_vec().map_err(|_| BindingError::InvalidInput);
    }
    envelope.validate().map_err(|_| BindingError::InvalidInput)?;
    let result = serde_json::to_vec(&envelope).map_err(|_| BindingError::InvalidInput)?;
    if result.len() > core::MAX_BROWSER_ENVELOPE_BYTES { return Err(BindingError::InvalidInput); }
    Ok(result)
}

pub(crate) fn message_records(message: BrowserMessage) -> Result<(Lane, Vec<Vec<u8>>), BindingError> {
    let lane = Lane::try_from(message.lane).map_err(|_| BindingError::InvalidInput)?;
    if message.data.len() > browser_message_buffer_limit(message.metadata.clone())? as usize { return Err(BindingError::InvalidInput); }
    let (mut writer, open) = core::Writer::open(lane, message.content.core(), message.data.len() as u64, message.metadata)
        .map_err(|_| BindingError::InvalidInput)?;
    let mut records = vec![open.encode(lane).map_err(|_| BindingError::InvalidInput)?];
    for chunk in message.data.chunks(core::MAX_DATA_BYTES) {
        records.push(writer.data(chunk.to_vec()).and_then(|record| record.encode(lane)).map_err(|_| BindingError::InvalidInput)?);
    }
    records.push(writer.finish().and_then(|record| record.encode(lane)).map_err(|_| BindingError::InvalidInput)?);
    Ok((lane, records))
}

#[derive(uniffi::Object)]
pub struct ByteStreamWriter { lane: Lane, open: Vec<u8>, writer: Mutex<core::Writer> }
#[uniffi::export]
impl ByteStreamWriter {
    #[uniffi::constructor]
    pub fn new(lane: u8, content: ByteStreamContent, total: u64, metadata: Vec<u8>) -> Result<Arc<Self>, BindingError> {
        let lane = Lane::try_from(lane).map_err(|_| BindingError::InvalidInput)?;
        let (writer, open) = core::Writer::open(lane, content.core(), total, metadata).map_err(|_| BindingError::InvalidInput)?;
        Ok(Arc::new(Self { lane, open: open.encode(lane).map_err(|_| BindingError::InvalidInput)?, writer: Mutex::new(writer) }))
    }
    pub fn open_record(&self) -> Vec<u8> { self.open.clone() }
    pub fn data_record(&self, data: Vec<u8>) -> Result<Vec<u8>, BindingError> {
        self.writer.lock().map_err(|_| BindingError::Worker)?.data(data)
            .and_then(|record| record.encode(self.lane)).map_err(|_| BindingError::InvalidInput)
    }
    pub fn finish_record(&self) -> Result<Vec<u8>, BindingError> {
        self.writer.lock().map_err(|_| BindingError::Worker)?.finish()
            .and_then(|record| record.encode(self.lane)).map_err(|_| BindingError::InvalidInput)
    }
    pub fn abort_record(&self) -> Result<Vec<u8>, BindingError> {
        self.writer.lock().map_err(|_| BindingError::Worker)?.abort()
            .and_then(|record| record.encode(self.lane)).map_err(|_| BindingError::InvalidInput)
    }
    pub fn cancel_record(&self, queued_bytes: u64) -> Result<Vec<u8>, BindingError> {
        self.writer.lock().map_err(|_| BindingError::Worker)?.cancel_at(queued_bytes)
            .and_then(|record| record.encode(self.lane)).map_err(|_| BindingError::InvalidInput)
    }
}

#[derive(uniffi::Object)]
pub struct ByteStreamReceiver { receiver: Mutex<core::Receiver> }
#[uniffi::export]
impl ByteStreamReceiver {
    #[uniffi::constructor]
    pub fn new() -> Arc<Self> { Arc::new(Self { receiver: Mutex::new(core::Receiver::default()) }) }
    pub fn accept(&self, lane: u8, stream_id: u64, payload: Vec<u8>) -> Result<ByteStreamRecord, BindingError> {
        let lane = Lane::try_from(lane).map_err(|_| BindingError::InvalidInput)?;
        let record = core::Record::decode(lane, &payload).map_err(|_| BindingError::InvalidInput)?;
        self.receiver.lock().map_err(|_| BindingError::Worker)?.accept(lane, stream_id, &record)
            .map_err(|_| BindingError::InvalidInput)?;
        Ok(record.into())
    }
    pub fn active_count(&self) -> Result<u32, BindingError> {
        Ok(self.receiver.lock().map_err(|_| BindingError::Worker)?.active_count() as u32)
    }
    pub fn shutdown(&self) -> Result<(), BindingError> {
        self.receiver.lock().map_err(|_| BindingError::Worker)?.clear(); Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn envelope(kind: &str, payload: serde_json::Value) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({"header": {"message_id":"synthetic-browser", "timestamp":1,
            "message_type":kind}, "payload":payload})).unwrap()
    }
    #[test]
    fn browser_message_binary_and_unicode_conversion_has_no_wire_base64_body() {
        for (kind, payload) in [
            ("http_request", serde_json::json!({"body":"AP8NCg==", "body_base64":true, "method":"PUT", "url":"/synthetic"})),
            ("http_stream_data", serde_json::json!({"data":"base64:AP8NCg==", "seq":1})),
            ("http_ws_data", serde_json::json!({"data_b64":"AP8NCg==", "binary":true, "opcode":"binary"})),
            ("http_sse_event", serde_json::json!({"data":"id: 1\ndata: £🙂\n\n", "event":"synthetic"})),
            ("http_sse_event", serde_json::json!({"data":"base64:literal"})),
        ] {
            let input = envelope(kind, payload);
            let prepared = prepare_browser_message(input.clone()).unwrap();
            let (lane, records) = message_records(prepared.clone()).unwrap();
            let mut receiver = core::Receiver::default(); let mut bytes = Vec::new(); let mut metadata = Vec::new();
            for payload in records {
                let record = core::Record::decode(lane, &payload).unwrap(); receiver.accept(lane, 3, &record).unwrap();
                if record.kind == core::Kind::Open { metadata = record.metadata; }
                bytes.extend(record.data);
            }
            assert_eq!(receiver.active_count(), 0);
            let restored = restore_browser_message(metadata, prepared.content, bytes).unwrap();
            let mut expected = Envelope::from_slice(&input).unwrap(); core::restore_header_map(&mut expected);
            assert_eq!(serde_json::from_slice::<serde_json::Value>(&restored).unwrap(),
                serde_json::from_slice::<serde_json::Value>(&expected.to_vec().unwrap()).unwrap());
        }
    }
    #[test]
    fn browser_message_large_bytes_are_bounded_records_and_duplicate_copies_fail() {
        let input = envelope("http_response", serde_json::json!({"body":"x".repeat(700_000), "status_code":206,
            "raw_headers":[["set-cookie","a=1"],["set-cookie","b=2"]]}));
        let prepared = prepare_browser_message(input).unwrap();
        let (lane, records) = message_records(prepared).unwrap();
        assert!(records.len() > 10);
        assert!(records.iter().all(|record| record.len() <= lane.max_payload()));
        let copied = envelope("http_ws_data", serde_json::json!({"data":"conflicting", "data_b64":"AP8="}));
        assert!(prepare_browser_message(copied).is_err());
    }
    #[test]
    fn browser_websocket_limit_preserves_raw_binary_and_escaped_text_without_widening_control_frames() {
        for (content, data) in [(ByteStreamContent::WebSocketBinary, vec![0xff; core::MAX_WEBSOCKET_BYTES]),
            (ByteStreamContent::TextData, vec![0; core::MAX_WEBSOCKET_BYTES])] {
            let metadata = envelope("http_ws_data", serde_json::json!({"request_id":"synthetic-large-ws"}));
            let canonical = restore_browser_message(metadata.clone(), content.clone(), data.clone()).unwrap();
            assert!(Envelope::from_slice(&canonical).is_err());
            let prepared = prepare_browser_message(canonical).unwrap();
            assert_eq!(prepared.data, data);
            let normalized = Envelope::from_slice(&prepared.metadata).unwrap();
            assert_eq!(normalized.payload.get("request_id").unwrap(), "synthetic-large-ws");
            assert!(!normalized.payload.contains_key("data") && !normalized.payload.contains_key("data_b64"));
            let (lane, records) = message_records(prepared).unwrap();
            assert!(records.len() <= 128 && records.len() > 64);
            let mut receiver = core::Receiver::default();
            for payload in records {
                let record = core::Record::decode(lane, &payload).unwrap();
                receiver.accept(lane, 1, &record).unwrap();
            }
            assert_eq!(receiver.active_count(), 0);
            assert!(restore_browser_message(metadata, content, vec![0; core::MAX_WEBSOCKET_BYTES + 1]).is_err());
        }
        assert!(validate_browser_envelope(envelope("chat", serde_json::json!({"content":"x".repeat(2*1024*1024)}))).is_err());
    }
}
