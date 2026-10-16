// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Bounded local HTTP upload framing. Hosts read at most `maximum_read` bytes
//! and pass decoded body chunks to the shared byte writer, without body copies
//! in application JSON or a plaintext temporary file.
use crate::{byte_stream::{MAX_DATA_BYTES, MAX_STREAM_BYTES}, ProtocolError};

enum State { Fixed(u64), Size, Chunk(u64), ChunkCr, ChunkLf, Trailer, Done }
pub struct Decoder { state: State, line: Vec<u8>, received: u64, trailers: usize,
    trailer_count: usize, failed: bool, total: Option<u64> }
fn token(name: &str) -> bool {
    !name.is_empty() && name.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&byte))
}
impl Decoder {
    pub fn new(headers: Vec<(String, String)>) -> Result<Self, ProtocolError> {
        if headers.len() > 256 { return Err(ProtocolError::FrameTooLarge); }
        let mut length = None; let mut transfer = None; let mut bytes = 0;
        for (name, value) in headers {
            bytes += name.len() + value.chars().count() + 4;
            if bytes > 16 * 1024 || !token(&name) || !value.chars().all(|ch| ch == '\t' || (32..=255).contains(&(ch as u32)) && ch != '\u{7f}') {
                return Err(ProtocolError::InvalidEnvelope);
            }
            if name.eq_ignore_ascii_case("content-length") {
                let text = value.trim();
                if text.is_empty() || !text.bytes().all(|byte| byte.is_ascii_digit()) { return Err(ProtocolError::InvalidEnvelope); }
                let parsed = text.parse::<u64>().map_err(|_| ProtocolError::InvalidEnvelope)?;
                if parsed > MAX_STREAM_BYTES || length.is_some_and(|previous| previous != parsed) { return Err(ProtocolError::InvalidEnvelope); }
                length = Some(parsed);
            }
            if name.eq_ignore_ascii_case("transfer-encoding") {
                if transfer.is_some() || !value.trim().eq_ignore_ascii_case("chunked") { return Err(ProtocolError::InvalidEnvelope); }
                transfer = Some(());
            }
        }
        if transfer.is_some() && length.is_some() { return Err(ProtocolError::InvalidEnvelope); }
        let total = if transfer.is_some() { None } else { Some(length.unwrap_or(0)) };
        let state = match total { None => State::Size, Some(0) => State::Done, Some(value) => State::Fixed(value) };
        Ok(Self { state, line: Vec::new(), received: 0, trailers: 0, trailer_count: 0, failed: false, total })
    }
    pub fn total(&self) -> Option<u64> { self.total }
    pub fn finished(&self) -> bool { !self.failed && matches!(self.state, State::Done) }
    pub fn maximum_read(&self) -> usize {
        if self.failed { return 0; }
        match self.state { State::Done => 0, State::Fixed(left) | State::Chunk(left) => left.min(MAX_DATA_BYTES as u64) as usize, _ => 1 }
    }
    pub fn accept(&mut self, data: Vec<u8>) -> Result<Vec<u8>, ProtocolError> {
        let result = self.accept_inner(data);
        if result.is_err() { self.failed = true; self.line.clear(); }
        result
    }
    fn accept_inner(&mut self, data: Vec<u8>) -> Result<Vec<u8>, ProtocolError> {
        if self.failed || data.is_empty() || data.len() > self.maximum_read() { return Err(ProtocolError::InvalidFrame); }
        match self.state {
            State::Fixed(left) | State::Chunk(left) => {
                let next = self.received.checked_add(data.len() as u64).ok_or(ProtocolError::FrameTooLarge)?;
                if next > MAX_STREAM_BYTES { return Err(ProtocolError::FrameTooLarge); }
                self.received = next; let remaining = left - data.len() as u64;
                self.state = match self.state { State::Fixed(_) => if remaining == 0 { State::Done } else { State::Fixed(remaining) },
                    _ => if remaining == 0 { State::ChunkCr } else { State::Chunk(remaining) } };
                return Ok(data);
            }
            State::ChunkCr => { if data != b"\r" { return Err(ProtocolError::InvalidFrame); } self.state = State::ChunkLf; }
            State::ChunkLf => { if data != b"\n" { return Err(ProtocolError::InvalidFrame); } self.state = State::Size; }
            State::Size | State::Trailer => {
                let trailer = matches!(self.state, State::Trailer);
                self.line.extend_from_slice(&data);
                if self.line.len() > if trailer { 16 * 1024 } else { 1024 } { return Err(ProtocolError::FrameTooLarge); }
                if self.line.last() != Some(&b'\n') { return Ok(vec![]); }
                if !self.line.ends_with(b"\r\n") { return Err(ProtocolError::InvalidFrame); }
                let line = &self.line[..self.line.len() - 2];
                if trailer {
                    self.trailers += self.line.len(); self.trailer_count += 1;
                    if self.trailers > 16 * 1024 || self.trailer_count > 256 { return Err(ProtocolError::FrameTooLarge); }
                    if line.is_empty() { self.state = State::Done; }
                    else {
                        let colon = line.iter().position(|byte| *byte == b':').ok_or(ProtocolError::InvalidFrame)?;
                        let name = std::str::from_utf8(&line[..colon]).map_err(|_| ProtocolError::InvalidFrame)?;
                        if !token(name) || matches!(name.to_ascii_lowercase().as_str(), "content-length" | "transfer-encoding" | "host" |
                            "cookie" | "authorization" | "connection" | "upgrade") || name.to_ascii_lowercase().starts_with("x-autoyou-") ||
                            !line[colon+1..].iter().all(|byte| *byte == b'\t' || *byte >= 32 && *byte != 127) { return Err(ProtocolError::InvalidFrame); }
                    }
                } else {
                    let value = line.split(|byte| *byte == b';').next().unwrap_or_default();
                    if value.is_empty() || value.len() > 16 || !value.iter().all(|byte| byte.is_ascii_hexdigit()) ||
                        !line.iter().all(|byte| *byte >= 32 && *byte <= 126) { return Err(ProtocolError::InvalidFrame); }
                    let length = u64::from_str_radix(std::str::from_utf8(value).map_err(|_| ProtocolError::InvalidFrame)?, 16)
                        .map_err(|_| ProtocolError::InvalidFrame)?;
                    if length > MAX_STREAM_BYTES - self.received { return Err(ProtocolError::FrameTooLarge); }
                    self.state = if length == 0 { State::Trailer } else { State::Chunk(length) };
                }
                self.line.clear();
            }
            State::Done => return Err(ProtocolError::InvalidFrame),
        }
        Ok(vec![])
    }
    pub fn eof(&mut self) -> Result<(), ProtocolError> {
        if self.finished() { Ok(()) } else { self.failed = true; Err(ProtocolError::IncompleteFrame) }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn decode(decoder: &mut Decoder, input: &[u8], fragmentation: usize) -> Result<Vec<u8>, ProtocolError> {
        let mut at = 0; let mut output = Vec::new();
        while at < input.len() {
            let count = decoder.maximum_read().min(fragmentation).min(input.len()-at);
            if count == 0 { return Err(ProtocolError::InvalidFrame); }
            output.extend(decoder.accept(input[at..at+count].to_vec())?); at += count;
        }
        decoder.eof()?; Ok(output)
    }
    #[test]
    fn upload_framing_preserves_binary_and_fragmented_chunk_extensions() {
        for fragmentation in [1, 7, MAX_DATA_BYTES] {
            let mut fixed = Decoder::new(vec![("Content-Length".into(), "6".into())]).unwrap();
            let bytes = b"\x00\xff\r\n\xc2\xa3";
            assert_eq!(decode(&mut fixed, bytes, fragmentation).unwrap(), bytes);
            let mut chunked = Decoder::new(vec![("Transfer-Encoding".into(), "chunked".into())]).unwrap();
            assert_eq!(chunked.total(), None);
            assert_eq!(decode(&mut chunked, b"2;name=synthetic\r\n\x00\xff\r\n4\r\n\r\n\xc2\xa3\r\n0\r\nX-Checksum: synthetic\r\n\r\n", fragmentation).unwrap(), bytes);
        }
    }
    #[test]
    fn upload_framing_denies_smuggling_truncation_trailer_identity_and_reuse() {
        for headers in [vec![("content-length", "2"), ("Content-Length", "3")], vec![("content-length", "2"),("transfer-encoding", "chunked")],
            vec![("transfer-encoding", "gzip, chunked")], vec![("content-length", "-1")], vec![("bad name", "x")]] {
            assert!(Decoder::new(headers.into_iter().map(|(a,b)| (a.into(),b.into())).collect()).is_err());
        }
        for body in [b"0\r\nAuthorization: synthetic\r\n\r\n".as_slice(), b"1\r\na\rX0\r\n\r\n", b"1\r\n", b"0\r\n\r\nextra"] {
            let mut decoder = Decoder::new(vec![("transfer-encoding".into(), "chunked".into())]).unwrap();
            assert!(decode(&mut decoder, body, MAX_DATA_BYTES).is_err());
        }
        let mut decoder = Decoder::new(vec![("content-length".into(), "2".into())]).unwrap();
        assert!(decoder.accept(vec![1,2,3]).is_err()); assert_eq!(decoder.maximum_read(), 0);
        assert!(decoder.accept(vec![1,2]).is_err());
    }
}
