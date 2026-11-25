export function renderMedia(host, entry) {
  const base = location.pathname.startsWith("/agent/diary_agent") ? "/agent/diary_agent" : "";
  const MEDIA_LINK = /\[([^\]\n]{1,160})\]\((\/agent\/diary_agent\/api\/diary\/entries\/[0-9A-Fa-f-]{36}\/media\/[A-Za-z0-9_-]{1,64})\)/g;
  const MEDIA_KINDS = [
    ["audio", /\.(m4a|aac|mp3|wav|ogg|oga|opus|flac|weba|amr|caf)$/i],
    ["video", /\.(mp4|m4v|mov|webm|3gp)$/i],
    ["image", /\.(jpe?g|png|gif|webp|heic|heif)$/i],
  ];

  function mediaKind(name) {
    const hit = MEDIA_KINDS.find(([, pattern]) => pattern.test(name));
    return hit ? hit[0] : "file";
  }

  function mediaUrl(path) {
    // The stored link carries the proxy prefix; keep it behind the proxy and drop it at the root.
    return base + path.slice("/agent/diary_agent".length);
  }

    host.textContent = "";
    const seen = new Set();
    const items = [];
    for (const row of entry.media || []) {
      if (!/^[a-f0-9-]{36}$/i.test(entry.id) || !/^[a-z0-9_-]{1,64}$/i.test(row.id)) continue;
      const path = "/agent/diary_agent/api/diary/entries/" + entry.id + "/media/" + row.id;
      seen.add(path); items.push({name:row.name, url:mediaUrl(path), kind:mediaKind(row.name), transcript:row.transcription});
    }
    for (const match of String(entry.body || "").matchAll(MEDIA_LINK)) {
      if (seen.has(match[2])) continue;
      seen.add(match[2]);
      items.push({ name: match[1], url: mediaUrl(match[2]), kind: mediaKind(match[1]) });
    }
    host.hidden = items.length === 0;
    if (!items.length) return;
    const heading = document.createElement("h3");
    heading.className = "diary-media-heading";
    heading.textContent = "Attachments (" + items.length + ")";
    host.appendChild(heading);
    for (const item of items) {
      const row = document.createElement("figure");
      row.className = "diary-media-item " + item.kind;
      let player;
      if (item.kind === "audio" || item.kind === "video") {
        player = document.createElement(item.kind);
        player.controls = true;
        player.preload = "metadata";
        if (item.kind === "video") player.playsInline = true;
        player.src = item.url;
      } else if (item.kind === "image") {
        player = document.createElement("img");
        player.loading = "lazy";
        player.alt = item.name;
        player.src = item.url;
      }
      if (player) row.appendChild(player);
      const caption = document.createElement("figcaption");
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = (item.kind === "audio" ? "Voice note · " : "") + item.name;
      caption.appendChild(link);
      if (item.transcript) { const text=document.createElement("p"); text.textContent=item.transcript; caption.appendChild(text); }
      row.appendChild(caption);
      host.appendChild(row);
    }
  }
