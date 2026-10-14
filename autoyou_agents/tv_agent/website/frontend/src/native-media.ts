import { assetUrl } from './api';

export interface Playback { version: number; item_id: string; title: string; url: string; fallback_url: string;
  ready?: boolean; message?: string; preparation?: {status: string; progress: number};
  mime_type: string; is_hls: boolean; optimized: boolean; qualities: number[];
  subtitles: { id: string; label: string; language: string }[] }
export interface NativeState { requestId: string; itemId: string; state: 'opened' | 'playing' | 'paused' | 'ended' | 'closed' | 'error';
  position?: number; duration?: number; message?: string }
declare global {
  interface Window {
    AutoYouNativeMedia?: { open: (payload: string) => void; close?: (payload: string) => void; available?: () => boolean };
    AutoYouNativeMediaAndroid?: { open: (payload: string) => boolean; close?: (payload: string) => void };
    webkit?: { messageHandlers?: { autoyouNativeMedia?: { postMessage: (payload: unknown) => void } } };
  }
}

export function nativeMediaAvailable() {
  if (!['127.0.0.1', 'localhost', '::1', '[::1]', 'autoyou.localhost'].includes(window.location.hostname)) return false;
  if (window.AutoYouNativeMedia?.available && !window.AutoYouNativeMedia.available()) return false;
  return !!(window.AutoYouNativeMedia || window.AutoYouNativeMediaAndroid || window.webkit?.messageHandlers?.autoyouNativeMedia);
}

export function openNativeMedia(playback: Playback, position: number, onState: (state: NativeState) => void) {
  const requestId = crypto.randomUUID();
  const payload = { version: 1, requestId, itemId: playback.item_id, title: playback.title,
    url: assetUrl(playback.url), fallbackUrl: assetUrl(playback.fallback_url), mimeType: playback.mime_type,
    position: Math.max(0, position), documentUrl: window.location.href,
    subtitles: playback.subtitles.map(track => ({ ...track, url: assetUrl(`api/subtitles/${playback.item_id}/${track.id}`) })) };
  let opened = false;
  const listener = (event: Event) => {
    const state = (event as CustomEvent<NativeState>).detail;
    if (!state || state.requestId !== requestId || state.itemId !== playback.item_id) return;
    if (state.state === 'opened') opened = true;
    onState(state);
  };
  window.addEventListener('autoyou-native-media-state', listener);
  const timer = window.setTimeout(() => { if (!opened) onState({ requestId, itemId: playback.item_id, state: 'error', message: 'The native player could not open. Playing in the browser.' }); }, 5000);
  try {
    const text = JSON.stringify(payload);
    if (window.AutoYouNativeMedia) window.AutoYouNativeMedia.open(text);
    else if (window.AutoYouNativeMediaAndroid) {
      if (!window.AutoYouNativeMediaAndroid.open(text)) throw new Error('Native playback is unavailable for this connection.');
    } else window.webkit?.messageHandlers?.autoyouNativeMedia?.postMessage(payload);
  } catch (error) {
    window.clearTimeout(timer); window.removeEventListener('autoyou-native-media-state', listener); throw error;
  }
  return () => {
    window.clearTimeout(timer); window.removeEventListener('autoyou-native-media-state', listener);
    const close = JSON.stringify({ requestId });
    window.AutoYouNativeMedia?.close?.(close); window.AutoYouNativeMediaAndroid?.close?.(close);
    window.webkit?.messageHandlers?.autoyouNativeMedia?.postMessage({ op: 'close', requestId });
  };
}
