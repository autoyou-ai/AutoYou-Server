// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-6400c7aeda245c57174d5b51

const state = {
  notes: [],
  categories: [],
  activeCategory: "",
  order: "newest",
  activeNoteId: null,
  activeNote: null,
  hasMore: false,
  nextCursor: null,
  totalCount: 0,
  currentQuery: "",
  loadingList: false,
  loadingDetail: false,
  savingNote: false,
  deletingNote: false,
  modalMode: "view",
  lastModalTrigger: null,
  lastLoadedAt: 0,
  confirmDeleteNoteId: null,
  confirmDeleteTrigger: null,
  confirmDeleteError: "",
  listError: "",
};

const PAGE_SIZE = 20;
const AUTO_REFRESH_INTERVAL_MS = 20000;
const MAX_CATEGORY_FILTERS = 12;
const ORDER_LABELS = { newest: "Newest first", oldest: "Oldest first" };
// Mirrors the accent and icon names the backend assigns in category_appearance().
const ACCENTS = new Set(["blue", "purple", "green", "amber", "teal", "rose", "slate"]);
const CATEGORY_ICONS = new Set(["doc", "briefcase", "leaf", "bulb", "check", "heart", "book", "cart", "card", "pin", "mic"]);
const initialPayload = window.__INITIAL_NOTES_BOOTSTRAP__ || null;
let currentTheme = document.documentElement.dataset.theme || "dark";
let searchDebounceTimer = null;
let listObserver = null;
let refreshInterval = null;
let listRequestToken = 0;
let renderedListHtml = null;
const UI_THEME_STORAGE_KEY = "autoyou.ui.theme";
const ROOT_THEME_API_URL = (() => {
  try {
    return new URL("/api/ui/theme", window.location.origin).toString();
  } catch (_) {
    return "/api/ui/theme";
  }
})();

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function icon(name, extraClass = "") {
  return `<i class="ic ic-${name}${extraClass ? ` ${extraClass}` : ""}" aria-hidden="true"></i>`;
}

function accentOf(item) {
  return item && ACCENTS.has(item.accent) ? item.accent : "slate";
}

function categoryIconOf(item) {
  return item && CATEGORY_ICONS.has(item.icon) ? item.icon : "doc";
}

function noteIconTile(item) {
  return `<span class="note-icon accent-${accentOf(item)}" aria-hidden="true">${icon(categoryIconOf(item))}</span>`;
}

function applyTheme(theme) {
  currentTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = currentTheme;
  try {
    localStorage.setItem(UI_THEME_STORAGE_KEY, currentTheme);
  } catch (_) {}
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (_) {
    return {};
  }
}

async function loadThemePreference() {
  try {
    const response = await fetch(ROOT_THEME_API_URL);
    const payload = await readJson(response);
    if (response.ok && payload.success) {
      applyTheme(payload.theme || currentTheme);
      return;
    }
  } catch (_) {}
  applyTheme(currentTheme);
}

// Notes store server-local times without a zone ("2026-09-24T20:11:00").
// Build those from their parts so every browser reads them as local time.
function parseTimestamp(value) {
  if (!value) {
    return null;
  }
  const text = String(value).trim();
  const local = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$/.exec(text);
  const parsed = local
    ? new Date(Number(local[1]), Number(local[2]) - 1, Number(local[3]), Number(local[4]), Number(local[5]), Number(local[6] || 0))
    : new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatTimestamp(value) {
  if (!value) {
    return "Unknown";
  }
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return String(value);
  }
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(parsed);
}

function formatClockTime(date) {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
}

// Keep in step with _format_relative_day() in the backend's first paint.
function formatRelativeDay(value) {
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return value ? String(value) : "";
  }
  const now = new Date();
  const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(parsed)) / 86400000);
  if (days === 0) {
    return "Today";
  }
  if (days === 1) {
    return "Yesterday";
  }
  const options = { month: "short", day: "numeric" };
  if (parsed.getFullYear() !== now.getFullYear()) {
    options.year = "numeric";
  }
  return new Intl.DateTimeFormat(undefined, options).format(parsed);
}

function timestampValue(value) {
  const parsed = parseTimestamp(value);
  return parsed ? parsed.getTime() : 0;
}

function buildPreview(content) {
  const compactPreview = String(content || "").replace(/\s+/g, " ").trim();
  return compactPreview.slice(0, 220) + (compactPreview.length > 220 ? "..." : "");
}

function normalizeNote(note) {
  const rawId = Number(note && note.id);
  return {
    id: Number.isFinite(rawId) ? rawId : null,
    title: String((note && note.title) || "Untitled note").trim() || "Untitled note",
    content: String((note && note.content) || ""),
    preview: String((note && note.preview) || buildPreview((note && note.content) || "")),
    tags: Array.isArray(note && note.tags) ? note.tags : [],
    category: String((note && note.category) || "").trim() || null,
    accent: accentOf(note),
    icon: categoryIconOf(note),
    created_at: note && note.created_at,
    updated_at: note && note.updated_at,
    metadata: (note && note.metadata) || {},
  };
}

function sortNotes(notes) {
  const direction = state.order === "oldest" ? -1 : 1;
  return [...notes].sort((left, right) => {
    const timeDelta = timestampValue(right.updated_at || right.created_at) - timestampValue(left.updated_at || left.created_at);
    if (timeDelta !== 0) {
      return timeDelta * direction;
    }
    return ((Number(right.id) || 0) - (Number(left.id) || 0)) * direction;
  });
}

function mergeNotes(existingNotes, incomingNotes, { reset = false } = {}) {
  const nextMap = new Map();
  if (!reset) {
    existingNotes.forEach((note) => {
      if (note.id !== null) {
        nextMap.set(note.id, note);
      }
    });
  }
  incomingNotes.forEach((note) => {
    if (note.id !== null) {
      nextMap.set(note.id, normalizeNote(note));
    }
  });
  return sortNotes(Array.from(nextMap.values()));
}

function setSyncState(syncState, detail = "") {
  const status = document.getElementById("sync-status");
  const label = document.getElementById("sync-label");
  if (!status || !label) {
    return;
  }
  const labels = { synced: "Synced", syncing: "Syncing...", error: "Not synced" };
  status.dataset.state = syncState;
  label.textContent = labels[syncState] || labels.synced;
  status.title = detail || label.textContent;
}

function updateHeaderStats() {
  const count = document.getElementById("notes-count");
  if (count) {
    count.textContent = String(state.totalCount || state.notes.length || 0);
  }
}

function describeFilters() {
  const parts = [];
  if (state.activeCategory) {
    parts.push(`in ${state.activeCategory}`);
  }
  if (state.currentQuery) {
    parts.push(`for "${state.currentQuery}"`);
  }
  return parts.length ? ` ${parts.join(" ")}` : "";
}

function updateStatus(message) {
  const status = document.getElementById("notes-status");
  if (!status) {
    return;
  }
  if (message) {
    status.textContent = message;
    return;
  }
  const loadedCount = state.notes.length;
  const total = state.totalCount || loadedCount;
  if (!loadedCount) {
    status.textContent = state.currentQuery || state.activeCategory
      ? `No notes${describeFilters()}.`
      : "";
    return;
  }
  const base = `Showing ${loadedCount} of ${total} notes${describeFilters()}.`;
  status.textContent = state.hasMore ? `${base} Scroll for more.` : base;
}

function setLoadingMore(visible) {
  const loadingMore = document.getElementById("notes-loading-more");
  if (!loadingMore) {
    return;
  }
  loadingMore.hidden = !visible;
}

function setListBusy(busy) {
  const container = document.getElementById("note-list");
  if (container) {
    container.setAttribute("aria-busy", busy ? "true" : "false");
  }
}

function adjustCategoryCount(name, delta, appearance = null) {
  const category = String(name || "").trim();
  if (!category || !delta) {
    return;
  }
  const existing = state.categories.find((item) => item.name === category);
  if (existing) {
    existing.count = Math.max(0, (Number(existing.count) || 0) + delta);
  } else if (delta > 0) {
    state.categories.push({ name: category, count: delta, accent: accentOf(appearance), icon: categoryIconOf(appearance) });
  }
  state.categories = state.categories
    .filter((item) => Number(item.count) > 0)
    .sort((left, right) => (Number(right.count) || 0) - (Number(left.count) || 0) || left.name.localeCompare(right.name));
}

function renderCategoryFilters() {
  const container = document.getElementById("category-filters");
  if (!container) {
    return;
  }
  const categories = state.categories.slice(0, MAX_CATEGORY_FILTERS);
  if (state.activeCategory && !categories.some((item) => item.name === state.activeCategory)) {
    categories.push({ name: state.activeCategory, accent: "slate" });
  }
  const allActive = !state.activeCategory;
  const chips = [
    `<button class="filter-chip filter-all${allActive ? " is-active" : ""}" type="button" data-category="" aria-pressed="${allActive}">All</button>`,
  ];
  categories.forEach((item) => {
    const selected = item.name === state.activeCategory;
    chips.push(
      `<button class="filter-chip accent-${accentOf(item)}${selected ? " is-active" : ""}" type="button" data-category="${escapeHtml(item.name)}" aria-pressed="${selected}">${escapeHtml(item.name)}</button>`,
    );
  });
  container.innerHTML = chips.join("");
}

function noteMetaMarkup(note) {
  const day = `<span class="note-time">${escapeHtml(formatRelativeDay(note.updated_at || note.created_at))}</span>`;
  if (!note.category) {
    return day;
  }
  return `${day}<span class="note-dot" aria-hidden="true">•</span><span class="note-category">${escapeHtml(note.category)}</span>`;
}

function emptyStateMarkup() {
  if (state.currentQuery || state.activeCategory) {
    return `
      <div class="empty-state">
        <span class="note-icon accent-slate" aria-hidden="true">${icon("search")}</span>
        <p class="empty-title">No matching notes</p>
        <p class="empty-copy">Try another search or category.</p>
        <button class="button subtle" type="button" data-action="clear-filters">Show all notes</button>
      </div>
    `;
  }
  return `
    <div class="empty-state">
      <span class="note-icon accent-slate" aria-hidden="true">${icon("doc")}</span>
      <p class="empty-title">No notes yet</p>
      <p class="empty-copy">Tap + to write one, or ask AutoYou to save a note for you.</p>
    </div>
  `;
}

function skeletonMarkup(count = 3) {
  return Array.from({ length: count }, () => `
    <div class="skeleton-card" aria-hidden="true">
      <span class="note-icon"></span>
      <span class="skeleton-lines">
        <span class="skeleton-line medium"></span>
        <span class="skeleton-line short"></span>
        <span class="skeleton-line"></span>
      </span>
    </div>
  `).join("");
}

function renderList() {
  const container = document.getElementById("note-list");
  if (!container) {
    return;
  }
  let html;
  if (state.notes.length) {
    html = state.notes
      .map((note) => `
        <article class="note-card accent-${accentOf(note)}${note.id === state.activeNoteId ? " active" : ""}" data-note-id="${note.id}">
          <button class="note-item" type="button" data-action="open" data-note-id="${note.id}">
            <span class="note-icon" aria-hidden="true">${icon(categoryIconOf(note))}</span>
            <span class="note-body">
              <span class="note-title">${escapeHtml(note.title)}</span>
              <span class="note-meta">${noteMetaMarkup(note)}</span>
              <span class="note-preview">${escapeHtml(note.preview || "No content")}</span>
            </span>
            ${icon("chevron-right", "note-chevron")}
          </button>
        </article>
      `)
      .join("");
  } else if (state.loadingList) {
    html = skeletonMarkup();
  } else if (state.listError) {
    html = `<div class="error-card">${escapeHtml(state.listError)}</div>`;
  } else {
    html = emptyStateMarkup();
  }
  if (html !== renderedListHtml || !container.firstElementChild) {
    container.innerHTML = html;
    renderedListHtml = html;
  }
}

function renderModalShell({ kickerHtml, title, bodyHtml, footerHtml = "" }) {
  const modal = document.getElementById("note-modal");
  const kickerEl = document.getElementById("note-modal-kicker");
  const titleEl = document.getElementById("note-modal-title");
  const contentEl = document.getElementById("note-modal-content");
  const footerEl = document.getElementById("note-modal-footer");

  if (modal) {
    modal.dataset.mode = state.modalMode;
  }
  if (kickerEl) {
    kickerEl.innerHTML = kickerHtml;
  }
  if (titleEl) {
    titleEl.textContent = title;
  }
  if (contentEl) {
    // Stop any media playing from the previously-rendered note before its <audio>/<video>
    // elements are detached by the innerHTML swap; a detached-but-playing element can keep
    // streaming until GC.
    stopModalMedia(contentEl);
    contentEl.innerHTML = bodyHtml;
    contentEl.scrollTop = 0;
  }
  if (footerEl) {
    footerEl.innerHTML = footerHtml;
    footerEl.hidden = !footerHtml;
  }
}

function kickerMarkup(item, text) {
  return `${noteIconTile(item)}<span class="sheet-kicker-text">${escapeHtml(text)}</span>`;
}

function noteKicker(note) {
  return kickerMarkup(note, `${note.category || "Note"} • ${formatRelativeDay(note.updated_at || note.created_at)}`);
}

function syncModalActions() {
  const modal = document.getElementById("note-modal");
  const editButton = document.getElementById("note-edit-toggle");
  const deleteButton = document.getElementById("note-delete-toggle");
  const closeButton = document.getElementById("note-modal-close");
  const hasActiveNote = !!state.activeNote && !state.loadingDetail;
  if (modal) {
    modal.dataset.mode = state.modalMode;
  }
  if (editButton) {
    editButton.hidden = state.modalMode === "edit" || state.modalMode === "create";
    editButton.disabled = !hasActiveNote || state.savingNote || state.deletingNote;
  }
  if (deleteButton) {
    deleteButton.hidden = state.modalMode === "create";
    deleteButton.disabled = !hasActiveNote || state.savingNote || state.deletingNote;
  }
  if (closeButton) {
    closeButton.disabled = false;
  }
}

function renderLoadingModal() {
  const listed = state.notes.find((note) => note.id === state.activeNoteId) || null;
  renderModalShell({
    kickerHtml: kickerMarkup(listed, "Loading..."),
    title: listed ? listed.title : "Loading note...",
    bodyHtml: `
      <div class="detail-loading" aria-live="polite">
        <div class="detail-loading-bar short"></div>
        <div class="detail-loading-bar"></div>
        <div class="detail-loading-bar medium"></div>
        <div class="detail-loading-bar"></div>
      </div>
    `,
  });
  syncModalActions();
}

// ── Attachments ─────────────────────────────────
// Photos show as a gallery that opens full screen, videos play in the note,
// and other files open in a new tab. Files stream from /api/media/{id}.

const IMAGE_NAME = /\.(jpe?g|png|gif|webp|avif|bmp|heic|heif)$/i;
const VIDEO_NAME = /\.(mp4|m4v|mov|webm|ogv)$/i;
const AUDIO_NAME = /\.(mp3|m4a|aac|wav|ogg|oga|opus|flac|weba|amr)$/i;
const VIEWER_DOUBLE_TAP_MS = 280;

function noteAttachments(metadata) {
  const attachments = metadata && Array.isArray(metadata.media_attachments) ? metadata.media_attachments : [];
  return attachments.filter((att) => att && /^\d+$/.test(String(att.id ?? "").trim()));
}

function attachmentName(att) {
  return String(att.filename || att.name || "Attachment");
}

// The saved type decides; files saved without one fall back to their name.
function attachmentKind(att) {
  const mime = String(att.mimetype || att.mime || "").split(";")[0].trim().toLowerCase();
  const name = attachmentName(att);
  const unknown = !mime || mime === "application/octet-stream";
  if (mime.startsWith("image/") && mime !== "image/svg+xml") return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (unknown && IMAGE_NAME.test(name)) return "image";
  if (unknown && VIDEO_NAME.test(name)) return "video";
  if (unknown && AUDIO_NAME.test(name)) return "audio";
  return "file";
}

function attachmentUrl(att) {
  return `./api/media/${encodeURIComponent(String(att.id).trim())}`;
}

function formatBytes(bytes) {
  const size = Number(bytes) || 0;
  if (size < 1024) {
    return `${size} B`;
  }
  const units = ["KB", "MB", "GB"];
  let value = size / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

function attachmentFacts(att) {
  const extension = (attachmentName(att).match(/\.([A-Za-z0-9]{1,5})$/) || [])[1];
  const size = Number(att.size_bytes || att.size || 0);
  return [extension ? extension.toUpperCase() : "", size > 0 ? formatBytes(size) : ""].filter(Boolean).join(" · ");
}

function attachmentCaption(att) {
  const facts = attachmentFacts(att);
  return `<span class="attachment-body"><span class="attachment-name">${escapeHtml(attachmentName(att))}</span>`
    + (facts ? `<span class="attachment-meta">${escapeHtml(facts)}</span>` : "")
    + "</span>";
}

function notePhotos(note) {
  return noteAttachments(note && note.metadata).filter((att) => attachmentKind(att) === "image");
}

function fileAttachmentMarkup(att) {
  return `<a class="attachment-file" href="${attachmentUrl(att)}" target="_blank" rel="noopener">`
    + `<span class="attachment-icon" aria-hidden="true">${icon("doc")}</span>`
    + attachmentCaption(att)
    + icon("external", "attachment-open")
    + "</a>";
}

function renderAttachments(metadata) {
  const attachments = noteAttachments(metadata);
  if (!attachments.length) return "";

  const photos = attachments.filter((att) => attachmentKind(att) === "image");
  const gallery = photos.length
    ? `<div class="attachment-gallery${photos.length === 1 ? " single" : ""}">`
      + photos.map((att, index) => {
        const name = escapeHtml(attachmentName(att));
        return `<button class="attachment-photo" type="button" data-photo-index="${index}" aria-label="View ${name} full screen">`
          + `<img src="${attachmentUrl(att)}" alt="${name}" loading="lazy" decoding="async"></button>`;
      }).join("")
      + "</div>"
    : "";

  const rows = attachments.filter((att) => attachmentKind(att) !== "image").map((att) => {
    const url = attachmentUrl(att);
    const kind = attachmentKind(att);
    if (kind === "video") {
      // The #t fragment makes phones draw the first frame before playback.
      return `<figure class="attachment-video">`
        + `<video controls playsinline preload="metadata" src="${url}#t=0.1"></video>`
        + `<figcaption class="attachment-caption">${attachmentCaption(att)}`
        + `<a class="attachment-link" href="${url}" target="_blank" rel="noopener" aria-label="Open ${escapeHtml(attachmentName(att))}">${icon("external")}</a>`
        + "</figcaption></figure>";
    }
    if (kind === "audio") {
      // preload="metadata" so the control shows real duration / a seekable timeline as
      // soon as the note opens, instead of sitting at 00:00 / 00:00 until first play.
      return `<div class="attachment-audio">`
        + `<span class="attachment-icon accent-purple" aria-hidden="true">${icon("mic")}</span>`
        + `<div class="attachment-audio-body">${attachmentCaption(att)}`
        + `<audio controls preload="metadata" class="attachment-audio-player" src="${url}">`
        + `<a href="${url}" target="_blank" rel="noopener">Download ${escapeHtml(attachmentName(att))}</a></audio>`
        + "</div></div>";
    }
    return fileAttachmentMarkup(att);
  }).join("");

  return `<section class="attachments-section" aria-label="Attachments">
    <p class="attachments-heading">${icon("clip")}Attachments (${attachments.length})</p>
    ${gallery}${rows ? `<div class="attachment-list">${rows}</div>` : ""}
  </section>`;
}

// A photo the browser can't draw (for example HEIC outside Safari) becomes a file link.
document.addEventListener("error", (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement)) {
    return;
  }
  const tile = image.closest(".attachment-photo");
  const photos = notePhotos(state.activeNote);
  const att = tile ? photos[Number(tile.dataset.photoIndex)] : null;
  if (tile && att) {
    tile.outerHTML = fileAttachmentMarkup(att);
  } else if (image.classList.contains("media-viewer-image")) {
    image.replaceWith(Object.assign(document.createElement("p"), {
      className: "media-viewer-note",
      textContent: "This photo can't be shown here. Download it to open it.",
    }));
  }
}, true);

// ── Photo viewer ────────────────────────────────

const mediaViewer = {
  open: false,
  photos: [],
  index: 0,
  trigger: null,
  pushed: false,
  ignoredPops: 0,
  lastTap: 0,
  scrollFrame: 0,
  target: null,
  targetTimer: null,
};

function mediaViewerSlides() {
  return Array.from(document.querySelectorAll("#media-viewer-track .media-viewer-slide"));
}

function syncMediaViewer() {
  const photo = mediaViewer.photos[mediaViewer.index];
  if (!photo) {
    return;
  }
  const total = mediaViewer.photos.length;
  document.getElementById("media-viewer-name").textContent = attachmentName(photo);
  document.getElementById("media-viewer-count").textContent = total > 1 ? `${mediaViewer.index + 1} of ${total}` : "";
  const download = document.getElementById("media-viewer-download");
  download.href = attachmentUrl(photo);
  download.setAttribute("download", attachmentName(photo));
  document.getElementById("media-viewer-prev").hidden = total < 2;
  document.getElementById("media-viewer-next").hidden = total < 2;
  document.getElementById("media-viewer-prev").disabled = mediaViewer.index <= 0;
  document.getElementById("media-viewer-next").disabled = mediaViewer.index >= total - 1;
  mediaViewerSlides().forEach((slide, position) => {
    slide.setAttribute("aria-hidden", String(position !== mediaViewer.index));
    if (position !== mediaViewer.index && slide.classList.contains("is-zoomed")) {
      setSlideZoom(slide, false);
    }
  });
}

function openMediaViewer(photos, index, trigger) {
  if (!photos.length) {
    return;
  }
  const shell = document.getElementById("media-viewer");
  const track = document.getElementById("media-viewer-track");
  mediaViewer.open = true;
  mediaViewer.photos = photos;
  mediaViewer.index = Math.max(0, Math.min(index, photos.length - 1));
  mediaViewer.target = null;
  mediaViewer.trigger = trigger || document.activeElement;
  track.innerHTML = photos.map((att, position) => `<div class="media-viewer-slide" data-index="${position}">`
    + `<img class="media-viewer-image" src="${attachmentUrl(att)}" alt="${escapeHtml(attachmentName(att))}" decoding="async"></div>`).join("");
  shell.hidden = false;
  shell.setAttribute("aria-hidden", "false");
  document.getElementById("note-modal").inert = true;
  track.scrollLeft = mediaViewer.index * track.clientWidth;
  syncMediaViewer();
  document.querySelectorAll("#note-modal-content audio, #note-modal-content video").forEach((media) => media.pause());
  try {
    window.history.pushState({ autoyouNotesViewer: true }, "", window.location.href);
    mediaViewer.pushed = true;
  } catch (_) {
    mediaViewer.pushed = false;
  }
  track.focus({ preventScroll: true });
}

function closeMediaViewer({ fromHistory = false } = {}) {
  if (!mediaViewer.open) {
    return;
  }
  mediaViewer.open = false;
  const shell = document.getElementById("media-viewer");
  shell.hidden = true;
  shell.setAttribute("aria-hidden", "true");
  document.getElementById("media-viewer-track").innerHTML = "";
  document.getElementById("note-modal").inert = false;
  if (!fromHistory && mediaViewer.pushed) {
    mediaViewer.ignoredPops += 1;
    window.history.back();
  }
  mediaViewer.pushed = false;
  mediaViewer.trigger?.focus?.({ preventScroll: true });
}

function goMediaViewer(step) {
  const track = document.getElementById("media-viewer-track");
  // Quick repeated presses add up while the previous scroll is still moving.
  const base = mediaViewer.target === null ? mediaViewer.index : mediaViewer.target;
  const next = Math.max(0, Math.min(base + step, mediaViewer.photos.length - 1));
  mediaViewer.target = next;
  window.clearTimeout(mediaViewer.targetTimer);
  mediaViewer.targetTimer = window.setTimeout(() => {
    mediaViewer.target = null;
  }, 700);
  const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  track.scrollTo({ left: next * track.clientWidth, behavior: smooth ? "smooth" : "auto" });
}

// Double tap zooms in around the tapped point; the zoomed photo pans freely.
function setSlideZoom(slide, zoomed, event) {
  const image = slide.querySelector(".media-viewer-image");
  if (!image) {
    return;
  }
  const box = image.getBoundingClientRect();
  const pointX = event ? Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1) : 0.5;
  const pointY = event ? Math.min(Math.max((event.clientY - box.top) / box.height, 0), 1) : 0.5;
  slide.classList.toggle("is-zoomed", zoomed);
  image.style.width = zoomed ? `${Math.round(Math.max(box.width * 2.5, Math.min(image.naturalWidth, box.width * 4)))}px` : "";
  if (zoomed) {
    slide.scrollLeft = image.offsetLeft + pointX * image.offsetWidth - slide.clientWidth / 2;
    slide.scrollTop = image.offsetTop + pointY * image.offsetHeight - slide.clientHeight / 2;
  }
}

function setupMediaViewer() {
  const shell = document.getElementById("media-viewer");
  const track = document.getElementById("media-viewer-track");
  if (!shell || !track) {
    return;
  }
  document.getElementById("note-modal-content").addEventListener("click", (event) => {
    const tile = event.target.closest("[data-photo-index]");
    if (tile) {
      openMediaViewer(notePhotos(state.activeNote), Number(tile.dataset.photoIndex), tile);
    }
  });
  track.addEventListener("scroll", () => {
    window.cancelAnimationFrame(mediaViewer.scrollFrame);
    mediaViewer.scrollFrame = window.requestAnimationFrame(() => {
      const index = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
      if (index !== mediaViewer.index && mediaViewer.photos[index]) {
        mediaViewer.index = index;
        syncMediaViewer();
      }
    });
  }, { passive: true });
  track.addEventListener("click", (event) => {
    const image = event.target.closest(".media-viewer-image");
    if (!image) {
      return;
    }
    const now = Date.now();
    if (now - mediaViewer.lastTap < VIEWER_DOUBLE_TAP_MS) {
      mediaViewer.lastTap = 0;
      const slide = image.closest(".media-viewer-slide");
      setSlideZoom(slide, !slide.classList.contains("is-zoomed"), event);
      return;
    }
    mediaViewer.lastTap = now;
  });
  document.getElementById("media-viewer-close").addEventListener("click", () => closeMediaViewer());
  document.getElementById("media-viewer-prev").addEventListener("click", () => goMediaViewer(-1));
  document.getElementById("media-viewer-next").addEventListener("click", () => goMediaViewer(1));
  shell.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      goMediaViewer(event.key === "ArrowLeft" ? -1 : 1);
    }
  });
  window.addEventListener("popstate", () => {
    if (mediaViewer.ignoredPops > 0) {
      mediaViewer.ignoredPops -= 1;
      return;
    }
    if (mediaViewer.open) {
      mediaViewer.pushed = false;
      closeMediaViewer({ fromHistory: true });
    }
  });
  window.addEventListener("resize", () => {
    if (mediaViewer.open) {
      track.scrollLeft = mediaViewer.index * track.clientWidth;
    }
  }, { passive: true });
}

function renderNoteView(note, errorMessage = "") {
  const tagsHtml = (note.tags || [])
    .map((tag) => `<span class="tag-chip">#${escapeHtml(tag)}</span>`)
    .join("");
  const hasContent = !!String(note.content || "").trim();
  renderModalShell({
    kickerHtml: noteKicker(note),
    title: note.title || "Untitled note",
    bodyHtml: `
      <div class="detail">
        ${errorMessage ? `<div class="error-card">${escapeHtml(errorMessage)}</div>` : ""}
        <p class="detail-text${hasContent ? "" : " is-empty"}">${escapeHtml(hasContent ? note.content : "This note is empty.")}</p>
        ${tagsHtml ? `<div class="tag-row">${tagsHtml}</div>` : ""}
        ${renderAttachments(note.metadata)}
        <dl class="detail-info">
          <div><dt>Category</dt><dd>${escapeHtml(note.category || "None")}</dd></div>
          <div><dt>Created</dt><dd>${escapeHtml(formatTimestamp(note.created_at))}</dd></div>
          <div><dt>Updated</dt><dd>${escapeHtml(formatTimestamp(note.updated_at || note.created_at))}</dd></div>
          <div><dt>Note</dt><dd>#${escapeHtml(note.id)}</dd></div>
        </dl>
      </div>
    `,
  });
  syncModalActions();
}

function focusEditorField() {
  const target = window.matchMedia("(max-width: 720px)").matches
    ? document.getElementById("note-edit-content")
    : document.getElementById("note-edit-title");
  if (!target) {
    return;
  }
  window.setTimeout(() => {
    try {
      target.focus({ preventScroll: true });
    } catch (_) {
      target.focus();
    }
    if (typeof target.setSelectionRange === "function" && target.value) {
      const length = target.value.length;
      target.setSelectionRange(length, length);
    }
    const isMobile = window.matchMedia("(max-width: 768px)").matches || /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);
    if (!isMobile) {
      target.scrollIntoView({ block: "center", behavior: "smooth" });
    } else {
      target.scrollIntoView({ block: "nearest", behavior: "auto" });
    }
  }, 60);
}

function editorFormMarkup(note) {
  const categoryOptions = state.categories
    .map((item) => `<option value="${escapeHtml(item.name)}"></option>`)
    .join("");
  return `
    <form id="note-edit-form" class="editor-form" autocomplete="off">
      <div class="field">
        <label for="note-edit-title">Title</label>
        <input id="note-edit-title" class="text-input" name="title" type="text" value="${escapeHtml(note.title || "")}" maxlength="240" enterkeyhint="next" placeholder="Note title">
      </div>
      <div class="field-grid">
        <div class="field">
          <label for="note-edit-category">Category</label>
          <input id="note-edit-category" class="text-input" name="category" type="text" value="${escapeHtml(note.category || "")}" maxlength="120" enterkeyhint="next" list="note-category-options" placeholder="Personal, Work, Ideas...">
          <datalist id="note-category-options">${categoryOptions}</datalist>
        </div>
        <div class="field">
          <label for="note-edit-tags">Tags</label>
          <input id="note-edit-tags" class="text-input" name="tags" type="text" value="${escapeHtml((note.tags || []).join(", "))}" placeholder="work, idea, urgent" enterkeyhint="next">
        </div>
      </div>
      <div class="field">
        <label for="note-edit-content">Note</label>
        <textarea id="note-edit-content" class="textarea-input" name="content" rows="12" placeholder="Write your note here...">${escapeHtml(note.content || "")}</textarea>
      </div>
    </form>
  `;
}

function renderNoteEditor(note) {
  renderModalShell({
    kickerHtml: `<span class="note-icon accent-blue" aria-hidden="true">${icon("pencil")}</span><span class="sheet-kicker-text">Editing</span>`,
    title: note.title || "Untitled note",
    bodyHtml: editorFormMarkup(note),
    footerHtml: `
      <button id="note-cancel-button" class="button subtle" type="button">Cancel</button>
      <button id="note-save-button" class="button primary" type="submit" form="note-edit-form">
        <span class="button-label">${state.savingNote ? "Saving..." : "Save"}</span>
      </button>
    `,
  });
  syncModalActions();
  focusEditorField();
}

function renderNewNoteEditor() {
  renderModalShell({
    kickerHtml: `<span class="note-icon accent-blue" aria-hidden="true">${icon("plus")}</span><span class="sheet-kicker-text">New note</span>`,
    title: "Create note",
    bodyHtml: editorFormMarkup({ category: state.activeCategory }),
    footerHtml: `
      <button id="note-cancel-button" class="button subtle" type="button">Cancel</button>
      <button id="note-save-button" class="button primary" type="submit" form="note-edit-form">
        <span class="button-label">${state.savingNote ? "Creating..." : "Create"}</span>
      </button>
    `,
  });
  syncModalActions();
  focusEditorField();
}

function openCreateNote() {
  closeMenus();
  state.activeNoteId = null;
  state.activeNote = null;
  state.modalMode = "create";
  state.lastModalTrigger = document.activeElement;
  setModalOpen(true);
  renderNewNoteEditor();
}

function showEditorError(message) {
  const contentEl = document.getElementById("note-modal-content");
  if (!contentEl) {
    return;
  }
  const existing = contentEl.querySelector(".error-card");
  if (existing) {
    existing.textContent = message;
  } else {
    contentEl.insertAdjacentHTML("afterbegin", `<div class="error-card">${escapeHtml(message)}</div>`);
  }
}

async function saveNewNote() {
  if (state.savingNote) return;
  const form = document.getElementById("note-edit-form");
  if (!(form instanceof HTMLFormElement)) return;
  state.savingNote = true;

  const saveButton = document.getElementById("note-save-button");
  if (saveButton) { saveButton.disabled = true; saveButton.querySelector(".button-label").textContent = "Creating..."; }

  const formData = new FormData(form);
  const payload = {
    title: String(formData.get("title") || "").trim() || "Untitled note",
    category: String(formData.get("category") || "").trim(),
    tags: String(formData.get("tags") || ""),
    content: String(formData.get("content") || ""),
  };

  try {
    const response = await fetch("./api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
    const result = await readJson(response);
    if (!response.ok || !result.success) {
      throw new Error(result.error || `HTTP ${response.status}`);
    }
    const newNote = result.note ? normalizeNote(result.note) : null;
    if (newNote) {
      adjustCategoryCount(newNote.category, 1, newNote);
      if (!state.activeCategory || state.activeCategory === newNote.category) {
        state.notes = sortNotes([newNote, ...state.notes]);
        state.totalCount = (state.totalCount || 0) + 1;
      }
      state.activeNoteId = newNote.id;
      state.activeNote = newNote;
      state.modalMode = "view";
      updateHeaderStats();
      renderCategoryFilters();
      renderList();
      renderNoteView(newNote);
      updateStatus(`Created note ${newNote.id}.`);
    } else {
      closeNoteModal({ restoreFocus: true, clearActive: true });
      void refreshNotes({ quiet: true });
    }
  } catch (error) {
    showEditorError(error.message || String(error));
  } finally {
    state.savingNote = false;
    syncModalActions();
    if (saveButton) {
      saveButton.disabled = false;
      const label = saveButton.querySelector(".button-label");
      if (label && state.modalMode === "create") {
        label.textContent = "Create";
      }
    }
  }
}

// Pause (and rewind) every audio/video element inside `root`. The note modal is only
// hidden via a CSS class on close, so without this an attachment's <audio> keeps playing
// after the note is dismissed.
function stopModalMedia(root) {
  const scope = root || document.getElementById("note-modal");
  if (!scope || typeof scope.querySelectorAll !== "function") {
    return;
  }
  scope.querySelectorAll("audio, video").forEach((media) => {
    try {
      media.pause();
      media.currentTime = 0;
    } catch (_) {}
  });
}

function setModalOpen(open) {
  const modal = document.getElementById("note-modal");
  if (!modal) {
    return;
  }
  if (!open) {
    stopModalMedia(modal);
  }
  modal.classList.toggle("open", !!open);
  modal.setAttribute("aria-hidden", open ? "false" : "true");
  document.body.classList.toggle("modal-open", !!open || state.confirmDeleteNoteId !== null);
  if (open && state.modalMode !== "edit" && state.modalMode !== "create") {
    window.setTimeout(() => {
      const closeButton = document.getElementById("note-modal-close");
      if (closeButton) {
        closeButton.focus();
      }
    }, 0);
  }
}

function noteSheetOpen() {
  return !!document.getElementById("note-modal")?.classList.contains("open")
    || !!document.getElementById("delete-confirm")?.classList.contains("open");
}

function closeNoteModal(options = {}) {
  setModalOpen(false);
  closeDeleteConfirm({ restoreFocus: false });
  state.modalMode = "view";
  if (options.clearActive) {
    state.activeNoteId = null;
    state.activeNote = null;
    renderList();
  }
  let trigger = state.lastModalTrigger;
  // Re-rendering the list replaces the card that opened the sheet.
  if (trigger && !trigger.isConnected && trigger.dataset && trigger.dataset.noteId) {
    trigger = document.querySelector(`#note-list [data-action="open"][data-note-id="${trigger.dataset.noteId}"]`);
  }
  if (options.restoreFocus !== false && trigger && typeof trigger.focus === "function") {
    trigger.focus();
  }
}

function setDeleteConfirmOpen(open) {
  const confirmEl = document.getElementById("delete-confirm");
  if (!confirmEl) {
    return;
  }
  confirmEl.classList.toggle("open", !!open);
  confirmEl.setAttribute("aria-hidden", open ? "false" : "true");
  document.body.classList.toggle("modal-open", !!open || document.getElementById("note-modal")?.classList.contains("open"));
}

function renderDeleteConfirm() {
  if (state.confirmDeleteNoteId === null) {
    return;
  }
  const note = state.notes.find((item) => item.id === state.confirmDeleteNoteId) || state.activeNote;
  const noteLabel = note && note.title ? `"${note.title}"` : `note ${state.confirmDeleteNoteId}`;
  const titleEl = document.getElementById("delete-confirm-title");
  const messageEl = document.getElementById("delete-confirm-message");
  const errorEl = document.getElementById("delete-confirm-error");
  const submitButton = document.getElementById("delete-confirm-submit");

  if (titleEl) {
    titleEl.textContent = `Delete ${noteLabel}?`;
  }
  if (messageEl) {
    messageEl.textContent = "This cannot be undone.";
  }
  if (errorEl) {
    errorEl.hidden = !state.confirmDeleteError;
    errorEl.textContent = state.confirmDeleteError;
  }
  if (submitButton) {
    submitButton.disabled = state.deletingNote;
    submitButton.textContent = state.deletingNote ? "Deleting..." : "Delete";
  }
}

function openDeleteConfirm(noteId, triggerEl = null) {
  state.confirmDeleteNoteId = noteId;
  state.confirmDeleteTrigger = triggerEl || document.activeElement;
  state.confirmDeleteError = "";
  renderDeleteConfirm();
  setDeleteConfirmOpen(true);
  window.setTimeout(() => {
    const submitButton = document.getElementById("delete-confirm-submit");
    if (submitButton) {
      submitButton.focus();
    }
  }, 0);
}

function closeDeleteConfirm(options = {}) {
  const shouldRestoreFocus = options.restoreFocus !== false;
  const focusTarget = state.confirmDeleteTrigger;
  state.confirmDeleteNoteId = null;
  state.confirmDeleteTrigger = null;
  state.confirmDeleteError = "";
  setDeleteConfirmOpen(false);
  if (shouldRestoreFocus && focusTarget && typeof focusTarget.focus === "function") {
    focusTarget.focus();
  }
}

function syncViewportMetrics() {
  const viewport = window.visualViewport;
  const height = Math.round(viewport ? viewport.height : window.innerHeight);
  document.documentElement.style.setProperty("--notes-viewport-height", `${height}px`);
}

async function fetchNoteDetail(noteId) {
  const response = await fetch(`./api/notes/${noteId}`, { cache: "no-store" });
  const payload = await readJson(response);
  if (!response.ok || !payload.success || !payload.note) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return normalizeNote(payload.note);
}

async function openNote(noteId, { triggerEl = null, mode = "view" } = {}) {
  closeMenus();
  state.activeNoteId = noteId;
  state.lastModalTrigger = triggerEl || document.activeElement;
  state.loadingDetail = true;
  state.modalMode = mode;
  renderList();
  setModalOpen(true);
  renderLoadingModal();

  try {
    const note = await fetchNoteDetail(noteId);
    if (state.activeNoteId !== noteId) {
      return;
    }
    state.activeNote = note;
    state.notes = sortNotes(
      state.notes.map((entry) => (entry.id === note.id ? { ...entry, ...note } : entry)),
    );
    state.loadingDetail = false;
    renderList();
    if (state.modalMode === "edit") {
      renderNoteEditor(note);
    } else {
      renderNoteView(note);
    }
  } catch (error) {
    state.loadingDetail = false;
    const fallbackNote = state.notes.find((note) => note.id === noteId) || null;
    if (fallbackNote) {
      state.activeNote = fallbackNote;
      renderNoteView(fallbackNote, error.message || String(error));
    } else {
      renderModalShell({
        kickerHtml: `<span class="note-icon accent-rose" aria-hidden="true">${icon("alert")}</span><span class="sheet-kicker-text">Note</span>`,
        title: "Unable to load note",
        bodyHtml: `<div class="error-card">${escapeHtml(error.message || String(error))}</div>`,
      });
    }
    syncModalActions();
  }
}

async function fetchNotesPage({ reset = false, quiet = false } = {}) {
  if (state.loadingList && !reset) {
    return;
  }
  state.loadingList = true;
  setLoadingMore(!reset);
  setListBusy(reset && !quiet && state.notes.length > 0);
  if (!quiet) {
    updateStatus(reset ? "Refreshing notes..." : "Loading more notes...");
    setSyncState("syncing");
  }
  if (reset && !state.notes.length) {
    renderList();
  }
  const requestToken = ++listRequestToken;

  try {
    const params = new URLSearchParams();
    params.set("limit", String(PAGE_SIZE));
    if (!reset && state.nextCursor) {
      params.set("cursor", state.nextCursor);
    }
    if (state.currentQuery) {
      params.set("query", state.currentQuery);
    }
    if (state.activeCategory) {
      params.set("category", state.activeCategory);
    }
    if (state.order !== "newest") {
      params.set("order", state.order);
    }
    const response = await fetch(`./api/notes?${params.toString()}`, { cache: "no-store" });
    const payload = await readJson(response);
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    if (requestToken !== listRequestToken) {
      return;
    }

    const incomingNotes = Array.isArray(payload.notes) ? payload.notes.map(normalizeNote).filter((note) => note.id !== null) : [];
    state.notes = mergeNotes(state.notes, incomingNotes, { reset });
    state.nextCursor = payload.next_cursor || null;
    state.hasMore = !!payload.has_more;
    state.totalCount = Number(payload.total_count) || state.notes.length;
    state.lastLoadedAt = Date.now();
    state.listError = "";
    if (Array.isArray(payload.categories)) {
      state.categories = payload.categories.filter((item) => item && item.name);
      renderCategoryFilters();
    }
    updateHeaderStats();
    renderList();
    updateStatus();
    setSyncState("synced", `Updated ${formatClockTime(new Date())}`);

    if (
      state.activeNoteId !== null
      && !state.loadingDetail
      && !noteSheetOpen()
      && !state.notes.some((note) => note.id === state.activeNoteId)
    ) {
      state.activeNoteId = null;
      state.activeNote = null;
    }
  } catch (error) {
    if (requestToken !== listRequestToken) {
      return;
    }
    const message = error.message || String(error);
    updateStatus(`Could not load notes right now. ${message}`);
    setSyncState("error", message);
    state.listError = `Could not load notes. ${message}`;
  } finally {
    if (requestToken === listRequestToken) {
      state.loadingList = false;
      setLoadingMore(false);
      setListBusy(false);
      if (!state.notes.length) {
        renderList();
      }
    }
  }
}

async function refreshNotes(options = {}) {
  state.nextCursor = null;
  state.hasMore = false;
  await fetchNotesPage({ reset: true, quiet: !!options.quiet });
}

async function requestJsonWithFallback(attempts) {
  let lastError = new Error("Request failed.");
  const fallbackStatuses = new Set([404, 405, 501]);
  for (let index = 0; index < attempts.length; index += 1) {
    const attempt = attempts[index];
    try {
      const response = await fetch(attempt.url, attempt.options);
      const payload = await readJson(response);
      if (!response.ok && fallbackStatuses.has(response.status) && index < attempts.length - 1) {
        lastError = new Error(payload.error || `HTTP ${response.status}`);
        continue;
      }
      return { response, payload };
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
      if (index === attempts.length - 1) {
        throw lastError;
      }
    }
  }
  throw lastError;
}

function buildUpdateAttempts(noteId, payload) {
  const body = JSON.stringify(payload);
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  return [
    {
      url: `./api/notes/${noteId}/update`,
      options: {
        method: "POST",
        headers,
        body,
        cache: "no-store",
      },
    },
    {
      url: `./api/notes/${noteId}`,
      options: {
        method: "PATCH",
        headers,
        body,
        cache: "no-store",
      },
    },
  ];
}

function buildDeleteAttempts(noteId) {
  return [
    {
      url: `./api/notes/${noteId}/delete`,
      options: {
        method: "POST",
        headers: {
          Accept: "application/json",
        },
        cache: "no-store",
      },
    },
    {
      url: `./api/notes/${noteId}`,
      options: {
        method: "DELETE",
        headers: {
          Accept: "application/json",
        },
        cache: "no-store",
      },
    },
  ];
}

async function saveActiveNote() {
  if (!state.activeNote || state.savingNote) {
    return;
  }
  const form = document.getElementById("note-edit-form");
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  state.savingNote = true;
  syncModalActions();
  const saveButton = document.getElementById("note-save-button");
  if (saveButton) {
    saveButton.disabled = true;
  }

  const formData = new FormData(form);
  const payload = {
    title: String(formData.get("title") || "").trim(),
    category: String(formData.get("category") || "").trim(),
    tags: String(formData.get("tags") || ""),
    content: String(formData.get("content") || ""),
  };
  const previousCategory = state.activeNote.category;

  try {
    const { response, payload: result } = await requestJsonWithFallback(buildUpdateAttempts(state.activeNote.id, payload));
    if (!response.ok || !result.success || !result.note) {
      throw new Error(result.error || `HTTP ${response.status}`);
    }
    const updatedNote = normalizeNote(result.note);
    if (updatedNote.category !== previousCategory) {
      adjustCategoryCount(previousCategory, -1);
      adjustCategoryCount(updatedNote.category, 1, updatedNote);
      renderCategoryFilters();
    }
    state.activeNote = updatedNote;
    state.modalMode = "view";
    state.notes = sortNotes(
      state.notes.map((note) => (note.id === updatedNote.id ? { ...note, ...updatedNote } : note)),
    );
    updateHeaderStats();
    renderList();
    renderNoteView(updatedNote);
    updateStatus(`Saved note ${updatedNote.id}.`);
    if (state.currentQuery || state.activeCategory) {
      void refreshNotes({ quiet: true });
    }
  } catch (error) {
    showEditorError(error.message || String(error));
  } finally {
    state.savingNote = false;
    syncModalActions();
    if (saveButton) {
      saveButton.disabled = false;
    }
  }
}

async function confirmDeleteNote() {
  if (state.deletingNote || !Number.isFinite(Number(state.confirmDeleteNoteId))) {
    return;
  }
  const noteId = Number(state.confirmDeleteNoteId);
  const deletedNote = state.notes.find((note) => note.id === noteId) || state.activeNote;
  state.deletingNote = true;
  state.confirmDeleteError = "";
  syncModalActions();
  renderDeleteConfirm();

  try {
    const { response, payload } = await requestJsonWithFallback(buildDeleteAttempts(noteId));
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }

    state.notes = state.notes.filter((note) => note.id !== noteId);
    state.totalCount = Math.max(0, (state.totalCount || state.notes.length) - 1);
    if (deletedNote && deletedNote.id === noteId) {
      adjustCategoryCount(deletedNote.category, -1);
      renderCategoryFilters();
    }
    if (state.activeNoteId === noteId) {
      closeNoteModal({ restoreFocus: false, clearActive: true });
    }
    closeDeleteConfirm({ restoreFocus: false });
    renderList();
    updateHeaderStats();
    updateStatus(`Deleted note ${noteId}.`);

    if (state.hasMore && state.notes.length < PAGE_SIZE) {
      await fetchNotesPage({ reset: false, quiet: true });
    }
  } catch (error) {
    state.confirmDeleteError = `Could not delete note ${noteId}. ${error.message || String(error)}`;
    renderDeleteConfirm();
    updateStatus(state.confirmDeleteError);
  } finally {
    state.deletingNote = false;
    syncModalActions();
    renderDeleteConfirm();
  }
}

function configureFrontendsLink() {
  const link = document.getElementById("all-frontends-link");
  if (!link) {
    return;
  }
  let targetUrl = "../../websites";
  try {
    targetUrl = new URL("../../websites", window.location.href).toString();
  } catch (_) {}
  link.href = targetUrl;
}

function setupInfiniteScroll() {
  const sentinel = document.getElementById("note-list-sentinel");
  if (!sentinel || typeof IntersectionObserver !== "function") {
    return;
  }
  if (listObserver) {
    listObserver.disconnect();
  }
  listObserver = new IntersectionObserver((entries) => {
    const entry = entries[0];
    if (!entry || !entry.isIntersecting) {
      return;
    }
    if (state.hasMore && !state.loadingList) {
      void fetchNotesPage({ reset: false, quiet: true });
    }
  }, { rootMargin: "260px 0px 260px 0px" });
  listObserver.observe(sentinel);
}

function setupAutoRefresh() {
  if (refreshInterval !== null) {
    window.clearInterval(refreshInterval);
  }
  refreshInterval = window.setInterval(() => {
    if (document.visibilityState !== "visible") {
      return;
    }
    if (noteSheetOpen() || state.notes.length > PAGE_SIZE) {
      return;
    }
    if (state.loadingList || state.savingNote || state.deletingNote) {
      return;
    }
    if (Date.now() - state.lastLoadedAt < AUTO_REFRESH_INTERVAL_MS) {
      return;
    }
    void refreshNotes({ quiet: true });
  }, AUTO_REFRESH_INTERVAL_MS);
}

// ── Menus ────────────────────────────────────────

const MENUS = [
  { toggle: "notes-menu-toggle", menu: "notes-menu" },
  { toggle: "notes-sort-toggle", menu: "notes-sort-menu" },
];

function menuItems(menu) {
  return Array.from(menu.querySelectorAll(".menu-item"));
}

function setMenuOpen(entry, open, { focus = false } = {}) {
  const toggle = document.getElementById(entry.toggle);
  const menu = document.getElementById(entry.menu);
  if (!toggle || !menu) {
    return;
  }
  menu.hidden = !open;
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
  if (open && focus) {
    const items = menuItems(menu);
    const checked = items.find((item) => item.getAttribute("aria-checked") === "true");
    (checked || items[0])?.focus();
  }
}

function closeMenus({ restoreFocusTo = null } = {}) {
  MENUS.forEach((entry) => setMenuOpen(entry, false));
  if (restoreFocusTo && typeof restoreFocusTo.focus === "function") {
    restoreFocusTo.focus();
  }
}

function openMenuEntry() {
  return MENUS.find((entry) => {
    const menu = document.getElementById(entry.menu);
    return menu && !menu.hidden;
  }) || null;
}

MENUS.forEach((entry) => {
  const toggle = document.getElementById(entry.toggle);
  const menu = document.getElementById(entry.menu);
  if (!toggle || !menu) {
    return;
  }
  toggle.addEventListener("click", () => {
    const willOpen = menu.hidden;
    closeMenus();
    setMenuOpen(entry, willOpen, { focus: willOpen });
  });
  menu.addEventListener("keydown", (event) => {
    const items = menuItems(menu);
    const index = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(index + step + items.length) % items.length]?.focus();
    } else if (event.key === "Tab") {
      closeMenus();
    }
  });
});

document.addEventListener("click", (event) => {
  const entry = openMenuEntry();
  if (!entry) {
    return;
  }
  const toggle = document.getElementById(entry.toggle);
  const menu = document.getElementById(entry.menu);
  if (menu?.contains(event.target) || toggle?.contains(event.target)) {
    return;
  }
  closeMenus();
});

function setOrder(order) {
  const nextOrder = order === "oldest" ? "oldest" : "newest";
  const label = document.getElementById("notes-sort-label");
  if (label) {
    label.textContent = ORDER_LABELS[nextOrder];
  }
  document.querySelectorAll("#notes-sort-menu [data-order]").forEach((item) => {
    item.setAttribute("aria-checked", item.getAttribute("data-order") === nextOrder ? "true" : "false");
  });
  if (nextOrder === state.order) {
    return false;
  }
  state.order = nextOrder;
  return true;
}

function setCategory(category) {
  const nextCategory = String(category || "");
  if (nextCategory === state.activeCategory) {
    return false;
  }
  state.activeCategory = nextCategory;
  renderCategoryFilters();
  return true;
}

function syncSearchClear() {
  const clear = document.getElementById("note-search-clear");
  const input = document.getElementById("note-search");
  if (clear && input) {
    clear.hidden = !input.value;
  }
}

function bootstrapFromPayload(payload) {
  if (!payload || !payload.success) {
    state.notes = [];
    state.totalCount = 0;
    state.hasMore = false;
    state.nextCursor = null;
    state.listError = payload && payload.error ? payload.error : "Notes unavailable.";
    updateStatus(state.listError);
    setSyncState("error", state.listError);
    renderList();
    return;
  }
  state.order = payload.order === "oldest" ? "oldest" : "newest";
  setOrder(state.order);
  state.activeCategory = String(payload.category || "");
  state.categories = Array.isArray(payload.categories) ? payload.categories.filter((item) => item && item.name) : [];
  state.notes = mergeNotes([], Array.isArray(payload.notes) ? payload.notes : [], { reset: true });
  state.totalCount = Number(payload.total_count) || state.notes.length;
  state.hasMore = !!payload.has_more;
  state.nextCursor = payload.next_cursor || null;
  state.currentQuery = String(payload.query || "");
  state.lastLoadedAt = Date.now();
  const searchInput = document.getElementById("note-search");
  if (searchInput) {
    searchInput.value = state.currentQuery;
  }
  syncSearchClear();
  updateHeaderStats();
  renderCategoryFilters();
  renderList();
  updateStatus();
  setSyncState("synced", `Updated ${formatClockTime(new Date())}`);
}

document.getElementById("notes-new")?.addEventListener("click", () => {
  openCreateNote();
});

document.getElementById("notes-refresh").addEventListener("click", () => {
  closeMenus();
  void refreshNotes();
});

document.getElementById("all-frontends-link")?.addEventListener("click", () => {
  closeMenus();
});

document.getElementById("notes-sort-menu")?.addEventListener("click", (event) => {
  const item = event.target.closest("[data-order]");
  if (!item) {
    return;
  }
  const changed = setOrder(item.getAttribute("data-order"));
  closeMenus({ restoreFocusTo: document.getElementById("notes-sort-toggle") });
  if (changed) {
    void refreshNotes();
  }
});

document.getElementById("category-filters")?.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-category]");
  if (!chip) {
    return;
  }
  if (setCategory(chip.getAttribute("data-category"))) {
    chip.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    void refreshNotes();
  }
});

document.getElementById("note-search").addEventListener("input", (event) => {
  const query = String(event.target.value || "").trim();
  state.currentQuery = query;
  syncSearchClear();
  if (searchDebounceTimer !== null) {
    window.clearTimeout(searchDebounceTimer);
  }
  searchDebounceTimer = window.setTimeout(() => {
    void refreshNotes();
  }, 250);
});

document.getElementById("note-search-clear")?.addEventListener("click", () => {
  const input = document.getElementById("note-search");
  if (!input) {
    return;
  }
  input.value = "";
  state.currentQuery = "";
  syncSearchClear();
  input.focus();
  void refreshNotes();
});

document.getElementById("note-list").addEventListener("click", (event) => {
  if (event.target.closest("[data-action='clear-filters']")) {
    const input = document.getElementById("note-search");
    if (input) {
      input.value = "";
    }
    state.currentQuery = "";
    syncSearchClear();
    setCategory("");
    void refreshNotes();
    return;
  }
  const trigger = event.target.closest("[data-action][data-note-id]");
  if (!trigger) {
    return;
  }
  const noteId = Number(trigger.getAttribute("data-note-id"));
  if (!Number.isFinite(noteId)) {
    return;
  }
  const action = trigger.getAttribute("data-action");
  if (action === "open") {
    void openNote(noteId, { triggerEl: trigger, mode: "view" });
    return;
  }
  if (action === "edit") {
    void openNote(noteId, { triggerEl: trigger, mode: "edit" });
    return;
  }
  if (action === "delete") {
    openDeleteConfirm(noteId, trigger);
  }
});

document.getElementById("note-modal-close").addEventListener("click", () => closeNoteModal({ restoreFocus: true, clearActive: state.modalMode === "create" }));
document.getElementById("note-modal-backdrop").addEventListener("click", () => closeNoteModal({ restoreFocus: true, clearActive: state.modalMode === "create" }));

document.getElementById("note-edit-toggle").addEventListener("click", () => {
  if (!state.activeNote || state.loadingDetail) {
    return;
  }
  state.modalMode = "edit";
  renderNoteEditor(state.activeNote);
});

document.getElementById("note-delete-toggle").addEventListener("click", () => {
  if (state.activeNoteId !== null) {
    openDeleteConfirm(state.activeNoteId, document.getElementById("note-delete-toggle"));
  }
});

document.getElementById("note-modal-footer").addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) {
    return;
  }
  if (target.id === "note-cancel-button") {
    event.preventDefault();
    if (state.modalMode === "create") {
      closeNoteModal({ restoreFocus: true, clearActive: true });
    } else {
      state.modalMode = "view";
      if (state.activeNote) {
        renderNoteView(state.activeNote);
      }
    }
  }
});

document.getElementById("note-modal-content").addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || form.id !== "note-edit-form") {
    return;
  }
  event.preventDefault();
  if (state.modalMode === "create") {
    void saveNewNote();
  } else {
    void saveActiveNote();
  }
});

document.getElementById("note-modal-content").addEventListener("focusin", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !target.matches("input, textarea")) {
    return;
  }
  window.setTimeout(() => {
    const isMobile = window.matchMedia("(max-width: 768px)").matches || /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);
    if (!isMobile) {
      target.scrollIntoView({ block: "center", behavior: "smooth" });
    } else {
      target.scrollIntoView({ block: "nearest", behavior: "auto" });
    }
  }, 120);
});

document.getElementById("delete-confirm-backdrop").addEventListener("click", () => closeDeleteConfirm({ restoreFocus: true }));
document.getElementById("delete-confirm-cancel").addEventListener("click", () => closeDeleteConfirm({ restoreFocus: true }));
document.getElementById("delete-confirm-submit").addEventListener("click", () => {
  void confirmDeleteNote();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") {
    return;
  }
  if (mediaViewer.open) {
    event.preventDefault();
    closeMediaViewer();
    return;
  }
  const menuEntry = openMenuEntry();
  if (menuEntry) {
    event.preventDefault();
    closeMenus({ restoreFocusTo: document.getElementById(menuEntry.toggle) });
    return;
  }
  const confirmOpen = document.getElementById("delete-confirm")?.classList.contains("open");
  const modalOpen = document.getElementById("note-modal")?.classList.contains("open");
  if (confirmOpen) {
    event.preventDefault();
    closeDeleteConfirm({ restoreFocus: true });
    return;
  }
  if (modalOpen) {
    event.preventDefault();
    if (state.modalMode === "create") {
      closeNoteModal({ restoreFocus: true, clearActive: true });
      return;
    }
    if (state.modalMode === "edit" && state.activeNote) {
      state.modalMode = "view";
      renderNoteView(state.activeNote);
      return;
    }
    closeNoteModal({ restoreFocus: true });
  }
});

document.addEventListener("visibilitychange", () => {
  if (
    document.visibilityState === "visible"
    && !noteSheetOpen()
    && state.notes.length <= PAGE_SIZE
    && Date.now() - state.lastLoadedAt >= AUTO_REFRESH_INTERVAL_MS
  ) {
    void refreshNotes({ quiet: true });
  }
});

window.addEventListener("offline", () => setSyncState("error", "This device is offline."));
window.addEventListener("online", () => {
  void refreshNotes({ quiet: true });
});

window.addEventListener("resize", syncViewportMetrics, { passive: true });
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", syncViewportMetrics, { passive: true });
  window.visualViewport.addEventListener("scroll", syncViewportMetrics, { passive: true });
}

applyTheme(currentTheme);
loadThemePreference();
configureFrontendsLink();
syncViewportMetrics();
bootstrapFromPayload(initialPayload);
setupMediaViewer();
setupInfiniteScroll();
setupAutoRefresh();
