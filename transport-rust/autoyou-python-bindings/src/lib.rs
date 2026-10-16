// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Direct CPython adapter: JSON becomes its final host values in one native pass.

use autoyou_protocol::{MAX_CONTROL_BYTES, MessageHeader, MessageType};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyModule, PyString, PyTuple};
use serde::ser::{Error, Serialize, SerializeMap, SerializeSeq, Serializer};
use sha2::{Digest, Sha256};
use std::borrow::Cow;

mod runtime_limits;
mod secure_stream;
mod protected_file;

const MAX_JSON_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_DEPTH: usize = 128;
pub const APPLICATION_API_VERSION: u32 = 1;

fn invalid() -> PyErr { PyValueError::new_err("Invalid application JSON") }
fn limit(max_bytes: u64) -> PyResult<usize> {
    if max_bytes == 0 || max_bytes > MAX_JSON_BYTES { return Err(invalid()); }
    usize::try_from(max_bytes).map_err(|_| invalid())
}

fn input_bytes<'a>(document: &'a Bound<'_, PyAny>) -> PyResult<Cow<'a, [u8]>> {
    if let Ok(value) = document.cast::<PyBytes>() { return Ok(Cow::Borrowed(value.as_bytes())); }
    if let Ok(value) = document.cast::<PyString>() { return Ok(Cow::Borrowed(value.to_str()?.as_bytes())); }
    Err(PyTypeError::new_err("Application JSON must be str or bytes"))
}

fn parse<'py>(py: Python<'py>, document: &Bound<'py, PyAny>, max_bytes: u64, strict_keys: bool) -> PyResult<Bound<'py, PyAny>> {
    let wire = input_bytes(document)?;
    if wire.len() > limit(max_bytes)? { return Err(invalid()); }
    jiter::PythonParse { allow_inf_nan: false, catch_duplicate_keys: strict_keys, cache_mode: jiter::StringCacheMode::Keys,
        ..Default::default() }.python_parse(py, &wire).map_err(|_| invalid())
}

#[pyfunction]
fn loads<'py>(py: Python<'py>, document: &Bound<'py, PyAny>, max_bytes: u64) -> PyResult<Bound<'py, PyAny>> {
    parse(py, document, max_bytes, false)
}

fn required<'py>(value: &Bound<'py, PyDict>, key: &str) -> PyResult<Bound<'py, PyAny>> {
    value.get_item(key)?.ok_or_else(invalid)
}
fn optional_string(value: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<String>> {
    value.get_item(key)?.filter(|value| !value.is_none()).map(|value| value.extract::<String>()).transpose()
}

fn envelope_header(value: &Bound<'_, PyAny>) -> PyResult<MessageHeader> {
    let value = value.cast::<PyDict>().map_err(|_| invalid())?;
    required(value, "payload")?.cast::<PyDict>().map_err(|_| invalid())?;
    if value.get_item("chunk_info")?.is_some_and(|value| !value.is_none()) { return Err(invalid()); }
    let header = required(value, "header")?;
    let header = header.cast::<PyDict>().map_err(|_| invalid())?;
    let kind: String = required(header, "message_type")?.extract()?;
    let message_type: MessageType = serde_json::from_value(serde_json::Value::String(kind)).map_err(|_| invalid())?;
    let timestamp = required(header, "timestamp")?;
    if timestamp.is_instance_of::<PyBool>() || !(timestamp.is_instance_of::<PyInt>() || timestamp.is_instance_of::<PyFloat>()) {
        return Err(invalid());
    }
    let header = MessageHeader { message_id: required(header, "message_id")?.extract()?, message_type,
        timestamp: timestamp.extract()?, session_id: optional_string(header, "session_id")?,
        user_id: optional_string(header, "user_id")?, extensions: Default::default() };
    header.validate_native().map_err(|_| invalid())?;
    Ok(header)
}

fn envelope_lane(value: &Bound<'_, PyAny>) -> PyResult<u8> {
    Ok(envelope_header(value)?.message_type.lane() as u8)
}

#[pyfunction]
fn decode_envelope<'py>(py: Python<'py>, document: &Bound<'py, PyAny>) -> PyResult<(Bound<'py, PyAny>, u8)> {
    let value = parse(py, document, MAX_CONTROL_BYTES as u64, true)?;
    let lane = envelope_lane(&value)?;
    Ok((value, lane))
}

struct PyJson<'a, 'py> { value: &'a Bound<'py, PyAny>, depth: usize }
impl Serialize for PyJson<'_, '_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        if self.depth >= MAX_DEPTH { return Err(S::Error::custom("application JSON nesting limit")); }
        let value = self.value;
        if value.is_none() { return serializer.serialize_none(); }
        if let Ok(value) = value.cast::<PyBool>() { return serializer.serialize_bool(value.is_true()); }
        if let Ok(value) = value.cast::<PyString>() {
            return serializer.serialize_str(value.to_str().map_err(|_| S::Error::custom("invalid JSON string"))?);
        }
        if value.is_instance_of::<PyInt>() {
            if let Ok(number) = value.extract::<i64>() { return serializer.serialize_i64(number); }
            if let Ok(number) = value.extract::<u64>() { return serializer.serialize_u64(number); }
            let decimal = value.str().map_err(|_| S::Error::custom("invalid JSON integer"))?;
            let raw = serde_json::value::RawValue::from_string(decimal.to_str().map_err(|_| S::Error::custom("invalid JSON integer"))?.into())
                .map_err(|_| S::Error::custom("invalid JSON integer"))?;
            return raw.serialize(serializer);
        }
        if let Ok(value) = value.cast::<PyFloat>() {
            let number = value.value();
            if !number.is_finite() { return Err(S::Error::custom("non-finite JSON number")); }
            return serializer.serialize_f64(number);
        }
        if let Ok(value) = value.cast::<PyDict>() {
            let mut output = serializer.serialize_map(Some(value.len()))?;
            for (key, item) in value.iter() {
                let key = object_key(&key).map_err(|_| S::Error::custom("invalid JSON object key"))?;
                output.serialize_entry(&key, &PyJson { value: &item, depth: self.depth + 1 })?;
            }
            return output.end();
        }
        if let Ok(value) = value.cast::<PyList>() {
            let mut output = serializer.serialize_seq(Some(value.len()))?;
            for item in value.iter() { output.serialize_element(&PyJson { value: &item, depth: self.depth + 1 })?; }
            return output.end();
        }
        if let Ok(value) = value.cast::<PyTuple>() {
            let mut output = serializer.serialize_seq(Some(value.len()))?;
            for item in value.iter() { output.serialize_element(&PyJson { value: &item, depth: self.depth + 1 })?; }
            return output.end();
        }
        Err(S::Error::custom("unsupported application JSON type"))
    }
}

fn object_key(value: &Bound<'_, PyAny>) -> PyResult<String> {
    if let Ok(value) = value.cast::<PyString>() { return Ok(value.to_str()?.into()); }
    if value.is_none() { return Ok("null".into()); }
    if let Ok(value) = value.cast::<PyBool>() { return Ok(if value.is_true() { "true" } else { "false" }.into()); }
    if value.is_instance_of::<PyInt>() { return value.str()?.extract(); }
    if let Ok(value) = value.cast::<PyFloat>() {
        if value.value().is_finite() { return value.repr()?.extract(); }
    }
    Err(invalid())
}

#[pyfunction]
fn dumps<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, max_bytes: u64) -> PyResult<Bound<'py, PyBytes>> {
    let output = autoyou_protocol::byte_stream::json_bytes(&PyJson { value, depth: 0 }, limit(max_bytes)?).map_err(|_| invalid())?;
    Ok(PyBytes::new(py, &output))
}

#[pyfunction]
fn encode_envelope<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, max_bytes: u64) -> PyResult<Bound<'py, PyBytes>> {
    envelope_lane(value)?;
    dumps(py, value, max_bytes)
}

#[pyfunction]
fn prepare_message<'py>(py: Python<'py>, value: &Bound<'py, PyAny>)
    -> PyResult<(u8, Option<u8>, Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
    use autoyou_protocol::{byte_stream as core, Lane};
    let header = envelope_header(value)?;
    let lane = header.message_type.lane();
    if !matches!(lane, Lane::Http | Lane::ServerEvents | Lane::WebSocket) {
        return Ok((lane as u8, None, dumps(py, value, MAX_CONTROL_BYTES as u64)?, PyBytes::new(py, b"")));
    }
    let maximum = if header.message_type == MessageType::HttpWsData { core::MAX_BROWSER_ENVELOPE_BYTES } else { MAX_CONTROL_BYTES };
    // Retain the compatibility byte ceiling without allocating or parsing a body-sized JSON document.
    let size = core::json_size(&PyJson { value, depth: 0 }, maximum).map_err(|_| invalid())?;
    let value = value.cast::<PyDict>().map_err(|_| invalid())?;
    let metadata = value.copy()?;
    let payload = required(value, "payload")?;
    let payload = payload.cast::<PyDict>().map_err(|_| invalid())?.copy()?;
    let mut fields = Vec::new();
    for name in ["body", "data", "data_b64"] {
        if let Some(value) = payload.get_item(name)? {
            let value = if value.is_none() { None } else { Some(value.extract::<String>().map_err(|_| invalid())?) };
            fields.push((name.into(), value)); payload.del_item(name)?;
        }
    }
    metadata.set_item("payload", payload)?;
    let wire = dumps(py, metadata.as_any(), maximum as u64)?.as_bytes().to_vec();
    let parts = py.detach(move || core::prepare_browser_parts(&wire, fields, size)).map_err(|_| invalid())?;
    Ok((parts.lane as u8, Some(parts.content as u8), PyBytes::new(py, &parts.metadata), PyBytes::new(py, &parts.data)))
}

#[pyfunction]
fn restore_browser_payload(payload: &Bound<'_, PyDict>, message_type: String, content: u8, data: &[u8]) -> PyResult<u64> {
    use autoyou_protocol::byte_stream::{self as core, Content};
    let message_type: MessageType = serde_json::from_value(serde_json::Value::String(message_type)).map_err(|_| invalid())?;
    let content = Content::try_from(content).map_err(|_| invalid())?;
    for key in ["body", "data", "data_b64"] {
        if payload.get_item(key)?.is_some_and(|value| !value.is_none()) { return Err(invalid()); }
    }
    if content == Content::RawBody {
        for key in ["compressed", "body_base64"] {
            if let Some(value) = payload.get_item(key)?.filter(|value| !value.is_none()) {
                if !value.cast::<PyBool>().is_ok_and(|value| !value.is_true()) { return Err(invalid()); }
            }
        }
    }
    let fields = core::restore_browser_payload(message_type, content, data.to_vec()).map_err(|_| invalid())?;
    let mut retained = 512u64;
    for (name, value) in fields {
        match value {
            // Python may retain both its Unicode storage and a cached UTF-8 view.
            serde_json::Value::String(value) => { retained += 2 * value.len() as u64; payload.set_item(name, value)?; },
            serde_json::Value::Bool(value) => payload.set_item(name, value)?,
            _ => return Err(invalid()),
        }
    }
    Ok(retained)
}

#[pymodule]
fn autoyou_application(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("api_version", APPLICATION_API_VERSION)?;
    module.add("lock_sha256", format!("{:x}", Sha256::digest(include_bytes!("../../Cargo.lock"))))?;
    module.add_function(wrap_pyfunction!(loads, module)?)?;
    module.add_function(wrap_pyfunction!(dumps, module)?)?;
    module.add_function(wrap_pyfunction!(decode_envelope, module)?)?;
    module.add_function(wrap_pyfunction!(encode_envelope, module)?)?;
    module.add_function(wrap_pyfunction!(prepare_message, module)?)?;
    module.add_function(wrap_pyfunction!(restore_browser_payload, module)?)?;
    runtime_limits::register(module)?;
    secure_stream::register(module)?;
    protected_file::register(module)?;
    Ok(())
}
