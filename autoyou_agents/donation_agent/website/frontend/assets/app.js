// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

// Support AutoYou — public donation page. One fetch, no polling, no auth.

(function () {
  "use strict";

  var els = {
    heroTitle: document.getElementById("hero-title"),
    heroTagline: document.getElementById("hero-tagline"),
    providersSection: document.getElementById("providers-section"),
    providerList: document.getElementById("provider-list"),
    cryptoSection: document.getElementById("crypto-section"),
    cryptoList: document.getElementById("crypto-list"),
    cryptoPending: document.getElementById("crypto-pending"),
    cryptoRouteSelect: document.getElementById("crypto-route-select"),
    cryptoRoutePreview: document.getElementById("crypto-route-preview"),
    cryptoRouteQr: document.getElementById("crypto-route-qr"),
    cryptoRouteQrFallback: document.getElementById("crypto-route-qr-fallback"),
    cryptoRouteBadge: document.getElementById("crypto-route-badge"),
    cryptoRouteName: document.getElementById("crypto-route-preview-name"),
    cryptoRouteNetwork: document.getElementById("crypto-route-preview-network"),
    cryptoRouteAddress: document.getElementById("crypto-route-preview-address"),
    cryptoRouteTag: document.getElementById("crypto-route-preview-tag"),
    cryptoRouteTagValue: document.getElementById("crypto-route-preview-tag-value"),
    cryptoRouteCopy: document.getElementById("crypto-route-copy"),
    cryptoRouteCopyTag: document.getElementById("crypto-route-copy-tag"),
    cryptoRouteDeposit: document.getElementById("crypto-route-deposit"),
    cryptoRouteStatus: document.getElementById("crypto-route-status"),
    socialsSection: document.getElementById("socials-section"),
    socialList: document.getElementById("social-list"),
    shareBtn: document.getElementById("share-btn"),
    shareFeedback: document.getElementById("share-feedback"),
    footerLinks: document.getElementById("footer-links"),
    toast: document.getElementById("toast"),
  };

  var shareState = { message: "", url: "" };
  var toastTimer = null;
  var cryptoRoutes = [];

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function showToast(message) {
    if (!els.toast) { return; }
    els.toast.textContent = message;
    els.toast.classList.add("show");
    if (toastTimer) { clearTimeout(toastTimer); }
    toastTimer = setTimeout(function () { els.toast.classList.remove("show"); }, 2200);
  }

  function copyText(text, onDone) {
    function fallback() {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "readonly");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      document.body.removeChild(area);
      onDone(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { onDone(true); },
        function () { fallback(); }
      );
    } else {
      fallback();
    }
  }

  // Minimal inline icons for well-known networks; generic link icon otherwise.
  var SOCIAL_ICONS = {
    github: "M12 .5a11.5 11.5 0 0 0-3.64 22.41c.58.11.79-.25.79-.55v-2.17c-3.2.7-3.87-1.37-3.87-1.37-.53-1.33-1.28-1.69-1.28-1.69-1.05-.71.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.75 2.7 1.25 3.36.96.1-.75.4-1.25.72-1.54-2.55-.29-5.24-1.28-5.24-5.68 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.16-1.18 3.16-1.18.63 1.59.24 2.76.12 3.05.74.81 1.18 1.84 1.18 3.1 0 4.41-2.69 5.38-5.25 5.66.41.36.77 1.05.77 2.13v3.16c0 .3.2.67.8.55A11.5 11.5 0 0 0 12 .5z",
    x: "M18.24 2.25h3.31l-7.23 8.26 8.5 11.24h-6.66l-5.21-6.82L5 21.75H1.68l7.73-8.84L1.25 2.25h6.83l4.72 6.23zm-1.16 17.52h1.83L7.08 4.13H5.12z",
    discord: "M20.32 4.37A19.8 19.8 0 0 0 15.36 3c-.21.38-.46.9-.63 1.3a18.3 18.3 0 0 0-5.49 0c-.17-.4-.42-.92-.64-1.3a19.7 19.7 0 0 0-4.96 1.38C.53 9.05-.32 13.6.1 18.06a19.9 19.9 0 0 0 6.07 3.06c.49-.66.92-1.37 1.3-2.11-.72-.27-1.4-.6-2.05-.99.17-.13.34-.26.5-.39a14.2 14.2 0 0 0 12.14 0c.17.13.33.26.5.39-.65.39-1.34.72-2.05.99.38.74.81 1.44 1.3 2.11a19.8 19.8 0 0 0 6.07-3.06c.5-5.18-.84-9.68-3.56-13.69zM8.01 15.33c-1.18 0-2.16-1.08-2.16-2.42 0-1.33.95-2.42 2.16-2.42 1.21 0 2.18 1.09 2.16 2.42 0 1.34-.95 2.42-2.16 2.42zm7.98 0c-1.18 0-2.15-1.08-2.15-2.42 0-1.33.95-2.42 2.15-2.42 1.22 0 2.18 1.09 2.16 2.42 0 1.34-.94 2.42-2.16 2.42z",
    youtube: "M23.5 6.19a3.02 3.02 0 0 0-2.12-2.14C19.5 3.55 12 3.55 12 3.55s-7.5 0-9.38.5A3.02 3.02 0 0 0 .5 6.19C0 8.07 0 12 0 12s0 3.93.5 5.81a3.02 3.02 0 0 0 2.12 2.14c1.88.5 9.38.5 9.38.5s7.5 0 9.38-.5a3.02 3.02 0 0 0 2.12-2.14C24 15.93 24 12 24 12s0-3.93-.5-5.81zM9.55 15.57V8.43L15.82 12z",
    instagram: "M12 2.16c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 1.38.16.42.36 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 2.23-.22.56-.48.96-.9 1.38-.42.42-.82.68-1.38.9-.42.16-1.06.36-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41a3.7 3.7 0 0 1-1.38-.9 3.7 3.7 0 0 1-.9-1.38c-.16-.42-.36-1.06-.41-2.23-.06-1.27-.07-1.65-.07-4.85s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 1.06-.36 2.23-.41 1.27-.06 1.65-.07 4.85-.07zM12 0C8.74 0 8.33.01 7.05.07 5.78.13 4.9.33 4.14.63a5.9 5.9 0 0 0-2.13 1.39A5.9 5.9 0 0 0 .63 4.14C.33 4.9.13 5.78.07 7.05.01 8.33 0 8.74 0 12s.01 3.67.07 4.95c.06 1.27.26 2.15.56 2.91.31.8.72 1.47 1.39 2.13a5.9 5.9 0 0 0 2.13 1.39c.76.3 1.64.5 2.91.56 1.28.06 1.69.07 4.95.07s3.67-.01 4.95-.07c1.27-.06 2.15-.26 2.91-.56a5.9 5.9 0 0 0 2.13-1.39 5.9 5.9 0 0 0 1.39-2.13c.3-.76.5-1.64.56-2.91.06-1.28.07-1.69.07-4.95s-.01-3.67-.07-4.95c-.06-1.27-.26-2.15-.56-2.91a5.9 5.9 0 0 0-1.39-2.13A5.9 5.9 0 0 0 19.86.63c-.76-.3-1.64-.5-2.91-.56C15.67.01 15.26 0 12 0zm0 5.84A6.16 6.16 0 1 0 18.16 12 6.16 6.16 0 0 0 12 5.84zm0 10.15A4 4 0 1 1 16 12a4 4 0 0 1-4 3.99zm7.85-10.4a1.44 1.44 0 1 1-1.44-1.44 1.44 1.44 0 0 1 1.44 1.44z",
    tiktok: "M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.9 2.9 0 1 1-2.31-2.84v-3.5a6.37 6.37 0 1 0 5.76 6.34V8.69a8.2 8.2 0 0 0 4.77 1.52V6.75a4.85 4.85 0 0 1-1-.06z",
    reddit: "M24 12a2.4 2.4 0 0 0-2.4-2.4c-.63 0-1.2.25-1.63.65a11.8 11.8 0 0 0-6.06-1.9l1.17-3.7 3.18.75a1.8 1.8 0 1 0 .17-1.06l-3.68-.87a.54.54 0 0 0-.63.35l-1.36 4.53a11.8 11.8 0 0 0-6.13 1.9 2.38 2.38 0 0 0-1.63-.65 2.4 2.4 0 0 0-1.16 4.5c-.03.24-.05.49-.05.74 0 3.78 4.4 6.86 9.83 6.86s9.83-3.08 9.83-6.86c0-.25-.02-.49-.05-.73A2.4 2.4 0 0 0 24 12zm-17.14 1.71a1.71 1.71 0 1 1 3.43 0 1.71 1.71 0 0 1-3.43 0zm9.61 4.53c-1.17 1.17-3.42 1.26-4.47 1.26s-3.3-.09-4.47-1.26a.46.46 0 0 1 .65-.65c.74.74 2.32.99 3.82.99s3.08-.26 3.82-.99a.46.46 0 0 1 .65.65zm-.31-2.82a1.71 1.71 0 1 1 1.71-1.71 1.71 1.71 0 0 1-1.71 1.71z",
    telegram: "M23.91 3.79 20.3 20.84c-.25 1.21-.98 1.5-2 .94l-5.5-4.07-2.66 2.57c-.3.3-.55.55-1.1.55l.4-5.61L19.65 6c.44-.4-.1-.61-.68-.22L6.38 13.72l-5.42-1.7c-1.18-.37-1.2-1.18.24-1.75L21.26 2.08c.98-.37 1.84.22 2.65 1.71z",
    substack: "M22.54 8.35H1.46V5.9h21.08zM1.46 10.6v12.15L12 16.85l10.54 5.9V10.6zM22.54 1.46H1.46v2.44h21.08z",
    linkedin: "M20.45 20.45h-3.55v-5.57c0-1.33-.03-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.36V9h3.41v1.56h.05c.47-.9 1.63-1.85 3.36-1.85 3.6 0 4.27 2.37 4.27 5.45zM5.34 7.43a2.06 2.06 0 1 1 0-4.12 2.06 2.06 0 0 1 0 4.12zM7.12 20.45H3.56V9h3.56zM22.22 0H1.77C.79 0 0 .77 0 1.72v20.55C0 23.22.79 24 1.77 24h20.45c.98 0 1.78-.78 1.78-1.72V1.72C24 .77 23.2 0 22.22 0z",
    generic: "M10.6 13.4a1 1 0 0 0 1.4 0l4-4a2.83 2.83 0 0 0-4-4l-1.3 1.3a1 1 0 1 0 1.42 1.4l1.3-1.28a.83.83 0 0 1 1.17 1.17l-4 4a1 1 0 0 0 0 1.41zm2.8-2.8a1 1 0 0 0-1.4 0l-4 4a2.83 2.83 0 1 0 4 4l1.3-1.3a1 1 0 1 0-1.42-1.4l-1.3 1.28a.83.83 0 0 1-1.17-1.17l4-4a1 1 0 0 0 0-1.41z",
  };

  function socialIcon(id) {
    var path = SOCIAL_ICONS[id] || SOCIAL_ICONS.generic;
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="' + path + '"/></svg>';
  }

  function renderProviders(providers) {
    if (!providers.length) { return; }
    els.providerList.innerHTML = providers
      .map(function (item) {
        return (
          '<a class="provider-card" href="' + esc(item.url) + '" target="_blank" rel="noopener noreferrer">' +
          "<strong>" + esc(item.label) + "</strong>" +
          (item.description ? "<span>" + esc(item.description) + "</span>" : "") +
          '<span class="go">Open &rarr;</span>' +
          "</a>"
        );
      })
      .join("");
    els.providersSection.hidden = false;
  }

  function cryptoQrUrl(address) {
    return "./api/crypto-qr?address=" + encodeURIComponent(address);
  }

  function routeNeedsDestinationTag(item) {
    return String(item.symbol || "").toUpperCase() === "XRP" && Boolean(item.memo);
  }

  // The card badge, border glow and token chips are all derived from --accent.
  // Several coins have near-black primary brand colours, which disappear
  // against this dark theme and make a fully configured route look dead. Keep
  // the operator's hue and push it up to a readable luminance instead.
  var ACCENT_FALLBACK = "#8b5cf6";
  var MIN_ACCENT_LUMINANCE = 0.22;

  function normalizeAccent(raw) {
    var value = String(raw || "").trim();
    if (!/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value)) { return ACCENT_FALLBACK; }
    if (value.length === 4) {
      value = "#" + value[1] + value[1] + value[2] + value[2] + value[3] + value[3];
    }
    var r = parseInt(value.slice(1, 3), 16) / 255;
    var g = parseInt(value.slice(3, 5), 16) / 255;
    var b = parseInt(value.slice(5, 7), 16) / 255;
    // Rec. 709 relative luminance; good enough to spot "this is basically black".
    var luminance = (0.2126 * r) + (0.7152 * g) + (0.0722 * b);
    if (luminance >= MIN_ACCENT_LUMINANCE) { return value; }
    if (luminance <= 0.02) { return ACCENT_FALLBACK; }
    var scale = MIN_ACCENT_LUMINANCE / luminance;
    var lift = function (channel) {
      return Math.round(Math.min(1, channel * scale) * 255).toString(16).padStart(2, "0");
    };
    return "#" + lift(r) + lift(g) + lift(b);
  }

  function selectCryptoRoute(index) {
    var item = cryptoRoutes[index];
    if (!item || !els.cryptoRoutePreview) { return; }
    if (els.cryptoRouteSelect) { els.cryptoRouteSelect.value = String(index); }
    els.cryptoRoutePreview.hidden = false;
    if (els.cryptoRouteName) { els.cryptoRouteName.textContent = item.name || item.symbol || "Crypto route"; }
    if (els.cryptoRouteNetwork) { els.cryptoRouteNetwork.textContent = (item.symbol ? item.symbol + " · " : "") + (item.network || "Network"); }
    if (els.cryptoRouteAddress) { els.cryptoRouteAddress.textContent = item.address; }
    if (els.cryptoRouteCopy) {
      els.cryptoRouteCopy.setAttribute("data-copy", item.address);
      els.cryptoRouteCopy.setAttribute("data-copy-label", "Copy address");
    }
    var hasMemo = routeNeedsDestinationTag(item);
    if (els.cryptoRouteTag) { els.cryptoRouteTag.hidden = !hasMemo; }
    if (els.cryptoRouteCopyTag) {
      els.cryptoRouteCopyTag.hidden = !hasMemo;
      if (hasMemo) {
        els.cryptoRouteTagValue.textContent = item.memo;
        els.cryptoRouteCopyTag.setAttribute("data-copy", item.memo);
        els.cryptoRouteCopyTag.setAttribute("data-copy-label", "Copy memo");
      } else {
        els.cryptoRouteTagValue.textContent = "";
        els.cryptoRouteCopyTag.removeAttribute("data-copy");
      }
    }
    if (els.cryptoRouteDeposit) {
      els.cryptoRouteDeposit.hidden = !item.deposit_url;
      if (item.deposit_url) { els.cryptoRouteDeposit.href = item.deposit_url; }
    }
    if (els.cryptoRouteQr) {
      els.cryptoRouteQr.alt = "QR code for the " + (item.name || item.symbol || "crypto") + " public address";
      els.cryptoRouteQr.hidden = false;
      els.cryptoRouteQr.src = cryptoQrUrl(item.address);
    }
    if (els.cryptoRouteBadge) { els.cryptoRouteBadge.textContent = "QR loading"; }
    if (els.cryptoRouteQrFallback) { els.cryptoRouteQrFallback.hidden = true; }
    if (els.cryptoRouteStatus) {
      els.cryptoRouteStatus.textContent = hasMemo
        ? "The QR contains the address only. Include the XRP memo too, then verify both before sending."
        : "The QR contains the public address only. Verify the asset and network before sending.";
    }
    document.querySelectorAll(".crypto-route-card[data-route-index]").forEach(function (card) {
      card.classList.toggle("is-selected", Number(card.getAttribute("data-route-index")) === index);
    });
  }

  function renderCrypto(entries) {
    var configured = entries.filter(function (item) { return item.configured && item.address; });
    if (!configured.length) {
      els.cryptoPending.hidden = false;
      return;
    }
    cryptoRoutes = configured;
    if (els.cryptoRouteSelect) {
      els.cryptoRouteSelect.replaceChildren.apply(els.cryptoRouteSelect, configured.map(function (item, index) {
        var option = document.createElement("option");
        option.value = String(index);
        option.textContent = (item.symbol ? item.symbol + " · " : "") + item.name + " · " + item.network;
        return option;
      }));
      els.cryptoRouteSelect.onchange = function () { selectCryptoRoute(Number(els.cryptoRouteSelect.value)); };
    }
    els.cryptoList.innerHTML = configured
      .map(function (item, index) {
        var accent = normalizeAccent(item.accent);
        var tokens = (item.tokens || [])
          .map(function (token) { return '<span class="token-chip">' + esc(token) + "</span>"; })
          .join("");
        var note = item.note ? '<p class="crypto-note">' + esc(item.note) + "</p>" : "";
        var hasMemo = routeNeedsDestinationTag(item);
        var memo = hasMemo
          ? '<div class="addr-box memo-box"><small>Memo</small><span class="addr">' + esc(item.memo) + "</span></div>"
          : "";
        var memoButton = hasMemo
          ? '<button type="button" class="btn btn-copy btn-copy-secondary" data-copy="' + esc(item.memo) + '" data-copy-label="Copy memo">Copy memo</button>'
          : "";
        var depositLink = item.deposit_url
          ? '<a class="crypto-deposit-link" href="' + esc(item.deposit_url) + '" target="_blank" rel="noopener noreferrer">Open deposit instructions</a>'
          : "";
        return (
          '<article class="crypto-card crypto-route-card" data-route-index="' + index + '" style="--accent:' + accent + '">' +
          '<div class="crypto-head">' +
          '<span class="coin-badge">' + esc((item.symbol || "?").slice(0, 4)) + "</span>" +
          "<div><h3>" + esc(item.name) + '</h3><span class="net">' + esc(item.network) + "</span></div>" +
          "</div>" +
          (tokens ? '<div class="token-row">' + tokens + "</div>" : "") +
          note +
          '<div class="addr-box"><small>Network address</small><span class="addr">' + esc(item.address) + "</span></div>" +
          memo +
          '<div class="crypto-actions"><button type="button" class="btn btn-copy" data-copy="' + esc(item.address) + '" data-copy-label="Copy Address">Copy Address</button>' + memoButton + "</div>" +
          depositLink +
          "</article>"
        );
      })
      .join("");
    if (els.cryptoRouteQr) {
      els.cryptoRouteQr.onload = function () {
        els.cryptoRouteQr.hidden = false;
        if (els.cryptoRouteQrFallback) { els.cryptoRouteQrFallback.hidden = true; }
        if (els.cryptoRouteBadge) { els.cryptoRouteBadge.textContent = "QR ready"; }
      };
      els.cryptoRouteQr.onerror = function () {
        els.cryptoRouteQr.hidden = true;
        if (els.cryptoRouteQrFallback) { els.cryptoRouteQrFallback.hidden = false; }
        if (els.cryptoRouteBadge) { els.cryptoRouteBadge.textContent = "QR unavailable"; }
      };
    }
    selectCryptoRoute(0);
    els.cryptoSection.hidden = false;
  }

  function renderSocials(socials) {
    if (!socials.length) {
      // Still show the share row so visitors can spread the word.
      els.socialList.innerHTML = "";
      els.socialsSection.hidden = false;
      return;
    }
    els.socialList.innerHTML = socials
      .map(function (item) {
        return (
          '<a class="social-card" href="' + esc(item.url) + '" target="_blank" rel="noopener noreferrer">' +
          socialIcon(item.id) +
          "<span><strong>" + esc(item.label) + "</strong>" +
          (item.handle ? "<small>@" + esc(item.handle.replace(/^@/, "")) + "</small>" : "") +
          "</span></a>"
        );
      })
      .join("");
    els.socialsSection.hidden = false;
  }

  function renderFooter(links) {
    var parts = [];
    if (links.share_url) {
      parts.push('<a href="' + esc(links.share_url) + '" target="_blank" rel="noopener noreferrer">' + esc(links.project_name) + " website</a>");
    }
    parts.push('<a href="https://www.autoyou.me/donate/" target="_blank" rel="noopener noreferrer">Funding transparency</a>');
    els.footerLinks.innerHTML = parts.join(" &middot; ");
  }

  function render(links) {
    if (links.project_name && els.heroTitle) {
      els.heroTitle.innerHTML = 'Support <span class="grad">' + esc(links.project_name) + "</span>";
    }
    if (links.tagline && els.heroTagline) {
      els.heroTagline.textContent = links.tagline;
    }
    shareState.message = links.share_message || "";
    shareState.url = links.share_url || "";
    renderProviders(links.providers || []);
    renderCrypto(links.crypto || []);
    renderSocials(links.socials || []);
    renderFooter(links);
  }

  document.addEventListener("click", function (event) {
    var routeCard = event.target.closest ? event.target.closest("[data-route-index]") : null;
    if (routeCard && !event.target.closest("button, a")) {
      selectCryptoRoute(Number(routeCard.getAttribute("data-route-index")));
      return;
    }
    var target = event.target.closest ? event.target.closest("[data-copy]") : null;
    if (!target) { return; }
    var value = target.getAttribute("data-copy") || "";
    var defaultLabel = target.getAttribute("data-copy-label") || "Copy Address";
    copyText(value, function (ok) {
      if (ok) {
        target.classList.add("copied");
        target.textContent = "Copied!";
        showToast("Copied — double-check the value and network before sending.");
        setTimeout(function () {
          target.classList.remove("copied");
          target.textContent = defaultLabel;
        }, 1800);
      } else {
        showToast("Copy failed — long-press the address to copy it manually.");
      }
    });
  });

  if (els.shareBtn) {
    els.shareBtn.addEventListener("click", function () {
      var text = (shareState.message + " " + shareState.url).trim();
      if (navigator.share) {
        navigator.share({ text: shareState.message, url: shareState.url }).catch(function () {});
        return;
      }
      copyText(text, function (ok) {
        els.shareFeedback.textContent = ok
          ? "Share message copied — paste it anywhere!"
          : "Copy failed — share " + shareState.url + " manually.";
      });
    });
  }

  function load() {
    fetch("./api/donation-links", { headers: { Accept: "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        if (payload && payload.success && payload.links) {
          render(payload.links);
        } else {
          els.cryptoPending.hidden = false;
          els.socialsSection.hidden = false;
        }
      })
      .catch(function () {
        els.cryptoPending.hidden = false;
        els.socialsSection.hidden = false;
      });
  }

  load();
})();
