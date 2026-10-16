// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Browser metadata retains opaque JSON values without coercing integers through f64.

use std::{borrow::Cow, collections::BTreeMap, sync::Arc};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use jiter::{JsonValue, PartialMode};
use serde::ser::{Error as _, Serialize, SerializeMap, SerializeSeq, Serializer};
use crate::{byte_stream as stream, Lane, MessageHeader, MessageType, ProtocolError, MAX_CONTROL_BYTES};

type Object = BTreeMap<Cow<'static, str>, JsonValue<'static>>;
const HEADER_FIELDS: [&str; 5] = ["message_id", "message_type", "timestamp", "session_id", "user_id"];

struct Json<'a>(&'a JsonValue<'static>, usize);
impl Serialize for Json<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        if self.1 >= 128 { return Err(S::Error::custom("JSON nesting limit")); }
        match self.0 {
            JsonValue::Null => serializer.serialize_none(),
            JsonValue::Bool(value) => serializer.serialize_bool(*value),
            JsonValue::Int(value) => serializer.serialize_i64(*value),
            JsonValue::BigInt(value) => serde_json::value::RawValue::from_string(value.to_string())
                .map_err(S::Error::custom)?.serialize(serializer),
            JsonValue::Float(value) if value.is_finite() => serializer.serialize_f64(*value),
            JsonValue::Str(value) => serializer.serialize_str(value),
            JsonValue::Array(values) => {
                let mut output = serializer.serialize_seq(Some(values.len()))?;
                for value in values.iter() { output.serialize_element(&Json(value, self.1 + 1))?; }
                output.end()
            },
            JsonValue::Object(values) => {
                let unique: BTreeMap<_, _> = values.iter().map(|(key, value)| (key.as_ref(), value)).collect();
                let mut output = serializer.serialize_map(Some(unique.len()))?;
                for (key, value) in unique { output.serialize_entry(key, &Json(value, self.1 + 1))?; }
                output.end()
            },
            _ => Err(S::Error::custom("non-finite JSON number")),
        }
    }
}

fn parse(document: &[u8]) -> Result<JsonValue<'static>, ProtocolError> {
    JsonValue::parse_owned(document, false, PartialMode::Off).map_err(|_| ProtocolError::InvalidEnvelope)
}
fn object(value: JsonValue<'static>, unique: &[&str]) -> Result<Object, ProtocolError> {
    let JsonValue::Object(values) = value else { return Err(ProtocolError::InvalidEnvelope); };
    let mut output = BTreeMap::new();
    for (key, value) in Arc::try_unwrap(values).unwrap_or_else(|values| (*values).clone()) {
        if unique.contains(&key.as_ref()) && output.contains_key(&key) { return Err(ProtocolError::InvalidEnvelope); }
        output.insert(key, value);
    }
    Ok(output)
}
fn node(value: Object) -> JsonValue<'static> { JsonValue::Object(Arc::new(value.into_iter().collect())) }
fn text(value: JsonValue<'static>) -> Result<Cow<'static, str>, ProtocolError> {
    match value { JsonValue::Str(value) => Ok(value), _ => Err(ProtocolError::InvalidEnvelope) }
}
fn null(value: &JsonValue<'static>) -> bool { matches!(value, JsonValue::Null) }

struct Metadata { root: Object, payload: Object, header: MessageHeader }
struct ObjectJson<'a>(&'a Object, usize);
impl Serialize for ObjectJson<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut output = serializer.serialize_map(Some(self.0.len()))?;
        for (key, value) in self.0 { output.serialize_entry(key, &Json(value, self.1 + 1))?; }
        output.end()
    }
}
impl Serialize for Metadata {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut output = serializer.serialize_map(Some(self.root.len() + 1))?;
        for (key, value) in &self.root { output.serialize_entry(key, &Json(value, 1))?; }
        output.serialize_entry("payload", &ObjectJson(&self.payload, 1))?;
        output.end()
    }
}
impl Metadata {
    fn parse(document: &[u8]) -> Result<Self, ProtocolError> {
        let mut root = object(parse(document)?, &["header", "payload"])?;
        let mut fields = object(root.remove("header").ok_or(ProtocolError::InvalidEnvelope)?, &HEADER_FIELDS)?;
        let known: BTreeMap<_, _> = fields.iter().filter(|(key, _)| HEADER_FIELDS.contains(&key.as_ref()))
            .map(|(key, value)| (key.as_ref(), Json(value, 0))).collect();
        let header: MessageHeader = serde_json::from_slice(&stream::json_bytes(&known, MAX_CONTROL_BYTES)?)
            .map_err(|_| ProtocolError::InvalidEnvelope)?;
        header.validate_native()?;
        // Reuse the header's canonical representation while opaque extensions retain their JSON types.
        for key in HEADER_FIELDS { fields.remove(key); }
        fields.extend(object(parse(&serde_json::to_vec(&header).map_err(|_| ProtocolError::InvalidEnvelope)?)?, &[])?.into_iter());
        root.insert("header".into(), node(fields));
        if root.get("chunk_info").is_some_and(|value| !null(value)) { return Err(ProtocolError::LegacyChunk); }
        let payload = object(root.remove("payload").ok_or(ProtocolError::InvalidEnvelope)?, &[])?;
        Ok(Self { root, payload, header })
    }
    fn normalize_headers(&mut self) {
        if matches!(self.payload.get("raw_headers"), Some(JsonValue::Array(_))) { self.payload.remove("headers"); }
    }
    fn restore_headers(&mut self) {
        if let Some(JsonValue::Array(rows)) = self.payload.get("raw_headers") {
            let headers = rows.iter().filter_map(|row| {
                let JsonValue::Array(pair) = row else { return None; };
                if pair.len() != 2 { return None; }
                match (&pair[0], &pair[1]) {
                    (JsonValue::Str(key), JsonValue::Str(value)) => Some((key.clone(), JsonValue::Str(value.clone()))),
                    _ => None,
                }
            }).collect();
            self.payload.insert("headers".into(), node(headers));
        }
    }
}

pub struct BrowserMessage {
    pub lane: Lane, pub content: stream::Content, pub metadata: Vec<u8>, pub data: Vec<u8>,
}

pub fn normalize_metadata(document: &[u8]) -> Result<Vec<u8>, ProtocolError> {
    if document.len() > MAX_CONTROL_BYTES { return Err(ProtocolError::FrameTooLarge); }
    let mut metadata = Metadata::parse(document)?; metadata.normalize_headers();
    stream::json_bytes(&metadata, MAX_CONTROL_BYTES)
}

pub fn prepare_browser_message(document: &[u8]) -> Result<BrowserMessage, ProtocolError> {
    if document.len() > stream::MAX_BROWSER_ENVELOPE_BYTES { return Err(ProtocolError::FrameTooLarge); }
    prepare(Metadata::parse(document)?, document.len())
}

pub fn prepare_browser_parts(document: &[u8], fields: Vec<(String, Option<String>)>, size: usize) -> Result<BrowserMessage, ProtocolError> {
    if document.len() > stream::MAX_BROWSER_ENVELOPE_BYTES || fields.len() > 3 { return Err(ProtocolError::FrameTooLarge); }
    let mut metadata = Metadata::parse(document)?;
    for (key, value) in fields {
        if !["body", "data", "data_b64"].contains(&key.as_str()) || metadata.payload.contains_key(key.as_str()) {
            return Err(ProtocolError::InvalidEnvelope);
        }
        metadata.payload.insert(key.into(), value.map(|value| JsonValue::Str(Cow::Owned(value))).unwrap_or(JsonValue::Null));
    }
    prepare(metadata, size)
}

fn prepare(mut metadata: Metadata, size: usize) -> Result<BrowserMessage, ProtocolError> {
    use stream::Content;
    let kind = metadata.header.message_type;
    if size > stream::MAX_BROWSER_ENVELOPE_BYTES || (size > MAX_CONTROL_BYTES && kind != MessageType::HttpWsData) {
        return Err(ProtocolError::FrameTooLarge);
    }
    let lane = kind.lane();
    if !matches!(lane, Lane::Http | Lane::ServerEvents | Lane::WebSocket) { return Err(ProtocolError::InvalidEnvelope); }
    let mut content = Content::None; let mut data = Vec::new();
    let field = match kind {
        MessageType::HttpRequest | MessageType::HttpResponse => Some("body"),
        MessageType::HttpStreamData | MessageType::HttpSseEvent | MessageType::HttpWsData => Some("data"), _ => None,
    };
    if kind == MessageType::HttpWsData && metadata.payload.get("data_b64").is_some_and(|value| !null(value)) {
        let encoded = text(metadata.payload.remove("data_b64").ok_or(ProtocolError::InvalidEnvelope)?)?;
        data = STANDARD.decode(encoded.as_ref()).map_err(|_| ProtocolError::InvalidEnvelope)?;
        if let Some(previous) = metadata.payload.remove("data").filter(|value| !null(value)) {
            let previous = text(previous)?;
            if !previous.is_empty() && previous.strip_prefix("base64:") != Some(encoded.as_ref()) { return Err(ProtocolError::InvalidEnvelope); }
        }
        content = Content::WebSocketBinary;
    } else if let Some(field) = field {
        if let Some(value) = metadata.payload.remove(field).filter(|value| !null(value)) {
            let value = text(value)?;
            if field == "body" {
                let encoded = ["body_base64", "compressed"].iter().any(|key| matches!(metadata.payload.get(*key), Some(JsonValue::Bool(true)))) ||
                    metadata.payload.get("headers").is_some_and(|value| {
                        if let JsonValue::Object(rows) = value {
                            let headers: BTreeMap<_, _> = rows.iter().map(|(key, value)| (key.as_ref(), value)).collect();
                            headers.iter().any(|(key, value)| key.eq_ignore_ascii_case("content-transfer-encoding") && matches!(value, JsonValue::Str(value) if value == "base64"))
                        } else { false }
                    });
                if encoded { data = STANDARD.decode(value.as_ref()).map_err(|_| ProtocolError::InvalidEnvelope)?; content = Content::Base64Body; }
                else { data = value.into_owned().into_bytes(); content = Content::TextBody; }
            } else if let Some(encoded) = value.strip_prefix("base64:").filter(|_| kind != MessageType::HttpSseEvent) {
                data = STANDARD.decode(encoded).map_err(|_| ProtocolError::InvalidEnvelope)?;
                content = if kind == MessageType::HttpWsData { Content::WebSocketBinary } else { Content::BinaryData };
            } else { data = value.into_owned().into_bytes(); content = Content::TextData; }
        }
    }
    if data.len() > if lane == Lane::WebSocket { stream::MAX_WEBSOCKET_BYTES } else { MAX_CONTROL_BYTES } { return Err(ProtocolError::FrameTooLarge); }
    stream::json_size(&metadata, MAX_CONTROL_BYTES)?;
    metadata.normalize_headers();
    let metadata = stream::json_bytes(&metadata, stream::MAX_METADATA_BYTES)?;
    stream::validate_metadata(lane, content, &metadata)?;
    Ok(BrowserMessage { lane, content, metadata, data })
}

pub fn restore_browser_message(document: &[u8], content: stream::Content, data: Vec<u8>) -> Result<Vec<u8>, ProtocolError> {
    if document.len() > stream::MAX_METADATA_BYTES { return Err(ProtocolError::FrameTooLarge); }
    let mut metadata = Metadata::parse(document)?;
    let kind = metadata.header.message_type;
    stream::validate_metadata(kind.lane(), content, document)?;
    for (key, value) in stream::restore_browser_payload(kind, content, data)? {
        let value = match value {
            serde_json::Value::String(value) => JsonValue::Str(Cow::Owned(value)),
            serde_json::Value::Bool(value) => JsonValue::Bool(value), _ => return Err(ProtocolError::InvalidEnvelope),
        };
        metadata.payload.insert(key.into(), value);
    }
    metadata.restore_headers();
    let maximum = if kind == MessageType::HttpWsData { stream::MAX_BROWSER_ENVELOPE_BYTES } else { MAX_CONTROL_BYTES };
    stream::json_bytes(&metadata, maximum)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opaque_numbers_and_reserved_object_keys_survive_every_browser_boundary() {
        let input = br#"{"header":{"message_id":"synthetic-precision","message_type":"http_ws_data","timestamp":1,"extra":1267650600228229401496703205376},"payload":{"data":"text","integer":1267650600228229401496703205376,"reserved":{"$serde_json::private::Number":"12345"},"raw_headers":[["x-synthetic","value"]]},"extra":1267650600228229401496703205376}"#;
        let parts = prepare_browser_message(input).unwrap();
        let normalized = normalize_metadata(&parts.metadata).unwrap();
        let restored = restore_browser_message(&normalized, parts.content, parts.data).unwrap();
        let JsonValue::Object(root) = parse(&restored).unwrap() else { panic!("expected envelope"); };
        let serialized = stream::json_bytes(&Json(&JsonValue::Object(root), 0), MAX_CONTROL_BYTES).unwrap();
        let text = std::str::from_utf8(&serialized).unwrap();
        assert_eq!(text.matches("1267650600228229401496703205376").count(), 3);
        assert!(text.contains(r#""reserved":{"$serde_json::private::Number":"12345"}"#));
        assert!(text.contains(r#""headers":{"x-synthetic":"value"}"#));
    }

    #[test]
    fn malformed_headers_duplicates_and_detached_field_overrides_are_rejected() {
        for input in [
            br#"{"header":{"message_id":"a","message_id":"b","message_type":"http_request","timestamp":1},"payload":{}}"#.as_slice(),
            br#"{"header":{"message_id":"a","message_type":"http_request","timestamp":true},"payload":{}}"#,
            br#"{"header":{"message_id":"a","message_type":"http_request","timestamp":1},"payload":{},"payload":{}}"#,
            br#"{"header":{"message_id":"a","message_type":"http_request","timestamp":1},"payload":{},"chunk_info":{}}"#,
        ] { assert!(prepare_browser_message(input).is_err()); }
        let metadata = br#"{"header":{"message_id":"synthetic-parts","message_type":"http_request","timestamp":1},"payload":{"body":null}}"#;
        assert!(prepare_browser_parts(metadata, vec![("body".into(), Some("override".into()))], metadata.len()).is_err());
        assert!(prepare_browser_parts(metadata, vec![("other".into(), None)], metadata.len()).is_err());
    }
}
