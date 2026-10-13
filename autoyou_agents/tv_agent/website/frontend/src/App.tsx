import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, Bookmark, Check, ChevronRight, Clapperboard, Copy, Film, Folder as FolderIcon, Grid2X2, House, LayoutList, LogOut, Play, Plus, RefreshCw, Search, Settings as SettingsIcon, Share2, X } from 'lucide-react';
import { api, ApiError, copyText, errorMessage, navigate, query, rememberSession, shareUrl } from './api';
import { Artwork, Empty, FolderCard, ItemMeta, Loading, MediaCard, Modal, Shelf } from './components';
import Detail from './Detail';
import Player from './Player';
import Settings from './Settings';
import Studio from './Studio';
import { usePersonal, useRoute } from './hooks';
import type { Config, Folder, FolderPage, Item, LibraryPage, Notify } from './types';

const defaultConfig: Config = { library_name: 'AutoYou TV', autoplay_next: true, auth: { role: 'viewer', otp_unlocked: false }, capabilities: { download: false, transcode: false, probe: false } };
const views = [{ id: 'watch', label: 'Watch', icon: House }, { id: 'library', label: 'Library', icon: Film }, { id: 'folders', label: 'Folders', icon: FolderIcon }, { id: 'watchlist', label: 'Watchlist', icon: Bookmark }];

export default function App() {
  const route = useRoute(), [view, routeId] = route.split('/');
  const activeView = view === 'folder' ? 'folders' : view === 'item' || view === 'play' ? 'library' : view;
  const [config, setConfig] = useState(defaultConfig), [configError, setConfigError] = useState('');
  const [revision, setRevision] = useState(0), [allFolders, setAllFolders] = useState<Folder[]>([]);
  const [page, setPage] = useState<LibraryPage | null>(null), [loading, setLoading] = useState(true), [loadError, setLoadError] = useState('');
  const [folderPage, setFolderPage] = useState<FolderPage>({ folders: [], breadcrumbs: [] });
  const [folderLoading, setFolderLoading] = useState(true);
  const [detail, setDetail] = useState<Item | null>(null), [detailError, setDetailError] = useState('');
  const [continueItems, setContinueItems] = useState<Item[]>([]);
  const [searchOpen, setSearchOpen] = useState(false), [search, setSearch] = useState(''), [debouncedSearch, setDebouncedSearch] = useState('');
  const [kind, setKind] = useState(''), [genre, setGenre] = useState(''), [sort, setSort] = useState('recent'), [list, setList] = useState(false);
  const [folderModal, setFolderModal] = useState(false), [share, setShare] = useState<{ title: string; path: string } | null>(null);
  const [toast, setToast] = useState<{ message: string; error: boolean } | null>(null);
  const toastTimer = useRef<number | undefined>(undefined), inviteConsumed = useRef(false);
  const personal = usePersonal();
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); }, [route]);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const notify: Notify = useCallback((message, error = false) => {
    setToast({ message, error }); clearTimeout(toastTimer.current); toastTimer.current = window.setTimeout(() => setToast(null), error ? 8000 : 4500);
  }, []);
  useEffect(() => () => clearTimeout(toastTimer.current), []);
  useEffect(() => { const timer = setTimeout(() => setDebouncedSearch(search), 220); return () => clearTimeout(timer); }, [search]);

  useEffect(() => {
    if (inviteConsumed.current || !window.location.hash.startsWith('#invite=')) return;
    inviteConsumed.current = true;
    const token = new URLSearchParams(window.location.hash.slice(1)).get('invite') || '';
    window.history.replaceState(null, '', shareUrl('studio'));
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    api<{ token: string }>('auth/invite', { method: 'POST', json: { token } }).then(result => {
      rememberSession(result.token); notify('Your editor access is ready.'); refresh();
    }).catch(error => notify(errorMessage(error), true));
  }, [notify, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    const load = () => api<Config>('config', { signal: controller.signal }).then(value => { setConfig(value); setConfigError(''); document.title = value.library_name; }).catch(error => { if (!controller.signal.aborted) setConfigError(errorMessage(error)); });
    void load();
    api<FolderPage>('folders?all=true', { signal: controller.signal }).then(value => setAllFolders(value.folders)).catch(error => { if (!controller.signal.aborted) notify(errorMessage(error), true); });
    const timer = setInterval(load, 30000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [revision, notify]);

  const folderId = view === 'folder' ? routeId : '';
  const watchIds = personal.watchlist.join(',');
  const libraryParams = query({ q: view === 'library' || view === 'folder' ? debouncedSearch : '', kind: view === 'library' ? kind : '',
    genre: view === 'library' ? genre : '', sort: view === 'library' || view === 'folder' ? sort : 'recent',
    folder_id: folderId, ids: view === 'watchlist' ? watchIds : '', limit: view === 'watchlist' ? 200 : 100 });
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setLoadError('');
    if (view === 'watchlist' && !watchIds) { setPage({ items: [], count: 0, total: 0, offset: 0, has_more: false, genres: [] }); setLoading(false); return; }
    api<LibraryPage>('library?' + libraryParams, { signal: controller.signal }).then(value => { setPage(value); setLoading(false); }).catch(error => {
      if (!controller.signal.aborted) { setLoadError(errorMessage(error)); setLoading(false); }
    });
    return () => controller.abort();
  }, [libraryParams, view === 'watchlist', revision]);

  useEffect(() => {
    const controller = new AbortController();
    if (view === 'folder' || view === 'folders') {
      setFolderLoading(true);
      api<FolderPage>('folders?' + query({ parent_id: folderId }), { signal: controller.signal }).then(value => { setFolderPage(value); setFolderLoading(false); }).catch(error => { if (!controller.signal.aborted) { setLoadError(errorMessage(error)); setFolderLoading(false); } });
    }
    return () => controller.abort();
  }, [folderId, view, revision]);

  useEffect(() => {
    if (!['item', 'play'].includes(view) || !routeId) { setDetail(null); setDetailError(''); return; }
    const controller = new AbortController(); setDetail(null); setDetailError('');
    api<Item>(`items/${encodeURIComponent(routeId)}`, { signal: controller.signal }).then(setDetail).catch(error => { if (!controller.signal.aborted) setDetailError(errorMessage(error)); });
    return () => controller.abort();
  }, [routeId, view, revision]);

  const continuingIds = Object.entries(personal.progress).filter(([, progress]) => !progress.completed && progress.position > 5)
    .sort((a, b) => b[1].updated - a[1].updated).slice(0, 12).map(([id]) => id).join(',');
  useEffect(() => {
    if (!continuingIds) { setContinueItems([]); return; }
    const controller = new AbortController();
    api<LibraryPage>('library?' + query({ ids: continuingIds, limit: 100 }), { signal: controller.signal }).then(value => {
      const order = continuingIds.split(','); setContinueItems(value.items.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id)));
    }).catch(() => {});
    return () => controller.abort();
  }, [continuingIds, revision]);

  async function more() {
    if (!page || !page.has_more) return; setLoading(true);
    try { const next = await api<LibraryPage>('library?' + libraryParams + `&offset=${page.items.length}`); setPage(old => old ? { ...next, items: [...old.items, ...next.items] } : next); }
    catch (error) { notify(errorMessage(error), true); } finally { setLoading(false); }
  }
  async function logout() { try { await api('auth/logout', { method: 'POST', json: {} }); rememberSession(''); setConfig(old => ({ ...old, auth: { role: 'viewer', otp_unlocked: false } })); refresh(); notify('Signed out of editor access.'); } catch (error) { notify(errorMessage(error), true); } }
  function go(id: string) { setSearchOpen(false); setSearch(''); setDebouncedSearch(''); navigate(id); window.scrollTo({ top: 0, behavior: 'instant' }); }
  function toggle(id: string) { const saved = personal.watchlist.includes(id); personal.toggle(id); notify(saved ? 'Removed from your watchlist.' : 'Added to your watchlist.'); }
  const roots = allFolders.filter(folder => !folder.parent_id), items = page?.items || [], canEdit = config.auth.role !== 'viewer';
  const currentFolder = allFolders.find(folder => folder.id === folderId);

  return <div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>Skip to content</a>
    <header className="site-header"><button className="wordmark" onClick={() => go('watch')}>{config.library_name === 'AutoYou TV' ? <>AutoYou <span>TV</span></> : config.library_name}</button>
      <nav className="desktop-nav" aria-label="Main navigation">{views.map(({ id, label }) => <button key={id} className={activeView === id ? 'active' : ''} aria-current={activeView === id ? 'page' : undefined} onClick={() => go(id)}>{label}</button>)}</nav>
      <div className="header-actions"><button className={`icon-button ${searchOpen ? 'active' : ''}`} aria-label="Search library" aria-expanded={searchOpen} onClick={() => setSearchOpen(value => !value)}><Search size={21} /></button>
        <button className={`studio-link ${view === 'studio' ? 'active' : ''}`} aria-label="Studio" onClick={() => go('studio')}><Clapperboard size={18} /><span>Studio</span></button>
        <button className={`icon-button ${view === 'settings' ? 'active' : ''}`} aria-label="Settings" onClick={() => go('settings')}><SettingsIcon size={21} /></button>
        {canEdit && <button className="icon-button signout-button" aria-label="Sign out of editor access" onClick={logout}><LogOut size={19} /></button>}
      </div>
    </header>
    {searchOpen && <div className="global-search"><Search size={20} /><input aria-label="Search all titles" placeholder="Find a movie, series or moment..." value={search} autoFocus
      onChange={event => { setSearch(event.target.value); if (view !== 'library') navigate('library'); }} onKeyDown={event => { if (event.key === 'Escape') setSearchOpen(false); }} />
      <button className="icon-button" aria-label="Close search" onClick={() => setSearchOpen(false)}><X size={20} /></button></div>}
    <main id="main-content" tabIndex={-1}>
      {configError && <div className="connection-error" role="alert"><span>{configError}</span><button className="text-button" onClick={refresh}>Retry</button></div>}
      {view === 'watch' && <>
        <HomeHero item={items[0]} saved={items[0] && personal.watchlist.includes(items[0].id)} onSave={toggle} loading={loading} />
        <div className="page-content home-content">
          {loadError ? <ErrorState message={loadError} retry={refresh} /> : loading && !page ? <Loading cards /> : <>
            <Shelf title="Pick up where you left off" subtitle="Your next quiet moment starts here." items={continueItems} watchlist={personal.watchlist} onSave={toggle} progress={personal.progress} landscape />
            <Shelf title="New in your library" items={items.slice(0, 12)} watchlist={personal.watchlist} onSave={toggle} onMore={() => go('library')} />
            <Shelf title="Made for a movie night" items={items.filter(item => item.kind === 'movie').slice(0, 10)} watchlist={personal.watchlist} onSave={toggle} onMore={() => { go('library'); setKind('movie'); }} />
            <Shelf title="One more episode" items={items.filter(item => item.kind === 'episode').slice(0, 10)} watchlist={personal.watchlist} onSave={toggle} onMore={() => { go('library'); setKind('episode'); }} landscape />
          </>}
          {!!roots.length && <section className="shelf"><div className="section-heading"><div><h2>Your folders</h2><p>Every collection has a place.</p></div><button className="text-button" onClick={() => go('folders')}>Explore all <ChevronRight size={16} /></button></div>
            <div className="folder-grid">{roots.slice(0, 6).map(folder => <FolderCard key={folder.id} folder={folder} onShare={() => setShare({ title: folder.name, path: `folder/${folder.id}` })} />)}</div></section>}
        </div>
      </>}
      {view === 'library' && <div className="page-content"><div className="page-heading"><p className="eyebrow">A world worth exploring</p><h1>Your library</h1><p>{page?.total || 0} stories. Always yours.</p></div>
        <div className="library-toolbar"><div className="search-field"><Search size={18} /><input aria-label="Search titles" placeholder="Search your library" value={search} onChange={event => setSearch(event.target.value)} />{search && <button className="icon-button" aria-label="Clear search" onClick={() => setSearch('')}><X size={17} /></button>}</div>
          <label className="select-filter"><span className="sr-only">Genre</span><select aria-label="Filter by genre" value={genre} onChange={event => setGenre(event.target.value)}><option value="">All genres</option>{(page?.genres || []).map(value => <option key={value}>{value}</option>)}</select></label>
          <label className="select-filter"><span className="sr-only">Sort</span><select aria-label="Sort library" value={sort} onChange={event => setSort(event.target.value)}><option value="recent">Recently added</option><option value="title">Title A-Z</option><option value="year">Release year</option><option value="rating">IMDb rating</option></select></label>
          <button className="icon-button" aria-label={list ? 'Show grid' : 'Show list'} onClick={() => setList(value => !value)}>{list ? <Grid2X2 size={19} /> : <LayoutList size={20} />}</button></div>
        <div className="filter-chips" aria-label="Content type">{[['', 'Everything'], ['movie', 'Movies'], ['episode', 'Episodes'], ['video', 'Personal videos']].map(([id, label]) => <button key={id} className={kind === id ? 'active' : ''} aria-pressed={kind === id} onClick={() => setKind(id)}>{label}</button>)}</div>
        <LibraryResults page={page} loading={loading} error={loadError} retry={refresh} list={list} watchlist={personal.watchlist} onSave={toggle} more={more} onAdd={() => go('studio')} />
      </div>}
      {(view === 'folders' || view === 'folder') && <div className="page-content folders-page">
        <div className="breadcrumbs"><button onClick={() => go('folders')}>Folders</button>{folderPage.breadcrumbs.map(folder => <span key={folder.id}><ChevronRight size={14} /><button onClick={() => navigate(`folder/${folder.id}`)}>{folder.name}</button></span>)}</div>
        <div className="page-heading heading-row"><div><p className="eyebrow">Room for every story</p><h1>{currentFolder?.name || 'Your folders'}</h1><p>{view === 'folder' ? 'A collection to come back to.' : 'Browse your sources and collections, just as you organized them.'}</p></div>
          <div className="button-row">{canEdit && <button className="button" onClick={() => setFolderModal(true)}><Plus size={18} />New folder</button>}
            {view === 'folder' && <button className="icon-button" aria-label="Share this folder" onClick={() => setShare({ title: currentFolder?.name || 'Folder', path: `folder/${folderId}` })}><Share2 size={20} /></button>}
            {canEdit && currentFolder?.source && <button className="icon-button" aria-label="Rescan this folder" onClick={() => { void api(`folders/${folderId}/scan`, { method: 'POST', json: {} }).then(() => notify('Folder scan queued in Studio.')).catch(error => notify(errorMessage(error), true)); }}><RefreshCw size={19} /></button>}</div>
        </div>
        {loadError && <ErrorState message={loadError} retry={refresh} />}
        {folderLoading ? <Loading /> : <div className="folder-grid">{folderPage.folders.map(folder => <FolderCard key={folder.id} folder={folder} onShare={() => setShare({ title: folder.name, path: `folder/${folder.id}` })} />)}</div>}
        {!folderLoading && !loadError && !folderPage.folders.length && view === 'folders' && <Empty title="A place for every collection" description="Connect a source folder in Settings or create a collection in Studio." icon={<FolderIcon size={34} />}><button className="button" onClick={() => go('settings')}>Connect a source</button>{canEdit && <button className="button primary" onClick={() => setFolderModal(true)}>New folder</button>}</Empty>}
        {view === 'folder' && <section className="folder-titles"><div className="section-heading"><h2>Videos in this folder</h2><button className="icon-button" aria-label={list ? 'Show grid' : 'Show list'} onClick={() => setList(value => !value)}>{list ? <Grid2X2 size={19} /> : <LayoutList size={20} />}</button></div>
          <LibraryResults page={page} loading={loading} error="" retry={refresh} list={list} watchlist={personal.watchlist} onSave={toggle} more={more} onAdd={() => go('studio')} />
        </section>}
      </div>}
      {view === 'watchlist' && <div className="page-content"><div className="page-heading"><p className="eyebrow">For another moment</p><h1>Your watchlist</h1><p>A few good stories, saved for when you're ready. Kept on this browser.</p></div>
        {loading ? <Loading cards /> : loadError ? <ErrorState message={loadError} retry={refresh} /> : !items.length ? <Empty title="Something to look forward to" description="Save a title from your library. It will be right here when the time is right."><button className="button primary" onClick={() => go('library')}>Explore your library</button></Empty> :
          <div className="media-grid">{items.map(item => <MediaCard key={item.id} item={item} saved onSave={toggle} progress={personal.progress[item.id]} />)}</div>}
      </div>}
      {view === 'studio' && <Studio config={config} folders={allFolders} onChanged={refresh} onAuthenticated={refresh} onFolder={() => setFolderModal(true)} notify={notify} />}
      {view === 'settings' && <Settings config={config} onAuthenticated={refresh} onChanged={refresh} notify={notify} />}
      {(view === 'item' || view === 'play') && (detailError ? <div className="page-content"><ErrorState message={detailError} retry={refresh} /><button className="button" onClick={() => go('library')}><ArrowLeft size={18} />Back to library</button></div> : !detail ? <Loading /> :
        <Detail item={detail} config={config} folders={allFolders} saved={personal.watchlist.includes(detail.id)} progress={personal.progress[detail.id]} onSave={() => toggle(detail.id)} onShare={() => setShare({ title: detail.title, path: `item/${detail.id}` })} onChanged={refresh} notify={notify} />)}
      {!['watch', 'library', 'folders', 'folder', 'watchlist', 'studio', 'settings', 'item', 'play'].includes(view) && <Empty title="This page has moved" description="Your library is one click away."><button className="button" onClick={() => go('watch')}>Go to Watch</button></Empty>}
    </main>
    <footer className="site-footer"><span>AutoYou TV</span><p>Your library. On your terms.</p><button className="text-button" onClick={() => setShare({ title: config.library_name, path: 'watch' })}><Share2 size={15} />Share library</button></footer>
    <nav className="mobile-nav" aria-label="Mobile navigation">{views.map(({ id, label, icon: Icon }) => <button key={id} className={activeView === id ? 'active' : ''} aria-current={activeView === id ? 'page' : undefined} onClick={() => go(id)}><Icon size={22} strokeWidth={1.7} /><span>{label}</span></button>)}</nav>
    {view === 'play' && detail && <Player key={detail.id} item={detail} config={config} progress={personal.progress[detail.id]} record={personal.record} notify={notify} />}
    {folderModal && <NewFolder parent={view === 'folder' ? folderId : ''} folders={allFolders} onClose={() => setFolderModal(false)} onChanged={refresh} notify={notify} />}
    {share && <Share title={share.title} path={share.path} onClose={() => setShare(null)} notify={notify} />}
    {toast && <div className={`toast ${toast.error ? 'toast-error' : ''}`} role={toast.error ? 'alert' : 'status'}>{toast.error ? <X size={18} /> : <Check size={18} />}<span>{toast.message}</span><button aria-label="Dismiss notification" onClick={() => setToast(null)}><X size={16} /></button></div>}
  </div>;
}

function HomeHero({ item, saved, onSave, loading }: { item?: Item; saved?: boolean; onSave: (id: string) => void; loading: boolean }) {
  return <section className={`home-hero ${!item ? 'empty-hero' : ''}`}>
    {item && <Artwork item={item} className="hero-backdrop" eager />}
    <div className="hero-shade" /><div className="hero-content"><p className="eyebrow">{item ? 'Tonight, make it a good story.' : 'Your own personal cinema'}</p>
      <h1>{item ? item.title : <>Stories worth<br />keeping.</>}</h1>{item ? <ItemMeta item={item} large /> : <div className="hero-tags"><span>Movies</span><span>Series</span><span>Your own moments</span></div>}
      <p className="hero-plot">{item ? item.metadata.plot || 'From your collection to your favorite screen. Settle in and press play.' : 'Bring your movies, shows and memories together. A beautiful home for everything you love to watch.'}</p>
      <div className="button-row">{item ? <><button className="button primary hero-play" disabled={item.status === 'missing'} onClick={() => navigate(`play/${item.id}`)}><Play size={20} fill="currentColor" />Watch now</button>
        <button className="button glass" onClick={() => navigate(`item/${item.id}`)}>More info<ChevronRight size={18} /></button><button className={`icon-button glass ${saved ? 'is-saved' : ''}`} aria-label={saved ? 'Remove featured title from watchlist' : 'Add featured title to watchlist'} aria-pressed={saved} onClick={() => onSave(item.id)}>{saved ? <Check size={20} /> : <Plus size={22} />}</button></> :
        <><button className="button primary" onClick={() => navigate('settings')}><FolderIcon size={19} />Connect your first folder</button><button className="button glass" onClick={() => navigate('studio')}><Plus size={19} />Add a video</button></>}
      </div>{!item && <p className="hero-note">{loading ? 'Opening your library...' : 'Your library is ready for its first story.'}</p>}
    </div>{!item && <div className="empty-hero-decoration" aria-hidden="true"><div /><div /><div /><Film size={80} strokeWidth={.7} /></div>}
  </section>;
}

function LibraryResults({ page, loading, error, retry, list, watchlist, onSave, more, onAdd }: {
  page: LibraryPage | null; loading: boolean; error: string; retry: () => void; list: boolean; watchlist: string[]; onSave: (id: string) => void; more: () => void; onAdd: () => void;
}) {
  if (error) return <ErrorState message={error} retry={retry} />;
  if (loading && !page) return <Loading cards />;
  if (!page?.items.length) return loading ? <Loading cards /> : <Empty title={page?.total ? 'No titles found' : 'Your next favorite is waiting'} description={page?.total ? 'Try another search, genre or content type.' : 'Bring in a video from Studio or connect a source folder in Settings.'} icon={<Film size={33} />}><button className="button" onClick={onAdd}>Open Studio</button></Empty>;
  return <><p className="result-count">{page.count} {page.count === 1 ? 'title' : 'titles'}</p>{list ? <div className="title-list">{page.items.map(item => <article key={item.id} className="title-row"><button className="row-art" aria-label={`Details for ${item.title}`} onClick={() => navigate(`item/${item.id}`)}><Artwork item={item} /></button>
    <button className="row-title" onClick={() => navigate(`item/${item.id}`)}><strong>{item.title}</strong><span>{item.technical.file_name || item.metadata.genre || 'Linked stream'}</span></button><div className="row-meta"><ItemMeta item={item} /></div>
    <button className="icon-button" aria-label={`Watch ${item.title}`} onClick={() => navigate(`play/${item.id}`)}><Play size={19} /></button><button className="icon-button" aria-label={`Toggle ${item.title} in watchlist`} aria-pressed={watchlist.includes(item.id)} onClick={() => onSave(item.id)}>{watchlist.includes(item.id) ? <Check size={19} /> : <Plus size={19} />}</button></article>)}</div> :
      <div className={`media-grid ${loading ? 'updating' : ''}`}>{page.items.map(item => <MediaCard key={item.id} item={item} saved={watchlist.includes(item.id)} onSave={onSave} />)}</div>}
    {page.has_more && <div className="load-more"><button className="button" disabled={loading} onClick={more}>{loading ? 'Loading...' : 'Show more titles'}</button></div>}</>;
}
function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return <div className="error-state" role="alert"><p>{message}</p><button className="button" onClick={retry}><RefreshCw size={17} />Try again</button></div>;
}

function NewFolder({ parent, folders, onClose, onChanged, notify }: { parent: string; folders: Folder[]; onClose: () => void; onChanged: () => void; notify: Notify }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); const form = new FormData(event.currentTarget);
    try { await api('folders', { method: 'POST', json: { name: form.get('name'), parent_id: form.get('parent') || null } }); notify('Folder created.'); onChanged(); onClose(); }
    catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  return <Modal title="A new place for your stories" onClose={onClose}><form className="form" onSubmit={submit}><label>Folder name<input name="name" placeholder="Give your collection a name" required maxLength={200} autoFocus /></label>
    <label>Inside<select name="parent" defaultValue={parent}><option value="">Library root</option>{folders.map(folder => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label>
    <p className="field-help">This creates a library collection. Add existing server folders from Settings.</p>{error && <p className="inline-error" role="alert">{error}</p>}<button className="button primary" disabled={busy}><Plus size={18} />{busy ? 'Creating...' : 'Create folder'}</button></form></Modal>;
}
function Share({ title, path, onClose, notify }: { title: string; path: string; onClose: () => void; notify: Notify }) {
  const url = shareUrl(path);
  const [copied, setCopied] = useState(false), [error, setError] = useState('');
  return <Modal title={`Share ${title}`} onClose={onClose}><p className="muted">Anyone who can reach this library can browse and watch.</p><label className="share-field">Viewer link<input readOnly value={url} onFocus={event => event.target.select()} /></label>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <button className="button primary" onClick={() => { setError(''); void copyText(url).then(() => { setCopied(true); notify('Viewer link copied.'); }).catch(error => setError(errorMessage(error))); }}>{copied ? <Check size={18} /> : <Copy size={18} />}{copied ? 'Link copied' : 'Copy viewer link'}</button></Modal>;
}
