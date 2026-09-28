// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

(() => {
  "use strict";

  const boot = window.__PAGE_BOOTSTRAP__ || {};
  const access = Object.assign(
    { role: "viewer", can_add: false, can_edit: false, can_delete: false, can_manage: false },
    boot.access || {},
  );
  const PAGE_SIZE = Number(boot.page_size) || 20;
  const SUMMARY_POLL_MS = 30000;
  const SEARCH_DEBOUNCE_MS = 300;
  // Mirror the accent and icon names the backend assigns in _ITEM_KINDS.
  const ACCENTS = new Set(["blue", "purple", "green", "amber", "teal", "rose", "slate"]);
  const ICONS = new Set(["globe", "play", "chat", "image", "wave", "doc", "layers"]);
  const DEFAULT_FILTERS = { view: "feed", tab: "latest", query: "", order: "desc", range: "default", dateFrom: "", dateTo: "", sites: "" };
  const TYPE_TAB_LABELS = { latest: "Feed", articles: "Articles", videos: "Videos", social: "Social", photos: "Photos", audio: "Audio", files: "Files" };
  // Items kept ready on each side of the one on screen in the full-screen feed.
  const REEL_KEEP = 2;
  const DOUBLE_TAP_MS = 280;
  const profile = Object.assign({ name: "", avatar_url: "", mark_url: "", has_photo: false }, boot.profile || {});

  const state = {
    items: Array.isArray(boot.items) ? boot.items : [],
    hasMore: !!boot.has_more,
    nextCursor: boot.next_cursor || null,
    windowDays: Number(boot.window_days) || 0,
    summary: boot.summary || null,
    loading: false,
    error: "",
    ...DEFAULT_FILTERS,
    activeItemId: null,
    lastTrigger: null,
    requestToken: 0,
    renderedListHtml: null,
    confirmAction: null,
    uploading: false,
    sheetMedia: true,
  };
  const reel = {
    open: false,
    index: -1,
    muted: true,
    observer: null,
    trigger: null,
    lastTap: 0,
    tapSlide: null,
    tapTimer: null,
    scrollTimer: null,
    targetIndex: null,
    targetTimer: null,
  };
  const photo = { open: false, zoomed: false, trigger: null, lastTap: 0 };
  // History entries added by open overlays, innermost last.
  const overlayHistory = [];
  const afterPop = [];
  let ignoredPops = 0;

  const $ = (id) => document.getElementById(id);
  let searchTimer = null;
  let sitesTimer = null;
  let toastTimer = null;
  let summaryTimer = null;
  let feedObserver = null;
  let mediaObserver = null;

  // ── Formatting ─────────────────────────────────

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
  }

  function icon(name, extraClass = "") {
    return `<i class="ic ic-${name}${extraClass ? ` ${extraClass}` : ""}" aria-hidden="true"></i>`;
  }

  function safeAccent(value) {
    return ACCENTS.has(value) ? value : "slate";
  }

  function safeIcon(value) {
    return ICONS.has(value) ? value : "doc";
  }

  // Only http(s) links and this site's own API and assets may reach href or src.
  function safeUrl(value) {
    const text = String(value || "").trim();
    if (text.startsWith("./api/") || text.startsWith("./assets/")) {
      return text;
    }
    try {
      const parsed = new URL(text);
      return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "";
    } catch (_) {
      return "";
    }
  }

  // Saved times are server-local and zoneless; build them from their parts
  // so every browser reads them as local time.
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

  // Keep in step with _relative_day() in the backend's first paint.
  function relativeDay(value) {
    const parsed = parseTimestamp(value);
    if (!parsed) {
      return "";
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

  function formatFull(value) {
    const parsed = parseTimestamp(value);
    if (!parsed) {
      return "Unknown";
    }
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    }).format(parsed);
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

  function formatCount(value) {
    return new Intl.NumberFormat().format(Number(value) || 0);
  }

  function localIso(date) {
    const pad = (number) => String(number).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function currentTheme() {
    return document.documentElement.dataset.theme === "light" ? "light" : "dark";
  }

  // ── Link parsing (for embeds in the item sheet) ─

  function canonicalType(value) {
    const type = String(value || "").trim().toLowerCase();
    return type === "x" ? "twitter" : (type || "article");
  }

  function classifyUrl(value) {
    const url = String(value || "").toLowerCase();
    if (url.includes("youtube.com") || url.includes("youtu.be")) return "youtube";
    if (url.includes("tiktok.com")) return "tiktok";
    if (url.includes("threads.net") || url.includes("threads.com")) return "threads";
    if (url.includes("instagram.com")) return "instagram";
    if (url.includes("twitter.com") || url.includes("x.com")) return "twitter";
    if (url.includes(".mp4") || url.includes("/shorts/") || url.includes("/video/")) return "video";
    return "article";
  }

  function parseUrl(value) {
    try {
      return new URL(String(value || ""));
    } catch (_) {
      return null;
    }
  }

  function cleanId(value) {
    return String(value || "").replace(/[^A-Za-z0-9_-]/g, "");
  }

  function youtubeId(value) {
    const url = parseUrl(value);
    if (!url) return "";
    if (url.hostname.includes("youtu.be")) return cleanId(url.pathname.slice(1));
    if (!url.hostname.includes("youtube.com")) return "";
    const fromQuery = url.searchParams.get("v");
    if (fromQuery) return cleanId(fromQuery);
    const parts = url.pathname.split("/").filter(Boolean);
    return parts.length >= 2 && ["embed", "shorts", "live"].includes(parts[0]) ? cleanId(parts[1]) : "";
  }

  function twitterId(value) {
    const url = parseUrl(value);
    if (!url) return "";
    const parts = url.pathname.split("/").filter(Boolean);
    const index = parts.indexOf("status");
    return index >= 0 && parts[index + 1] ? String(parts[index + 1]).replace(/\D/g, "") : "";
  }

  function instagramEmbed(value) {
    const url = parseUrl(value);
    if (!url || !url.hostname.includes("instagram.com")) return "";
    const parts = url.pathname.split("/").filter(Boolean);
    return parts.length >= 2 && ["p", "reel", "tv"].includes(parts[0])
      ? `https://www.instagram.com/${parts[0]}/${cleanId(parts[1])}/embed/`
      : "";
  }

  function tiktokId(value) {
    const url = parseUrl(value);
    if (!url || !url.hostname.includes("tiktok.com")) return "";
    const match = url.pathname.match(/\/video\/(\d+)/);
    return match ? match[1] : String(url.searchParams.get("share_item_id") || url.searchParams.get("item_id") || "").replace(/\D/g, "");
  }

  // ── Markup (matches the backend's first paint) ─

  function thumbMarkup(view) {
    const play = view.icon === "play" ? `<span class="thumb-play" aria-hidden="true">${icon("play-fill")}</span>` : "";
    const thumb = safeUrl(view.thumb);
    if (thumb) {
      return `<span class="feed-thumb"><img src="${escapeHtml(thumb)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">${play}</span>`;
    }
    const videoThumb = safeUrl(view.video_thumb);
    if (videoThumb) {
      return `<span class="feed-thumb"><video data-src="${escapeHtml(videoThumb)}#t=0.1" muted playsinline preload="none" tabindex="-1" aria-hidden="true"></video>${play}</span>`;
    }
    const parts = [`<span class="feed-thumb tile accent-${safeAccent(view.accent)}">`];
    const favicon = safeUrl(view.favicon);
    if (favicon) {
      parts.push(`<img class="tile-favicon" src="${escapeHtml(favicon)}" alt="" loading="lazy" referrerpolicy="no-referrer">`);
    }
    if (view.label && view.tab !== "files") {
      const labelClass = String(view.label).length > 3 ? "tile-label tile-word" : "tile-label";
      parts.push(`<span class="${labelClass}">${escapeHtml(view.label)}</span>`);
    } else {
      parts.push(icon(safeIcon(view.icon), "tile-icon"));
      if (view.tab === "files" && view.label) {
        parts.push(`<span class="tile-ext">${escapeHtml(view.label)}</span>`);
      }
    }
    parts.push("</span>");
    return parts.join("");
  }

  function byline(view) {
    return Array.isArray(view.byline) ? view.byline : [view.kind, view.host];
  }

  function rowMarkup(item) {
    const view = item.view || {};
    const id = Number(item.id);
    const meta = [...byline(view), relativeDay(item.added_at)]
      .filter(Boolean)
      .map((part) => `<span>${escapeHtml(part)}</span>`)
      .join('<span aria-hidden="true">·</span>');
    const favourite = item.favourite ? `${icon("heart-fill", "feed-fav")}<span class="sr-only">Favourite</span>` : "";
    return `<article class="feed-row${id === state.activeItemId ? " is-active" : ""}" data-item-id="${id}">`
      + `<button class="feed-item" type="button" data-action="open" data-item-id="${id}">`
      + thumbMarkup(view)
      + `<span class="feed-body"><span class="feed-title">${escapeHtml(view.title || item.title || "Saved item")}</span>`
      + `<span class="feed-meta">${meta}${favourite}</span></span>`
      + icon("chevron-right", "feed-chevron")
      + "</button></article>";
  }

  function skeletonMarkup() {
    const row = '<div class="skeleton-row" aria-hidden="true"><span class="shimmer"></span><span class="skeleton-lines">'
      + '<span class="skeleton-line medium"></span><span class="skeleton-line"></span><span class="skeleton-line short"></span></span></div>';
    return row.repeat(4);
  }

  function filtersActive() {
    return state.view !== DEFAULT_FILTERS.view || state.tab !== DEFAULT_FILTERS.tab || !!state.query
      || state.range !== DEFAULT_FILTERS.range || !!state.sites.trim();
  }

  function emptyMarkup() {
    if (filtersActive()) {
      return `<div class="empty-state"><span class="empty-icon" aria-hidden="true">${icon("search")}</span>`
        + '<p class="empty-title">Nothing matches</p><p class="empty-copy">Try another tab, search or time range.</p>'
        + '<button class="button subtle" type="button" data-action="clear-filters">Show everything</button></div>';
    }
    const copy = access.can_add
      ? "Save a link or upload a file, or ask AutoYou in Chat to save something here."
      : "Nothing has been saved to this Page yet.";
    return `<div class="empty-state"><span class="empty-icon" aria-hidden="true">${icon("layers")}</span>`
      + `<p class="empty-title">Nothing here yet</p><p class="empty-copy">${escapeHtml(copy)}</p></div>`;
  }

  function favouriteCardMarkup(item) {
    if (!item) {
      return "";
    }
    const view = item.view || {};
    return `<button class="fav-card" type="button" data-action="open" data-item-id="${Number(item.id)}">`
      + '<span class="fav-card-body"><span class="fav-card-top">'
      + `<span class="fav-card-label">${icon("heart-fill")}Latest favourite</span>`
      + `<span class="fav-card-time">${escapeHtml(relativeDay(item.added_at))}</span></span>`
      + `<span class="fav-card-title">${escapeHtml(view.title || item.title || "Saved item")}</span>`
      + `<span class="fav-card-meta">${escapeHtml(byline(view).filter(Boolean).join(" · "))}</span></span>`
      + icon("chevron-right")
      + "</button>";
  }

  function tagChipsMarkup(tags) {
    return (Array.isArray(tags) ? tags : [])
      .filter((entry) => entry && entry.tag)
      .map((entry) => {
        const active = state.query && state.query.toLowerCase() === String(entry.tag).toLowerCase();
        return `<button class="tag-chip${active ? " is-active" : ""}" type="button" data-tag="${escapeHtml(entry.tag)}" aria-pressed="${active}">`
          + `${icon("tag")}<span>${escapeHtml(entry.tag)}</span></button>`;
      })
      .join("");
  }

  function coverMarkup(cover) {
    const url = cover ? safeUrl(cover.url) : "";
    return url
      ? `<img class="cover-img" src="${escapeHtml(url)}" alt="" decoding="async" referrerpolicy="no-referrer">`
      : '<span class="cover-fallback" aria-hidden="true"></span>';
  }

  // ── Rendering ──────────────────────────────────

  function setHtml(element, html) {
    if (element && element.innerHTML !== html) {
      element.innerHTML = html;
    }
  }

  function renderList() {
    const list = $("feed-list");
    let html;
    if (state.items.length) {
      html = state.items.map(rowMarkup).join("");
    } else if (state.loading) {
      html = skeletonMarkup();
    } else if (state.error) {
      html = `<div class="error-card" role="alert">${escapeHtml(state.error)}</div>`;
    } else {
      html = emptyMarkup();
    }
    if (html !== state.renderedListHtml || !list.firstElementChild) {
      list.innerHTML = html;
      state.renderedListHtml = html;
      observeMedia(list);
    }
    $("feed-more").hidden = !(state.loading && state.items.length);
    const status = $("feed-status");
    status.textContent = !state.loading && !state.hasMore && state.items.length
      ? `That's everything · ${formatCount(state.items.length)} ${state.items.length === 1 ? "item" : "items"}`
      : "";
    $("play-feed").disabled = !state.items.length;
    syncReel();
  }

  function renderNotice() {
    const notice = $("feed-notice");
    let text = "";
    if (state.query) {
      text = `Results for “${escapeHtml(state.query)}” across your whole Page.`;
    } else if (state.range === "7" || state.range === "30") {
      text = `Showing the last ${state.range} days.`;
    } else if (state.range === "custom" && (state.dateFrom || state.dateTo)) {
      text = `Showing ${escapeHtml(state.dateFrom || "the beginning")} to ${escapeHtml(state.dateTo || "today")}.`;
    } else if (state.range === "default" && state.windowDays > 0) {
      text = `Showing the last ${state.windowDays} day${state.windowDays === 1 ? "" : "s"}.`;
    }
    const action = state.query
      ? '<button class="link-button" type="button" data-action="clear-search">Clear search</button>'
      : '<button class="link-button" type="button" data-action="show-all">Show everything</button>';
    setHtml(notice, text ? `<p class="feed-notice">${text} ${action}</p>` : "");
  }

  function renderSummary() {
    const summary = state.summary;
    if (!summary) {
      return;
    }
    $("stat-total").textContent = formatCount(summary.total);
    $("stat-favourites").textContent = formatCount(summary.favourites);
    $("stat-sources").textContent = formatCount(summary.sources);
    setHtml($("hero-cover"), coverMarkup(summary.cover));
    setHtml($("top-tags"), tagChipsMarkup(summary.top_tags));
    setHtml($("latest-favourite"), favouriteCardMarkup(summary.latest_favourite));
  }

  function renderTabs() {
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.getAttribute("data-view") === state.view;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    document.querySelectorAll("[data-tab]").forEach((button) => {
      const active = button.getAttribute("data-tab") === state.tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function renderFilters() {
    document.querySelectorAll("[data-order]").forEach((button) => {
      const active = button.getAttribute("data-order") === state.order;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const defaultChip = document.querySelector('[data-range="default"]');
    const allChip = document.querySelector('[data-range="all"]');
    if (defaultChip) {
      defaultChip.textContent = state.windowDays > 0 ? `Last ${state.windowDays} days (default)` : "Entire feed";
    }
    if (allChip) {
      allChip.hidden = state.windowDays === 0;
    }
    document.querySelectorAll("[data-range]").forEach((button) => {
      const active = button.getAttribute("data-range") === state.range;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    $("custom-range").hidden = state.range !== "custom";
    $("date-from").value = state.dateFrom;
    $("date-to").value = state.dateTo;
    if (document.activeElement !== $("sites-input")) {
      $("sites-input").value = state.sites;
    }
  }

  function renderAll() {
    renderTabs();
    renderFilters();
    renderNotice();
    renderList();
    renderSummary();
    syncSearchUi();
  }

  function setBusy(busy) {
    $("feed-list").setAttribute("aria-busy", busy ? "true" : "false");
  }

  function setLive(online, detail = "") {
    const pill = $("live-pill");
    pill.dataset.state = online ? "live" : "offline";
    $("live-label").textContent = online ? "Live" : "Offline";
    pill.title = detail || (online ? "Connected to your AutoYou server" : "Your AutoYou server can't be reached");
  }

  function toast(message) {
    const element = $("toast");
    element.textContent = message;
    element.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      element.hidden = true;
    }, 3200);
  }

  // ── Media in the list ──────────────────────────

  function observeMedia(root) {
    const videos = root.querySelectorAll("video[data-src]");
    if (!videos.length) {
      return;
    }
    if (typeof IntersectionObserver !== "function") {
      videos.forEach(loadVideoThumb);
      return;
    }
    if (!mediaObserver) {
      mediaObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            mediaObserver.unobserve(entry.target);
            loadVideoThumb(entry.target);
          }
        });
      }, { rootMargin: "200px 0px" });
    }
    videos.forEach((video) => mediaObserver.observe(video));
  }

  // The first frame of a saved video is its thumbnail.
  function loadVideoThumb(video) {
    const source = video.getAttribute("data-src");
    if (!source) {
      return;
    }
    video.removeAttribute("data-src");
    video.preload = "metadata";
    video.src = source;
  }

  function replaceBrokenThumb(image) {
    const thumb = image.closest(".feed-thumb");
    if (!thumb || thumb.classList.contains("tile")) {
      image.remove();
      return;
    }
    thumb.classList.add("tile", "accent-slate");
    image.replaceWith(Object.assign(document.createElement("i"), { className: "ic ic-image tile-icon" }));
  }

  // ── Loading ────────────────────────────────────

  async function readJson(response) {
    try {
      return await response.json();
    } catch (_) {
      return {};
    }
  }

  function rangeDates() {
    if (state.range === "all") {
      return { all: true };
    }
    if (state.range === "7" || state.range === "30") {
      return { from: localIso(new Date(Date.now() - Number(state.range) * 86400000)) };
    }
    if (state.range === "custom") {
      return {
        from: state.dateFrom ? `${state.dateFrom}T00:00:00` : "",
        to: state.dateTo ? `${state.dateTo}T23:59:59` : "",
      };
    }
    return {};
  }

  function feedParams(cursor) {
    const params = new URLSearchParams();
    params.set("limit", String(PAGE_SIZE));
    if (state.view !== "feed") params.set("view", state.view);
    if (state.tab !== "latest") params.set("tab", state.tab);
    if (state.query) params.set("q", state.query);
    if (state.order === "asc") params.set("order", "asc");
    const sites = state.sites.split(",").map((site) => site.trim()).filter(Boolean);
    if (sites.length && state.view !== "uploads") params.set("sources", sites.join(","));
    const range = rangeDates();
    if (range.all) params.set("all", "1");
    if (range.from) params.set("date_from", range.from);
    if (range.to) params.set("date_to", range.to);
    if (cursor) params.set("cursor", cursor);
    return params;
  }

  function mergeItems(existing, incoming) {
    const seen = new Set(existing.map((item) => Number(item.id)));
    return existing.concat(incoming.filter((item) => !seen.has(Number(item.id))));
  }

  async function loadPage({ reset = false, quiet = false } = {}) {
    if (!reset && (state.loading || !state.hasMore)) {
      return;
    }
    const token = ++state.requestToken;
    state.loading = true;
    if (reset) {
      state.error = "";
      if (state.items.length) {
        setBusy(!quiet);
      } else {
        renderList();
      }
    } else {
      renderList();
    }
    try {
      const response = await fetch(`./api/feed/page?${feedParams(reset ? null : state.nextCursor)}`, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const payload = await readJson(response);
      if (token !== state.requestToken) {
        return;
      }
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }
      const incoming = Array.isArray(payload.items) ? payload.items : [];
      state.items = reset ? incoming : mergeItems(state.items, incoming);
      state.hasMore = !!payload.has_more;
      state.nextCursor = payload.next_cursor || null;
      state.windowDays = Number(payload.window_days) || 0;
      state.error = "";
      setLive(true);
    } catch (error) {
      if (token !== state.requestToken) {
        return;
      }
      const message = error instanceof TypeError ? "Your AutoYou server can't be reached." : (error.message || String(error));
      if (state.items.length && reset) {
        toast(`Couldn't refresh. ${message}`);
      } else {
        state.error = `Couldn't load your Page. ${message}`;
      }
      if (error instanceof TypeError) {
        setLive(false, message);
      }
    } finally {
      if (token === state.requestToken) {
        state.loading = false;
        setBusy(false);
        renderList();
        renderNotice();
        renderFilters();
        window.requestAnimationFrame(fillViewport);
      }
    }
  }

  // Keep loading while the end of the list is already on screen.
  function fillViewport() {
    const sentinel = $("feed-sentinel");
    if (!state.hasMore || state.loading || !sentinel) {
      return;
    }
    if (sentinel.getBoundingClientRect().top < window.innerHeight + 400) {
      void loadPage();
    }
  }

  async function refreshSummary() {
    try {
      const response = await fetch("./api/feed/summary", { cache: "no-store", headers: { Accept: "application/json" } });
      const payload = await readJson(response);
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }
      const previousLatest = state.summary ? state.summary.latest_id : null;
      state.summary = payload;
      state.windowDays = Number(payload.window_days) || 0;
      renderSummary();
      renderFilters();
      setLive(true);
      return previousLatest !== payload.latest_id;
    } catch (error) {
      setLive(false, error instanceof TypeError ? "Your AutoYou server can't be reached." : "");
      return false;
    }
  }

  function reloadFeed(options = {}) {
    syncUrl();
    renderTabs();
    renderNotice();
    void loadPage({ reset: true, ...options });
  }

  // ── Filters and URL ────────────────────────────

  function syncUrl() {
    const params = new URLSearchParams();
    if (state.view !== "feed") params.set("view", state.view);
    if (state.tab !== "latest") params.set("tab", state.tab);
    if (state.query) params.set("q", state.query);
    if (state.order === "asc") params.set("order", "asc");
    if (state.range !== "default") params.set("range", state.range);
    if (state.range === "custom" && state.dateFrom) params.set("from", state.dateFrom);
    if (state.range === "custom" && state.dateTo) params.set("to", state.dateTo);
    if (state.sites.trim()) params.set("sites", state.sites.trim());
    const query = params.toString();
    try {
      window.history.replaceState(window.history.state, "", `${query ? `?${query}` : window.location.pathname}${window.location.hash}`);
    } catch (_) {}
  }

  function restoreFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const pick = (key, allowed, fallback) => (allowed.includes(params.get(key)) ? params.get(key) : fallback);
    state.view = pick("view", ["feed", "favourites", "uploads"], "feed");
    state.tab = pick("tab", Object.keys(boot.type_tabs || { latest: [] }), "latest");
    state.order = pick("order", ["asc", "desc"], "desc");
    state.range = pick("range", ["default", "all", "7", "30", "custom"], "default");
    state.query = String(params.get("q") || params.get("tag_search") || "").trim().slice(0, 200);
    const dateOnly = (value) => (/^\d{4}-\d{2}-\d{2}$/.test(String(value || "")) ? value : "");
    state.dateFrom = dateOnly(params.get("from"));
    state.dateTo = dateOnly(params.get("to"));
    state.sites = String(params.get("sites") || "").slice(0, 400);
    return filtersActive() || state.order !== "desc";
  }

  function resetFilters() {
    Object.assign(state, DEFAULT_FILTERS);
    $("search-input").value = "";
  }

  function syncSearchUi() {
    const input = $("search-input");
    if (document.activeElement !== input) {
      input.value = state.query;
    }
    $("search-clear").hidden = !input.value;
    if (state.query) {
      $("search-panel").hidden = false;
      $("search-toggle").setAttribute("aria-expanded", "true");
    }
  }

  // ── Menus ──────────────────────────────────────

  function setMenuOpen(open, { focus = false } = {}) {
    const menu = $("page-menu");
    menu.hidden = !open;
    $("page-menu-toggle").setAttribute("aria-expanded", String(open));
    if (open && focus) {
      menu.querySelector(".menu-item:not([hidden])")?.focus();
    }
  }

  function menuOpen() {
    return !$("page-menu").hidden;
  }

  function syncThemeMenu() {
    const dark = currentTheme() === "dark";
    $("menu-theme-label").textContent = dark ? "Light mode" : "Dark mode";
    const themeIcon = $("menu-theme").querySelector(".ic");
    if (themeIcon) {
      themeIcon.className = `ic ic-${dark ? "sun" : "moon"}`;
    }
  }

  // ── Sheets and dialogs ─────────────────────────

  function openSheets() {
    return Array.from(document.querySelectorAll(".sheet-shell.open, .confirm-shell.open"));
  }

  function syncModalState() {
    const layers = [];
    if (reel.open) layers.push($("reel"));
    document.querySelectorAll(".sheet-shell.open").forEach((shell) => layers.push(shell));
    if (photo.open) layers.push($("photo-viewer"));
    if ($("confirm-dialog").classList.contains("open")) layers.push($("confirm-dialog"));
    const top = layers[layers.length - 1] || null;
    document.body.classList.toggle("modal-open", !!top);
    document.querySelector("main.app").inert = !!top;
    $("fab-add").inert = !!top;
    [$("reel"), $("photo-viewer"), $("confirm-dialog"), ...document.querySelectorAll(".sheet-shell")].forEach((layer) => {
      layer.inert = !!top && layer !== top && layers.includes(layer);
    });
  }

  function openSheet(id, trigger) {
    const shell = $(id);
    if (!shell) {
      return;
    }
    state.lastTrigger = trigger || document.activeElement;
    setMenuOpen(false);
    shell.classList.add("open");
    shell.setAttribute("aria-hidden", "false");
    syncModalState();
    window.setTimeout(() => {
      const focusTarget = shell.querySelector("[data-autofocus]") || shell.querySelector(".sheet [data-close-sheet]");
      focusTarget?.focus();
    }, 30);
  }

  function closeSheet(id, { restoreFocus = true } = {}) {
    const shell = $(id);
    if (!shell || !shell.classList.contains("open")) {
      return;
    }
    shell.classList.remove("open");
    shell.setAttribute("aria-hidden", "true");
    if (id === "item-sheet") {
      stopMedia(shell);
      $("item-content").innerHTML = "";
      state.activeItemId = null;
      document.querySelectorAll(".feed-row.is-active").forEach((row) => row.classList.remove("is-active"));
      state.renderedListHtml = null;
      state.sheetMedia = true;
    }
    syncModalState();
    if (id === "item-sheet") {
      playCurrent();
    }
    if (restoreFocus) {
      let target = state.lastTrigger;
      if (target && !target.isConnected && target.dataset && target.dataset.itemId) {
        target = document.querySelector(`[data-action="open"][data-item-id="${target.dataset.itemId}"]`);
      }
      target?.focus?.();
    }
  }

  function stopMedia(root) {
    root.querySelectorAll("audio, video").forEach((media) => {
      try {
        media.pause();
      } catch (_) {}
    });
  }

  function openConfirm({ title, message, confirmLabel = "Delete", onConfirm }) {
    $("confirm-title").textContent = title;
    $("confirm-message").textContent = message;
    $("confirm-ok").textContent = confirmLabel;
    $("confirm-ok").disabled = false;
    $("confirm-error").hidden = true;
    state.confirmAction = onConfirm;
    const shell = $("confirm-dialog");
    shell.classList.add("open");
    shell.setAttribute("aria-hidden", "false");
    syncModalState();
    window.setTimeout(() => $("confirm-ok").focus(), 30);
  }

  function closeConfirm() {
    const shell = $("confirm-dialog");
    shell.classList.remove("open");
    shell.setAttribute("aria-hidden", "true");
    state.confirmAction = null;
    syncModalState();
  }

  async function runConfirm() {
    const action = state.confirmAction;
    const button = $("confirm-ok");
    if (!action || button.disabled) {
      return;
    }
    button.disabled = true;
    const error = await action();
    if (error) {
      $("confirm-error").textContent = error;
      $("confirm-error").hidden = false;
      button.disabled = false;
      return;
    }
    closeConfirm();
  }

  // ── Item sheet ─────────────────────────────────

  function findItem(id) {
    const numeric = Number(id);
    const inList = state.items.find((item) => Number(item.id) === numeric);
    if (inList) {
      return inList;
    }
    const favourite = state.summary && state.summary.latest_favourite;
    return favourite && Number(favourite.id) === numeric ? favourite : null;
  }

  function activeItem() {
    return state.activeItemId === null ? null : findItem(state.activeItemId);
  }

  function embedFrame(src, variant, title, height) {
    const style = height ? ` style="--frame-height: ${Number(height)}px"` : "";
    return `<div class="media-stage"><div class="media-frame ${variant}"${style}>`
      + `<iframe src="${escapeHtml(src)}" title="${escapeHtml(title)}" loading="lazy" allowfullscreen `
      + 'allow="autoplay; encrypted-media; picture-in-picture; fullscreen; clipboard-write" '
      + 'referrerpolicy="strict-origin-when-cross-origin" '
      + 'sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-presentation"></iframe>'
      + "</div></div>";
  }

  function linkCardMarkup(item) {
    const view = item.view || {};
    const address = safeUrl(item.url);
    let shown = address;
    try {
      const parsed = new URL(address);
      shown = `${parsed.hostname.replace(/^www\./, "")}${parsed.pathname === "/" ? "" : parsed.pathname}`;
    } catch (_) {}
    return `<div class="media-stage"><div class="link-card">${thumbMarkup(view)}`
      + `<span class="link-card-copy"><span class="link-card-host">${escapeHtml(view.host)}</span>`
      + `<span class="link-card-url">${escapeHtml(shown || view.kind)}</span></span></div></div>`;
  }

  function mediaMarkup(item) {
    const view = item.view || {};
    const type = canonicalType(item.type);
    const open = safeUrl(view.open_url);
    const title = view.title || item.title || "Saved item";
    if (type === "youtube" && youtubeId(item.url)) {
      return embedFrame(`https://www.youtube-nocookie.com/embed/${youtubeId(item.url)}?rel=0&playsinline=1`, "ratio-video", title);
    }
    if (type === "twitter" && twitterId(item.url)) {
      return embedFrame(`https://platform.twitter.com/embed/Tweet.html?id=${twitterId(item.url)}&dnt=true&theme=${currentTheme()}&conversation=none`, "social", title, 560);
    }
    if (type === "instagram" && instagramEmbed(item.url)) {
      return embedFrame(instagramEmbed(item.url), "social", title, 640);
    }
    if (type === "tiktok" && tiktokId(item.url)) {
      return embedFrame(`https://www.tiktok.com/embed/v2/${tiktokId(item.url)}`, "social", title, 740);
    }
    if (type === "image" || type === "gif") {
      const sources = photoSources(item);
      if (sources.preview) {
        return '<div class="media-stage photo"><button class="photo-open" type="button" data-action="view-photo" aria-label="View the full photo">'
          + `<img src="${escapeHtml(sources.preview)}" data-full="${escapeHtml(sources.full)}" alt="${escapeHtml(title)}" decoding="async" referrerpolicy="no-referrer">`
          + `<span class="photo-open-hint" aria-hidden="true">${icon("expand")}</span></button></div>`;
      }
    }
    if (type === "video" && open) {
      if (view.uploaded) {
        return `<div class="media-stage"><video controls playsinline preload="metadata" src="${escapeHtml(open)}"></video></div>`;
      }
      return `<div class="media-stage" data-remote-video="${escapeHtml(open)}"><p class="media-note">Preparing video...</p></div>`;
    }
    if (type === "audio" && open) {
      return `<div class="media-stage audio"><audio controls preload="metadata" src="${escapeHtml(open)}"></audio></div>`;
    }
    return linkCardMarkup(item);
  }

  function actionsMarkup(item) {
    const view = item.view || {};
    const type = canonicalType(item.type);
    const open = safeUrl(view.open_url);
    const buttons = [];
    if (open) {
      const label = view.uploaded ? "Open file" : type === "article" ? "Open website" : "Open original";
      buttons.push(`<a class="button primary" href="${escapeHtml(open)}" target="_blank" rel="noopener">${icon("external")}${label}</a>`);
    }
    if (view.uploaded && open) {
      buttons.push(`<a class="button subtle" href="${escapeHtml(open)}" download>${icon("download")}Download</a>`);
    } else if (safeUrl(item.url)) {
      buttons.push(`<button class="button subtle" type="button" data-action="copy-link">${icon("copy")}Copy link</button>`);
    }
    return buttons.length ? `<div class="action-row">${buttons.join("")}</div>` : "";
  }

  function tagsMarkup(item) {
    const tags = Array.isArray(item.tags) ? item.tags : [];
    if (!tags.length && !access.can_edit) {
      return "";
    }
    const pills = tags.map((tag) => {
      const remove = access.can_delete
        ? `<button type="button" data-action="remove-tag" data-tag="${escapeHtml(tag)}" aria-label="Remove tag ${escapeHtml(tag)}">${icon("close")}</button>`
        : "";
      return `<span class="tag-pill">#${escapeHtml(tag)}${remove}</span>`;
    }).join("");
    const form = access.can_edit
      ? '<form class="tag-form" data-form="add-tag"><label class="sr-only" for="tag-input">Add a tag</label>'
        + '<input id="tag-input" class="text-input" type="text" maxlength="60" placeholder="Add a tag" autocomplete="off" enterkeyhint="done">'
        + '<button class="button subtle compact" type="submit">Add</button></form>'
      : "";
    return `<section class="tag-editor" aria-label="Tags"><p class="section-label">Tags</p>`
      + `<div class="tag-list">${pills || '<span class="field-help">No tags yet.</span>'}</div>${form}</section>`;
  }

  function infoMarkup(item) {
    const view = item.view || {};
    const rows = [
      ["Saved", formatFull(item.added_at)],
      ["Type", view.kind],
      ["Source", view.host],
    ];
    if (Number(view.size) > 0) {
      rows.push(["Size", formatBytes(view.size)]);
    }
    return `<dl class="detail-info">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
  }

  function renderItemSheet(item) {
    const view = item.view || {};
    $("item-kicker").innerHTML = `<span class="kicker-tile accent-${safeAccent(view.accent)}">${icon(safeIcon(view.icon))}</span>`
      + `<span class="kicker-text">${escapeHtml([...byline(view), relativeDay(item.added_at)].filter(Boolean).join(" · "))}</span>`;
    $("item-title").textContent = view.title || item.title || "Saved item";
    $("item-rename-form").hidden = true;
    $("item-rename").hidden = false;
    syncFavouriteButton(item);
    const content = $("item-content");
    stopMedia(content);
    // Opened from the full-screen feed, the sheet leaves the media to the feed.
    content.innerHTML = (state.sheetMedia ? mediaMarkup(item) : "") + actionsMarkup(item) + tagsMarkup(item) + infoMarkup(item);
    content.scrollTop = 0;
    upgradePhoto(content.querySelector(".media-stage.photo img"));
    void activateRemoteVideo(content);
  }

  function syncFavouriteButton(item) {
    const button = $("item-favourite");
    const favourite = !!(item && item.favourite);
    button.setAttribute("aria-pressed", String(favourite));
    button.setAttribute("aria-label", favourite ? "Remove from favourites" : "Add to favourites");
    button.innerHTML = icon(favourite ? "heart-fill" : "heart");
  }

  async function activateRemoteVideo(root) {
    const stage = root.querySelector("[data-remote-video]");
    if (!stage) {
      return;
    }
    const original = stage.getAttribute("data-remote-video");
    let source = "";
    try {
      const response = await fetch(`./api/media/resolve?url=${encodeURIComponent(original)}`, { cache: "no-store" });
      const payload = await readJson(response);
      if (response.ok && payload.ok && payload.url) {
        source = `./api/media/stream?url=${encodeURIComponent(payload.url)}`;
      }
    } catch (_) {}
    if (!stage.isConnected) {
      return;
    }
    stage.removeAttribute("data-remote-video");
    stage.innerHTML = source
      ? `<video controls playsinline preload="metadata" src="${escapeHtml(source)}"></video>`
      : '<p class="media-note">This video can\'t play here. Open the original instead.</p>';
  }

  function openItem(id, trigger, { withMedia = true } = {}) {
    const item = findItem(id);
    if (!item) {
      return;
    }
    state.activeItemId = Number(item.id);
    state.sheetMedia = withMedia;
    renderItemSheet(item);
    document.querySelectorAll(".feed-row").forEach((row) => {
      row.classList.toggle("is-active", Number(row.dataset.itemId) === state.activeItemId);
    });
    openSheet("item-sheet", trigger);
  }

  function updateItem(id, changes) {
    const numeric = Number(id);
    const apply = (item) => (item && Number(item.id) === numeric ? Object.assign(item, changes) : item);
    state.items.forEach(apply);
    if (state.summary) {
      apply(state.summary.latest_favourite);
    }
    state.renderedListHtml = null;
    renderList();
    refreshReelSlide(numeric);
  }

  async function sendJson(url, method, body) {
    try {
      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
      });
      const payload = await readJson(response);
      if (!response.ok) {
        return { ok: false, status: response.status, error: payload.error || payload.detail || `HTTP ${response.status}` };
      }
      if (payload.ok === false || payload.success === false) {
        return { ok: false, status: response.status, error: payload.error || "The change wasn't saved." };
      }
      return { ok: true, payload };
    } catch (error) {
      return { ok: false, status: 0, error: "Your AutoYou server can't be reached." };
    }
  }

  // A favourite changes at once and changes back if the server refuses.
  async function setFavourite(item, next, { announce = false } = {}) {
    if (!item || !access.can_edit || !!item.favourite === next) {
      return;
    }
    const id = Number(item.id);
    updateItem(id, { favourite: next });
    syncActiveFavourite(id);
    const result = await sendJson(`./api/item/${id}/favourite`, "POST", { favourite: next });
    if (!result.ok) {
      updateItem(id, { favourite: !next });
      syncActiveFavourite(id);
      toast(result.error);
      return;
    }
    if (announce) {
      toast(next ? "Added to favourites" : "Removed from favourites");
    }
    void refreshSummary();
  }

  function syncActiveFavourite(id) {
    if (Number(state.activeItemId) === Number(id)) {
      syncFavouriteButton(findItem(id));
    }
  }

  async function toggleFavourite() {
    const item = activeItem();
    if (item) {
      await setFavourite(item, !item.favourite, { announce: true });
    }
  }

  function startRename() {
    const item = activeItem();
    if (!item || !access.can_edit) {
      return;
    }
    $("item-rename-input").value = String(item.title || "").trim() || (item.view && item.view.title) || "";
    $("item-rename-form").hidden = false;
    $("item-rename").hidden = true;
    $("item-rename-input").focus();
    $("item-rename-input").select();
  }

  function cancelRename() {
    $("item-rename-form").hidden = true;
    $("item-rename").hidden = false;
  }

  async function saveRename(event) {
    event.preventDefault();
    const item = activeItem();
    const title = $("item-rename-input").value.trim();
    if (!item || !title) {
      return;
    }
    const result = await sendJson(`./api/item/${Number(item.id)}/title`, "POST", { title });
    if (!result.ok) {
      toast(result.error);
      return;
    }
    updateItem(item.id, { title, view: Object.assign({}, item.view, { title }) });
    $("item-title").textContent = title;
    cancelRename();
    toast("Title saved");
  }

  async function addTag(form) {
    const item = activeItem();
    const input = form.querySelector("input");
    const tag = String(input.value || "").trim().replace(/^#+/, "");
    if (!item || !tag || !access.can_edit) {
      return;
    }
    const result = await sendJson(`./api/item/${Number(item.id)}/tags`, "POST", { tag });
    if (!result.ok) {
      toast(result.error);
      return;
    }
    const tags = Array.from(new Set([...(item.tags || []), tag])).sort((a, b) => a.localeCompare(b));
    updateItem(item.id, { tags });
    renderItemSheet(item);
    $("tag-input")?.focus();
    void refreshSummary();
  }

  async function removeTag(tag) {
    const item = activeItem();
    if (!item || !access.can_delete) {
      return;
    }
    const result = await sendJson(`./api/item/${Number(item.id)}/tags/${encodeURIComponent(tag)}`, "DELETE");
    if (!result.ok) {
      toast(result.error);
      return;
    }
    updateItem(item.id, { tags: (item.tags || []).filter((entry) => entry !== tag) });
    renderItemSheet(item);
    void refreshSummary();
  }

  async function copyLink() {
    const item = activeItem();
    const link = item ? safeUrl(item.url) : "";
    if (link) {
      await copyText(link, "Link copied");
    }
  }

  async function deleteItemRequest(id) {
    // Tunnels that drop DELETE bodies still accept the POST alias.
    let result = await sendJson(`./api/feed/${Number(id)}/delete`, "POST");
    if (!result.ok && [404, 405, 501].includes(result.status)) {
      result = await sendJson(`./api/feed/${Number(id)}`, "DELETE");
    }
    return result;
  }

  function confirmDeleteItem() {
    const item = activeItem();
    if (!item || !access.can_delete) {
      return;
    }
    const title = (item.view && item.view.title) || item.title || "this item";
    openConfirm({
      title: `Delete “${title}”?`,
      message: item.view && item.view.uploaded
        ? "The item and its uploaded file will be removed. This cannot be undone."
        : "The item will be removed from your Page. This cannot be undone.",
      onConfirm: async () => {
        const result = await deleteItemRequest(item.id);
        if (!result.ok) {
          return result.error;
        }
        const numeric = Number(item.id);
        state.items = state.items.filter((entry) => Number(entry.id) !== numeric);
        if (state.summary) {
          state.summary.total = Math.max(0, (Number(state.summary.total) || 1) - 1);
          if (item.favourite) {
            state.summary.favourites = Math.max(0, (Number(state.summary.favourites) || 1) - 1);
          }
          if (state.summary.latest_favourite && Number(state.summary.latest_favourite.id) === numeric) {
            state.summary.latest_favourite = null;
          }
        }
        closeSheet("item-sheet", { restoreFocus: false });
        renderList();
        renderSummary();
        toast("Deleted");
        void refreshSummary();
        // Scrolling loads more; only fetch now if the end of the list is on screen.
        window.requestAnimationFrame(fillViewport);
        return "";
      },
    });
  }

  function confirmDeleteAll() {
    if (!access.can_manage) {
      return;
    }
    setMenuOpen(false);
    openConfirm({
      title: "Delete everything on your Page?",
      message: "Every saved item and uploaded file will be removed. This cannot be undone.",
      confirmLabel: "Delete everything",
      onConfirm: async () => {
        const result = await sendJson("./api/feed/clear", "GET");
        if (!result.ok) {
          return result.error;
        }
        state.items = [];
        state.hasMore = false;
        state.nextCursor = null;
        renderList();
        void refreshSummary();
        toast("Your Page is empty");
        return "";
      },
    });
  }

  // ── Adding ─────────────────────────────────────

  function showAddError(message) {
    const element = $("add-error");
    element.textContent = message;
    element.hidden = !message;
  }

  function openAdd(trigger) {
    if (!access.can_add) {
      return;
    }
    showAddError("");
    $("add-link-input").setAttribute("data-autofocus", "");
    openSheet("add-sheet", trigger);
  }

  function showNewestItems() {
    resetFilters();
    renderFilters();
    reloadFeed();
    void refreshSummary();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function addLink(event) {
    event.preventDefault();
    const input = $("add-link-input");
    const url = String(input.value || "").trim();
    const parsed = parseUrl(url);
    if (!parsed || !["http:", "https:"].includes(parsed.protocol)) {
      showAddError("Enter a full web address that starts with https://");
      return;
    }
    const submit = $("add-link-submit");
    submit.disabled = true;
    showAddError("");
    const result = await sendJson("./api/feed", "POST", { url, type: classifyUrl(url), title: "" });
    submit.disabled = false;
    if (!result.ok) {
      showAddError(result.error);
      return;
    }
    input.value = "";
    closeSheet("add-sheet", { restoreFocus: false });
    toast("Added to your Page");
    showNewestItems();
  }

  function fileItemType(file) {
    const name = String(file.name || "").toLowerCase();
    const mime = String(file.type || "").toLowerCase();
    if (mime.startsWith("video/") || /\.(mp4|webm|mov|m4v)$/.test(name)) return "video";
    if (mime.startsWith("audio/") || /\.(mp3|wav|m4a|aac|flac|ogg)$/.test(name)) return "audio";
    if (mime === "image/gif" || name.endsWith(".gif")) return "gif";
    if (mime.startsWith("image/") || /\.(png|jpe?g|heic|heif|webp)$/.test(name)) return "image";
    if (/\.(pdf|xls|xlsx|doc|docx|rtf|ppt|pptx)$/.test(name)) return "document";
    if (mime.startsWith("text/") || name.endsWith(".txt")) return "text";
    return "file";
  }

  function uploadWithProgress(file) {
    return new Promise((resolve) => {
      const request = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file);
      request.open("POST", "./api/blob");
      request.responseType = "json";
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          const percent = Math.round((event.loaded / event.total) * 100);
          $("upload-percent").textContent = `${percent}%`;
          $("upload-bar").style.width = `${percent}%`;
        }
      });
      request.addEventListener("load", () => {
        const payload = request.response || {};
        if (request.status >= 200 && request.status < 300 && payload.id) {
          resolve({ ok: true, payload });
        } else {
          resolve({ ok: false, error: payload.error || payload.detail || `Upload failed (HTTP ${request.status}).` });
        }
      });
      request.addEventListener("error", () => resolve({ ok: false, error: "Your AutoYou server can't be reached." }));
      request.send(form);
    });
  }

  async function uploadFile(file) {
    if (!file || state.uploading || !access.can_add) {
      return;
    }
    if (!$("add-sheet").classList.contains("open")) {
      openAdd();
    }
    state.uploading = true;
    showAddError("");
    $("upload-name").textContent = file.name || "Uploading";
    $("upload-percent").textContent = "0%";
    $("upload-bar").style.width = "0%";
    $("upload-progress").hidden = false;
    $("add-upload").disabled = true;
    const upload = await uploadWithProgress(file);
    let result = upload;
    if (upload.ok) {
      const stem = String(file.name || "").replace(/\.[^.]+$/, "").trim();
      result = await sendJson("./api/feed", "POST", {
        url: `blob://${upload.payload.id}`,
        type: upload.payload.render_type || fileItemType(file),
        title: stem || file.name || "Upload",
        source: "Local",
      });
    }
    state.uploading = false;
    $("add-upload").disabled = false;
    $("upload-progress").hidden = true;
    $("upload-input").value = "";
    if (!result.ok) {
      showAddError(result.error);
      return;
    }
    closeSheet("add-sheet", { restoreFocus: false });
    toast("Uploaded to your Page");
    showNewestItems();
  }

  // ── Overlay history ────────────────────────────
  // The feed viewer and the photo viewer add a history entry, so the phone's
  // back gesture closes them instead of leaving the page.

  function pushOverlay(name, hash) {
    try {
      window.history.pushState({ autoyouOverlay: name }, "", `${window.location.pathname}${window.location.search}${hash}`);
      overlayHistory.push(name);
    } catch (_) {}
  }

  function popOverlay(name) {
    if (overlayHistory[overlayHistory.length - 1] !== name) {
      return;
    }
    overlayHistory.pop();
    ignoredPops += 1;
    window.history.back();
  }

  // Runs once closing overlays have finished stepping back through history,
  // so a URL change made afterwards lands on the page's own entry.
  function afterHistorySettles(callback) {
    if (ignoredPops > 0) {
      afterPop.push(callback);
    } else {
      callback();
    }
  }

  function onPopState() {
    if (ignoredPops > 0) {
      ignoredPops -= 1;
      if (!ignoredPops) {
        afterPop.splice(0).forEach((callback) => callback());
      }
      return;
    }
    const name = overlayHistory.pop();
    if (name === "photo") {
      closePhoto({ fromHistory: true });
    } else if (name === "reel") {
      closeReel({ fromHistory: true });
    }
  }

  // ── Photos ─────────────────────────────────────

  // The list shows a light preview; opened photos show the original file.
  function photoSources(item) {
    const view = item.view || {};
    const full = safeUrl(view.open_url);
    const preview = canonicalType(item.type) === "gif" ? full : safeUrl(view.thumb);
    return { preview: preview || full, full: full || preview };
  }

  // Swap in the original once it has loaded; the preview stays until then.
  function upgradePhoto(image) {
    const full = image ? image.getAttribute("data-full") : "";
    if (!full) {
      return;
    }
    image.removeAttribute("data-full");
    if (image.getAttribute("src") === full) {
      return;
    }
    const loader = new Image();
    loader.decoding = "async";
    loader.referrerPolicy = "no-referrer";
    loader.onload = () => {
      if (image.isConnected) {
        image.src = full;
      }
    };
    loader.src = full;
  }

  function openPhoto(item, trigger) {
    const sources = item ? photoSources(item) : {};
    if (!sources.preview) {
      return;
    }
    const image = $("photo-image");
    const title = (item.view && item.view.title) || item.title || "Photo";
    photo.open = true;
    photo.zoomed = false;
    photo.trigger = trigger || document.activeElement;
    image.style.width = "";
    image.alt = title;
    image.src = sources.preview;
    image.setAttribute("data-full", sources.full);
    upgradePhoto(image);
    $("photo-title").textContent = title;
    syncPhotoZoom();
    const shell = $("photo-viewer");
    shell.hidden = false;
    shell.setAttribute("aria-hidden", "false");
    $("photo-scroll").scrollTo(0, 0);
    pauseReelMedia();
    syncModalState();
    pushOverlay("photo", window.location.hash || "");
    $("photo-scroll").focus({ preventScroll: true });
  }

  function closePhoto({ fromHistory = false } = {}) {
    if (!photo.open) {
      return;
    }
    photo.open = false;
    const shell = $("photo-viewer");
    shell.hidden = true;
    shell.setAttribute("aria-hidden", "true");
    $("photo-image").removeAttribute("src");
    syncModalState();
    if (!fromHistory) {
      popOverlay("photo");
    }
    playCurrent();
    photo.trigger?.focus?.({ preventScroll: true });
  }

  function syncPhotoZoom() {
    $("photo-viewer").classList.toggle("is-zoomed", photo.zoomed);
    const button = $("photo-size");
    button.setAttribute("aria-pressed", String(photo.zoomed));
    button.setAttribute("aria-label", photo.zoomed ? "Fit to screen" : "Zoom in");
    button.innerHTML = icon(photo.zoomed ? "shrink" : "expand");
  }

  // Zoom keeps the tapped point under the finger.
  function togglePhotoZoom(event) {
    const scroller = $("photo-scroll");
    const image = $("photo-image");
    const box = image.getBoundingClientRect();
    const tapped = event && Number.isFinite(event.clientX) && event.clientX > 0;
    const pointX = tapped ? Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1) : 0.5;
    const pointY = tapped ? Math.min(Math.max((event.clientY - box.top) / box.height, 0), 1) : 0.5;
    photo.zoomed = !photo.zoomed;
    image.style.width = photo.zoomed ? `${Math.round(Math.max(box.width * 2.5, Math.min(image.naturalWidth, box.width * 4)))}px` : "";
    syncPhotoZoom();
    if (photo.zoomed) {
      scroller.scrollLeft = image.offsetLeft + pointX * image.offsetWidth - scroller.clientWidth / 2;
      scroller.scrollTop = image.offsetTop + pointY * image.offsetHeight - scroller.clientHeight / 2;
    }
  }

  function onPhotoClick(event) {
    if (event.target !== $("photo-image")) {
      return;
    }
    const now = Date.now();
    if (now - photo.lastTap < DOUBLE_TAP_MS) {
      photo.lastTap = 0;
      togglePhotoZoom(event);
      return;
    }
    photo.lastTap = now;
  }

  // ── Full-screen feed ───────────────────────────
  // Opening an item plays the list as a vertical feed, one item per screen.
  // Only the current item and its neighbours hold media, and embeds and
  // videos play on the current item alone.

  function reelTrack() {
    return $("reel-track");
  }

  function reelSlides() {
    return Array.from(reelTrack().querySelectorAll(".reel-slide"));
  }

  function currentSlide() {
    return reel.open ? reelSlides()[reel.index] || null : null;
  }

  function reelKind(item) {
    const view = item.view || {};
    const type = canonicalType(item.type);
    const open = safeUrl(view.open_url);
    if ((type === "image" || type === "gif") && photoSources(item).preview) return "photo";
    if (type === "video" && open) return "video";
    if (type === "youtube" && youtubeId(item.url)) return "youtube";
    if (type === "tiktok" && tiktokId(item.url)) return "tiktok";
    if ((type === "twitter" && twitterId(item.url)) || (type === "instagram" && instagramEmbed(item.url))) return "post";
    if (type === "audio" && open) return "audio";
    if (type === "text" && view.uploaded && open) return "text";
    if (view.uploaded || view.tab === "files") return "file";
    return "link";
  }

  function reelShape(item, kind) {
    if (kind === "tiktok") return "portrait";
    if (kind === "youtube") {
      const url = parseUrl(item.url);
      return url && url.pathname.split("/").filter(Boolean)[0] === "shorts" ? "portrait" : "wide";
    }
    return kind === "post" ? "post" : "wide";
  }

  function reelInfoMarkup(item) {
    const view = item.view || {};
    const meta = [...byline(view), relativeDay(item.added_at)].filter(Boolean).join(" · ");
    const tags = (Array.isArray(item.tags) ? item.tags : []).slice(0, 8)
      .map((tag) => `<button class="reel-tag" type="button" data-reel-tag="${escapeHtml(tag)}">#${escapeHtml(tag)}</button>`)
      .join("");
    return '<div class="reel-info">'
      + `<p class="reel-meta"><span class="reel-kind accent-${safeAccent(view.accent)}">${icon(safeIcon(view.icon))}</span>`
      + `<span>${escapeHtml(meta)}</span></p>`
      + `<h2 class="reel-title">${escapeHtml(view.title || item.title || "Saved item")}</h2>`
      + (tags ? `<div class="reel-tags">${tags}</div>` : "")
      + "</div>";
  }

  function railButton(action, iconName, label, ariaLabel, extra = "") {
    return `<button class="rail-button" type="button" data-reel-action="${action}" aria-label="${escapeHtml(ariaLabel)}"${extra}>`
      + `<span class="rail-icon">${icon(iconName)}</span><span class="rail-label">${escapeHtml(label)}</span></button>`;
  }

  function reelRailMarkup(item) {
    const view = item.view || {};
    const open = safeUrl(view.open_url);
    const buttons = [];
    if (access.can_edit) {
      const favourite = !!item.favourite;
      buttons.push(railButton(
        "favourite",
        favourite ? "heart-fill" : "heart",
        "Favourite",
        favourite ? "Remove from favourites" : "Add to favourites",
        ` aria-pressed="${favourite}"`,
      ));
    }
    buttons.push(railButton("details", "info", "Details", "Details"));
    // Link cards carry their own "Read on" button.
    if (open && (view.uploaded || reelKind(item) !== "link")) {
      const download = view.uploaded ? " download" : ' target="_blank" rel="noopener"';
      buttons.push(`<a class="rail-button" href="${escapeHtml(open)}"${download} aria-label="${view.uploaded ? "Download" : "Open original"}">`
        + `<span class="rail-icon">${icon(view.uploaded ? "download" : "external")}</span>`
        + `<span class="rail-label">${view.uploaded ? "Save" : "Open"}</span></a>`);
    }
    if (!view.uploaded && safeUrl(item.url)) {
      buttons.push(railButton("share", "share", "Share", "Share link"));
    }
    return `<div class="reel-rail">${buttons.join("")}</div>`;
  }

  function reelSlideMarkup(item, index) {
    const view = item.view || {};
    const kind = reelKind(item);
    return `<article class="reel-slide" data-index="${index}" data-item-id="${Number(item.id)}" data-kind="${kind}" `
      + `data-shape="${reelShape(item, kind)}" aria-roledescription="item" `
      + `aria-label="${escapeHtml(view.title || item.title || "Saved item")}" inert>`
      + '<div class="reel-frame"><div class="reel-stage"></div>'
      + `<span class="reel-glyph" aria-hidden="true">${icon("play-fill")}</span>`
      + '<div class="reel-shade" aria-hidden="true"></div>'
      + reelInfoMarkup(item)
      + reelRailMarkup(item)
      + "</div></article>";
  }

  function reelEndMarkup() {
    return '<section class="reel-end" aria-label="End of the list" inert>'
      + `<span class="reel-end-icon" aria-hidden="true">${icon("tick")}</span>`
      + '<p class="reel-end-title">You\'re all caught up</p>'
      + `<p class="reel-end-copy">${escapeHtml(reelEndCopy())}</p>`
      + '<button class="button primary" type="button" data-reel-action="close">Back to the list</button>'
      + "</section>";
  }

  function reelEndCopy() {
    const count = state.items.length;
    return `${formatCount(count)} ${count === 1 ? "item" : "items"} in ${reelContextLabel().toLowerCase()}.`;
  }

  function reelBackdrop(url) {
    return url
      ? `<img class="reel-backdrop" src="${escapeHtml(url)}" alt="" aria-hidden="true" decoding="async" referrerpolicy="no-referrer">`
      : "";
  }

  function reelFrame(src, title) {
    return `<iframe src="${escapeHtml(src)}" title="${escapeHtml(title)}" allowfullscreen `
      + 'allow="autoplay; encrypted-media; picture-in-picture; fullscreen; clipboard-write" '
      + 'referrerpolicy="strict-origin-when-cross-origin" '
      + 'sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-presentation"></iframe>';
  }

  function reelVideoMarkup(src) {
    return `<video class="reel-video" src="${escapeHtml(src)}" playsinline loop muted preload="metadata"></video>`
      + '<input class="reel-progress" type="range" min="0" max="1000" step="1" value="0" aria-label="Video position">';
  }

  function reelOpenButton(view, label) {
    const open = safeUrl(view.open_url);
    return open
      ? `<a class="button primary" href="${escapeHtml(open)}" target="_blank" rel="noopener">${icon("external")}${escapeHtml(label)}</a>`
      : "";
  }

  function reelStageMarkup(item, kind, live) {
    const view = item.view || {};
    const title = view.title || item.title || "Saved item";
    const open = safeUrl(view.open_url);
    if (kind === "photo") {
      const sources = photoSources(item);
      return reelBackdrop(sources.preview)
        + `<img class="reel-photo" src="${escapeHtml(sources.preview)}" data-full="${escapeHtml(sources.full)}" `
        + `alt="${escapeHtml(title)}" decoding="async" referrerpolicy="no-referrer">`;
    }
    if (kind === "video") {
      if (view.uploaded) {
        return reelVideoMarkup(open);
      }
      return live
        ? `<div class="reel-center" data-remote-video="${escapeHtml(open)}"><p class="reel-note">Preparing video...</p></div>`
        : `<div class="reel-center">${thumbMarkup(view)}</div>`;
    }
    if (kind === "youtube") {
      const id = youtubeId(item.url);
      const poster = safeUrl(view.thumb);
      const player = `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&mute=${reel.muted ? 1 : 0}`
        + `&playsinline=1&rel=0&loop=1&playlist=${id}&enablejsapi=1`;
      const inner = live
        ? reelFrame(player, title)
        : (poster ? `<img class="reel-poster" src="${escapeHtml(poster)}" alt="" decoding="async" referrerpolicy="no-referrer">` : "");
      return reelBackdrop(poster) + `<div class="reel-embed">${inner}</div>`;
    }
    if (kind === "tiktok") {
      const player = `https://www.tiktok.com/player/v1/${tiktokId(item.url)}?autoplay=1&loop=1&rel=0&music_info=1&description=1`;
      return `<div class="reel-embed">${live ? reelFrame(player, title) : `<span class="reel-embed-tile">${thumbMarkup(view)}</span>`}</div>`;
    }
    if (kind === "post") {
      const src = canonicalType(item.type) === "twitter"
        ? `https://platform.twitter.com/embed/Tweet.html?id=${twitterId(item.url)}&dnt=true&theme=dark&conversation=none`
        : instagramEmbed(item.url);
      return live
        ? `<div class="reel-post">${reelFrame(src, title)}</div>`
        : `<div class="reel-center">${thumbMarkup(view)}</div>`;
    }
    if (kind === "audio") {
      return '<div class="reel-center"><div class="reel-audio">'
        + `<span class="reel-disc accent-${safeAccent(view.accent)}" aria-hidden="true">${icon("wave")}</span>`
        + `<audio controls preload="metadata" src="${escapeHtml(open)}"></audio></div></div>`;
    }
    if (kind === "text") {
      return '<div class="reel-center"><div class="reel-card reel-textcard">'
        + `<pre class="reel-text" data-text-src="${escapeHtml(open)}">Loading...</pre>`
        + `${reelOpenButton(view, "Open file")}</div></div>`;
    }
    if (kind === "file") {
      const facts = [view.label || view.ext || view.kind, Number(view.size) > 0 ? formatBytes(view.size) : ""].filter(Boolean).join(" · ");
      return `<div class="reel-center"><div class="reel-card">${thumbMarkup(view)}`
        + `<p class="reel-card-title">${escapeHtml(title)}</p>`
        + (facts ? `<p class="reel-card-meta">${escapeHtml(facts)}</p>` : "")
        + `${reelOpenButton(view, "Open file")}</div></div>`;
    }
    return `<div class="reel-center"><div class="reel-card">${thumbMarkup(view)}`
      + `<p class="reel-card-host">${escapeHtml(view.host || view.kind)}</p>`
      + `<p class="reel-card-title">${escapeHtml(title)}</p>`
      + `${reelOpenButton(view, view.host && !view.uploaded ? `Read on ${view.host}` : "Open")}</div></div>`;
  }

  // Embeds and linked videos load on the current item only; everything else
  // is ready on the neighbouring items so a swipe shows it at once.
  const LIVE_ONLY_KINDS = new Set(["youtube", "tiktok", "post"]);

  function mountSlide(slide, live) {
    const stage = slide.querySelector(".reel-stage");
    const item = findItem(slide.dataset.itemId);
    if (!stage || !item) {
      return;
    }
    const kind = slide.dataset.kind;
    const remoteVideo = kind === "video" && !(item.view && item.view.uploaded);
    const mode = live && (LIVE_ONLY_KINDS.has(kind) || remoteVideo) ? "live" : "still";
    if (stage.dataset.mode !== mode) {
      releaseMedia(stage);
      stage.innerHTML = reelStageMarkup(item, kind, mode === "live");
      stage.dataset.mode = mode;
      if (remoteVideo && mode === "live") {
        void resolveReelVideo(slide, stage);
      }
      if (kind === "text") {
        void loadReelText(stage);
      }
    }
    if (live) {
      upgradePhoto(stage.querySelector(".reel-photo"));
    }
  }

  function releaseMedia(root) {
    root.querySelectorAll("video, audio").forEach((media) => {
      try {
        media.pause();
        media.removeAttribute("src");
        media.load();
      } catch (_) {}
    });
  }

  function unmountSlide(slide) {
    const stage = slide.querySelector(".reel-stage");
    if (stage && stage.dataset.mode) {
      releaseMedia(stage);
      stage.innerHTML = "";
      delete stage.dataset.mode;
    }
    slide.classList.remove("is-paused", "is-playing");
    delete slide.dataset.userPaused;
  }

  async function resolveReelVideo(slide, stage) {
    const holder = stage.querySelector("[data-remote-video]");
    if (!holder) {
      return;
    }
    const original = holder.getAttribute("data-remote-video");
    let source = "";
    try {
      const response = await fetch(`./api/media/resolve?url=${encodeURIComponent(original)}`, { cache: "no-store" });
      const payload = await readJson(response);
      if (response.ok && payload.ok && payload.url) {
        source = `./api/media/stream?url=${encodeURIComponent(payload.url)}`;
      }
    } catch (_) {}
    if (!holder.isConnected) {
      return;
    }
    if (!source) {
      const item = findItem(slide.dataset.itemId);
      holder.innerHTML = '<div class="reel-card"><p class="reel-card-meta">This video can\'t play here.</p>'
        + `${item ? reelOpenButton(item.view || {}, "Open original") : ""}</div>`;
      return;
    }
    holder.outerHTML = reelVideoMarkup(source);
    if (slide === currentSlide()) {
      playCurrent();
    }
  }

  // Text files show their opening lines; the full file opens separately.
  async function loadReelText(stage) {
    const block = stage.querySelector("[data-text-src]");
    if (!block) {
      return;
    }
    const source = block.getAttribute("data-text-src");
    block.removeAttribute("data-text-src");
    let text = "";
    try {
      const response = await fetch(source, { headers: { Range: "bytes=0-16383" } });
      if (response.ok) {
        text = await response.text();
      }
    } catch (_) {}
    if (block.isConnected) {
      block.textContent = text.trim() ? text.slice(0, 16000) : "This file can't be previewed here.";
    }
  }

  function reelContextLabel() {
    if (state.query) return "Search results";
    if (state.view === "favourites") return "Favourites";
    if (state.view === "uploads") return "Uploads";
    return TYPE_TAB_LABELS[state.tab] || "Feed";
  }

  function updateReelChrome() {
    const total = state.items.length;
    const atEnd = reel.index >= total;
    $("reel-context").textContent = reelContextLabel();
    $("reel-count").textContent = total && !atEnd
      ? `${formatCount(reel.index + 1)} of ${formatCount(total)}${state.hasMore ? "+" : ""}`
      : "";
    $("reel-prev").disabled = reel.index <= 0;
    $("reel-next").disabled = atEnd || (reel.index >= total - 1 && !reelTrack().querySelector(".reel-end") && !state.hasMore);
    const slide = currentSlide();
    const sound = $("reel-sound");
    sound.hidden = !slide || !["video", "youtube", "audio"].includes(slide.dataset.kind);
    sound.setAttribute("aria-pressed", String(!reel.muted));
    sound.setAttribute("aria-label", reel.muted ? "Turn sound on" : "Turn sound off");
    sound.innerHTML = icon(reel.muted ? "volume-off" : "volume");
  }

  function announceReel() {
    const slide = currentSlide();
    $("reel-announcer").textContent = slide
      ? `${reel.index + 1} of ${state.items.length}: ${slide.getAttribute("aria-label")}`
      : "End of the list";
  }

  function reelCovered() {
    return photo.open || !!document.querySelector(".sheet-shell.open, .confirm-shell.open") || document.visibilityState === "hidden";
  }

  function commandEmbed(slide, command) {
    const frame = slide ? slide.querySelector(".reel-embed iframe") : null;
    if (!frame || !frame.contentWindow) {
      return;
    }
    try {
      if (slide.dataset.kind === "youtube") {
        frame.contentWindow.postMessage(JSON.stringify({ event: "command", func: command, args: [] }), "https://www.youtube-nocookie.com");
      } else if (slide.dataset.kind === "tiktok") {
        const type = { pauseVideo: "pause", playVideo: "play", mute: "mute", unMute: "unMute" }[command];
        frame.contentWindow.postMessage({ "x-tiktok-player": true, type }, "https://www.tiktok.com");
      }
    } catch (_) {}
  }

  // Only the current item plays; a video the viewer paused stays paused.
  function playCurrent() {
    const current = currentSlide();
    const covered = reelCovered();
    reelSlides().forEach((slide) => {
      const active = slide === current && !covered;
      slide.querySelectorAll("video.reel-video").forEach((video) => {
        video.muted = reel.muted;
        if (active && !slide.dataset.userPaused) {
          const attempt = video.play();
          if (attempt && typeof attempt.catch === "function") {
            attempt.catch(() => slide.classList.add("is-paused"));
          }
        } else if (!active) {
          video.pause();
        }
      });
      slide.querySelectorAll("audio").forEach((audio) => {
        if (active && !reel.muted && !slide.dataset.userPaused) {
          audio.play().catch(() => {});
        } else if (!active) {
          audio.pause();
        }
      });
    });
  }

  function pauseReelMedia() {
    const slide = currentSlide();
    if (!slide) {
      return;
    }
    slide.querySelectorAll("video, audio").forEach((media) => media.pause());
    commandEmbed(slide, "pauseVideo");
  }

  function setReelIndex(index) {
    const slides = reelSlides();
    const end = reelTrack().querySelector(".reel-end");
    const next = Math.max(0, Math.min(index, end ? slides.length : slides.length - 1));
    const changed = next !== reel.index;
    reel.index = next;
    slides.forEach((slide, position) => {
      const distance = Math.abs(position - next);
      if (distance === 0) {
        mountSlide(slide, true);
      } else if (distance <= REEL_KEEP) {
        mountSlide(slide, false);
      } else {
        unmountSlide(slide);
      }
      if (distance !== 0) {
        delete slide.dataset.userPaused;
      }
      slide.inert = distance !== 0;
      slide.classList.toggle("is-current", distance === 0);
    });
    if (end) {
      end.inert = next !== slides.length;
    }
    playCurrent();
    updateReelChrome();
    if (changed) {
      syncReelHash();
      announceReel();
      maybeLoadMore();
    }
  }

  function syncReelHash() {
    const slide = currentSlide();
    if (!slide || overlayHistory[overlayHistory.length - 1] !== "reel") {
      return;
    }
    try {
      window.history.replaceState({ autoyouOverlay: "reel" }, "", `${window.location.pathname}${window.location.search}#item-${Number(slide.dataset.itemId)}`);
    } catch (_) {}
  }

  function maybeLoadMore() {
    if (reel.open && state.hasMore && !state.loading && reel.index >= state.items.length - 3) {
      void loadPage();
    }
  }

  function observeReel() {
    const track = reelTrack();
    if (typeof IntersectionObserver !== "function") {
      return;
    }
    if (!reel.observer) {
      reel.observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!reel.open || !entry.isIntersecting || entry.intersectionRatio < 0.6) {
            return;
          }
          const target = entry.target;
          setReelIndex(target.classList.contains("reel-end") ? reelSlides().length : Number(target.dataset.index));
        });
      }, { root: track, threshold: [0.6] });
    }
    track.querySelectorAll(".reel-slide, .reel-end").forEach((node) => reel.observer.observe(node));
  }

  // Without IntersectionObserver the settled scroll position picks the item.
  function onReelScroll() {
    if (typeof IntersectionObserver === "function" || !reel.open) {
      return;
    }
    window.clearTimeout(reel.scrollTimer);
    reel.scrollTimer = window.setTimeout(() => {
      const track = reelTrack();
      setReelIndex(Math.round(track.scrollTop / Math.max(1, track.clientHeight)));
    }, 120);
  }

  function syncReelEnd() {
    const track = reelTrack();
    const end = track.querySelector(".reel-end");
    if (state.hasMore) {
      end?.remove();
    } else if (!end) {
      track.insertAdjacentHTML("beforeend", reelEndMarkup());
    } else {
      end.querySelector(".reel-end-copy").textContent = reelEndCopy();
    }
  }

  function renderReel(index) {
    const track = reelTrack();
    reelSlides().forEach(unmountSlide);
    reel.observer?.disconnect();
    track.innerHTML = state.items.map(reelSlideMarkup).join("");
    syncReelEnd();
    reel.index = -1;
    reel.targetIndex = null;
    const target = reelSlides()[index];
    track.scrollTop = target ? target.offsetTop : 0;
    observeReel();
    setReelIndex(index);
  }

  // Keep the viewer in step with the list: pages loaded while swiping are
  // appended, and a deleted item leaves the viewer on the next one.
  function syncReel() {
    if (!reel.open) {
      return;
    }
    const ids = state.items.map((item) => Number(item.id));
    const shown = reelSlides().map((slide) => Number(slide.dataset.itemId));
    if (!ids.length) {
      closeReel();
      return;
    }
    if (shown.length <= ids.length && shown.every((id, position) => ids[position] === id)) {
      if (ids.length > shown.length) {
        reelTrack().querySelector(".reel-end")?.remove();
        reelTrack().insertAdjacentHTML(
          "beforeend",
          state.items.slice(shown.length).map((item, offset) => reelSlideMarkup(item, shown.length + offset)).join(""),
        );
        syncReelEnd();
        observeReel();
        setReelIndex(reel.index);
      } else {
        syncReelEnd();
        observeReel();
        updateReelChrome();
      }
      return;
    }
    const index = ids.indexOf(shown[reel.index]);
    renderReel(index >= 0 ? index : Math.min(reel.index, ids.length - 1));
  }

  function refreshReelSlide(id) {
    if (!reel.open) {
      return;
    }
    const item = findItem(id);
    const slide = reelTrack().querySelector(`.reel-slide[data-item-id="${Number(id)}"]`);
    if (!item || !slide) {
      return;
    }
    const focused = slide.contains(document.activeElement) ? document.activeElement.getAttribute("data-reel-action") : null;
    const build = (html) => {
      const template = document.createElement("template");
      template.innerHTML = html;
      return template.content.firstElementChild;
    };
    slide.querySelector(".reel-info")?.replaceWith(build(reelInfoMarkup(item)));
    slide.querySelector(".reel-rail")?.replaceWith(build(reelRailMarkup(item)));
    slide.setAttribute("aria-label", (item.view && item.view.title) || item.title || "Saved item");
    if (focused) {
      slide.querySelector(`[data-reel-action="${focused}"]`)?.focus();
    }
  }

  function openReel(id, trigger) {
    const index = state.items.findIndex((item) => Number(item.id) === Number(id));
    if (index < 0) {
      // For example the latest favourite while the list shows other items.
      openItem(id, trigger);
      return;
    }
    setMenuOpen(false);
    reel.open = true;
    reel.trigger = trigger || document.activeElement;
    const avatar = $("reel-avatar");
    if (profile.avatar_url && !avatar.getAttribute("src")) {
      avatar.src = safeUrl(profile.avatar_url);
    }
    avatar.hidden = !profile.avatar_url;
    const shell = $("reel");
    shell.hidden = false;
    shell.setAttribute("aria-hidden", "false");
    syncModalState();
    pushOverlay("reel", `#item-${Number(id)}`);
    renderReel(index);
    reelTrack().focus({ preventScroll: true });
    showReelHint();
  }

  function closeReel({ fromHistory = false } = {}) {
    if (!reel.open) {
      return;
    }
    const last = currentSlide();
    const lastId = last ? Number(last.dataset.itemId) : null;
    window.clearTimeout(reel.tapTimer);
    closePhoto();
    closeSheet("item-sheet", { restoreFocus: false });
    reel.open = false;
    reelSlides().forEach(unmountSlide);
    reel.observer?.disconnect();
    reelTrack().innerHTML = "";
    reel.index = -1;
    const shell = $("reel");
    shell.hidden = true;
    shell.setAttribute("aria-hidden", "true");
    shell.querySelector(".reel-hint")?.remove();
    syncModalState();
    if (!fromHistory) {
      popOverlay("reel");
    }
    // Come back to the item the viewer ended on.
    const row = lastId === null ? null : document.querySelector(`.feed-row[data-item-id="${lastId}"] .feed-item`);
    if (row) {
      row.scrollIntoView({ block: "center" });
      row.focus({ preventScroll: true });
    } else {
      reel.trigger?.focus?.({ preventScroll: true });
    }
  }

  function goReel(step) {
    // Quick repeated presses add up while the previous scroll is still moving.
    goReelTo((reel.targetIndex === null ? reel.index : reel.targetIndex) + step);
  }

  function goReelTo(position) {
    const nodes = Array.from(reelTrack().querySelectorAll(".reel-slide, .reel-end"));
    const index = Math.max(0, Math.min(position, nodes.length - 1));
    const target = nodes[index];
    reel.targetIndex = index;
    window.clearTimeout(reel.targetTimer);
    reel.targetTimer = window.setTimeout(() => {
      reel.targetIndex = null;
    }, 700);
    if (target) {
      const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      reelTrack().scrollTo({ top: target.offsetTop, behavior: smooth ? "smooth" : "auto" });
    }
  }

  function showReelHint() {
    let seen = false;
    try {
      seen = localStorage.getItem("autoyou.page.reelHint") === "1";
      localStorage.setItem("autoyou.page.reelHint", "1");
    } catch (_) {}
    if (seen || state.items.length < 2) {
      return;
    }
    const touch = window.matchMedia("(pointer: coarse)").matches;
    const hint = document.createElement("p");
    hint.className = "reel-hint";
    hint.setAttribute("aria-hidden", "true");
    hint.innerHTML = `${icon("chevron-up")}<span>${touch ? "Swipe up for more" : "Scroll or use the arrow keys"}</span>`;
    $("reel").appendChild(hint);
    window.setTimeout(() => hint.remove(), 3400);
  }

  function toggleSound() {
    reel.muted = !reel.muted;
    const slide = currentSlide();
    if (slide) {
      slide.querySelectorAll("video.reel-video").forEach((video) => {
        video.muted = reel.muted;
      });
      commandEmbed(slide, reel.muted ? "mute" : "unMute");
      const audio = slide.querySelector("audio");
      if (audio && !reel.muted) {
        delete slide.dataset.userPaused;
        audio.play().catch(() => {});
      } else if (audio) {
        audio.pause();
      }
    }
    updateReelChrome();
  }

  function toggleSlideVideo(slide) {
    const video = slide.querySelector("video.reel-video");
    if (!video) {
      return;
    }
    if (video.paused) {
      delete slide.dataset.userPaused;
      video.muted = reel.muted;
      video.play().catch(() => {});
    } else {
      slide.dataset.userPaused = "1";
      video.pause();
    }
  }

  function heartBurst(slide, event) {
    const frame = slide.querySelector(".reel-frame");
    const box = frame.getBoundingClientRect();
    const heart = document.createElement("span");
    heart.className = "heart-burst";
    heart.setAttribute("aria-hidden", "true");
    heart.innerHTML = icon("heart-fill");
    heart.style.left = `${(event.clientX || box.left + box.width / 2) - box.left}px`;
    heart.style.top = `${(event.clientY || box.top + box.height / 2) - box.top}px`;
    frame.appendChild(heart);
    window.setTimeout(() => heart.remove(), 900);
  }

  // A tap plays or pauses a video or opens a photo; a double tap adds the
  // item to favourites for people who can edit the Page.
  function handleStageTap(slide, event) {
    const now = Date.now();
    if (access.can_edit && reel.tapSlide === slide && now - reel.lastTap < DOUBLE_TAP_MS) {
      window.clearTimeout(reel.tapTimer);
      reel.lastTap = 0;
      reel.tapSlide = null;
      heartBurst(slide, event);
      const item = findItem(slide.dataset.itemId);
      if (item && !item.favourite) {
        void setFavourite(item, true);
      }
      return;
    }
    reel.lastTap = now;
    reel.tapSlide = slide;
    window.clearTimeout(reel.tapTimer);
    reel.tapTimer = window.setTimeout(() => {
      reel.tapSlide = null;
      if (!reel.open || slide !== currentSlide()) {
        return;
      }
      if (slide.dataset.kind === "video") {
        toggleSlideVideo(slide);
      } else if (slide.dataset.kind === "photo") {
        openPhoto(findItem(slide.dataset.itemId), slide.querySelector(".reel-rail button, .reel-rail a"));
      }
    }, access.can_edit ? DOUBLE_TAP_MS : 0);
  }

  async function copyText(text, message) {
    try {
      await navigator.clipboard.writeText(text);
      toast(message);
      return;
    } catch (_) {}
    const field = Object.assign(document.createElement("textarea"), { value: text });
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.appendChild(field);
    field.select();
    const copied = document.execCommand && document.execCommand("copy");
    field.remove();
    toast(copied ? message : "Couldn't copy the link");
  }

  async function shareItem(item) {
    const link = safeUrl(item.url);
    if (!link) {
      return;
    }
    if (typeof navigator.share === "function") {
      try {
        await navigator.share({ title: (item.view && item.view.title) || item.title || "", url: link });
        return;
      } catch (error) {
        if (error && error.name === "AbortError") {
          return;
        }
      }
    }
    await copyText(link, "Link copied");
  }

  function filterByTagFromReel(tag) {
    closeReel();
    afterHistorySettles(() => {
      state.query = tag;
      $("search-input").value = tag;
      syncSearchUi();
      renderSummary();
      reloadFeed();
      $("type-tabs").scrollIntoView({ block: "start" });
    });
  }

  function handleReelAction(name, slide, control) {
    const item = slide ? findItem(slide.dataset.itemId) : null;
    if (name === "close") {
      closeReel();
    } else if (name === "favourite" && item) {
      if (!item.favourite) {
        heartBurst(slide, {});
      }
      void setFavourite(item, !item.favourite);
    } else if (name === "details" && item) {
      pauseReelMedia();
      openItem(item.id, control, { withMedia: false });
    } else if (name === "share" && item) {
      void shareItem(item);
    }
  }

  function handleReelKey(event) {
    if (event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    const target = event.target;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLAudioElement) {
      return;
    }
    const key = event.key;
    if (["ArrowDown", "PageDown", "j", "J"].includes(key)) {
      goReel(1);
    } else if (["ArrowUp", "PageUp", "k", "K"].includes(key)) {
      goReel(-1);
    } else if (key === "Home") {
      goReelTo(0);
    } else if (key === "End") {
      goReelTo(Number.MAX_SAFE_INTEGER);
    } else if ((key === " " || key === "Spacebar") && !target.closest?.("button, a")) {
      const slide = currentSlide();
      if (!slide || slide.dataset.kind !== "video") {
        return;
      }
      toggleSlideVideo(slide);
    } else if (key === "m" || key === "M") {
      toggleSound();
    } else {
      return;
    }
    event.preventDefault();
  }

  function setupReel() {
    const track = reelTrack();
    track.addEventListener("scroll", onReelScroll, { passive: true });
    $("reel").addEventListener("click", (event) => {
      const action = event.target.closest("[data-reel-action]");
      const slide = event.target.closest(".reel-slide");
      if (action) {
        handleReelAction(action.getAttribute("data-reel-action"), slide, action);
        return;
      }
      const tag = event.target.closest("[data-reel-tag]");
      if (tag) {
        filterByTagFromReel(tag.getAttribute("data-reel-tag") || "");
        return;
      }
      if (slide && !event.target.closest("a, button, input, audio, iframe")) {
        handleStageTap(slide, event);
      }
    });
    track.addEventListener("play", (event) => {
      const slide = event.target.closest?.(".reel-slide");
      slide?.classList.remove("is-paused");
      slide?.classList.add("is-playing");
    }, true);
    track.addEventListener("pause", (event) => {
      const slide = event.target.closest?.(".reel-slide");
      slide?.classList.remove("is-playing");
      if (slide && slide === currentSlide() && event.target.classList.contains("reel-video")) {
        slide.classList.add("is-paused");
      }
    }, true);
    track.addEventListener("timeupdate", (event) => {
      const video = event.target;
      const bar = video.classList?.contains("reel-video") ? video.parentElement.querySelector(".reel-progress") : null;
      if (bar && Number.isFinite(video.duration) && video.duration > 0 && document.activeElement !== bar) {
        bar.value = String(Math.round((video.currentTime / video.duration) * 1000));
        bar.style.setProperty("--progress", `${(video.currentTime / video.duration) * 100}%`);
      }
    }, true);
    track.addEventListener("input", (event) => {
      const bar = event.target;
      if (!bar.classList?.contains("reel-progress")) {
        return;
      }
      const video = bar.parentElement.querySelector(".reel-video");
      if (video && Number.isFinite(video.duration) && video.duration > 0) {
        video.currentTime = (Number(bar.value) / 1000) * video.duration;
        bar.style.setProperty("--progress", `${Number(bar.value) / 10}%`);
      }
    });
    $("reel-close").addEventListener("click", () => closeReel());
    $("reel-sound").addEventListener("click", toggleSound);
    $("reel-prev").addEventListener("click", () => goReel(-1));
    $("reel-next").addEventListener("click", () => goReel(1));
    $("play-feed").addEventListener("click", (event) => {
      if (state.items.length) {
        openReel(state.items[0].id, event.currentTarget);
      }
    });
    $("photo-close").addEventListener("click", () => closePhoto());
    $("photo-size").addEventListener("click", () => togglePhotoZoom(null));
    $("photo-scroll").addEventListener("click", onPhotoClick);
    window.addEventListener("popstate", onPopState);
  }

  // A Page link ending in #item-<id> opens that item in the viewer.
  function openReelFromHash() {
    const match = /^#item-(\d+)$/.exec(window.location.hash || "");
    if (!match) {
      return;
    }
    try {
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
    } catch (_) {}
    if (state.items.some((item) => Number(item.id) === Number(match[1]))) {
      openReel(match[1], $("play-feed"));
    }
  }

  // ── Embed sizing ───────────────────────────────

  function embedHeight(payload) {
    const heights = [];
    const collect = (value) => {
      const number = Number(value);
      if (Number.isFinite(number) && number >= 120 && number <= 2400) {
        heights.push(number);
      }
    };
    const visit = (value) => {
      if (!value) return;
      if (typeof value === "number" || typeof value === "string") {
        collect(value);
        return;
      }
      if (typeof value !== "object") return;
      collect(value.height);
      collect(value.frameHeight);
      if (value.details) collect(value.details.height);
      if (value.params && Array.isArray(value.params)) value.params.forEach((param) => visit(param));
      if (value["twttr.embed"]) visit(value["twttr.embed"]);
    };
    if (typeof payload === "string" && payload.length <= 2000) {
      try {
        visit(JSON.parse(payload));
      } catch (_) {
        const match = payload.match(/(?:height|frameHeight)["'=:\s]+(\d{3,4})/i);
        if (match) collect(match[1]);
      }
    } else {
      visit(payload);
    }
    return heights.length ? Math.max(...heights) : 0;
  }

  window.addEventListener("message", (event) => {
    document.querySelectorAll("#item-content .media-frame.social iframe, .reel-post iframe").forEach((frame) => {
      if (frame.contentWindow !== event.source) {
        return;
      }
      const height = embedHeight(event.data);
      if (height) {
        frame.parentElement.style.setProperty("--frame-height", `${Math.min(Math.max(height, 240), 1400)}px`);
      }
    });
  });

  // ── Events ─────────────────────────────────────

  function setupEvents() {
    $("search-toggle").addEventListener("click", () => {
      const panel = $("search-panel");
      // An active search keeps its field visible so it can be seen and cleared.
      if (panel.hidden) {
        panel.hidden = false;
      } else if (!state.query) {
        panel.hidden = true;
      }
      $("search-toggle").setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) {
        $("search-input").focus();
      }
    });

    $("search-input").addEventListener("input", (event) => {
      $("search-clear").hidden = !event.target.value;
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.query = String(event.target.value || "").trim().slice(0, 200);
        renderSummary();
        reloadFeed();
      }, SEARCH_DEBOUNCE_MS);
    });

    $("search-clear").addEventListener("click", () => {
      $("search-input").value = "";
      state.query = "";
      $("search-clear").hidden = true;
      $("search-input").focus();
      renderSummary();
      reloadFeed();
    });

    $("top-tags").addEventListener("click", (event) => {
      const chip = event.target.closest("[data-tag]");
      if (!chip) {
        return;
      }
      const tag = chip.getAttribute("data-tag") || "";
      state.query = state.query.toLowerCase() === tag.toLowerCase() ? "" : tag;
      $("search-input").value = state.query;
      syncSearchUi();
      renderSummary();
      reloadFeed();
    });

    document.querySelector(".view-tabs").addEventListener("click", (event) => {
      const tab = event.target.closest("[data-view]");
      if (!tab || tab.getAttribute("data-view") === state.view) {
        return;
      }
      state.view = tab.getAttribute("data-view");
      reloadFeed();
    });

    $("type-tabs").addEventListener("click", (event) => {
      const tab = event.target.closest("[data-tab]");
      if (!tab || tab.getAttribute("data-tab") === state.tab) {
        return;
      }
      state.tab = tab.getAttribute("data-tab");
      tab.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
      reloadFeed();
    });

    document.addEventListener("click", (event) => {
      const opener = event.target.closest('[data-action="open"][data-item-id]');
      if (opener) {
        openReel(opener.getAttribute("data-item-id"), opener);
        return;
      }
      const action = event.target.closest("[data-action]");
      if (action) {
        const name = action.getAttribute("data-action");
        if (name === "show-all") {
          state.range = state.windowDays > 0 ? "all" : "default";
          renderFilters();
          reloadFeed();
        } else if (name === "clear-search") {
          state.query = "";
          $("search-input").value = "";
          syncSearchUi();
          renderSummary();
          reloadFeed();
        } else if (name === "clear-filters") {
          resetFilters();
          renderFilters();
          renderSummary();
          reloadFeed();
        } else if (name === "copy-link") {
          void copyLink();
        } else if (name === "view-photo") {
          openPhoto(activeItem(), action);
        } else if (name === "remove-tag") {
          void removeTag(action.getAttribute("data-tag") || "");
        }
      }
      if (event.target.closest("[data-close-sheet]")) {
        const shell = event.target.closest(".sheet-shell");
        if (shell) {
          closeSheet(shell.id);
        }
      }
      if (event.target.closest("[data-close-confirm]")) {
        closeConfirm();
      }
      if (menuOpen() && !event.target.closest(".menu-wrap")) {
        setMenuOpen(false);
      }
    });

    $("item-content").addEventListener("submit", (event) => {
      const form = event.target.closest('[data-form="add-tag"]');
      if (form) {
        event.preventDefault();
        void addTag(form);
      }
    });

    $("page-menu-toggle").addEventListener("click", () => setMenuOpen(!menuOpen(), { focus: !menuOpen() }));
    $("page-menu").addEventListener("keydown", (event) => {
      const items = Array.from($("page-menu").querySelectorAll(".menu-item")).filter((item) => item.offsetParent !== null);
      const index = items.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        items[(index + step + items.length) % items.length]?.focus();
      } else if (event.key === "Tab") {
        setMenuOpen(false);
      }
    });
    $("menu-refresh").addEventListener("click", () => {
      setMenuOpen(false);
      void refreshSummary();
      reloadFeed();
    });
    $("menu-filters").addEventListener("click", () => openSheet("filters-sheet", $("page-menu-toggle")));
    $("menu-theme").addEventListener("click", async () => {
      setMenuOpen(false);
      const next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      syncThemeMenu();
      try {
        localStorage.setItem("autoyou.ui.theme", next);
      } catch (_) {}
      const result = await sendJson("./api/ui/theme", "POST", { theme: next });
      if (!result.ok) {
        toast("Theme changed on this device only");
      }
    });
    $("all-websites-link").addEventListener("click", () => setMenuOpen(false));
    $("menu-delete-all").addEventListener("click", confirmDeleteAll);

    ["add-open", "fab-add", "link-open"].forEach((id) => {
      $(id).addEventListener("click", (event) => openAdd(event.currentTarget));
    });
    $("upload-open").addEventListener("click", () => $("upload-input").click());
    $("add-upload").addEventListener("click", () => $("upload-input").click());
    $("upload-input").addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      if (file) {
        void uploadFile(file);
      }
    });
    const zone = $("add-upload");
    zone.addEventListener("dragover", (event) => {
      event.preventDefault();
      zone.classList.add("is-dragging");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      zone.classList.remove("is-dragging");
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) {
        void uploadFile(file);
      }
    });
    $("add-link-form").addEventListener("submit", addLink);

    $("item-favourite").addEventListener("click", toggleFavourite);
    $("item-delete").addEventListener("click", confirmDeleteItem);
    $("item-rename").addEventListener("click", startRename);
    $("item-rename-cancel").addEventListener("click", cancelRename);
    $("item-rename-form").addEventListener("submit", saveRename);
    $("confirm-ok").addEventListener("click", runConfirm);

    document.querySelector('#filters-sheet .segmented').addEventListener("click", (event) => {
      const button = event.target.closest("[data-order]");
      if (!button || button.getAttribute("data-order") === state.order) {
        return;
      }
      state.order = button.getAttribute("data-order");
      renderFilters();
      reloadFeed();
    });
    $("range-options").addEventListener("click", (event) => {
      const button = event.target.closest("[data-range]");
      if (!button || button.getAttribute("data-range") === state.range) {
        return;
      }
      state.range = button.getAttribute("data-range");
      renderFilters();
      if (state.range !== "custom" || state.dateFrom || state.dateTo) {
        reloadFeed();
      }
    });
    ["date-from", "date-to"].forEach((id) => {
      $(id).addEventListener("change", () => {
        state.dateFrom = $("date-from").value;
        state.dateTo = $("date-to").value;
        reloadFeed();
      });
    });
    $("sites-input").addEventListener("input", (event) => {
      window.clearTimeout(sitesTimer);
      sitesTimer = window.setTimeout(() => {
        state.sites = String(event.target.value || "").slice(0, 400);
        reloadFeed();
      }, SEARCH_DEBOUNCE_MS);
    });
    $("filters-clear").addEventListener("click", () => {
      state.order = DEFAULT_FILTERS.order;
      state.range = DEFAULT_FILTERS.range;
      state.dateFrom = "";
      state.dateTo = "";
      state.sites = "";
      $("sites-input").value = "";
      renderFilters();
      reloadFeed();
    });

    // A missing favicon falls back to the site's initials; a broken
    // thumbnail becomes a plain tile. Error events do not bubble.
    document.addEventListener("error", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLImageElement)) {
        return;
      }
      if (target.classList.contains("tile-favicon")) {
        target.remove();
      } else if (target.closest(".feed-thumb")) {
        replaceBrokenThumb(target);
      } else if (target.classList.contains("cover-img")) {
        target.replaceWith(Object.assign(document.createElement("span"), { className: "cover-fallback" }));
      } else if (target.classList.contains("avatar-img") && profile.mark_url && !target.src.includes("autoyou-mark.svg")) {
        target.parentElement?.classList.replace("has-photo", "is-mark");
        target.src = safeUrl(profile.mark_url);
      } else if (target.classList.contains("reel-backdrop") || target.classList.contains("reel-poster")) {
        target.remove();
      } else if (target.classList.contains("reel-photo")) {
        target.replaceWith(Object.assign(document.createElement("p"), {
          className: "reel-note",
          textContent: "This photo can't be shown here.",
        }));
      }
    }, true);

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") {
        if (reel.open && !photo.open && !openSheets().length) {
          handleReelKey(event);
        }
        return;
      }
      const shell = document.querySelector(".sheet-shell.open");
      if (menuOpen()) {
        setMenuOpen(false);
        $("page-menu-toggle").focus();
      } else if ($("confirm-dialog").classList.contains("open")) {
        closeConfirm();
      } else if (photo.open) {
        closePhoto();
      } else if (!$("item-rename-form").hidden) {
        cancelRename();
      } else if (shell) {
        closeSheet(shell.id);
      } else if (reel.open) {
        closeReel();
      } else {
        return;
      }
      event.preventDefault();
    });

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        playCurrent();
        void pollSummary();
      } else {
        pauseReelMedia();
      }
    });
    window.addEventListener("online", () => {
      void refreshSummary();
      reloadFeed({ quiet: true });
    });
    window.addEventListener("offline", () => setLive(false, "This device is offline."));

    const syncViewport = () => {
      const height = Math.round(window.visualViewport ? window.visualViewport.height : window.innerHeight);
      document.documentElement.style.setProperty("--viewport-height", `${height}px`);
    };
    window.addEventListener("resize", syncViewport, { passive: true });
    window.visualViewport?.addEventListener("resize", syncViewport, { passive: true });
    syncViewport();
  }

  function setupInfiniteScroll() {
    const sentinel = $("feed-sentinel");
    if (typeof IntersectionObserver !== "function") {
      window.addEventListener("scroll", fillViewport, { passive: true });
      return;
    }
    feedObserver = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        void loadPage();
      }
    }, { rootMargin: "600px 0px" });
    feedObserver.observe(sentinel);
  }

  // New items (for example ones AutoYou saves from Chat) appear on their own
  // while the reader is at the top of the feed.
  async function pollSummary() {
    if (document.visibilityState !== "visible") {
      return;
    }
    const changed = await refreshSummary();
    const idle = !openSheets().length && !reel.open && !photo.open && window.scrollY < 240 && !state.loading;
    if (changed && idle) {
      void loadPage({ reset: true, quiet: true });
    }
  }

  function init() {
    const hasUrlFilters = restoreFromUrl();
    syncThemeMenu();
    setupEvents();
    setupReel();
    setupInfiniteScroll();
    renderAll();
    openReelFromHash();
    if (hasUrlFilters) {
      void loadPage({ reset: true });
    } else {
      window.requestAnimationFrame(fillViewport);
    }
    summaryTimer = window.setInterval(pollSummary, SUMMARY_POLL_MS);
  }

  init();
})();
