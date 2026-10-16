// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use std::{
    cmp::Ordering,
    collections::{BTreeMap, BTreeSet, HashMap},
};

#[derive(Debug, PartialEq, Eq)]
pub enum LimitsError {
    InvalidInput,
}

// Only finite request times and their non-NaN window boundaries enter the index.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Time(f64);
impl Eq for Time {}
impl PartialOrd for Time {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Time {
    fn cmp(&self, other: &Self) -> Ordering {
        self.0
            .partial_cmp(&other.0)
            .expect("validated rate-limit timestamp")
    }
}

struct Requests {
    stamps: Vec<f64>,
    touched: u64,
}

pub struct RateLimiter {
    max_requests: u64,
    window_seconds: f64,
    max_keys: usize,
    requests: HashMap<String, Requests>,
    expires: BTreeSet<(Time, String)>,
    touched: BTreeMap<u64, String>,
    sequence: u64,
}
impl RateLimiter {
    pub fn new(max_requests: u64, window_seconds: f64, max_keys: u64) -> Result<Self, LimitsError> {
        if !window_seconds.is_finite() || window_seconds <= 0.0 || max_keys == 0 {
            return Err(LimitsError::InvalidInput);
        }
        Ok(Self {
            max_requests,
            window_seconds,
            max_keys: usize::try_from(max_keys).map_err(|_| LimitsError::InvalidInput)?,
            requests: HashMap::new(),
            expires: BTreeSet::new(),
            touched: BTreeMap::new(),
            sequence: 0,
        })
    }
    fn remove(&mut self, key: &str) {
        if let Some(old) = self.requests.remove(key) {
            if let Some(last) = old.stamps.last() {
                self.expires.remove(&(Time(*last), key.into()));
            }
            self.touched.remove(&old.touched);
        }
    }
    pub fn is_allowed(&mut self, key: &str, now: f64) -> bool {
        if !now.is_finite() || key.len() > 4096 {
            return false;
        }
        let Some(sequence) = self.sequence.checked_add(1) else {
            return false;
        };
        let window_start = now - self.window_seconds;
        while self
            .expires
            .first()
            .is_some_and(|(last, _)| last.0 <= window_start)
        {
            let (_, key) = self.expires.first().unwrap().clone();
            self.remove(&key);
        }
        if self.max_requests == 0 {
            return false;
        }
        let mut current = self.requests.remove(key).unwrap_or(Requests {
            stamps: Vec::new(),
            touched: 0,
        });
        if let Some(last) = current.stamps.last() {
            self.expires.remove(&(Time(*last), key.into()));
        }
        self.touched.remove(&current.touched);
        // Retain preserves the existing wall-clock behavior even after an OS clock correction.
        current.stamps.retain(|stamp| *stamp > window_start);
        let allowed = (current.stamps.len() as u64) < self.max_requests;
        if allowed {
            current.stamps.push(now);
        }
        current.touched = sequence;
        self.sequence = sequence;
        self.expires
            .insert((Time(*current.stamps.last().unwrap()), key.into()));
        self.touched.insert(sequence, key.into());
        self.requests.insert(key.into(), current);
        if self.requests.len() > self.max_keys {
            let key = self.touched.first_key_value().unwrap().1.clone();
            self.remove(&key);
        }
        allowed
    }
    pub fn tracked_key_count(&self) -> u64 {
        self.requests.len() as u64
    }
}

#[derive(Clone, Copy)]
pub struct ExpiryRow {
    pub expires_at: Option<f64>,
    pub created_at: f64,
}

pub fn expiring_rows_to_remove(
    rows: &[ExpiryRow],
    max_entries: u64,
    now: f64,
) -> Result<Vec<usize>, LimitsError> {
    if !now.is_finite() {
        return Err(LimitsError::InvalidInput);
    }
    let mut remove = Vec::new();
    let mut live = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        if row
            .expires_at
            .is_none_or(|expires| !expires.is_finite() || now > expires)
        {
            remove.push(index);
        } else {
            let created = if row.created_at.is_finite() {
                row.created_at
            } else {
                0.0
            };
            live.push((index, created));
        }
    }
    let excess = (live.len() as u64).saturating_sub(max_entries) as usize;
    if excess > 0 {
        live.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
        remove.extend(live.iter().take(excess).map(|(index, _)| *index));
    }
    Ok(remove)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rolling_window_preserves_each_key_and_exact_boundary() {
        let mut owner = RateLimiter::new(2, 10.0, 10).unwrap();
        assert!(owner.is_allowed("synthetic-a", 100.0));
        assert!(owner.is_allowed("synthetic-a", 101.0));
        assert!(!owner.is_allowed("synthetic-a", 109.0));
        assert!(owner.is_allowed("synthetic-b", 109.0));
        assert!(owner.is_allowed("synthetic-a", 110.0));
        assert!(!owner.is_allowed("synthetic-a", 110.0));
        assert!(owner.is_allowed("synthetic-a", 111.0));
        assert_eq!(owner.tracked_key_count(), 2);
    }

    #[test]
    fn denied_hits_touch_lru_without_extending_expiry_or_growing_indexes() {
        let mut owner = RateLimiter::new(1, 10.0, 2).unwrap();
        assert!(owner.is_allowed("synthetic-a", 100.0));
        assert!(owner.is_allowed("synthetic-b", 101.0));
        for _ in 0..1000 {
            assert!(!owner.is_allowed("synthetic-a", 102.0));
        }
        assert_eq!(
            (
                owner.requests.len(),
                owner.expires.len(),
                owner.touched.len()
            ),
            (2, 2, 2)
        );
        assert!(owner.is_allowed("synthetic-c", 102.0));
        assert!(!owner.requests.contains_key("synthetic-b"));
        assert!(!owner.is_allowed("synthetic-a", 103.0));
        assert!(owner.is_allowed("synthetic-a", 110.0));
        assert_eq!(owner.tracked_key_count(), 2);
    }

    #[test]
    fn spray_expiry_and_invalid_inputs_remain_bounded() {
        let mut owner = RateLimiter::new(3, 10.0, 128).unwrap();
        for i in 0..10000 {
            assert!(owner.is_allowed(&format!("synthetic-{i}"), 100.0));
        }
        assert_eq!(owner.tracked_key_count(), 128);
        assert!(!owner.is_allowed("synthetic-invalid", f64::NAN));
        assert!(!owner.is_allowed(&"x".repeat(4097), 101.0));
        assert_eq!(owner.tracked_key_count(), 128);
        assert!(owner.is_allowed("synthetic-after-window", 110.0));
        assert_eq!(owner.tracked_key_count(), 1);
        owner.sequence = u64::MAX;
        assert!(!owner.is_allowed("synthetic-overflow", 111.0));
        assert_eq!(owner.tracked_key_count(), 1);
        assert!(RateLimiter::new(1, f64::INFINITY, 1).is_err());
        assert!(RateLimiter::new(1, 0.0, 1).is_err());
        let mut denied = RateLimiter::new(0, 10.0, 1).unwrap();
        assert!(!denied.is_allowed("synthetic-denied", 1.0));
        assert_eq!(denied.tracked_key_count(), 0);
    }

    #[test]
    fn expiry_keeps_equality_and_trims_oldest_with_stable_ties() {
        let rows = [
            ExpiryRow {
                expires_at: None,
                created_at: 0.0,
            },
            ExpiryRow {
                expires_at: Some(f64::INFINITY),
                created_at: 0.0,
            },
            ExpiryRow {
                expires_at: Some(99.0),
                created_at: 0.0,
            },
            ExpiryRow {
                expires_at: Some(100.0),
                created_at: 20.0,
            },
            ExpiryRow {
                expires_at: Some(200.0),
                created_at: 10.0,
            },
            ExpiryRow {
                expires_at: Some(200.0),
                created_at: 10.0,
            },
        ];
        assert_eq!(
            expiring_rows_to_remove(&rows, 2, 100.0).unwrap(),
            vec![0, 1, 2, 4]
        );
        assert_eq!(
            expiring_rows_to_remove(&rows, 0, 100.0).unwrap(),
            vec![0, 1, 2, 4, 5, 3]
        );
        assert!(expiring_rows_to_remove(&rows, 2, f64::NAN).is_err());
    }
}
