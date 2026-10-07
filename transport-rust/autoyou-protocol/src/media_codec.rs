// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Small bounded checks before native decoders allocate their working sets.
//! This is not a codec implementation. Opus framing follows RFC 6716; VP8's
//! uncompressed header follows RFC 6386. H.264 uses progressive 8-bit 4:2:0
//! Annex B, repeat parameter sets on IDR and no B slices in this media profile.

use crate::{ProtocolError, media::{Codec, MediaHeader}};
type Result<T> = std::result::Result<T, ProtocolError>;
fn invalid<T>() -> Result<T> { Err(ProtocolError::InvalidFrame) }

pub fn validate(header: &MediaHeader, data: &[u8]) -> Result<()> {
    header.validate()?;
    if data.len() != header.length { return invalid(); }
    match header.codec {
        Codec::Pcm16 => Ok(()),
        Codec::Opus => {
            if opus_duration(data)? != header.duration_us || (data[0] & 4 != 0 && header.channels == 1) { return invalid(); }
            Ok(())
        },
        Codec::Vp8 => vp8(header, data),
        Codec::H264 => h264(header, data),
    }
}

fn opus_length(data: &[u8], cursor: &mut usize) -> Result<usize> {
    let first = *data.get(*cursor).ok_or(ProtocolError::InvalidFrame)? as usize; *cursor += 1;
    if first < 252 { return Ok(first); }
    let second = *data.get(*cursor).ok_or(ProtocolError::InvalidFrame)? as usize; *cursor += 1;
    Ok(first + 4 * second)
}
fn opus_duration(data: &[u8]) -> Result<u32> {
    let toc = *data.first().ok_or(ProtocolError::InvalidFrame)?;
    let config = toc >> 3;
    let duration = if config < 12 { [10_000,20_000,40_000,60_000][(config % 4) as usize] }
        else if config < 16 { [10_000,20_000][(config % 2) as usize] }
        else { [2500,5000,10_000,20_000][(config % 4) as usize] };
    let mut cursor = 1; let mut end = data.len();
    let (count, vbr) = match toc & 3 {
        0 => (1usize, false), 1 => (2, false), 2 => (2, true),
        _ => {
            let flags = *data.get(cursor).ok_or(ProtocolError::InvalidFrame)?; cursor += 1;
            let count = (flags & 63) as usize;
            if count == 0 || count as u32 * duration > 120_000 { return invalid(); }
            if flags & 64 != 0 {
                let mut padding = 0usize;
                loop {
                    let size = *data.get(cursor).ok_or(ProtocolError::InvalidFrame)?; cursor += 1;
                    padding += if size == 255 { 254 } else { size as usize };
                    if padding > data.len() { return invalid(); }
                    if size != 255 { break; }
                }
                end = end.checked_sub(padding).ok_or(ProtocolError::InvalidFrame)?;
            }
            (count, flags & 128 != 0)
        },
    };
    if count as u32 * duration > 120_000 || cursor > end { return invalid(); }
    if vbr {
        let mut accounted = 0usize;
        for _ in 0..count-1 {
            let length = opus_length(&data[..end], &mut cursor)?;
            if length > 1275 { return invalid(); } accounted += length;
        }
        let remaining = end.checked_sub(cursor).ok_or(ProtocolError::InvalidFrame)?;
        if accounted > remaining || remaining - accounted > 1275 { return invalid(); }
    } else {
        let remaining = end - cursor;
        if remaining % count != 0 || remaining / count > 1275 { return invalid(); }
    }
    Ok(count as u32 * duration)
}

fn vp8(header: &MediaHeader, data: &[u8]) -> Result<()> {
    let bytes = data.get(..3).ok_or(ProtocolError::InvalidFrame)?;
    let tag = u32::from(bytes[0]) | u32::from(bytes[1]) << 8 | u32::from(bytes[2]) << 16;
    let keyframe = tag & 1 == 0;
    let prefix = if keyframe { 10 } else { 3 };
    // Hidden frames and display scaling aren't negotiated in this profile.
    if keyframe != header.keyframe || (tag >> 1) & 7 > 3 || tag & 16 == 0 ||
        data.len() < prefix || tag >> 5 == 0 || tag as usize >> 5 > data.len() - prefix { return invalid(); }
    if keyframe {
        if data[3..6] != [0x9d,1,0x2a] { return invalid(); }
        let width = u16::from_le_bytes([data[6],data[7]]);
        let height = u16::from_le_bytes([data[8],data[9]]);
        if width != header.width || height != header.height { return invalid(); }
    }
    Ok(())
}

struct Bits { data: Vec<u8>, bit: usize }
impl Bits {
    fn new(data: &[u8]) -> Result<Self> {
        if data.len() > 4096 { return invalid(); }
        let mut out = Vec::with_capacity(data.len()); let mut zeros = 0; let mut cursor = 0;
        while cursor < data.len() {
            let byte = data[cursor];
            if zeros >= 2 && byte == 3 {
                if data.get(cursor+1).is_none_or(|next| *next > 3) { return invalid(); }
                zeros = 0; cursor += 1; continue;
            }
            out.push(byte); zeros = if byte == 0 { zeros + 1 } else { 0 }; cursor += 1;
        }
        Ok(Self { data: out, bit: 0 })
    }
    fn slice_prefix(data: &[u8]) -> Result<Self> {
        let mut end = data.len().min(128);
        // Cutting a large slice at an emulation-prevention byte must not turn
        // a valid encoded picture into a malformed complete RBSP. The bounded
        // fields read below need far fewer bits than this retained prefix.
        if end < data.len() && data[..end].ends_with(&[0, 0, 3]) { end -= 1; }
        Self::new(&data[..end])
    }
    fn read(&mut self, width: usize) -> Result<u32> {
        if width > 32 || self.bit.saturating_add(width) > self.data.len()*8 { return invalid(); }
        let mut value = 0;
        for _ in 0..width {
            value = (value << 1) | u32::from((self.data[self.bit/8] >> (7-self.bit%8)) & 1); self.bit += 1;
        }
        Ok(value)
    }
    fn unsigned(&mut self, maximum: u32) -> Result<u32> {
        let mut zeros = 0;
        while self.read(1)? == 0 { zeros += 1; if zeros > 31 { return invalid(); } }
        let value = ((1u64 << zeros)-1) + u64::from(self.read(zeros)?);
        if value > u64::from(maximum) { return invalid(); } Ok(value as u32)
    }
    fn signed(&mut self) -> Result<i32> {
        let encoded = self.unsigned(65535)?;
        Ok(if encoded % 2 == 0 { -(encoded as i32 / 2) } else { (encoded as i32 + 1) / 2 })
    }
}

fn sps(data: &[u8], header: &MediaHeader) -> Result<()> {
    let mut bits = Bits::new(data)?;
    let profile = bits.read(8)?; let constraints = bits.read(8)?; let level = bits.read(8)?;
    if !matches!(profile, 66 | 77 | 88 | 100) || constraints & 3 != 0 || level > 62 { return invalid(); }
    bits.unsigned(31)?;
    if profile == 100 {
        if bits.unsigned(3)? != 1 || bits.unsigned(6)? != 0 || bits.unsigned(6)? != 0 || bits.read(1)? != 0 { return invalid(); }
        if bits.read(1)? != 0 {
            for index in 0..8 {
                if bits.read(1)? != 0 {
                    let mut previous = 8; let mut next = 8;
                    for _ in 0..if index < 6 { 16 } else { 64 } {
                        if next != 0 { next = (previous + bits.signed()? + 256).rem_euclid(256); }
                        if next != 0 { previous = next; }
                    }
                }
            }
        }
    }
    bits.unsigned(12)?;
    match bits.unsigned(2)? {
        0 => { bits.unsigned(12)?; },
        1 => {
            bits.read(1)?; bits.signed()?; bits.signed()?;
            let count = bits.unsigned(255)?; for _ in 0..count { bits.signed()?; }
        },
        _ => {},
    }
    bits.unsigned(16)?; bits.read(1)?;
    let width = (bits.unsigned(511)?+1)*16; let height = (bits.unsigned(511)?+1)*16;
    if bits.read(1)? != 1 { return invalid(); } // progressive capture only
    bits.read(1)?;
    let (crop_x, crop_y) = if bits.read(1)? != 0 {
        let left=bits.unsigned(4096)?; let right=bits.unsigned(4096)?;
        let top=bits.unsigned(4096)?; let bottom=bits.unsigned(4096)?;
        (2*(left+right), 2*(top+bottom))
    } else { (0,0) };
    if width.checked_sub(crop_x) != Some(u32::from(header.width)) ||
        height.checked_sub(crop_y) != Some(u32::from(header.height)) { return invalid(); }
    Ok(())
}
fn marker(data: &[u8], from: usize) -> Option<usize> {
    data.get(from..)?.windows(3).position(|bytes| bytes == [0,0,1]).map(|offset| from+offset)
}
fn h264(header: &MediaHeader, data: &[u8]) -> Result<()> {
    let mut start = marker(data, 0).ok_or(ProtocolError::InvalidFrame)?;
    if start > 5 || data[..start].iter().any(|byte| *byte != 0) { return invalid(); }
    let mut count = 0; let mut got_sps = false; let mut got_pps = false; let mut got_slice = false; let mut got_idr = false;
    loop {
        count += 1; if count > 256 { return invalid(); }
        let next = marker(data, start+3);
        let mut end = next.unwrap_or(data.len());
        while end > start+3 && data[end-1] == 0 { end -= 1; }
        let nal = data.get(start+3..end).ok_or(ProtocolError::InvalidFrame)?;
        let tag = *nal.first().ok_or(ProtocolError::InvalidFrame)?;
        if tag & 128 != 0 { return invalid(); }
        match tag & 31 {
            7 => { sps(&nal[1..], header)?; got_sps = true; },
            8 => {
                let mut bits = Bits::new(&nal[1..])?; bits.unsigned(255)?; bits.unsigned(31)?;
                bits.read(1)?; bits.read(1)?; bits.unsigned(0)?; got_pps = true;
            },
            1 | 5 => {
                // Only the slice prefix is needed; encoded picture bytes may
                // be large, while this parse stays within a fixed small bound.
                let mut bits = Bits::slice_prefix(&nal[1..])?;
                bits.unsigned(u32::from(header.width).div_ceil(16)*u32::from(header.height).div_ceil(16)-1)?;
                let kind = bits.unsigned(9)? % 5;
                if kind == 1 { return invalid(); } // B-frame reordering is not negotiated
                bits.unsigned(255)?; got_slice = true; got_idr |= tag & 31 == 5;
            },
            6 => { if nal.len() > 65536 { return invalid(); } },
            9 | 12 => {},
            _ => return invalid(),
        }
        if let Some(next) = next { start = next; } else { break; }
    }
    if !got_slice || got_idr != header.keyframe || (header.keyframe && !(got_sps && got_pps)) { return invalid(); }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::media::MediaKind;
    fn audio(data: &[u8]) -> MediaHeader {
        MediaHeader { kind: MediaKind::Audio, codec: Codec::Opus, keyframe: false, generation: 2,
            authorization_epoch: 3, source_id: 9, sequence: 0, timestamp_us: 0, duration_us: 20_000,
            length: data.len(), width: 0, height: 0, channels: 1 }
    }
    #[test]
    fn media_codec_opus_duration_lengths_padding_and_truncation_are_bounded() {
        for data in [&[0xf8][..], &[0xf8,7,8][..], &[0xf1,7,8][..], &[0xf2,1,7,8][..],
            &[0xe3,8][..], &[0xf3,0xc2,1,1,7,8,0][..]] { assert!(validate(&audio(data),data).is_ok(), "{data:?}"); }
        for data in [&[][..], &[0xf0,7][..], &[0xf1,7][..], &[0xf2,252][..], &[0xfb,0][..],
            &[0xfb,0x42,255][..], &[0xf3,0x82,255,255][..], &[0xfc,7][..]] { assert!(validate(&audio(data),data).is_err(), "{data:?}"); }
        let large = vec![0xf8;1277]; assert!(validate(&audio(&large),&large).is_err());
    }
    #[test]
    fn media_codec_vp8_keyframe_geometry_hidden_frames_and_partition_size_are_checked() {
        let data = vec![0x30,0,0,0x9d,1,0x2a,0x80,2,0x68,1,7];
        let header = MediaHeader { kind: MediaKind::Screen, codec: Codec::Vp8, keyframe: true,
            width: 640, height: 360, channels: 0, duration_us: 33_333, ..audio(&data) };
        assert!(validate(&header,&data).is_ok());
        let mut wrong=data.clone(); wrong[7]=0x42; assert!(validate(&header,&wrong).is_err());
        let mut wrong=data.clone(); wrong[0]&=!16; assert!(validate(&header,&wrong).is_err());
        let mut wrong=data.clone(); wrong[0]=0xf0; assert!(validate(&header,&wrong).is_err());
        assert!(validate(&MediaHeader { keyframe: false, ..header.clone() },&data).is_err());
    }
    #[test]
    fn media_codec_hostile_h264_prefix_and_header_floods_do_not_reach_decoder() {
        let data = vec![0,0,0,1,0x65,0xff];
        let header=MediaHeader { kind: MediaKind::Screen, codec: Codec::H264, keyframe: true,
            width: 640, height: 360, channels: 0, duration_us: 33_333, ..audio(&data) };
        assert!(validate(&header,&data).is_err());
        let flood=[0,0,1,0x09,0xff].repeat(257);
        assert!(validate(&MediaHeader { length: flood.len(), ..header.clone() },&flood).is_err());
        let mut bits=Bits::new(&[0;4096]).unwrap(); assert!(bits.unsigned(31).is_err());
        assert!(Bits::new(&[0;4097]).is_err()); assert!(Bits::new(&[0,0,3,255]).is_err());
        let mut slice = vec![0x55; 129]; slice[0] = 0xe0;
        slice[125..129].copy_from_slice(&[0,0,3,1]);
        let mut prefix = Bits::slice_prefix(&slice).unwrap();
        assert_eq!((prefix.unsigned(262143).unwrap(), prefix.unsigned(9).unwrap(), prefix.unsigned(255).unwrap()), (0,0,0));
    }
}
