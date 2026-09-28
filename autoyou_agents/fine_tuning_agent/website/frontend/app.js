// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-983faa71c025fde2f0afbd31

const state = {
  datasets: [],
  dataCollector: null,
  dumpJobs: [],
  jobs: [],
  selectedDatasetId: "",
  selectedJobId: "",
  pollTimer: null,
  pollInFlight: false,
};

const ACTIVE_POLL_INTERVAL_MS = 15000;

const $ = (id) => document.getElementById(id);
const baseUrl = new URL(".", window.location.href);

function isActiveJobStatus(status) {
  const normalized = String(status || "").toLowerCase();
  return normalized === "queued" || normalized === "running";
}

function hasActiveWork() {
  return state.dumpJobs.some((job) => isActiveJobStatus(job.status))
    || state.jobs.some((job) => isActiveJobStatus(job.status));
}

function stopPolling() {
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function syncPolling(delayMs = ACTIVE_POLL_INTERVAL_MS) {
  if (state.pollInFlight) return;
  stopPolling();
  if (document.hidden || !hasActiveWork()) return;
  state.pollTimer = setTimeout(() => {
    pollWhileActive().catch(() => {});
  }, delayMs);
}

async function pollWhileActive() {
  if (state.pollInFlight) return;
  if (document.hidden || !hasActiveWork()) {
    stopPolling();
    return;
  }
  state.pollInFlight = true;
  try {
    const unlocked = await checkAuth();
    if (!unlocked) {
      stopPolling();
      return;
    }
    await Promise.all([loadStatus(), loadDumpJobs(), loadJobs()]);
  } finally {
    state.pollInFlight = false;
    syncPolling();
  }
}

function handleVisibilityChange() {
  if (document.hidden) {
    stopPolling();
    return;
  }
  if (hasActiveWork()) {
    pollWhileActive().catch(() => {});
  }
}

function api(path, options = {}) {
  const target = new URL(String(path || "").replace(/^\//, ""), baseUrl).toString();
  const headers = options.headers || {};
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  return fetch(target, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers,
  }).then(async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) {
      throw new Error(data.error || data.message || `Request failed with ${response.status}`);
    }
    return data;
  });
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.add("hidden"), 4200);
}

function pill(status) {
  const normalized = String(status || "unknown").toLowerCase();
  const cls = ["completed", "success", "installed", "ok"].includes(normalized)
    ? "success"
    : ["failed", "error", "interrupted"].includes(normalized)
      ? "error"
      : ["running", "queued"].includes(normalized)
        ? "warning"
        : "neutral";
  return `<span class="pill ${cls}">${escapeHtml(status || "unknown")}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const idx = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / Math.pow(1024, idx)).toFixed(idx ? 1 : 0)} ${units[idx]}`;
}

function formatEta(seconds) {
  const n = Math.max(0, Number(seconds || 0));
  if (n < 60) return `${Math.round(n)}s`;
  if (n < 3600) return `${Math.round(n / 60)}m`;
  return `${(n / 3600).toFixed(1)}h`;
}

async function checkAuth() {
  const data = await api("/api/auth/status");
  const unlocked = Boolean(data.authenticated || data.auth_mode === "open");
  $("authPill").textContent = data.authenticated ? "Authenticated" : "Locked";
  $("authPill").className = `pill ${data.authenticated ? "success" : "warning"}`;
  document.body.classList.toggle("locked", !unlocked);
  $("authModal").classList.toggle("hidden", unlocked);
  $("app-shell").classList.toggle("hidden", !unlocked);
  $("authModal").inert = unlocked;
  $("app-shell").inert = !unlocked;
  return unlocked;
}

async function loadStatus() {
  const data = await api("/api/status");
  $("workspaceText").textContent = data.workspace || "Workspace ready";
  const retention = data.retention || {};
  const diskLabel = `${formatBytes(data.disk?.free_bytes)} free`;
  $("diskText").textContent = retention.low_disk
    ? `${diskLabel}; cleanup keeps ${retention.max_runs || 0} recent runs`
    : diskLabel;

  const wa = data.whatsapp || {};
  $("whatsappText").textContent = wa.available ? `${wa.message_count || 0} captured messages` : "Unavailable";
  $("whatsappDetail").textContent = wa.available ? JSON.stringify(wa.status || {}) : wa.message || "No runtime link";

  const telegramUser = data.telegram_user || {};
  $("telegramUserText").textContent = telegramUser.available
    ? `${telegramUser.message_count || 0} saved messages`
    : "Unavailable";
  $("telegramUserDetail").textContent = telegramUser.available
    ? "Owner-only Saved Messages; training consent is active."
    : telegramUser.message || "Consent is required.";
  $("telegramUserSupportText").textContent = telegramUser.available
    ? "Saved Messages only; ready"
    : telegramUser.message || "Consent is required.";
  $("pullTelegramUserBtn").disabled = !telegramUser.available;

  const dump = data.whatsapp_dump || {};
  $("dumpSupportText").textContent = dump.ready
    ? "Ready: full available history, time-bounded"
    : dump.message || "History dump unavailable";

  const ollama = data.ollama || {};
  $("ollamaText").textContent = ollama.status === "success" ? `${ollama.models?.length || 0} models` : "Unavailable";
  $("ollamaDetail").textContent = ollama.message || (ollama.models || []).slice(0, 4).join(", ") || "No models returned";

  const trainer = data.training_capability || {};
  const profile = data.hardware_profile || {};
  $("trainerText").textContent = trainer.real_training_ready ? "CUDA ready" : "Preparation mode";
  $("trainerDetail").textContent = profile.message || trainer.message || "Trainer capability unknown";

  $("activeJobText").textContent = data.active_job_id || "None";
  $("activeJobDetail").textContent = data.active_job_id
    ? "Trainer is active"
    : data.active_dump_job_id
      ? `Dump ${data.active_dump_job_id} is active`
      : "No trainer is running";

  $("trainingModelId").value ||= data.defaults?.training_model_id || "";
  $("ollamaBaseModel").value ||= data.defaults?.ollama_base_model || "ministral-3:8b";
  $("maxSteps").value ||= data.defaults?.max_steps || 80;
  if (!$("prepareOnly").dataset.initialized) {
    $("prepareOnly").checked = Boolean(data.defaults?.prepare_only);
    $("prepareOnly").dataset.initialized = "true";
  }
}

async function loadDatasets() {
  const data = await api("/api/datasets");
  state.datasets = data.datasets || [];
  renderDatasets();
}

async function loadDataCollectorStatus() {
  const url = $("collectorUrl")?.value.trim();
  const data = await api(`/api/datacollector/status${url ? `?url=${encodeURIComponent(url)}` : ""}`);
  state.dataCollector = data;
  renderDataCollectorStatus();
}

function renderDatasets() {
  const list = $("datasetList");
  const select = $("datasetSelect");
  select.innerHTML = "";
  if (!state.datasets.length) {
    list.className = "list empty";
    list.textContent = "No datasets prepared yet.";
    select.innerHTML = '<option value="">No datasets available</option>';
    return;
  }
  list.className = "list";
  list.innerHTML = state.datasets.map((dataset) => `
    <div class="item ${dataset.id === state.selectedDatasetId ? "selected" : ""}" data-dataset-id="${escapeHtml(dataset.id)}">
      <div class="item-head">
        <div>
          <div class="item-title">${escapeHtml(dataset.title)}</div>
          <div class="item-meta">
            <span>${escapeHtml(dataset.source_type)}</span>
            <span>${dataset.sample_count} samples</span>
            <span>${dataset.message_count} messages</span>
          </div>
        </div>
        <button class="secondary-button select-dataset" type="button" data-dataset-id="${escapeHtml(dataset.id)}">Select</button>
      </div>
      ${(dataset.warnings || []).length ? `<div class="item-meta">${escapeHtml((dataset.warnings || []).join(" "))}</div>` : ""}
    </div>
  `).join("");
  select.innerHTML = state.datasets.map((dataset) => `
    <option value="${escapeHtml(dataset.id)}">${escapeHtml(dataset.title)} (${dataset.sample_count} samples)</option>
  `).join("");
  if (!state.selectedDatasetId && state.datasets[0]) {
    state.selectedDatasetId = state.datasets[0].id;
  }
  select.value = state.selectedDatasetId || state.datasets[0]?.id || "";
  document.querySelectorAll(".select-dataset").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedDatasetId = button.dataset.datasetId;
      $("datasetSelect").value = state.selectedDatasetId;
      renderDatasets();
    });
  });
}

function renderDataCollectorStatus() {
  const data = state.dataCollector || {};
  $("dataCollectorStatus").textContent = data.available ? "Running locally" : "Not detected";
  $("dataCollectorDetail").textContent = data.message || "Looking only on this computer.";
}

async function loadDumpJobs() {
  const hadActive = state.dumpJobs.some((job) => isActiveJobStatus(job.status));
  const data = await api("/api/dump-jobs");
  state.dumpJobs = data.jobs || [];
  renderDumpJobs();
  const hasActive = state.dumpJobs.some((job) => isActiveJobStatus(job.status));
  if (hadActive && !hasActive) {
    loadDatasets().catch(() => {});
  }
  syncPolling();
}

function renderDumpJobs() {
  const list = $("dumpJobList");
  if (!state.dumpJobs.length) {
    list.className = "list empty";
    list.textContent = "No history dumps loaded.";
    return;
  }
  list.className = "list";
  list.innerHTML = state.dumpJobs.slice(0, 5).map((job) => `
    <div class="item">
      <div class="item-head">
        <div>
          <div class="item-title">${escapeHtml(job.title || job.id)}</div>
          <div class="item-meta">
            <span>${escapeHtml(job.config?.chat_scope || "personal")}</span>
            <span>${job.chat_count || 0} chats</span>
            <span>${job.message_count || 0} messages</span>
            ${job.dataset_id ? `<span>dataset ${escapeHtml(job.dataset_id)}</span>` : ""}
          </div>
        </div>
        ${pill(job.status)}
      </div>
      <div class="progress" aria-label="Dump progress"><span style="width:${Number(job.progress_percent || 0)}%"></span></div>
      <div class="item-meta">
        <span>${Number(job.progress_percent || 0).toFixed(1)}%</span>
        <span>ETA ${formatEta(job.eta_seconds || 0)}</span>
        ${job.error_message ? `<span>${escapeHtml(job.error_message)}</span>` : ""}
      </div>
    </div>
  `).join("");
}

async function loadJobs() {
  const data = await api("/api/jobs");
  state.jobs = data.jobs || [];
  renderJobs();
  syncPolling();
}

function renderJobs() {
  const list = $("jobList");
  if (!state.jobs.length) {
    list.className = "list empty";
    list.textContent = "No training jobs yet.";
    return;
  }
  list.className = "list";
  list.innerHTML = state.jobs.map((job) => {
    const progress = Math.max(0, Math.min(100, Number(job.progress_percent || 0)));
    return `
      <div class="item ${job.id === state.selectedJobId ? "selected" : ""}" data-job-id="${escapeHtml(job.id)}">
        <div class="item-head">
          <div>
            <div class="item-title">${escapeHtml(job.title)}</div>
            <div class="item-meta">
              <span>${escapeHtml(job.model_name)}</span>
              <span>ETA ${formatEta(job.eta_seconds)}</span>
              <span>${Math.round(progress)}%</span>
            </div>
          </div>
          ${pill(job.status)}
        </div>
        <div class="progress"><span style="width:${progress}%"></span></div>
        <div class="item-actions">
          <button class="ghost-button view-log" type="button" data-job-id="${escapeHtml(job.id)}">Log</button>
          <button class="secondary-button install-job" type="button" data-job-id="${escapeHtml(job.id)}" ${job.status === "completed" ? "" : "disabled"}>Install</button>
          <button class="danger-button cancel-job" type="button" data-job-id="${escapeHtml(job.id)}" ${["queued", "running"].includes(job.status) ? "" : "disabled"}>${job.status === "cancelling" ? "Stopping..." : "Stop"}</button>
          <button class="danger-button delete-job" type="button" data-job-id="${escapeHtml(job.id)}" ${["queued", "running"].includes(job.status) ? "disabled" : ""}>Delete</button>
        </div>
      </div>
    `;
  }).join("");
  document.querySelectorAll(".view-log").forEach((button) => button.addEventListener("click", () => viewLog(button.dataset.jobId)));
  document.querySelectorAll(".install-job").forEach((button) => button.addEventListener("click", () => installJob(button.dataset.jobId)));
  document.querySelectorAll(".cancel-job").forEach((button) => button.addEventListener("click", () => cancelJob(button.dataset.jobId)));
  document.querySelectorAll(".delete-job").forEach((button) => button.addEventListener("click", () => deleteJob(button.dataset.jobId)));
}

async function viewLog(jobId) {
  state.selectedJobId = jobId;
  renderJobs();
  const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/log?lines=160`);
  $("logPane").textContent = data.log_tail || "No log output yet.";
}

async function loadModels() {
  const data = await api("/api/models");
  const list = $("modelList");
  const models = data.models || [];
  if (!models.length) {
    list.className = "model-list empty";
    list.textContent = data.message || "No local models returned.";
    return;
  }
  list.className = "model-list";
  list.innerHTML = models.map((name) => `
    <div class="item">
      <div class="item-head">
        <div class="item-title">${escapeHtml(name)}</div>
        <button class="danger-button delete-model" type="button" data-model-name="${escapeHtml(name)}">Remove</button>
      </div>
    </div>
  `).join("");
  document.querySelectorAll(".delete-model").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!confirm(`Remove local model ${button.dataset.modelName}?`)) return;
      await api(`/api/models/${encodeURIComponent(button.dataset.modelName)}`, { method: "DELETE" });
      toast("Model removed.");
      loadModels();
    });
  });
}

async function installJob(jobId) {
  const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/install`, { method: "POST" });
  toast(data.message || `Installed ${data.model_name}`);
  await Promise.all([loadJobs(), loadModels()]);
}

async function cancelJob(jobId) {
  if (!confirm("Stop this training job?")) return;
  const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST", body: JSON.stringify({}) });
  toast(data.message || "Cancellation requested.");
  await Promise.all([loadStatus(), loadJobs()]);
  if (state.selectedJobId === jobId) viewLog(jobId).catch(() => {});
}

async function deleteJob(jobId) {
  const deleteModel = confirm("Delete run files. Press OK to also try removing the local model, or Cancel to keep any installed model.");
  await api(`/api/jobs/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
    body: JSON.stringify({ delete_ollama_model: deleteModel }),
  });
  if (state.selectedJobId === jobId) {
    state.selectedJobId = "";
    $("logPane").textContent = "Select a job to view logs.";
  }
  toast("Training run deleted.");
  await Promise.all([loadJobs(), loadModels()]);
}

async function cleanupJobs() {
  const data = await api("/api/jobs/cleanup", { method: "POST", body: JSON.stringify({}) });
  toast(`Cleaned ${data.deleted_count || 0} run(s), freed ${formatBytes(data.freed_bytes || 0)}.`);
  await Promise.all([loadStatus(), loadJobs(), loadModels()]);
}

async function pullWhatsapp() {
  const data = await api("/api/datasets/whatsapp", {
    method: "POST",
    body: JSON.stringify({ title: "Connected message log" }),
  });
  const dataset = data.dataset || {};
  state.selectedDatasetId = dataset.id || state.selectedDatasetId;
  toast(`Live log dataset ready: ${dataset.sample_count || 0} samples`);
  await loadDatasets();
}

async function pullTelegramUser() {
  const data = await api("/api/datasets/telegram-user", {
    method: "POST",
    body: JSON.stringify({ title: "Telegram Saved Messages live log" }),
  });
  const dataset = data.dataset || {};
  state.selectedDatasetId = dataset.id || state.selectedDatasetId;
  toast(`Saved Messages dataset ready: ${dataset.sample_count || 0} samples`);
  await loadDatasets();
}

function selectedTimelineMode() {
  const checked = document.querySelector('input[name="timelineMode"]:checked');
  return checked ? checked.value : "all_time";
}

function validateTimeline() {
  const errorEl = $("timelineError");
  const setError = (message) => {
    errorEl.textContent = message || "";
    errorEl.hidden = !message;
    return !message;
  };
  if (selectedTimelineMode() !== "range") {
    return setError("");
  }
  const earliest = $("timelineEarliest").value;
  const latest = $("timelineLatest").value;
  if (earliest && latest && earliest > latest) {
    return setError("Earliest date must be on or before the latest date.");
  }
  return setError("");
}

async function startWhatsappDump(event) {
  event.preventDefault();
  if (!validateTimeline()) {
    toast("Fix the timeline range before extracting.");
    return;
  }
  const includePersonal = $("includePersonal").checked;
  const includeGroups = $("includeGroups").checked;
  if (!includePersonal && !includeGroups) {
    toast("Select personal messages, groups, or both.");
    return;
  }
  const rangeMode = selectedTimelineMode() === "range";
  const payload = {
    include_personal: includePersonal,
    include_groups: includeGroups,
    timeline_mode: rangeMode ? "range" : "all_time",
    earliest: rangeMode ? ($("timelineEarliest").value || "") : "",
    latest: rangeMode ? ($("timelineLatest").value || "") : "",
    all_available_history: true,
    title: $("dumpTitle").value.trim(),
    me_name: $("dumpMeName").value.trim(),
    pause_runtime_bridge: $("pauseBridgeDuringDump").checked,
  };
  const data = await api("/api/datasets/whatsapp-dump", { method: "POST", body: JSON.stringify(payload) });
  toast(`Extraction started: ${data.job?.id || ""}`.trim());
  await Promise.all([loadStatus(), loadDumpJobs()]);
}

async function uploadDataset(event) {
  event.preventDefault();
  const file = $("datasetFile").files[0];
  if (!file) {
    toast("Choose a dataset file first.");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  form.append("me_name", $("meName").value.trim());
  form.append("title", $("datasetTitle").value.trim());
  const data = await api("/api/datasets/upload", { method: "POST", body: form });
  const dataset = data.dataset || {};
  state.selectedDatasetId = dataset.id || state.selectedDatasetId;
  $("uploadForm").reset();
  toast(`Dataset ready: ${dataset.sample_count || 0} samples`);
  await loadDatasets();
}

async function importDataCollectorHandoff(event) {
  event.preventDefault();
  const code = $("collectorHandoffCode").value.trim();
  if (!code) {
    toast("Create a one-time handoff code in Data Collector first.");
    return;
  }
  const data = await api("/api/datasets/datacollector/handoff", {
    method: "POST",
    body: JSON.stringify({
      handoff_code: code,
      collector_url: $("collectorUrl").value.trim(),
      title: $("datacollectorDatasetTitle").value.trim(),
    }),
  });
  const dataset = data.dataset || {};
  state.selectedDatasetId = dataset.id || state.selectedDatasetId;
  $("collectorHandoffCode").value = "";
  toast(`Data Collector dataset ready: ${dataset.sample_count || 0} samples`);
  await Promise.all([loadDatasets(), loadDataCollectorStatus()]);
}

async function importFolderDataset(event) {
  event.preventDefault();
  const data = await api("/api/datasets/folder", {
    method: "POST",
    body: JSON.stringify({
      folder_path: $("folderPath").value.trim(),
      me_name: $("folderMeName").value.trim(),
      title: $("folderTitle").value.trim(),
    }),
  });
  const dataset = data.dataset || {};
  state.selectedDatasetId = dataset.id || state.selectedDatasetId;
  toast(`Folder dataset ready: ${dataset.sample_count || 0} samples`);
  await loadDatasets();
}

async function startTraining(event) {
  event.preventDefault();
  const datasetId = $("datasetSelect").value || state.selectedDatasetId;
  if (!datasetId) {
    toast("Select a dataset first.");
    return;
  }
  const payload = {
    dataset_id: datasetId,
    model_name: $("modelName").value.trim(),
    training_model_id: $("trainingModelId").value.trim(),
    ollama_base_model: $("ollamaBaseModel").value.trim(),
    max_steps: Number($("maxSteps").value || 80),
    epochs: Number($("epochs").value || 1),
    prepare_only: $("prepareOnly").checked,
    allow_cpu_training: $("allowCpuTraining").checked,
  };
  const data = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
  state.selectedJobId = data.job?.id || "";
  toast("Training job started.");
  await Promise.all([loadStatus(), loadJobs()]);
  if (state.selectedJobId) {
    viewLog(state.selectedJobId);
  }
}

async function refreshAll() {
  const unlocked = await checkAuth();
  if (!unlocked) {
    stopPolling();
    return;
  }
  await Promise.all([loadStatus(), loadDatasets(), loadDataCollectorStatus(), loadDumpJobs(), loadJobs(), loadModels()]);
  syncPolling();
}

function bindEvents() {
  document.addEventListener("visibilitychange", handleVisibilityChange);
  $("authForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("authError").textContent = "";
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ totp_code: $("totpCode").value.trim() }),
      });
      document.body.classList.remove("locked");
      $("authModal").classList.add("hidden");
      $("app-shell").classList.remove("hidden");
      $("authModal").inert = true;
      $("app-shell").inert = false;
      toast("Unlocked.");
      await refreshAll();
    } catch (error) {
      $("authError").textContent = error.message;
    }
  });
  $("logoutBtn").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    location.reload();
  });
  $("refreshBtn").addEventListener("click", refreshAll);
  $("reloadDatasetsBtn").addEventListener("click", loadDatasets);
  $("reloadDataCollectorBtn").addEventListener("click", loadDataCollectorStatus);
  $("reloadJobsBtn").addEventListener("click", loadJobs);
  $("cleanupJobsBtn").addEventListener("click", () => cleanupJobs().catch((error) => toast(error.message)));
  $("reloadModelsBtn").addEventListener("click", loadModels);
  $("pullWhatsappBtn").addEventListener("click", () => pullWhatsapp().catch((error) => toast(error.message)));
  $("pullTelegramUserBtn").addEventListener("click", () => pullTelegramUser().catch((error) => toast(error.message)));
  $("dumpForm").addEventListener("submit", (event) => startWhatsappDump(event).catch((error) => toast(error.message)));
  // Timeline: show/hide the custom range and keep From<=To with live boundaries.
  const syncTimelineUi = () => {
    const rangeMode = selectedTimelineMode() === "range";
    $("timelineRange").hidden = !rangeMode;
    if (!rangeMode) {
      $("timelineError").hidden = true;
    }
    validateTimeline();
  };
  document.querySelectorAll('input[name="timelineMode"]').forEach((radio) => {
    radio.addEventListener("change", syncTimelineUi);
  });
  $("timelineEarliest").addEventListener("change", () => {
    // 'Latest' cannot precede 'Earliest'.
    $("timelineLatest").min = $("timelineEarliest").value || "";
    validateTimeline();
  });
  $("timelineLatest").addEventListener("change", () => {
    // 'Earliest' cannot exceed 'Latest'.
    $("timelineEarliest").max = $("timelineLatest").value || "";
    validateTimeline();
  });
  syncTimelineUi();
  $("uploadForm").addEventListener("submit", (event) => uploadDataset(event).catch((error) => toast(error.message)));
  $("folderForm").addEventListener("submit", (event) => importFolderDataset(event).catch((error) => toast(error.message)));
  $("dataCollectorHandoffForm").addEventListener("submit", (event) => importDataCollectorHandoff(event).catch((error) => toast(error.message)));
  $("trainForm").addEventListener("submit", (event) => startTraining(event).catch((error) => toast(error.message)));
  $("datasetSelect").addEventListener("change", (event) => {
    state.selectedDatasetId = event.target.value;
    renderDatasets();
  });
}

bindEvents();
refreshAll().catch((error) => toast(error.message));
