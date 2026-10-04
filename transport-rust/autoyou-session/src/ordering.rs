// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use std::collections::{BTreeMap, HashMap};
use autoyou_protocol::{Frame, ProtocolError};

pub const MAX_LOGICAL_STREAMS: usize = 128;
pub const MAX_REORDER_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_SEQUENCE_GAP: u64 = 128;
const MAX_RETIREMENT_RANGES: usize = 1024;

/// Retired IDs are never reusable within a connection generation. Sequential
/// IDs compress to one interval per allocation parity, while a hostile sparse
/// flood is bounded. Initiator/acceptor IDs advance by two to avoid collision.
#[derive(Default)]
pub struct RetiredStreams { lanes: HashMap<(u8,u8), Vec<(u64,u64)>> }
impl RetiredStreams {
    pub fn contains(&self, lane: u8, stream_id: u64) -> bool {
        let parity = (stream_id % 2) as u8;
        let stream_id = stream_id / 2;
        self.lanes.get(&(lane,parity)).is_some_and(|ranges| {
            let index = ranges.partition_point(|(start,_)| *start <= stream_id);
            index > 0 && stream_id <= ranges[index - 1].1
        })
    }
    pub fn retire(&mut self, lane: u8, stream_id: u64) -> Result<(), ProtocolError> {
        if self.contains(lane, stream_id) { return Ok(()); }
        let parity = (stream_id % 2) as u8;
        let stream_id = stream_id / 2;
        let ranges = self.lanes.entry((lane,parity)).or_default();
        let index = ranges.partition_point(|(start,_)| *start < stream_id);
        let merge_left = index > 0 && ranges[index-1].1.checked_add(1) == Some(stream_id);
        let merge_right = index < ranges.len() && stream_id.checked_add(1) == Some(ranges[index].0);
        match (merge_left, merge_right) {
            (true,true) => { let end = ranges.remove(index).1; ranges[index-1].1 = end; }
            (true,false) => ranges[index-1].1 = stream_id,
            (false,true) => ranges[index].0 = stream_id,
            (false,false) => {
                if ranges.len() >= MAX_RETIREMENT_RANGES { return Err(ProtocolError::FrameTooLarge); }
                ranges.insert(index, (stream_id,stream_id));
            }
        }
        Ok(())
    }
}

#[derive(Default)]
struct Stream { next: u64, pending: BTreeMap<u64, Frame> }

#[derive(Default)]
pub struct OrderedReceiver {
    streams: HashMap<(u8, u64), Stream>,
    bytes: usize,
}

impl OrderedReceiver {
    pub fn is_duplicate(&self, frame: &Frame) -> bool {
        self.streams.get(&(frame.lane as u8, frame.stream_id)).is_some_and(|stream|
            frame.sequence < stream.next || stream.pending.contains_key(&frame.sequence))
    }
    pub fn receive(&mut self, frame: Frame) -> Result<Vec<Frame>, ProtocolError> {
        let key = (frame.lane as u8, frame.stream_id);
        if !self.streams.contains_key(&key) && self.streams.len() >= MAX_LOGICAL_STREAMS {
            return Err(ProtocolError::FrameTooLarge);
        }
        let stream = self.streams.entry(key).or_default();
        if frame.sequence < stream.next || stream.pending.contains_key(&frame.sequence) {
            return Ok(Vec::new());
        }
        if frame.sequence - stream.next >= MAX_SEQUENCE_GAP ||
            self.bytes.saturating_add(frame.payload.len()) > MAX_REORDER_BYTES {
            return Err(ProtocolError::FrameTooLarge);
        }
        self.bytes += frame.payload.len();
        stream.pending.insert(frame.sequence, frame);
        let mut ready = Vec::new();
        while let Some(frame) = stream.pending.remove(&stream.next) {
            self.bytes -= frame.payload.len();
            stream.next = stream.next.checked_add(1).ok_or(ProtocolError::InvalidFrame)?;
            ready.push(frame);
        }
        Ok(ready)
    }
    pub fn retire(&mut self, lane: u8, stream_id: u64) {
        if let Some(stream) = self.streams.remove(&(lane, stream_id)) {
            self.bytes -= stream.pending.values().map(|frame| frame.payload.len()).sum::<usize>();
        }
    }
    pub fn buffered_bytes(&self) -> usize { self.bytes }
}

#[cfg(test)]
mod tests {
    use super::*;
    use autoyou_protocol::Lane;
    fn frame(stream_id: u64, sequence: u64) -> Frame {
        Frame { lane: Lane::Http, generation: 1, stream_id, sequence, payload: vec![sequence as u8] }
    }
    #[test]
    fn reliable_order_is_per_request_and_duplicates_are_bounded() {
        let mut receiver = OrderedReceiver::default();
        assert!(receiver.receive(frame(1, 1)).unwrap().is_empty());
        assert_eq!(receiver.receive(frame(2, 0)).unwrap()[0].stream_id, 2);
        let ready = receiver.receive(frame(1, 0)).unwrap();
        assert_eq!(ready.iter().map(|frame| frame.sequence).collect::<Vec<_>>(), [0, 1]);
        assert!(receiver.receive(frame(1, 1)).unwrap().is_empty());
        assert!(receiver.receive(frame(1, u64::MAX)).is_err());
        receiver.retire(Lane::Http as u8, 1);
        assert_eq!(receiver.buffered_bytes(), 0);
    }
    #[test]
    fn stream_flood_cannot_create_unbounded_state() {
        let mut receiver = OrderedReceiver::default();
        for id in 0..MAX_LOGICAL_STREAMS as u64 { receiver.receive(frame(id, 0)).unwrap(); }
        assert!(receiver.receive(frame(MAX_LOGICAL_STREAMS as u64, 0)).is_err());
    }
    #[test]
    fn retirement_fences_late_frames_without_unbounded_tombstones() {
        let mut retired = RetiredStreams::default();
        for id in 0..100_000 { retired.retire(Lane::Http as u8, id).unwrap(); }
        assert_eq!(retired.lanes[&(Lane::Http as u8,0)], [(0,49_999)]);
        assert_eq!(retired.lanes[&(Lane::Http as u8,1)], [(0,49_999)]);
        retired.retire(Lane::Http as u8, u64::MAX).unwrap();
        assert!(retired.contains(Lane::Http as u8, u64::MAX));
        assert!(!retired.contains(Lane::Binary as u8, 5));
        assert!(!retired.contains(Lane::Http as u8, 100_000));
        let mut sparse = RetiredStreams::default();
        for id in 0..MAX_RETIREMENT_RANGES as u64 { sparse.retire(4, 4*id).unwrap(); }
        assert!(sparse.retire(4, 4*MAX_RETIREMENT_RANGES as u64).is_err());
        sparse.retire(4, 2).unwrap();
        assert!(sparse.contains(4, 0) && sparse.contains(4, 2) && sparse.contains(4, 4));
        assert!(!sparse.contains(4, 1));
        assert!(sparse.retire(4, 4*MAX_RETIREMENT_RANGES as u64).is_ok());
    }
    #[test]
    fn retirement_of_ongoing_parity_allocated_uploads_does_not_exhaust_the_connection() {
        let mut retired = RetiredStreams::default();
        for id in (3..200_003).step_by(2) { retired.retire(4,id).unwrap(); }
        assert_eq!(retired.lanes[&(4,1)].len(),1);
        assert!(retired.contains(4,200_001));
        assert!(!retired.contains(4,200_000));
    }
}
