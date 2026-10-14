import { useCallback, useEffect, useState } from 'react';
import type { ProgressMap } from './types';

const personalPrefix = `autoyou-tv:${window.location.pathname}:`;
function read<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(personalPrefix + key) || 'null') ?? fallback; } catch { return fallback; }
}
function save(key: string, value: unknown) { try { localStorage.setItem(personalPrefix + key, JSON.stringify(value)); } catch { /* Viewing remains usable in a private browser. */ } }

function savedWatchlist() {
  const value = read<unknown>('watchlist', []);
  return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string' && id.length <= 128).slice(-200) : [];
}
function savedProgress(): ProgressMap {
  const value = read<unknown>('progress', {});
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([, row]) => row && typeof row === 'object' &&
    Number.isFinite(row.position) && row.position >= 0 && Number.isFinite(row.duration) && row.duration > 0 &&
    Number.isFinite(row.updated) && typeof row.completed === 'boolean').slice(0, 200));
}

export function useRoute() {
  const [route, setRoute] = useState(() => window.location.hash.slice(2) || 'watch');
  useEffect(() => {
    const change = () => setRoute(window.location.hash.startsWith('#/') ? window.location.hash.slice(2) : 'watch');
    window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change);
  }, []);
  return route;
}

export function usePersonal() {
  const [watchlist, setWatchlist] = useState<string[]>(savedWatchlist);
  const [progress, setProgress] = useState<ProgressMap>(savedProgress);
  const toggle = useCallback((id: string) => setWatchlist(old => {
    const next = old.includes(id) ? old.filter(value => value !== id) : [...old, id].slice(-200);
    save('watchlist', next); return next;
  }), []);
  const record = useCallback((id: string, position: number, duration: number) => setProgress(old => {
    if (!Number.isFinite(position) || !Number.isFinite(duration) || duration <= 0) return old;
    const next = { ...old, [id]: { position, duration, completed: position >= duration - Math.min(10, duration * .05), updated: Date.now() } };
    const trimmed = Object.fromEntries(Object.entries(next).sort((a, b) => b[1].updated - a[1].updated).slice(0, 200));
    save('progress', trimmed); return trimmed;
  }), []);
  return { watchlist, progress, toggle, record };
}
