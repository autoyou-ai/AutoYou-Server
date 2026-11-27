/**
 * Media gallery, upload pipeline, Lightbox, Voice Recorder (MediaRecorder + optional dictation),
 * and Sketch / Drawing Pad for AutoYou Diary.
 */

const MEDIA_LINK_RE =
  /\[([^\]\n]{1,160})\]\((\/agent\/diary_agent\/api\/diary\/entries\/[0-9A-Fa-f-]{36}\/media\/[A-Za-z0-9_-]{1,64})\)/g;

const MEDIA_KINDS = [
  ["audio", /\.(m4a|aac|mp3|wav|ogg|oga|opus|flac|weba|amr|caf)$/i],
  ["video", /\.(mp4|m4v|mov|webm|3gp|mkv)$/i],
  ["image", /\.(jpe?g|png|gif|webp|heic|heif|avif)$/i],
];

export function mediaKind(name) {
  const hit = MEDIA_KINDS.find(([, pattern]) => pattern.test(String(name || "")));
  return hit ? hit[0] : "file";
}

export function isSketchName(name) {
  const lower = String(name || "").trim().toLowerCase();
  return mediaKind(lower) === "image" && (lower.startsWith("sketch") || lower.startsWith("drawing"));
}

export function getBasePrefix() {
  return location.pathname.startsWith("/agent/diary_agent") ? "/agent/diary_agent" : "";
}

export function entryMediaUrl(entryId, mediaId, basePrefix = getBasePrefix()) {
  return `${basePrefix}/api/diary/entries/${encodeURIComponent(entryId)}/media/${encodeURIComponent(mediaId)}`;
}

export function sanitizeAttachmentName(rawName, fallback = "attachment") {
  const cleaned = String(rawName || "")
    .replace(/[\x00-\x1f/\\]+/g, "_")
    .trim()
    .slice(0, 200);
  return cleaned || fallback;
}

export function makeMediaId(prefix = "m") {
  const rand = typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID().replace(/-/g, "").slice(0, 16)
    : Math.random().toString(36).slice(2, 14);
  return `${prefix}-${rand}`.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 48);
}

/**
 * Upload one file/blob to /api/diary/entries/{entryId}/media with X-AutoYou-Media-ID.
 * Compatible with iOS/Android sync & idempotent retries.
 */
export async function uploadDiaryMedia(entryId, fileOrBlob, { filename, transcription = "", basePrefix = getBasePrefix() } = {}) {
  const mediaId = makeMediaId(mediaKind(filename || fileOrBlob.name || "") === "audio" ? "voice" : "media");
  const safeName = sanitizeAttachmentName(filename || fileOrBlob.name || "attachment");
  const form = new FormData();
  const mime = (fileOrBlob.type || "application/octet-stream").split(";")[0].trim() || "application/octet-stream";
  const fileObj = fileOrBlob instanceof File ? fileOrBlob : new File([fileOrBlob], safeName, { type: mime });
  form.append("file", fileObj, safeName);

  const response = await fetch(`${basePrefix}/api/diary/entries/${encodeURIComponent(entryId)}/media`, {
    method: "POST",
    headers: { "X-AutoYou-Media-ID": mediaId },
    body: form,
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.success === false) {
    throw new Error(data.error || data.message || `Upload failed (${response.status})`);
  }
  return {
    id: mediaId,
    name: safeName,
    transcription: String(transcription || ""),
    url: data.url,
    size: data.size,
  };
}

export function collectEntryMedia(entry, basePrefix = getBasePrefix()) {
  const seen = new Set();
  const items = [];
  for (const row of entry.media || []) {
    if (!/^[a-f0-9-]{36}$/i.test(String(entry.id || "")) || !/^[A-Za-z0-9_-]{1,64}$/.test(String(row.id || ""))) continue;
    const storedPath = `/agent/diary_agent/api/diary/entries/${entry.id}/media/${row.id}`;
    seen.add(storedPath);
    items.push({
      id: row.id,
      name: row.name,
      url: entryMediaUrl(entry.id, row.id, basePrefix),
      kind: mediaKind(row.name),
      sketch: isSketchName(row.name),
      transcription: row.transcription || "",
      structured: true,
    });
  }
  for (const match of String(entry.body || "").matchAll(MEDIA_LINK_RE)) {
    if (seen.has(match[2])) continue;
    seen.add(match[2]);
    const mid = match[2].split("/").pop() || "";
    items.push({
      id: mid,
      name: match[1],
      url: basePrefix + match[2].slice("/agent/diary_agent".length),
      kind: mediaKind(match[1]),
      sketch: isSketchName(match[1]),
      transcription: "",
      structured: false,
    });
  }
  return items;
}

/**
 * Render media collage (photos/sketches), inline videos, voice-note cards, and file chips.
 */
export function renderMedia(host, entry, options = {}) {
  const {
    basePrefix = getBasePrefix(),
    editable = false,
    onRemove,
    onInsertTranscript,
    onUpdateTranscript,
    onOpenLightbox,
  } = options;

  host.textContent = "";
  const items = collectEntryMedia(entry, basePrefix);
  host.hidden = items.length === 0;
  if (!items.length) return;

  const visuals = items.filter((it) => it.kind === "image" || it.kind === "video");
  const audios = items.filter((it) => it.kind === "audio");
  const files = items.filter((it) => it.kind === "file");

  // 1. Visual Collage Grid (Photos, Sketches, Videos)
  if (visuals.length) {
    const grid = document.createElement("div");
    grid.className = `media-collage count-${Math.min(visuals.length, 4)}`;
    visuals.forEach((item, idx) => {
      const tile = document.createElement("figure");
      tile.className = `media-tile kind-${item.kind}${item.sketch ? " is-sketch" : ""}`;

      if (item.kind === "image") {
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = item.name;
        img.src = item.url;
        img.addEventListener("click", () => onOpenLightbox?.(visuals, idx));
        tile.appendChild(img);
      } else {
        const vid = document.createElement("video");
        vid.controls = true;
        vid.preload = "metadata";
        vid.playsInline = true;
        vid.setAttribute("playsinline", "");
        vid.src = item.url;
        tile.appendChild(vid);
      }

      if (item.sketch) {
        const badge = document.createElement("span");
        badge.className = "media-tile-badge";
        badge.textContent = "Sketch";
        tile.appendChild(badge);
      }

      if (editable && item.structured && onRemove) {
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "media-tile-remove";
        rm.title = `Remove ${item.name}`;
        rm.setAttribute("aria-label", `Remove ${item.name}`);
        rm.textContent = "×";
        rm.addEventListener("click", (e) => {
          e.stopPropagation();
          onRemove(item);
        });
        tile.appendChild(rm);
      }
      grid.appendChild(tile);
    });
    host.appendChild(grid);
  }

  // 2. Voice Note / Audio Cards
  if (audios.length) {
    const stack = document.createElement("div");
    stack.className = "audio-card-stack";
    for (const item of audios) {
      const card = document.createElement("div");
      card.className = "audio-card";

      const top = document.createElement("div");
      top.className = "audio-card-head";
      top.innerHTML = `
        <span class="audio-card-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </span>
      `;
      const meta = document.createElement("div");
      meta.className = "audio-card-meta";
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = item.name;
      const sub = document.createElement("small");
      sub.textContent = item.transcription ? "Voice note · Transcribed" : "Voice note";
      meta.append(link, sub);
      top.appendChild(meta);

      if (editable && item.structured && onRemove) {
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "icon-btn-sm";
        rm.title = `Remove ${item.name}`;
        rm.textContent = "×";
        rm.onclick = () => onRemove(item);
        top.appendChild(rm);
      }

      const audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "metadata";
      audio.src = item.url;

      card.append(top, audio);

      if (item.transcription) {
        const tr = document.createElement("div");
        tr.className = "audio-transcript";
        const p = document.createElement("p");
        p.textContent = item.transcription;
        tr.appendChild(p);
        if (editable && onInsertTranscript) {
          const insertBtn = document.createElement("button");
          insertBtn.type = "button";
          insertBtn.className = "text-action";
          insertBtn.textContent = "Insert transcript into entry";
          insertBtn.onclick = () => onInsertTranscript(item.transcription);
          tr.appendChild(insertBtn);
        }
        card.appendChild(tr);
      } else if (editable && item.structured && onUpdateTranscript) {
        const addTrBtn = document.createElement("button");
        addTrBtn.type = "button";
        addTrBtn.className = "text-action audio-add-tr";
        addTrBtn.textContent = "+ Add transcript note";
        addTrBtn.onclick = () => {
          const text = prompt("Enter transcript or summary for this voice note:", "");
          if (text !== null && text.trim()) onUpdateTranscript(item.id, text.trim());
        };
        card.appendChild(addTrBtn);
      }

      stack.appendChild(card);
    }
    host.appendChild(stack);
  }

  // 3. Document / Generic File Chips
  if (files.length) {
    const chipRow = document.createElement("div");
    chipRow.className = "file-chip-row";
    for (const item of files) {
      const chip = document.createElement("div");
      chip.className = "file-chip";
      const a = document.createElement("a");
      a.href = item.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = `📎 ${item.name}`;
      chip.appendChild(a);
      if (editable && item.structured && onRemove) {
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "icon-btn-sm";
        rm.textContent = "×";
        rm.onclick = () => onRemove(item);
        chip.appendChild(rm);
      }
      chipRow.appendChild(chip);
    }
    host.appendChild(chipRow);
  }
}

/**
 * Full-screen Lightbox for photos, sketches & videos.
 */
export function openLightbox(root, items, startIndex = 0, { onJumpToEntry } = {}) {
  if (!items.length) return;
  let index = Math.max(0, Math.min(startIndex, items.length - 1));
  root.hidden = false;
  root.setAttribute("aria-hidden", "false");

  function close() {
    root.hidden = true;
    root.setAttribute("aria-hidden", "true");
    root.innerHTML = "";
    window.removeEventListener("keydown", onKey);
  }

  function onKey(e) {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowLeft" && items.length > 1) { index = (index - 1 + items.length) % items.length; render(); }
    else if (e.key === "ArrowRight" && items.length > 1) { index = (index + 1) % items.length; render(); }
  }

  function render() {
    const item = items[index];
    root.innerHTML = `
      <div class="lightbox-backdrop"></div>
      <div class="lightbox-dialog" role="dialog" aria-modal="true" aria-label="Media preview">
        <div class="lightbox-top">
          <div class="lightbox-caption">
            <strong></strong>
            <small></small>
          </div>
          <div class="lightbox-actions">
            ${item.entryId && onJumpToEntry ? `<button type="button" class="lightbox-btn" id="lb-jump">Open Entry</button>` : ""}
            <a class="lightbox-btn" download href="${item.url}">Download</a>
            <button type="button" class="lightbox-close" aria-label="Close">×</button>
          </div>
        </div>
        <div class="lightbox-stage">
          ${items.length > 1 ? `<button type="button" class="lightbox-nav prev" aria-label="Previous">‹</button>` : ""}
          <div class="lightbox-media-slot"></div>
          ${items.length > 1 ? `<button type="button" class="lightbox-nav next" aria-label="Next">›</button>` : ""}
        </div>
      </div>
    `;
    root.querySelector(".lightbox-caption strong").textContent = item.entryTitle || item.name;
    root.querySelector(".lightbox-caption small").textContent =
      [item.entryDate, item.name, `${index + 1} / ${items.length}`].filter(Boolean).join(" · ");

    const slot = root.querySelector(".lightbox-media-slot");
    if (item.kind === "video") {
      const v = document.createElement("video");
      v.src = item.url;
      v.controls = true;
      v.autoplay = true;
      v.playsInline = true;
      slot.appendChild(v);
    } else {
      const img = document.createElement("img");
      img.src = item.url;
      img.alt = item.name;
      slot.appendChild(img);
    }

    root.querySelector(".lightbox-backdrop").onclick = close;
    root.querySelector(".lightbox-close").onclick = close;
    root.querySelector(".lightbox-nav.prev")?.addEventListener("click", () => {
      index = (index - 1 + items.length) % items.length;
      render();
    });
    root.querySelector(".lightbox-nav.next")?.addEventListener("click", () => {
      index = (index + 1) % items.length;
      render();
    });
    root.querySelector("#lb-jump")?.addEventListener("click", () => {
      const eid = item.entryId;
      close();
      onJumpToEntry?.(eid);
    });
  }

  window.addEventListener("keydown", onKey);
  render();
}

/**
 * Built-in Voice Recorder & Dictation Sheet.
 */
export function openVoiceRecorderModal(openModal, { onAttach }) {
  let mediaRecorder = null;
  let stream = null;
  let chunks = [];
  let recordedBlob = null;
  let recordedExt = "webm";
  let timerId = null;
  let startMs = 0;
  let recognition = null;
  let transcript = "";
  let analyser = null;
  let audioCtx = null;
  let rafId = null;

  const content = document.createElement("div");
  content.className = "recorder-sheet";
  content.innerHTML = `
    <div class="recorder-meter" aria-hidden="true">
      ${Array.from({ length: 24 }, () => `<span class="meter-bar"></span>`).join("")}
    </div>
    <div class="recorder-timer" id="rec-timer">00:00</div>
    <p class="recorder-hint" id="rec-hint">Tap the microphone to record a voice note. Live transcription runs when supported by your browser.</p>
    <div class="recorder-controls">
      <button type="button" class="rec-btn" id="rec-toggle" aria-label="Start recording">
        <span class="rec-dot"></span>
      </button>
    </div>
    <div id="rec-preview-wrap" class="rec-preview-wrap" hidden>
      <audio id="rec-audio" controls></audio>
    </div>
    <label class="sheet-field">
      <span>Transcript / Voice Note Caption (optional)</span>
      <textarea id="rec-transcript" rows="3" placeholder="Spoken words or summary…"></textarea>
    </label>
    <div class="sheet-footer">
      <button type="button" class="btn-secondary" id="rec-cancel">Cancel</button>
      <button type="button" class="btn-primary" id="rec-save" disabled>Attach Voice Note</button>
    </div>
  `;

  function cleanup() {
    if (timerId) clearInterval(timerId);
    if (rafId) cancelAnimationFrame(rafId);
    try { recognition?.stop(); } catch (_) {}
    try { mediaRecorder && mediaRecorder.state !== "inactive" && mediaRecorder.stop(); } catch (_) {}
    stream?.getTracks().forEach((t) => t.stop());
    try { audioCtx?.close(); } catch (_) {}
    mediaRecorder = null;
    stream = null;
    audioCtx = null;
    analyser = null;
  }

  const closeModal = openModal({ title: "Record Voice Note", body: content, onClose: cleanup });

  const bars = [...content.querySelectorAll(".meter-bar")];
  const timerEl = content.querySelector("#rec-timer");
  const hintEl = content.querySelector("#rec-hint");
  const toggleBtn = content.querySelector("#rec-toggle");
  const previewWrap = content.querySelector("#rec-preview-wrap");
  const audioEl = content.querySelector("#rec-audio");
  const trArea = content.querySelector("#rec-transcript");
  const saveBtn = content.querySelector("#rec-save");

  function fmtTime(sec) {
    const m = String(Math.floor(sec / 60)).padStart(2, "0");
    const s = String(Math.floor(sec % 60)).padStart(2, "0");
    return `${m}:${s}`;
  }

  function animateBars() {
    if (!analyser) return;
    const freq = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(freq);
    bars.forEach((bar, i) => {
      const val = freq[Math.floor((i / bars.length) * (freq.length * 0.6))] || 0;
      bar.style.height = `${Math.max(12, Math.min(100, (val / 255) * 100))}%`;
    });
    rafId = requestAnimationFrame(animateBars);
  }

  async function startRec() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      hintEl.textContent = "Microphone recording is unavailable in this browser context. You can still attach an audio file via Media.";
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const preferred = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
      const mimeType = preferred.find((m) => MediaRecorder.isTypeSupported?.(m)) || "";
      mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      recordedExt = mimeType.includes("mp4") ? "m4a" : mimeType.includes("ogg") ? "ogg" : "webm";
      chunks = [];
      recordedBlob = null;
      saveBtn.disabled = true;
      previewWrap.hidden = true;

      mediaRecorder.ondataavailable = (ev) => { if (ev.data && ev.data.size > 0) chunks.push(ev.data); };
      mediaRecorder.onstop = () => {
        const finalType = (mediaRecorder?.mimeType || mimeType || "audio/webm").split(";")[0];
        recordedBlob = new Blob(chunks, { type: finalType });
        audioEl.src = URL.createObjectURL(recordedBlob);
        previewWrap.hidden = false;
        saveBtn.disabled = recordedBlob.size === 0;
        hintEl.textContent = "Recording captured. Preview or edit the transcript before attaching.";
      };

      // Live visualizer
      try {
        const AC = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AC();
        const src = audioCtx.createMediaStreamSource(stream);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 64;
        src.connect(analyser);
        animateBars();
      } catch (_) {}

      // Optional browser dictation
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (SR) {
        try {
          recognition = new SR();
          recognition.continuous = true;
          recognition.interimResults = true;
          recognition.onresult = (event) => {
            let text = "";
            for (let i = 0; i < event.results.length; i++) {
              text += event.results[i][0].transcript;
            }
            transcript = text.trim();
            trArea.value = transcript;
          };
          recognition.start();
        } catch (_) {}
      }

      mediaRecorder.start(250);
      startMs = Date.now();
      timerEl.textContent = "00:00";
      timerId = setInterval(() => {
        timerEl.textContent = fmtTime((Date.now() - startMs) / 1000);
      }, 250);
      toggleBtn.classList.add("is-recording");
      toggleBtn.setAttribute("aria-label", "Stop recording");
      hintEl.textContent = "Recording… tap again to stop.";
    } catch (err) {
      hintEl.textContent = `Could not access microphone: ${err.message || "Permission denied"}`;
      cleanup();
    }
  }

  function stopRec() {
    if (timerId) clearInterval(timerId);
    if (rafId) cancelAnimationFrame(rafId);
    try { recognition?.stop(); } catch (_) {}
    try { mediaRecorder && mediaRecorder.state !== "inactive" && mediaRecorder.stop(); } catch (_) {}
    stream?.getTracks().forEach((t) => t.stop());
    bars.forEach((b) => { b.style.height = "16%"; });
    toggleBtn.classList.remove("is-recording");
    toggleBtn.setAttribute("aria-label", "Record again");
  }

  toggleBtn.addEventListener("click", () => {
    if (mediaRecorder && mediaRecorder.state === "recording") stopRec();
    else startRec();
  });

  content.querySelector("#rec-cancel").onclick = () => closeModal();
  saveBtn.onclick = async () => {
    if (!recordedBlob) return;
    saveBtn.disabled = true;
    saveBtn.textContent = "Uploading…";
    try {
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const name = `Voice note ${stamp}.${recordedExt}`;
      await onAttach(recordedBlob, name, trArea.value.trim());
      closeModal();
    } catch (err) {
      hintEl.textContent = err.message || "Could not upload voice note.";
      saveBtn.disabled = false;
      saveBtn.textContent = "Attach Voice Note";
    }
  };
}

/**
 * Built-in Sketch / Handwriting Canvas Pad.
 */
export function openSketchPadModal(openModal, { onAttach }) {
  const colors = ["#1f2937", "#44B6FF", "#2FB67C", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#ffffff"];
  const sizes = [2.5, 5, 10];
  let activeColor = "#44B6FF";
  let activeSize = 5;
  let strokes = [];
  let currentStroke = null;

  const content = document.createElement("div");
  content.className = "sketch-sheet";
  content.innerHTML = `
    <div class="sketch-canvas-wrap">
      <canvas class="sketch-canvas" width="900" height="540"></canvas>
    </div>
    <div class="sketch-tools">
      <div class="sketch-colors">
        ${colors.map((c) => `<button type="button" class="swatch-btn${c === activeColor ? " is-active" : ""}" data-color="${c}" style="background:${c}" aria-label="Color ${c}"></button>`).join("")}
      </div>
      <div class="sketch-widths">
        ${sizes.map((s) => `<button type="button" class="width-btn${s === activeSize ? " is-active" : ""}" data-size="${s}" aria-label="Stroke width ${s}"><span style="width:${s + 4}px;height:${s + 4}px"></span></button>`).join("")}
      </div>
      <div class="sketch-actions-inline">
        <button type="button" class="btn-secondary-sm" id="sketch-undo">Undo</button>
        <button type="button" class="btn-secondary-sm" id="sketch-clear">Clear</button>
      </div>
    </div>
    <div class="sheet-footer">
      <button type="button" class="btn-secondary" id="sketch-cancel">Cancel</button>
      <button type="button" class="btn-primary" id="sketch-save" disabled>Insert Sketch</button>
    </div>
  `;

  const closeModal = openModal({ title: "Sketch & Handwriting", body: content, wide: true });
  const canvas = content.querySelector(".sketch-canvas");
  const ctx = canvas.getContext("2d");
  const saveBtn = content.querySelector("#sketch-save");

  function redraw() {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // Subtle dot grid notebook paper background
    ctx.fillStyle = "#e5edf4";
    for (let x = 24; x < canvas.width; x += 28) {
      for (let y = 24; y < canvas.height; y += 28) {
        ctx.fillRect(x, y, 2, 2);
      }
    }
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const s of strokes) {
      if (s.points.length < 1) continue;
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.size;
      ctx.beginPath();
      ctx.moveTo(s.points[0][0], s.points[0][1]);
      for (let i = 1; i < s.points.length; i++) {
        ctx.lineTo(s.points[i][0], s.points[i][1]);
      }
      if (s.points.length === 1) ctx.lineTo(s.points[0][0] + 0.1, s.points[0][1] + 0.1);
      ctx.stroke();
    }
    saveBtn.disabled = strokes.length === 0;
  }

  function pointFromEvent(e) {
    const r = canvas.getBoundingClientRect();
    return [
      ((e.clientX - r.left) / r.width) * canvas.width,
      ((e.clientY - r.top) / r.height) * canvas.height,
    ];
  }

  canvas.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    canvas.setPointerCapture?.(e.pointerId);
    currentStroke = { color: activeColor, size: activeSize, points: [pointFromEvent(e)] };
    strokes.push(currentStroke);
    redraw();
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!currentStroke) return;
    e.preventDefault();
    currentStroke.points.push(pointFromEvent(e));
    redraw();
  });
  const endStroke = () => { currentStroke = null; };
  canvas.addEventListener("pointerup", endStroke);
  canvas.addEventListener("pointercancel", endStroke);

  content.querySelectorAll(".swatch-btn").forEach((btn) => {
    btn.onclick = () => {
      activeColor = btn.dataset.color;
      content.querySelectorAll(".swatch-btn").forEach((b) => b.classList.toggle("is-active", b === btn));
    };
  });
  content.querySelectorAll(".width-btn").forEach((btn) => {
    btn.onclick = () => {
      activeSize = Number(btn.dataset.size);
      content.querySelectorAll(".width-btn").forEach((b) => b.classList.toggle("is-active", b === btn));
    };
  });
  content.querySelector("#sketch-undo").onclick = () => { strokes.pop(); redraw(); };
  content.querySelector("#sketch-clear").onclick = () => { strokes = []; redraw(); };
  content.querySelector("#sketch-cancel").onclick = () => closeModal();

  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      const blob = await new Promise((resolve, reject) =>
        canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not export sketch."))), "image/png")
      );
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      await onAttach(blob, `sketch-${stamp}.png`);
      closeModal();
    } catch (err) {
      alert(err.message || "Could not save sketch.");
      saveBtn.disabled = false;
      saveBtn.textContent = "Insert Sketch";
    }
  };

  redraw();
}
