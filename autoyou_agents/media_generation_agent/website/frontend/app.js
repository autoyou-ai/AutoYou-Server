// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

document.addEventListener("DOMContentLoaded", () => {
  // Navigation elements
  const navItems = document.querySelectorAll(".nav-item");
  const viewSections = document.querySelectorAll(".view-section");
  
  // Form & Input elements
  const generationForm = document.getElementById("generation-form");
  const promptInput = document.getElementById("prompt");
  const mediaTypeSelect = document.getElementById("media-type");
  const modelTypeSelect = document.getElementById("model-type");
  const resolutionSelect = document.getElementById("resolution");
  const numStepsInput = document.getElementById("num-steps");
  const videoLengthInput = document.getElementById("video-length");
  const framesGroup = document.getElementById("frames-group");
  const seedInput = document.getElementById("seed");
  
  const enhanceBtn = document.getElementById("enhance-prompt-btn");
  const optimizedBox = document.getElementById("optimized-prompt-box");
  const optimizedText = document.getElementById("optimized-prompt-text");
  
  // Status Sidebar elements
  const statusDot = document.getElementById("status-dot");
  const statusLabel = document.getElementById("status-label");
  const statusDetail = document.getElementById("status-detail");
  
  // Active Jobs & History elements
  const activeJobsList = document.getElementById("active-jobs-list");
  const galleryGrid = document.getElementById("gallery-grid-container");
  const galleryEmpty = document.getElementById("gallery-empty");
  const filterTabs = document.querySelectorAll(".filter-tab");
  
  // Environment elements
  const envPlatformLine = document.getElementById("env-platform-line");
  const envGuidanceLine = document.getElementById("env-guidance-line");
  const envDetectBtn = document.getElementById("env-detect-btn");
  const envInstallBtn = document.getElementById("env-install-btn");
  const envActionResult = document.getElementById("env-action-result");
  const envInstallLog = document.getElementById("env-install-log");
  let envInstallPollTimer = null;

  // Settings elements
  const settingsForm = document.getElementById("settings-form");
  const settingsRoot = document.getElementById("settings-root");
  const settingsApp = document.getElementById("settings-app");
  const settingsPython = document.getElementById("settings-python");
  const settingsModel = document.getElementById("settings-model");
  const settingsImageModel = document.getElementById("settings-image-model");
  const settingsResolution = document.getElementById("settings-resolution");
  const settingsImageResolution = document.getElementById("settings-image-resolution");
  const settingsSteps = document.getElementById("settings-steps");
  const settingsImageSteps = document.getElementById("settings-image-steps");
  const settingsLength = document.getElementById("settings-length");
  const settingsEnhance = document.getElementById("settings-enhance");

  // Modal elements
  const mediaModal = document.getElementById("media-modal");
  const modalCloseBtn = document.getElementById("modal-close-btn");
  const modalMediaContent = document.getElementById("modal-media-content");
  const modalPrompt = document.getElementById("modal-prompt");
  const modalMeta = document.getElementById("modal-meta");

  // Auth elements
  const loginGate = document.getElementById("login-gate");
  const mainLayout = document.getElementById("main-layout");
  const otpInput = document.getElementById("otp-input");
  const otpSubmitBtn = document.getElementById("otp-submit-btn");
  const otpError = document.getElementById("otp-error");
  const logoutBtn = document.getElementById("logout-btn");

  // State Management
  let currentView = "dashboard-view";
  let activeJobs = {}; // ID -> Job element
  let galleryFilter = "all";
  let configData = {};
  let pollingInterval = null;
  let currentOptimizedPrompt = "";

  const IMAGE_MODEL_IDS = new Set(["flux_schnell", "flux", "z_image", "qwen_image_20B", "qwen_image_2512_20B"]);
  const VIDEO_MODEL_IDS = new Set(["ltx2_distilled_gguf_q4_k_m", "ltx2_distilled", "t2v", "t2v_2_2"]);

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
    if (link) {
      link.href = adminLoginUrl();
    }
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
    if (value && optionExists(select, value)) {
      select.value = value;
      return true;
    }
    return false;
  }

  function usableOptimizedPrompt() {
    const text = currentOptimizedPrompt.trim();
    if (!text) return "";
    if (text.startsWith("Generating optimized")) return "";
    if (text.startsWith("AI Prompt Enhancement failed")) return "";
    return text;
  }

  function setNumberInput(input, value, fallback) {
    const parsed = parseInt(value, 10);
    input.value = Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  }

  function applyGenerationDefaultsForMediaType() {
    if (mediaTypeSelect.value === "image") {
      setSelectIfPossible(modelTypeSelect, configData.image_model_type) ||
        setSelectIfPossible(modelTypeSelect, "flux_schnell");
      setSelectIfPossible(resolutionSelect, configData.image_resolution || "1280x720");
      setNumberInput(numStepsInput, configData.image_num_inference_steps, 10);
      framesGroup.classList.add("hidden");
      return;
    }

    setSelectIfPossible(modelTypeSelect, configData.model_type) ||
      setSelectIfPossible(modelTypeSelect, "ltx2_distilled_gguf_q4_k_m");
    setSelectIfPossible(resolutionSelect, configData.resolution || "416x240");
    setNumberInput(numStepsInput, configData.num_inference_steps, 8);
    setNumberInput(videoLengthInput, configData.video_length, 49);
    framesGroup.classList.remove("hidden");
  }

  // ── SESSION MANAGEMENT & AUTH HELPERS ──
  const SESSION_TOKEN_KEY = 'autoyou_media_token';
  
  function _storedToken() { try { return localStorage.getItem(SESSION_TOKEN_KEY) || ''; } catch (_) { return ''; } }
  function _saveToken(t) { try { if (t) localStorage.setItem(SESSION_TOKEN_KEY, t); else localStorage.removeItem(SESSION_TOKEN_KEY); } catch (_) {} }
  function _clearToken() { _saveToken(''); }
  
  async function apiFetch(url, options = {}) {
    const token = _storedToken();
    const merged = Object.assign({}, options);
    const base = merged.headers instanceof Headers
        ? Object.fromEntries(merged.headers.entries())
        : Object.assign({}, merged.headers || {});
    if (token && !base['Authorization']) {
      base['Authorization'] = 'Bearer ' + token;
    }
    merged.headers = base;
    
    const res = await fetch(url, merged);
    if (res.status === 401 && !url.includes('/api/auth/status') && !url.includes('/api/auth/login')) {
      _clearToken();
      checkAuthStatus();
      throw new Error("Session expired. Please log in again.");
    }
    return res;
  }

  async function checkAuthStatus() {
    try {
      const res = await apiFetch("/agent/media_generation_agent/api/auth/status");
      const data = await res.json();
      if (data.success) {
        if (data.authenticated) {
          loginGate.classList.add("hidden");
          mainLayout.classList.remove("hidden");
          loginGate.inert = true;
          mainLayout.inert = false;
          
          // Trigger data loading upon authentication
          checkSystemConnection();
          pollActiveJobs();
          if (pollingInterval) clearInterval(pollingInterval);
          pollingInterval = setInterval(pollActiveJobs, 5000);
          return true;
        } else {
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
            if (loginCopy) loginCopy.textContent = "Enter your 6-digit authenticator code to unlock the Media Lab.";
            if (otpRow) otpRow.style.display = "";
          } else {
            if (loginCopy) loginCopy.textContent = "Sign in through the main AutoYou admin UI, then return here.";
            if (otpRow) otpRow.style.display = "none";
          }
          return false;
        }
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
      const res = await apiFetch("/agent/media_generation_agent/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ totp_code: code })
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
      await apiFetch("/agent/media_generation_agent/api/auth/logout", { method: "POST" });
    } catch (e) {}
    _clearToken();
    location.reload();
  }

  // Bind Events for Auth
  if (otpSubmitBtn) otpSubmitBtn.addEventListener("click", handleLogin);
  if (otpInput) {
    otpInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") handleLogin();
    });
  }
  if (logoutBtn) logoutBtn.addEventListener("click", handleLogout);
  hydrateAdminLoginLink();
  
  // ── NAVIGATION ──
  navItems.forEach(item => {
    item.addEventListener("click", () => {
      const target = item.getAttribute("data-target");
      if (target === currentView) return;
      
      // Update sidebar nav items
      navItems.forEach(nav => nav.classList.remove("active"));
      item.classList.add("active");
      
      // Toggle visibility sections
      viewSections.forEach(section => {
        if (section.id === target) {
          section.classList.add("active");
        } else {
          section.classList.remove("active");
        }
      });
      
      currentView = target;
      
      // Refresh context when switching views
      if (target === "history-view") {
        loadCreationsGallery();
      } else if (target === "settings-view") {
        loadSettingsView();
        loadEnvironmentPanel();
      }
    });
  });

  // Toggle Video length parameters based on media type selection
  mediaTypeSelect.addEventListener("change", (e) => {
    if (e.target.value === "image") {
      framesGroup.classList.add("hidden");
      if (!IMAGE_MODEL_IDS.has(modelTypeSelect.value)) {
        setSelectIfPossible(modelTypeSelect, configData.image_model_type) ||
          setSelectIfPossible(modelTypeSelect, "flux_schnell");
      }
      if (resolutionSelect.value === "416x240") {
        setSelectIfPossible(resolutionSelect, configData.image_resolution || "1280x720");
      }
      setNumberInput(numStepsInput, configData.image_num_inference_steps, 10);
    } else {
      framesGroup.classList.remove("hidden");
      if (!VIDEO_MODEL_IDS.has(modelTypeSelect.value)) {
        setSelectIfPossible(modelTypeSelect, configData.model_type) ||
          setSelectIfPossible(modelTypeSelect, "ltx2_distilled_gguf_q4_k_m");
      }
      setSelectIfPossible(resolutionSelect, configData.resolution || "416x240");
      setNumberInput(numStepsInput, configData.num_inference_steps, 8);
      setNumberInput(videoLengthInput, configData.video_length, 49);
    }
  });

  // ── CORE LOADS & DIAGNOSTIC POLLING ──
  async function checkSystemConnection() {
    try {
      const res = await apiFetch("/agent/media_generation_agent/api/config");
      const data = await res.json();
      
      if (data.success) {
        configData = data.config;
        applyGenerationDefaultsForMediaType();
        if (data.connected) {
          statusDot.className = "status-dot green";
          statusLabel.textContent = "Wan2GP: Connected";
          statusDetail.textContent = `Model default loaded: ${configData.model_type || 'unspecified'}. System ready.`;
        } else {
          statusDot.className = "status-dot red";
          statusLabel.textContent = "Wan2GP: Offline";
          
          let failReason = "Local directories are missing or incorrect.";
          if (!data.diagnostic.root_exists) {
            failReason = "Wan2GP Root folder does not exist.";
          } else if (!data.diagnostic.python_exists) {
            failReason = "Wan2GP Python path is invalid.";
          }
          statusDetail.textContent = `${failReason} Check Configuration settings page.`;
        }
      }
    } catch (e) {
      statusDot.className = "status-dot red";
      statusLabel.textContent = "Wan2GP: Error";
      statusDetail.textContent = "Could not check local setup.";
    }
  }

  // ── AI PROMPT ENHANCEMENT ──
  enhanceBtn.addEventListener("click", async () => {
    const rawPrompt = promptInput.value.trim();
    if (!rawPrompt) {
      alert("Please write a raw prompt description before applying AI enhancement.");
      return;
    }
    
    optimizedBox.classList.remove("hidden");
    optimizedText.textContent = "Generating optimized cinematic script details...";
    currentOptimizedPrompt = "";
    
    try {
      const res = await apiFetch("/agent/media_generation_agent/api/enhance-prompt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: rawPrompt,
          media_type: mediaTypeSelect.value
        })
      });
      const data = await res.json();
      if (data.success) {
        optimizedText.textContent = data.enhanced_prompt;
        currentOptimizedPrompt = data.enhanced_prompt || "";
      } else {
        optimizedText.textContent = "AI Prompt Enhancement failed. Will proceed using standard local visual templates.";
        currentOptimizedPrompt = "";
      }
    } catch (e) {
      optimizedText.textContent = "AI Prompt Enhancement failed. Will proceed using standard local visual templates.";
      currentOptimizedPrompt = "";
    }
  });

  // ── JOB SUBMISSION ──
  generationForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    
    const prompt = promptInput.value.trim();
    if (!prompt) return;
    
    const submitBtn = document.getElementById("generate-submit-btn");
    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting Generation Request...";
    
    const payload = {
      prompt: prompt,
      media_type: mediaTypeSelect.value,
      model_type: modelTypeSelect.value,
      resolution: resolutionSelect.value,
      steps: parseInt(numStepsInput.value),
      frames: mediaTypeSelect.value === "video" ? parseInt(videoLengthInput.value) : null,
      seed: parseInt(seedInput.value),
      optimized_prompt: usableOptimizedPrompt()
    };
    
    try {
      const res = await apiFetch("/agent/media_generation_agent/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      
      if (data.success) {
        // Clear inputs and reset UI
        promptInput.value = "";
        optimizedBox.classList.add("hidden");
        currentOptimizedPrompt = "";
        
        // Show success animation or trigger polling
        pollActiveJobs();
      } else {
        alert("Error launching generation job: " + data.error);
      }
    } catch (err) {
      alert("Unexpected connection error: Could not reach the local generator.");
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = "<span>🚀</span> Kick off Generation";
    }
  });

  // ── LIVE JOB POLLING ──
  async function pollActiveJobs() {
    try {
      const res = await apiFetch("/agent/media_generation_agent/api/history");
      const data = await res.json();
      
      if (!data.success) return;
      
      const runningJobs = data.history.filter(item => item.status === "generating");
      
      if (runningJobs.length > 0) {
        // Render running jobs
        activeJobsList.innerHTML = "";
        runningJobs.forEach(job => {
          const card = document.createElement("div");
          card.className = "job-card";
          card.innerHTML = `
            <div class="job-loader"></div>
            <div class="job-details">
              <div class="job-title">[${escapeHtml(job.media_type.toUpperCase())}] Generating Media #${escapeHtml(job.id)}</div>
              <p class="job-status">Please wait while your local generator works...</p>
              <div class="prompt-text" style="font-size: 11px; margin-top: 6px; opacity: 0.8;">"${escapeHtml(job.original_prompt.substring(0, 80))}..."</div>
            </div>
          `;
          activeJobsList.appendChild(card);
        });
        
        // Start polling if not already started
        if (!pollingInterval) {
          pollingInterval = setInterval(pollActiveJobs, 5000);
        }
      } else {
        const latest = data.history && data.history.length ? data.history[0] : null;
        if (latest && latest.status === "failed") {
          activeJobsList.innerHTML = `
            <div class="job-card">
              <div class="job-details">
                <div class="job-title">Last generation failed: #${escapeHtml(latest.id)}</div>
                <p class="job-status">${escapeHtml((latest.error_message || "Wan2GP did not produce an output file.").substring(0, 220))}</p>
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
        // Clear active jobs list and reset message
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
          // Refresh gallery if active
          if (currentView === "history-view") {
            loadCreationsGallery();
          }
        }
      }
    } catch (e) {
      console.error("Job status polling failed.", e);
    }
  }

  // ── GALLERY OF CREATIONS ──
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
      const url = filterType ? `/agent/media_generation_agent/api/history?media_type=${filterType}` : "/agent/media_generation_agent/api/history";
      
      const res = await apiFetch(url);
      const data = await res.json();
      
      if (!data.success) return;
      
      const completedItems = data.history.filter(item => item.status !== "generating");
      
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
        
        let previewHtml = "";
        const fileUrl = `/agent/media_generation_agent/media/${item.media_type}/${item.file_name}`;
        
        if (item.status === "failed") {
          previewHtml = `
            <div class="media-preview-container" style="background-color: hsla(355, 85%, 55%, 0.15); display: flex; align-items: center; justify-content: center; text-align: center; padding: 16px;">
              <div style="color: var(--accent-error); font-size: 13px; font-weight: 600;">⚠️ Generation Failed</div>
            </div>
          `;
        } else if (isVideo) {
          previewHtml = `
            <div class="media-preview-container" data-id="${item.id}">
              <div class="media-type-badge">${badgeText}</div>
              <div class="media-play-overlay">▶</div>
              <video muted playsinline loop src="${fileUrl}"></video>
            </div>
          `;
        } else {
          previewHtml = `
            <div class="media-preview-container" data-id="${item.id}">
              <div class="media-type-badge">${badgeText}</div>
              <img src="${fileUrl}" alt="Generation preview">
            </div>
          `;
        }
        
        const timestamp = new Date(item.timestamp).toLocaleString();
        
        card.innerHTML = `
          ${previewHtml}
          <div class="card-body">
            <h3 class="card-title" title="${item.original_prompt}">${item.original_prompt}</h3>
            <div class="card-meta">
              <span>Seed: ${item.seed}</span>
              <span>${timestamp}</span>
            </div>
          </div>
          <div class="card-footer-actions">
            <button class="btn btn-danger btn-sm delete-btn" data-id="${item.id}">Delete</button>
          </div>
        `;
        
        galleryGrid.appendChild(card);
      });
      
      // Attach click events to previews for Lightbox playback
      const previews = galleryGrid.querySelectorAll(".media-preview-container");
      previews.forEach(preview => {
        preview.addEventListener("click", () => {
          const id = preview.getAttribute("data-id");
          if (id) openMediaLightbox(id);
        });
      });
      
      // Play videos on hover
      const videoPreviews = galleryGrid.querySelectorAll(".media-preview-container video");
      videoPreviews.forEach(video => {
        const container = video.parentElement;
        container.addEventListener("mouseenter", () => video.play().catch(e => {}));
        container.addEventListener("mouseleave", () => {
          video.pause();
          video.currentTime = 0;
        });
      });

      // Attach Delete events
      const deleteButtons = galleryGrid.querySelectorAll(".delete-btn");
      deleteButtons.forEach(btn => {
        btn.addEventListener("click", (e) => {
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
      const res = await apiFetch(`/agent/media_generation_agent/api/history/${id}`, {
        method: "DELETE"
      });
      const data = await res.json();
      if (data.success) {
        loadCreationsGallery();
      } else {
        alert("Failed to delete creation: " + data.error);
      }
    } catch (e) {
      alert("Unexpected error attempting delete.");
    }
  }

  // ── MODAL LIGHTBOX ──
  async function openMediaLightbox(id) {
    try {
      const res = await apiFetch(`/agent/media_generation_agent/api/history/${id}`);
      const data = await res.json();
      
      if (!data.success) return;
      const item = data.item;
      
      const isVideo = item.media_type === "video";
      const fileUrl = `/agent/media_generation_agent/media/${item.media_type}/${item.file_name}`;
      
      // Render media inside Lightbox
      if (isVideo) {
        modalMediaContent.innerHTML = `<video controls autoplay loop src="${fileUrl}"></video>`;
      } else {
        modalMediaContent.innerHTML = `<img src="${fileUrl}" alt="Detail view">`;
      }
      
      // Render footer
      document.getElementById("modal-title").textContent = isVideo ? "Optimized Cinematic Prompt" : "Optimized Image Prompt";
      modalPrompt.textContent = item.optimized_prompt || item.original_prompt;
      
      // Meta chips
      modalMeta.innerHTML = `
        <div class="meta-chip"><span class="meta-chip-label">ID:</span> #${item.id}</div>
        <div class="meta-chip"><span class="meta-chip-label">Type:</span> ${item.media_type.toUpperCase()}</div>
        <div class="meta-chip"><span class="meta-chip-label">Model:</span> ${item.model_type || 'Default'}</div>
        <div class="meta-chip"><span class="meta-chip-label">Resolution:</span> ${item.resolution || 'Default'}</div>
        <div class="meta-chip"><span class="meta-chip-label">Steps:</span> ${item.steps || 'Default'}</div>
        <div class="meta-chip"><span class="meta-chip-label">Seed:</span> ${item.seed}</div>
      `;
      
      mediaModal.classList.remove("hidden");
    } catch (e) {
      console.error("Lightbox rendering failed.", e);
    }
  }

  // Close lightbox modal
  modalCloseBtn.addEventListener("click", () => {
    mediaModal.classList.add("hidden");
    modalMediaContent.innerHTML = ""; // Stop audio/video playing
  });
  
  document.querySelector(".modal-backdrop").addEventListener("click", () => {
    mediaModal.classList.add("hidden");
    modalMediaContent.innerHTML = "";
  });

  // ── SETTINGS VIEW ──
  // ── WAN2GP ENVIRONMENT (detect / machine-dependent install) ──
  async function loadEnvironmentPanel() {
    if (!envPlatformLine) return;
    try {
      const res = await apiFetch("/agent/media_generation_agent/api/wan2gp/environment");
      const data = await res.json();
      if (!data.success) throw new Error(data.error || "Environment check failed");
      renderEnvironmentPanel(data.environment);
    } catch (e) {
      envPlatformLine.textContent = "Could not check this computer's Wan2GP environment.";
    }
  }

  function renderEnvironmentPanel(env) {
    const platform = env.platform || {};
    const configured = env.configured || {};
    const ready = configured.app_dir_exists && configured.python_exists;
    const osLabel = platform.system === "Darwin" ? "macOS" : (platform.system || "Unknown OS");
    const gpuLabel = platform.has_nvidia ? "NVIDIA GPU" : (platform.machine || "");
    envPlatformLine.textContent = ready
      ? `${osLabel} (${gpuLabel}) — Wan2GP is set up and ready at ${configured.app_dir}.`
      : `${osLabel} (${gpuLabel}) — Wan2GP is not set up yet on this computer.`;
    if (ready) {
      envGuidanceLine.textContent = "";
      envInstallBtn.classList.add("hidden");
    } else if (env.discovered && env.discovered.python) {
      envGuidanceLine.textContent = `Found an existing install at ${env.discovered.app_dir}. Use "Detect installation" to start using it.`;
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
      envActionResult.textContent = `Installing… current step: ${install.step || "starting"}. This downloads several GB and can take a long time.`;
      envInstallLog.classList.remove("hidden");
      envInstallLog.textContent = install.log_tail || "";
      envInstallLog.scrollTop = envInstallLog.scrollHeight;
      if (!envInstallPollTimer) {
        envInstallPollTimer = setInterval(pollInstallStatus, 4000);
      }
    } else {
      envInstallBtn.disabled = false;
      if (envInstallPollTimer) {
        clearInterval(envInstallPollTimer);
        envInstallPollTimer = null;
      }
      if (status === "completed") {
        envActionResult.textContent = "Install completed. Settings now point at the managed install.";
        envInstallLog.classList.remove("hidden");
        envInstallLog.textContent = install.log_tail || "";
        checkSystemConnection();
      } else if (status === "failed") {
        envActionResult.textContent = `Install failed: ${install.error || "see log below"}`;
        envInstallLog.classList.remove("hidden");
        envInstallLog.textContent = install.log_tail || "";
      }
    }
  }

  async function pollInstallStatus() {
    try {
      const res = await apiFetch("/agent/media_generation_agent/api/wan2gp/install/status");
      const data = await res.json();
      if (data.success) renderInstallState(data.install || {});
    } catch (e) { /* keep polling */ }
  }

  if (envDetectBtn) {
    envDetectBtn.addEventListener("click", async () => {
      envDetectBtn.disabled = true;
      envActionResult.textContent = "Searching this computer for a Wan2GP installation…";
      try {
        const res = await apiFetch("/agent/media_generation_agent/api/wan2gp/detect", { method: "POST" });
        const data = await res.json();
        envActionResult.textContent = data.message || (data.success ? "Installation detected and saved." : "No installation found.");
        if (data.success) {
          await checkSystemConnection();
          loadSettingsView();
          loadEnvironmentPanel();
        }
      } catch (e) {
        envActionResult.textContent = "Detection failed: " + e.message;
      } finally {
        envDetectBtn.disabled = false;
      }
    });
  }

  if (envInstallBtn) {
    envInstallBtn.addEventListener("click", async () => {
      if (!confirm("Install Wan2GP on this computer? This downloads several gigabytes (PyTorch + dependencies) and can take a long time.")) return;
      envInstallBtn.disabled = true;
      envActionResult.textContent = "Starting install…";
      try {
        const res = await apiFetch("/agent/media_generation_agent/api/wan2gp/install", { method: "POST" });
        const data = await res.json();
        envActionResult.textContent = data.message || "";
        if (data.success) {
          renderInstallState({ status: "running", step: "starting" });
        } else {
          envInstallBtn.disabled = false;
        }
      } catch (e) {
        envActionResult.textContent = "Install request failed: " + e.message;
        envInstallBtn.disabled = false;
      }
    });
  }

  function loadSettingsView() {
    if (!configData.root) return;

    settingsRoot.value = configData.root || "";
    settingsApp.value = configData.app_dir || "";
    settingsPython.value = configData.python || "";
    
    settingsModel.value = configData.model_type || "ltx2_distilled_gguf_q4_k_m";
    settingsImageModel.value = configData.image_model_type || "flux_schnell";
    settingsResolution.value = configData.resolution || "416x240";
    settingsImageResolution.value = configData.image_resolution || "1280x720";
    settingsSteps.value = configData.num_inference_steps || 8;
    settingsImageSteps.value = configData.image_num_inference_steps || 10;
    settingsLength.value = configData.video_length || 49;
    settingsEnhance.checked = configData.enhance_prompt !== false;
  }

  settingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    
    const saveBtn = document.getElementById("settings-save-btn");
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving Configuration Override...";
    
    const payload = {
      root: settingsRoot.value.trim(),
      app_dir: settingsApp.value.trim(),
      python: settingsPython.value.trim(),
      model_type: settingsModel.value.trim(),
      image_model_type: settingsImageModel.value.trim(),
      resolution: settingsResolution.value.trim(),
      image_resolution: settingsImageResolution.value.trim(),
      num_inference_steps: parseInt(settingsSteps.value),
      image_num_inference_steps: parseInt(settingsImageSteps.value),
      video_length: parseInt(settingsLength.value),
      enhance_prompt: settingsEnhance.checked
    };
    
    try {
      const res = await apiFetch("/agent/media_generation_agent/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      
      if (data.success) {
      alert("Settings saved.");
        checkSystemConnection();
      } else {
        alert("Failed to save settings: " + data.error);
      }
    } catch (err) {
      alert("Connection error while saving settings.");
    } finally {
      saveBtn.disabled = false;
      saveBtn.innerHTML = "<span>💾</span> Save Settings";
    }
  });

  // ── INITIALIZE ON BOOT ──
  checkAuthStatus();
});
