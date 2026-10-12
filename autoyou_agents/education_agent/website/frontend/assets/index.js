(function () {
  "use strict";

  var bootstrap = window.__BOOTSTRAP__ || {};
  var tokenKey = "autoyou.education_agent.token";
  var proxiedThroughAdmin = /^\/agent\//.test(window.location.pathname);
  var state = {
    auth: bootstrap.auth || {},
    token: "",
    paused: false,
    selectedSession: "",
    pollTimer: null,
    lastSnapshotAt: 0,
    lastSnapshot: null,
    micBusy: false,
    feeds: {},
    expandedFeeds: {},
    viewer: {
      key: "",
      fit: "contain",
      zoom: 1,
      x: 0,
      y: 0,
      dragging: false,
      pointerId: null,
      startX: 0,
      startY: 0,
      startClientX: 0,
      startClientY: 0
    }
  };

  try {
    state.token = localStorage.getItem(tokenKey) || "";
  } catch (_) {
    state.token = "";
  }

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[ch];
    });
  }

  function headers(extra) {
    var out = { "Content-Type": "application/json" };
    if (state.token) {
      out.Authorization = "Bearer " + state.token;
    }
    Object.assign(out, extra || {});
    return out;
  }

  function formatTime(ms) {
    var value = Number(ms || 0);
    if (!value) {
      return "";
    }
    try {
      return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch (_) {
      return "";
    }
  }

  function shortId(value) {
    var text = String(value || "");
    if (text.length <= 14) {
      return text || "session";
    }
    return text.slice(0, 6) + "..." + text.slice(-6);
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function clearImage(image) {
    image.hidden = true;
    image.removeAttribute("src");
    image.removeAttribute("srcset");
  }

  function showImage(image, src, alt) {
    image.alt = alt || "Live video frame";
    image.hidden = false;
    if (image.getAttribute("src") !== src) {
      image.setAttribute("src", src);
    }
  }

  function focusHint(isExpanded) {
    return '<span class="feed-focus-hint" aria-hidden="true">' + (isExpanded ? "Collapse" : "Open") + '</span>';
  }

  function findFeedTrigger(key) {
    return Array.prototype.find.call(
      document.querySelectorAll(".feed-media[data-viewer-key]"),
      function (item) { return item.dataset.viewerKey === key; }
    );
  }

  async function api(path, options) {
    var opts = options || {};
    var response = await fetch(path, Object.assign({}, opts, {
      headers: headers(opts.headers)
    }));
    var payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      payload = { success: response.ok };
    }
    if (!response.ok) {
      var err = new Error(payload.error || "Request failed");
      err.status = response.status;
      err.payload = payload;
      throw err;
    }
    return payload;
  }

  function setAuthView(authenticated) {
    $("auth-gate").classList.toggle("hidden", authenticated);
    $("dashboard").classList.toggle("hidden", !authenticated);
    $("logout-button").classList.toggle("hidden", !authenticated);
    $("pause-button").classList.toggle("hidden", !authenticated);
    $("refresh-button").classList.toggle("hidden", !authenticated);
    $("auth-pill").textContent = authenticated ? "Unlocked" : "Locked";
    $("auth-pill").classList.toggle("good", authenticated);
  }

  function statusText(on) {
    return on ? "On" : "Off";
  }

  function videoSourceLabels(value) {
    var labels = {
      remote_desktop: "Screen",
      camera: "Webcam",
      api: "API video",
      video_file: "Video file"
    };
    return (Array.isArray(value) ? value : []).map(function (source) {
      var key = String(source || "");
      return labels[key] || key.replace(/_/g, " ");
    }).filter(Boolean);
  }

  function renderMetrics(snapshot) {
    var sessions = (((snapshot.webrtc || {}).sessions) || []);
    var datachannels = sessions.filter(function (item) { return item.datachannel_connected; }).length;
    var remoteVideo = sessions.filter(function (item) { return item.video_active || ((item.latest_video_frame || {}).active); }).length;
    var voice = sessions.filter(function (item) { return item.voice_call_active || item.audio_active; }).length;
    $("metric-sessions").textContent = String(sessions.length);
    $("metric-datachannels").textContent = String(datachannels);
    $("metric-video").textContent = String(remoteVideo);
    $("metric-voice").textContent = String(voice);
    $("connection-line").textContent = "Updated " + (formatTime(snapshot.timestamp_ms) || "now");
  }

  function renderSessionFilter(sessions) {
    var select = $("session-filter");
    var existing = state.selectedSession;
    var html = '<option value="">All live clients</option>';
    sessions.forEach(function (session) {
      var id = String(session.session_id || "");
      var name = String(session.client_display_name || "");
      var label = name ? name + " (" + shortId(id) + ")" : shortId(id);
      html += '<option value="' + escapeHtml(id) + '">' + escapeHtml(label) + "</option>";
    });
    select.innerHTML = html;
    if (existing && sessions.some(function (item) { return item.session_id === existing; })) {
      select.value = existing;
    } else {
      state.selectedSession = "";
    }
  }

  function chip(label, on, cls) {
    return '<span class="chip ' + (on ? (cls || "on") : "") + '">' + escapeHtml(label) + "</span>";
  }

  function sessionListHasPendingNameEdit() {
    var list = $("session-list");
    if (!list) {
      return false;
    }
    var active = document.activeElement;
    if (active && active.classList && active.classList.contains("client-name-input") && list.contains(active)) {
      return true;
    }
    var inputs = list.querySelectorAll(".client-name-input");
    for (var i = 0; i < inputs.length; i += 1) {
      if (inputs[i].value !== inputs[i].defaultValue) {
        return true;
      }
    }
    return false;
  }

  function renderSessions(snapshot) {
    var sessions = (((snapshot.webrtc || {}).sessions) || []);
    var clientIdentity = snapshot.client_identity || {};
    $("client-name-note").textContent = clientIdentity.history_note
      || "Names are live-only until you choose to store them with chat history.";
    renderSessionFilter(sessions);
    if (sessionListHasPendingNameEdit()) {
      return;
    }
    if (!sessions.length) {
      $("session-list").innerHTML = '<div class="session-card muted">No active sessions</div>';
      return;
    }
    $("session-list").innerHTML = sessions.map(function (session) {
      var bg = session.background_audio || {};
      var vr = session.video_recording || {};
      var displayName = String(session.client_display_name || "");
      var ownerKey = String(session.owner_key || "");
      var nameEditor = ownerKey
        ? '<div class="name-editor"><label>Name<input class="client-name-input" value="' + escapeHtml(displayName) + '" maxlength="120" aria-label="Client name"></label><button class="button secondary save-client-name" type="button" data-owner-key="' + escapeHtml(ownerKey) + '">Save</button></div><p class="muted">Leave blank and save to remove the server override.</p>'
        : "";
      return [
        '<article class="session-card">',
        '<div class="row"><strong>' + escapeHtml(displayName || shortId(session.session_id)) + '</strong><span class="muted">' + escapeHtml(formatTime((session.voice_status || {}).timestamp_ms || (bg || {}).timestamp_ms)) + '</span></div>',
        '<div class="chip-row">',
        chip("Peer", session.peer_connected),
        chip("Data", session.datachannel_connected),
        chip("Audio", session.audio_active || bg.active),
        chip("Video", session.video_active || (session.latest_video_frame || {}).active, "info"),
        chip("Record", session.silent_recording_active || vr.enabled, "info"),
        '</div>',
        '<div class="muted mono">' + escapeHtml(session.session_id) + '</div>',
        nameEditor,
        '</article>'
      ].join("");
    }).join("");
  }

  function renderMessages(snapshot) {
    var events = (snapshot.recent_events || []).slice(-90).reverse();
    var list = $("message-list");
    if (!events.length) {
      list.innerHTML = '<div class="message-item muted">No live client messages</div>';
      return;
    }
    list.innerHTML = events.map(function (event) {
      var direction = String(event.direction || "event");
      var session = shortId(event.session_id || event.canonical_session_id || "");
      var feed = event.feed || event.source || "";
      return [
        '<article class="message-item ' + escapeHtml(direction) + '">',
        '<div class="message-meta"><span>' + escapeHtml(direction) + ' / ' + escapeHtml(event.channel || "chat") + '</span><span>' + escapeHtml(session) + ' ' + escapeHtml(formatTime(event.timestamp_ms)) + '</span></div>',
        '<div class="message-text">' + escapeHtml(event.text || "") + '</div>',
        feed ? '<div class="message-meta"><span>' + escapeHtml(feed) + '</span><span></span></div>' : "",
        '</article>'
      ].join("");
    }).join("");
    list.scrollTop = 0;
  }

  function renderInteract(snapshot) {
    var interact = snapshot.interact || {};
    var people = interact.participants || [];
    var inputs = interact.inputs || [];
    $("interact-participants").innerHTML = people.length ? people.map(function (person) {
      var recent = Date.now() / 1000 - Number(person.last_input_at || 0) < 1.5 ? " · " + String(person.last_input || "") : "";
      return '<div class="session-card"><strong>' + escapeHtml(person.name || "Connected device") + '</strong><p class="muted">' + escapeHtml(person.mode === "interactive" ? (person.muted ? "Microphone muted" : "Microphone live") : "Watching only") + escapeHtml(recent) + '</p></div>';
    }).join("") : '<div class="session-card muted">No screen participants</div>';
    $("interact-inputs").innerHTML = inputs.length ? inputs.slice(-30).reverse().map(function (input) {
      return '<div class="message-item">' + escapeHtml(input.name || "A device") + ' sent ' + escapeHtml(input.value || "an input") + '</div>';
    }).join("") : '<div class="message-item muted">No inputs yet</div>';
  }

  function renderRecordings(snapshot) {
    var recordings = snapshot.recordings || {};
    var items = []
      .concat(recordings.active_silent_recorders || [])
      .concat(recordings.video_recordings || [])
      .concat(recordings.silent_audio_recordings || []);
    if (!items.length) {
      $("recording-list").innerHTML = '<div class="recording-card muted">No recordings</div>';
      return;
    }
    $("recording-list").innerHTML = items.slice(0, 12).map(function (item) {
      var label = item.current_path || item.path || item.output_dir || item.name || "recording";
      var detail = item.bytes_written ? String(item.bytes_written) + " bytes" : (item.kind || "");
      return [
        '<article class="recording-card">',
        '<strong class="mono">' + escapeHtml(shortId(label)) + '</strong>',
        '<div class="muted mono">' + escapeHtml(label) + '</div>',
        detail ? '<div class="muted">' + escapeHtml(detail) + '</div>' : "",
        '</article>'
      ].join("");
    }).join("");
  }

  function renderMediaStatus(snapshot) {
    var caps = snapshot.capabilities || {};
    var audio = caps.audio || {};
    var video = caps.video || {};
    var outbound = caps.outbound_video || {};
    var frames = snapshot.video_frames || {};
    var computerVideo = snapshot.computer_video || snapshot.self_video || {};
    var computerSources = videoSourceLabels(computerVideo.active_sources);
    var rows = [
      ["Live audio", statusText(audio.enabled)],
      ["Agent processing", statusText(audio.agent_processing_enabled)],
      ["Phone camera received", statusText(video.receive_enabled)],
      ["Remote frames", String(frames.active_sessions || 0)],
      ["Record video", statusText(video.record_my_video)],
      ["Computer video", computerVideo.active ? (computerSources.join(" + ") || outbound.source || "Active") : computerVideo.enabled ? "Waiting" : "Off"]
    ];
    $("media-status").innerHTML = rows.map(function (row) {
      return '<div class="status-card"><div class="row"><span class="muted">' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div></div>';
    }).join("");
    $("db-pill").textContent = "Live only";
    $("db-pill").classList.add("good");
    $("db-pill").classList.remove("warn");
  }

  function renderAudioRouting(snapshot) {
    var routing = snapshot.audio_routing || {};
    var ownerLabels = {
      connected_call: "Connected computer call",
      lobby: "Lobby audio",
      peer: "Peer Link call",
      recording: "Safety recording"
    };
    var owner = String(routing.owner || "");
    var sources = routing.configured_sources || [];
    $("mic-owner").textContent = owner ? "In use by " + (ownerLabels[owner] || owner)
      : routing.sharing_enabled !== true ? "Sharing is paused"
      : sources.indexOf("microphone") < 0 ? "Microphone capture is off in server media settings"
      : "Available for connected clients";
    if (!state.micBusy) {
      $("mic-sharing-toggle").checked = routing.sharing_enabled === true;
    }
    var count = Number(routing.connected_devices || 0);
    var local = Number(routing.same_machine_devices || 0);
    $("mic-device-summary").textContent = count + " connected device" + (count === 1 ? "" : "s")
      + " | " + local + " on this computer";
  }

  async function loadLiveAudioRouting() {
    if (!proxiedThroughAdmin) {
      $("mic-sharing-toggle").disabled = true;
      $("mic-sharing-note").textContent = "Open Education Agent through the AutoYou Admin UI to manage this microphone.";
      return;
    }
    try {
      var routing = await api("/api/webrtc/microphone-sharing");
      renderAudioRouting({ audio_routing: {
        sharing_enabled: routing.enabled,
        owner: routing.owner,
        connected_devices: routing.connected_devices,
        same_machine_devices: routing.same_machine_devices || 0,
        configured_sources: routing.configured_sources || []
      } });
      $("mic-sharing-toggle").disabled = false;
    } catch (err) {
      $("mic-sharing-toggle").disabled = true;
      $("mic-control-error").textContent = err.status === 401
        ? "Sign in to the Admin UI to manage microphone sharing."
        : err.message || "Live microphone status is unavailable.";
      $("mic-control-error").classList.remove("hidden");
    }
  }

  function viewerGeometry() {
    var canvas = $("viewer-canvas");
    var image = $("viewer-image");
    var feed = state.feeds[state.viewer.key] || {};
    var canvasWidth = Math.max(1, canvas.clientWidth);
    var canvasHeight = Math.max(1, canvas.clientHeight);
    var frameWidth = Number(image.naturalWidth || feed.width || 16);
    var frameHeight = Number(image.naturalHeight || feed.height || 9);
    var scale = state.viewer.fit === "cover"
      ? Math.max(canvasWidth / frameWidth, canvasHeight / frameHeight)
      : Math.min(canvasWidth / frameWidth, canvasHeight / frameHeight);
    var baseWidth = frameWidth * scale;
    var baseHeight = frameHeight * scale;
    return {
      baseWidth: baseWidth,
      baseHeight: baseHeight,
      maxX: Math.max(0, (baseWidth * state.viewer.zoom - canvasWidth) / 2),
      maxY: Math.max(0, (baseHeight * state.viewer.zoom - canvasHeight) / 2)
    };
  }

  function layoutViewer() {
    var image = $("viewer-image");
    var canvas = $("viewer-canvas");
    var fitButton = $("viewer-fit-button");
    if (!image.hidden) {
      var geometry = viewerGeometry();
      state.viewer.x = clamp(state.viewer.x, -geometry.maxX, geometry.maxX);
      state.viewer.y = clamp(state.viewer.y, -geometry.maxY, geometry.maxY);
      image.style.width = geometry.baseWidth + "px";
      image.style.height = geometry.baseHeight + "px";
      image.style.setProperty("--viewer-zoom", String(state.viewer.zoom));
      image.style.setProperty("--viewer-pan-x", state.viewer.x + "px");
      image.style.setProperty("--viewer-pan-y", state.viewer.y + "px");
      canvas.classList.toggle("is-pannable", geometry.maxX > 0.5 || geometry.maxY > 0.5);
    } else {
      canvas.classList.remove("is-pannable", "is-dragging");
    }
    $("viewer-zoom-level").value = Math.round(state.viewer.zoom * 100) + "%";
    $("viewer-zoom-out").disabled = state.viewer.zoom <= 1;
    $("viewer-zoom-in").disabled = state.viewer.zoom >= 5;
    fitButton.setAttribute("aria-pressed", String(state.viewer.fit === "cover"));
    fitButton.title = state.viewer.fit === "cover" ? "Fit the whole frame" : "Fill the viewer";
  }

  function syncFocusedViewer() {
    if (!state.viewer.key) {
      return;
    }
    var feed = state.feeds[state.viewer.key];
    var image = $("viewer-image");
    var empty = $("viewer-empty");
    $("video-viewer-title").textContent = feed ? feed.label : "Video feed";
    $("video-viewer-meta").textContent = feed ? (feed.meta || "Live frame") : "Feed disconnected";
    if (!feed || !feed.active || !feed.src) {
      clearImage(image);
      empty.textContent = feed ? (feed.empty || "No live frame") : "Feed disconnected";
      empty.hidden = false;
      state.viewer.zoom = 1;
      state.viewer.x = 0;
      state.viewer.y = 0;
      layoutViewer();
      return;
    }
    empty.hidden = true;
    showImage(image, feed.src, feed.alt);
    requestAnimationFrame(layoutViewer);
  }

  function openFocusedViewer(key) {
    var feed = state.feeds[key];
    if (!feed) {
      return;
    }
    state.viewer.key = key;
    state.viewer.fit = "contain";
    state.viewer.zoom = 1;
    state.viewer.x = 0;
    state.viewer.y = 0;
    var dialog = $("video-viewer");
    if (!dialog.hasAttribute("open")) {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    }
    document.body.classList.add("viewer-open");
    syncFocusedViewer();
    requestAnimationFrame(function () {
      $("viewer-canvas").focus();
      layoutViewer();
    });
  }

  function closeFocusedViewer() {
    var dialog = $("video-viewer");
    if (document.fullscreenElement === dialog && document.exitFullscreen) {
      document.exitFullscreen().catch(function () {});
    }
    if (dialog.hasAttribute("open")) {
      if (typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
        finishFocusedViewerClose();
      }
    }
  }

  function finishFocusedViewerClose() {
    var closedFeedKey = state.viewer.key;
    document.body.classList.remove("viewer-open");
    state.viewer.key = "";
    state.viewer.dragging = false;
    clearImage($("viewer-image"));
    $("viewer-empty").hidden = false;
    requestAnimationFrame(function () {
      var trigger = findFeedTrigger(closedFeedKey);
      if (trigger) {
        trigger.focus({ preventScroll: true });
      }
    });
  }

  function setViewerZoom(value) {
    state.viewer.zoom = clamp(Math.round(value * 4) / 4, 1, 5);
    layoutViewer();
  }

  function resetViewer() {
    state.viewer.fit = "contain";
    state.viewer.zoom = 1;
    state.viewer.x = 0;
    state.viewer.y = 0;
    layoutViewer();
  }

  function toggleViewerFit() {
    state.viewer.fit = state.viewer.fit === "contain" ? "cover" : "contain";
    state.viewer.x = 0;
    state.viewer.y = 0;
    layoutViewer();
  }

  function panViewer(dx, dy) {
    var geometry = viewerGeometry();
    state.viewer.x = clamp(state.viewer.x + dx, -geometry.maxX, geometry.maxX);
    state.viewer.y = clamp(state.viewer.y + dy, -geometry.maxY, geometry.maxY);
    layoutViewer();
  }

  function viewerPointerDown(event) {
    var geometry = viewerGeometry();
    if (geometry.maxX <= 0.5 && geometry.maxY <= 0.5) {
      return;
    }
    state.viewer.dragging = true;
    state.viewer.pointerId = event.pointerId;
    state.viewer.startX = state.viewer.x;
    state.viewer.startY = state.viewer.y;
    state.viewer.startClientX = event.clientX;
    state.viewer.startClientY = event.clientY;
    $("viewer-canvas").classList.add("is-dragging");
    $("viewer-canvas").setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function viewerPointerMove(event) {
    if (!state.viewer.dragging || state.viewer.pointerId !== event.pointerId) {
      return;
    }
    var geometry = viewerGeometry();
    state.viewer.x = clamp(
      state.viewer.startX + event.clientX - state.viewer.startClientX,
      -geometry.maxX,
      geometry.maxX
    );
    state.viewer.y = clamp(
      state.viewer.startY + event.clientY - state.viewer.startClientY,
      -geometry.maxY,
      geometry.maxY
    );
    layoutViewer();
  }

  function viewerPointerUp(event) {
    if (state.viewer.pointerId !== event.pointerId) {
      return;
    }
    state.viewer.dragging = false;
    state.viewer.pointerId = null;
    $("viewer-canvas").classList.remove("is-dragging");
  }

  function viewerKeyDown(event) {
    var handled = true;
    if (event.key === "+" || event.key === "=") {
      setViewerZoom(state.viewer.zoom + 0.25);
    } else if (event.key === "-" || event.key === "_") {
      setViewerZoom(state.viewer.zoom - 0.25);
    } else if (event.key === "0") {
      resetViewer();
    } else if (event.key === "f" || event.key === "F") {
      toggleViewerFit();
    } else if (event.key === "ArrowLeft") {
      panViewer(-48, 0);
    } else if (event.key === "ArrowRight") {
      panViewer(48, 0);
    } else if (event.key === "ArrowUp") {
      panViewer(0, -48);
    } else if (event.key === "ArrowDown") {
      panViewer(0, 48);
    } else {
      handled = false;
    }
    if (handled) {
      event.preventDefault();
    }
  }

  async function toggleViewerFullscreen() {
    var dialog = $("video-viewer");
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else if (dialog.requestFullscreen) {
        await dialog.requestFullscreen();
      }
    } catch (_) {}
  }

  function renderVideoFeeds(snapshot) {
    var frames = snapshot.video_frames || {};
    var sessions = (((snapshot.webrtc || {}).sessions) || []);
    var nextFeeds = {};
    var remoteSessions = sessions.filter(function (session) {
      return !state.selectedSession || session.session_id === state.selectedSession;
    });
    var selfVideo = snapshot.computer_video || snapshot.self_video || {};
    var selfCard = $("self-feed-card");
    var selfImg = $("self-video-frame");
    var selfEmpty = $("self-video-empty");
    var selfMeta = $("self-video-meta");
    var configuredSources = videoSourceLabels(selfVideo.configured_sources);
    var activeSources = videoSourceLabels(selfVideo.active_sources);
    var selfActive = !!(selfVideo.enabled && selfVideo.active && selfVideo.preview_available);
    var selfSrc = selfActive ? "./api/education/computer-frame.jpg?ts=" + Date.now() : "";
    var selfEmptyText = !selfVideo.enabled
      ? "Computer video off"
      : !selfVideo.active
      ? "Waiting for an active video call"
      : "Preview unavailable";
    var selfDetail = [
      activeSources.length ? "Active: " + activeSources.join(" + ") : "",
      configuredSources.length ? "Configured: " + configuredSources.join(" + ") : ""
    ].filter(Boolean).join(" / ") || (selfVideo.enabled ? "Waiting for active video call" : "Off");
    if (selfActive) {
      showImage(selfImg, selfSrc, "Current computer video preview");
    } else {
      clearImage(selfImg);
    }
    var selfExpanded = !!state.expandedFeeds["self"];
    selfCard.classList.toggle("has-frame", selfActive);
    selfCard.classList.toggle("expanded", selfExpanded);
    selfEmpty.textContent = selfEmptyText;
    selfMeta.textContent = selfDetail;
    var selfHint = selfCard.querySelector(".feed-focus-hint");
    if (selfHint) {
      selfHint.textContent = selfExpanded ? "Collapse" : "Open";
    }
    nextFeeds.self = {
      active: selfActive,
      src: selfSrc,
      label: "Computer video",
      alt: "Current computer video preview",
      meta: selfDetail,
      empty: selfEmptyText,
      width: selfVideo.width,
      height: selfVideo.height
    };

    var focusedFeedKey = document.activeElement && document.activeElement.dataset
      ? document.activeElement.dataset.viewerKey || ""
      : "";
    var remoteGrid = $("remote-video-grid");
    remoteGrid.classList.toggle("single-feed", remoteSessions.length === 1);
    if (!remoteSessions.length) {
      remoteGrid.innerHTML = '<article class="feed-card muted"><div class="feed-empty">No connected phone cameras</div></article>';
    } else {
      remoteGrid.innerHTML = remoteSessions.map(function (session) {
        var frame = session.latest_video_frame || {};
        var active = !!(session.video_active && frame.active);
        var sessionId = String(session.session_id || "");
        var feedKey = "remote:" + sessionId;
        var clientName = String(session.client_display_name || "");
        var label = "Phone camera " + (clientName || shortId(sessionId));
        var detail = active
          ? [frame.width && frame.height ? frame.width + "x" + frame.height : "", formatTime(frame.timestamp_ms)].filter(Boolean).join(" / ")
          : "Connected, no video frames";
        var frameSrc = "./api/education/frame.jpg?session_id=" + encodeURIComponent(session.session_id || "") + "&ts=" + Date.now();
        nextFeeds[feedKey] = {
          active: active,
          src: active ? frameSrc : "",
          label: label,
          alt: "Phone camera feed for " + (clientName || shortId(sessionId)),
          meta: detail,
          empty: "No phone camera frame",
          width: frame.width,
          height: frame.height
        };
        var isExpanded = !!state.expandedFeeds[feedKey];
        return [
          '<article class="feed-card remote-feed ' + (active ? "has-frame" : "") + (isExpanded ? " expanded" : "") + '">',
          '<div class="feed-media" data-viewer-key="' + escapeHtml(feedKey) + '" role="button" tabindex="0" aria-label="Open ' + escapeHtml(label) + ' in the focused viewer">',
          active ? '<img src="' + escapeHtml(frameSrc) + '" alt="Phone camera feed for ' + escapeHtml(clientName || shortId(session.session_id)) + '">' : '<div class="feed-empty">No phone camera frame</div>',
          focusHint(isExpanded),
          '</div>',
          '<div class="feed-meta">',
          '<strong>' + escapeHtml(clientName || shortId(session.session_id)) + '</strong>',
          '<span>' + escapeHtml(detail) + '</span>',
          '</div>',
          '</article>'
        ].join("");
      }).join("");
    }

    if (focusedFeedKey) {
      var refreshedTrigger = findFeedTrigger(focusedFeedKey);
      if (refreshedTrigger) {
        refreshedTrigger.focus({ preventScroll: true });
      }
    }

    state.feeds = nextFeeds;
    syncFocusedViewer();

    var selected = state.selectedSession || "";
    $("frame-meta").textContent = frames.active
      ? [shortId(selected || frames.latest_session_id), frames.latest_width + "x" + frames.latest_height, formatTime(frames.latest_timestamp_ms)].filter(Boolean).join(" / ")
      : "Idle";
  }

  function clearLiveVideo(reason) {
    var message = reason || "Live video unavailable";
    clearImage($("self-video-frame"));
    $("self-feed-card").classList.remove("has-frame");
    $("self-video-empty").textContent = message;
    $("self-video-meta").textContent = "Unavailable";
    $("remote-video-grid").classList.remove("single-feed");
    $("remote-video-grid").innerHTML = '<article class="feed-card muted"><div class="feed-empty">' + escapeHtml(message) + "</div></article>";
    $("frame-meta").textContent = "Unavailable";
    state.feeds = {
      self: {
        active: false,
        src: "",
        label: "Computer video",
        meta: "Unavailable",
        empty: message
      }
    };
    syncFocusedViewer();
  }

  function render(snapshot) {
    renderMetrics(snapshot);
    renderSessions(snapshot);
    renderMessages(snapshot);
    renderInteract(snapshot);
    renderRecordings(snapshot);
    renderMediaStatus(snapshot);
    renderAudioRouting(snapshot);
    renderVideoFeeds(snapshot);
  }

  async function loadStatus() {
    if (state.paused) {
      return;
    }
    try {
      var snapshot = await api("./api/education/status?limit=120");
      state.lastSnapshotAt = Date.now();
      state.lastSnapshot = snapshot;
      state.auth = snapshot.auth || state.auth;
      setAuthView(true);
      render(snapshot);
      await loadLiveAudioRouting();
    } catch (err) {
      if (err.status === 401) {
        clearLiveVideo("Session locked");
        closeFocusedViewer();
        setAuthView(false);
      } else {
        if (!state.lastSnapshotAt || Date.now() - state.lastSnapshotAt >= 5000) {
          clearLiveVideo("Live status unavailable");
        }
        $("connection-line").textContent = err.message || "Status unavailable";
      }
    }
  }

  function startPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
    }
    state.pollTimer = setInterval(loadStatus, 1500);
  }

  async function submitOtp(event) {
    event.preventDefault();
    var input = $("otp-input");
    var error = $("auth-error");
    error.classList.add("hidden");
    try {
      var payload = await api("./api/auth/login", {
        method: "POST",
        body: JSON.stringify({ code: input.value.trim() })
      });
      state.token = payload.token || state.token;
      try {
        if (state.token) {
          localStorage.setItem(tokenKey, state.token);
        }
      } catch (_) {}
      input.value = "";
      setAuthView(true);
      await loadStatus();
    } catch (err) {
      error.textContent = err.message || "Unlock failed";
      error.classList.remove("hidden");
    }
  }

  async function logout() {
    try {
      await api("./api/auth/logout", { method: "POST" });
    } catch (_) {}
    state.token = "";
    try {
      localStorage.removeItem(tokenKey);
    } catch (_) {}
    clearLiveVideo("Signed out");
    closeFocusedViewer();
    setAuthView(false);
  }

  async function saveClientName(button) {
    var ownerKey = String(button.dataset.ownerKey || "");
    var editor = button.closest(".name-editor");
    var input = editor ? editor.querySelector(".client-name-input") : null;
    if (!ownerKey || !input) {
      return;
    }
    button.disabled = true;
    try {
      var payload = await api("./api/education/client-name", {
        method: "PUT",
        body: JSON.stringify({
          owner_key: ownerKey,
          client_display_name: input.value
        })
      });
      var identity = payload.client_identity || {};
      $("connection-line").textContent = identity.history_stored
        ? "Client name saved with chat history"
        : "Client name updated for this live session";
      // Mark the edit as applied so the next poll may repaint the card list.
      input.defaultValue = input.value;
      await loadStatus();
    } catch (err) {
      $("connection-line").textContent = err.message || "Could not update client name";
    } finally {
      button.disabled = false;
    }
  }

  async function changeMicrophoneSharing(event) {
    if (state.micBusy) { return; }
    state.micBusy = true;
    var input = event.target;
    input.disabled = true;
    $("mic-control-error").classList.add("hidden");
    try {
      await api("/api/webrtc/microphone-sharing", {
        method: "PUT",
        body: JSON.stringify({ enabled: input.checked })
      });
      await loadStatus();
    } catch (err) {
      $("mic-control-error").textContent = err.status === 401
        ? "Sign in to the Admin UI to change microphone sharing."
        : err.message || "Could not update microphone sharing.";
      $("mic-control-error").classList.remove("hidden");
      if (state.lastSnapshot) { input.checked = !!(state.lastSnapshot.audio_routing || {}).sharing_enabled; }
    } finally {
      state.micBusy = false;
      input.disabled = false;
      await loadLiveAudioRouting();
    }
  }

  function toggleFeedExpansion(key) {
    state.expandedFeeds[key] = !state.expandedFeeds[key];
    if (state.lastSnapshot) {
      render(state.lastSnapshot);
    }
  }

  function wireEvents() {
    var dialog = $("video-viewer");
    var canvas = $("viewer-canvas");
    var viewerImage = $("viewer-image");
    $("auth-form").addEventListener("submit", submitOtp);
    $("logout-button").addEventListener("click", logout);
    $("refresh-button").addEventListener("click", loadStatus);
    $("mic-sharing-toggle").addEventListener("change", changeMicrophoneSharing);
    $("pause-button").addEventListener("click", function () {
      state.paused = !state.paused;
      $("pause-button").textContent = state.paused ? "Resume" : "Pause";
      if (!state.paused) {
        loadStatus();
      }
    });
    $("session-filter").addEventListener("change", function (event) {
      state.selectedSession = event.target.value || "";
      loadStatus();
    });
    $("viewer-fit-button").addEventListener("click", toggleViewerFit);
    $("viewer-zoom-out").addEventListener("click", function () {
      setViewerZoom(state.viewer.zoom - 0.25);
    });
    $("viewer-zoom-in").addEventListener("click", function () {
      setViewerZoom(state.viewer.zoom + 0.25);
    });
    $("viewer-reset").addEventListener("click", resetViewer);
    $("viewer-fullscreen").addEventListener("click", toggleViewerFullscreen);
    $("viewer-close").addEventListener("click", closeFocusedViewer);
    canvas.addEventListener("pointerdown", viewerPointerDown);
    canvas.addEventListener("pointermove", viewerPointerMove);
    canvas.addEventListener("pointerup", viewerPointerUp);
    canvas.addEventListener("pointercancel", viewerPointerUp);
    canvas.addEventListener("lostpointercapture", viewerPointerUp);
    dialog.addEventListener("keydown", viewerKeyDown);
    canvas.addEventListener("dblclick", toggleViewerFullscreen);
    canvas.addEventListener("wheel", function (event) {
      if (!event.ctrlKey && !event.metaKey) {
        return;
      }
      event.preventDefault();
      setViewerZoom(state.viewer.zoom + (event.deltaY < 0 ? 0.25 : -0.25));
    }, { passive: false });
    viewerImage.addEventListener("load", layoutViewer);
    viewerImage.addEventListener("error", function () {
      clearImage(viewerImage);
      $("viewer-empty").textContent = "Frame unavailable";
      $("viewer-empty").hidden = false;
      layoutViewer();
    });
    dialog.addEventListener("close", finishFocusedViewerClose);
    document.addEventListener("click", function (event) {
      var saveName = event.target.closest && event.target.closest(".save-client-name");
      if (saveName) {
        saveClientName(saveName);
        return;
      }
      var media = event.target.closest && event.target.closest(".feed-media[data-viewer-key]");
      if (media) {
        toggleFeedExpansion(media.dataset.viewerKey);
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && state.viewer.key && dialog.hasAttribute("open")) {
        event.preventDefault();
        closeFocusedViewer();
        return;
      }
      var media = event.target.closest && event.target.closest(".feed-media[data-viewer-key]");
      if (media && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        toggleFeedExpansion(media.dataset.viewerKey);
      }
    });
    document.addEventListener("fullscreenchange", function () {
      var fullscreenButton = $("viewer-fullscreen");
      var isFullscreen = document.fullscreenElement === dialog;
      fullscreenButton.textContent = isFullscreen ? "Exit full screen" : "Full screen";
      fullscreenButton.setAttribute("aria-pressed", String(isFullscreen));
      requestAnimationFrame(layoutViewer);
    });
    window.addEventListener("resize", function () {
      if (dialog.open) {
        requestAnimationFrame(layoutViewer);
      }
    });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) {
        loadStatus();
      }
    });
  }

  function boot() {
    wireEvents();
    setAuthView(!!(state.auth && state.auth.authenticated));
    if (state.auth && state.auth.authenticated) {
      loadStatus();
    } else if (state.token) {
      loadStatus();
    }
    startPolling();
  }

  boot();
})();
