import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Check, Clapperboard, Download, Link, Plus, RefreshCw, Upload, X } from 'lucide-react';
import { api, apiUrl, authHeaders, errorMessage, navigate, rememberSession, uploadFile } from './api';
import { Empty, Loading } from './components';
import type { Config, Folder, Item, Job, Notify } from './types';

export default function Studio({ config, folders, onChanged, onAuthenticated, onFolder, notify }: {
  config: Config; folders: Folder[]; onChanged: () => void; onAuthenticated: () => void; onFolder: () => void; notify: Notify;
}) {
  const [tab, setTab] = useState('link');
  if (config.auth.role === 'viewer') return <div className="page-content"><div className="page-heading"><p className="eyebrow">Behind the scenes</p><h1>Studio</h1></div>
    <InviteGate onAuthenticated={onAuthenticated} notify={notify} /></div>;
  return <div className="page-content studio-page">
    <div className="page-heading heading-row"><div><p className="eyebrow">Behind the scenes</p><h1>Your library, curated.</h1><p>Add a link, bring in a video, or give every story its place.</p></div>
      <span className="role-badge"><span />{config.auth.role === 'admin' ? 'Admin' : 'Editor'} access</span></div>
    <div className="studio-grid"><section className="panel import-panel"><div className="panel-heading"><Clapperboard size={23} /><h2>Add to your library</h2></div>
      <div className="segmented" aria-label="Import method"><button aria-pressed={tab === 'link'} className={tab === 'link' ? 'active' : ''} onClick={() => setTab('link')}><Link size={17} />From a link</button>
        <button aria-pressed={tab === 'upload'} className={tab === 'upload' ? 'active' : ''} onClick={() => setTab('upload')}><Upload size={17} />Upload a video</button></div>
      {tab === 'link' ? <LinkImport folders={folders} canDownload={config.capabilities.download} onChanged={onChanged} notify={notify} /> :
        <UploadImport folders={folders} onChanged={onChanged} notify={notify} />}
    </section><aside className="panel studio-aside"><p className="eyebrow">A little organization</p><h2>Make room for more.</h2><p>Keep movies, series and personal videos together in folders that make sense to you.</p>
      <button className="button" onClick={onFolder}><Plus size={18} />New folder</button><hr /><h3>Already on this computer?</h3>
      <p>Add a source folder in Settings. AutoYou TV will discover its videos, subfolders and episode names.</p><button className="text-button" onClick={() => navigate('settings')}>Open Settings</button>
      <hr /><p className="small muted">IMDb matches appear automatically when the library has an OMDb key. You can correct any match from a title's editor tools.</p>
    </aside></div>
    <JobList onChanged={onChanged} notify={notify} />
  </div>;
}

function FolderSelect({ folders }: { folders: Folder[] }) {
  return <label>Folder<select name="folder"><option value="">No folder</option>{folders.map(folder => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label>;
}
function TypeSelect() { return <label>Type<select name="kind"><option value="movie">Movie</option><option value="episode">Episode</option><option value="video">Personal video</option></select></label>; }

function LinkImport({ folders, canDownload, onChanged, notify }: { folders: Folder[]; canDownload: boolean; onChanged: () => void; notify: Notify }) {
  const [mode, setMode] = useState('stream');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const node = event.currentTarget; const form = new FormData(node);
    setBusy(true); setError('');
    try {
      const result = await api<{ item?: Item; job?: Job }>('items', { method: 'POST', json: { url: form.get('url'), title: form.get('title'),
        folder_id: form.get('folder') || null, kind: form.get('kind'), year: form.get('year') ? Number(form.get('year')) : null, mode } });
      notify(result.item ? 'Added to your library.' : 'Download queued. You can follow its progress below.');
      node.reset(); onChanged();
    } catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  return <form className="form" onSubmit={submit}>
    <label>Media link<input name="url" type="url" placeholder="https://..." required maxLength={4096} /></label>
    <div className="mode-options"><label className={mode === 'stream' ? 'selected' : ''}><input type="radio" name="mode" checked={mode === 'stream'} onChange={() => setMode('stream')} /><Link size={18} /><span><strong>Stream</strong><small>Play a direct MP4, WebM or HLS link.</small></span></label>
      <label className={mode === 'download' ? 'selected' : ''}><input type="radio" name="mode" checked={mode === 'download'} onChange={() => setMode('download')} /><Download size={18} /><span><strong>Download</strong><small>Save from YouTube, Vimeo or archive.org.</small></span></label></div>
    {mode === 'download' && !canDownload && <p className="inline-error">Downloads need yt-dlp on the server. Direct media links can be streamed now.</p>}
    <label>Title<input name="title" placeholder="Give this story a name" maxLength={300} /></label>
    <div className="form-grid"><TypeSelect /><label>Year<input name="year" type="number" min={1880} max={2200} placeholder="Optional" /></label></div>
    <FolderSelect folders={folders} />
    {error && <p className="inline-error" role="alert">{error}</p>}
    <button className="button primary" disabled={busy || (mode === 'download' && !canDownload)}>{busy ? 'Adding...' : mode === 'download' ? 'Queue download' : 'Add to library'}<Plus size={18} /></button>
  </form>;
}

function UploadImport({ folders, onChanged, notify }: { folders: Folder[]; onChanged: () => void; notify: Notify }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const cancel = useRef<(() => void) | null>(null);
  useEffect(() => () => cancel.current?.(), []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!file) return;
    const node = event.currentTarget, form = new FormData(node);
    setBusy(true); setError(''); setProgress(0);
    const task = uploadFile(file, String(form.get('folder') || ''), String(form.get('title') || ''), String(form.get('kind')), setProgress);
    cancel.current = task.cancel;
    try { await task.promise; notify('Video uploaded and added to your library.'); setFile(null); node.reset(); onChanged(); }
    catch (error) { setError(errorMessage(error)); } finally { cancel.current = null; setBusy(false); }
  }
  return <form className="form" onSubmit={submit}>
    <label className="upload-drop"><Upload size={29} /><strong>{file?.name || 'Choose a video'}</strong><span>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB . Ready to upload` : 'MP4, WebM, MOV, MKV and more . Up to 20 GB'}</span>
      <input aria-label="Video file" type="file" accept="video/*,.mkv,.m4v,.avi,.ts,.mpg,.mpeg,.ogv" disabled={busy} onChange={event => { setFile(event.target.files?.[0] || null); setError(''); }} /></label>
    <label>Title<input name="title" placeholder="Uses the file name if left blank" maxLength={300} disabled={busy} /></label><div className="form-grid"><TypeSelect /><FolderSelect folders={folders} /></div>
    {busy && <div className="upload-progress" role="status"><progress value={progress} max={1} /><span>{progress < 1 ? `Uploading . ${Math.round(progress * 100)}%` : 'Finishing your upload...'} </span><button type="button" className="text-button" onClick={() => cancel.current?.()}>Cancel</button></div>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <button className="button primary" disabled={!file || busy || file.size > 20 * 1024 ** 3}><Upload size={18} />{busy ? 'Uploading...' : 'Upload to library'}</button>
  </form>;
}

export function InviteGate({ onAuthenticated, notify }: { onAuthenticated: () => void; notify: Notify }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const raw = String(new FormData(event.currentTarget).get('invite') || '').trim();
    let token = raw;
    if (raw.includes('invite=')) token = new URLSearchParams(raw.slice(raw.indexOf('invite='))).get('invite') || '';
    try { const result = await api<{ token: string }>('auth/invite', { method: 'POST', json: { token } }); rememberSession(result.token); notify('Studio is ready.'); onAuthenticated(); }
    catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  return <div className="invite-gate panel"><Clapperboard size={36} /><h2>A little access. A lot of possibility.</h2><p>Viewers can browse and watch freely. Use an editor invitation to add and organize content.</p>
    <form className="form" onSubmit={submit}><label>Editor invitation<input name="invite" placeholder="Paste your invitation link or code" required maxLength={4096} autoComplete="off" /></label>
      {error && <p className="inline-error" role="alert">{error}</p>}<button className="button primary" disabled={busy}>{busy ? 'Opening Studio...' : 'Open Studio'}</button></form>
    <p className="small muted">Own this library? <button className="inline-link" onClick={() => navigate('settings')}>Unlock Settings</button> with your authenticator to create invitations.</p>
  </div>;
}

export function JobList({ onChanged, notify }: { onChanged: () => void; notify: Notify }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState('');
  const seen = useRef(new Map<string, string>());
  const receive = useCallback((rows: Job[]) => {
    let changed = false;
    for (const job of rows) { if (seen.current.has(job.id) && seen.current.get(job.id) !== job.status && job.status === 'completed') changed = true; seen.current.set(job.id, job.status); }
    setJobs(rows); setError(''); if (changed) onChanged();
  }, [onChanged]);
  useEffect(() => {
    const controller = new AbortController();
    async function poll() { try { const result = await api<{ jobs: Job[] }>('jobs', { signal: controller.signal }); receive(result.jobs); } catch (error) { if (!controller.signal.aborted) setError(errorMessage(error)); } }
    void poll();
    // Polling also covers data proxies that do not expose a streaming fetch body.
    const timer = window.setInterval(poll, 10000);
    async function events() {
      try {
        const response = await fetch(apiUrl('stream/jobs/events'), { headers: authHeaders(), credentials: 'same-origin', signal: controller.signal });
        if (!response.ok || !response.body) return;
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let buffer = '';
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read(); if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const packets = buffer.split('\n\n'); buffer = packets.pop() || '';
          for (const packet of packets) {
            const data = packet.split('\n').find(line => line.startsWith('data: '));
            if (data) receive(JSON.parse(data.slice(6)));
          }
        }
      } catch { /* The ordinary status endpoint keeps imports visible. */ }
    }
    void events();
    return () => { controller.abort(); clearInterval(timer); };
  }, [receive]);
  async function action(job: Job, operation: 'retry' | 'cancel') {
    try { await api(`jobs/${job.id}/${operation}`, { method: 'POST', json: {} }); const result = await api<{ jobs: Job[] }>('jobs'); receive(result.jobs); notify(operation === 'retry' ? 'Import queued again.' : 'Import cancelled.'); }
    catch (error) { notify(errorMessage(error), true); }
  }
  return <section className="job-section"><div className="section-heading"><div><p className="eyebrow">In motion</p><h2>Imports & activity</h2></div><span className="live-label"><span />Live updates</span></div>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {!jobs ? <Loading /> : !jobs.length ? <Empty title="Ready for your next addition" description="Downloads, scans and IMDb matches will appear here as you build your library." icon={<Download size={29} />} /> :
      <div className="job-list">{jobs.map(job => <article key={job.id} className={`job-row job-${job.status}`}>
        <span className="job-icon">{job.status === 'completed' ? <Check size={20} /> : job.status === 'running' ? <span className="spinner" /> : job.status === 'failed' ? <X size={20} /> : <Download size={20} />}</span>
        <div className="job-description"><strong>{job.payload.title || ({ metadata: 'IMDb match', prepare: 'Video preparation', scan: 'Folder scan', transcode: 'Browser version', download: 'Video download' }[job.kind]) || job.kind}</strong>
          <p>{job.message || 'Waiting to start'}</p>{job.status === 'running' && <progress value={job.progress} max={1} />}</div><span className="job-status">{job.status}</span>
        {(job.status === 'queued' || job.status === 'running') ? <button className="icon-button" aria-label="Cancel import" onClick={() => action(job, 'cancel')}><X size={18} /></button> :
          (job.status === 'failed' || job.status === 'cancelled') ? <button className="icon-button" aria-label="Retry import" onClick={() => action(job, 'retry')}><RefreshCw size={18} /></button> :
            job.result.item_id ? <button className="text-button" onClick={() => navigate(`item/${job.result.item_id}`)}>View</button> : null}
      </article>)}</div>}
  </section>;
}
