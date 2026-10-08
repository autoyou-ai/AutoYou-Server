# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Stylesheet for the Agent Apps store page."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


# Class names deliberately avoid "scroll" and "overflow": the browser shells
# tune any element whose class contains them. The page also opts out of those
# shells' scroll scripts with data-autoyou-scroll-managed, because it owns its
# own touch handling (see page_js).
CSS = r"""
:root {
  color-scheme: dark light;
  --s: 1;
  --sd: min(var(--s), 1.25);
  --bg: #090b14;
  --bg-glow-a: rgba(111, 124, 255, 0.34);
  --bg-glow-b: rgba(61, 223, 176, 0.2);
  --bg-glow-c: rgba(245, 138, 208, 0.14);
  --surface: rgba(255, 255, 255, 0.06);
  --surface-hi: rgba(255, 255, 255, 0.11);
  --glass: rgba(16, 19, 36, 0.8);
  --line: rgba(255, 255, 255, 0.12);
  --text: #f3f5ff;
  --muted: #a6aecb;
  --faint: #7b84a6;
  --accent: #8e9bff;
  --accent-ink: #0b0e1d;
  --ring: rgba(142, 155, 255, 0.95);
  --ok: #4be3a3;
  --warn: #ffc23d;
  --shadow: 0 18px 50px -12px rgba(0, 0, 0, 0.65);
  --dock-h: 0px;
  --gutter: max(16px, env(safe-area-inset-left), env(safe-area-inset-right));
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #eef1fc;
    --bg-glow-a: rgba(112, 126, 255, 0.28);
    --bg-glow-b: rgba(61, 223, 176, 0.2);
    --bg-glow-c: rgba(245, 138, 208, 0.16);
    --surface: rgba(20, 28, 70, 0.05);
    --surface-hi: rgba(20, 28, 70, 0.09);
    --glass: rgba(255, 255, 255, 0.82);
    --line: rgba(20, 28, 70, 0.13);
    --text: #121631;
    --muted: #4d5780;
    --faint: #7b84a6;
    --accent: #4b58e6;
    --accent-ink: #ffffff;
    --ring: rgba(75, 88, 230, 0.9);
    --ok: #0b9c63;
    --warn: #b9770a;
    --shadow: 0 18px 50px -14px rgba(34, 42, 110, 0.35);
  }
}

* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; scroll-padding-top: 5rem; }
body {
  margin: 0;
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--bg);
  color: var(--text);
  font: 400 1rem/1.42 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
body::before {
  content: "";
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background:
    radial-gradient(900px 520px at 8% -8%, var(--bg-glow-a), transparent 62%),
    radial-gradient(760px 480px at 100% 4%, var(--bg-glow-b), transparent 58%),
    radial-gradient(700px 520px at 70% 108%, var(--bg-glow-c), transparent 60%);
}
button, input, a { font: inherit; color: inherit; }
button { cursor: pointer; border: 0; background: none; padding: 0; -webkit-appearance: none; appearance: none; }
[hidden] { display: none !important; }
.sr {
  position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
  overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}

.glyph {
  fill: none; stroke: currentColor; stroke-width: 1.7;
  stroke-linecap: round; stroke-linejoin: round;
}

/* ---------- layout ---------- */
.shell {
  width: min(100%, 76rem);
  margin: 0 auto;
  padding: 0 var(--gutter) calc(var(--dock-h) + 1.5rem + env(safe-area-inset-bottom));
  /* Two fingers resize the apps (see page_js), so the browser must not zoom the page instead. */
  touch-action: pan-y;
}
.layout { display: block; }
.hero {
  display: flex; align-items: center; gap: 0.9rem;
  padding: calc(0.9rem + env(safe-area-inset-top)) 0 0.55rem;
}
.mark {
  flex: none; display: grid; place-items: center;
  width: 2.9rem; height: 2.9rem; border-radius: 0.95rem; color: #fff;
  background: linear-gradient(135deg, #8e9bff, #3ddfb0);
  box-shadow: 0 10px 24px -10px rgba(111, 124, 255, 0.9), inset 0 1px 0 rgba(255, 255, 255, 0.45);
}
.mark .glyph { width: 62%; height: 62%; stroke-width: 1.8; }
.hero-text { min-width: 0; }
h1 {
  margin: 0; font-size: clamp(1.75rem, 6.2vw, 2.45rem); line-height: 1.05;
  font-weight: 760; letter-spacing: -0.025em;
}
.sub {
  margin: 0.2rem 0 0; color: var(--muted); font-size: 0.9rem;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* ---------- search bar ---------- */
.bar {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center; gap: 0.5rem;
  margin: 0 calc(var(--gutter) * -1); padding: 0.55rem var(--gutter);
  padding-top: calc(0.55rem + env(safe-area-inset-top));
  transition: background 0.2s, box-shadow 0.2s;
}
.bar.is-stuck {
  background: color-mix(in srgb, var(--bg) 78%, transparent);
  box-shadow: 0 1px 0 var(--line);
  -webkit-backdrop-filter: blur(18px) saturate(1.5); backdrop-filter: blur(18px) saturate(1.5);
}
.search {
  flex: 1; min-width: 0; display: flex; align-items: center; gap: 0.55rem;
  height: 3rem; padding: 0 0.35rem 0 0.95rem; border-radius: 1.1rem;
  background: var(--glass); border: 1px solid var(--line);
  -webkit-backdrop-filter: blur(18px) saturate(1.5); backdrop-filter: blur(18px) saturate(1.5);
  transition: border-color 0.18s, box-shadow 0.18s;
}
.search:focus-within { border-color: var(--accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 24%, transparent); }
.search .glyph { flex: none; width: 1.2rem; height: 1.2rem; color: var(--muted); }
.search input {
  flex: 1; min-width: 0; height: 100%; border: 0; outline: 0; background: transparent;
  font-size: max(16px, 1rem); -webkit-appearance: none; appearance: none;
  -webkit-user-select: text; user-select: text;
}
.search input::placeholder { color: var(--faint); }
.search input::-webkit-search-cancel-button { -webkit-appearance: none; display: none; }
.ibtn {
  flex: none; display: grid; place-items: center; position: relative;
  width: 3rem; height: 3rem; border-radius: 1.1rem; color: var(--muted);
  background: var(--glass); border: 1px solid var(--line);
  -webkit-backdrop-filter: blur(18px) saturate(1.5); backdrop-filter: blur(18px) saturate(1.5);
  transition: transform 0.15s, color 0.15s, background 0.15s;
}
.ibtn .glyph { width: 1.3rem; height: 1.3rem; }
.ibtn:active { transform: scale(0.94); }
.ibtn[aria-expanded="true"] { color: var(--accent-ink); background: var(--accent); border-color: transparent; }
.search .ibtn { width: 2.4rem; height: 2.4rem; border-radius: 0.8rem; border: 0; background: transparent; }
.ibtn.is-busy .glyph { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ---------- category chips ---------- */
.chips {
  display: flex; gap: 0.5rem; margin: 0 calc(var(--gutter) * -1); padding: 0.35rem var(--gutter) 0.8rem;
  overflow-x: auto; scrollbar-width: none; -webkit-overflow-scrolling: touch; touch-action: pan-x pan-y;
}
.chips::-webkit-scrollbar { display: none; }
.chip {
  flex: none; display: inline-flex; align-items: center; gap: 0.4rem; min-height: 2.4rem; padding: 0 1rem;
  border-radius: 999px; border: 1px solid var(--line); background: var(--surface);
  color: var(--muted); font-size: 0.88rem; font-weight: 640; white-space: nowrap;
  transition: background 0.15s, color 0.15s, transform 0.15s;
}
.chip b { font-weight: 600; font-size: 0.74rem; opacity: 0.7; }
.chip:active { transform: scale(0.96); }
.chip[aria-pressed="true"] { background: var(--text); color: var(--bg); border-color: transparent; }

/* ---------- app grid ---------- */
.grid {
  list-style: none; margin: 0; padding: 0.4rem 0 1rem;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(calc(4.7rem * var(--s)), 1fr));
  gap: calc(0.9rem * var(--s)) 0.35rem;
  touch-action: pan-y;
  -webkit-user-select: none; user-select: none; -webkit-touch-callout: none;
}
.cell { position: relative; display: flex; justify-content: center; min-width: 0; }
.app {
  display: flex; flex-direction: column; align-items: center; gap: 0.5rem; width: 100%;
  padding: 0.3rem 0.1rem 0.2rem; border-radius: 1.2rem; text-decoration: none; outline: none; cursor: pointer;
  -webkit-user-drag: none; -webkit-touch-callout: none; touch-action: pan-y;
}
.icon {
  position: relative; display: grid; place-items: center; flex: none;
  width: calc(4.4rem * var(--s)); max-width: 100%; aspect-ratio: 1; border-radius: 23%;
  background: linear-gradient(150deg, var(--a), var(--b));
  box-shadow:
    0 0.7rem 1.3rem -0.6rem var(--b),
    inset 0 1px 0 rgba(255, 255, 255, 0.42),
    inset 0 -0.55rem 0.9rem rgba(0, 0, 0, 0.14);
  transition: transform 0.28s cubic-bezier(0.2, 0.95, 0.3, 1.35), box-shadow 0.2s;
}
.icon::after {
  content: ""; position: absolute; inset: 0; border-radius: inherit; pointer-events: none;
  background: linear-gradient(158deg, rgba(255, 255, 255, 0.3), transparent 46%);
}
.icon .glyph {
  width: 54%; height: 54%; color: #fff; stroke-width: 1.65;
  filter: drop-shadow(0 1px 1.5px rgba(0, 0, 0, 0.28));
}
.label {
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
  max-width: 100%; font-size: calc(0.8rem * var(--s)); font-weight: 620; line-height: 1.18;
  text-align: center; word-break: break-word; color: var(--text);
}
.cell.is-down .icon { transform: scale(0.9); }
.cell.is-focus .icon {
  box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--ring), 0 1rem 1.8rem -0.6rem var(--b);
  transform: scale(1.05);
}
.cell.is-focus.is-down .icon { transform: scale(0.95); }
.cell.is-wait .icon { filter: grayscale(0.75) opacity(0.62); }
.cell.is-wait .app::after {
  content: ""; position: absolute; top: 0.45rem; right: calc(50% - 2.4rem * var(--s));
  width: 0.7rem; height: 0.7rem; border-radius: 50%; background: var(--warn);
  box-shadow: 0 0 0 3px var(--bg);
}
.cell.is-opening .icon { animation: pulse 0.9s ease-in-out infinite; }
@keyframes pulse { 50% { transform: scale(1.12); filter: brightness(1.18); } }
.app:focus-visible .icon { outline: 3px solid var(--ring); outline-offset: 4px; }
@media (hover: hover) and (pointer: fine) {
  .app:hover .icon { transform: translateY(-3px) scale(1.06); }
  .cell.is-down .app:hover .icon { transform: scale(0.94); }
}

/* ---------- picking an app up ---------- */
.is-dragging, .is-dragging * { cursor: grabbing !important; }
.is-dragging .grid .cell:not(.is-placeholder) .app { pointer-events: none; }
.cell.is-placeholder .icon { opacity: 0.24; transform: scale(0.88); box-shadow: none; }
.cell.is-placeholder .label { opacity: 0.35; }
.drag-ghost {
  position: fixed; left: 0; top: 0; z-index: 60; margin: 0; pointer-events: none;
  transform: translate3d(0, 0, 0) scale(1.12); will-change: transform;
  filter: drop-shadow(0 1.1rem 1.4rem rgba(0, 0, 0, 0.5));
}
.drag-ghost .icon { transform: none !important; box-shadow: 0 0 0 3px var(--ring), 0 1rem 1.8rem -0.6rem var(--b); }

/* ---------- empty / status ---------- */
.empty {
  margin: 1.5rem 0; padding: 2rem 1.25rem; text-align: center; color: var(--muted);
  border: 1px dashed var(--line); border-radius: 1.4rem; background: var(--surface);
}
.empty strong { display: block; margin-bottom: 0.3rem; color: var(--text); font-size: 1.05rem; }
.note {
  display: flex; align-items: center; gap: 0.6rem;
  margin: 0 0 0.6rem; padding: 0.55rem 0.85rem; border-radius: 0.9rem; font-size: 0.85rem;
  color: var(--muted); background: var(--surface); border: 1px solid var(--line);
}
.note .glyph { flex: none; width: 1.1rem; height: 1.1rem; color: var(--warn); }

/* ---------- details dock ---------- */
.dock {
  position: fixed; z-index: 30; left: var(--gutter); right: var(--gutter);
  bottom: calc(0.75rem + env(safe-area-inset-bottom));
  max-height: 52vh; max-height: 52dvh; display: flex; flex-direction: column; gap: 0.7rem;
  padding: 0.25rem 0.9rem 0.85rem; border-radius: 1.7rem;
  overflow: hidden;
  background: var(--glass); border: 1px solid var(--line); box-shadow: var(--shadow);
  -webkit-backdrop-filter: blur(26px) saturate(1.6); backdrop-filter: blur(26px) saturate(1.6);
  transition: transform 0.3s cubic-bezier(0.2, 0.9, 0.25, 1.1), max-height 0.25s, padding 0.25s, gap 0.25s;
}
.dock-content { display: flex; flex: 1 1 auto; flex-direction: column; gap: 0.7rem; min-height: 0; overflow: hidden; }
.dock-grab { align-self: center; display: grid; flex: 0 0 1.4rem; place-items: center; width: 4.5rem; height: 1.4rem; color: var(--faint); }
.dock-grab i { display: block; width: 2.4rem; height: 0.28rem; border-radius: 99px; background: currentColor; opacity: 0.5; }
.dock-head { display: flex; align-items: center; gap: 0.85rem; min-width: 0; }
.dock .icon { width: 3.6rem; border-radius: 23%; }
.dock-text { flex: 1; min-width: 0; }
.dock-title { display: flex; align-items: center; flex-wrap: wrap; gap: 0.2rem 0.55rem; }
.dock h2 { margin: 0; font-size: calc(1.12rem * var(--sd)); line-height: 1.15; font-weight: 720; letter-spacing: -0.012em; }
.pill {
  display: inline-flex; align-items: center; gap: 0.3rem; padding: 0.08rem 0.6rem; border-radius: 99px;
  font-size: 0.72rem; font-weight: 650; color: var(--muted); background: var(--surface-hi);
}
.pill.warn { color: var(--warn); background: color-mix(in srgb, var(--warn) 15%, transparent); }
.pill.ok { color: var(--ok); background: color-mix(in srgb, var(--ok) 15%, transparent); }
.dock-body { min-height: 0; overflow-y: auto; -webkit-overflow-scrolling: touch; padding-right: 0.1rem; }
.dock-desc {
  margin: 0; color: var(--muted); font-size: calc(0.93rem * var(--sd)); line-height: 1.45;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden;
}
.dock[data-expanded="1"] .dock-desc { display: block; -webkit-line-clamp: unset; }
.dock-meta { display: none; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.7rem; }
.dock[data-expanded="1"] .dock-meta { display: flex; }
.dock-actions { display: flex; gap: 0.6rem; }
.btn {
  flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem;
  min-height: 2.9rem; padding: 0 1rem; border-radius: 1rem; text-decoration: none; white-space: nowrap;
  font-size: 0.95rem; font-weight: 680; border: 1px solid var(--line); background: var(--surface-hi);
  transition: transform 0.15s, filter 0.15s;
}
.btn .glyph { width: 1.15rem; height: 1.15rem; }
.btn:active { transform: scale(0.97); }
.btn.primary { flex: 1.25; color: var(--accent-ink); background: var(--accent); border-color: transparent; }
.btn.primary[aria-disabled="true"] { filter: grayscale(1) opacity(0.5); pointer-events: none; }
.dock-hint { display: none; margin: 0; font-size: 0.78rem; color: var(--faint); text-align: center; }
.is-dragging .dock-hint { color: var(--accent); }

@media (max-width: 61.999rem) {
  .dock[data-collapsed="1"] { max-height: 2rem; gap: 0; padding: 0 0.9rem; }
  .dock[data-collapsed="1"] .dock-content { visibility: hidden; }
  .dock[data-collapsed="1"] .dock-grab { touch-action: none; }
}

/* ---------- appearance sheet ---------- */
.sheet {
  position: fixed; z-index: 45; top: calc(4.6rem + env(safe-area-inset-top));
  right: var(--gutter); width: min(24rem, calc(100% - var(--gutter) * 2));
  display: grid; gap: 1.05rem; padding: 1.1rem; border-radius: 1.5rem;
  background: color-mix(in srgb, var(--bg) 93%, transparent); border: 1px solid var(--line); box-shadow: var(--shadow);
  -webkit-backdrop-filter: blur(26px) saturate(1.6); backdrop-filter: blur(26px) saturate(1.6);
  animation: rise 0.22s cubic-bezier(0.2, 0.9, 0.25, 1.1);
}
@keyframes rise { from { opacity: 0; transform: translateY(-8px) scale(0.98); } }
.sheet h3 { margin: 0 0 0.5rem; font-size: 0.78rem; letter-spacing: 0.07em; text-transform: uppercase; color: var(--faint); font-weight: 700; }
.seg { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 0.3rem; padding: 0.25rem; border-radius: 1rem; background: var(--surface); }
.seg button {
  min-height: 2.8rem; border-radius: 0.8rem; color: var(--muted); font-weight: 650;
  display: grid; place-items: center; transition: background 0.15s, color 0.15s;
}
.seg button[aria-checked="true"] { background: var(--text); color: var(--bg); }
.seg.sizes button { font-weight: 720; line-height: 1; }
.sheet .row { display: flex; gap: 0.6rem; align-items: center; justify-content: space-between; }
.sheet .row .link { flex: none; white-space: nowrap; }
.sheet small { color: var(--faint); font-size: 0.78rem; line-height: 1.35; }
.link { color: var(--accent); font-weight: 650; min-height: 2.5rem; padding: 0 0.3rem; }
.link.armed { color: var(--warn); }

.toast {
  position: fixed; z-index: 70; left: 50%; bottom: calc(var(--dock-h) + 1.6rem + env(safe-area-inset-bottom));
  max-width: min(26rem, calc(100% - 2rem)); padding: 0.7rem 1.1rem; border-radius: 99px;
  font-size: 0.9rem; font-weight: 620; color: var(--bg); background: var(--text); box-shadow: var(--shadow);
  transform: translate(-50%, 140%); opacity: 0; transition: transform 0.3s cubic-bezier(0.2, 0.9, 0.25, 1.1), opacity 0.2s;
  pointer-events: none;
}
.toast.show { transform: translate(-50%, 0); opacity: 1; }
.is-pending .grid { opacity: 0; }
.grid { transition: opacity 0.18s; }

/* ---------- wide screens: the dock becomes a side panel ---------- */
@media (min-width: 62rem) {
  .layout { display: grid; grid-template-columns: minmax(0, 1fr) 21.5rem; gap: 2rem; align-items: start; }
  .shell { padding-bottom: 3rem; }
  .dock {
    position: sticky; z-index: 5; left: auto; right: auto; bottom: auto; top: 5.6rem;
    max-height: calc(100vh - 7rem); margin-top: 0.4rem; padding: 1.2rem; gap: 1rem;
  }
  .dock-grab { display: none; }
  .dock-hint { display: block; }
  .dock .icon { width: 4.6rem; }
  .dock-desc { display: block; -webkit-line-clamp: unset; font-size: calc(0.97rem * var(--sd)); }
  .dock-meta { display: flex; }
  .toast { bottom: 2rem; }
  .sheet { top: 5.2rem; }
}
@media (min-width: 44rem) and (max-width: 62rem) {
  .dock { left: 50%; right: auto; width: min(34rem, calc(100% - 2rem)); transform: translateX(-50%); }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.001ms !important; animation-iteration-count: 1 !important; transition-duration: 0.001ms !important; }
}
@media (forced-colors: active) {
  .icon { border: 2px solid CanvasText; }
  .cell.is-focus .icon { outline: 3px solid Highlight; }
}
"""
