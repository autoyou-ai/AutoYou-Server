/**
 * Offline-first interactive Mercator Map View for AutoYou Diary.
 * Zero external CDN dependencies — works seamlessly on localhost, offline & in iOS/Android WebViews.
 */

// Simplified low-poly world landmass rings [lng, lat] for crisp offline Day One-style cartography.
const CONTINENTS = [
  // North America
  [[-168,66],[-162,70],[-140,70],[-125,70],[-110,68],[-95,69],[-80,63],[-64,60],[-55,52],[-60,46],[-67,45],[-70,41],[-75,35],[-81,25],[-83,30],[-90,29],[-97,26],[-97,19],[-88,16],[-83,10],[-79,8],[-84,10],[-92,15],[-105,20],[-112,28],[-117,32],[-122,38],[-124,48],[-135,57],[-150,60],[-162,55],[-166,60],[-168,66]],
  // Greenland
  [[-52,60],[-42,60],[-34,68],[-20,76],[-22,82],[-48,83],[-65,78],[-56,69],[-52,60]],
  // South America
  [[-80,9],[-74,11],[-62,10],[-50,2],[-35,-6],[-38,-14],[-41,-22],[-48,-26],[-54,-34],[-64,-41],[-68,-54],[-74,-52],[-73,-41],[-71,-30],[-71,-18],[-81,-5],[-80,9]],
  // Europe
  [[-9,37],[-9,43],[-2,43],[-5,48],[2,51],[8,54],[10,58],[5,61],[14,68],[28,71],[42,67],[55,68],[60,55],[46,46],[38,45],[29,41],[24,38],[16,40],[12,44],[5,43],[-5,36],[-9,37]],
  // UK & Ireland
  [[-6,50],[-5,55],[-3,58],[0,53],[1,51],[-6,50]],
  // Africa
  [[-17,15],[-13,28],[-5,36],[10,37],[15,32],[25,31],[33,31],[43,12],[51,11],[41,-2],[40,-15],[35,-25],[28,-33],[18,-34],[14,-22],[12,-6],[9,4],[-8,5],[-17,15]],
  // Madagascar
  [[44,-25],[47,-25],[50,-14],[48,-12],[44,-20],[44,-25]],
  // Asia
  [[27,41],[36,37],[35,31],[43,13],[53,16],[60,22],[67,25],[72,20],[77,8],[81,16],[89,22],[98,16],[101,3],[104,1],[109,13],[108,21],[118,24],[122,31],[120,39],[128,35],[130,42],[141,52],[158,52],[166,60],[178,66],[160,70],[135,72],[105,77],[82,73],[68,70],[60,55],[52,46],[46,42],[40,42],[27,41]],
  // Japan
  [[130,31],[136,34],[141,38],[145,44],[141,45],[136,37],[130,33],[130,31]],
  // Indonesia / SE Islands
  [[95,5],[105,-6],[115,-8],[125,-8],[120,-2],[105,0],[95,5]],
  // Australia
  [[114,-22],[122,-17],[131,-12],[136,-12],[142,-11],[146,-19],[153,-28],[150,-37],[141,-38],[135,-34],[122,-34],[115,-34],[113,-26],[114,-22]],
  // New Zealand
  [[167,-46],[172,-43],[178,-37],[174,-35],[170,-42],[167,-46]],
];

export const PLACE_PRESETS = [
  { name: "San Francisco, CA", city: "San Francisco", region: "California", country: "United States", lat: 37.7749, lng: -122.4194 },
  { name: "New York, NY", city: "New York", region: "New York", country: "United States", lat: 40.7128, lng: -74.006 },
  { name: "Los Angeles, CA", city: "Los Angeles", region: "California", country: "United States", lat: 34.0522, lng: -118.2437 },
  { name: "Seattle, WA", city: "Seattle", region: "Washington", country: "United States", lat: 47.6062, lng: -122.3321 },
  { name: "Austin, TX", city: "Austin", region: "Texas", country: "United States", lat: 30.2672, lng: -97.7431 },
  { name: "Chicago, IL", city: "Chicago", region: "Illinois", country: "United States", lat: 41.8781, lng: -87.6298 },
  { name: "London, UK", city: "London", region: "England", country: "United Kingdom", lat: 51.5074, lng: -0.1278 },
  { name: "Paris, France", city: "Paris", region: "Île-de-France", country: "France", lat: 48.8566, lng: 2.3522 },
  { name: "Tokyo, Japan", city: "Tokyo", region: "Kanto", country: "Japan", lat: 35.6762, lng: 139.6503 },
  { name: "Sydney, Australia", city: "Sydney", region: "NSW", country: "Australia", lat: -33.8688, lng: 151.2093 },
  { name: "Toronto, Canada", city: "Toronto", region: "Ontario", country: "Canada", lat: 43.6532, lng: -79.3832 },
  { name: "Bengaluru, India", city: "Bengaluru", region: "Karnataka", country: "India", lat: 12.9716, lng: 77.5946 },
  { name: "Singapore", city: "Singapore", region: "", country: "Singapore", lat: 1.3521, lng: 103.8198 },
  { name: "Zurich, Switzerland", city: "Zurich", region: "Zurich", country: "Switzerland", lat: 47.3769, lng: 8.5417 },
];

function mercatorProject(lng, lat) {
  const clamped = Math.max(-82, Math.min(82, lat));
  const x = (lng + 180) / 360;
  const rad = (clamped * Math.PI) / 180;
  const y = 0.5 - Math.log(Math.tan(Math.PI / 4 + rad / 2)) / (2 * Math.PI);
  return [x, y];
}

export function renderDiaryMap(container, entries, { accentColor = "#44B6FF", basePrefix = "", onOpenEntry, onAddLocatedEntry }) {
  const located = entries.filter(
    (e) => e.location && typeof e.location.lat === "number" && typeof e.location.lng === "number"
  );
  const namedOnly = entries.filter(
    (e) => e.location && e.location.name && (typeof e.location.lat !== "number" || typeof e.location.lng !== "number")
  );

  container.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "map-shell";

  const stage = document.createElement("div");
  stage.className = "map-stage";

  const canvas = document.createElement("canvas");
  canvas.className = "map-canvas";
  stage.appendChild(canvas);

  const controls = document.createElement("div");
  controls.className = "map-controls";
  controls.innerHTML = `
    <button type="button" class="map-ctrl-btn" data-zoom="in" aria-label="Zoom in">+</button>
    <button type="button" class="map-ctrl-btn" data-zoom="out" aria-label="Zoom out">−</button>
    <button type="button" class="map-ctrl-btn" data-zoom="fit" aria-label="Fit all pins">◎</button>
  `;
  stage.appendChild(controls);

  const badge = document.createElement("div");
  badge.className = "map-summary-pill";
  badge.textContent = located.length
    ? `${located.length} geotagged ${located.length === 1 ? "entry" : "entries"}`
    : "No geotagged entries yet";
  stage.appendChild(badge);

  const popup = document.createElement("div");
  popup.className = "map-popup";
  popup.hidden = true;
  stage.appendChild(popup);

  wrap.appendChild(stage);

  // Places list below/beside map
  const placesList = document.createElement("div");
  placesList.className = "map-places-list";
  const byPlace = new Map();
  for (const entry of [...located, ...namedOnly]) {
    const key = entry.location.name || entry.location.city || `${entry.location.lat?.toFixed(2)}, ${entry.location.lng?.toFixed(2)}`;
    if (!byPlace.has(key)) byPlace.set(key, { name: key, location: entry.location, entries: [] });
    byPlace.get(key).entries.push(entry);
  }

  if (byPlace.size === 0) {
    placesList.innerHTML = `
      <div class="empty-card compact">
        <div class="empty-icon">🗺️</div>
        <h3>Map your memories</h3>
        <p>Add a location to any entry using the 📍 button in the editor to plot your travels and favorite places here.</p>
        <button type="button" class="btn-primary" id="map-add-entry-btn">Create Geotagged Entry</button>
      </div>
    `;
    placesList.querySelector("#map-add-entry-btn")?.addEventListener("click", () => onAddLocatedEntry?.());
  } else {
    const heading = document.createElement("div");
    heading.className = "map-places-head";
    heading.innerHTML = `<strong>Places (${byPlace.size})</strong><span>Tap to focus</span>`;
    placesList.appendChild(heading);

    for (const group of byPlace.values()) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "map-place-row";
      btn.innerHTML = `
        <span class="map-place-pin">📍</span>
        <span class="map-place-info">
          <strong></strong>
          <small></small>
        </span>
        <span class="map-place-count">${group.entries.length}</span>
      `;
      btn.querySelector("strong").textContent = group.name;
      const latest = group.entries[0];
      btn.querySelector("small").textContent = `${latest.date} · ${latest.title}`;
      btn.addEventListener("click", () => {
        if (typeof group.location.lat === "number" && typeof group.location.lng === "number") {
          focusLatLng(group.location.lat, group.location.lng, Math.max(state.zoom, 3.2));
          showClusterPopup(group.entries, group.name);
        } else {
          onOpenEntry?.(latest.id);
        }
      });
      placesList.appendChild(btn);
    }
  }
  wrap.appendChild(placesList);
  container.appendChild(wrap);

  // Camera state: normalized Mercator center [0..1], zoom >= 1
  const state = { cx: 0.5, cy: 0.42, zoom: 1.15, pins: [], dragging: false, lastX: 0, lastY: 0, moved: false };

  function fitAll() {
    if (!located.length) {
      state.cx = 0.5;
      state.cy = 0.42;
      state.zoom = 1.15;
      return;
    }
    const pts = located.map((e) => mercatorProject(e.location.lng, e.location.lat));
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    state.cx = (minX + maxX) / 2;
    state.cy = (minY + maxY) / 2;
    const span = Math.max(maxX - minX, maxY - minY, 0.06);
    state.zoom = Math.max(1.2, Math.min(8, 0.55 / span));
  }

  function focusLatLng(lat, lng, zoom) {
    const [mx, my] = mercatorProject(lng, lat);
    state.cx = mx;
    state.cy = my;
    state.zoom = Math.max(1, Math.min(18, zoom));
    draw();
  }

  function showClusterPopup(clusterEntries, headingText) {
    popup.hidden = false;
    popup.innerHTML = "";
    const head = document.createElement("div");
    head.className = "map-popup-head";
    const h = document.createElement("strong");
    h.textContent = headingText || (clusterEntries[0]?.location?.name || "Location");
    const close = document.createElement("button");
    close.type = "button";
    close.className = "icon-btn-sm";
    close.textContent = "×";
    close.onclick = () => { popup.hidden = true; };
    head.append(h, close);
    popup.appendChild(head);

    const list = document.createElement("div");
    list.className = "map-popup-entries";
    for (const entry of clusterEntries.slice(0, 6)) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "map-popup-item";
      const firstPhoto = (entry.media || []).find((m) => m.kind === "image");
      if (firstPhoto) {
        const img = document.createElement("img");
        img.src = `${basePrefix}/api/diary/entries/${encodeURIComponent(entry.id)}/media/${encodeURIComponent(firstPhoto.id)}`;
        img.alt = "";
        item.appendChild(img);
      }
      const txt = document.createElement("span");
      const title = document.createElement("b");
      title.textContent = entry.title;
      const meta = document.createElement("small");
      meta.textContent = `${entry.date}${entry.journal ? " · " + entry.journal : ""}`;
      txt.append(title, meta);
      item.appendChild(txt);
      item.onclick = () => onOpenEntry?.(entry.id);
      list.appendChild(item);
    }
    popup.appendChild(list);
  }

  function draw() {
    const rect = stage.getBoundingClientRect();
    const w = Math.max(280, Math.floor(rect.width));
    const h = Math.max(240, Math.floor(rect.height));
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const isDark =
      document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme && window.matchMedia?.("(prefers-color-scheme: dark)").matches);

    const ocean = isDark ? "#121a24" : "#dbeeff";
    const land = isDark ? "#23303f" : "#f7fafc";
    const border = isDark ? "#33475c" : "#b8d2e8";
    const grid = isDark ? "rgba(255,255,255,0.05)" : "rgba(30,100,160,0.08)";

    ctx.fillStyle = ocean;
    ctx.fillRect(0, 0, w, h);

    const worldSize = Math.min(w, h) * 1.45 * state.zoom;
    const toScreen = (mx, my) => [
      w / 2 + (mx - state.cx) * worldSize,
      h / 2 + (my - state.cy) * worldSize,
    ];

    // Graticule lines
    ctx.strokeStyle = grid;
    ctx.lineWidth = 1;
    for (let lng = -150; lng <= 180; lng += 30) {
      const [x] = toScreen((lng + 180) / 360, 0.5);
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
    for (let lat = -60; lat <= 75; lat += 30) {
      const [, y] = toScreen(0.5, mercatorProject(0, lat)[1]);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    // Continents
    ctx.fillStyle = land;
    ctx.strokeStyle = border;
    ctx.lineWidth = 1.25;
    for (const poly of CONTINENTS) {
      ctx.beginPath();
      poly.forEach(([lng, lat], idx) => {
        const [mx, my] = mercatorProject(lng, lat);
        const [sx, sy] = toScreen(mx, my);
        if (idx === 0) ctx.moveTo(sx, sy);
        else ctx.lineTo(sx, sy);
      });
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    }

    // Cluster nearby pins
    const clusters = [];
    for (const entry of located) {
      const [mx, my] = mercatorProject(entry.location.lng, entry.location.lat);
      const [sx, sy] = toScreen(mx, my);
      let hit = null;
      for (const c of clusters) {
        if (Math.hypot(c.x - sx, c.y - sy) < 34) {
          hit = c;
          break;
        }
      }
      if (hit) {
        hit.entries.push(entry);
        hit.x = (hit.x * (hit.entries.length - 1) + sx) / hit.entries.length;
        hit.y = (hit.y * (hit.entries.length - 1) + sy) / hit.entries.length;
      } else {
        clusters.push({ x: sx, y: sy, entries: [entry] });
      }
    }
    state.pins = clusters;

    // Draw pins
    for (const cluster of clusters) {
      if (cluster.x < -30 || cluster.x > w + 30 || cluster.y < -30 || cluster.y > h + 30) continue;
      const r = cluster.entries.length > 1 ? 16 : 12;

      // Soft shadow
      ctx.beginPath();
      ctx.arc(cluster.x, cluster.y + 3, r + 2, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(0,0,0,0.22)";
      ctx.fill();

      // White ring
      ctx.beginPath();
      ctx.arc(cluster.x, cluster.y, r + 2.5, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.fill();

      // Accent fill
      ctx.beginPath();
      ctx.arc(cluster.x, cluster.y, r, 0, Math.PI * 2);
      ctx.fillStyle = accentColor;
      ctx.fill();

      // Count or center dot
      ctx.fillStyle = "#ffffff";
      if (cluster.entries.length > 1) {
        ctx.font = "700 11px system-ui, -apple-system, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(String(cluster.entries.length), cluster.x, cluster.y + 0.5);
      } else {
        ctx.beginPath();
        ctx.arc(cluster.x, cluster.y, 4, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  // Pointer pan & click
  canvas.addEventListener("pointerdown", (e) => {
    state.dragging = true;
    state.moved = false;
    state.lastX = e.clientX;
    state.lastY = e.clientY;
    canvas.setPointerCapture?.(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!state.dragging) return;
    const dx = e.clientX - state.lastX;
    const dy = e.clientY - state.lastY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) state.moved = true;
    state.lastX = e.clientX;
    state.lastY = e.clientY;
    const rect = stage.getBoundingClientRect();
    const worldSize = Math.min(rect.width, rect.height) * 1.45 * state.zoom;
    state.cx = Math.max(0.05, Math.min(0.95, state.cx - dx / worldSize));
    state.cy = Math.max(0.08, Math.min(0.92, state.cy - dy / worldSize));
    draw();
  });
  canvas.addEventListener("pointerup", (e) => {
    if (!state.dragging) return;
    state.dragging = false;
    if (!state.moved) {
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const hit = state.pins.find((p) => Math.hypot(p.x - x, p.y - y) <= 22);
      if (hit) {
        showClusterPopup(hit.entries, hit.entries[0]?.location?.name || "Entries here");
      } else {
        popup.hidden = true;
      }
    }
  });
  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.2 : 0.83;
      state.zoom = Math.max(1, Math.min(18, state.zoom * factor));
      draw();
    },
    { passive: false }
  );

  controls.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-zoom]");
    if (!btn) return;
    const act = btn.dataset.zoom;
    if (act === "in") state.zoom = Math.min(18, state.zoom * 1.4);
    else if (act === "out") state.zoom = Math.max(1, state.zoom / 1.4);
    else if (act === "fit") fitAll();
    draw();
  });

  fitAll();
  requestAnimationFrame(draw);
  const ro = new ResizeObserver(() => draw());
  ro.observe(stage);
  return () => ro.disconnect();
}
