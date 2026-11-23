// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-S-btc-32981775f82f1d852509695b

document.addEventListener("DOMContentLoaded", () => {
  const API_BASE = "/agent/media_generation_agent";

  // Navigation elements
  const navItems = document.querySelectorAll(".nav-item[data-target]");
  const viewSections = document.querySelectorAll(".view-section");

  // Quick Studio elements
  const generationForm = document.getElementById("generation-form");
  const promptInput = document.getElementById("prompt");
  const mediaTypeSelect = document.getElementById("media-type");
  const modelTypeSelect = document.getElementById("model-type");
  const resolutionSelect = document.getElementById("resolution");
  const numStepsInput = document.getElementById("num-steps");
  const videoDurationInput = document.getElementById("video-duration-sec");
  const videoLengthInput = document.getElementById("video-length");
  const durationGroup = document.getElementById("duration-group");
  const framesGroup = document.getElementById("frames-group");
  const seedInput = document.getElementById("seed");
  const quickNegPromptInput = document.getElementById("quick-negative-prompt");
  const enhanceBtn = document.getElementById("enhance-prompt-btn");
  const optimizedBox = document.getElementById("optimized-prompt-box");
  const optimizedText = document.getElementById("optimized-prompt-text");
  const quickDropzone = document.getElementById("quick-dropzone");
  const quickFileInput = document.getElementById("quick-file-input");
  const quickBrowseBtn = document.getElementById("quick-browse-btn");
  const quickAttachmentChips = document.getElementById("quick-attachment-chips");
  const jumpToFactoryBtn = document.getElementById("jump-to-factory-btn");

  // Status Sidebar elements
  const statusDot = document.getElementById("status-dot");
  const statusLabel = document.getElementById("status-label");
  const statusDetail = document.getElementById("status-detail");
  const sidebarLlmBadge = document.getElementById("sidebar-llm-badge");
  const sidebarHwPill = document.getElementById("sidebar-hw-pill");

  // Active Jobs & Gallery elements
  const activeJobsList = document.getElementById("active-jobs-list");
  const galleryGrid = document.getElementById("gallery-grid-container");
  const galleryEmpty = document.getElementById("gallery-empty");
  const filterTabs = document.querySelectorAll(".filter-tab");

  // Long-Video Factory elements
  const factoryNewToggleBtn = document.getElementById("factory-new-toggle-btn");
  const factoryNewCard = document.getElementById("factory-new-card");
  const factoryCancelCreate = document.getElementById("factory-cancel-create");
  const factoryCreateForm = document.getElementById("factory-create-form");
  const factoryTitleInput = document.getElementById("factory-title");
  const factoryAspectSelect = document.getElementById("factory-aspect");
  const factoryBriefInput = document.getElementById("factory-brief");
  const factoryDurationPresets = document.getElementById("factory-duration-presets");
  const factoryDurationInput = document.getElementById("factory-duration-seconds");
  const factorySceneSecondsInput = document.getElementById("factory-scene-seconds");
  const factoryLlmSelect = document.getElementById("factory-llm-select");
  const factoryPlanNowCheck = document.getElementById("factory-plan-now");
  const factoryUseKeyframesCheck = document.getElementById("factory-use-keyframes");
  const factoryDropzone = document.getElementById("factory-dropzone");
  const factoryFileInput = document.getElementById("factory-file-input");
  const factoryBrowseBtn = document.getElementById("factory-browse-btn");
  const factoryAttachmentChips = document.getElementById("factory-attachment-chips");
  const factoryProjectsList = document.getElementById("factory-projects-list");
  const factoryEmptyWorkspace = document.getElementById("factory-empty-workspace");
  const factoryProjectDetail = document.getElementById("factory-project-detail");
  const projDetailTitle = document.getElementById("proj-detail-title");
  const projDetailMeta = document.getElementById("proj-detail-meta");
  const projStatusPill = document.getElementById("proj-status-pill");
  const projDeleteBtn = document.getElementById("proj-delete-btn");
  const projStageStepper = document.getElementById("proj-stage-stepper");
  const projProgressFill = document.getElementById("proj-progress-fill");
  const projProgressMsg = document.getElementById("proj-progress-msg");
  const projProgressPct = document.getElementById("proj-progress-pct");
  const projMasterBox = document.getElementById("proj-master-box");
  const projMasterVideo = document.getElementById("proj-master-video");
  const projMasterDownload = document.getElementById("proj-master-download");
  const projStyleBible = document.getElementById("proj-style-bible");
  const projScriptText = document.getElementById("proj-script-text");
  const projSaveScriptBtn = document.getElementById("proj-save-script-btn");
  const projSceneCount = document.getElementById("proj-scene-count");
  const projScenesList = document.getElementById("proj-scenes-list");
  const projAddSceneBtn = document.getElementById("proj-add-scene-btn");
  const projStageLogs = document.getElementById("proj-stage-logs");

  // Image Studio & Editor elements
  const editorDropzone = document.getElementById("editor-dropzone");
  const editorFileInput = document.getElementById("editor-file-input");
  const editorBrowseBtn = document.getElementById("editor-browse-btn");
  const editorGalleryPicker = document.getElementById("editor-gallery-picker");
  const imageEditForm = document.getElementById("image-edit-form");
  const editorInstruction = document.getElementById("editor-instruction");
  const editorEnhanceBtn = document.getElementById("editor-enhance-btn");
  const editorModelSelect = document.getElementById("editor-model");
  const editorStrengthInput = document.getElementById("editor-strength");
  const editorStrengthVal = document.getElementById("editor-strength-val");
  const editorStepsInput = document.getElementById("editor-steps");
  const editorSeedInput = document.getElementById("editor-seed");
  const editorSubmitBtn = document.getElementById("editor-submit-btn");
  const editorOriginalPreview = document.getElementById("editor-original-preview");
  const editorResultPreview = document.getElementById("editor-result-preview");
  const editorStatusText = document.getElementById("editor-status-text");
  const editorResultActions = document.getElementById("editor-result-actions");
  const editorAnimateBtn = document.getElementById("editor-animate-btn");
  const editorUseAsSourceBtn = document.getElementById("editor-use-as-source-btn");

  // Environment / Hardware / Settings elements
  const envPlatformLine = document.getElementById("env-platform-line");
  const envGuidanceLine = document.getElementById("env-guidance-line");
  const envVerifyBtn = document.getElementById("env-verify-btn");
  const envDetectBtn = document.getElementById("env-detect-btn");
  const envInstallBtn = document.getElementById("env-install-btn");
  const envActionResult = document.getElementById("env-action-result");
  const envInstallLog = document.getElementById("env-install-log");
  const engineMatrix = document.getElementById("engine-matrix");
  const verificationList = document.getElementById("verification-list");
  const hwGpuVal = document.getElementById("hw-gpu-val");
  const hwVramVal = document.getElementById("hw-vram-val");
  const hwRamVal = document.getElementById("hw-ram-val");
  const hwProfileVal = document.getElementById("hw-profile-val");
  const hwFfmpegVal = document.getElementById("hw-ffmpeg-val");

  const settingsForm = document.getElementById("settings-form");
  const settingsEngine = document.getElementById("settings-engine");
  const wan2gpPathFields = document.getElementById("wan2gp-path-fields");
  const wan2gpModelSettings = document.getElementById("wan2gp-model-settings");
  const drawThingsSettings = document.getElementById("draw-things-settings");
  const comfyuiSettings = document.getElementById("comfyui-settings");
  const mlxSettings = document.getElementById("mlx-settings");
  const sdcppSettings = document.getElementById("sdcpp-settings");
  const settingsRoot = document.getElementById("settings-root");
  const settingsApp = document.getElementById("settings-app");
  const settingsPython = document.getElementById("settings-python");
  const settingsModel = document.getElementById("settings-model");
  const settingsImageModel = document.getElementById("settings-image-model");
  const settingsEditModel = document.getElementById("settings-edit-model");
  const settingsMemoryProfile = document.getElementById("settings-memory-profile");
  const settingsResolution = document.getElementById("settings-resolution");
  const settingsImageResolution = document.getElementById("settings-image-resolution");
  const settingsSteps = document.getElementById("settings-steps");
  const settingsImageSteps = document.getElementById("settings-image-steps");
  const settingsLength = document.getElementById("settings-length");
  const settingsSceneDuration = document.getElementById("settings-scene-duration");
  const settingsFps = document.getElementById("settings-fps");
  const settingsLlmProvider = document.getElementById("settings-llm-provider");
  const settingsLlmModel = document.getElementById("settings-llm-model");
  const settingsEnhance = document.getElementById("settings-enhance");
  const settingsContinuityChain = document.getElementById("settings-continuity-chain");
  const settingsReleaseOllamaVram = document.getElementById("settings-release-ollama-vram");
  const settingsDrawThingsCLI = document.getElementById("settings-draw-things-cli");
  const settingsDrawThingsModels = document.getElementById("settings-draw-things-models");
  const settingsDrawThingsImageModel = document.getElementById("settings-draw-things-image-model");
  const settingsDrawThingsVideoModel = document.getElementById("settings-draw-things-video-model");
  const settingsComfyuiUrl = document.getElementById("settings-comfyui-url");
  const settingsComfyuiDir = document.getElementById("settings-comfyui-dir");
  const settingsMfluxCli = document.getElementById("settings-mflux-cli");
  const settingsMlxImageModel = document.getElementById("settings-mlx-image-model");
  const settingsMlxVideoModel = document.getElementById("settings-mlx-video-model");
  const settingsSdcppCli = document.getElementById("settings-sdcpp-cli");
  const settingsSdcppModels = document.getElementById("settings-sdcpp-models");

  // Modal & Auth elements
  const mediaModal = document.getElementById("media-modal");
  const modalCloseBtn = document.getElementById("modal-close-btn");
  const modalMediaContent = document.getElementById("modal-media-content");
  const modalPrompt = document.getElementById("modal-prompt");
  const modalMeta = document.getElementById("modal-meta");
  const modalActions = document.getElementById("modal-actions");
  const loginGate = document.getElementById("login-gate");
  const mainLayout = document.getElementById("main-layout");
  const otpInput = document.getElementById("otp-input");
  const otpSubmitBtn = document.getElementById("otp-submit-btn");
  const otpError = document.getElementById("otp-error");
  const logoutBtn = document.getElementById("logout-btn");

  // State Management
  let currentView = "dashboard-view";
  let galleryFilter = "all";
  let configData = {};
  let engineReady = false;
  let pollingInterval = null;
  let factoryPollInterval = null;
  let envInstallPollTimer = null;
  let activeInstallEngine = "wan2gp";
  let currentOptimizedPrompt = "";

  // Multimodal attachment slots
  let quickAttachments = []; // [{ref, kind, filename, url}]
  let factoryAttachments = [];
  let selectedProjectId = null;
  let selectedProjectData = null;
  let editorSource = null; // {ref, url, filename}
  let editorActiveJobId = null;
  let editorLastResult = null; // {ref, url, item_id}
  let editorPollTimer = null;

  const ENGINE_LABELS = {
    wan2gp: "Wan2GP",
    comfyui: "ComfyUI",
    mlx: "Apple MLX",
    draw_things: "Draw Things",
    sdcpp: "stable-diffusion.cpp",
  };

  function proxyRootPrefix() {
    const pathname = String((window.location && window.location.pathname) || "");
    const marker = "/agent/";
    const index = pathname.indexOf(marker);
    return index > 0 ? pathname.slice(0, index) : "";
  }

  function adminLoginUrl() {
    const pathname = String((window.location && window.location.pathname) || "");
    const routePath = proxyRootPrefix() + "/agent/admin_agent/login";
    if (pathname.indexOf("/agent/") >= 0 && window.location && window.location.origin) {
      return window.location.origin + routePath;
    }
    return routePath;
  }

  function hydrateAdminLoginLink() {
    const link = document.getElementById("admin-login-link");
    if (link) link.href = adminLoginUrl();
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function optionExists(select, value) {
    return Array.from(select.options).some(option => option.value === value);
  }

  function setSelectIfPossible(select, value) {
    if (!select || !value) return false;
    if (!optionExists(select, value)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.append(option);
    }
    select.value = value;
    return true;
  }

  function usableOptimizedPrompt() {
    const text = currentOptimizedPrompt.trim();
    if (!text) return "";
    if (text.startsWith("Generating optimized")) return "";
    if (text.startsWith("AI Prompt Enhancement failed")) return "";
    return text;
  }

  function setNumberInput(input, value, fallback) {
    if (!input) return;
    const parsed = parseInt(value, 10);
    input.value = Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  }

  function secondsToFrames(seconds, fps) {
    const raw = Math.max(9, Math.round(Number(seconds || 2) * Number(fps || 24)) + 1);
    const aligned = Math.round((raw - 1) / 4) * 4 + 1;
    return Math.max(9, Math.min(481, aligned));
  }

  function formatDuration(seconds) {
    const s = Math.max(0, Math.round(Number(seconds || 0)));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    if (m < 60) return rem ? `${m}m ${rem}s` : `${m}m`;
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return rm ? `${h}h ${rm}m` : `${h}h`;
  }

  function applyGenerationDefaultsForMediaType() {
    const engine = configData.engine || "wan2gp";
    const drawThings = engine === "draw_things";
    const isMlx = engine === "mlx";
    if (mediaTypeSelect.value === "image") {
      const model = drawThings
        ? (configData.draw_things_image_model || "flux_2_klein_4b_q6p.ckpt")
        : (isMlx ? (configData.mlx_image_model || "schnell") : (configData.image_model_type || "flux_schnell"));
      modelTypeSelect.value = model;
      setSelectIfPossible(modelTypeSelect, model);
      setSelectIfPossible(
        resolutionSelect,
        drawThings ? (configData.draw_things_image_resolution || "768x512") : (configData.image_resolution || "1280x720")
      );
      setNumberInput(numStepsInput, configData.image_num_inference_steps, 10);
      framesGroup.classList.add("hidden");
      if (durationGroup) durationGroup.classList.add("hidden");
      return;
    }

    const model = drawThings
      ? (configData.draw_things_video_model || "")
      : (isMlx ? (configData.mlx_video_model || "Lightricks/LTX-2") : (configData.model_type || "ltx2_distilled_gguf_q4_k_m"));
    modelTypeSelect.value = model;
    setSelectIfPossible(modelTypeSelect, model);
    setSelectIfPossible(
      resolutionSelect,
      drawThings ? (configData.draw_things_resolution || "768x512") : (configData.resolution || "416x240")
    );
    setNumberInput(numStepsInput, configData.num_inference_steps, 8);
    setNumberInput(videoLengthInput, configData.video_length, 49);
    if (videoDurationInput) {
      const fps = Number(configData.video_fps || 24);
      videoDurationInput.value = Math.max(1, Math.round(((Number(videoLengthInput.value) - 1) / fps) * 10) / 10);
    }
    framesGroup.classList.remove("hidden");
    if (durationGroup) durationGroup.classList.remove("hidden");
  }

  if (videoDurationInput && videoLengthInput) {
    videoDurationInput.addEventListener("input", () => {
      const fps = Number(configData.video_fps || 24);
      videoLengthInput.value = secondsToFrames(videoDurationInput.value, fps);
    });
  }

  // ── SESSION MANAGEMENT & AUTH HELPERS ──
  const SESSION_TOKEN_KEY = "autoyou_media_token";
  function _storedToken() { try { return localStorage.getItem(SESSION_TOKEN_KEY) || ""; } catch (_) { return ""; } }
  function _saveToken(t) { try { if (t) localStorage.setItem(SESSION_TOKEN_KEY, t); else localStorage.removeItem(SESSION_TOKEN_KEY); } catch (_) {} }
  function _clearToken() { _saveToken(""); }

  async function apiFetch(url, options = {}) {
    const token = _storedToken();
    const merged = Object.assign({}, options);
    const base = merged.headers instanceof Headers
      ? Object.fromEntries(merged.headers.entries())
      : Object.assign({}, merged.headers || {});
    if (token && !base["Authorization"]) {
      base["Authorization"] = "Bearer " + token;
    }
    merged.headers = base;

    const res = await fetch(url, merged);
    if (res.status === 401 && !url.includes("/api/auth/status") && !url.includes("/api/auth/login")) {
      _clearToken();
      checkAuthStatus();
      throw new Error("Session expired. Please log in again.");
    }
    return res;
  }

  function switchView(target) {
    if (!target) return;
    navItems.forEach(nav => nav.classList.toggle("active", nav.getAttribute("data-target") === target));
    viewSections.forEach(section => section.classList.toggle("active", section.id === target));
    currentView = target;

    if (target === "history-view") {
      loadCreationsGallery();
    } else if (target === "settings-view") {
      loadSettingsView();
      loadEnvironmentPanel();
    } else if (target === "factory-view") {
      loadFactoryProjects();
      loadLlmOptions();
    } else if (target === "editor-view") {
      populateEditorGalleryPicker();
    }
  }

  async function checkAuthStatus() {
    try {
      const res = await apiFetch(`${API_BASE}/api/auth/status`);
      const data = await res.json();
      if (data.success) {
        if (data.authenticated) {
          loginGate.classList.add("hidden");
          mainLayout.classList.remove("hidden");
          loginGate.inert = true;
          mainLayout.inert = false;

          checkSystemConnection();
          loadInstalledModels();
          pollActiveJobs();
          if (pollingInterval) clearInterval(pollingInterval);
          pollingInterval = setInterval(() => {
            if (!document.hidden) pollActiveJobs();
          }, 5000);
          return true;
        }
        loginGate.classList.remove("hidden");
        mainLayout.classList.add("hidden");
        loginGate.inert = false;
        mainLayout.inert = true;
        if (pollingInterval) {
          clearInterval(pollingInterval);
          pollingInterval = null;
        }
        const loginCopy = document.getElementById("login-copy");
        const otpRow = document.getElementById("otp-row");
        if (data.auth_mode === "open") {
          if (loginCopy) loginCopy.textContent = "This board is configured as open on localhost.";
          if (otpRow) otpRow.style.display = "none";
        } else if (data.totp_configured || data.totp_configured == null) {
          if (loginCopy) loginCopy.textContent = "Enter your 6-digit authenticator code to unlock the Media Factory Studio.";
          if (otpRow) otpRow.style.display = "";
        } else {
          if (loginCopy) loginCopy.textContent = "Sign in through the main AutoYou admin UI, then return here.";
          if (otpRow) otpRow.style.display = "none";
        }
        return false;
      }
    } catch (e) {
      console.error("Auth check failed", e);
    }
    return false;
  }

  async function handleLogin() {
    const code = otpInput.value.trim();
    if (!code || code.length < 6) return;
    otpSubmitBtn.disabled = true;
    otpError.classList.add("hidden");
    try {
      const res = await apiFetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ totp_code: code }),
      });
      const data = await res.json();
      if (data.success) {
        if (data.token) _saveToken(data.token);
        await checkAuthStatus();
      } else {
        otpError.textContent = data.error || "Authentication failed.";
        otpError.classList.remove("hidden");
        otpInput.value = "";
      }
    } catch (e) {
      otpError.textContent = "Connection error. Please try again.";
      otpError.classList.remove("hidden");
    } finally {
      otpSubmitBtn.disabled = false;
    }
  }

  async function handleLogout() {
    try {
      await apiFetch(`${API_BASE}/api/auth/logout`, { method: "POST" });
    } catch (e) {}
    _clearToken();
    location.reload();
  }

  if (otpSubmitBtn) otpSubmitBtn.addEventListener("click", handleLogin);
  if (otpInput) otpInput.addEventListener("keydown", e => { if (e.key === "Enter") handleLogin(); });
  if (logoutBtn) logoutBtn.addEventListener("click", handleLogout);
  hydrateAdminLoginLink();

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !mainLayout.classList.contains("hidden")) {
      pollActiveJobs();
      if (currentView === "factory-view" && selectedProjectId) {
        refreshSelectedProject();
      }
    }
  });

  // ── NAVIGATION ──
  navItems.forEach(item => {
    item.addEventListener("click", () => {
      const target = item.getAttribute("data-target");
      if (target && target !== currentView) switchView(target);
    });
  });

  if (jumpToFactoryBtn) {
    jumpToFactoryBtn.addEventListener("click", () => {
      switchView("factory-view");
      factoryNewCard.classList.remove("hidden");
    });
  }

  mediaTypeSelect.addEventListener("change", applyGenerationDefaultsForMediaType);

  // ── MULTIMODAL UPLOAD HELPER ──
  function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || "");
        const idx = result.indexOf(",");
        resolve(idx >= 0 ? result.slice(idx + 1) : result);
      };
      reader.onerror = () => reject(new Error("Could not read file."));
      reader.readAsDataURL(file);
    });
  }

  async function uploadMediaFile(file) {
    const b64 = await readFileAsBase64(file);
    const res = await apiFetch(`${API_BASE}/api/uploads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: file.name || "pasted.png",
        mimetype: file.type || "",
        data_base64: b64,
      }),
    });
    const data = await res.json();
    if (!data.success) throw new Error(data.error || "Upload failed");
    return data.upload;
  }

  function renderAttachmentChips(container, list, onRemove) {
    if (!container) return;
    container.innerHTML = "";
    list.forEach((item, idx) => {
      const chip = document.createElement("div");
      chip.className = "attachment-chip";
      const thumb = item.kind === "image"
        ? `<img src="${escapeHtml(item.url)}" alt="">`
        : `<span>${item.kind === "video" ? "🎬" : "🎵"}</span>`;
      chip.innerHTML = `${thumb}<span>${escapeHtml(item.filename)}</span><button type="button" class="attachment-chip-remove" aria-label="Remove">&times;</button>`;
      chip.querySelector(".attachment-chip-remove").addEventListener("click", () => onRemove(idx));
      container.appendChild(chip);
    });
  }

  function bindDropzone(dropzone, fileInput, browseBtn, onFiles) {
    if (!dropzone || !fileInput) return;
    if (browseBtn) browseBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      if (fileInput.files && fileInput.files.length) {
        onFiles(Array.from(fileInput.files));
        fileInput.value = "";
      }
    });
    ["dragenter", "dragover"].forEach(ev => {
      dropzone.addEventListener(ev, e => {
        e.preventDefault();
        dropzone.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach(ev => {
      dropzone.addEventListener(ev, e => {
        e.preventDefault();
        dropzone.classList.remove("drag-over");
      });
    });
    dropzone.addEventListener("drop", e => {
      const files = e.dataTransfer && e.dataTransfer.files ? Array.from(e.dataTransfer.files) : [];
      if (files.length) onFiles(files);
    });
  }

  async function addAttachmentsTo(targetList, chipsEl, files) {
    for (const file of files) {
      try {
        const up = await uploadMediaFile(file);
        // Replace existing attachment of the same kind in Quick Studio so refs stay crisp
        const existingIdx = targetList.findIndex(x => x.kind === up.kind);
        if (existingIdx >= 0) targetList[existingIdx] = up;
        else targetList.push(up);
      } catch (e) {
        alert(`Upload failed for ${file.name}: ${e.message}`);
      }
    }
    renderAttachmentChips(chipsEl, targetList, idx => {
      targetList.splice(idx, 1);
      renderAttachmentChips(chipsEl, targetList, () => {});
      addAttachmentsTo(targetList, chipsEl, []);
    });
  }

  bindDropzone(quickDropzone, quickFileInput, quickBrowseBtn, files => {
    addAttachmentsTo(quickAttachments, quickAttachmentChips, files);
  });
  bindDropzone(factoryDropzone, factoryFileInput, factoryBrowseBtn, files => {
    addAttachmentsTo(factoryAttachments, factoryAttachmentChips, files);
  });
  bindDropzone(editorDropzone, editorFileInput, editorBrowseBtn, async files => {
    const img = files.find(f => (f.type || "").startsWith("image/")) || files[0];
    if (!img) return;
    try {
      const up = await uploadMediaFile(img);
      setEditorSource(up.ref, up.url, up.filename);
    } catch (e) {
      alert(`Image upload failed: ${e.message}`);
    }
  });

  // Global clipboard image paste support for Quick Studio & Image Editor!
  document.addEventListener("paste", async e => {
    if (!e.clipboardData || !e.clipboardData.items) return;
    const files = [];
    for (const item of e.clipboardData.items) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (!files.length) return;
    if (currentView === "editor-view") {
      try {
        const up = await uploadMediaFile(files[0]);
        setEditorSource(up.ref, up.url, up.filename);
      } catch (err) {
        alert("Paste upload failed: " + err.message);
      }
    } else if (currentView === "dashboard-view") {
      addAttachmentsTo(quickAttachments, quickAttachmentChips, files);
    } else if (currentView === "factory-view" && !factoryNewCard.classList.contains("hidden")) {
      addAttachmentsTo(factoryAttachments, factoryAttachmentChips, files);
    }
  });

  // ── CORE LOADS & CONNECTION DIAGNOSTICS ──
  async function checkSystemConnection() {
    try {
      const res = await apiFetch(`${API_BASE}/api/config`);
      const data = await res.json();
      if (data.success) {
        configData = data.config || {};
        engineReady = data.connected === true;
        applyGenerationDefaultsForMediaType();

        if (sidebarLlmBadge && data.active_llm) {
          sidebarLlmBadge.textContent = `LLM: ${data.active_llm.provider}/${data.active_llm.model}`;
        }

        const engine = configData.engine || "wan2gp";
        const engineName = ENGINE_LABELS[engine] || engine;
        const generateButton = document.getElementById("generate-submit-btn");
        generateButton.disabled = !data.connected;
        generateButton.title = data.connected ? "" : "Complete local engine setup in Hardware & Engines.";

        if (data.connected) {
          statusDot.className = "status-dot green";
          statusLabel.textContent = `${engineName}: Ready`;
          const model = mediaTypeSelect.value === "image"
            ? (engine === "draw_things" ? configData.draw_things_image_model : configData.image_model_type)
            : (engine === "draw_things" ? configData.draw_things_video_model : configData.model_type);
          statusDetail.textContent = `Default model: ${model || "auto"}.`;
          const rt = data.diagnostic && data.diagnostic.runtime;
          if (sidebarHwPill && rt && rt.device) {
            const vramGb = rt.memory_bytes ? `${Math.round(rt.memory_bytes / (1024 ** 3))}GB VRAM` : "";
            sidebarHwPill.textContent = `${rt.device}${vramGb ? " · " + vramGb : ""}`;
            sidebarHwPill.classList.remove("hidden");
          }
        } else {
          statusDot.className = "status-dot red";
          statusLabel.textContent = `${engineName}: Offline`;
          let failReason = "Local engine is not ready.";
          if (engine === "draw_things") {
            failReason = data.diagnostic.draw_things_cli_found
              ? "Draw Things CLI found, but Apple Silicon macOS is required."
              : "Draw Things CLI was not found; check Hardware & Engines.";
          } else if (engine === "comfyui") {
            failReason = (data.diagnostic.comfyui && data.diagnostic.comfyui.guidance) || "ComfyUI not reachable.";
          } else if (engine === "mlx") {
            failReason = (data.diagnostic.mlx && data.diagnostic.mlx.guidance) || "Apple MLX not installed.";
          } else if (engine === "sdcpp") {
            failReason = (data.diagnostic.sdcpp && data.diagnostic.sdcpp.guidance) || "sd-cli not found.";
          } else if (!data.diagnostic.root_exists) {
            failReason = "Wan2GP Root folder does not exist.";
          } else if (!data.diagnostic.python_exists) {
            failReason = "Wan2GP Python path is invalid.";
          } else if (data.diagnostic.runtime && data.diagnostic.runtime.error) {
            failReason = data.diagnostic.runtime.error;
          }
          statusDetail.textContent = `${failReason} Check Hardware & Engines.`;
        }
      }
    } catch (e) {
      engineReady = false;
      statusDot.className = "status-dot red";
      statusLabel.textContent = "Local engine: Error";
      statusDetail.textContent = "Could not check local setup.";
      document.getElementById("generate-submit-btn").disabled = true;
    }
  }

  async function loadInstalledModels() {
    try {
      const res = await apiFetch(`${API_BASE}/api/models`);
      const data = await res.json();
      if (!data.success || !Array.isArray(data.installed_models)) return;
      data.installed_models.forEach(m => {
        if (m && m.id && !optionExists(modelTypeSelect, m.id)) {
          const opt = document.createElement("option");
          opt.value = m.id;
          opt.textContent = `${m.name || m.id} [${m.kind || "local"} ✓]`;
          modelTypeSelect.append(opt);
        }
      });
    } catch (_) {}
  }

  async function loadLlmOptions() {
    if (!factoryLlmSelect) return;
    try {
      const res = await apiFetch(`${API_BASE}/api/llm-options`);
      const data = await res.json();
      if (!data.success) return;
      const active = data.active || {};
      factoryLlmSelect.innerHTML = `<option value="">Follow Active Chat LLM (${escapeHtml(active.provider || "auto")}/${escapeHtml(active.model || "default")})</option>`;
      (data.options || []).forEach(opt => {
        const el = document.createElement("option");
        el.value = `${opt.provider}::${opt.model}`;
        el.textContent = `${opt.label || `${opt.provider} / ${opt.model}`}${opt.is_active ? " ★" : ""}`;
        factoryLlmSelect.append(el);
      });
    } catch (_) {}
  }

  // ── QUICK STUDIO: AI PROMPT ENHANCEMENT & SUBMISSION ──
  enhanceBtn.addEventListener("click", async () => {
    const rawPrompt = promptInput.value.trim();
    if (!rawPrompt) {
      alert("Please write a prompt description before applying AI enhancement.");
      return;
    }
    optimizedBox.classList.remove("hidden");
    optimizedText.textContent = "Generating optimized cinematic script details...";
    currentOptimizedPrompt = "";
    try {
      const res = await apiFetch(`${API_BASE}/api/enhance-prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: rawPrompt, media_type: mediaTypeSelect.value }),
      });
      const data = await res.json();
      if (data.success) {
        optimizedText.textContent = data.enhanced_prompt;
        currentOptimizedPrompt = data.enhanced_prompt || "";
      } else {
        optimizedText.textContent = "AI Prompt Enhancement failed. Will proceed using standard local visual templates.";
      }
    } catch (_) {
      optimizedText.textContent = "AI Prompt Enhancement failed. Will proceed using standard local visual templates.";
    }
  });

  generationForm.addEventListener("submit", async e => {
    e.preventDefault();
    const prompt = promptInput.value.trim();
    if (!prompt) return;

    const submitBtn = document.getElementById("generate-submit-btn");
    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting Generation Request...";

    const imgAtt = quickAttachments.find(a => a.kind === "image");
    const vidAtt = quickAttachments.find(a => a.kind === "video");
    const audAtt = quickAttachments.find(a => a.kind === "audio");

    const payload = {
      prompt,
      media_type: mediaTypeSelect.value,
      model_type: modelTypeSelect.value,
      resolution: resolutionSelect.value,
      steps: parseInt(numStepsInput.value, 10),
      frames: mediaTypeSelect.value === "video" ? parseInt(videoLengthInput.value, 10) : null,
      duration_seconds: mediaTypeSelect.value === "video" && videoDurationInput ? parseFloat(videoDurationInput.value) : null,
      seed: parseInt(seedInput.value, 10),
      optimized_prompt: usableOptimizedPrompt(),
      negative_prompt: quickNegPromptInput ? quickNegPromptInput.value.trim() : "",
      reference_image_ref: imgAtt ? imgAtt.ref : "",
      input_video_ref: vidAtt ? vidAtt.ref : "",
      input_audio_ref: audAtt ? audAtt.ref : "",
    };

    try {
      const res = await apiFetch(`${API_BASE}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        promptInput.value = "";
        optimizedBox.classList.add("hidden");
        currentOptimizedPrompt = "";
        quickAttachments = [];
        renderAttachmentChips(quickAttachmentChips, quickAttachments, () => {});
        pollActiveJobs();
      } else {
        alert("Error launching generation job: " + data.error);
      }
    } catch (_) {
      alert("Unexpected connection error: Could not reach the local generator.");
    } finally {
      submitBtn.disabled = !engineReady;
      submitBtn.innerHTML = "<span>🚀</span> Kick off Generation";
    }
  });

  // ── LIVE JOB POLLING ──
  async function pollActiveJobs() {
    try {
      const res = await apiFetch(`${API_BASE}/api/history`);
      const data = await res.json();
      if (!data.success) return;

      const runningJobs = (data.history || []).filter(item => ["queued", "generating"].includes(item.status));
      if (runningJobs.length > 0) {
        activeJobsList.innerHTML = "";
        runningJobs.forEach(job => {
          const card = document.createElement("div");
          card.className = "job-card";
          card.innerHTML = `
            <div class="job-loader"></div>
            <div class="job-details">
              <div class="job-title">[${escapeHtml((job.media_type || "media").toUpperCase())}] ${job.status === "queued" ? "Queued" : "Generating"} Media #${escapeHtml(job.id)}</div>
              <p class="job-status">${escapeHtml(job.progress_message || (job.status === "queued" ? "Waiting for the local generation engine." : "Loading the model and generating media."))}</p>
              <div class="prompt-text" style="font-size: 11px; margin-top: 6px; opacity: 0.8;">"${escapeHtml((job.original_prompt || "").substring(0, 80))}..."</div>
            </div>
          `;
          activeJobsList.appendChild(card);
        });
        if (!pollingInterval) {
          pollingInterval = setInterval(() => { if (!document.hidden) pollActiveJobs(); }, 5000);
        }
      } else {
        const latest = data.history && data.history.length ? data.history[0] : null;
        if (latest && latest.status === "failed") {
          activeJobsList.innerHTML = `
            <div class="job-card">
              <div class="job-details">
                <div class="job-title">Last generation failed: #${escapeHtml(latest.id)}</div>
                <p class="job-status">${escapeHtml((latest.error_message || "Generator did not produce an output file.").substring(0, 220))}</p>
                <details><summary>Failure details</summary><pre style="white-space: pre-wrap; overflow-wrap: anywhere;">${escapeHtml(latest.error_message || "The generator did not produce an output file.")}</pre></details>
                <div class="prompt-text" style="font-size: 11px; margin-top: 6px; opacity: 0.8;">"${escapeHtml((latest.original_prompt || "").substring(0, 80))}..."</div>
              </div>
            </div>
          `;
        } else if (latest && latest.status === "completed") {
          activeJobsList.innerHTML = `
            <div class="job-card">
              <div class="job-details">
                <div class="job-title">Last generation completed: #${escapeHtml(latest.id)}</div>
                <p class="job-status">${escapeHtml(latest.media_type || "Media")} saved as ${escapeHtml(latest.file_name || "output file")}.</p>
              </div>
            </div>
          `;
        } else {
          activeJobsList.innerHTML = `
            <div class="no-jobs">
              <div class="no-jobs-icon">😴</div>
              <p>No active background generation tasks running.</p>
              <span class="no-jobs-sub">Submitting a request will show live progress here.</span>
            </div>
          `;
        }
        if (pollingInterval) {
          clearInterval(pollingInterval);
          pollingInterval = null;
          if (currentView === "history-view") loadCreationsGallery();
        }
      }
    } catch (e) {
      console.error("Job status polling failed.", e);
    }
  }

  // ── LONG-VIDEO FACTORY (GOAL LOOP CONTROLLED) ──
  if (factoryNewToggleBtn) {
    factoryNewToggleBtn.addEventListener("click", () => {
      factoryNewCard.classList.toggle("hidden");
      loadLlmOptions();
    });
  }
  if (factoryCancelCreate) {
    factoryCancelCreate.addEventListener("click", () => factoryNewCard.classList.add("hidden"));
  }
  if (factoryDurationPresets) {
    factoryDurationPresets.querySelectorAll(".pill-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        factoryDurationPresets.querySelectorAll(".pill-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        factoryDurationInput.value = btn.getAttribute("data-seconds");
      });
    });
  }

  if (factoryCreateForm) {
    factoryCreateForm.addEventListener("submit", async e => {
      e.preventDefault();
      const submitBtn = document.getElementById("factory-create-submit");
      submitBtn.disabled = true;
      submitBtn.textContent = factoryPlanNowCheck.checked ? "Creating & Planning Script…" : "Creating…";

      let llmProvider = "";
      let llmModel = "";
      if (factoryLlmSelect && factoryLlmSelect.value && factoryLlmSelect.value.includes("::")) {
        const parts = factoryLlmSelect.value.split("::");
        llmProvider = parts[0] || "";
        llmModel = parts[1] || "";
      }

      try {
        const res = await apiFetch(`${API_BASE}/api/projects`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: factoryTitleInput.value.trim(),
            original_request: factoryBriefInput.value.trim(),
            aspect_ratio: factoryAspectSelect.value,
            target_duration_seconds: parseFloat(factoryDurationInput.value) || 30,
            scene_duration_seconds: parseFloat(factorySceneSecondsInput.value) || 5,
            llm_provider_override: llmProvider,
            llm_model_override: llmModel,
            attachment_refs: factoryAttachments.map(a => a.ref),
            settings: { use_keyframes: Boolean(factoryUseKeyframesCheck && factoryUseKeyframesCheck.checked) },
            plan_now: Boolean(factoryPlanNowCheck && factoryPlanNowCheck.checked),
          }),
        });
        const data = await res.json();
        if (data.success && data.project) {
          factoryNewCard.classList.add("hidden");
          factoryTitleInput.value = "";
          factoryBriefInput.value = "";
          factoryAttachments = [];
          renderAttachmentChips(factoryAttachmentChips, factoryAttachments, () => {});
          selectedProjectId = data.project.id;
          await loadFactoryProjects();
          renderSelectedProject(data.project);
        } else {
          alert("Could not create project: " + (data.error || "Unknown error"));
        }
      } catch (err) {
        alert("Failed to create project: " + err.message);
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = "<span>🎬</span> Create Production";
      }
    });
  }

  async function loadFactoryProjects() {
    if (!factoryProjectsList) return;
    try {
      const res = await apiFetch(`${API_BASE}/api/projects`);
      const data = await res.json();
      if (!data.success) return;
      const projects = data.projects || [];
      if (!projects.length) {
        factoryProjectsList.innerHTML = `<p class="field-hint">No video productions yet. Click "New Video Project" to start.</p>`;
        selectedProjectId = null;
        factoryEmptyWorkspace.classList.remove("hidden");
        factoryProjectDetail.classList.add("hidden");
        return;
      }
      factoryProjectsList.innerHTML = "";
      projects.forEach(p => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "project-list-item" + (p.id === selectedProjectId ? " active" : "");
        btn.innerHTML = `
          <div class="project-list-title">#${escapeHtml(p.id)} · ${escapeHtml(p.title)}</div>
          <div class="project-list-meta">
            <span>${escapeHtml(p.status)} · ${escapeHtml(p.rendered_scene_count || 0)}/${escapeHtml(p.scene_count || 0)} scenes</span>
            <span>${escapeHtml(formatDuration(p.target_duration_seconds))}</span>
          </div>
        `;
        btn.addEventListener("click", () => selectFactoryProject(p.id));
        factoryProjectsList.appendChild(btn);
      });
      if (!selectedProjectId && projects.length) {
        selectFactoryProject(projects[0].id);
      }
    } catch (e) {
      factoryProjectsList.innerHTML = `<p class="field-hint">Failed to load productions.</p>`;
    }
  }

  async function selectFactoryProject(projectId) {
    selectedProjectId = projectId;
    await refreshSelectedProject();
    loadFactoryProjects();
  }

  async function refreshSelectedProject() {
    if (!selectedProjectId) return;
    try {
      const res = await apiFetch(`${API_BASE}/api/projects/${selectedProjectId}`);
      const data = await res.json();
      if (data.success && data.project) {
        renderSelectedProject(data.project);
      }
    } catch (_) {}
  }

  function renderSelectedProject(proj) {
    selectedProjectData = proj;
    factoryEmptyWorkspace.classList.add("hidden");
    factoryProjectDetail.classList.remove("hidden");

    projDetailTitle.textContent = `#${proj.id} — ${proj.title}`;
    projDetailMeta.textContent =
      `${proj.scene_count || 0} scenes (${proj.rendered_scene_count || 0} rendered) · Target ${formatDuration(proj.target_duration_seconds)} · ${proj.resolution} @ ${proj.fps}fps · Engine: ${proj.engine || "default"}`;
    projStatusPill.textContent = proj.status || "draft";
    projStatusPill.className = `status-pill ${escapeHtml(proj.status || "draft")}`;

    const pct = Math.max(0, Math.min(100, Number(proj.progress_percent || 0)));
    projProgressFill.style.width = `${pct}%`;
    projProgressPct.textContent = `${Math.round(pct)}%`;
    projProgressMsg.textContent = proj.progress_message || proj.error_message || "Ready.";

    // Stage stepper highlight
    const stagesOrder = ["plan", "keyframes", "render", "refine", "assemble"];
    const curStage = proj.current_stage || "plan";
    const curIdx = stagesOrder.indexOf(curStage);
    const goalCompleted = (proj.goal_status && proj.goal_status.completed_stages) || [];
    projStageStepper.querySelectorAll(".stage-step").forEach(stepEl => {
      const st = stepEl.getAttribute("data-stage");
      const stIdx = stagesOrder.indexOf(st);
      stepEl.classList.remove("active", "done");
      if (proj.status === "completed" || goalCompleted.includes(st) || (stIdx >= 0 && stIdx < curIdx)) {
        stepEl.classList.add("done");
      } else if (st === curStage) {
        stepEl.classList.add("active");
      }
    });

    // Final master video
    if (proj.has_final_output && proj.final_video_url) {
      projMasterBox.classList.remove("hidden");
      if (projMasterVideo.getAttribute("data-src") !== proj.final_video_url) {
        projMasterVideo.src = proj.final_video_url;
        projMasterVideo.setAttribute("data-src", proj.final_video_url);
      }
      projMasterDownload.href = proj.final_video_url;
      projMasterDownload.setAttribute("download", proj.final_file_name || "master.mp4");
    } else {
      projMasterBox.classList.add("hidden");
    }

    // Only overwrite textareas if user is not currently typing inside them
    if (document.activeElement !== projStyleBible) projStyleBible.value = proj.style_bible || "";
    if (document.activeElement !== projScriptText) projScriptText.value = proj.script_text || "";

    // Render scene storyboard cards
    const scenes = proj.scenes || [];
    projSceneCount.textContent = String(scenes.length);
    if (!scenes.length) {
      projScenesList.innerHTML = `<p class="field-hint">No scenes yet. Click "Plan / Re-Plan" to generate an LLM scene breakdown or "Add Scene" manually.</p>`;
    } else {
      projScenesList.innerHTML = "";
      scenes.forEach(scene => {
        const card = document.createElement("div");
        card.className = "scene-card";
        let previewHtml = `<span class="field-hint">Not rendered yet</span>`;
        if (scene.clip_url) {
          previewHtml = `<video controls muted playsinline src="${escapeHtml(scene.clip_url)}"></video>`;
        } else if (scene.keyframe_url) {
          previewHtml = `<img src="${escapeHtml(scene.keyframe_url)}" alt="Keyframe">`;
        }
        card.innerHTML = `
          <div class="scene-card-head">
            <span class="scene-index-badge">Scene ${escapeHtml(scene.scene_index + 1)} · ${escapeHtml(scene.duration_seconds)}s (${escapeHtml(scene.frames)}f)</span>
            <span class="status-pill ${escapeHtml(scene.status || "pending")}">${escapeHtml(scene.status || "pending")}</span>
          </div>
          <div class="scene-body-grid">
            <div>
              <div class="form-row">
                <div class="form-group col">
                  <label>Scene Title</label>
                  <input type="text" class="sc-title" value="${escapeHtml(scene.title || "")}">
                </div>
                <div class="form-group" style="width: 110px;">
                  <label>Seconds</label>
                  <input type="number" class="sc-dur" min="2" max="20" step="0.5" value="${escapeHtml(scene.duration_seconds || 5)}">
                </div>
                <div class="form-group" style="width: 130px;">
                  <label>Transition</label>
                  <select class="sc-trans">
                    <option value="crossfade"${scene.transition === "crossfade" ? " selected" : ""}>Crossfade</option>
                    <option value="cut"${scene.transition !== "crossfade" ? " selected" : ""}>Cut</option>
                  </select>
                </div>
              </div>
              <div class="form-group">
                <label>Visual Prompt</label>
                <textarea class="sc-prompt compact-textarea">${escapeHtml(scene.prompt || "")}</textarea>
              </div>
              ${scene.enhanced_prompt ? `<p class="field-hint"><strong>LLM Enhanced:</strong> ${escapeHtml(scene.enhanced_prompt)}</p>` : ""}
              <div class="form-row" style="margin-top: 8px;">
                <div class="form-group col">
                  <label>Camera Direction</label>
                  <input type="text" class="sc-camera" value="${escapeHtml(scene.camera_direction || "")}">
                </div>
                <div class="form-group col">
                  <label>Audio / Mood Cue</label>
                  <input type="text" class="sc-audio" value="${escapeHtml(scene.audio_cue || "")}">
                </div>
              </div>
              ${scene.error_message ? `<p class="field-hint" style="color: var(--accent-error);">${escapeHtml(scene.error_message)}</p>` : ""}
              <div class="form-actions-row" style="margin-top: 10px;">
                <button type="button" class="btn btn-secondary btn-sm sc-save-btn">💾 Save</button>
                <button type="button" class="btn btn-secondary btn-sm sc-enhance-btn">✨ LLM Enhance</button>
                <button type="button" class="btn btn-primary btn-auto btn-sm sc-render-btn">🎬 Render Scene</button>
                <button type="button" class="btn btn-danger btn-sm sc-delete-btn">Delete</button>
              </div>
            </div>
            <div>
              <div class="scene-preview-box">${previewHtml}</div>
              ${scene.quality_notes ? `<p class="field-hint" style="margin-top: 6px;">${escapeHtml(scene.quality_notes)}</p>` : ""}
            </div>
          </div>
        `;

        card.querySelector(".sc-save-btn").addEventListener("click", async () => {
          const res = await apiFetch(`${API_BASE}/api/projects/${proj.id}/scenes/${scene.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              title: card.querySelector(".sc-title").value.trim(),
              duration_seconds: parseFloat(card.querySelector(".sc-dur").value) || 5,
              transition: card.querySelector(".sc-trans").value,
              prompt: card.querySelector(".sc-prompt").value.trim(),
              camera_direction: card.querySelector(".sc-camera").value.trim(),
              audio_cue: card.querySelector(".sc-audio").value.trim(),
            }),
          });
          const data = await res.json();
          if (data.success && data.project) renderSelectedProject(data.project);
        });

        card.querySelector(".sc-enhance-btn").addEventListener("click", async e => {
          const btn = e.currentTarget;
          btn.disabled = true;
          btn.textContent = "Enhancing…";
          try {
            const res = await apiFetch(`${API_BASE}/api/projects/${proj.id}/scenes/${scene.id}/enhance`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({}),
            });
            const data = await res.json();
            if (data.success && data.project) renderSelectedProject(data.project);
          } finally {
            btn.disabled = false;
            btn.textContent = "✨ LLM Enhance";
          }
        });

        card.querySelector(".sc-render-btn").addEventListener("click", async () => {
          const res = await apiFetch(`${API_BASE}/api/projects/${proj.id}/scenes/${scene.id}/render`, { method: "POST" });
          const data = await res.json();
          if (data.success) startFactoryPolling();
          else alert(data.message || data.error || "Could not start scene render.");
        });

        card.querySelector(".sc-delete-btn").addEventListener("click", async () => {
          if (!confirm(`Delete Scene ${scene.scene_index + 1}?`)) return;
          const res = await apiFetch(`${API_BASE}/api/projects/${proj.id}/scenes/${scene.id}`, { method: "DELETE" });
          const data = await res.json();
          if (data.success && data.project) renderSelectedProject(data.project);
        });

        projScenesList.appendChild(card);
      });
    }

    // Stage logs
    const logs = proj.stage_logs || [];
    projStageLogs.innerHTML = logs.length
      ? logs.map(l => `<div class="stage-log-entry ${escapeHtml(l.level || "info")}">[${escapeHtml(l.stage)}] ${escapeHtml(l.message)}</div>`).join("")
      : `<div class="stage-log-entry">No stage events recorded yet.</div>`;

    // Auto-poll if active
    const goalState = (proj.goal_status && proj.goal_status.status) || "";
    const isActive = ["rendering", "refining", "assembling"].includes(proj.status) || ["queued", "running"].includes(goalState);
    if (isActive) startFactoryPolling();
    else stopFactoryPolling();
  }

  function startFactoryPolling() {
    if (factoryPollInterval) return;
    factoryPollInterval = setInterval(() => {
      if (!document.hidden && selectedProjectId) refreshSelectedProject();
    }, 3500);
  }

  function stopFactoryPolling() {
    if (factoryPollInterval) {
      clearInterval(factoryPollInterval);
      factoryPollInterval = null;
    }
  }

  async function triggerGoalEndpoint(path, body = null) {
    if (!selectedProjectId) return;
    try {
      const opts = { method: "POST", headers: { "Content-Type": "application/json" } };
      if (body !== null) opts.body = JSON.stringify(body);
      const res = await apiFetch(`${API_BASE}/api/projects/${selectedProjectId}${path}`, opts);
      const data = await res.json();
      if (!data.success) {
        alert(data.error || data.message || "Goal loop command failed.");
        return;
      }
      if (data.project) renderSelectedProject(data.project);
      else await refreshSelectedProject();
      startFactoryPolling();
    } catch (e) {
      alert("Goal loop error: " + e.message);
    }
  }

  document.getElementById("goal-run-all-btn")?.addEventListener("click", () => {
    const useKf = Boolean(selectedProjectData && selectedProjectData.settings && selectedProjectData.settings.use_keyframes);
    triggerGoalEndpoint("/goal/start", {
      stages: useKf ? ["plan", "keyframes", "render", "refine", "assemble"] : ["plan", "render", "refine", "assemble"],
      use_keyframes: useKf,
    });
  });
  document.getElementById("goal-stage-plan-btn")?.addEventListener("click", () => triggerGoalEndpoint("/plan", { replace_existing: true }));
  document.getElementById("goal-stage-keyframes-btn")?.addEventListener("click", () => triggerGoalEndpoint("/goal/stage", { stage: "keyframes" }));
  document.getElementById("goal-stage-render-btn")?.addEventListener("click", () => triggerGoalEndpoint("/goal/stage", { stage: "render" }));
  document.getElementById("goal-stage-refine-btn")?.addEventListener("click", () => triggerGoalEndpoint("/goal/stage", { stage: "refine" }));
  document.getElementById("goal-stage-assemble-btn")?.addEventListener("click", () => triggerGoalEndpoint("/goal/stage", { stage: "assemble" }));
  document.getElementById("goal-pause-btn")?.addEventListener("click", () => triggerGoalEndpoint("/goal/pause"));
  document.getElementById("goal-resume-btn")?.addEventListener("click", () => triggerGoalEndpoint("/goal/resume", {}));
  document.getElementById("goal-cancel-btn")?.addEventListener("click", () => triggerGoalEndpoint("/goal/cancel"));

  projSaveScriptBtn?.addEventListener("click", async () => {
    if (!selectedProjectId) return;
    const res = await apiFetch(`${API_BASE}/api/projects/${selectedProjectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        style_bible: projStyleBible.value,
        script_text: projScriptText.value,
      }),
    });
    const data = await res.json();
    if (data.success && data.project) {
      renderSelectedProject(data.project);
      alert("Script and style bible saved.");
    }
  });

  projAddSceneBtn?.addEventListener("click", async () => {
    if (!selectedProjectId) return;
    const res = await apiFetch(`${API_BASE}/api/projects/${selectedProjectId}/scenes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: `Scene ${(selectedProjectData && selectedProjectData.scene_count ? selectedProjectData.scene_count : 0) + 1}`,
        duration_seconds: (selectedProjectData && selectedProjectData.scene_duration_seconds) || 5,
        prompt: (selectedProjectData && selectedProjectData.original_request) || "",
      }),
    });
    const data = await res.json();
    if (data.success && data.project) renderSelectedProject(data.project);
  });

  projDeleteBtn?.addEventListener("click", async () => {
    if (!selectedProjectId || !confirm(`Permanently delete Media Factory project #${selectedProjectId}?`)) return;
    const res = await apiFetch(`${API_BASE}/api/projects/${selectedProjectId}`, { method: "DELETE" });
    const data = await res.json();
    if (data.success) {
      selectedProjectId = null;
      loadFactoryProjects();
    }
  });

  // ── IMAGE STUDIO & EDITOR ──
  function setEditorSource(ref, url, filename) {
    editorSource = { ref, url, filename: filename || "image" };
    editorOriginalPreview.innerHTML = `<img src="${escapeHtml(url)}" alt="Original source">`;
    editorStatusText.textContent = `Loaded source image: ${filename || ref}`;
  }

  async function populateEditorGalleryPicker() {
    if (!editorGalleryPicker) return;
    try {
      const res = await apiFetch(`${API_BASE}/api/history?media_type=image`);
      const data = await res.json();
      if (!data.success) return;
      const items = (data.history || []).filter(i => i.status === "completed" && i.file_name);
      editorGalleryPicker.innerHTML = `<option value="">Or select an image from your Gallery (${items.length})…</option>`;
      items.forEach(item => {
        const opt = document.createElement("option");
        opt.value = `history:${item.id}`;
        opt.dataset.url = `${API_BASE}/media/image/${item.file_name}`;
        opt.textContent = `#${item.id} — ${(item.original_prompt || "").substring(0, 60)}`;
        editorGalleryPicker.append(opt);
      });
    } catch (_) {}
  }

  editorGalleryPicker?.addEventListener("change", () => {
    const opt = editorGalleryPicker.selectedOptions[0];
    if (opt && opt.value) {
      setEditorSource(opt.value, opt.dataset.url, opt.textContent);
    }
  });

  editorStrengthInput?.addEventListener("input", () => {
    editorStrengthVal.textContent = editorStrengthInput.value;
  });

  editorEnhanceBtn?.addEventListener("click", async () => {
    const text = editorInstruction.value.trim();
    if (!text) return;
    editorEnhanceBtn.disabled = true;
    try {
      const res = await apiFetch(`${API_BASE}/api/enhance-prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text, media_type: "image", edit_mode: true }),
      });
      const data = await res.json();
      if (data.success && data.enhanced_prompt) editorInstruction.value = data.enhanced_prompt;
    } finally {
      editorEnhanceBtn.disabled = false;
    }
  });

  imageEditForm?.addEventListener("submit", async e => {
    e.preventDefault();
    if (!editorSource) {
      alert("Please upload, paste, or pick a source image first.");
      return;
    }
    const instruction = editorInstruction.value.trim();
    if (!instruction) return;
    editorSubmitBtn.disabled = true;
    editorStatusText.textContent = "Submitting image modification job…";
    editorResultActions.classList.add("hidden");
    try {
      const res = await apiFetch(`${API_BASE}/api/edit-image`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instruction,
          source_ref: editorSource.ref,
          model_type: editorModelSelect.value || undefined,
          denoise_strength: parseFloat(editorStrengthInput.value) || 0.65,
          steps: parseInt(editorStepsInput.value, 10) || 12,
          seed: parseInt(editorSeedInput.value, 10),
        }),
      });
      const data = await res.json();
      if (data.success && data.item_id) {
        editorActiveJobId = data.item_id;
        editorResultPreview.innerHTML = `<div class="job-loader"></div>`;
        editorStatusText.textContent = `Modifying image (#${data.item_id})…`;
        pollEditorResult();
      } else {
        alert("Image modification failed: " + (data.error || "Unknown error"));
        editorSubmitBtn.disabled = false;
      }
    } catch (err) {
      alert("Image modification error: " + err.message);
      editorSubmitBtn.disabled = false;
    }
  });

  async function pollEditorResult() {
    if (!editorActiveJobId) return;
    if (editorPollTimer) clearTimeout(editorPollTimer);
    try {
      const res = await apiFetch(`${API_BASE}/api/history/${editorActiveJobId}`);
      const data = await res.json();
      if (data.success && data.item) {
        const item = data.item;
        if (item.status === "completed" && item.file_name) {
          const url = `${API_BASE}/media/image/${item.file_name}`;
          editorLastResult = { ref: `history:${item.id}`, url, item_id: item.id, filename: item.file_name };
          editorResultPreview.innerHTML = `<img src="${escapeHtml(url)}" alt="Modified image">`;
          editorStatusText.textContent = `Modification #${item.id} complete!`;
          editorResultActions.classList.remove("hidden");
          editorSubmitBtn.disabled = false;
          editorActiveJobId = null;
          return;
        }
        if (item.status === "failed") {
          editorResultPreview.innerHTML = `<span class="field-hint" style="color: var(--accent-error);">Edit failed: ${escapeHtml(item.error_message || "Unknown error")}</span>`;
          editorStatusText.textContent = "Edit failed.";
          editorSubmitBtn.disabled = false;
          editorActiveJobId = null;
          return;
        }
        editorStatusText.textContent = item.progress_message || `Status: ${item.status}…`;
      }
    } catch (_) {}
    editorPollTimer = setTimeout(pollEditorResult, 3000);
  }

  editorAnimateBtn?.addEventListener("click", () => {
    if (!editorLastResult) return;
    quickAttachments = [{ ref: editorLastResult.ref, kind: "image", filename: editorLastResult.filename, url: editorLastResult.url }];
    renderAttachmentChips(quickAttachmentChips, quickAttachments, idx => {
      quickAttachments.splice(idx, 1);
      renderAttachmentChips(quickAttachmentChips, quickAttachments, () => {});
    });
    mediaTypeSelect.value = "video";
    applyGenerationDefaultsForMediaType();
    switchView("dashboard-view");
  });

  editorUseAsSourceBtn?.addEventListener("click", () => {
    if (!editorLastResult) return;
    setEditorSource(editorLastResult.ref, editorLastResult.url, editorLastResult.filename);
  });

  // ── GALLERY OF CREATIONS (XSS-SAFE) ──
  filterTabs.forEach(tab => {
    tab.addEventListener("click", () => {
      filterTabs.forEach(f => f.classList.remove("active"));
      tab.classList.add("active");
      galleryFilter = tab.getAttribute("data-filter");
      loadCreationsGallery();
    });
  });

  async function loadCreationsGallery() {
    galleryGrid.innerHTML = "";
    try {
      const filterType = galleryFilter === "all" ? null : galleryFilter;
      const url = filterType ? `${API_BASE}/api/history?media_type=${encodeURIComponent(filterType)}` : `${API_BASE}/api/history`;
      const res = await apiFetch(url);
      const data = await res.json();
      if (!data.success) return;

      const completedItems = (data.history || []).filter(item => !["queued", "generating"].includes(item.status));
      if (completedItems.length === 0) {
        galleryEmpty.classList.remove("hidden");
        galleryGrid.classList.add("hidden");
        return;
      }
      galleryEmpty.classList.add("hidden");
      galleryGrid.classList.remove("hidden");

      completedItems.forEach(item => {
        const card = document.createElement("article");
        card.className = "gallery-card";
        const isVideo = item.media_type === "video";
        const badgeText = isVideo ? "🎬 VIDEO" : "🖼️ IMAGE";
        const fileUrl = `${API_BASE}/media/${encodeURIComponent(item.media_type)}/${encodeURIComponent(item.file_name || "")}`;

        let previewHtml = "";
        if (item.status === "failed") {
          previewHtml = `
            <div class="media-preview-container" style="background-color: hsla(355, 85%, 55%, 0.15); display: flex; align-items: center; justify-content: center; text-align: center; padding: 16px;">
              <div style="color: var(--accent-error); font-size: 13px; font-weight: 600;">⚠️ Generation Failed</div>
            </div>
          `;
        } else if (isVideo) {
          previewHtml = `
            <div class="media-preview-container" data-id="${escapeHtml(item.id)}">
              <div class="media-type-badge">${badgeText}</div>
              <div class="media-play-overlay">▶</div>
              <video muted playsinline loop src="${escapeHtml(fileUrl)}"></video>
            </div>
          `;
        } else {
          previewHtml = `
            <div class="media-preview-container" data-id="${escapeHtml(item.id)}">
              <div class="media-type-badge">${badgeText}</div>
              <img src="${escapeHtml(fileUrl)}" alt="Generation preview">
            </div>
          `;
        }

        const timestamp = item.timestamp ? new Date(item.timestamp).toLocaleString() : "";
        const imageActions = !isVideo && item.status === "completed"
          ? `<button class="btn btn-secondary btn-sm edit-img-btn" data-id="${escapeHtml(item.id)}" data-url="${escapeHtml(fileUrl)}" data-name="${escapeHtml(item.file_name)}">🪄 Edit</button>
             <button class="btn btn-secondary btn-sm animate-img-btn" data-id="${escapeHtml(item.id)}" data-url="${escapeHtml(fileUrl)}" data-name="${escapeHtml(item.file_name)}">🎬 Animate</button>`
          : "";

        card.innerHTML = `
          ${previewHtml}
          <div class="card-body">
            <h3 class="card-title" title="${escapeHtml(item.original_prompt)}">${escapeHtml(item.original_prompt)}</h3>
            <div class="card-meta">
              <span>Seed: ${escapeHtml(item.seed)}</span>
              <span>${escapeHtml(timestamp)}</span>
            </div>
          </div>
          <div class="card-footer-actions">
            ${imageActions}
            <button class="btn btn-danger btn-sm delete-btn" data-id="${escapeHtml(item.id)}">Delete</button>
          </div>
        `;
        galleryGrid.appendChild(card);
      });

      galleryGrid.querySelectorAll(".media-preview-container").forEach(preview => {
        preview.addEventListener("click", () => {
          const id = preview.getAttribute("data-id");
          if (id) openMediaLightbox(id);
        });
      });

      galleryGrid.querySelectorAll(".media-preview-container video").forEach(video => {
        const container = video.parentElement;
        container.addEventListener("mouseenter", () => video.play().catch(() => {}));
        container.addEventListener("mouseleave", () => { video.pause(); video.currentTime = 0; });
      });

      galleryGrid.querySelectorAll(".edit-img-btn").forEach(btn => {
        btn.addEventListener("click", e => {
          e.stopPropagation();
          setEditorSource(`history:${btn.dataset.id}`, btn.dataset.url, btn.dataset.name);
          switchView("editor-view");
        });
      });

      galleryGrid.querySelectorAll(".animate-img-btn").forEach(btn => {
        btn.addEventListener("click", e => {
          e.stopPropagation();
          quickAttachments = [{ ref: `history:${btn.dataset.id}`, kind: "image", filename: btn.dataset.name, url: btn.dataset.url }];
          renderAttachmentChips(quickAttachmentChips, quickAttachments, idx => {
            quickAttachments.splice(idx, 1);
            renderAttachmentChips(quickAttachmentChips, quickAttachments, () => {});
          });
          mediaTypeSelect.value = "video";
          applyGenerationDefaultsForMediaType();
          switchView("dashboard-view");
        });
      });

      galleryGrid.querySelectorAll(".delete-btn").forEach(btn => {
        btn.addEventListener("click", e => {
          e.stopPropagation();
          const id = btn.getAttribute("data-id");
          if (confirm(`Are you sure you want to permanently delete generation #${id}?`)) {
            deleteCreation(id);
          }
        });
      });
    } catch (e) {
      console.error("Creations loading failed.", e);
    }
  }

  async function deleteCreation(id) {
    try {
      const res = await apiFetch(`${API_BASE}/api/history/${id}`, { method: "DELETE" });
      const data = await res.json();
      if (data.success) loadCreationsGallery();
      else alert("Failed to delete creation: " + data.error);
    } catch (_) {
      alert("Unexpected error attempting delete.");
    }
  }

  // ── MODAL LIGHTBOX ──
  async function openMediaLightbox(id) {
    try {
      const res = await apiFetch(`${API_BASE}/api/history/${id}`);
      const data = await res.json();
      if (!data.success) return;
      const item = data.item;
      const isVideo = item.media_type === "video";
      const fileUrl = `${API_BASE}/media/${encodeURIComponent(item.media_type)}/${encodeURIComponent(item.file_name || "")}`;

      modalMediaContent.innerHTML = isVideo
        ? `<video controls autoplay loop src="${escapeHtml(fileUrl)}"></video>`
        : `<img src="${escapeHtml(fileUrl)}" alt="Detail view">`;

      document.getElementById("modal-title").textContent = isVideo ? "Optimized Cinematic Prompt" : "Optimized Image Prompt";
      modalPrompt.textContent = item.optimized_prompt || item.original_prompt;
      modalMeta.innerHTML = `
        <div class="meta-chip"><span class="meta-chip-label">ID:</span> #${escapeHtml(item.id)}</div>
        <div class="meta-chip"><span class="meta-chip-label">Type:</span> ${escapeHtml((item.media_type || "").toUpperCase())}</div>
        <div class="meta-chip"><span class="meta-chip-label">Model:</span> ${escapeHtml(item.model_type || "Default")}</div>
        <div class="meta-chip"><span class="meta-chip-label">Resolution:</span> ${escapeHtml(item.resolution || "Default")}</div>
        <div class="meta-chip"><span class="meta-chip-label">Steps:</span> ${escapeHtml(item.steps || "Default")}</div>
        <div class="meta-chip"><span class="meta-chip-label">Seed:</span> ${escapeHtml(item.seed)}</div>
      `;
      if (modalActions) {
        modalActions.innerHTML = `<a class="btn btn-secondary btn-sm" href="${escapeHtml(fileUrl)}" download="${escapeHtml(item.file_name || "media")}">⬇️ Download</a>`;
        if (!isVideo && item.status === "completed") {
          const editBtn = document.createElement("button");
          editBtn.type = "button";
          editBtn.className = "btn btn-secondary btn-sm";
          editBtn.textContent = "🪄 Modify This Image";
          editBtn.addEventListener("click", () => {
            closeMediaModal();
            setEditorSource(`history:${item.id}`, fileUrl, item.file_name);
            switchView("editor-view");
          });
          modalActions.appendChild(editBtn);
        }
      }
      mediaModal.classList.remove("hidden");
    } catch (e) {
      console.error("Lightbox rendering failed.", e);
    }
  }

  function closeMediaModal() {
    mediaModal.classList.add("hidden");
    modalMediaContent.innerHTML = "";
  }
  modalCloseBtn.addEventListener("click", closeMediaModal);
  document.querySelector(".modal-backdrop").addEventListener("click", closeMediaModal);

  // ── HARDWARE TELEMETRY & 5-ENGINE MATRIX ──
  async function loadEnvironmentPanel() {
    if (!envPlatformLine) return;
    try {
      const [envRes, engRes] = await Promise.all([
        apiFetch(`${API_BASE}/api/wan2gp/environment`),
        apiFetch(`${API_BASE}/api/engines`),
      ]);
      const envData = await envRes.json();
      const engData = await engRes.json();
      if (envData.success) renderEnvironmentPanel(envData.environment);
      if (engData.success) renderHardwareAndEngines(engData);
    } catch (e) {
      envPlatformLine.textContent = "Could not check this computer's local engine environment.";
    }
  }

  function renderHardwareAndEngines(data) {
    const hw = data.hardware || {};
    const profile = hw.recommended_memory_profile || {};
    if (hwGpuVal) hwGpuVal.textContent = `${hw.gpu_name || "CPU"} (${(hw.gpu_backend || "cpu").toUpperCase()})`;
    if (hwVramVal) hwVramVal.textContent = hw.gpu_vram_gb ? `${hw.gpu_vram_gb} GB${hw.unified_memory ? " Unified" : ""}` : "N/A";
    if (hwRamVal) hwRamVal.textContent = hw.system_ram_gb ? `${hw.system_ram_gb} GB (${hw.available_ram_gb || "?"} GB free)` : "N/A";
    if (hwProfileVal) hwProfileVal.textContent = profile.label || `Profile ${profile.profile || "auto"}`;
    if (hwFfmpegVal) hwFfmpegVal.textContent = hw.ffmpeg_available ? "✅ Ready" : "⚠️ Missing";

    if (!engineMatrix) return;
    const engines = data.engines || {};
    const active = data.active_engine || configData.engine || "wan2gp";
    engineMatrix.innerHTML = "";
    ["wan2gp", "comfyui", "mlx", "draw_things", "sdcpp"].forEach(key => {
      const info = engines[key] || {};
      const card = document.createElement("div");
      card.className = "engine-card" + (key === active ? " active-engine" : "");
      const statusText = info.ready ? "Ready" : (info.supported ? "Not Ready" : "Unsupported OS");
      const statusClass = info.ready ? "ready" : (info.supported ? "paused" : "offline");
      const canInstall = Boolean(info.install_supported) && !info.ready && ["wan2gp", "comfyui", "mlx"].includes(key);

      card.innerHTML = `
        <div class="engine-card-head">
          <span class="engine-card-name">${escapeHtml(ENGINE_LABELS[key] || key)}</span>
          <span class="status-pill ${statusClass}">${escapeHtml(statusText)}</span>
        </div>
        <p class="engine-card-desc">${escapeHtml(info.guidance || "")}</p>
        <div class="form-actions-row">
          <button type="button" class="btn btn-secondary btn-sm eng-use-btn" data-engine="${escapeHtml(key)}">${key === active ? "✓ Active" : "Use Engine"}</button>
          ${canInstall ? `<button type="button" class="btn btn-primary btn-auto btn-sm eng-install-btn" data-engine="${escapeHtml(key)}">⬇️ Install</button>` : ""}
        </div>
      `;
      card.querySelector(".eng-use-btn").addEventListener("click", () => {
        settingsEngine.value = key;
        syncEngineSettings();
        settingsForm.requestSubmit();
      });
      const instBtn = card.querySelector(".eng-install-btn");
      if (instBtn) {
        instBtn.addEventListener("click", () => triggerEngineInstall(key));
      }
      engineMatrix.appendChild(card);
    });
  }

  function renderEnvironmentPanel(env) {
    const platform = env.platform || {};
    const configured = env.configured || {};
    if (configData.engine === "draw_things") {
      const drawThings = env.draw_things || {};
      envPlatformLine.textContent = drawThings.ready
        ? `Apple Silicon macOS — Draw Things CLI is ready at ${drawThings.cli}.`
        : (drawThings.supported ? "Apple Silicon macOS — Draw Things CLI was not found." : "Draw Things local inference requires Apple Silicon macOS.");
      envGuidanceLine.textContent = drawThings.models_dir_exists
        ? `Local model store: ${drawThings.models_dir}.`
        : "No model store was detected yet; configure an existing Draw Things models folder.";
      envInstallBtn.classList.add("hidden");
      return;
    }
    const runtime = env.runtime || {};
    const ready = runtime.ready === true;
    const osLabel = platform.system === "Darwin" ? "macOS" : (platform.system || "Unknown OS");
    const gpuLabel = runtime.device || (platform.has_nvidia ? "NVIDIA GPU" : (platform.amd_targets || []).join(", ") || platform.machine || "");
    envPlatformLine.textContent = ready
      ? `${osLabel} (${gpuLabel}) — Wan2GP is set up and ready at ${configured.app_dir}.`
      : `${osLabel} (${gpuLabel}) — Wan2GP is not set up yet on this computer.`;
    if (ready) {
      envGuidanceLine.textContent = "";
      envInstallBtn.classList.add("hidden");
    } else if (env.discovered && env.discovered.python) {
      envGuidanceLine.textContent = `Found an existing install at ${env.discovered.app_dir}. Click "Detect Engines" to apply it.`;
      envInstallBtn.classList.toggle("hidden", !env.install_supported);
    } else {
      envGuidanceLine.textContent = (env.install_supported ? "" : "Automatic install is unavailable here: ")
        + (env.install_guidance || "") + " " + (env.pinokio_hint || "");
      envInstallBtn.classList.toggle("hidden", !env.install_supported);
    }
    renderInstallState(env.install || {});
  }

  function renderInstallState(install) {
    const status = install.status || "idle";
    if (status === "running") {
      envInstallBtn.disabled = true;
      envActionResult.textContent = `Installing ${activeInstallEngine}… current step: ${install.step || "starting"}.`;
      envInstallLog.classList.remove("hidden");
      envInstallLog.textContent = install.log_tail || "";
      envInstallLog.scrollTop = envInstallLog.scrollHeight;
      if (!envInstallPollTimer) envInstallPollTimer = setInterval(pollInstallStatus, 4000);
    } else {
      envInstallBtn.disabled = false;
      if (envInstallPollTimer) {
        clearInterval(envInstallPollTimer);
        envInstallPollTimer = null;
      }
      if (status === "completed") {
        envActionResult.textContent = `${activeInstallEngine} install completed.`;
        envInstallLog.classList.remove("hidden");
        envInstallLog.textContent = install.log_tail || "";
        checkSystemConnection();
        loadEnvironmentPanel();
      } else if (status === "failed") {
        envActionResult.textContent = `Install failed: ${install.error || "see log below"}`;
        envInstallLog.classList.remove("hidden");
        envInstallLog.textContent = install.log_tail || "";
      }
    }
  }

  async function pollInstallStatus() {
    try {
      const res = await apiFetch(`${API_BASE}/api/engines/install/status?engine=${encodeURIComponent(activeInstallEngine)}`);
      const data = await res.json();
      if (data.success) renderInstallState(data.install || {});
    } catch (_) {}
  }

  async function triggerEngineInstall(engine) {
    if (!confirm(`Install ${ENGINE_LABELS[engine] || engine} on this machine? This downloads local AI dependencies and may take several minutes.`)) return;
    activeInstallEngine = engine;
    envActionResult.textContent = `Starting ${engine} install…`;
    try {
      const res = await apiFetch(`${API_BASE}/api/engines/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ engine }),
      });
      const data = await res.json();
      envActionResult.textContent = data.message || data.error || "";
      if (data.success) renderInstallState({ status: "running", step: "starting" });
    } catch (e) {
      envActionResult.textContent = "Install request failed: " + e.message;
    }
  }

  envVerifyBtn?.addEventListener("click", async () => {
    envVerifyBtn.disabled = true;
    envActionResult.textContent = "Verifying GPU runtime, FFmpeg, storage, and memory profile…";
    try {
      const res = await apiFetch(`${API_BASE}/api/engines/verify`, { method: "POST" });
      const data = await res.json();
      if (data.success && data.verification) {
        const v = data.verification;
        verificationList.classList.remove("hidden");
        verificationList.innerHTML = (v.checks || []).map(c =>
          `<div class="verify-item"><span>${c.ok ? "✅" : "⚠️"}</span><div><strong>${escapeHtml(c.name)}</strong>: ${escapeHtml(c.detail)}</div></div>`
        ).join("");
        envActionResult.textContent = v.long_video_ready
          ? "Media Factory verified: GPU engine + FFmpeg multi-scene assembly are ready!"
          : (v.ok ? "Active engine ready; install FFmpeg for multi-scene video stitching." : "Verification found items that need attention below.");
      }
    } catch (e) {
      envActionResult.textContent = "Verification error: " + e.message;
    } finally {
      envVerifyBtn.disabled = false;
    }
  });

  envDetectBtn?.addEventListener("click", async () => {
    envDetectBtn.disabled = true;
    envActionResult.textContent = "Scanning this computer for local generation engines…";
    try {
      const res = await apiFetch(`${API_BASE}/api/engines/detect`, { method: "POST" });
      const data = await res.json();
      envActionResult.textContent = (data.wan2gp_detect && data.wan2gp_detect.message) || "Engine scan complete.";
      await checkSystemConnection();
      loadSettingsView();
      renderHardwareAndEngines(data);
    } catch (e) {
      envActionResult.textContent = "Detection failed: " + e.message;
    } finally {
      envDetectBtn.disabled = false;
    }
  });

  envInstallBtn?.addEventListener("click", () => triggerEngineInstall("wan2gp"));

  // ── SETTINGS VIEW ──
  function loadSettingsView() {
    if (!configData) return;
    settingsEngine.value = configData.engine || "wan2gp";
    settingsRoot.value = configData.root || "";
    settingsApp.value = configData.app_dir || "";
    settingsPython.value = configData.python || "";
    settingsModel.value = configData.model_type || "ltx2_distilled_gguf_q4_k_m";
    settingsImageModel.value = configData.image_model_type || "flux_schnell";
    if (settingsEditModel) settingsEditModel.value = configData.image_edit_model_type || "flux_dev_kontext";
    settingsMemoryProfile.value = String(configData.memory_profile || "auto");
    settingsResolution.value = settingsEngine.value === "draw_things"
      ? (configData.draw_things_resolution || "768x512")
      : (configData.resolution || "416x240");
    settingsImageResolution.value = settingsEngine.value === "draw_things"
      ? (configData.draw_things_image_resolution || "768x512")
      : (configData.image_resolution || "1280x720");
    settingsSteps.value = configData.num_inference_steps || 8;
    settingsImageSteps.value = configData.image_num_inference_steps || 10;
    settingsLength.value = configData.video_length || 49;
    if (settingsSceneDuration) settingsSceneDuration.value = configData.default_scene_duration_seconds || 5;
    if (settingsFps) settingsFps.value = configData.video_fps || 24;
    if (settingsLlmProvider) settingsLlmProvider.value = configData.llm_provider_override || "";
    if (settingsLlmModel) settingsLlmModel.value = configData.llm_model_override || "";
    settingsEnhance.checked = configData.enhance_prompt !== false;
    if (settingsContinuityChain) settingsContinuityChain.checked = configData.continuity_chain_frames !== false;
    if (settingsReleaseOllamaVram) settingsReleaseOllamaVram.checked = configData.release_idle_ollama_vram !== false;
    settingsDrawThingsCLI.value = configData.draw_things_cli || "";
    settingsDrawThingsModels.value = configData.draw_things_models_dir || "";
    settingsDrawThingsImageModel.value = configData.draw_things_image_model || "flux_2_klein_4b_q6p.ckpt";
    settingsDrawThingsVideoModel.value = configData.draw_things_video_model || "";
    if (settingsComfyuiUrl) settingsComfyuiUrl.value = configData.comfyui_url || "http://127.0.0.1:8188";
    if (settingsComfyuiDir) settingsComfyuiDir.value = configData.comfyui_dir || "";
    if (settingsMfluxCli) settingsMfluxCli.value = configData.mflux_cli || "";
    if (settingsMlxImageModel) settingsMlxImageModel.value = configData.mlx_image_model || "schnell";
    if (settingsMlxVideoModel) settingsMlxVideoModel.value = configData.mlx_video_module || "mlx_video.ltx_2.generate";
    if (settingsSdcppCli) settingsSdcppCli.value = configData.sdcpp_cli || "";
    if (settingsSdcppModels) settingsSdcppModels.value = configData.sdcpp_models_dir || "";
    syncEngineSettings();
  }

  function syncEngineSettings() {
    const engine = settingsEngine.value;
    const isWan = engine === "wan2gp";
    wan2gpPathFields.classList.toggle("hidden", !isWan);
    wan2gpModelSettings.classList.toggle("hidden", !isWan);
    drawThingsSettings.classList.toggle("hidden", engine !== "draw_things");
    if (comfyuiSettings) comfyuiSettings.classList.toggle("hidden", engine !== "comfyui");
    if (mlxSettings) mlxSettings.classList.toggle("hidden", engine !== "mlx");
    if (sdcppSettings) sdcppSettings.classList.toggle("hidden", engine !== "sdcpp");
    [settingsRoot, settingsApp, settingsPython, settingsModel].forEach(input => {
      input.required = isWan;
    });
  }

  settingsEngine.addEventListener("change", syncEngineSettings);

  settingsForm.addEventListener("submit", async e => {
    e.preventDefault();
    const saveBtn = document.getElementById("settings-save-btn");
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving Configuration Override...";

    const payload = {
      ...configData,
      engine: settingsEngine.value,
      root: settingsRoot.value.trim(),
      app_dir: settingsApp.value.trim(),
      python: settingsPython.value.trim(),
      model_type: settingsModel.value.trim(),
      image_model_type: settingsImageModel.value.trim(),
      image_edit_model_type: settingsEditModel ? settingsEditModel.value.trim() : (configData.image_edit_model_type || "flux_dev_kontext"),
      memory_profile: settingsMemoryProfile.value,
      resolution: settingsResolution.value.trim(),
      image_resolution: settingsImageResolution.value.trim(),
      num_inference_steps: parseInt(settingsSteps.value, 10),
      image_num_inference_steps: parseInt(settingsImageSteps.value, 10),
      video_length: parseInt(settingsLength.value, 10),
      default_scene_duration_seconds: settingsSceneDuration ? parseFloat(settingsSceneDuration.value) : 5,
      video_fps: settingsFps ? parseInt(settingsFps.value, 10) : 24,
      llm_provider_override: settingsLlmProvider ? settingsLlmProvider.value.trim() : "",
      llm_model_override: settingsLlmModel ? settingsLlmModel.value.trim() : "",
      enhance_prompt: settingsEnhance.checked,
      continuity_chain_frames: settingsContinuityChain ? settingsContinuityChain.checked : true,
      release_idle_ollama_vram: settingsReleaseOllamaVram ? settingsReleaseOllamaVram.checked : true,
      draw_things_resolution: settingsResolution.value.trim(),
      draw_things_image_resolution: settingsImageResolution.value.trim(),
      draw_things_cli: settingsDrawThingsCLI.value.trim(),
      draw_things_models_dir: settingsDrawThingsModels.value.trim(),
      draw_things_video_model: settingsDrawThingsVideoModel.value.trim(),
      draw_things_image_model: settingsDrawThingsImageModel.value.trim(),
      comfyui_url: settingsComfyuiUrl ? settingsComfyuiUrl.value.trim() : (configData.comfyui_url || "http://127.0.0.1:8188"),
      comfyui_dir: settingsComfyuiDir ? settingsComfyuiDir.value.trim() : "",
      mflux_cli: settingsMfluxCli ? settingsMfluxCli.value.trim() : "",
      mlx_image_model: settingsMlxImageModel ? settingsMlxImageModel.value.trim() : "schnell",
      mlx_video_module: settingsMlxVideoModel ? settingsMlxVideoModel.value.trim() : "mlx_video.ltx_2.generate",
      sdcpp_cli: settingsSdcppCli ? settingsSdcppCli.value.trim() : "",
      sdcpp_models_dir: settingsSdcppModels ? settingsSdcppModels.value.trim() : "",
    };

    try {
      const res = await apiFetch(`${API_BASE}/api/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        alert("Settings saved.");
        await checkSystemConnection();
        loadEnvironmentPanel();
      } else {
        alert("Failed to save settings: " + data.error);
      }
    } catch (_) {
      alert("Connection error while saving settings.");
    } finally {
      saveBtn.disabled = false;
      saveBtn.innerHTML = "<span>💾</span> Save Settings";
    }
  });

  // ── INITIALIZE ON BOOT ──
  checkAuthStatus();
});
