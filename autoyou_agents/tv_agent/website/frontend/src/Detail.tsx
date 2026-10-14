import { useState } from 'react';
import { Bookmark, Check, ExternalLink, Film, Play, Share2 } from 'lucide-react';
import { api, errorMessage, navigate } from './api';
import { Artwork, ItemMeta, Modal } from './components';
import type { Config, Folder, Item, Notify, Progress } from './types';

export default function Detail({ item, config, folders, saved, progress, onSave, onShare, onChanged, notify }: {
  item: Item; config: Config; folders: Folder[]; saved: boolean; progress?: Progress; onSave: () => void;
  onShare: () => void; onChanged: () => void; notify: Notify;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const canEdit = config.auth.role !== 'viewer';
  async function action(path: string, json?: unknown) {
    setBusy(true);
    try { await api(path, { method: 'POST', json: json ?? {} }); notify('Added to the Studio queue.'); }
    catch (error) { notify(errorMessage(error), true); } finally { setBusy(false); }
  }
  const canResume = progress && !progress.completed && progress.position > 5;
  return <>
    <section className="detail-hero"><Artwork item={item} className="detail-backdrop" eager />
      <div className="detail-hero-content"><p className="eyebrow">{item.kind === 'episode' ? item.series || 'Your shows' : 'In your library'}</p><h1>{item.title}</h1>
        <ItemMeta item={item} large /><p className="detail-plot">{item.metadata.plot || 'This title is part of your personal library. Press play and make yourself comfortable.'}</p>
        <div className="button-row"><button className="button primary" onClick={() => navigate(`play/${item.id}`)} disabled={item.status === 'missing'}><Play size={19} fill="currentColor" />{item.kind === 'photo' ? 'View photo' : canResume ? 'Resume watching' : 'Watch now'}</button>
          <button className={`button ${saved ? 'saved-button' : ''}`} onClick={onSave}>{saved ? <Check size={19} /> : <Bookmark size={19} />}{saved ? 'In watchlist' : 'Add to watchlist'}</button>
          <button className="icon-button glass" aria-label={`Share ${item.title}`} onClick={onShare}><Share2 size={20} /></button></div>
        {item.status === 'missing' && <p className="inline-error">The source file is missing. An editor can rescan its source folder.</p>}
      </div>
    </section>
    <div className="page-content detail-body">
      {!!item.episodes?.length && <section><div className="section-heading"><h2>Episodes</h2><span className="muted">{item.episodes.length} in this library</span></div>
        <div className="episode-list">{item.episodes.map(episode => <button key={episode.id} className={`episode-row ${episode.id === item.id ? 'current' : ''}`} onClick={() => navigate(`play/${episode.id}`)}>
          <Artwork item={episode} /><span><small>SEASON {episode.season} . EPISODE {episode.episode}</small><strong>{episode.title}</strong><span>{episode.metadata.plot || 'Ready to watch'}</span></span><Play size={22} /></button>)}</div>
      </section>}
      <section className="detail-about"><div><h2>About this title</h2><p>{item.metadata.plot || 'IMDb details can be matched automatically when the library has an OMDb key.'}</p>
        {item.metadata.imdb_id && <a className="text-button" href={`https://www.imdb.com/title/${item.metadata.imdb_id}/`} target="_blank" rel="noreferrer">View on IMDb <ExternalLink size={16} /></a>}
      </div><dl>{item.metadata.director && <><dt>Director</dt><dd>{item.metadata.director}</dd></>}{item.metadata.actors && <><dt>Cast</dt><dd>{item.metadata.actors}</dd></>}
        <dt>From</dt><dd>{folders.find(folder => folder.id === item.folder_id)?.name || (item.is_remote ? 'Linked media' : 'Your library')}</dd>
        <dt>Format</dt><dd>{[item.is_hls ? 'HLS' : item.technical.extension?.slice(1).toUpperCase(), item.technical.video_codec?.toUpperCase(), item.technical.height ? `${item.technical.height}p` : null].filter(Boolean).join(' . ') || 'HTTP stream'}</dd>
        {item.subtitles?.length ? <><dt>Subtitles</dt><dd>{item.subtitles.map(track => track.label).join(', ')}</dd></> : null}
      </dl></section>
      {canEdit && <section className="editor-panel"><div><Film size={23} /><h2>Editor tools</h2></div><p className="muted">Correct details, match IMDb or prepare this video for more browsers.</p>
        {item.metadata.status !== 'matched' && <p className="muted">{item.metadata.message || 'IMDb details have not been matched yet.'}</p>}
        <div className="button-row"><button className="button" onClick={() => setEditing(true)}>Edit title</button>
          <button className="button" disabled={busy} onClick={() => action(`items/${item.id}/metadata`)}>Refresh IMDb details</button>
          {!item.is_remote && item.kind !== 'photo' && config.capabilities.transcode && <button className="button" disabled={busy} onClick={() => action(`items/${item.id}/transcode`)}>{item.technical.browser_ready ? 'Rebuild browser version' : 'Create browser version'}</button>}</div>
      </section>}
    </div>
    {editing && <EditTitle item={item} folders={folders} onClose={() => setEditing(false)} onChanged={onChanged} notify={notify} />}
  </>;
}

function EditTitle({ item, folders, onClose, onChanged, notify }: { item: Item; folders: Folder[]; onClose: () => void; onChanged: () => void; notify: Notify }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    setBusy(true); setError('');
    try {
      await api(`items/${item.id}`, { method: 'PATCH', json: { title: form.get('title'), kind: form.get('kind'),
        year: form.get('year') ? Number(form.get('year')) : null, folder_id: form.get('folder') || null,
        series: form.get('series') || null, season: form.get('season') ? Number(form.get('season')) : null, episode: form.get('episode') ? Number(form.get('episode')) : null } });
      const imdb = String(form.get('imdb') || '').trim();
      if (imdb) await api(`items/${item.id}/metadata`, { method: 'POST', json: { imdb_id: imdb } });
      notify('Title updated.'); onChanged(); onClose();
    } catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  return <Modal title="Edit title" onClose={onClose}><form className="form" onSubmit={submit}>
    <label>Title<input name="title" defaultValue={item.title} required maxLength={300} autoFocus /></label>
    <div className="form-grid"><label>Type<select name="kind" defaultValue={item.kind}>{item.kind === 'photo' ? <option value="photo">Photo</option> : <><option value="movie">Movie</option><option value="episode">Episode</option><option value="video">Video</option></>}</select></label>
      <label>Year<input name="year" type="number" min={1880} max={2200} defaultValue={item.year || ''} /></label></div>
    <label>Folder<select name="folder" defaultValue={item.folder_id || ''}><option value="">No folder</option>{folders.map(folder => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label>
    <label>Series<input name="series" defaultValue={item.series || ''} placeholder="For episodes" maxLength={300} /></label>
    <div className="form-grid"><label>Season<input name="season" type="number" min={0} max={999} defaultValue={item.season ?? ''} /></label>
      <label>Episode<input name="episode" type="number" min={0} max={9999} defaultValue={item.episode ?? ''} /></label></div>
    <label>IMDb ID<input name="imdb" placeholder="tt1234567" pattern="tt[0-9]{7,10}" maxLength={12} /></label>
    <p className="field-help">Leave the IMDb ID blank to keep the current match.</p>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <button className="button primary" disabled={busy}>{busy ? 'Saving...' : 'Save changes'}</button>
  </form></Modal>;
}
