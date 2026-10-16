// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Canonical application identity and connection aliases for every transport.
//! Only a host's verified pairing entry point binds an owner; wire headers do not.

use std::collections::{BTreeSet, HashMap};

fn whitespace(value: char) -> bool {
    value.is_whitespace() || matches!(value, '\u{1c}'..='\u{1f}')
}
fn sender(value: &str) -> &str {
    let value = value.trim_matches(whitespace);
    if value.is_empty() {
        "unknown"
    } else {
        value
    }
}
fn transport(value: &str) -> String {
    sender(value).to_lowercase()
}
pub fn owner_key(transport_name: &str, sender_id: &str) -> String {
    format!("{}:{}", transport(transport_name), sender(sender_id))
}
pub fn transport_alias(transport_name: &str, sender_id: &str) -> String {
    format!("transport:{}", owner_key(transport_name, sender_id))
}
pub fn webrtc_alias(session: &str) -> String {
    format!("webrtc:{}", sender(session))
}
pub fn guest_owner_key(session: &str) -> String {
    format!("guest:{}", sender(session))
}
pub fn canonical_user_id(owner: &str) -> String {
    format!("user::{owner}")
}

fn thread(value: Option<&str>) -> Option<String> {
    let value = value?.trim_matches(whitespace);
    let value = value.strip_prefix('+').unwrap_or(value);
    if value.is_empty() || !value.bytes().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let value = value.trim_start_matches('0');
    if value.is_empty() || value == "1" {
        None
    } else {
        Some(value.into())
    }
}
pub fn canonical_session_id(owner: &str, thread_id: Option<&str>) -> String {
    session_id(owner, thread(thread_id).as_deref())
}
fn session_id(owner: &str, normalized_thread: Option<&str>) -> String {
    match normalized_thread {
        Some(thread) => format!("session::{owner}::{thread}"),
        None => format!("session::{owner}"),
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Identity {
    pub transport: String,
    pub sender_id: String,
    pub raw_session_id: String,
    pub owner_key: String,
    pub canonical_user_id: String,
    pub canonical_session_id: String,
    pub thread_id: Option<String>,
    pub pairing_mode: String,
    pub destination_session_id: String,
}
fn identity(
    owner: String,
    raw: &str,
    thread_id: Option<&str>,
    pairing_mode: String,
    destination: String,
) -> Identity {
    let (kind, id) = owner.split_once(':').unwrap_or((&owner, ""));
    let normalized_thread = thread(thread_id);
    Identity {
        transport: transport(kind),
        sender_id: sender(if id.is_empty() { raw } else { id }).into(),
        raw_session_id: sender(raw).into(),
        canonical_user_id: canonical_user_id(&owner),
        canonical_session_id: session_id(&owner, normalized_thread.as_deref()),
        thread_id: normalized_thread,
        owner_key: owner,
        pairing_mode,
        destination_session_id: destination,
    }
}

#[derive(Default)]
pub struct IdentityIndex {
    aliases: HashMap<String, String>,
    pairing_modes: HashMap<String, String>,
    destinations: HashMap<String, String>,
}
impl IdentityIndex {
    pub fn bind(
        &mut self,
        kind: &str,
        id: &str,
        raw: Option<&str>,
        thread_id: Option<&str>,
        mode: &str,
        destination: &str,
    ) -> Identity {
        let owner = owner_key(kind, id);
        self.aliases
            .insert(format!("transport:{owner}"), owner.clone());
        let destination = destination.trim_matches(whitespace).to_string();
        if let Some(raw) = raw {
            let alias = webrtc_alias(raw);
            self.aliases.insert(alias.clone(), owner.clone());
            self.destinations.remove(&alias);
            if !destination.is_empty() {
                self.destinations.insert(alias, destination.clone());
            }
        }
        let mode = mode.trim_matches(whitespace).to_string();
        if !mode.is_empty() {
            self.pairing_modes.insert(owner.clone(), mode.clone());
        }
        // Binding returns this proof's mode, while later resolutions retain the last nonempty mode.
        let mut result = identity(owner, raw.unwrap_or(id), thread_id, mode, destination);
        result.transport = transport(kind);
        result.sender_id = sender(id).into();
        result
    }
    pub fn release(&mut self, raw: &str, expected_owner: &str) -> bool {
        let alias = webrtc_alias(raw);
        if self.aliases.get(&alias).map(String::as_str) != Some(expected_owner) {
            return false;
        }
        self.aliases.remove(&alias);
        self.destinations.remove(&alias);
        true
    }
    pub fn alias(
        &mut self,
        existing: &str,
        alias: &str,
        thread_id: Option<&str>,
    ) -> Option<Identity> {
        let owner = self.aliases.get(&webrtc_alias(existing))?.clone();
        let key = webrtc_alias(alias);
        if self.aliases.get(&key).is_some_and(|old| old != &owner) {
            return None;
        }
        let destination = self
            .destinations
            .get(&webrtc_alias(existing))
            .cloned()
            .unwrap_or_default();
        self.aliases.insert(key.clone(), owner.clone());
        self.destinations.remove(&key);
        if !destination.is_empty() {
            self.destinations.insert(key, destination.clone());
        }
        Some(self.resolved(owner, alias, thread_id, destination))
    }
    fn resolved(
        &self,
        owner: String,
        raw: &str,
        thread_id: Option<&str>,
        destination: String,
    ) -> Identity {
        let mode = self.pairing_modes.get(&owner).cloned().unwrap_or_default();
        identity(owner, raw, thread_id, mode, destination)
    }
    pub fn resolve_transport(&self, kind: &str, id: &str, thread_id: Option<&str>) -> Identity {
        let owner = self
            .aliases
            .get(&transport_alias(kind, id))
            .cloned()
            .unwrap_or_else(|| owner_key(kind, id));
        self.resolved(owner, id, thread_id, String::new())
    }
    pub fn resolve_webrtc(&self, raw: &str, thread_id: Option<&str>) -> Identity {
        let key = webrtc_alias(raw);
        let owner = self
            .aliases
            .get(&key)
            .cloned()
            .unwrap_or_else(|| guest_owner_key(raw));
        self.resolved(
            owner,
            raw,
            thread_id,
            self.destinations.get(&key).cloned().unwrap_or_default(),
        )
    }
    pub fn known_owners(&self) -> Vec<String> {
        self.aliases
            .values()
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }
    pub fn clear_sessions(&mut self) {
        self.aliases.clear();
        self.destinations.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn namespaces_unicode_and_arbitrary_size_threads_keep_canonical_ids() {
        assert_eq!(
            owner_key("\u{1c}WHATSAPP\u{2003}", "  synthetic:sender  "),
            "whatsapp:synthetic:sender"
        );
        assert_eq!(owner_key("ΟΣ", "İ"), "ος:İ");
        assert_eq!(owner_key("", ""), "unknown:unknown");
        for value in [None, Some("0"), Some("1"), Some("-2"), Some("wrong")] {
            assert_eq!(
                canonical_session_id("synthetic-owner", value),
                "session::synthetic-owner"
            );
        }
        assert_eq!(
            canonical_session_id("synthetic-owner", Some("+0007")),
            "session::synthetic-owner::7"
        );
        assert_eq!(
            canonical_session_id("synthetic-owner", Some("184467440737095516160")),
            "session::synthetic-owner::184467440737095516160"
        );
    }
    #[test]
    fn alias_takeover_and_delayed_disconnect_never_replace_current_owner() {
        let mut index = IdentityIndex::default();
        let first = index.bind(
            "local",
            "synthetic-first",
            Some("synthetic-connection"),
            None,
            "secure_pair",
            "synthetic-destination",
        );
        let alias = index
            .alias("synthetic-connection", "synthetic-alias", Some("7"))
            .unwrap();
        assert_eq!(
            alias.canonical_session_id,
            format!("session::{}::7", first.owner_key)
        );
        assert_eq!(alias.destination_session_id, "synthetic-destination");
        let second = index.bind(
            "local",
            "synthetic-second",
            Some("synthetic-replacement"),
            None,
            "pair",
            "",
        );
        assert!(index
            .alias("synthetic-connection", "synthetic-replacement", None)
            .is_none());
        assert!(!index.release("synthetic-replacement", &first.owner_key));
        assert_eq!(index.resolve_webrtc("synthetic-replacement", None), second);
        index.bind(
            "local",
            "synthetic-second",
            Some("synthetic-connection"),
            None,
            "",
            "",
        );
        assert!(!index.release("synthetic-connection", &first.owner_key));
        assert!(index.release("synthetic-connection", &second.owner_key));
        assert_eq!(
            index.resolve_webrtc("synthetic-connection", None).transport,
            "guest"
        );
    }
    #[test]
    fn clearing_connections_preserves_canonical_owner_history_and_pairing_mode() {
        let mut index = IdentityIndex::default();
        let first = index.bind(
            "direct",
            "synthetic-owner",
            Some("synthetic-connection"),
            None,
            "secure_pair",
            "synthetic-destination",
        );
        assert_eq!(index.known_owners(), vec![first.owner_key.clone()]);
        let rebound = index.bind(
            "direct",
            "synthetic-owner",
            Some("synthetic-connection"),
            None,
            "",
            "",
        );
        assert!(rebound.pairing_mode.is_empty());
        assert_eq!(
            index
                .resolve_webrtc("synthetic-connection", None)
                .pairing_mode,
            "secure_pair"
        );
        assert!(index
            .resolve_webrtc("synthetic-connection", None)
            .destination_session_id
            .is_empty());
        index.clear_sessions();
        assert!(index.known_owners().is_empty());
        let restored = index.resolve_transport("direct", "synthetic-owner", None);
        assert_eq!(restored.canonical_user_id, first.canonical_user_id);
        assert_eq!(restored.pairing_mode, "secure_pair");
        assert_eq!(
            index.resolve_webrtc("synthetic-connection", None).owner_key,
            "guest:synthetic-connection"
        );
    }
}
