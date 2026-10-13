import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Bookmark, Check, ChevronRight, Folder as FolderIcon, Play, Plus, Share2, X } from 'lucide-react';
import { artwork, duration, kindLabel, navigate } from './api';
import type { Folder, Item, Progress } from './types';

export function Modal({ title, onClose, children, wide = false, className = '' }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean; className?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const node = dialog.current!;
    node.showModal();
    return () => node.close();
  }, []);
  return <dialog ref={dialog} className={`modal ${wide ? 'modal-wide' : ''} ${className}`} aria-labelledby={id}
    onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="modal-content">
      <div className="modal-heading"><h2 id={id}>{title}</h2><button className="icon-button" aria-label="Close" onClick={onClose}><X size={22} /></button></div>
      {children}
    </div>
  </dialog>;
}

export function ItemMeta({ item, large = false }: { item: Item; large?: boolean }) {
  const meta = item.metadata;
  return <div className={`item-meta ${large ? 'meta-large' : ''}`}>
    {meta.rated && <span className="rating-label">{meta.rated}</span>}
    {item.year && <span>{item.year}</span>}
    {item.kind === 'episode' && <span>S{item.season ?? '?'} E{item.episode ?? '?'}</span>}
    {(meta.runtime || item.technical.duration) && <span>{meta.runtime || duration(item.technical.duration)}</span>}
    {meta.genre && <span>{meta.genre.split(',').slice(0, 2).join(', ')}</span>}
    {Number(meta.imdb_rating) > 0 && <span className="imdb-rating"><b>IMDb</b> {meta.imdb_rating}</span>}
    {(item.technical.height || 0) >= 2160 && <span className="quality-label">4K</span>}
    {!meta.genre && <span>{kindLabel(item.kind)}</span>}
  </div>;
}

export function Artwork({ item, className = '', eager = false }: { item: Item; className?: string; eager?: boolean }) {
  const url = artwork(item);
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  return <div className={`artwork tone-${parseInt(item.id.slice(0, 4), 16) % 4 || 0} ${className}`}>
    {url && !failed ? <img src={url} alt="" loading={eager ? 'eager' : 'lazy'} onError={() => setFailed(true)} /> :
      <div className="artwork-fallback"><span>{item.series || item.title}</span><small>{item.metadata.genre?.split(',')[0] || kindLabel(item.kind)}</small></div>}
  </div>;
}

export function MediaCard({ item, saved, onSave, progress, landscape = false }: {
  item: Item; saved: boolean; onSave: (id: string) => void; progress?: Progress; landscape?: boolean;
}) {
  return <article className={`media-card ${landscape ? 'landscape-card' : ''}`}>
    <div className="card-art">
      <button className="card-open" aria-label={`Details for ${item.title}`} onClick={() => navigate(`item/${item.id}`)}><Artwork item={item} /></button>
      {item.status === 'missing' && <span className="card-state">Source missing</span>}
      <div className="card-actions">
        <button className="card-play" aria-label={`Watch ${item.title}`} onClick={() => navigate(`play/${item.id}`)}><Play size={22} fill="currentColor" /></button>
        <button className={`card-save ${saved ? 'is-saved' : ''}`} aria-label={saved ? `Remove ${item.title} from watchlist` : `Save ${item.title} to watchlist`}
          aria-pressed={saved} onClick={() => onSave(item.id)}>{saved ? <Check size={19} /> : <Plus size={19} />}</button>
      </div>
      {progress && <div className="watch-progress" aria-label={`${Math.round(progress.position / progress.duration * 100)} percent watched`}>
        <span style={{ width: `${Math.min(100, progress.position / progress.duration * 100)}%` }} />
      </div>}
    </div>
    <div className="card-caption"><h3><button onClick={() => navigate(`item/${item.id}`)}>{item.title}</button></h3>
      <p>{progress && !progress.completed ? `${duration(Math.max(0, progress.duration - progress.position))} remaining` :
        [item.year, item.kind === 'episode' ? `S${item.season ?? '?'} E${item.episode ?? '?'}` : item.metadata.genre?.split(',')[0] || kindLabel(item.kind)].filter(Boolean).join(' . ')}</p>
    </div>
  </article>;
}

export function Shelf({ title, subtitle, items, watchlist, onSave, progress = {}, landscape = false, onMore }: {
  title: string; subtitle?: string; items: Item[]; watchlist: string[]; onSave: (id: string) => void;
  progress?: Record<string, Progress>; landscape?: boolean; onMore?: () => void;
}) {
  if (!items.length) return null;
  return <section className="shelf">
    <div className="section-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
      {onMore && <button className="text-button" onClick={onMore}>View all <ChevronRight size={16} /></button>}</div>
    <div className={`shelf-track ${landscape ? 'landscape-track' : ''}`}>{items.map(item => <MediaCard key={item.id} item={item}
      saved={watchlist.includes(item.id)} onSave={onSave} progress={progress[item.id]} landscape={landscape} />)}</div>
  </section>;
}

export function FolderCard({ folder, onShare }: { folder: Folder; onShare?: (folder: Folder) => void }) {
  return <article className="folder-card">
    <button className="folder-open" onClick={() => navigate(`folder/${folder.id}`)}>
      <span className="folder-mark"><FolderIcon size={30} strokeWidth={1.5} /></span>
      <span><strong>{folder.name}</strong><small>{folder.item_count || 0} {folder.item_count === 1 ? 'video' : 'videos'}{folder.folder_count ? ` . ${folder.folder_count} ${folder.folder_count === 1 ? 'folder' : 'folders'}` : ''}</small></span>
      <ChevronRight size={19} className="folder-chevron" />
    </button>
    {onShare && <button className="icon-button folder-share" aria-label={`Share ${folder.name}`} onClick={() => onShare(folder)}><Share2 size={17} /></button>}
  </article>;
}

export function Empty({ title, description, children, icon = <Bookmark size={32} /> }: {
  title: string; description: string; children?: ReactNode; icon?: ReactNode;
}) {
  return <div className="empty-state"><span className="empty-icon">{icon}</span><h2>{title}</h2><p>{description}</p>
    {children && <div className="button-row">{children}</div>}</div>;
}

export function Loading({ cards = false }: { cards?: boolean }) {
  return cards ? <div className="media-grid" aria-label="Loading library" aria-busy="true">{Array.from({ length: 6 }, (_, i) =>
    <div className="loading-card" key={i}><div /><span /><small /></div>)}</div> : <div className="loading-state" role="status"><span className="spinner" /> Loading your library...</div>;
}
