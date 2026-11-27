/**
 * Day One-parity View Renderers:
 * - Timeline List (Month/Year headers, stacked weekday/day column, thumbnails, metadata chips)
 * - Calendar (Month grid with photo/dot badges, back-journaling CTA, quick Year overview)
 * - Media Gallery (Photos, Videos, Audio, Sketches grouped by Month)
 * - On This Day Memories (Grouped by Years Ago + memory reflection CTA)
 * - Streaks & Insights Dashboard (Current/Longest Streak, 52-week Heatmap, Mood/Tag/Place charts)
 * - Filter Sheet, Templates Gallery, Journal Manager, Entry Info Inspector, Export/Print, Preferences
 */
import { escapeHtml, stripMarkdownPreview } from "./markdown.js";
import { entryMediaUrl } from "./media.js";
import { PLACE_PRESETS } from "./map.js";

const WEEKDAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const JOURNAL_SWATCHES = [
  "#44B6FF", "#2FB67C", "#F59E0B", "#8B5CF6", "#EC4899",
  "#0EA5E9", "#EF4444", "#14B8A6", "#6366F1", "#1E293B",
];
export const MOOD_OPTIONS = [
  "Grateful", "Joyful", "Calm", "Energized", "Inspired", "Reflective", "Focused", "Tired", "Anxious", "Stressed",
];
export const ACTIVITY_OPTIONS = [
  "Stationary", "Walking", "Running", "Cycling", "Driving", "Flying", "Hiking", "Working", "Reading", "Traveling",
];
export const WEATHER_OPTIONS = [
  { condition: "Sunny", icon: "☀️" },
  { condition: "Partly Cloudy", icon: "⛅" },
  { condition: "Cloudy", icon: "☁️" },
  { condition: "Rain", icon: "🌧️" },
  { condition: "Storm", icon: "⛈️" },
  { condition: "Snow", icon: "❄️" },
  { condition: "Windy", icon: "💨" },
  { condition: "Clear Night", icon: "🌙" },
];

export function parseIsoDate(dateStr) {
  const [y, m, d] = String(dateStr || "2026-01-01").split("-").map(Number);
  return new Date(y || 2026, (m || 1) - 1, d || 1);
}

export function formatShortTime(entry) {
  if (entry.captured_at) {
    const dt = new Date(entry.captured_at);
    if (!Number.isNaN(dt.getTime())) {
      return dt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }
  }
  const m = String(entry.time || "").match(/(\d{1,2}):(\d{2})/);
  if (!m) return entry.time || "";
  const h = Number(m[1]);
  const ampm = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${h12}:${m[2]} ${ampm}`;
}

export function formatTemp(weather, unit = "F") {
  if (!weather || typeof weather.temp_c !== "number") return "";
  if (unit === "C") return `${Math.round(weather.temp_c)}°C`;
  return `${Math.round((weather.temp_c * 9) / 5 + 32)}°F`;
}

export function todayIsoLocal() {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/* ===========================
   1. TIMELINE LIST VIEW
   =========================== */
export function renderTimelineList(host, entries, { currentId, journalsMap, tempUnit, basePrefix, onSelect, onNewEntry, onToggleFav }) {
  host.innerHTML = "";
  if (!entries.length) {
    host.innerHTML = `
      <div class="empty-card">
        <div class="empty-icon">📖</div>
        <h3>No entries here yet</h3>
        <p>Start capturing today’s moments, photos, voice notes, or sketches — they sync automatically with AutoYou on iOS and Android.</p>
        <button type="button" class="btn-primary" id="empty-new-btn">Write Your First Entry</button>
      </div>
    `;
    host.querySelector("#empty-new-btn")?.addEventListener("click", () => onNewEntry?.());
    return;
  }

  const pinned = entries.filter((e) => e.pinned);
  const regular = entries.filter((e) => !e.pinned);

  const renderCard = (row) => {
    const dt = parseIsoDate(row.date);
    const weekday = WEEKDAYS[dt.getDay()];
    const dayNum = dt.getDate();
    const firstVisual = (row.media || []).find((m) => m.kind === "image");
    const audioCount = row.media_counts?.audio || 0;
    const videoCount = row.media_counts?.video || 0;
    const photoCount = row.media_counts?.photo || 0;
    const journalName = row.journal || "Daily Journal";
    const jColor = journalsMap.get(journalName.toLowerCase())?.color || "#44B6FF";

    const card = document.createElement("div");
    card.className = `timeline-row${row.id === currentId ? " is-active" : ""}`;
    card.setAttribute("role", "listitem");
    card.tabIndex = 0;

    const dateCol = document.createElement("div");
    dateCol.className = "row-date-col";
    dateCol.innerHTML = `<span class="row-weekday">${weekday}</span><strong class="row-daynum">${dayNum}</strong>`;

    const mainCol = document.createElement("div");
    mainCol.className = "row-main-col";

    const titleRow = document.createElement("div");
    titleRow.className = "row-title-line";
    const titleEl = document.createElement("h3");
    titleEl.textContent = row.title || "Untitled";
    titleRow.appendChild(titleEl);
    if (row.pinned) {
      const pin = document.createElement("span");
      pin.className = "row-pin-badge";
      pin.title = "Pinned";
      pin.textContent = "📌";
      titleRow.appendChild(pin);
    }
    if (row.favorite) {
      const fav = document.createElement("span");
      fav.className = "row-fav-badge";
      fav.title = "Favorite";
      fav.textContent = "♥";
      titleRow.appendChild(fav);
    }

    const excerpt = document.createElement("p");
    excerpt.className = "row-excerpt";
    excerpt.textContent = stripMarkdownPreview(row.preview || "");

    const meta = document.createElement("div");
    meta.className = "row-meta-line";
    const parts = [];
    parts.push(`<span class="meta-time">${escapeHtml(formatShortTime(row))}</span>`);
    if (row.journal) {
      parts.push(`<span class="meta-journal"><i style="background:${escapeHtml(jColor)}"></i>${escapeHtml(row.journal)}</span>`);
    }
    if (row.location?.name) {
      parts.push(`<span class="meta-place">📍 ${escapeHtml(row.location.name)}</span>`);
    }
    if (row.weather?.condition || typeof row.weather?.temp_c === "number") {
      const wStr = [row.weather.icon || "", formatTemp(row.weather, tempUnit)].filter(Boolean).join(" ");
      if (wStr) parts.push(`<span class="meta-weather">${escapeHtml(wStr)}</span>`);
    }
    if (row.mood) {
      parts.push(`<span class="meta-mood">${escapeHtml(row.mood)}</span>`);
    }
    if (audioCount) {
      parts.push(`<span class="meta-pill-audio">🎙️ ${audioCount}</span>`);
    }
    if (videoCount) {
      parts.push(`<span class="meta-pill-audio">🎬 ${videoCount}</span>`);
    }
    if (row.has_checklist) {
      parts.push(`<span class="meta-pill-check">☑</span>`);
    }
    meta.innerHTML = parts.join('<span class="dot">·</span>');

    mainCol.append(titleRow, excerpt, meta);

    if (row.tags && row.tags.length) {
      const tagRow = document.createElement("div");
      tagRow.className = "row-tags-inline";
      row.tags.slice(0, 4).forEach((t) => {
        const sp = document.createElement("span");
        sp.textContent = `#${t}`;
        tagRow.appendChild(sp);
      });
      if (row.tags.length > 4) {
        const more = document.createElement("span");
        more.textContent = `+${row.tags.length - 4}`;
        tagRow.appendChild(more);
      }
      mainCol.appendChild(tagRow);
    }

    card.append(dateCol, mainCol);

    if (firstVisual) {
      const thumbWrap = document.createElement("div");
      thumbWrap.className = "row-thumb-wrap";
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = "";
      img.src = entryMediaUrl(row.id, firstVisual.id, basePrefix);
      thumbWrap.appendChild(img);
      if (photoCount > 1) {
        const cnt = document.createElement("span");
        cnt.className = "row-thumb-count";
        cnt.textContent = `+${photoCount - 1}`;
        thumbWrap.appendChild(cnt);
      }
      card.appendChild(thumbWrap);
    }

    card.addEventListener("click", () => onSelect(row.id));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onSelect(row.id);
      }
    });
    return card;
  };

  if (pinned.length) {
    const header = document.createElement("div");
    header.className = "month-divider pinned-divider";
    header.textContent = `Pinned · ${pinned.length}`;
    host.appendChild(header);
    pinned.forEach((row) => host.appendChild(renderCard(row)));
  }

  let lastGroup = "";
  for (const row of regular) {
    const dt = parseIsoDate(row.date);
    const groupKey = `${MONTHS[dt.getMonth()]} ${dt.getFullYear()}`;
    if (groupKey !== lastGroup) {
      lastGroup = groupKey;
      const header = document.createElement("div");
      header.className = "month-divider";
      header.textContent = groupKey;
      host.appendChild(header);
    }
    host.appendChild(renderCard(row));
  }
}

/* ===========================
   2. CALENDAR VIEW
   =========================== */
export function renderCalendarView(host, entries, state, { basePrefix, onSelectEntry, onNewEntryForDate, onChangeMonth }) {
  const today = todayIsoLocal();
  const year = state.calYear;
  const month = state.calMonth; // 0-based
  const selectedDate = state.calSelectedDate || today;

  const byDate = new Map();
  for (const e of entries) {
    if (!byDate.has(e.date)) byDate.set(e.date, []);
    byDate.get(e.date).push(e);
  }

  const firstOfMonth = new Date(year, month, 1);
  const startDay = firstOfMonth.getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const monthPrefix = `${year}-${String(month + 1).padStart(2, "0")}`;
  const monthEntryCount = entries.filter((e) => e.date.startsWith(monthPrefix)).length;

  host.innerHTML = `
    <div class="cal-shell">
      <div class="cal-header">
        <button type="button" class="icon-btn" id="cal-prev" aria-label="Previous month">‹</button>
        <div class="cal-title">
          <h2>${MONTHS[month]} ${year}</h2>
          <small>${monthEntryCount} ${monthEntryCount === 1 ? "entry" : "entries"} this month</small>
        </div>
        <div class="cal-header-right">
          <button type="button" class="btn-secondary-sm" id="cal-today">Today</button>
          <button type="button" class="icon-btn" id="cal-next" aria-label="Next month">›</button>
        </div>
      </div>

      <div class="cal-weekdays">
        ${WEEKDAYS.map((d) => `<span>${d}</span>`).join("")}
      </div>
      <div class="cal-grid" id="cal-grid"></div>

      <div class="cal-day-detail" id="cal-day-detail"></div>
    </div>
  `;

  const grid = host.querySelector("#cal-grid");
  for (let i = 0; i < startDay; i++) {
    const blank = document.createElement("div");
    blank.className = "cal-cell is-empty";
    grid.appendChild(blank);
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const iso = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const dayEntries = byDate.get(iso) || [];
    const firstPhotoEntry = dayEntries.find((e) => (e.media || []).some((m) => m.kind === "image"));
    const firstPhoto = firstPhotoEntry?.media?.find((m) => m.kind === "image");

    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = [
      "cal-cell",
      dayEntries.length ? "has-entries" : "",
      iso === today ? "is-today" : "",
      iso === selectedDate ? "is-selected" : "",
      firstPhoto ? "has-photo" : "",
    ].filter(Boolean).join(" ");

    if (firstPhoto) {
      const bg = document.createElement("img");
      bg.className = "cal-cell-photo";
      bg.loading = "lazy";
      bg.alt = "";
      bg.src = entryMediaUrl(firstPhotoEntry.id, firstPhoto.id, basePrefix);
      cell.appendChild(bg);
    }

    const num = document.createElement("span");
    num.className = "cal-day-num";
    num.textContent = String(day);
    cell.appendChild(num);

    if (dayEntries.length) {
      const dots = document.createElement("span");
      dots.className = "cal-dots";
      dots.textContent = dayEntries.length > 1 ? String(dayEntries.length) : "•";
      cell.appendChild(dots);
    }

    cell.addEventListener("click", () => {
      state.calSelectedDate = iso;
      onChangeMonth(year, month, iso);
    });
    grid.appendChild(cell);
  }

  // Day Detail Panel below calendar (supports back-journaling!)
  const detail = host.querySelector("#cal-day-detail");
  const dayEntries = byDate.get(selectedDate) || [];
  const prettyDay = parseIsoDate(selectedDate).toLocaleDateString(undefined, {
    weekday: "long", month: "long", day: "numeric", year: "numeric",
  });

  detail.innerHTML = `
    <div class="cal-day-head">
      <div>
        <strong>${escapeHtml(prettyDay)}</strong>
        <small>${dayEntries.length} ${dayEntries.length === 1 ? "entry" : "entries"}</small>
      </div>
      <button type="button" class="btn-chip-primary" id="cal-write-date">+ Write for this day</button>
    </div>
    <div class="cal-day-list" id="cal-day-list"></div>
  `;
  detail.querySelector("#cal-write-date").onclick = () => onNewEntryForDate(selectedDate);

  const listEl = detail.querySelector("#cal-day-list");
  if (!dayEntries.length) {
    listEl.innerHTML = `<p class="muted-note">No entries on this day yet. Back-journal to keep your memories and streak complete.</p>`;
  } else {
    for (const row of dayEntries) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cal-entry-card";
      btn.innerHTML = `
        <div class="cal-entry-info">
          <strong></strong>
          <p></p>
          <small></small>
        </div>
      `;
      btn.querySelector("strong").textContent = row.title;
      btn.querySelector("p").textContent = stripMarkdownPreview(row.preview || "");
      btn.querySelector("small").textContent = [formatShortTime(row), row.journal, row.location?.name].filter(Boolean).join(" · ");
      btn.onclick = () => onSelectEntry(row.id);
      listEl.appendChild(btn);
    }
  }

  host.querySelector("#cal-prev").onclick = () => {
    const prev = new Date(year, month - 1, 1);
    onChangeMonth(prev.getFullYear(), prev.getMonth(), state.calSelectedDate);
  };
  host.querySelector("#cal-next").onclick = () => {
    const next = new Date(year, month + 1, 1);
    onChangeMonth(next.getFullYear(), next.getMonth(), state.calSelectedDate);
  };
  host.querySelector("#cal-today").onclick = () => {
    const now = new Date();
    onChangeMonth(now.getFullYear(), now.getMonth(), today);
  };
}

/* ===========================
   3. MEDIA GALLERY VIEW
   =========================== */
export function renderMediaGalleryView(host, entries, activeKind, { basePrefix, onFilterKind, onOpenLightbox, onOpenEntry }) {
  const allItems = [];
  for (const entry of entries) {
    for (const m of entry.media || []) {
      allItems.push({
        ...m,
        entryId: entry.id,
        entryTitle: entry.title,
        entryDate: entry.date,
        journal: entry.journal,
        url: entryMediaUrl(entry.id, m.id, basePrefix),
      });
    }
  }

  const filtered = allItems.filter((item) => {
    if (activeKind === "all") return true;
    if (activeKind === "sketch") return Boolean(item.sketch);
    return item.kind === activeKind;
  });

  const counts = {
    all: allItems.length,
    image: allItems.filter((m) => m.kind === "image").length,
    video: allItems.filter((m) => m.kind === "video").length,
    audio: allItems.filter((m) => m.kind === "audio").length,
    sketch: allItems.filter((m) => m.sketch).length,
  };

  host.innerHTML = `
    <div class="media-gallery-shell">
      <div class="media-filter-pills" role="tablist">
        ${[
          ["all", `All (${counts.all})`],
          ["image", `Photos (${counts.image})`],
          ["video", `Videos (${counts.video})`],
          ["audio", `Audio (${counts.audio})`],
          ["sketch", `Sketches (${counts.sketch})`],
        ].map(([k, label]) => `<button type="button" class="pill-filter${activeKind === k ? " is-active" : ""}" data-mkind="${k}">${label}</button>`).join("")}
      </div>
      <div id="media-gallery-groups"></div>
    </div>
  `;

  host.querySelectorAll(".pill-filter").forEach((b) => {
    b.onclick = () => onFilterKind(b.dataset.mkind);
  });

  const groupsHost = host.querySelector("#media-gallery-groups");
  if (!filtered.length) {
    groupsHost.innerHTML = `
      <div class="empty-card compact">
        <div class="empty-icon">🖼️</div>
        <h3>No media yet</h3>
        <p>Photos, videos, voice recordings, and sketches from this web app or your iPhone &amp; Android Diary will appear here.</p>
      </div>
    `;
    return;
  }

  const visualsForLightbox = filtered.filter((m) => m.kind === "image" || m.kind === "video");

  let lastMonth = "";
  let currentGrid = null;
  for (const item of filtered) {
    const dt = parseIsoDate(item.entryDate);
    const key = `${MONTHS[dt.getMonth()]} ${dt.getFullYear()}`;
    if (key !== lastMonth) {
      lastMonth = key;
      const head = document.createElement("div");
      head.className = "month-divider";
      head.textContent = key;
      groupsHost.appendChild(head);
      currentGrid = document.createElement("div");
      currentGrid.className = "media-gallery-grid";
      groupsHost.appendChild(currentGrid);
    }

    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = `gallery-tile kind-${item.kind}`;
    if (item.kind === "image") {
      tile.innerHTML = `<img loading="lazy" alt="" src="${escapeHtml(item.url)}"><span class="gallery-overlay"><b></b><small></small></span>`;
      tile.querySelector("b").textContent = item.entryTitle;
      tile.querySelector("small").textContent = item.entryDate;
      const lbIdx = visualsForLightbox.indexOf(item);
      tile.onclick = () => onOpenLightbox(visualsForLightbox, lbIdx);
    } else if (item.kind === "video") {
      tile.innerHTML = `<div class="gallery-video-thumb"><span>▶</span><b></b><small></small></div>`;
      tile.querySelector("b").textContent = item.name;
      tile.querySelector("small").textContent = item.entryDate;
      const lbIdx = visualsForLightbox.indexOf(item);
      tile.onclick = () => onOpenLightbox(visualsForLightbox, lbIdx);
    } else if (item.kind === "audio") {
      tile.innerHTML = `<div class="gallery-audio-thumb"><span>🎙️</span><b></b><small></small></div>`;
      tile.querySelector("b").textContent = item.name;
      tile.querySelector("small").textContent = `${item.entryDate} · ${item.entryTitle}`;
      tile.onclick = () => onOpenEntry(item.entryId);
    } else {
      tile.innerHTML = `<div class="gallery-audio-thumb"><span>📎</span><b></b><small></small></div>`;
      tile.querySelector("b").textContent = item.name;
      tile.querySelector("small").textContent = item.entryDate;
      tile.onclick = () => onOpenEntry(item.entryId);
    }
    currentGrid.appendChild(tile);
  }
}

/* ===========================
   4. ON THIS DAY (MEMORIES) VIEW
   =========================== */
export function renderMemoriesView(host, entries, { basePrefix, onSelectEntry, onReflectEntry }) {
  const today = todayIsoLocal();
  const todayYear = Number(today.slice(0, 4));
  const todayMD = today.slice(5);
  const prettyToday = parseIsoDate(today).toLocaleDateString(undefined, { month: "long", day: "numeric" });

  const exactAnniversaries = entries
    .filter((e) => e.date.slice(5) === todayMD && Number(e.date.slice(0, 4)) < todayYear)
    .sort((a, b) => b.date.localeCompare(a.date));

  const todayEntries = entries.filter((e) => e.date === today);

  host.innerHTML = `
    <div class="memories-shell">
      <div class="memories-hero">
        <span class="memories-badge">ON THIS DAY</span>
        <h2>${escapeHtml(prettyToday)}</h2>
        <p>${
          exactAnniversaries.length
            ? `${exactAnniversaries.length} ${exactAnniversaries.length === 1 ? "memory" : "memories"} from years past.`
            : "Every entry you write today becomes a memory you’ll relive on this date in future years."
        }</p>
      </div>
      <div id="memories-cards" class="memories-cards"></div>
    </div>
  `;

  const cardsHost = host.querySelector("#memories-cards");
  if (!exactAnniversaries.length) {
    cardsHost.innerHTML = `
      <div class="empty-card compact">
        <div class="empty-icon">🕰️</div>
        <h3>No past-year entries for ${escapeHtml(prettyToday)} yet</h3>
        <p>${
          todayEntries.length
            ? `You already have ${todayEntries.length} ${todayEntries.length === 1 ? "entry" : "entries"} from today — they’ll shine here next year!`
            : "Write an entry today to start your On This Day timeline."
        }</p>
      </div>
    `;
    return;
  }

  for (const row of exactAnniversaries) {
    const yearsAgo = todayYear - Number(row.date.slice(0, 4));
    const firstPhoto = (row.media || []).find((m) => m.kind === "image");
    const card = document.createElement("article");
    card.className = "memory-card";
    card.innerHTML = `
      <div class="memory-card-label">${yearsAgo} ${yearsAgo === 1 ? "Year" : "Years"} Ago · ${escapeHtml(row.date)}</div>
      ${firstPhoto ? `<img class="memory-card-photo" loading="lazy" alt="" src="${escapeHtml(entryMediaUrl(row.id, firstPhoto.id, basePrefix))}">` : ""}
      <div class="memory-card-body">
        <h3></h3>
        <p></p>
        <div class="memory-card-actions">
          <button type="button" class="btn-secondary-sm" data-open>Read Entry</button>
          <button type="button" class="btn-chip-primary" data-reflect>Reflect on This</button>
        </div>
      </div>
    `;
    card.querySelector("h3").textContent = row.title;
    card.querySelector("p").textContent = stripMarkdownPreview(row.preview || "");
    card.querySelector("[data-open]").onclick = () => onSelectEntry(row.id);
    card.querySelector("[data-reflect]").onclick = () => onReflectEntry(row, yearsAgo);
    cardsHost.appendChild(card);
  }
}

/* ===========================
   5. STREAKS & INSIGHTS VIEW
   =========================== */
export function renderInsightsView(host, insights, preferences, { onFilterTag, onFilterMood, onWriteToday }) {
  const ins = insights || {};
  const goal = preferences?.daily_word_goal || 250;
  const dailyCounts = ins.daily_counts || {};
  const today = ins.today || todayIsoLocal();

  // Build 26-week (182-day) heatmap grid
  const todayDate = parseIsoDate(today);
  const cells = [];
  const start = new Date(todayDate);
  start.setDate(start.getDate() - 181);
  // Align to Sunday
  start.setDate(start.getDate() - start.getDay());
  const cursor = new Date(start);
  while (cursor <= todayDate) {
    const iso = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}-${String(cursor.getDate()).padStart(2, "0")}`;
    const c = dailyCounts[iso] || 0;
    const lvl = c === 0 ? 0 : c === 1 ? 1 : c === 2 ? 2 : 3;
    cells.push(`<span class="heat-cell lvl-${lvl}" title="${iso}: ${c} ${c === 1 ? "entry" : "entries"}"></span>`);
    cursor.setDate(cursor.getDate() + 1);
  }

  const topTags = Object.entries(ins.tag_counts || {}).slice(0, 8);
  const topMoods = Object.entries(ins.mood_counts || {}).slice(0, 6);
  const topPlaces = Object.entries(ins.place_counts || {}).slice(0, 6);

  host.innerHTML = `
    <div class="insights-shell">
      <div class="streak-hero-grid">
        <div class="stat-card accent-streak">
          <div class="stat-label">Current Streak</div>
          <div class="stat-value">🔥 ${ins.current_streak || 0} <span>days</span></div>
          <div class="stat-sub">${ins.journaled_today ? "You journaled today!" : "Write today to extend your streak"}</div>
          ${!ins.journaled_today ? `<button type="button" class="btn-chip-primary stat-cta" id="ins-write-today">Write Today</button>` : ""}
        </div>
        <div class="stat-card">
          <div class="stat-label">Longest Streak</div>
          <div class="stat-value">🏆 ${ins.longest_streak || 0} <span>days</span></div>
          <div class="stat-sub">${ins.active_days || 0} total active days</div>
        </div>
      </div>

      <div class="stats-quad">
        <div class="mini-stat"><strong>${ins.total_entries || 0}</strong><span>Entries</span></div>
        <div class="mini-stat"><strong>${(ins.total_words || 0).toLocaleString()}</strong><span>Words</span></div>
        <div class="mini-stat"><strong>${ins.total_media || 0}</strong><span>Media</span></div>
        <div class="mini-stat"><strong>${ins.entries_this_month || 0}</strong><span>This Month</span></div>
      </div>

      <section class="insight-card">
        <div class="insight-card-head">
          <h3>Writing Activity (Last 6 Months)</h3>
          <small>Daily word goal: ${goal} words</small>
        </div>
        <div class="heatmap-scroll">
          <div class="heatmap-grid">${cells.join("")}</div>
        </div>
        <div class="heatmap-legend">
          <span>Less</span>
          <i class="heat-cell lvl-0"></i><i class="heat-cell lvl-1"></i><i class="heat-cell lvl-2"></i><i class="heat-cell lvl-3"></i>
          <span>More</span>
        </div>
      </section>

      ${topMoods.length ? `
      <section class="insight-card">
        <h3>Moods</h3>
        <div class="bar-list">
          ${topMoods.map(([mood, count]) => {
            const pct = Math.max(8, Math.round((count / (topMoods[0][1] || 1)) * 100));
            return `<button type="button" class="bar-row" data-mood="${escapeHtml(mood)}">
              <span>${escapeHtml(mood)}</span>
              <div class="bar-track"><i style="width:${pct}%"></i></div>
              <b>${count}</b>
            </button>`;
          }).join("")}
        </div>
      </section>` : ""}

      ${topTags.length ? `
      <section class="insight-card">
        <h3>Top Tags</h3>
        <div class="insight-chip-list">
          ${topTags.map(([tag, count]) => `<button type="button" class="insight-chip" data-tag="${escapeHtml(tag)}">#${escapeHtml(tag)} <span>${count}</span></button>`).join("")}
        </div>
      </section>` : ""}

      ${topPlaces.length ? `
      <section class="insight-card">
        <h3>Top Places</h3>
        <div class="insight-chip-list">
          ${topPlaces.map(([place, count]) => `<span class="insight-chip is-static">📍 ${escapeHtml(place)} <span>${count}</span></span>`).join("")}
        </div>
      </section>` : ""}
    </div>
  `;

  host.querySelector("#ins-write-today")?.addEventListener("click", () => onWriteToday?.());
  host.querySelectorAll("[data-tag]").forEach((b) => b.addEventListener("click", () => onFilterTag?.(b.dataset.tag)));
  host.querySelectorAll("[data-mood]").forEach((b) => b.addEventListener("click", () => onFilterMood?.(b.dataset.mood)));
}

/* ===========================
   6. DAY ONE FILTER SHEET
   =========================== */
export function openFilterModal(openModal, entries, filters, { onApply }) {
  const counts = {
    favorite: entries.filter((e) => e.favorite).length,
    checklist: entries.filter((e) => e.has_checklist).length,
    tagged: entries.filter((e) => (e.tags || []).length > 0).length,
    place: entries.filter((e) => e.location && (e.location.name || typeof e.location.lat === "number")).length,
    audio: entries.filter((e) => (e.media_counts?.audio || 0) > 0).length,
    photo: entries.filter((e) => (e.media_counts?.photo || 0) > 0).length,
    video: entries.filter((e) => (e.media_counts?.video || 0) > 0).length,
    sketch: entries.filter((e) => (e.media_counts?.sketch || 0) > 0).length,
  };

  const draft = { ...filters };
  const body = document.createElement("div");
  body.className = "filter-sheet";
  const rows = [
    ["favorite", "❤️", "Favorite", counts.favorite],
    ["checklist", "☑️", "Checklist", counts.checklist],
    ["tagged", "🏷️", "Has Tags", counts.tagged],
    ["place", "📍", "Has Place", counts.place],
    ["audio", "🔊", "Audio / Voice Note", counts.audio],
    ["photo", "🖼️", "Photo", counts.photo],
    ["video", "🎬", "Video", counts.video],
    ["sketch", "✏️", "Sketch", counts.sketch],
  ];

  body.innerHTML = `
    <div class="filter-rows">
      ${rows.map(([key, icon, label, cnt]) => `
        <button type="button" class="filter-row${draft[key] ? " is-active" : ""}" data-fkey="${key}">
          <span class="filter-row-icon">${icon}</span>
          <span class="filter-row-label">${label}</span>
          <span class="filter-row-count">${cnt}</span>
          <span class="filter-row-check">${draft[key] ? "✓" : ""}</span>
        </button>
      `).join("")}
    </div>
    <div class="sheet-footer">
      <button type="button" class="btn-secondary" id="flt-clear">Clear All</button>
      <button type="button" class="btn-primary" id="flt-apply">Apply Filters</button>
    </div>
  `;

  const close = openModal({ title: "Filter Entries", body });
  body.querySelectorAll(".filter-row").forEach((btn) => {
    btn.onclick = () => {
      const k = btn.dataset.fkey;
      draft[k] = !draft[k];
      btn.classList.toggle("is-active", draft[k]);
      btn.querySelector(".filter-row-check").textContent = draft[k] ? "✓" : "";
    };
  });
  body.querySelector("#flt-clear").onclick = () => {
    onApply({ favorite: false, checklist: false, tagged: false, place: false, audio: false, photo: false, video: false, sketch: false, tag: "", mood: "" });
    close();
  };
  body.querySelector("#flt-apply").onclick = () => {
    onApply(draft);
    close();
  };
}

/* ===========================
   7. TEMPLATES GALLERY MODAL
   =========================== */
export function openTemplatesModal(openModal, templates, { canEdit, onUseTemplate, onCreateTemplate, onDeleteTemplate }) {
  const body = document.createElement("div");
  body.className = "templates-sheet";

  const byCat = new Map();
  for (const t of templates) {
    const cat = t.category || "Templates";
    if (!byCat.has(cat)) byCat.set(cat, []);
    byCat.get(cat).push(t);
  }

  let html = "";
  for (const [cat, items] of byCat.entries()) {
    html += `<h4 class="tpl-cat-title">${escapeHtml(cat)}</h4><div class="tpl-grid">`;
    for (const t of items) {
      html += `
        <div class="tpl-card" style="--tpl-color:${escapeHtml(t.color || "#44B6FF")}" data-tid="${escapeHtml(t.id)}">
          <button type="button" class="tpl-card-main" data-use="${escapeHtml(t.id)}">
            <strong>${escapeHtml(t.name)}</strong>
            <small>${escapeHtml((t.tags || []).map((x) => "#" + x).join(" ") || t.title)}</small>
          </button>
          ${!t.builtin && canEdit ? `<button type="button" class="tpl-del" data-del="${escapeHtml(t.id)}" title="Delete template">×</button>` : ""}
        </div>
      `;
    }
    html += `</div>`;
  }

  if (canEdit) {
    html += `
      <details class="tpl-create-box">
        <summary>+ Create Custom Template</summary>
        <form id="tpl-create-form" class="sheet-form">
          <label class="sheet-field"><span>Template Name</span><input name="name" required maxlength="80" placeholder="e.g., Book Notes"></label>
          <div class="field-row">
            <label class="sheet-field"><span>Category</span><input name="category" maxlength="48" value="Custom"></label>
            <label class="sheet-field"><span>Color</span><input name="color" type="color" value="#44B6FF"></label>
          </div>
          <label class="sheet-field"><span>Default Entry Title</span><input name="title" maxlength="200" placeholder="Book Reflection"></label>
          <label class="sheet-field"><span>Default Tags (comma separated)</span><input name="tags" placeholder="reading, notes"></label>
          <label class="sheet-field"><span>Template Markdown Body</span><textarea name="body" rows="5" placeholder="### Key takeaway&#10;- &#10;&#10;### Favorite quote&#10;&gt; "></textarea></label>
          <button type="submit" class="btn-primary">Save Template</button>
        </form>
      </details>
    `;
  }

  body.innerHTML = html;
  const close = openModal({ title: "Journal Templates", body, wide: true });

  body.querySelectorAll("[data-use]").forEach((btn) => {
    btn.onclick = () => {
      const tpl = templates.find((t) => t.id === btn.dataset.use);
      if (tpl) {
        onUseTemplate(tpl);
        close();
      }
    };
  });

  body.querySelectorAll("[data-del]").forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm("Delete this custom template?")) return;
      await onDeleteTemplate(btn.dataset.del);
      close();
    };
  });

  body.querySelector("#tpl-create-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = {
      name: String(fd.get("name") || "").trim(),
      category: String(fd.get("category") || "Custom").trim(),
      color: String(fd.get("color") || "#44B6FF"),
      title: String(fd.get("title") || "").trim() || String(fd.get("name") || "").trim(),
      tags: String(fd.get("tags") || "").split(",").map((s) => s.trim()).filter(Boolean),
      body: String(fd.get("body") || ""),
    };
    await onCreateTemplate(payload);
    close();
  });
}

/* ===========================
   8. JOURNAL MANAGER MODAL
   =========================== */
export function openJournalModal(openModal, existingJournal, { canDelete, onSave, onDelete }) {
  const isEdit = Boolean(existingJournal);
  let chosenColor = existingJournal?.color || JOURNAL_SWATCHES[0];
  const body = document.createElement("form");
  body.className = "sheet-form";
  body.innerHTML = `
    <label class="sheet-field">
      <span>Journal Name</span>
      <input name="name" required maxlength="256" placeholder="e.g., Travel, Work, Gratitude" value="${escapeHtml(existingJournal?.name || "")}">
    </label>
    <label class="sheet-field">
      <span>Description (optional)</span>
      <input name="description" maxlength="300" placeholder="What belongs in this journal?" value="${escapeHtml(existingJournal?.description || "")}">
    </label>
    <div class="sheet-field">
      <span>Journal Color</span>
      <div class="color-swatch-row">
        ${JOURNAL_SWATCHES.map((c) => `<button type="button" class="swatch-btn${c.toLowerCase() === chosenColor.toLowerCase() ? " is-active" : ""}" data-color="${c}" style="background:${c}"></button>`).join("")}
      </div>
    </div>
    <label class="toggle-row">
      <input type="checkbox" name="streak_included" ${existingJournal?.streak_included !== false ? "checked" : ""}>
      <span>Include entries in daily writing streak</span>
    </label>
    <div class="sheet-footer">
      ${isEdit && !existingJournal.is_default && canDelete ? `<button type="button" class="btn-danger-ghost" id="j-del-btn">Delete Journal</button>` : `<span></span>`}
      <div class="footer-right">
        <button type="button" class="btn-secondary" id="j-cancel">Cancel</button>
        <button type="submit" class="btn-primary">${isEdit ? "Save Changes" : "Create Journal"}</button>
      </div>
    </div>
  `;

  const close = openModal({ title: isEdit ? "Edit Journal" : "New Journal", body });
  body.querySelectorAll(".swatch-btn").forEach((btn) => {
    btn.onclick = () => {
      chosenColor = btn.dataset.color;
      body.querySelectorAll(".swatch-btn").forEach((b) => b.classList.toggle("is-active", b === btn));
    };
  });
  body.querySelector("#j-cancel").onclick = () => close();
  body.querySelector("#j-del-btn")?.addEventListener("click", async () => {
    if (!confirm(`Delete journal "${existingJournal.name}"? Its entries will be preserved in Daily Journal.`)) return;
    await onDelete(existingJournal.name);
    close();
  });
  body.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(body);
    await onSave({
      name: String(fd.get("name") || "").trim(),
      description: String(fd.get("description") || "").trim(),
      color: chosenColor,
      streak_included: body.querySelector('[name="streak_included"]').checked,
      rename_from: isEdit ? existingJournal.name : undefined,
    });
    close();
  };
}

/* ===========================
   9. ENTRY INFO & METADATA INSPECTOR
   =========================== */
export function openEntryInfoModal(openModal, entry, journals, tempUnit, { onUpdate }) {
  const loc = entry.location || {};
  const w = entry.weather || {};
  const initTime = (() => {
    if (entry.captured_at) {
      const dt = new Date(entry.captured_at);
      if (!Number.isNaN(dt.getTime())) {
        return `${String(dt.getHours()).padStart(2, "0")}:${String(dt.getMinutes()).padStart(2, "0")}`;
      }
    }
    const m = String(entry.time || "").match(/(\d{2}):(\d{2})/);
    return m ? `${m[1]}:${m[2]}` : "12:00";
  })();
  const initTempDisplay = typeof w.temp_c === "number"
    ? (tempUnit === "C" ? String(Math.round(w.temp_c)) : String(Math.round((w.temp_c * 9) / 5 + 32)))
    : "";

  const body = document.createElement("form");
  body.className = "sheet-form info-inspector";
  body.innerHTML = `
    <div class="field-row">
      <label class="sheet-field">
        <span>Date</span>
        <input type="date" name="date" required value="${escapeHtml(entry.date)}">
      </label>
      <label class="sheet-field">
        <span>Time</span>
        <input type="time" name="time_hm" value="${escapeHtml(initTime)}">
      </label>
    </div>

    <label class="sheet-field">
      <span>Journal</span>
      <select name="journal">
        ${journals.map((j) => {
          const val = j.is_default ? "" : j.name;
          const selected = (entry.journal || "").toLowerCase() === val.toLowerCase() || (j.is_default && !entry.journal);
          return `<option value="${escapeHtml(val)}"${selected ? " selected" : ""}>${escapeHtml(j.name)}</option>`;
        }).join("")}
      </select>
    </label>

    <div class="inspector-section">
      <div class="inspector-section-head">
        <span>📍 Location</span>
        <button type="button" class="text-action" id="btn-geolocate">Use current GPS</button>
      </div>
      <input name="loc_name" maxlength="200" placeholder="Place name (e.g., Golden Gate Park, SF)" value="${escapeHtml(loc.name || "")}">
      <div class="preset-chips">
        ${PLACE_PRESETS.slice(0, 6).map((p, idx) => `<button type="button" class="mini-chip" data-preset="${idx}">${escapeHtml(p.city)}</button>`).join("")}
      </div>
      <div class="field-row">
        <label class="sheet-field"><span>Latitude</span><input name="loc_lat" type="number" step="0.0001" placeholder="37.7749" value="${loc.lat ?? ""}"></label>
        <label class="sheet-field"><span>Longitude</span><input name="loc_lng" type="number" step="0.0001" placeholder="-122.4194" value="${loc.lng ?? ""}"></label>
      </div>
    </div>

    <div class="inspector-section">
      <div class="inspector-section-head"><span>☀️ Weather</span></div>
      <div class="field-row">
        <label class="sheet-field">
          <span>Condition</span>
          <select name="weather_cond">
            <option value="">None</option>
            ${WEATHER_OPTIONS.map((opt) => `<option value="${escapeHtml(opt.condition)}" data-icon="${opt.icon}"${w.condition === opt.condition ? " selected" : ""}>${opt.icon} ${escapeHtml(opt.condition)}</option>`).join("")}
          </select>
        </label>
        <label class="sheet-field">
          <span>Temp (°${tempUnit})</span>
          <input name="weather_temp" type="number" step="1" placeholder="${tempUnit === "C" ? "21" : "70"}" value="${escapeHtml(initTempDisplay)}">
        </label>
      </div>
    </div>

    <div class="field-row">
      <label class="sheet-field">
        <span>Mood</span>
        <select name="mood">
          <option value="">None</option>
          ${MOOD_OPTIONS.map((m) => `<option value="${escapeHtml(m)}"${entry.mood === m ? " selected" : ""}>${escapeHtml(m)}</option>`).join("")}
        </select>
      </label>
      <label class="sheet-field">
        <span>Activity</span>
        <select name="activity">
          <option value="">None</option>
          ${ACTIVITY_OPTIONS.map((a) => `<option value="${escapeHtml(a)}"${entry.activity === a ? " selected" : ""}>${escapeHtml(a)}</option>`).join("")}
        </select>
      </label>
    </div>

    <div class="field-row">
      <label class="sheet-field">
        <span>Step Count</span>
        <input name="steps" type="number" min="0" max="500000" placeholder="e.g., 8420" value="${entry.steps ?? ""}">
      </label>
      <label class="sheet-field">
        <span>Time Zone</span>
        <input name="time_zone" maxlength="120" value="${escapeHtml(entry.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone)}">
      </label>
    </div>

    <div class="entry-stats-strip">
      <span>Words: <b>${entry.word_count || 0}</b></span>
      <span>Characters: <b>${(entry.body || "").length}</b></span>
      <span>Attachments: <b>${(entry.media || []).length}</b></span>
    </div>

    <div class="sheet-footer">
      <button type="button" class="btn-secondary" id="info-cancel">Cancel</button>
      <button type="submit" class="btn-primary">Apply Details</button>
    </div>
  `;

  const close = openModal({ title: "Entry Details & Metadata", body });

  body.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.onclick = () => {
      const p = PLACE_PRESETS[Number(btn.dataset.preset)];
      if (!p) return;
      body.querySelector('[name="loc_name"]').value = p.name;
      body.querySelector('[name="loc_lat"]').value = String(p.lat);
      body.querySelector('[name="loc_lng"]').value = String(p.lng);
    };
  });

  body.querySelector("#btn-geolocate").onclick = () => {
    if (!navigator.geolocation) {
      alert("Geolocation is unavailable in this browser.");
      return;
    }
    const btn = body.querySelector("#btn-geolocate");
    btn.textContent = "Locating…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const lat = Number(pos.coords.latitude.toFixed(5));
        const lng = Number(pos.coords.longitude.toFixed(5));
        body.querySelector('[name="loc_lat"]').value = String(lat);
        body.querySelector('[name="loc_lng"]').value = String(lng);
        if (!body.querySelector('[name="loc_name"]').value.trim()) {
          body.querySelector('[name="loc_name"]').value = `${lat.toFixed(3)}, ${lng.toFixed(3)}`;
        }
        btn.textContent = "Located ✓";
      },
      (err) => {
        btn.textContent = "Use current GPS";
        alert(err.message || "Could not determine location.");
      },
      { timeout: 8000 }
    );
  };

  body.querySelector("#info-cancel").onclick = () => close();
  body.onsubmit = (e) => {
    e.preventDefault();
    const fd = new FormData(body);
    const locName = String(fd.get("loc_name") || "").trim();
    const latRaw = String(fd.get("loc_lat") || "").trim();
    const lngRaw = String(fd.get("loc_lng") || "").trim();
    let location = null;
    if (locName || (latRaw && lngRaw)) {
      location = {};
      if (locName) location.name = locName;
      if (latRaw && lngRaw && !Number.isNaN(Number(latRaw)) && !Number.isNaN(Number(lngRaw))) {
        location.lat = Number(latRaw);
        location.lng = Number(lngRaw);
      }
    }

    const wCond = String(fd.get("weather_cond") || "").trim();
    const wTempRaw = String(fd.get("weather_temp") || "").trim();
    let weather = null;
    if (wCond || wTempRaw) {
      weather = {};
      if (wCond) {
        weather.condition = wCond;
        const matched = WEATHER_OPTIONS.find((x) => x.condition === wCond);
        if (matched) weather.icon = matched.icon;
      }
      if (wTempRaw && !Number.isNaN(Number(wTempRaw))) {
        const n = Number(wTempRaw);
        weather.temp_c = tempUnit === "C" ? n : Number((((n - 32) * 5) / 9).toFixed(1));
      }
    }

    const stepsRaw = String(fd.get("steps") || "").trim();
    const steps = stepsRaw && !Number.isNaN(Number(stepsRaw)) ? Math.max(0, Math.round(Number(stepsRaw))) : null;

    onUpdate({
      date: String(fd.get("date") || entry.date),
      time_hm: String(fd.get("time_hm") || initTime),
      time_zone: String(fd.get("time_zone") || "").trim() || Intl.DateTimeFormat().resolvedOptions().timeZone,
      journal: String(fd.get("journal") || ""),
      location,
      weather,
      mood: String(fd.get("mood") || ""),
      activity: String(fd.get("activity") || ""),
      steps,
    });
    close();
  };
}

/* ===========================
   10. EXPORT & PRINT BOOK MODAL
   =========================== */
export function openExportModal(openModal, journals, activeJournal, { basePrefix, onPrintBook }) {
  const body = document.createElement("div");
  body.className = "export-sheet";
  body.innerHTML = `
    <label class="sheet-field">
      <span>Scope</span>
      <select id="exp-journal">
        <option value="">All Journals</option>
        ${journals.map((j) => `<option value="${escapeHtml(j.name)}"${activeJournal && activeJournal.toLowerCase() === j.name.toLowerCase() ? " selected" : ""}>${escapeHtml(j.name)} (${j.count})</option>`).join("")}
      </select>
    </label>
    <div class="export-options">
      <button type="button" class="export-row" data-fmt="dayone_json">
        <strong>Export Day One JSON (.json)</strong>
        <small>Full metadata, tags, locations, weather, and media descriptors</small>
      </button>
      <button type="button" class="export-row" data-fmt="markdown">
        <strong>Export Markdown (.md)</strong>
        <small>Portable Markdown archive ready for any editor or backup</small>
      </button>
      <button type="button" class="export-row" data-fmt="text">
        <strong>Export Plain Text (.txt)</strong>
        <small>Simple timeless plain text journal archive</small>
      </button>
      <button type="button" class="export-row accent" data-fmt="print">
        <strong>Print / Save as PDF Book</strong>
        <small>Formatted book layout with typography and photos via Print dialog</small>
      </button>
    </div>
  `;

  const close = openModal({ title: "Export & Print Book", body });
  const select = body.querySelector("#exp-journal");
  body.querySelectorAll(".export-row").forEach((btn) => {
    btn.onclick = () => {
      const fmt = btn.dataset.fmt;
      const j = select.value;
      if (fmt === "print") {
        close();
        onPrintBook(j);
        return;
      }
      const qs = new URLSearchParams({ format: fmt });
      if (j) qs.set("journal", j);
      const a = document.createElement("a");
      a.href = `${basePrefix}/api/diary/export?${qs.toString()}`;
      a.download = "";
      a.click();
      close();
    };
  });
}

/* ===========================
   11. PREFERENCES & MOBILE SYNC MODAL
   =========================== */
export function openPreferencesModal(openModal, prefs, syncState, { canEdit, onSave }) {
  const body = document.createElement("form");
  body.className = "sheet-form";
  body.innerHTML = `
    <div class="sync-status-card">
      <div>
        <strong>Sync with iOS &amp; Android Native Diary</strong>
        <small>${syncState?.total || 0} entries stored · ${syncState?.encrypted ? "Independent-key encrypted" : "Local private store"} · Automatic two-way collection sync ready</small>
      </div>
      <span class="sync-badge-ok">Active</span>
    </div>

    <div class="field-row">
      <label class="sheet-field">
        <span>Theme</span>
        <select name="theme">
          <option value="system"${prefs.theme === "system" ? " selected" : ""}>System</option>
          <option value="light"${prefs.theme === "light" ? " selected" : ""}>Light</option>
          <option value="dark"${prefs.theme === "dark" ? " selected" : ""}>Dark</option>
        </select>
      </label>
      <label class="sheet-field">
        <span>Temperature</span>
        <select name="temp_unit">
          <option value="F"${prefs.temp_unit === "F" ? " selected" : ""}>Fahrenheit (°F)</option>
          <option value="C"${prefs.temp_unit === "C" ? " selected" : ""}>Celsius (°C)</option>
        </select>
      </label>
    </div>

    <div class="field-row">
      <label class="sheet-field">
        <span>Editor Font</span>
        <select name="font_family">
          <option value="sans"${prefs.font_family === "sans" ? " selected" : ""}>System Sans</option>
          <option value="serif"${prefs.font_family === "serif" ? " selected" : ""}>Book Serif</option>
          <option value="mono"${prefs.font_family === "mono" ? " selected" : ""}>Monospace</option>
        </select>
      </label>
      <label class="sheet-field">
        <span>Text Size</span>
        <select name="font_size">
          <option value="sm"${prefs.font_size === "sm" ? " selected" : ""}>Compact</option>
          <option value="md"${prefs.font_size === "md" ? " selected" : ""}>Standard</option>
          <option value="lg"${prefs.font_size === "lg" ? " selected" : ""}>Large</option>
        </select>
      </label>
    </div>

    <div class="field-row">
      <label class="sheet-field">
        <span>Daily Word Goal</span>
        <input name="daily_word_goal" type="number" min="0" max="50000" value="${prefs.daily_word_goal ?? 250}">
      </label>
      <label class="sheet-field">
        <span>Reminder Time</span>
        <input name="reminder_time" type="time" value="${escapeHtml(prefs.reminder_time || "20:00")}">
      </label>
    </div>

    <label class="toggle-row">
      <input type="checkbox" name="reminder_enabled" ${prefs.reminder_enabled ? "checked" : ""}>
      <span>Show daily writing reminder when I haven’t journaled yet</span>
    </label>

    <div class="sheet-footer">
      <button type="button" class="btn-secondary" id="pref-cancel">Cancel</button>
      <button type="submit" class="btn-primary"${canEdit ? "" : " disabled"}>Save Settings</button>
    </div>
  `;

  const close = openModal({ title: "Diary Settings", body });
  body.querySelector("#pref-cancel").onclick = () => close();
  body.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(body);
    await onSave({
      theme: String(fd.get("theme") || "system"),
      temp_unit: String(fd.get("temp_unit") || "F"),
      font_family: String(fd.get("font_family") || "sans"),
      font_size: String(fd.get("font_size") || "md"),
      daily_word_goal: Math.max(0, Number(fd.get("daily_word_goal") || 0)),
      reminder_time: String(fd.get("reminder_time") || "20:00"),
      reminder_enabled: body.querySelector('[name="reminder_enabled"]').checked,
    });
    close();
  };
}
