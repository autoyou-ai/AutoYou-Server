/**
 * AutoYou Diary — Day One-parity journal web application.
 * Synchronizes seamlessly with AutoYou native Diary agents on iOS and Android.
 */
import { applyMarkdownCommand, escapeHtml, renderMarkdown, toggleChecklistLine } from "./markdown.js";
import {
  getBasePrefix,
  openLightbox,
  openSketchPadModal,
  openVoiceRecorderModal,
  renderMedia,
  uploadDiaryMedia,
} from "./media.js";
import { renderDiaryMap } from "./map.js";
import {
  ACTIVITY_OPTIONS,
  MOOD_OPTIONS,
  WEATHER_OPTIONS,
  formatShortTime,
  formatTemp,
  openEntryInfoModal,
  openExportModal,
  openFilterModal,
  openJournalModal,
  openPreferencesModal,
  openTemplatesModal,
  parseIsoDate,
  renderCalendarView,
  renderInsightsView,
  renderMediaGalleryView,
  renderMemoriesView,
  renderTimelineList,
  todayIsoLocal,
} from "./views.js";

const base = getBasePrefix();
const $ = (id) => document.getElementById(id);

const ui = {
  app: $("app"),
  sidebar: $("sidebar"),
  sidebarBackdrop: $("sidebar-backdrop"),
  sidebarClose: $("sidebar-close"),
  btnSidebarOpen: $("btn-sidebar-open"),
  syncSubtitle: $("sync-subtitle"),
  btnNewEntry: $("btn-new-entry"),
  btnOpenTemplates: $("btn-open-templates"),
  btnAddJournal: $("btn-add-journal"),
  sidebarJournals: $("sidebar-journals"),
  sidebarTags: $("sidebar-tags"),
  tagsTotal: $("tags-total"),
  countAll: $("count-all"),
  countOtd: $("count-otd"),
  countFav: $("count-fav"),
  countStreak: $("count-streak"),
  btnExportOpen: $("btn-export-open"),
  btnPrefsOpen: $("btn-prefs-open"),

  journalBanner: $("journal-banner"),
  bannerTitle: $("banner-title"),
  bannerSubtitle: $("banner-subtitle"),
  bannerStreakNum: $("banner-streak-num"),
  btnSyncNow: $("btn-sync-now"),
  btnSearchToggle: $("btn-search-toggle"),
  btnFilterOpen: $("btn-filter-open"),
  filterDot: $("filter-dot"),
  btnSortToggle: $("btn-sort-toggle"),
  btnJournalEditCurrent: $("btn-journal-edit-current"),

  viewTabs: $("view-tabs"),
  searchBar: $("search-bar"),
  searchInput: $("search-input"),
  searchClear: $("search-clear"),
  activeFilters: $("active-filters"),
  reminderBanner: $("reminder-banner"),
  reminderText: $("reminder-text"),
  reminderWriteBtn: $("reminder-write-btn"),
  otdTeaser: $("otd-teaser"),

  listView: $("list-view"),
  timelineEntries: $("timeline-entries"),
  calendarView: $("calendar-view"),
  mediaView: $("media-view"),
  mapView: $("map-view"),
  memoriesView: $("memories-view"),
  insightsView: $("insights-view"),

  editorPane: $("editor-pane"),
  editorScroll: $("editor-scroll"),
  btnMobileBack: $("btn-mobile-back"),
  btnEntryJournal: $("btn-entry-journal"),
  entryJournalDot: $("entry-journal-dot"),
  entryJournalName: $("entry-journal-name"),
  btnEntryDatetime: $("btn-entry-datetime"),
  entryDateDisplay: $("entry-date-display"),
  entryTimeDisplay: $("entry-time-display"),
  mobileEntryDatebar: $("mobile-entry-datebar"),
  mobileDateText: $("mobile-date-text"),
  btnEntryFav: $("btn-entry-fav"),
  btnEntryPin: $("btn-entry-pin"),
  btnPreviewToggle: $("btn-preview-toggle"),
  btnFocusToggle: $("btn-focus-toggle"),
  btnEntryInfo: $("btn-entry-info"),
  btnEntryMore: $("btn-entry-more"),
  entryMoreMenu: $("entry-more-menu"),
  menuDeleteEntry: $("menu-delete-entry"),
  btnSaveEntry: $("btn-save-entry"),

  conflictBanner: $("conflict-banner"),
  btnDownloadDraft: $("btn-download-draft"),
  btnReloadLatest: $("btn-reload-latest"),

  chipLocation: $("chip-location"),
  chipLocationText: $("chip-location-text"),
  chipWeather: $("chip-weather"),
  chipWeatherIcon: $("chip-weather-icon"),
  chipWeatherText: $("chip-weather-text"),
  chipMood: $("chip-mood"),
  chipMoodText: $("chip-mood-text"),
  chipActivity: $("chip-activity"),
  chipActivityText: $("chip-activity-text"),

  entryTitle: $("entry-title"),
  entryMedia: $("entry-media"),
  entryBody: $("entry-body"),
  entryPreview: $("entry-preview"),
  entryTagsList: $("entry-tags-list"),
  entryTagInput: $("entry-tag-input"),
  editorToolbar: $("editor-toolbar"),
  btnAttachMedia: $("btn-attach-media"),
  btnRecordAudio: $("btn-record-audio"),
  btnOpenSketch: $("btn-open-sketch"),
  btnInsertTemplate: $("btn-insert-template"),
  entryWordCount: $("entry-word-count"),
  entrySaveState: $("entry-save-state"),
  mediaFileInput: $("media-file-input"),

  mobileFabWrap: $("mobile-fab-wrap"),
  fabSpeedDial: $("fab-speed-dial"),
  btnFabMore: $("btn-fab-more"),
  btnFabNew: $("btn-fab-new"),
  mobileTabbar: $("mobile-tabbar"),

  modalRoot: $("modal-root"),
  lightboxRoot: $("lightbox-root"),
  toastRoot: $("toast-root"),
  printBook: $("print-book"),
};

const nowInit = new Date();
const state = {
  entries: [],
  journals: [],
  templates: [],
  preferences: { temp_unit: "F", font_family: "sans", font_size: "md", theme: "system", daily_word_goal: 250 },
  insights: {},
  sync: { catalog_hash: "", total: 0, encrypted: false },
  permissions: { role: "owner", can_edit: true, can_delete: true },

  scope: "all", // 'all' | 'favorites' | 'memories' | 'insights' | 'journal:<name>'
  view: "list", // 'list' | 'calendar' | 'media' | 'map' | 'memories' | 'insights'
  sortOrder: "desc",
  search: "",
  mediaKindFilter: "all",
  filters: {
    favorite: false, checklist: false, tagged: false, place: false,
    audio: false, photo: false, video: false, sketch: false, tag: "", mood: "",
  },

  calYear: nowInit.getFullYear(),
  calMonth: nowInit.getMonth(),
  calSelectedDate: todayIsoLocal(),

  current: null,
  dirty: false,
  saving: false,
  uploading: false,
  conflict: false,
  previewMode: false,
  focusMode: false,
  generation: 0,
  autosaveTimer: null,
  syncPollTimer: null,
  mapCleanup: null,
};

/* ===========================
   API & HELPERS
   =========================== */
async function api(path, options = {}) {
  const response = await fetch(`${base}${path}`, { cache: "no-store", ...options });
  if (response.status === 409) {
    const err = new Error("This entry changed on another device. Download your draft or load the latest version.");
    err.status = 409;
    throw err;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.success === false) {
    const err = new Error(data.error || data.message || `Request failed (${response.status})`);
    err.status = response.status;
    throw err;
  }
  return data;
}

function toast(message, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast-item kind-${kind}`;
  el.textContent = message;
  ui.toastRoot.appendChild(el);
  setTimeout(() => el.classList.add("is-visible"), 10);
  setTimeout(() => {
    el.classList.remove("is-visible");
    setTimeout(() => el.remove(), 260);
  }, 3200);
}

function formatOffsetIso(dateObj) {
  const mins = -dateObj.getTimezoneOffset();
  const sign = mins >= 0 ? "+" : "-";
  const abs = Math.abs(mins);
  const hh = String(Math.floor(abs / 60)).padStart(2, "0");
  const mm = String(abs % 60).padStart(2, "0");
  return `${sign}${hh}:${mm}`;
}

function buildTimeLabel(dateObj, tz) {
  const hh = String(dateObj.getHours()).padStart(2, "0");
  const mm = String(dateObj.getMinutes()).padStart(2, "0");
  const ss = String(dateObj.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss} ${formatOffsetIso(dateObj)} (${tz})`;
}

function makeNewEntry({ date, journal, title = "", body = "", tags = [], template = "", location = null } = {}) {
  const now = new Date();
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const chosenDate = date || todayIsoLocal();
  let stamp = now;
  if (date && date !== todayIsoLocal()) {
    const [y, m, d] = date.split("-").map(Number);
    stamp = new Date(y, (m || 1) - 1, d || 1, now.getHours(), now.getMinutes(), now.getSeconds());
  }
  const activeJournal = journal !== undefined
    ? journal
    : state.scope.startsWith("journal:")
      ? state.scope.slice("journal:".length)
      : state.preferences.default_journal || "";
  const normalizedJournal = activeJournal.toLowerCase() === "daily journal" ? "" : activeJournal;

  return {
    id: crypto.randomUUID(),
    revision: "",
    date: chosenDate,
    time: buildTimeLabel(stamp, tz),
    captured_at: stamp.toISOString(),
    time_zone: tz,
    title,
    body,
    journal: normalizedJournal,
    tags: [...tags],
    favorite: false,
    pinned: false,
    location,
    weather: null,
    mood: "",
    activity: "",
    steps: null,
    template,
    media: [],
  };
}

function journalsMap() {
  const map = new Map();
  for (const j of state.journals) {
    map.set(j.name.toLowerCase(), j);
  }
  return map;
}

function activeJournalMeta() {
  if (state.scope.startsWith("journal:")) {
    const name = state.scope.slice("journal:".length);
    return journalsMap().get(name.toLowerCase()) || { name, color: "#44B6FF", count: 0 };
  }
  return null;
}

function applyPreferencesToDocument(prefs) {
  const theme = prefs?.theme || "system";
  if (theme === "light" || theme === "dark") {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("autoyou.diary.theme", theme); } catch (_) {}
  } else {
    delete document.documentElement.dataset.theme;
    try { localStorage.removeItem("autoyou.diary.theme"); } catch (_) {}
  }
  document.documentElement.dataset.font = prefs?.font_family || "sans";
  document.documentElement.dataset.size = prefs?.font_size || "md";
  try {
    localStorage.setItem("autoyou.diary.font_family", prefs?.font_family || "sans");
    localStorage.setItem("autoyou.diary.font_size", prefs?.font_size || "md");
  } catch (_) {}
}

/* ===========================
   MODAL ENGINE
   =========================== */
function openModal({ title, body, wide = false, onClose } = {}) {
  ui.modalRoot.hidden = false;
  ui.modalRoot.setAttribute("aria-hidden", "false");
  ui.modalRoot.innerHTML = "";

  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";

  const dialog = document.createElement("section");
  dialog.className = `modal-sheet${wide ? " is-wide" : ""}`;
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", title || "Dialog");

  const grabber = document.createElement("div");
  grabber.className = "sheet-grabber";
  const head = document.createElement("header");
  head.className = "modal-head";
  const h = document.createElement("h3");
  h.textContent = title || "";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "icon-btn";
  closeBtn.setAttribute("aria-label", "Close");
  closeBtn.textContent = "×";
  head.append(h, closeBtn);

  const slot = document.createElement("div");
  slot.className = "modal-body";
  slot.appendChild(body);

  dialog.append(grabber, head, slot);
  ui.modalRoot.append(backdrop, dialog);

  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    onClose?.();
    ui.modalRoot.hidden = true;
    ui.modalRoot.setAttribute("aria-hidden", "true");
    ui.modalRoot.innerHTML = "";
    window.removeEventListener("keydown", onEsc);
  }
  function onEsc(e) { if (e.key === "Escape") close(); }
  backdrop.onclick = close;
  closeBtn.onclick = close;
  window.addEventListener("keydown", onEsc);
  return close;
}

/* ===========================
   FILTERING & DATA LOADING
   =========================== */
function getFilteredEntries() {
  const q = state.search.trim().toLocaleLowerCase();
  const f = state.filters;
  const today = todayIsoLocal();
  const todayMD = today.slice(5);

  let rows = state.entries.filter((row) => {
    // Scope filter
    if (state.scope === "favorites" && !row.favorite) return false;
    if (state.scope === "memories") {
      if (row.date === today || row.date.slice(5) !== todayMD) return false;
    }
    if (state.scope.startsWith("journal:")) {
      const target = state.scope.slice("journal:".length).toLowerCase();
      const entryJ = (row.journal || "").trim().toLowerCase();
      if (target === "daily journal") {
        if (entryJ !== "" && entryJ !== "daily journal") return false;
      } else if (entryJ !== target) {
        return false;
      }
    }

    // Day One Filter Sheet toggles
    if (f.favorite && !row.favorite) return false;
    if (f.checklist && !row.has_checklist) return false;
    if (f.tagged && !(row.tags && row.tags.length)) return false;
    if (f.place && !(row.location && (row.location.name || typeof row.location.lat === "number"))) return false;
    if (f.audio && !(row.media_counts?.audio > 0)) return false;
    if (f.photo && !(row.media_counts?.photo > 0)) return false;
    if (f.video && !(row.media_counts?.video > 0)) return false;
    if (f.sketch && !(row.media_counts?.sketch > 0)) return false;
    if (f.tag && !(row.tags || []).some((t) => t.toLowerCase() === f.tag.toLowerCase())) return false;
    if (f.mood && (row.mood || "").toLowerCase() !== f.mood.toLowerCase()) return false;

    // Text search across all Day One fields
    if (q) {
      const haystack = [
        row.date,
        row.title,
        row.preview,
        row.journal,
        row.mood,
        row.activity,
        row.location?.name,
        row.location?.city,
        row.weather?.condition,
        ...(row.tags || []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  if (state.sortOrder === "asc") {
    rows = [...rows].reverse();
  }
  return rows;
}

function hasActiveFilters() {
  const f = state.filters;
  return Boolean(
    f.favorite || f.checklist || f.tagged || f.place ||
    f.audio || f.photo || f.video || f.sketch || f.tag || f.mood
  );
}

function buildFallbackWorkspace(entries) {
  const counts = new Map();
  const daily = {};
  const tagCounts = {};
  let totalWords = 0;
  for (const row of entries) {
    const j = (row.journal || "").trim() || "Daily Journal";
    counts.set(j, (counts.get(j) || 0) + 1);
    if (row.date) daily[row.date] = (daily[row.date] || 0) + 1;
    for (const t of row.tags || []) tagCounts[t] = (tagCounts[t] || 0) + 1;
    totalWords += Number(row.word_count || 0);
  }
  if (!counts.has("Daily Journal")) counts.set("Daily Journal", 0);
  const journals = [...counts.entries()].map(([name, count], idx) => ({
    name,
    color: name === "Daily Journal" ? "#44B6FF" : ["#2FB67C", "#F59E0B", "#8B5CF6", "#EC4899", "#0EA5E9"][idx % 5],
    icon: "book",
    description: "",
    streak_included: true,
    count,
    is_default: name === "Daily Journal",
  }));
  return {
    journals,
    templates: [
      { id: "five-minute-am", name: "5 Minute AM", category: "Five Minute Journal", color: "#F59E0B", builtin: true, title: "Morning Intentions", tags: ["morning", "gratitude"], body: "### I am grateful for...\n1. \n2. \n3. \n\n### What would make today great?\n- [ ] \n- [ ] \n" },
      { id: "five-minute-pm", name: "5 Minute PM", category: "Five Minute Journal", color: "#1E3A5F", builtin: true, title: "Evening Reflection", tags: ["evening"], body: "### Highlights of today\n1. \n2. \n3. \n\n### What did I learn?\n" },
      { id: "daily-reflection", name: "Daily Reflection", category: "Daily", color: "#44B6FF", builtin: true, title: "Daily Reflection", tags: ["daily"], body: "### How I feel\n\n### Key moments\n- \n" },
    ],
    preferences: state.preferences,
    insights: {
      today: todayIsoLocal(),
      journaled_today: Boolean(daily[todayIsoLocal()]),
      current_streak: daily[todayIsoLocal()] ? 1 : 0,
      longest_streak: daily[todayIsoLocal()] ? 1 : 0,
      active_days: Object.keys(daily).length,
      total_entries: entries.length,
      total_words: totalWords,
      total_media: entries.filter((e) => e.has_media).length,
      entries_this_month: entries.filter((e) => String(e.date || "").startsWith(todayIsoLocal().slice(0, 7))).length,
      daily_counts: daily,
      tag_counts: tagCounts,
      place_counts: {},
      mood_counts: {},
      on_this_day_count: 0,
      on_this_day: [],
    },
    sync: { catalog_hash: "", total: entries.length, encrypted: false },
    permissions: { role: "owner", can_edit: true, can_delete: true },
  };
}

async function loadAll({ silent = false } = {}) {
  try {
    if (!silent) ui.btnSyncNow.classList.add("is-spinning");
    const listRes = await api("/api/diary/entries?order=desc");
    state.entries = listRes.entries || [];
    let ws;
    try {
      ws = await api("/api/diary/workspace");
    } catch (err) {
      if (err.status === 404) {
        ws = buildFallbackWorkspace(state.entries);
      } else {
        throw err;
      }
    }
    state.journals = ws.journals || [];
    state.templates = ws.templates || [];
    state.preferences = { ...state.preferences, ...(ws.preferences || {}) };
    state.insights = ws.insights || {};
    state.sync = ws.sync || state.sync;
    state.permissions = ws.permissions || state.permissions;

    applyPreferencesToDocument(state.preferences);
    renderSidebar();
    renderBrowseHeader();
    renderActiveView();
    syncEditorReadOnlyState();

    // Update sync subtitle
    ui.syncSubtitle.textContent = state.sync.encrypted ? "Encrypted · Synced" : "Synced · iOS & Android";
  } catch (err) {
    if (!silent) toast(err.message || "Could not load journal.", "error");
    ui.entrySaveState.textContent = err.message || "Offline";
  } finally {
    ui.btnSyncNow.classList.remove("is-spinning");
  }
}

async function pollSyncState() {
  if (document.visibilityState !== "visible") return;
  try {
    const remote = await api("/api/diary/sync/state");
    if (state.sync.catalog_hash && remote.catalog_hash !== state.sync.catalog_hash) {
      await loadAll({ silent: true });
      // Refresh current open entry if unmodified so mobile edits appear seamlessly!
      if (state.current?.revision && !state.dirty) {
        try {
          const { entry } = await api(`/api/diary/entries/${encodeURIComponent(state.current.id)}`);
          if (entry && entry.revision !== state.current.revision) {
            bindEntryToEditor(entry, { switchPane: false });
          }
        } catch (err) {
          if (err.status === 404) {
            bindEntryToEditor(makeNewEntry(), { switchPane: false });
          }
        }
      }
      toast("Synced updates from another device", "info");
    }
    state.sync = remote;
  } catch (_) {}
}

/* ===========================
   RENDER SIDEBAR & HEADER
   =========================== */
function renderSidebar() {
  const ins = state.insights || {};
  ui.countAll.textContent = String(state.entries.length);
  ui.countOtd.textContent = String(ins.on_this_day_count || 0);
  ui.countOtd.hidden = !(ins.on_this_day_count > 0);
  ui.countFav.textContent = String(state.entries.filter((e) => e.favorite).length);
  ui.countStreak.textContent = `${ins.current_streak || 0}d`;

  ui.sidebar.querySelectorAll(".nav-item[data-scope]").forEach((btn) => {
    btn.setAttribute("aria-current", String(btn.dataset.scope === state.scope));
  });

  // Journals list
  ui.sidebarJournals.innerHTML = "";
  for (const j of state.journals) {
    const scopeKey = `journal:${j.name}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "nav-item journal-row";
    btn.setAttribute("aria-current", String(state.scope === scopeKey));
    btn.innerHTML = `
      <span class="journal-book-icon" style="background:${escapeHtml(j.color)}"></span>
      <span class="nav-label"></span>
      <span class="nav-count">${j.count}</span>
    `;
    btn.querySelector(".nav-label").textContent = j.name;
    btn.onclick = () => {
      state.scope = scopeKey;
      if (state.view === "memories" || state.view === "insights") state.view = "list";
      closeMobileSidebar();
      renderSidebar();
      renderBrowseHeader();
      renderActiveView();
    };
    ui.sidebarJournals.appendChild(btn);
  }

  // Tags cloud
  const tagEntries = Object.entries(ins.tag_counts || {});
  ui.tagsTotal.textContent = tagEntries.length ? String(tagEntries.length) : "";
  ui.sidebarTags.innerHTML = "";
  if (!tagEntries.length) {
    ui.sidebarTags.innerHTML = `<span class="muted-small">Add #tags to organize entries</span>`;
  } else {
    for (const [tag, count] of tagEntries.slice(0, 24)) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `sidebar-tag${state.filters.tag.toLowerCase() === tag.toLowerCase() ? " is-active" : ""}`;
      btn.innerHTML = `<span>#${escapeHtml(tag)}</span><small>${count}</small>`;
      btn.onclick = () => {
        state.filters.tag = state.filters.tag.toLowerCase() === tag.toLowerCase() ? "" : tag;
        if (state.view === "insights") state.view = "list";
        closeMobileSidebar();
        renderSidebar();
        renderBrowseHeader();
        renderActiveView();
      };
      ui.sidebarTags.appendChild(btn);
    }
  }
}

function renderBrowseHeader() {
  const jMeta = activeJournalMeta();
  const accent = jMeta?.color || "#44B6FF";
  document.documentElement.style.setProperty("--journal-accent", accent);

  const filtered = getFilteredEntries();
  if (jMeta) {
    ui.bannerTitle.textContent = jMeta.name;
    ui.bannerSubtitle.textContent = `${filtered.length} ${filtered.length === 1 ? "entry" : "entries"}${jMeta.description ? " · " + jMeta.description : ""}`;
    ui.btnJournalEditCurrent.hidden = !state.permissions.can_edit;
  } else if (state.scope === "favorites") {
    ui.bannerTitle.textContent = "Favorites";
    ui.bannerSubtitle.textContent = `${filtered.length} favorite ${filtered.length === 1 ? "entry" : "entries"}`;
    ui.btnJournalEditCurrent.hidden = true;
  } else if (state.scope === "memories") {
    ui.bannerTitle.textContent = "On This Day";
    ui.bannerSubtitle.textContent = "Memories from past years";
    ui.btnJournalEditCurrent.hidden = true;
  } else if (state.scope === "insights") {
    ui.bannerTitle.textContent = "Streaks & Insights";
    ui.bannerSubtitle.textContent = `${state.insights.active_days || 0} active days · ${(state.insights.total_words || 0).toLocaleString()} words`;
    ui.btnJournalEditCurrent.hidden = true;
  } else {
    ui.bannerTitle.textContent = "All Entries";
    ui.bannerSubtitle.textContent = `${filtered.length} ${filtered.length === 1 ? "entry" : "entries"} · ${state.journals.length} ${state.journals.length === 1 ? "journal" : "journals"}`;
    ui.btnJournalEditCurrent.hidden = true;
  }

  ui.bannerStreakNum.textContent = String(state.insights.current_streak || 0);
  ui.filterDot.hidden = !hasActiveFilters();
  ui.btnSortToggle.classList.toggle("is-active", state.sortOrder === "asc");
  ui.btnSortToggle.title = state.sortOrder === "desc" ? "Sorted: Newest first (tap to flip)" : "Sorted: Oldest first (tap to flip)";

  // Daily reminder banner
  const showReminder = Boolean(state.preferences.reminder_enabled && !state.insights.journaled_today && state.view === "list");
  ui.reminderBanner.hidden = !showReminder;
  if (showReminder) {
    ui.reminderText.textContent = `Reminder set for ${state.preferences.reminder_time || "20:00"} · Keep your ${state.insights.current_streak || 0}-day streak going.`;
  }

  // Active filter chips bar
  renderActiveFilterChips();

  // Sync view tabs + mobile tabbar
  ui.app.dataset.view = state.view;
  ui.viewTabs.querySelectorAll(".view-tab").forEach((t) => {
    t.setAttribute("aria-selected", String(t.dataset.view === state.view));
  });
  ui.mobileTabbar.querySelectorAll(".tab-item").forEach((t) => {
    const active = t.dataset.tab === state.view || (t.dataset.tab === "list" && state.view === "memories");
    if (active) t.setAttribute("aria-current", "page");
    else t.removeAttribute("aria-current");
  });
}

function renderActiveFilterChips() {
  const chips = [];
  const f = state.filters;
  const add = (label, onRemove) => chips.push({ label, onRemove });
  if (f.favorite) add("❤️ Favorite", () => { f.favorite = false; });
  if (f.checklist) add("☑️ Checklist", () => { f.checklist = false; });
  if (f.tagged) add("🏷️ Tagged", () => { f.tagged = false; });
  if (f.place) add("📍 Place", () => { f.place = false; });
  if (f.audio) add("🔊 Audio", () => { f.audio = false; });
  if (f.photo) add("🖼️ Photo", () => { f.photo = false; });
  if (f.video) add("🎬 Video", () => { f.video = false; });
  if (f.sketch) add("✏️ Sketch", () => { f.sketch = false; });
  if (f.tag) add(`#${f.tag}`, () => { f.tag = ""; });
  if (f.mood) add(`Mood: ${f.mood}`, () => { f.mood = ""; });

  ui.activeFilters.hidden = chips.length === 0;
  ui.activeFilters.innerHTML = "";
  for (const ch of chips) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "active-filter-chip";
    btn.innerHTML = `<span>${escapeHtml(ch.label)}</span><b>×</b>`;
    btn.onclick = () => {
      ch.onRemove();
      renderSidebar();
      renderBrowseHeader();
      renderActiveView();
    };
    ui.activeFilters.appendChild(btn);
  }
}

/* ===========================
   RENDER ACTIVE BROWSE VIEW
   =========================== */
function renderActiveView() {
  if (state.mapCleanup) {
    state.mapCleanup();
    state.mapCleanup = null;
  }

  const panels = {
    list: ui.listView,
    calendar: ui.calendarView,
    media: ui.mediaView,
    map: ui.mapView,
    memories: ui.memoriesView,
    insights: ui.insightsView,
  };
  for (const [k, el] of Object.entries(panels)) {
    el.hidden = k !== state.view;
  }

  const filtered = getFilteredEntries();
  const jMap = journalsMap();
  const accent = activeJournalMeta()?.color || "#44B6FF";

  if (state.view === "list") {
    // On This Day teaser banner inside All Entries / Journal timeline
    const otd = state.insights.on_this_day || [];
    if (otd.length && !state.search && !hasActiveFilters() && state.scope !== "memories") {
      ui.otdTeaser.hidden = false;
      const first = otd[0];
      ui.otdTeaser.innerHTML = `
        <div class="otd-teaser-copy">
          <span class="otd-pill">On This Day · ${first.years_ago} ${first.years_ago === 1 ? "yr" : "yrs"} ago</span>
          <strong>${escapeHtml(first.title)}</strong>
        </div>
        <button type="button" class="btn-chip-primary" id="otd-teaser-btn">View ${otd.length}</button>
      `;
      ui.otdTeaser.querySelector("#otd-teaser-btn").onclick = () => switchView("memories");
    } else {
      ui.otdTeaser.hidden = true;
    }

    renderTimelineList(ui.timelineEntries, filtered, {
      currentId: state.current?.id,
      journalsMap: jMap,
      tempUnit: state.preferences.temp_unit,
      basePrefix: base,
      onSelect: (id) => openEntryById(id),
      onNewEntry: () => startNewEntry(),
    });
  } else if (state.view === "calendar") {
    renderCalendarView(ui.calendarView, filtered, state, {
      basePrefix: base,
      onSelectEntry: (id) => openEntryById(id),
      onNewEntryForDate: (iso) => startNewEntry({ date: iso }),
      onChangeMonth: (y, m, sel) => {
        state.calYear = y;
        state.calMonth = m;
        state.calSelectedDate = sel;
        renderActiveView();
      },
    });
  } else if (state.view === "media") {
    renderMediaGalleryView(ui.mediaView, filtered, state.mediaKindFilter, {
      basePrefix: base,
      onFilterKind: (kind) => {
        state.mediaKindFilter = kind;
        renderActiveView();
      },
      onOpenLightbox: (items, idx) => openLightbox(ui.lightboxRoot, items, idx, { onJumpToEntry: (eid) => openEntryById(eid) }),
      onOpenEntry: (id) => openEntryById(id),
    });
  } else if (state.view === "map") {
    state.mapCleanup = renderDiaryMap(ui.mapView, filtered, {
      accentColor: accent,
      basePrefix: base,
      onOpenEntry: (id) => openEntryById(id),
      onAddLocatedEntry: () => {
        startNewEntry();
        setTimeout(() => openEntryInspector(), 60);
      },
    });
  } else if (state.view === "memories") {
    renderMemoriesView(ui.memoriesView, state.entries, {
      basePrefix: base,
      onSelectEntry: (id) => openEntryById(id),
      onReflectEntry: (past, yearsAgo) => {
        startNewEntry({
          title: `Reflecting on "${past.title}" (${yearsAgo} ${yearsAgo === 1 ? "year" : "years"} later)`,
          body: `> *On ${past.date}:* ${past.preview}\n\n`,
          tags: ["on-this-day", ...(past.tags || []).slice(0, 3)],
        });
      },
    });
  } else if (state.view === "insights") {
    renderInsightsView(ui.insightsView, state.insights, state.preferences, {
      onWriteToday: () => startNewEntry({ date: todayIsoLocal() }),
      onFilterTag: (tag) => {
        state.filters.tag = tag;
        switchView("list");
      },
      onFilterMood: (mood) => {
        state.filters.mood = mood;
        switchView("list");
      },
    });
  }
}

function switchView(nextView) {
  state.view = nextView;
  ui.app.dataset.mobilePane = "browse";
  if (nextView === "insights") state.scope = "insights";
  else if (nextView === "memories") state.scope = "memories";
  else if (state.scope === "insights" || state.scope === "memories") state.scope = "all";
  renderSidebar();
  renderBrowseHeader();
  renderActiveView();
}

/* ===========================
   EDITOR & ENTRY LIFECYCLE
   =========================== */
function syncEditorReadOnlyState() {
  const canEdit = Boolean(state.permissions.can_edit);
  ui.entryTitle.readOnly = !canEdit;
  ui.entryBody.readOnly = !canEdit;
  ui.entryTagInput.disabled = !canEdit;
  ui.btnSaveEntry.hidden = !canEdit;
  ui.btnAttachMedia.disabled = !canEdit;
  ui.btnRecordAudio.disabled = !canEdit;
  ui.btnOpenSketch.disabled = !canEdit;
  ui.btnInsertTemplate.disabled = !canEdit;
  ui.menuDeleteEntry.hidden = !state.permissions.can_delete || !state.current?.revision;
}

function renderEntryMetaBar() {
  const e = state.current;
  if (!e) return;
  const jName = e.journal || "Daily Journal";
  const jColor = journalsMap().get(jName.toLowerCase())?.color || "#44B6FF";
  ui.entryJournalDot.style.background = jColor;
  ui.entryJournalName.textContent = jName;

  const dt = parseIsoDate(e.date);
  const dateText = dt.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  const timeText = formatShortTime(e);
  ui.entryDateDisplay.textContent = dateText;
  ui.entryTimeDisplay.textContent = timeText;
  if (ui.mobileDateText) ui.mobileDateText.textContent = `${dateText} · ${timeText}`;

  ui.btnEntryFav.setAttribute("aria-pressed", String(Boolean(e.favorite)));
  ui.btnEntryFav.classList.toggle("is-favorited", Boolean(e.favorite));
  ui.btnEntryPin.setAttribute("aria-pressed", String(Boolean(e.pinned)));
  ui.btnEntryPin.classList.toggle("is-pinned", Boolean(e.pinned));

  // Quick metadata chips
  if (e.location?.name) {
    ui.chipLocation.classList.add("is-filled");
    ui.chipLocationText.textContent = e.location.name;
  } else {
    ui.chipLocation.classList.remove("is-filled");
    ui.chipLocationText.textContent = "Add location";
  }

  if (e.weather && (e.weather.condition || typeof e.weather.temp_c === "number")) {
    ui.chipWeather.classList.add("is-filled");
    ui.chipWeatherIcon.textContent = e.weather.icon || "🌤️";
    ui.chipWeatherText.textContent = [formatTemp(e.weather, state.preferences.temp_unit), e.weather.condition].filter(Boolean).join(" ");
  } else {
    ui.chipWeather.classList.remove("is-filled");
    ui.chipWeatherIcon.textContent = "☀️";
    ui.chipWeatherText.textContent = "Weather";
  }

  if (e.mood) {
    ui.chipMood.classList.add("is-filled");
    ui.chipMoodText.textContent = e.mood;
  } else {
    ui.chipMood.classList.remove("is-filled");
    ui.chipMoodText.textContent = "Mood";
  }

  if (e.activity) {
    ui.chipActivity.classList.add("is-filled");
    ui.chipActivityText.textContent = e.steps ? `${e.activity} · ${e.steps.toLocaleString()} steps` : e.activity;
  } else if (e.steps) {
    ui.chipActivity.classList.add("is-filled");
    ui.chipActivityText.textContent = `${e.steps.toLocaleString()} steps`;
  } else {
    ui.chipActivity.classList.remove("is-filled");
    ui.chipActivityText.textContent = "Activity";
  }

  // Tags chips
  ui.entryTagsList.innerHTML = "";
  for (const tag of e.tags || []) {
    const chip = document.createElement("span");
    chip.className = "entry-tag-chip";
    chip.innerHTML = `<span>#${escapeHtml(tag)}</span>`;
    if (state.permissions.can_edit) {
      const rm = document.createElement("button");
      rm.type = "button";
      rm.textContent = "×";
      rm.setAttribute("aria-label", `Remove tag ${tag}`);
      rm.onclick = () => {
        e.tags = (e.tags || []).filter((t) => t !== tag);
        renderEntryMetaBar();
        markDirty();
      };
      chip.appendChild(rm);
    }
    ui.entryTagsList.appendChild(chip);
  }
}

function updateWordCountAndPreview() {
  const title = ui.entryTitle.value.trim();
  const body = ui.entryBody.value;
  const combined = `${title} ${body}`.trim();
  const words = combined ? combined.split(/\s+/).length : 0;
  const mins = Math.max(1, Math.ceil(words / 200));
  ui.entryWordCount.textContent = words ? `${words.toLocaleString()} ${words === 1 ? "word" : "words"} · ${mins} min read` : "0 words";

  if (state.previewMode) {
    ui.entryPreview.innerHTML = body.trim()
      ? renderMarkdown(body, base)
      : `<p class="muted-note">Nothing written yet. Switch to Edit mode to write.</p>`;
  }
}

function renderEntryAttachments() {
  const e = state.current;
  if (!e) return;
  renderMedia(ui.entryMedia, e, {
    basePrefix: base,
    editable: state.permissions.can_edit,
    onOpenLightbox: (visuals, idx) => openLightbox(ui.lightboxRoot, visuals, idx),
    onInsertTranscript: (text) => {
      if (!text) return;
      const cur = ui.entryBody.value;
      ui.entryBody.value = cur ? `${cur.trimEnd()}\n\n> ${text}\n` : text;
      markDirty();
      updateWordCountAndPreview();
      toast("Transcript inserted into entry", "success");
    },
    onUpdateTranscript: (mediaId, text) => {
      e.media = (e.media || []).map((m) => (m.id === mediaId ? { ...m, transcription: text } : m));
      renderEntryAttachments();
      markDirty();
    },
    onRemove: async (item) => {
      if (!confirm(`Remove "${item.name}" from this entry?`)) return;
      e.media = (e.media || []).filter((m) => m.id !== item.id);
      renderEntryAttachments();
      markDirty();
      await saveCurrentEntry({ silent: false });
      try {
        await fetch(`${base}/api/diary/entries/${encodeURIComponent(e.id)}/media/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      } catch (_) {}
    },
  });
}

function bindEntryToEditor(entry, { switchPane = true } = {}) {
  clearTimeout(state.autosaveTimer);
  state.current = {
    ...entry,
    tags: [...(entry.tags || [])],
    media: (entry.media || []).map((m) => ({ ...m })),
  };
  state.dirty = false;
  state.conflict = false;
  ui.conflictBanner.hidden = true;

  ui.entryTitle.value = entry.title === "Untitled" && !entry.revision ? "" : entry.title || "";
  ui.entryBody.value = entry.body || "";
  ui.entryTagInput.value = "";
  ui.entrySaveState.textContent = entry.revision ? "Saved" : "New entry";

  renderEntryMetaBar();
  renderEntryAttachments();
  updateWordCountAndPreview();
  syncEditorReadOnlyState();

  if (switchPane) {
    ui.app.dataset.mobilePane = "editor";
    // On desktop, ensure timeline active row updates
    if (state.view === "list") renderActiveView();
    ui.editorScroll.scrollTop = 0;
  }
}

async function openEntryById(entryId) {
  if (state.dirty && !confirm("Discard unsaved changes to the current entry?")) return;
  const ticket = ++state.generation;
  try {
    ui.entrySaveState.textContent = "Loading…";
    const { entry } = await api(`/api/diary/entries/${encodeURIComponent(entryId)}`);
    if (ticket !== state.generation) return;
    bindEntryToEditor(entry, { switchPane: true });
  } catch (err) {
    toast(err.message || "Could not open entry.", "error");
    ui.entrySaveState.textContent = err.message || "Error";
  }
}

function startNewEntry(opts = {}) {
  if (state.dirty && !confirm("Discard unsaved changes and start a new entry?")) return;
  ++state.generation;
  setPreviewMode(false);
  const entry = makeNewEntry(opts);
  bindEntryToEditor(entry, { switchPane: true });
  setTimeout(() => ui.entryTitle.focus(), 40);
}

function markDirty() {
  if (!state.permissions.can_edit) return;
  state.dirty = true;
  ui.entrySaveState.textContent = "Unsaved changes";
  ui.btnSaveEntry.classList.add("is-dirty");
  clearTimeout(state.autosaveTimer);
  if (!state.conflict && !state.uploading) {
    state.autosaveTimer = setTimeout(() => {
      if (state.dirty && !state.saving && !state.uploading && !state.conflict) {
        const hasSomething =
          ui.entryTitle.value.trim().length > 0 ||
          ui.entryBody.value.trim().length > 0 ||
          (state.current?.media?.length || 0) > 0;
        if (hasSomething) saveCurrentEntry({ silent: true });
      }
    }, 1800);
  }
}

function deriveTitle(rawTitle, body, media) {
  const clean = rawTitle.replace(/\r?\n/g, " ").trim();
  if (clean) return clean.slice(0, 200);
  const firstLine = String(body || "")
    .split("\n")
    .map((l) => l.replace(/^#+\s*/, "").trim())
    .find(Boolean);
  if (firstLine) return firstLine.slice(0, 80);
  if (media && media.length) return media[0].name.slice(0, 80);
  return "Diary entry";
}

function buildEntryDetails(e) {
  const payload = {
    captured_at: e.captured_at || new Date().toISOString(),
    time_zone: e.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    journal: (e.journal || "").trim(),
    tags: [...new Set((e.tags || []).map((t) => t.trim()).filter(Boolean))],
    favorite: Boolean(e.favorite),
    media: (e.media || []).map((m) => ({ id: m.id, name: m.name, transcription: m.transcription || "" })),
    location: e.location || null,
    weather: e.weather || null,
    mood: e.mood || "",
    activity: e.activity || "",
    steps: typeof e.steps === "number" ? e.steps : null,
    pinned: Boolean(e.pinned),
    template: e.template || "",
  };
  return payload;
}

async function saveCurrentEntry({ silent = false } = {}) {
  if (!state.current || !state.permissions.can_edit || state.saving) return false;
  clearTimeout(state.autosaveTimer);
  const e = state.current;
  const title = deriveTitle(ui.entryTitle.value, ui.entryBody.value, e.media);
  const body = ui.entryBody.value;
  if (!ui.entryTitle.value.trim()) ui.entryTitle.value = title;

  const details = buildEntryDetails(e);
  const markdown = [
    `<!-- autoyou-diary:${e.id} -->`,
    `### ${e.date}`,
    "",
    `#### ${e.time}`,
    "",
    `##### ${title}`,
    "",
    body,
    `<!-- /autoyou-diary:${e.id} -->`,
  ].join("\n");

  state.saving = true;
  ui.entrySaveState.textContent = "Saving…";
  const snapshotTitle = ui.entryTitle.value;
  const snapshotBody = ui.entryBody.value;
  const snapshotDetailsJson = JSON.stringify(details);

  try {
    const saved = await api("/api/diary/entries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: e.id,
        markdown,
        details,
        base_revision: e.revision || "",
      }),
    });

    if (state.current && state.current.id === e.id) {
      state.current.revision = saved.revision;
      state.current.title = title;
      state.current.body = body;
      const changedSinceSend =
        ui.entryTitle.value !== snapshotTitle ||
        ui.entryBody.value !== snapshotBody ||
        JSON.stringify(buildEntryDetails(state.current)) !== snapshotDetailsJson;
      state.dirty = changedSinceSend;
      state.conflict = false;
      ui.conflictBanner.hidden = true;
      ui.btnSaveEntry.classList.toggle("is-dirty", state.dirty);
      ui.entrySaveState.textContent = state.dirty ? "Pending edits…" : "Saved · Synced";
      syncEditorReadOnlyState();
    }

    await loadAll({ silent: true });
    if (!silent) toast("Entry saved", "success");
    return true;
  } catch (err) {
    if (err.status === 409) {
      state.conflict = true;
      ui.conflictBanner.hidden = false;
    }
    ui.entrySaveState.textContent = err.message || "Save failed";
    if (!silent) toast(err.message || "Could not save entry", "error");
    return false;
  } finally {
    state.saving = false;
  }
}

async function deleteCurrentEntry() {
  if (!state.current?.revision) return;
  if (!confirm("Delete this diary entry and all its attachments? This also syncs removal to paired devices.")) return;
  try {
    await api(`/api/diary/entries/${encodeURIComponent(state.current.id)}?base_revision=${encodeURIComponent(state.current.revision)}`, {
      method: "DELETE",
    });
    ++state.generation;
    bindEntryToEditor(makeNewEntry(), { switchPane: false });
    ui.app.dataset.mobilePane = "browse";
    await loadAll({ silent: true });
    toast("Entry deleted", "info");
  } catch (err) {
    toast(err.message || "Could not delete entry.", "error");
  }
}

async function attachFiles(fileList) {
  if (!state.current || !state.permissions.can_edit || !fileList?.length) return;
  state.uploading = true;
  ui.entrySaveState.textContent = `Uploading ${fileList.length} ${fileList.length === 1 ? "file" : "files"}…`;
  try {
    for (const file of Array.from(fileList)) {
      const uploaded = await uploadDiaryMedia(state.current.id, file, { basePrefix: base });
      state.current.media = [...(state.current.media || []), { id: uploaded.id, name: uploaded.name, transcription: "" }];
      renderEntryAttachments();
    }
    markDirty();
    await saveCurrentEntry({ silent: true });
    toast(`Attached ${fileList.length} ${fileList.length === 1 ? "file" : "files"}`, "success");
  } catch (err) {
    toast(err.message || "Upload failed.", "error");
    ui.entrySaveState.textContent = err.message || "Upload failed";
  } finally {
    state.uploading = false;
  }
}

function setPreviewMode(enabled) {
  state.previewMode = Boolean(enabled);
  ui.entryBody.hidden = state.previewMode;
  ui.entryPreview.hidden = !state.previewMode;
  ui.btnPreviewToggle.setAttribute("aria-pressed", String(state.previewMode));
  ui.btnPreviewToggle.classList.toggle("is-active", state.previewMode);
  updateWordCountAndPreview();
}

function setFocusMode(enabled) {
  state.focusMode = Boolean(enabled);
  ui.app.classList.toggle("is-focus-mode", state.focusMode);
  ui.btnFocusToggle.setAttribute("aria-pressed", String(state.focusMode));
  ui.btnFocusToggle.classList.toggle("is-active", state.focusMode);
}

function openEntryInspector() {
  if (!state.current) return;
  openEntryInfoModal(openModal, state.current, state.journals, state.preferences.temp_unit, {
    onUpdate: (updates) => {
      const e = state.current;
      e.date = updates.date;
      e.time_zone = updates.time_zone;
      const [y, mo, d] = updates.date.split("-").map(Number);
      const [hh, mm] = (updates.time_hm || "12:00").split(":").map(Number);
      const stamp = new Date(y, (mo || 1) - 1, d || 1, hh || 0, mm || 0, 0);
      e.captured_at = stamp.toISOString();
      e.time = buildTimeLabel(stamp, e.time_zone);
      e.journal = updates.journal;
      e.location = updates.location;
      e.weather = updates.weather;
      e.mood = updates.mood;
      e.activity = updates.activity;
      e.steps = updates.steps;
      renderEntryMetaBar();
      markDirty();
    },
  });
}

function openQuickPicker(title, options, currentVal, onSelect, { includeNone = true } = {}) {
  const body = document.createElement("div");
  body.className = "quick-picker-grid";
  const close = openModal({ title, body });
  if (includeNone) {
    const noneBtn = document.createElement("button");
    noneBtn.type = "button";
    noneBtn.className = `picker-pill${!currentVal ? " is-active" : ""}`;
    noneBtn.textContent = "None";
    noneBtn.onclick = () => { onSelect(""); close(); };
    body.appendChild(noneBtn);
  }

  for (const opt of options) {
    const val = typeof opt === "string" ? opt : opt.value;
    const label = typeof opt === "string" ? opt : opt.label;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `picker-pill${val.toLowerCase() === String(currentVal || "").toLowerCase() ? " is-active" : ""}`;
    btn.textContent = label;
    btn.onclick = () => { onSelect(val); close(); };
    body.appendChild(btn);
  }
}

/* ===========================
   PRINT BOOK ENGINE
   =========================== */
async function printJournalBook(journalFilter = "", singleEntry = null) {
  let entriesToPrint = [];
  if (singleEntry) {
    entriesToPrint = [singleEntry];
  } else {
    toast("Preparing book layout for printing…", "info");
    const list = state.entries.filter((e) => {
      if (!journalFilter) return true;
      const ej = (e.journal || "").toLowerCase();
      if (journalFilter.toLowerCase() === "daily journal") return ej === "" || ej === "daily journal";
      return ej === journalFilter.toLowerCase();
    });
    const full = await Promise.all(
      list.slice(0, 120).map((row) =>
        api(`/api/diary/entries/${encodeURIComponent(row.id)}`).then((r) => r.entry).catch(() => null)
      )
    );
    entriesToPrint = full.filter(Boolean);
  }

  ui.printBook.innerHTML = `
    <div class="print-cover">
      <h1>${escapeHtml(singleEntry ? singleEntry.title : journalFilter || "My Diary")}</h1>
      <p>${entriesToPrint.length} ${entriesToPrint.length === 1 ? "Entry" : "Entries"} · Exported ${escapeHtml(todayIsoLocal())}</p>
    </div>
    ${entriesToPrint
      .map((e) => {
        const meta = [e.date, formatShortTime(e), e.journal, e.location?.name, e.mood].filter(Boolean).join(" · ");
        const imgs = (e.media || [])
          .filter((m) => /\.(jpe?g|png|gif|webp|heic|heif|avif)$/i.test(m.name))
          .map((m) => `<img src="${escapeHtml(`${base}/api/diary/entries/${encodeURIComponent(e.id)}/media/${encodeURIComponent(m.id)}`)}" alt="${escapeHtml(m.name)}">`)
          .join("");
        return `
          <article class="print-entry">
            <header>
              <div class="print-meta">${escapeHtml(meta)}</div>
              <h2>${escapeHtml(e.title)}</h2>
            </header>
            ${imgs ? `<div class="print-photos">${imgs}</div>` : ""}
            <div class="print-body">${renderMarkdown(e.body, base)}</div>
            ${e.tags?.length ? `<div class="print-tags">${e.tags.map((t) => `#${escapeHtml(t)}`).join(" ")}</div>` : ""}
          </article>
        `;
      })
      .join("")}
  `;
  setTimeout(() => window.print(), 120);
}

/* ===========================
   MOBILE SIDEBAR HELPERS
   =========================== */
function openMobileSidebar() {
  ui.sidebar.classList.add("is-open");
  ui.sidebarBackdrop.hidden = false;
}
function closeMobileSidebar() {
  ui.sidebar.classList.remove("is-open");
  ui.sidebarBackdrop.hidden = true;
}

/* ===========================
   EVENT WIRING
   =========================== */
function wireEvents() {
  // Sidebar open/close
  ui.btnSidebarOpen.onclick = openMobileSidebar;
  ui.sidebarClose.onclick = closeMobileSidebar;
  ui.sidebarBackdrop.onclick = closeMobileSidebar;

  // Sidebar scopes
  ui.sidebar.querySelectorAll(".nav-item[data-scope]").forEach((btn) => {
    btn.onclick = () => {
      const sc = btn.dataset.scope;
      state.scope = sc;
      if (sc === "memories") state.view = "memories";
      else if (sc === "insights") state.view = "insights";
      else if (state.view === "memories" || state.view === "insights") state.view = "list";
      closeMobileSidebar();
      renderSidebar();
      renderBrowseHeader();
      renderActiveView();
    };
  });

  // New Entry & Templates
  ui.btnNewEntry.onclick = () => { closeMobileSidebar(); startNewEntry(); };
  const launchTemplates = () => {
    closeMobileSidebar();
    openTemplatesModal(openModal, state.templates, {
      canEdit: state.permissions.can_edit,
      onUseTemplate: (tpl) => {
        if (!state.dirty && state.current && !state.current.revision && !ui.entryBody.value.trim()) {
          state.current.title = tpl.title || tpl.name;
          state.current.body = tpl.body || "";
          state.current.tags = [...new Set([...(state.current.tags || []), ...(tpl.tags || [])])];
          state.current.template = tpl.name;
          bindEntryToEditor(state.current, { switchPane: true });
          markDirty();
        } else {
          startNewEntry({ title: tpl.title || tpl.name, body: tpl.body || "", tags: tpl.tags || [], template: tpl.name });
          markDirty();
        }
      },
      onCreateTemplate: async (payload) => {
        const res = await api("/api/diary/templates", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        state.templates = res.templates || state.templates;
        toast("Template saved", "success");
      },
      onDeleteTemplate: async (tid) => {
        const res = await api(`/api/diary/templates/${encodeURIComponent(tid)}`, { method: "DELETE" });
        state.templates = res.templates || state.templates;
        toast("Template deleted", "info");
      },
    });
  };
  ui.btnOpenTemplates.onclick = launchTemplates;
  ui.btnInsertTemplate.onclick = launchTemplates;

  // Journal Create / Edit
  const openJournalEditor = (existing = null) => {
    openJournalModal(openModal, existing, {
      canDelete: state.permissions.can_delete,
      onSave: async (payload) => {
        const res = await api("/api/diary/journals", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        state.journals = res.journals || state.journals;
        if (existing && payload.rename_from && state.scope === `journal:${existing.name}`) {
          state.scope = `journal:${payload.name}`;
        }
        await loadAll({ silent: true });
        toast(existing ? "Journal updated" : "Journal created", "success");
      },
      onDelete: async (name) => {
        await api(`/api/diary/journals/${encodeURIComponent(name)}`, { method: "DELETE" });
        if (state.scope === `journal:${name}`) state.scope = "all";
        await loadAll({ silent: true });
        toast("Journal deleted", "info");
      },
    });
  };
  ui.btnAddJournal.onclick = () => { closeMobileSidebar(); openJournalEditor(null); };
  ui.btnJournalEditCurrent.onclick = () => {
    const j = activeJournalMeta();
    if (j) openJournalEditor(j);
  };

  // Export & Settings
  ui.btnExportOpen.onclick = () => {
    closeMobileSidebar();
    openExportModal(openModal, state.journals, activeJournalMeta()?.name || "", {
      basePrefix: base,
      onPrintBook: (j) => printJournalBook(j),
    });
  };
  ui.btnPrefsOpen.onclick = () => {
    closeMobileSidebar();
    openPreferencesModal(openModal, state.preferences, state.sync, {
      canEdit: state.permissions.can_edit,
      onSave: async (updates) => {
        const res = await api("/api/diary/preferences", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(updates),
        });
        state.preferences = res.preferences || updates;
        applyPreferencesToDocument(state.preferences);
        renderBrowseHeader();
        renderActiveView();
        renderEntryMetaBar();
        toast("Settings saved", "success");
      },
    });
  };

  // Header controls
  ui.btnSyncNow.onclick = async () => {
    await loadAll({ silent: false });
    toast("Diary synced", "success");
  };
  ui.btnSearchToggle.onclick = () => {
    ui.searchBar.hidden = !ui.searchBar.hidden;
    if (!ui.searchBar.hidden) ui.searchInput.focus();
    else {
      state.search = "";
      ui.searchInput.value = "";
      ui.searchClear.hidden = true;
      renderBrowseHeader();
      renderActiveView();
    }
  };
  ui.searchInput.oninput = () => {
    state.search = ui.searchInput.value;
    ui.searchClear.hidden = !state.search;
    renderBrowseHeader();
    renderActiveView();
  };
  ui.searchClear.onclick = () => {
    state.search = "";
    ui.searchInput.value = "";
    ui.searchClear.hidden = true;
    renderBrowseHeader();
    renderActiveView();
    ui.searchInput.focus();
  };
  ui.btnFilterOpen.onclick = () => {
    openFilterModal(openModal, state.entries, state.filters, {
      onApply: (nextFilters) => {
        state.filters = nextFilters;
        renderSidebar();
        renderBrowseHeader();
        renderActiveView();
      },
    });
  };
  ui.btnSortToggle.onclick = () => {
    state.sortOrder = state.sortOrder === "desc" ? "asc" : "desc";
    renderBrowseHeader();
    renderActiveView();
  };
  ui.reminderWriteBtn.onclick = () => startNewEntry({ date: todayIsoLocal() });

  // Segmented View Tabs + Mobile Tabbar
  ui.viewTabs.addEventListener("click", (e) => {
    const tab = e.target.closest(".view-tab[data-view]");
    if (!tab) return;
    switchView(tab.dataset.view);
  });
  ui.mobileTabbar.addEventListener("click", (e) => {
    const tab = e.target.closest(".tab-item[data-tab]");
    if (!tab) return;
    ui.app.dataset.mobilePane = "browse";
    switchView(tab.dataset.tab);
  });

  // Mobile FAB & Speed Dial
  ui.btnFabNew.onclick = () => {
    ui.fabSpeedDial.hidden = true;
    startNewEntry();
  };
  ui.btnFabMore.onclick = () => {
    ui.fabSpeedDial.hidden = !ui.fabSpeedDial.hidden;
  };
  ui.fabSpeedDial.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-fab]");
    if (!btn) return;
    ui.fabSpeedDial.hidden = true;
    const act = btn.dataset.fab;
    if (act === "template") launchTemplates();
    else if (act === "audio") { startNewEntry(); setTimeout(() => ui.btnRecordAudio.click(), 60); }
    else if (act === "photo") { startNewEntry(); setTimeout(() => ui.mediaFileInput.click(), 60); }
    else if (act === "sketch") { startNewEntry(); setTimeout(() => ui.btnOpenSketch.click(), 60); }
  });

  // Editor Topbar
  ui.btnMobileBack.onclick = async () => {
    if (state.dirty && state.permissions.can_edit && !state.conflict) {
      const hasContent = ui.entryTitle.value.trim() || ui.entryBody.value.trim() || (state.current?.media?.length || 0) > 0;
      if (hasContent) await saveCurrentEntry({ silent: true });
    }
    ui.app.dataset.mobilePane = "browse";
    renderActiveView();
  };

  ui.btnEntryJournal.onclick = () => {
    if (!state.permissions.can_edit || !state.current) return;
    const opts = state.journals.map((j) => ({ value: j.is_default ? "" : j.name, label: j.name }));
    openQuickPicker("Move to Journal", opts, state.current.journal || "", (val) => {
      state.current.journal = val;
      renderEntryMetaBar();
      markDirty();
    }, { includeNone: false });
  };
  ui.btnEntryDatetime.onclick = openEntryInspector;
  if (ui.mobileEntryDatebar) ui.mobileEntryDatebar.onclick = openEntryInspector;
  ui.btnEntryInfo.onclick = openEntryInspector;
  ui.chipLocation.onclick = openEntryInspector;

  ui.chipWeather.onclick = () => {
    if (!state.permissions.can_edit || !state.current) return;
    openEntryInspector();
  };
  ui.chipMood.onclick = () => {
    if (!state.permissions.can_edit || !state.current) return;
    openQuickPicker("How are you feeling?", MOOD_OPTIONS, state.current.mood || "", (val) => {
      state.current.mood = val;
      renderEntryMetaBar();
      markDirty();
    });
  };
  ui.chipActivity.onclick = () => {
    if (!state.permissions.can_edit || !state.current) return;
    openQuickPicker("Activity", ACTIVITY_OPTIONS, state.current.activity || "", (val) => {
      state.current.activity = val;
      renderEntryMetaBar();
      markDirty();
    });
  };

  ui.btnEntryFav.onclick = () => {
    if (!state.permissions.can_edit || !state.current) return;
    state.current.favorite = !state.current.favorite;
    renderEntryMetaBar();
    markDirty();
  };
  ui.btnEntryPin.onclick = () => {
    if (!state.permissions.can_edit || !state.current) return;
    state.current.pinned = !state.current.pinned;
    renderEntryMetaBar();
    markDirty();
  };
  ui.btnPreviewToggle.onclick = () => setPreviewMode(!state.previewMode);
  ui.btnFocusToggle.onclick = () => setFocusMode(!state.focusMode);
  ui.btnSaveEntry.onclick = () => saveCurrentEntry({ silent: false });

  // Overflow menu
  ui.btnEntryMore.onclick = (e) => {
    e.stopPropagation();
    const open = ui.entryMoreMenu.hidden;
    ui.entryMoreMenu.hidden = !open;
    ui.btnEntryMore.setAttribute("aria-expanded", String(open));
  };
  document.addEventListener("click", () => {
    if (!ui.entryMoreMenu.hidden) {
      ui.entryMoreMenu.hidden = true;
      ui.btnEntryMore.setAttribute("aria-expanded", "false");
    }
  });
  ui.entryMoreMenu.addEventListener("click", async (e) => {
    const item = e.target.closest("[data-action]");
    if (!item || !state.current) return;
    const act = item.dataset.action;
    ui.entryMoreMenu.hidden = true;
    if (act === "info") openEntryInspector();
    else if (act === "pin") {
      if (state.permissions.can_edit && state.current) {
        state.current.pinned = !state.current.pinned;
        renderEntryMetaBar();
        markDirty();
      }
    } else if (act === "template") launchTemplates();
    else if (act === "save-template") {
      const name = prompt("Save current entry structure as template name:", ui.entryTitle.value.trim() || "My Template");
      if (!name || !name.trim()) return;
      const res = await api("/api/diary/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          title: ui.entryTitle.value.trim() || name.trim(),
          body: ui.entryBody.value,
          tags: state.current.tags || [],
          category: "Custom",
          color: activeJournalMeta()?.color || "#44B6FF",
        }),
      });
      state.templates = res.templates || state.templates;
      toast(`Saved template "${name.trim()}"`, "success");
    } else if (act === "duplicate") {
      const copy = makeNewEntry({
        title: `${ui.entryTitle.value.trim() || "Entry"} (Copy)`,
        body: ui.entryBody.value,
        journal: state.current.journal,
        tags: state.current.tags || [],
        location: state.current.location,
      });
      bindEntryToEditor(copy, { switchPane: true });
      markDirty();
      toast("Duplicated entry draft", "info");
    } else if (act === "print") {
      printJournalBook("", { ...state.current, title: ui.entryTitle.value || state.current.title, body: ui.entryBody.value });
    } else if (act === "download-json") {
      downloadDraftJson();
    } else if (act === "delete") {
      await deleteCurrentEntry();
    }
  });

  // Conflict banner actions
  const downloadDraftJson = () => {
    if (!state.current) return;
    const draft = JSON.stringify(
      {
        id: state.current.id,
        date: state.current.date,
        time: state.current.time,
        title: ui.entryTitle.value,
        body: ui.entryBody.value,
        ...buildEntryDetails(state.current),
      },
      null,
      2
    );
    const url = URL.createObjectURL(new Blob([draft], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `Diary-${state.current.date}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  ui.btnDownloadDraft.onclick = downloadDraftJson;
  ui.btnReloadLatest.onclick = async () => {
    if (!state.current) return;
    state.dirty = false;
    await openEntryById(state.current.id);
    toast("Loaded latest synced version", "info");
  };

  // Title & Body editing
  ui.entryTitle.oninput = () => { markDirty(); updateWordCountAndPreview(); };
  ui.entryBody.oninput = () => { markDirty(); updateWordCountAndPreview(); };

  // Interactive checklist toggle in Preview mode
  ui.entryPreview.addEventListener("change", (e) => {
    const cb = e.target.closest('input[type="checkbox"][data-line]');
    if (!cb || !state.permissions.can_edit) return;
    const lineIdx = Number(cb.dataset.line);
    ui.entryBody.value = toggleChecklistLine(ui.entryBody.value, lineIdx);
    markDirty();
    updateWordCountAndPreview();
  });

  // Inline Tags Input
  const commitTagInput = () => {
    if (!state.current || !state.permissions.can_edit) return;
    const raw = ui.entryTagInput.value;
    if (!raw.trim()) return;
    const added = raw
      .split(",")
      .map((s) => s.trim().replace(/^#+/, "").trim())
      .filter(Boolean);
    const existingLower = new Set((state.current.tags || []).map((t) => t.toLowerCase()));
    let changed = false;
    for (const tag of added) {
      if (!existingLower.has(tag.toLowerCase())) {
        state.current.tags.push(tag);
        existingLower.add(tag.toLowerCase());
        changed = true;
      }
    }
    ui.entryTagInput.value = "";
    if (changed) {
      renderEntryMetaBar();
      markDirty();
    }
  };
  ui.entryTagInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commitTagInput();
    } else if (e.key === "Backspace" && !ui.entryTagInput.value && state.current?.tags?.length) {
      state.current.tags.pop();
      renderEntryMetaBar();
      markDirty();
    }
  });
  ui.entryTagInput.addEventListener("blur", commitTagInput);

  // Markdown formatting toolbar
  ui.editorToolbar.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-md]");
    if (!btn || !state.permissions.can_edit) return;
    if (state.previewMode) setPreviewMode(false);
    applyMarkdownCommand(ui.entryBody, btn.dataset.md);
  });

  // Media attach, Voice Recorder, Sketch Pad
  ui.btnAttachMedia.onclick = () => ui.mediaFileInput.click();
  ui.mediaFileInput.onchange = async () => {
    const files = ui.mediaFileInput.files;
    if (files?.length) await attachFiles(files);
    ui.mediaFileInput.value = "";
  };
  ui.btnRecordAudio.onclick = () => {
    if (!state.current || !state.permissions.can_edit) return;
    openVoiceRecorderModal(openModal, {
      onAttach: async (blob, name, transcript) => {
        const uploaded = await uploadDiaryMedia(state.current.id, blob, { filename: name, transcription: transcript, basePrefix: base });
        state.current.media = [...(state.current.media || []), { id: uploaded.id, name: uploaded.name, transcription: transcript }];
        renderEntryAttachments();
        markDirty();
        await saveCurrentEntry({ silent: true });
        toast("Voice note attached", "success");
      },
    });
  };
  ui.btnOpenSketch.onclick = () => {
    if (!state.current || !state.permissions.can_edit) return;
    openSketchPadModal(openModal, {
      onAttach: async (blob, name) => {
        const uploaded = await uploadDiaryMedia(state.current.id, blob, { filename: name, basePrefix: base });
        state.current.media = [...(state.current.media || []), { id: uploaded.id, name: uploaded.name, transcription: "" }];
        renderEntryAttachments();
        markDirty();
        await saveCurrentEntry({ silent: true });
        toast("Sketch attached", "success");
      },
    });
  };

  // Drag & Drop + Clipboard Paste of images/files onto editor
  ui.editorPane.addEventListener("dragover", (e) => {
    if (!state.permissions.can_edit) return;
    e.preventDefault();
    ui.editorPane.classList.add("is-drop-target");
  });
  ui.editorPane.addEventListener("dragleave", () => ui.editorPane.classList.remove("is-drop-target"));
  ui.editorPane.addEventListener("drop", async (e) => {
    e.preventDefault();
    ui.editorPane.classList.remove("is-drop-target");
    if (e.dataTransfer?.files?.length) await attachFiles(e.dataTransfer.files);
  });
  ui.entryBody.addEventListener("paste", async (e) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length && state.permissions.can_edit) {
      e.preventDefault();
      await attachFiles(files);
    }
  });

  // Global keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveCurrentEntry({ silent: false });
    } else if (mod && e.key.toLowerCase() === "k") {
      e.preventDefault();
      ui.searchBar.hidden = false;
      ui.searchInput.focus();
    } else if (mod && e.shiftKey && e.key.toLowerCase() === "f") {
      e.preventDefault();
      setFocusMode(!state.focusMode);
    } else if (mod && e.key.toLowerCase() === "b" && document.activeElement === ui.entryBody) {
      e.preventDefault();
      applyMarkdownCommand(ui.entryBody, "bold");
    } else if (mod && e.key.toLowerCase() === "i" && document.activeElement === ui.entryBody) {
      e.preventDefault();
      applyMarkdownCommand(ui.entryBody, "italic");
    } else if (e.key === "Escape" && state.focusMode && ui.modalRoot.hidden && ui.lightboxRoot.hidden) {
      setFocusMode(false);
    }
  });

  // Live bidirectional sync with iOS & Android
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") pollSyncState();
  });
  window.addEventListener("focus", () => pollSyncState());
  state.syncPollTimer = setInterval(pollSyncState, 12000);
}

/* ===========================
   BOOT
   =========================== */
async function boot() {
  wireEvents();
  bindEntryToEditor(makeNewEntry(), { switchPane: false });
  await loadAll({ silent: true });
  // On desktop, automatically open the latest entry if one exists so the screen feels alive immediately
  if (state.entries.length && window.innerWidth >= 960) {
    await openEntryById(state.entries[0].id);
  }
}

boot();
