import type { Item } from './types';

const base = new URL('./', window.location.href);
const tokenKey = `autoyou-tv-session:${base.pathname}`;
let token = '';
try { token = sessionStorage.getItem(tokenKey) || ''; } catch { /* Cookies still work when storage is unavailable. */ }

export function rememberSession(value: string) {
  token = value;
  try { if (value) sessionStorage.setItem(tokenKey, value); else sessionStorage.removeItem(tokenKey); } catch { /* Keep the in-memory session. */ }
}
export function assetUrl(path: string) { return new URL(path, base).href; }
export function apiUrl(path: string) { return assetUrl(`api/${path}`); }
export function authHeaders(): Record<string, string> { return token ? { Authorization: `Bearer ${token}` } : {}; }
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }

export async function api<T>(path: string, options: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = options;
  const headers = new Headers(rest.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (json !== undefined) headers.set('Content-Type', 'application/json');
  const response = await fetch(apiUrl(path), {
    credentials: 'same-origin', ...rest,
    headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof body.detail === 'string' ? body.detail : body.error || 'This request could not be completed. Try again.';
    throw new ApiError(message, response.status);
  }
  return body as T;
}

export function query(values: Record<string, string | number | boolean | null | undefined>) {
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(values)) if (value !== '' && value !== undefined && value !== null) params.set(name, String(value));
  return params.toString();
}
export function navigate(path: string) { window.location.hash = `/${path}`; }
export function shareUrl(path: string) { const url = new URL(base); url.hash = `/${path}`; return url.href; }
export async function copyText(text: string) {
  if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
  const field = document.createElement('textarea');
  field.value = text; field.style.position = 'fixed'; field.style.opacity = '0';
  document.body.appendChild(field); field.select();
  const copied = document.execCommand('copy'); field.remove();
  if (!copied) throw new Error('Copy is unavailable in this browser. Select and copy the link.');
}
export function artwork(item: Item) { return item.artwork_url ? assetUrl(item.artwork_url) : item.metadata.poster || ''; }
export function kindLabel(kind: string) { return kind === 'photo' ? 'Photo' : kind === 'audio' ? 'Audio' : kind === 'document' ? 'Document' : kind === 'episode' ? 'Episode' : kind === 'movie' ? 'Movie' : 'Video'; }
export function duration(seconds = 0) { if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`; const minutes = Math.round(seconds / 60); return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`; }
export function clock(seconds = 0) { const minutes = Math.floor(seconds / 60); return `${Math.floor(minutes / 60) ? Math.floor(minutes / 60) + ':' : ''}${String(minutes % 60).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`; }
export function errorMessage(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Try again.'; }

export function uploadFile(file: File, folderId: string, title: string, kind: string, onProgress: (progress: number) => void) {
  type Upload = { id: string; received: number; size: number; chunk_bytes: number; item_id: string | null };
  const controller = new AbortController();
  let xhr: XMLHttpRequest | null = null;
  let uploadId = '';
  const resumeKey = `${tokenKey}:upload:${JSON.stringify([file.name, file.size, file.lastModified, folderId, title, kind])}`;
  function forget() { try { sessionStorage.removeItem(resumeKey); } catch { /* Storage is optional. */ } }
  function sendChunk(upload: Upload) {
    return new Promise<Upload>((resolve, reject) => {
      if (controller.signal.aborted) { reject(new Error('Upload cancelled.')); return; }
      const start = upload.received, blob = file.slice(start, start + upload.chunk_bytes);
      const request = new XMLHttpRequest(); xhr = request;
      request.open('PUT', apiUrl(`upload-sessions/${upload.id}?offset=${start}`));
      request.withCredentials = true;
      request.setRequestHeader('Content-Type', 'application/octet-stream');
      if (token) request.setRequestHeader('Authorization', `Bearer ${token}`);
      request.upload.onprogress = event => onProgress(Math.min(1, (start + event.loaded) / file.size));
      request.onerror = () => reject(new ApiError('Upload interrupted. Reselect the same file to resume.', 0));
      request.onabort = () => reject(new Error('Upload cancelled.'));
      request.onload = () => {
        let body: Upload & { detail?: string };
        try { body = JSON.parse(request.responseText); } catch { reject(new ApiError('Invalid upload response. Reselect the same file to resume.', 0)); return; }
        if (request.status >= 200 && request.status < 300) resolve(body);
        else reject(new ApiError(body.detail || 'Upload could not be completed.', request.status));
      };
      request.send(blob);
    });
  }
  const promise = (async () => {
    let savedId = '';
    try { savedId = sessionStorage.getItem(resumeKey) || ''; } catch { /* Start a fresh upload without browser storage. */ }
    let upload: Upload | null = null;
    if (savedId) {
      try { upload = await api<Upload>(`upload-sessions/${savedId}`, { signal: controller.signal }); }
      catch (error) { if (!(error instanceof ApiError) || error.status !== 404) throw error; forget(); }
    }
    if (!upload) upload = await api<Upload>('upload-sessions', { method: 'POST', signal: controller.signal,
      json: { filename: file.name, size: file.size, title, kind, folder_id: folderId || null } });
    uploadId = upload.id;
    try { sessionStorage.setItem(resumeKey, uploadId); } catch { /* The active upload still works. */ }
    if (controller.signal.aborted) {
      forget(); void api(`upload-sessions/${uploadId}`, { method: 'DELETE' }).catch(() => {});
      throw new Error('Upload cancelled.');
    }
    let retries = 0;
    while (upload.received < file.size) {
      try { upload = await sendChunk(upload); retries = 0; }
      catch (error) {
        if (controller.signal.aborted) throw new Error('Upload cancelled.');
        if (!(error instanceof ApiError) || ![0, 409, 502, 503, 504].includes(error.status) || ++retries > 3) throw error;
        upload = await api<Upload>(`upload-sessions/${uploadId}`, { signal: controller.signal });
      }
      onProgress(upload.received / file.size);
    }
    const result = await api<{ item: Item }>(`upload-sessions/${uploadId}/complete`, { method: 'POST', signal: controller.signal, json: {} });
    forget(); return result;
  })();
  return { promise, cancel: () => {
    controller.abort(); xhr?.abort(); forget();
    if (uploadId) void api(`upload-sessions/${uploadId}`, { method: 'DELETE' }).catch(() => {});
  } };
}
