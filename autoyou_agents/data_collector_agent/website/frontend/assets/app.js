const state = { token: "", job: null, exports: [] };
const $ = (id) => document.getElementById(id);
const baseUrl = new URL(".", window.location.href);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function lines(value) {
  return String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.remove("hidden");
  clearTimeout(toast.timeout);
  toast.timeout = setTimeout(() => element.classList.add("hidden"), 4200);
}

async function api(path, options = {}) {
  const target = new URL(String(path || "").replace(/^\//, ""), baseUrl).toString();
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(target, { ...options, headers, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.success === false) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
}

function setLocked(locked, data = {}) {
  document.body.classList.toggle("locked", locked);
  $("app").classList.toggle("hidden", locked);
  $("app").inert = locked;
  $("authModal").classList.toggle("hidden", !locked);
  $("authModal").inert = !locked;
  $("authState").textContent = locked ? "Locked" : "Authenticated";
  $("authState").className = `pill ${locked ? "" : "ready"}`;
  if (locked && data.setup_hint) $("authError").textContent = data.setup_hint;
}

async function checkAuth() {
  const data = await api("/api/auth/status");
  setLocked(!data.authenticated, data);
  return data.authenticated;
}

function fillSettings(job) {
  state.job = job;
  const apps = job.apps || {};
  const roots = job.source_roots || {};
  $("codexEnabled").checked = Boolean(apps.codex);
  $("claudeEnabled").checked = Boolean(apps.claude);
  $("chatgptEnabled").checked = Boolean(apps.chatgpt);
  $("copyRaw").checked = Boolean(job.copy_raw);
  $("codexRoots").value = (roots.codex || []).join("\n");
  $("claudeRoots").value = (roots.claude || []).join("\n");
  $("chatgptRoots").value = (roots.chatgpt || []).join("\n");
  $("projectFilters").value = (job.project_filters || []).join("\n");
  $("worktrees").value = (job.worktrees || []).map((item) => item.path || "").filter(Boolean).join("\n");
}

function settingsPayload() {
  return {
    apps: { codex: $("codexEnabled").checked, claude: $("claudeEnabled").checked, chatgpt: $("chatgptEnabled").checked },
    copy_raw: $("copyRaw").checked,
    source_roots: { codex: lines($("codexRoots").value), claude: lines($("claudeRoots").value), chatgpt: lines($("chatgptRoots").value) },
    project_filters: lines($("projectFilters").value),
    worktrees: lines($("worktrees").value).map((path) => ({ path })),
  };
}

function renderStatus(data) {
  const counts = data.counts || {};
  $("sessionCount").textContent = counts.sessions ?? 0;
  $("turnCount").textContent = counts.turns ?? 0;
  $("pairCount").textContent = counts.pairs ?? 0;
  const run = data.run || {};
  const canCancel = ["running", "cancelling"].includes(run.state);
  $("cancelRun").hidden = !canCancel;
  $("cancelRun").disabled = run.state === "cancelling";
  $("cancelRun").textContent = run.state === "cancelling" ? "Stopping..." : "Stop collection";
  $("runState").textContent = String(run.state || "idle").replace(/_/g, " ");
  $("runDetail").textContent = run.error || run.result?.manifest?.created_at || (run.state === "running" ? "Working locally…" : "Waiting for a collection run");
  const sources = data.configured_apps || [];
  $("sourceSummary").textContent = sources.length ? `${sources.length} source${sources.length === 1 ? "" : "s"} enabled` : "No sources enabled";
  $("sourceSummary").className = `pill ${sources.length ? "ready" : ""}`;
  fillSettings(data.job || state.job || {});
}

async function refresh() {
  const data = await api("/api/status");
  renderStatus(data);
  await loadExports();
}

async function saveSettings(event) {
  event?.preventDefault();
  const data = await api("/api/job", { method: "PUT", body: JSON.stringify(settingsPayload()) });
  fillSettings(data.job);
  toast("Collection settings saved.");
}

async function begin(action) {
  await saveSettings();
  const data = await api(`/api/${action}`, { method: "POST", body: JSON.stringify({}) });
  renderStatus({ ...(await api("/api/status")), run: data.run });
  toast(action === "collect" ? "Collection started." : "Rebuilding normalized views.");
}

async function cancelRun() {
  if (!confirm("Stop the active collection at its next safe boundary?")) return;
  const data = await api("/api/run/cancel", { method: "POST", body: JSON.stringify({}) });
  toast(data.message || "Cancellation requested.");
  await refresh();
}

function renderExports() {
  const list = $("exportList");
  if (!state.exports.length) {
    list.className = "list empty";
    list.textContent = "No training exports yet. Collect conversations, then create one here.";
    return;
  }
  list.className = "list";
  list.innerHTML = state.exports.map((item) => `<article class="export-item"><div><div><strong>${escapeHtml(item.title || item.id)}</strong><div class="meta"><span>${escapeHtml(item.created_at || "")}</span><span>${Number(item.train_sample_count || 0)} train samples</span><span>${Number(item.eval_sample_count || 0)} eval samples</span></div></div><button class="secondary handoff-button" data-export-id="${escapeHtml(item.id)}" type="button">Create handoff code</button></div></article>`).join("");
  document.querySelectorAll(".handoff-button").forEach((button) => button.addEventListener("click", () => createHandoff(button.dataset.exportId)));
}

async function loadExports() {
  const data = await api("/api/exports");
  state.exports = data.exports || [];
  renderExports();
}

async function createExport(event) {
  event.preventDefault();
  const data = await api("/api/exports", { method: "POST", body: JSON.stringify({ title: $("exportTitle").value.trim() }) });
  $("exportTitle").value = "";
  toast(`Training export created with ${data.export?.sample_count || 0} samples.`);
  await loadExports();
}

async function createHandoff(exportId) {
  const data = await api("/api/handoffs", { method: "POST", body: JSON.stringify({ export_id: exportId }) });
  $("handoffCode").textContent = data.code;
  $("handoff").classList.remove("hidden");
  toast("One-time code created. It expires in five minutes.");
}

$("authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ totp_code: $("totpCode").value.trim() }) });
    state.token = data.token || "";
    $("totpCode").value = "";
    $("authError").textContent = "";
    setLocked(false);
    await refresh();
  } catch (error) { $("authError").textContent = error.message; }
});
$("logout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); state.token = ""; setLocked(true); });
$("refresh").addEventListener("click", () => refresh().catch((error) => toast(error.message)));
$("settings").addEventListener("submit", (event) => saveSettings(event).catch((error) => toast(error.message)));
$("collect").addEventListener("click", () => begin("collect").catch((error) => toast(error.message)));
$("cancelRun").addEventListener("click", () => cancelRun().catch((error) => toast(error.message)));
$("rebuild").addEventListener("click", () => begin("rebuild").catch((error) => toast(error.message)));
$("exportForm").addEventListener("submit", (event) => createExport(event).catch((error) => toast(error.message)));
$("reloadExports").addEventListener("click", () => loadExports().catch((error) => toast(error.message)));
checkAuth().then((authenticated) => authenticated && refresh()).catch((error) => { $("authError").textContent = error.message; });
setInterval(() => { if (!document.body.classList.contains("locked")) refresh().catch(() => {}); }, 8000);
