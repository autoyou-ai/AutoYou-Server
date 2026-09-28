// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-0c19a3c7c1ad5087d8c395f9

"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const base = (() => {
    // Works whether served at /agent/persona_agent/ (proxy) or at root.
    const m = window.location.pathname.match(/^(.*\/agent\/persona_agent)\b/);
    return m ? m[1] : "";
  })();

  let toastTimer = null;
  function toast(message) {
    const el = $("toast");
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  async function api(path, options) {
    const res = await fetch(base + path, Object.assign({ headers: { "Content-Type": "application/json" } }, options));
    const text = await res.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) {}
    if (res.status === 401) {
      showAuth();
      throw new Error((json && json.error) || "Not authenticated");
    }
    if (!res.ok) {
      throw new Error((json && (json.error || json.message)) || res.statusText);
    }
    return json || {};
  }

  function showAuth() {
    $("authView").hidden = false;
    $("authView").inert = false;
    $("personaView").hidden = true;
    $("personaView").inert = true;
    $("logoutBtn").hidden = true;
    $("statusChip").textContent = "Locked";
    setTimeout(() => $("authCode").focus(), 50);
  }

  function showPersona(canLock = true) {
    $("authView").hidden = true;
    $("authView").inert = true;
    $("personaView").hidden = false;
    $("personaView").inert = false;
    $("logoutBtn").hidden = !canLock;
  }

  function renderStatus(status) {
    const chip = $("statusChip");
    if (!status || !status.exists) {
      chip.textContent = "Empty";
      chip.className = "chip";
      $("metaLine").textContent = "No profile yet - add your first detail above.";
      return;
    }
    const encrypted = !!status.encrypted_on_disk;
    chip.textContent = encrypted ? "Encrypted" : "Saved";
    chip.className = encrypted ? "chip encrypted" : "chip";
    const chars = status.character_count >= 0 ? status.character_count + " chars" : "unreadable";
    $("metaLine").textContent = (encrypted ? "Encrypted at rest · " : "") + chars;
  }

  async function refresh() {
    const status = await api("/api/status", { method: "GET" });
    renderStatus(status);
    const data = await api("/api/persona", { method: "GET" });
    $("personaView_md").textContent = (data.content && data.content.trim()) ? data.content : "No profile yet.";
  }

  async function init() {
    let auth;
    try {
      auth = await api("/api/auth/status", { method: "GET" });
      if (!auth.authenticated) { showAuth(); return; }
    } catch (e) { showAuth(); return; }
    showPersona(auth.auth_mode !== "open");
    try { await refresh(); } catch (e) { toast(e.message); }
  }

  // Auth
  $("authForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("authError").hidden = true;
    const code = $("authCode").value.trim();
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ totp_code: code }) });
      showPersona();
      await refresh();
      toast("Unlocked");
    } catch (err) {
      $("authError").textContent = err.message || "Could not unlock.";
      $("authError").hidden = false;
      $("authCode").value = "";
      $("authCode").focus();
    }
  });

  $("logoutBtn").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
    showAuth();
  });

  // Append
  $("appendForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("appendText").value.trim();
    if (!text) { toast("Write something to append."); return; }
    try {
      await api("/api/persona/append", { method: "POST", body: JSON.stringify({ text, heading: $("appendHeading").value.trim() }) });
      $("appendText").value = "";
      $("appendHeading").value = "";
      await refresh();
      toast("Added to your persona");
    } catch (err) { toast(err.message); }
  });

  // Edit / Save
  $("editBtn").addEventListener("click", () => {
    $("personaEditor").value = $("personaView_md").textContent === "No profile yet." ? "" : $("personaView_md").textContent;
    $("editor").hidden = false;
    $("personaView_md").hidden = true;
    $("personaEditor").focus();
  });
  $("cancelEditBtn").addEventListener("click", () => {
    $("editor").hidden = true;
    $("personaView_md").hidden = false;
  });
  $("saveBtn").addEventListener("click", async () => {
    const content = $("personaEditor").value.trim();
    if (!content) { toast("Nothing to save. Use Wipe to clear."); return; }
    try {
      await api("/api/persona", { method: "POST", body: JSON.stringify({ content }) });
      $("editor").hidden = true;
      $("personaView_md").hidden = false;
      await refresh();
      toast("Saved");
    } catch (err) { toast(err.message); }
  });

  $("reloadBtn").addEventListener("click", () => refresh().catch((e) => toast(e.message)));

  // Wipe
  $("wipeBtn").addEventListener("click", async () => {
    if (!window.confirm("Permanently delete your persona profile and its encryption key? This cannot be undone.")) return;
    try {
      await api("/api/persona", { method: "DELETE" });
      await refresh();
      toast("Profile wiped");
    } catch (err) { toast(err.message); }
  });

  init();
})();
