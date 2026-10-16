// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Bounded scheduling shared by bindings; control has reserved queue capacity.
//! Each dequeue opens an independent stream, avoiding a blocked bulk writer.

use std::collections::VecDeque;
use autoyou_protocol::{Frame, FrameHeader, Lane, ProtocolError};

pub const MAX_QUEUED_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_QUEUED_FRAMES: usize = 256;
const RESERVED_CONTROL_FRAMES: usize = 16;
const RESERVED_CONTROL_BYTES: usize = 1024 * 1024;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum QueueError {
    #[error(transparent)] Protocol(#[from] ProtocolError),
    #[error("transport queue is full")] Full,
    #[error("transport queue is closed")] Closed,
    #[error("reliable frames cannot expire in the transport queue")] InvalidDeadline,
}

#[derive(Debug)]
pub struct ScheduledFrame { pub frame: Frame, pub deadline_ms: Option<u64> }

#[derive(Debug, Default)]
pub struct Scheduler {
    lanes: [VecDeque<ScheduledFrame>; 5],
    bytes: usize,
    frames: usize,
    lower_cursor: usize,
    control_burst: usize,
    closed: bool,
}

impl Scheduler {
    pub fn push(&mut self, frame: Frame, deadline_ms: Option<u64>) -> Result<(), QueueError> {
        if self.closed { return Err(QueueError::Closed); }
        if deadline_ms.is_some() && !matches!(frame.lane, Lane::Media | Lane::Input) {
            return Err(QueueError::InvalidDeadline);
        }
        if frame.lane == Lane::Input && deadline_ms.is_none() { return Err(QueueError::InvalidDeadline); }
        FrameHeader { stream_id: frame.stream_id, sequence: frame.sequence, lane: frame.lane,
            generation: frame.generation, length: frame.payload.len() }.encode()?;
        let priority = frame.lane.priority() as usize;
        let max_frames = if priority == 0 { MAX_QUEUED_FRAMES } else { MAX_QUEUED_FRAMES - RESERVED_CONTROL_FRAMES };
        let max_bytes = if priority == 0 { MAX_QUEUED_BYTES } else { MAX_QUEUED_BYTES - RESERVED_CONTROL_BYTES };
        if self.frames >= max_frames || self.bytes.saturating_add(frame.payload.len()) > max_bytes {
            return Err(QueueError::Full);
        }
        self.bytes += frame.payload.len(); self.frames += 1;
        self.lanes[priority].push_back(ScheduledFrame { frame, deadline_ms });
        Ok(())
    }

    /// Validate an entire reliable message before mutating the queue. A foreign
    /// caller may retry Backpressure without replaying a partially queued body.
    pub fn push_batch(&mut self, frames: Vec<Frame>) -> Result<(), QueueError> {
        if self.closed { return Err(QueueError::Closed); }
        if frames.is_empty() || frames.len() > MAX_QUEUED_FRAMES { return Err(QueueError::Full); }
        let mut bytes = self.bytes; let mut count = self.frames;
        for frame in &frames {
            FrameHeader { stream_id: frame.stream_id, sequence: frame.sequence, lane: frame.lane,
                generation: frame.generation, length: frame.payload.len() }.encode()?;
            if matches!(frame.lane, Lane::Media | Lane::Input) { return Err(QueueError::InvalidDeadline); }
            let control = frame.lane.priority() == 0;
            let max_bytes = MAX_QUEUED_BYTES - if control { 0 } else { RESERVED_CONTROL_BYTES };
            let max_frames = MAX_QUEUED_FRAMES - if control { 0 } else { RESERVED_CONTROL_FRAMES };
            bytes = bytes.checked_add(frame.payload.len()).ok_or(QueueError::Full)?;
            count += 1;
            if count > max_frames || bytes > max_bytes { return Err(QueueError::Full); }
        }
        for frame in frames {
            self.bytes += frame.payload.len(); self.frames += 1;
            self.lanes[frame.lane.priority() as usize].push_back(ScheduledFrame { frame, deadline_ms: None });
        }
        Ok(())
    }

    pub fn pop(&mut self, now_ms: u64) -> Option<Frame> {
        self.pop_for_capacity(now_ms, true, true, |_| {})
    }

    pub fn pop_for_capacity(&mut self, now_ms: u64, control_allowed: bool, other_allowed: bool,
        on_expired: impl FnMut(&Frame)) -> Option<Frame> {
        self.pop_scheduled(now_ms, control_allowed, other_allowed, on_expired).map(|item| item.frame)
    }

    /// Keep the deadline attached while opening/writing/acknowledging the QUIC
    /// stream. Expiry after dequeue must not leave a blocked media writer alive.
    pub fn pop_scheduled(&mut self, now_ms: u64, control_allowed: bool, other_allowed: bool,
        mut on_expired: impl FnMut(&Frame)) -> Option<ScheduledFrame> {
        loop {
            // At most eight urgent frames before one lower-priority item. This
            // makes cancellation immediate without starving app/bulk progress.
            let control = control_allowed && !self.lanes[0].is_empty();
            let mut chosen = if control && self.control_burst < 8 { Some(0) } else { None };
            if chosen.is_none() && other_allowed {
                for offset in 0..4 {
                    let index = 1 + (self.lower_cursor + offset) % 4;
                    if !self.lanes[index].is_empty() {
                        chosen = Some(index); self.lower_cursor = index % 4; break;
                    }
                }
            }
            if chosen.is_none() && control { chosen = Some(0); }
            let index = chosen?;
            let pending = self.lanes[index].pop_front().unwrap();
            self.bytes -= pending.frame.payload.len(); self.frames -= 1;
            if index == 0 { self.control_burst = self.control_burst.saturating_add(1); } else { self.control_burst = 0; }
            if pending.deadline_ms.is_some_and(|deadline| deadline <= now_ms) {
                on_expired(&pending.frame); continue;
            }
            return Some(pending);
        }
    }

    pub fn queued_bytes(&self) -> usize { self.bytes }
    pub fn retire_stream(&mut self, lane: u8, stream_id: u64, mut on_removed: impl FnMut(&Frame)) {
        for queue in &mut self.lanes {
            queue.retain(|pending| {
                if pending.frame.lane as u8 != lane || pending.frame.stream_id != stream_id { return true; }
                self.bytes -= pending.frame.payload.len(); self.frames -= 1;
                on_removed(&pending.frame); false
            });
        }
    }
    pub fn close(&mut self) {
        self.closed = true;
        for lane in &mut self.lanes { lane.clear(); }
        self.bytes = 0; self.frames = 0;
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn input_deadlines_release_bytes_without_starving_control() {
        let mut queue = super::Scheduler::default();
        let input = autoyou_protocol::Frame { lane: autoyou_protocol::Lane::Input, stream_id: 0,
            sequence: 0, generation: 1, payload: b"{}".to_vec() };
        assert_eq!(queue.push(input.clone(), None), Err(super::QueueError::InvalidDeadline));
        assert_eq!(queue.push_batch(vec![input.clone()]), Err(super::QueueError::InvalidDeadline));
        queue.push(input, Some(120)).unwrap();
        queue.push(autoyou_protocol::Frame { lane: autoyou_protocol::Lane::Control, stream_id: 0,
            sequence: 0, generation: 1, payload: b"end".to_vec() }, None).unwrap();
        assert_eq!(queue.pop_scheduled(100, true, false, |_| {}).unwrap().frame.lane,
            autoyou_protocol::Lane::Control);
        let mut retired = 0;
        assert!(queue.pop_scheduled(120, true, true, |_| retired += 1).is_none());
        assert_eq!(retired, 1); assert_eq!(queue.queued_bytes(), 0);
    }
    use super::*;
    use autoyou_protocol::Lane;
    fn frame(lane: Lane) -> Frame { Frame { stream_id: 0, sequence: 0, lane, generation: 1, payload: vec![0; 64*1024] } }
    #[test]
    fn saturated_bulk_keeps_room_for_cancel_and_never_starves_bulk() {
        let mut queue = Scheduler::default();
        while queue.push(frame(Lane::Binary), None).is_ok() {}
        queue.push(Frame { stream_id: 0, sequence: 0, lane: Lane::Control, generation: 1, payload: b"cancel".to_vec() }, None).unwrap();
        assert_eq!(queue.pop(0).unwrap().lane, Lane::Control);
        for _ in 0..16 { queue.push(Frame { stream_id: 0, sequence: 0, lane: Lane::Control, generation: 1, payload: vec![1] }, None).unwrap(); }
        assert!((0..9).any(|_| queue.pop(0).unwrap().lane == Lane::Binary));
        assert!(queue.queued_bytes() <= MAX_QUEUED_BYTES);
    }
    #[test]
    fn expired_media_drops_and_shutdown_releases_all_memory() {
        let mut queue = Scheduler::default();
        queue.push(frame(Lane::Media), Some(10)).unwrap();
        queue.push(frame(Lane::Application), None).unwrap();
        assert_eq!(queue.pop(10).unwrap().lane, Lane::Application);
        assert_eq!(queue.queued_bytes(), 0);
        queue.close(); assert_eq!(queue.push(frame(Lane::Binary), None), Err(QueueError::Closed));
    }
    #[test]
    fn media_deadline_survives_dequeue_and_reliable_frames_have_no_deadline() {
        let mut queue = Scheduler::default();
        queue.push(frame(Lane::Media), Some(120)).unwrap();
        let scheduled = queue.pop_scheduled(100, true, true, |_| {}).unwrap();
        assert_eq!(scheduled.deadline_ms, Some(120));
        assert_eq!(scheduled.frame.lane, Lane::Media);
        queue.push(frame(Lane::Application), None).unwrap();
        assert_eq!(queue.pop_scheduled(100, true, true, |_| {}).unwrap().deadline_ms, None);
        assert_eq!(queue.queued_bytes(), 0);
    }
    #[test]
    fn dropped_deadlines_and_retired_streams_release_owned_allocations() {
        let mut queue = Scheduler::default();
        let mut expired = Vec::new();
        queue.push(frame(Lane::Media), Some(1)).unwrap();
        queue.push(frame(Lane::Binary), None).unwrap();
        let ready = queue.pop_for_capacity(1, false, true, |frame| expired.push(frame.lane));
        assert_eq!(ready.unwrap().lane, Lane::Binary);
        assert_eq!(expired, [Lane::Media]);
        queue.push(frame(Lane::Http), None).unwrap();
        queue.push(frame(Lane::Binary), None).unwrap();
        let mut retired = 0;
        queue.retire_stream(Lane::Http as u8, 0, |_| retired += 1);
        assert_eq!(retired, 1);
        assert_eq!(queue.queued_bytes(), 64*1024);
        assert_eq!(queue.pop(1).unwrap().lane, Lane::Binary);
    }
    #[test]
    fn reliable_frames_never_create_sequence_gaps_by_deadline() {
        let mut queue = Scheduler::default();
        for lane in [Lane::Enrollment, Lane::Control, Lane::Application, Lane::Http,
            Lane::ServerEvents, Lane::WebSocket, Lane::Binary] {
            assert_eq!(queue.push(Frame { payload: vec![1], ..frame(lane) }, Some(1)), Err(QueueError::InvalidDeadline));
        }
        assert_eq!(queue.queued_bytes(), 0);
        queue.push(frame(Lane::Application), None).unwrap();
        assert_eq!(queue.pop(u64::MAX).unwrap().lane, Lane::Application);
    }
    #[test]
    fn atomic_browser_batch_backpressure_does_not_queue_a_partial_message() {
        let mut queue = Scheduler::default();
        while queue.push(frame(Lane::Http), None).is_ok() {}
        let before = queue.queued_bytes();
        assert_eq!(queue.push_batch(vec![frame(Lane::Http), frame(Lane::Http)]), Err(QueueError::Full));
        assert_eq!(queue.queued_bytes(), before);
        queue.push_batch(vec![Frame { payload: vec![1], ..frame(Lane::Control) }]).unwrap();
        assert_eq!(queue.pop(0).unwrap().lane, Lane::Control);
    }
}
