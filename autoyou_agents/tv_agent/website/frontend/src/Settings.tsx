import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ArrowLeft, Check, ChevronRight, Copy, Folder, FolderPlus, KeyRound, LockKeyhole, Plus, RefreshCw, ShieldCheck, Trash2, Users } from 'lucide-react';
import { api, ApiError, copyText, errorMessage, navigate, query, rememberSession, shareUrl } from './api';
import { Loading, Modal } from './components';
import type { Config, Invitation, Notify, Settings as SettingsData } from './types';

export default function Settings({ config, onAuthenticated, onChanged, notify }: { config: Config; onAuthenticated: () => void; onChanged: () => void; notify: Notify }) {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('general');
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => { setRevision(value => value + 1); onChanged(); }, [onChanged]);
  useEffect(() => {
    if (!config.auth.otp_unlocked) { setSettings(null); return; }
    const controller = new AbortController();
    api<SettingsData>('settings', { signal: controller.signal }).then(value => { setSettings(value); setError(''); }).catch(error => {
      if (controller.signal.aborted) return;
      setError(errorMessage(error)); if (error instanceof ApiError && error.status === 401) onAuthenticated();
    });
    return () => controller.abort();
  }, [config.auth.otp_unlocked, revision, onAuthenticated]);
  return <div className="page-content settings-page"><div className="page-heading heading-row"><div><p className="eyebrow">Make it yours</p><h1>Library Settings</h1><p>A few thoughtful choices. Everything else is ready to watch.</p></div>
    {config.auth.otp_unlocked && <span className="role-badge"><ShieldCheck size={16} />OTP verified</span>}</div>
    {!config.auth.otp_unlocked ? <OtpGate onAuthenticated={onAuthenticated} /> : <>
      <div className="settings-tabs" aria-label="Settings section">{[['general', KeyRound, 'General'], ['sources', Folder, 'Source folders'], ['people', Users, 'People']].map(([id, Icon, label]) => {
        const Glyph = Icon as typeof KeyRound; return <button key={String(id)} className={tab === id ? 'active' : ''} aria-pressed={tab === id} onClick={() => setTab(String(id))}><Glyph size={18} />{String(label)}</button>;
      })}</div>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {!settings ? <Loading /> : tab === 'general' ? <General key={revision} settings={settings} reload={reload} notify={notify} /> : tab === 'sources' ?
        <Sources settings={settings} reload={reload} notify={notify} /> : <People settings={settings} reload={reload} notify={notify} />}
    </>}
  </div>;
}

function OtpGate({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const code = String(new FormData(event.currentTarget).get('code') || '').replace(/\s/g, '');
    try { const result = await api<{ token: string }>('auth/login', { method: 'POST', json: { code } }); rememberSession(result.token); onAuthenticated(); }
    catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  return <section className="otp-gate panel"><span className="otp-lock"><LockKeyhole size={32} /></span><p className="eyebrow">Just for the library owner</p><h2>A quick check.<br />Then it's all yours.</h2>
    <p>Enter the current code from your AutoYou authenticator to manage folders, IMDb matching and editor invitations.</p>
    <form className="form" onSubmit={submit}><label>Authenticator code<input className="otp-input" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} placeholder="000000" required autoFocus /></label>
      {error && <p className="inline-error" role="alert">{error}</p>}<button className="button primary" disabled={busy}><ShieldCheck size={18} />{busy ? 'Verifying...' : 'Unlock Settings'}</button></form>
    <p className="small muted">Watching and browsing remain open. This unlock lasts 30 minutes.</p>
  </section>;
}

function General({ settings, reload, notify }: { settings: SettingsData; reload: () => void; notify: Notify }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); const form = new FormData(event.currentTarget);
    const key = String(form.get('key') || '').trim();
    try { await api('settings', { method: 'POST', json: { library_name: form.get('name'), autoplay_next: form.get('autoplay') === 'on', adaptive_streaming: form.get('adaptive') === 'on', ...(key ? { omdb_api_key: key } : {}) } }); notify('Settings saved.'); reload(); }
    catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  async function enrich() {
    setBusy(true); try { const result = await api<{ queued: number }>('settings/enrich', { method: 'POST', json: {} }); notify(`${result.queued} titles queued for IMDb matching.`); }
    catch (error) { notify(errorMessage(error), true); } finally { setBusy(false); }
  }
  return <section className="panel settings-panel"><div className="panel-heading"><KeyRound size={22} /><h2>The essentials</h2></div><form className="form" onSubmit={submit}>
    <label>Library name<input name="name" defaultValue={settings.library_name} required maxLength={100} /></label>
    <label className="check-row"><span><strong>Play the next episode</strong><small>Continue a series when an episode ends.</small></span><input type="checkbox" name="autoplay" defaultChecked={settings.autoplay_next} /></label>
    <label className="check-row"><span><strong>Optimize streaming automatically</strong><small>Prepare smaller video segments for new uploads and scans. Requires FFmpeg, FFprobe and additional disk space.</small></span><input type="checkbox" name="adaptive" defaultChecked={settings.adaptive_streaming} /></label>
    <hr /><div className="panel-heading"><h3>IMDb details, automatically.</h3>{settings.omdb_configured && <span className="status-pill"><Check size={14} />Connected</span>}</div>
    <p className="muted">AutoYou TV uses OMDb to find posters, plots, cast and IMDb ratings for your titles.</p>
    <label>OMDb API key<input name="key" type="password" autoComplete="off" maxLength={512} placeholder={settings.omdb_configured ? 'A key is saved. Leave blank to keep it.' : 'Paste your OMDb API key'} /></label>
    <p className="field-help">Saved keys use this server's OS credential store. <a href="https://www.omdbapi.com/apikey.aspx" target="_blank" rel="noreferrer">Get an OMDb key</a>.</p>
    {error && <p className="inline-error" role="alert">{error}</p>}<div className="button-row"><button className="button primary" disabled={busy}>{busy ? 'Saving...' : 'Save Settings'}</button>
      {settings.omdb_configured && <button type="button" className="button" disabled={busy} onClick={enrich}><RefreshCw size={17} />Match existing titles</button>}</div>
  </form></section>;
}

function Sources({ settings, reload, notify }: { settings: SettingsData; reload: () => void; notify: Notify }) {
  const [path, setPath] = useState(''), [picker, setPicker] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); const node = event.currentTarget;
    try { await api('settings/sources', { method: 'POST', json: { path, name: new FormData(node).get('name') || '' } }); notify('Source added. Its videos are being indexed.'); node.reset(); setPath(''); reload(); }
    catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  async function action(id: string, remove = false) {
    try { await api(remove ? `settings/sources/${id}` : `folders/${id}/scan`, { method: remove ? 'DELETE' : 'POST', ...(remove ? {} : { json: {} }) }); notify(remove ? 'Source removed from the library. Its files are preserved.' : 'Folder scan queued.'); reload(); }
    catch (error) { notify(errorMessage(error), true); }
  }
  return <><section className="panel settings-panel"><div className="panel-heading"><FolderPlus size={23} /><h2>Bring your folders together</h2></div><p className="muted">Choose a folder on the server. Its videos and subfolders become available in your library.</p>
    <form className="form" onSubmit={submit}><label>Folder on this computer<div className="input-action"><input value={path} onChange={event => setPath(event.target.value)} required maxLength={4096} placeholder="Choose a folder or enter its path" />
      <button type="button" className="button" onClick={() => setPicker(true)}><Folder size={17} />Browse</button></div></label>
      <label>Library label<input name="name" placeholder="Movies, family videos, a favorite series..." maxLength={200} /></label>
      {error && <p className="inline-error" role="alert">{error}</p>}<button className="button primary" disabled={busy}><Plus size={18} />{busy ? 'Adding folder...' : 'Add source folder'}</button></form></section>
    <section className="source-list"><div className="section-heading"><h2>Your source folders</h2><span className="muted">{settings.sources.length} connected</span></div>
      {!settings.sources.length ? <p className="muted">Your first source will appear here.</p> : settings.sources.map(source => <article className="source-row" key={source.id}><span className="folder-mark"><Folder size={24} /></span><div><h3>{source.name}</h3><p>{source.root_path}</p></div>
        <button className="icon-button" aria-label={`Rescan ${source.name}`} onClick={() => action(source.id)}><RefreshCw size={18} /></button><button className="icon-button danger" aria-label={`Remove source ${source.name}`} onClick={() => action(source.id, true)}><Trash2 size={18} /></button></article>)}
    </section>{picker && <FolderPicker initial={path} onClose={() => setPicker(false)} onChoose={value => { setPath(value); setPicker(false); }} />}</>;
}

interface BrowseData { path: string; parent: string | null; directories: { name: string; path: string }[]; locations: { label: string; path: string }[]; truncated: boolean }
function FolderPicker({ initial, onClose, onChoose }: { initial: string; onClose: () => void; onChoose: (path: string) => void }) {
  const [path, setPath] = useState(initial), [data, setData] = useState<BrowseData | null>(null), [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setError('');
    api<BrowseData>('settings/browse?' + query({ path }), { signal: controller.signal }).then(setData).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [path]);
  return <Modal title="Choose a server folder" onClose={onClose} wide><p className="muted">Browse folders on the computer running this library.</p>
    {data && <><div className="picker-locations">{data.locations.map(location => <button className="button compact" key={location.path} onClick={() => setPath(location.path)}>{location.label}</button>)}</div>
      <div className="picker-path"><button className="icon-button" aria-label="Parent folder" disabled={!data.parent} onClick={() => setPath(data.parent!)}><ArrowLeft size={19} /></button><span>{data.path}</span></div>
      <div className="picker-list">{data.directories.length ? data.directories.map(directory => <button key={directory.path} onClick={() => setPath(directory.path)}><Folder size={21} /><span>{directory.name}</span><ChevronRight size={17} /></button>) : <p className="muted">This folder has no subfolders.</p>}</div>
      {data.truncated && <p className="field-help">Showing the first 400 folders. Enter a path above to reach another folder.</p>}
      <button className="button primary" onClick={() => onChoose(data.path)}><Check size={18} />Use this folder</button></>}
    {!data && !error && <Loading />}{error && <p className="inline-error" role="alert">{error}</p>}
  </Modal>;
}

function People({ settings, reload, notify }: { settings: SettingsData; reload: () => void; notify: Notify }) {
  const [link, setLink] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); const form = new FormData(event.currentTarget);
    try { const result = await api<{ token: string }>('settings/invitations', { method: 'POST', json: { label: form.get('label'), role: form.get('role'), days: Number(form.get('days')) } });
      const url = new URL(shareUrl('studio')); url.hash = `invite=${result.token}`; setLink(url.href); notify('Invitation created. Copy the link to share it.'); reload(); }
    catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  async function revoke(invitation: Invitation) {
    try { await api(`settings/invitations/${invitation.id}`, { method: 'DELETE' }); notify('Invitation revoked. Its active sessions have ended.'); reload(); }
    catch (error) { notify(errorMessage(error), true); }
  }
  return <><section className="panel settings-panel"><div className="panel-heading"><Users size={23} /><h2>Give your people a place</h2></div><p className="muted">Editors add and organize content. Admins have the same tools; Settings always requires OTP.</p>
    <form className="form" onSubmit={submit}><label>Invitation label<input name="label" placeholder="A person or team" required maxLength={100} /></label>
      <div className="form-grid"><label>Role<select name="role"><option value="editor">Editor</option><option value="admin">Admin</option></select></label><label>Expires after<select name="days" defaultValue={7}>{[1, 7, 14, 30].map(days => <option key={days} value={days}>{days} {days === 1 ? 'day' : 'days'}</option>)}</select></label></div>
      {error && <p className="inline-error" role="alert">{error}</p>}<button className="button primary" disabled={busy}><Plus size={18} />{busy ? 'Creating...' : 'Create invitation'}</button></form>
    {link && <div className="invitation-created"><label>Invitation link<input readOnly value={link} onFocus={event => event.target.select()} /></label><button className="button" onClick={() => { void copyText(link).then(() => notify('Invitation link copied.')).catch(error => notify(errorMessage(error), true)); }}><Copy size={17} />Copy link</button></div>}
  </section><section className="invitation-list"><div className="section-heading"><h2>Invitations</h2></div>{!settings.invitations.length ? <p className="muted">No invitations yet.</p> : settings.invitations.map(invitation => <article className="invitation-row" key={invitation.id}>
    <span className="person-mark"><Users size={21} /></span><div><h3>{invitation.label}</h3><p>{invitation.role} . {invitation.revoked ? 'Revoked' : invitation.expires * 1000 < Date.now() ? 'Expired' : `Expires ${new Date(invitation.expires * 1000).toLocaleDateString('en-US')}`}</p></div>
    {!invitation.revoked && <button className="button compact" onClick={() => revoke(invitation)}>Revoke</button>}</article>)}</section></>;
}
