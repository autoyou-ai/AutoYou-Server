import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Download, Maximize, Play, RotateCcw, SkipForward } from 'lucide-react';
import type Hls from 'hls.js';
import { api, apiUrl, artwork, assetUrl, authHeaders, clock, errorMessage, navigate } from './api';
import { Modal } from './components';
import type { Config, Item, Notify, Progress } from './types';
import { nativeMediaAvailable, openNativeMedia, type Playback } from './native-media';

export default function Player({ item, config, progress, record, notify }: {
  item: Item; config: Config; progress?: Progress; record: (id: string, position: number, duration: number) => void; notify: Notify;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const startingProgress = useRef(progress);
  const resumed = useRef(false);
  const lastSaved = useRef(0);
  const [error, setError] = useState('');
  const [nonce, setNonce] = useState(0);
  const [ready, setReady] = useState(false);
  const [position, setPosition] = useState(0);
  const [quality, setQuality] = useState(-1);
  const [levels, setLevels] = useState<{ height: number; index: number }[]>([]);
  const [ended, setEnded] = useState(false);
  const [renditionMessage, setRenditionMessage] = useState('');
  const [native, setNative] = useState(false);
  const [browserOnly, setBrowserOnly] = useState(false);
  const [optimized, setOptimized] = useState(!!item.technical.streaming_ready);

  useEffect(() => {
    if (['photo', 'audio', 'document'].includes(item.kind)) return;
    const node = video.current!;
    const controller = new AbortController();
    let cancelled = false;
    let closeNativeListener: (() => void) | undefined;
    setError(''); setReady(false); resumed.current = false;
    async function load() {
      try {
        let playback = await api<Playback>(`items/${item.id}/playback?compatible=true`, { signal: controller.signal });
        while (!cancelled && playback.ready === false) {
          setRenditionMessage(playback.message || 'Preparing a compatible video…');
          if (['failed', 'cancelled'].includes(playback.preparation?.status || '')) { setError(playback.message || 'Video preparation failed. Ask an editor to retry in Studio.'); return; }
          await new Promise(resolve => setTimeout(resolve, 1000));
          if (cancelled) return;
          playback = await api<Playback>(`items/${item.id}/playback?compatible=true`, { signal: controller.signal });
        }
        setRenditionMessage('');
        if (!cancelled) setOptimized(playback.optimized);
        if (item.is_remote && !playback.is_hls) {
          const head = await fetch(assetUrl(playback.url), { method: 'HEAD', credentials: 'same-origin', headers: authHeaders(), signal: controller.signal });
          const mime = head.headers.get('Content-Type') || '';
          if (mime) playback.mime_type = mime.split(';')[0];
          playback.is_hls = mime.toLowerCase().includes('mpegurl');
        }
        if (cancelled) return;
        if (!browserOnly && nativeMediaAvailable()) {
          closeNativeListener = openNativeMedia(playback, startingProgress.current?.completed ? 0 : startingProgress.current?.position || 0, state => {
            if (cancelled) return;
            if (Number.isFinite(state.position) && Number.isFinite(state.duration) && state.duration! > 0) {
              startingProgress.current = { position: state.position!, duration: state.duration!, updated: Date.now(), completed: state.state === 'ended' };
              setPosition(state.position!); record(item.id, state.position!, state.duration!);
            }
            if (state.state === 'opened') { setNative(true); setReady(true); }
            if (state.state === 'ended') { setEnded(true); if (item.next_episode && config.autoplay_next) navigate(`play/${item.next_episode.id}`); }
            if (state.state === 'closed') navigate(`item/${item.id}`);
            if (state.state === 'error') { setNative(false); setBrowserOnly(true); notify(state.message || 'Native playback failed. Playing in the browser.'); }
          });
          return;
        }
        let isHls = playback.is_hls;
        if (item.is_remote && !isHls) {
          // Some signed or query-based HLS URLs have no .m3u8 extension.
          const head = await fetch(assetUrl(playback.url), { method: 'HEAD', credentials: 'same-origin', headers: authHeaders(), signal: controller.signal });
          isHls = (head.headers.get('Content-Type') || '').toLowerCase().includes('mpegurl');
        }
        if (cancelled) return;
        const source = assetUrl(playback.url);
        if (isHls && !node.canPlayType('application/vnd.apple.mpegurl')) {
          const { default: Hls } = await import('hls.js');
          if (cancelled) return;
          if (!Hls.isSupported()) { setError('This browser does not support HLS. Try a current browser or a downloaded MP4 version.'); return; }
          const player = new Hls({ enableWorker: true, backBufferLength: 20, maxBufferLength: 12,
            abrEwmaDefaultEstimate: 800000, startLevel: 0, capLevelToPlayerSize: true });
          hlsRef.current = player;
          player.on(Hls.Events.MANIFEST_PARSED, () => {
            setLevels(player.levels.map((level, index) => ({ index, height: level.height })));
            void node.play().catch(() => {});
          });
          player.on(Hls.Events.ERROR, (_event, data) => { if (data.fatal) setError('The HLS stream could not be played. Check the source or try again.'); });
          player.attachMedia(node); player.loadSource(source);
        } else node.src = source;
      } catch (error) { if (!cancelled) { if (!browserOnly && nativeMediaAvailable()) { setBrowserOnly(true); notify(errorMessage(error)); } else setError(errorMessage(error)); } }
    }
    void load();
    function keys(event: KeyboardEvent) {
      if ((event.target as HTMLElement).closest('input,select,textarea,button')) return;
      if (event.code === 'Space' || event.key.toLowerCase() === 'k') { event.preventDefault(); if (node.paused) void node.play().catch(() => {}); else node.pause(); }
      if (event.key === 'ArrowRight') { event.preventDefault(); node.currentTime = Math.min(node.duration || Infinity, node.currentTime + 10); }
      if (event.key === 'ArrowLeft') { event.preventDefault(); node.currentTime = Math.max(0, node.currentTime - 10); }
      if (event.key.toLowerCase() === 'm') node.muted = !node.muted;
      if (event.key.toLowerCase() === 'f') void node.requestFullscreen?.().catch(() => {});
    }
    window.addEventListener('keydown', keys);
    return () => {
      cancelled = true; controller.abort();
      closeNativeListener?.();
      if (resumed.current) record(item.id, node.currentTime, node.duration);
      window.removeEventListener('keydown', keys);
      hlsRef.current?.destroy(); hlsRef.current = null;
      node.pause(); node.removeAttribute('src'); node.load();
    };
  }, [item.id, nonce, browserOnly, record]);

  function loaded() {
    const node = video.current!;
    const old = startingProgress.current;
    if (!resumed.current && old && !old.completed && old.position > 0 && old.position < node.duration - .25) node.currentTime = old.position;
    resumed.current = true; setReady(true);
  }
  function update(force = false) {
    const node = video.current;
    if (!node || !resumed.current) return;
    setPosition(node.currentTime);
    if (force || Date.now() - lastSaved.current > 5000) { record(item.id, node.currentTime, node.duration); lastSaved.current = Date.now(); }
  }
  function complete() {
    update(true); setEnded(true);
    if (item.next_episode && config.autoplay_next) navigate(`play/${item.next_episode.id}`);
  }
  async function rendition() {
    try { await api(`items/${item.id}/streaming`, { method: 'POST', json: {} }); setRenditionMessage('Streaming quality is being optimized in Studio. Reopen this title when it is ready.'); notify('Streaming quality is being optimized in Studio.'); }
    catch (error) { setRenditionMessage(errorMessage(error)); }
  }
  if (item.kind === 'photo') return <Modal title={item.title} onClose={() => navigate(`item/${item.id}`)} wide>
    <div className="photo-stage"><img src={assetUrl(item.stream_url)} alt={item.title} /></div>
  </Modal>;
  if (item.kind === 'audio' || item.kind === 'document') return <Modal title={item.title} onClose={() => navigate(`item/${item.id}`)} wide>
    {item.kind === 'audio' ? <audio controls autoPlay preload="metadata" src={assetUrl(item.stream_url)} aria-label={`Audio player for ${item.title}`} />
      : item.technical.extension?.replace(/^\./, '').toLowerCase() === 'pdf'
        ? <iframe title={item.title} src={assetUrl(item.stream_url)} style={{ width: '100%', height: '70vh', border: 0 }} />
        : <p>Download this EPUB to read it in your device's book reader.</p>}
    {item.download_url && <a className="button" href={assetUrl(item.download_url)} download><Download size={19} />Download {item.kind === 'audio' ? 'audio' : 'document'}</a>}
  </Modal>;

  return <Modal title={item.title} onClose={() => navigate(`item/${item.id}`)} wide className="player-modal">
    <div className="player-header"><button className="text-button" onClick={() => navigate(`item/${item.id}`)}><ArrowLeft size={19} />Back to title</button>
      <span>{item.series ? `${item.series} . S${item.season} E${item.episode}` : 'Now playing'}</span></div>
    <div className="player-screen">
      {native && <div className="player-error" role="status"><p>{ended ? 'Playback ended. Your place is saved.' : 'Playing in the native player. Your place is saved as you watch.'}</p>
        <button className="button" onClick={() => { setNative(false); setBrowserOnly(true); }}>Play in browser</button></div>}
      <video ref={video} controls autoPlay playsInline preload="metadata" poster={artwork(item)} aria-label={`Video player for ${item.title}`}
        onLoadedMetadata={loaded} onTimeUpdate={() => update()} onPause={() => update(true)} onEnded={complete}
        onError={() => { if (video.current?.error) setError(video.current.error.code === 4 ? 'This video format is not supported by your browser. An editor can create a browser version.' : 'The video could not be loaded. Check its source and try again.'); }}>
        {(item.subtitles || []).map(track => <track key={track.id} kind="subtitles" src={apiUrl(`subtitles/${item.id}/${track.id}`)} label={track.label} srcLang={track.language} />)}
      </video>
      {error && <div className="player-error" role="alert"><p>{error}</p><div className="button-row">
        <button className="button" onClick={() => { setEnded(false); setNonce(n => n + 1); }}><RotateCcw size={18} />Try again</button>
        {config.auth.role !== 'viewer' && !item.is_remote && config.capabilities.transcode && <button className="button primary" onClick={rendition}>Optimize streaming</button>}
      </div>{renditionMessage && <p role="status">{renditionMessage}</p>}</div>}
    </div>
    <div className="player-tools"><span className="muted" role="status">{ready ? clock(position) : renditionMessage || 'Preparing your stream...'}</span>
      {config.auth.role !== 'viewer' && !item.is_remote && !optimized && config.capabilities.transcode && <button className="text-button" onClick={rendition}>Optimize streaming</button>}
      <label>Speed<select aria-label="Playback speed" defaultValue="1" onChange={event => { if (video.current) video.current.playbackRate = Number(event.target.value); }}>
        {[.5, .75, 1, 1.25, 1.5, 2].map(rate => <option key={rate} value={rate}>{rate}x</option>)}</select></label>
      {!!levels.length && <label>Quality<select aria-label="Video quality" value={quality} onChange={event => { const level = Number(event.target.value); setQuality(level); if (hlsRef.current) hlsRef.current.currentLevel = level; }}>
        <option value={-1}>Auto</option>{levels.map(level => <option key={level.index} value={level.index}>{level.height}p</option>)}</select></label>}
      <button className="icon-button" aria-label="Fullscreen" onClick={() => { void video.current?.requestFullscreen?.().catch(() => {}); }}><Maximize size={20} /></button>
    </div>
    {item.next_episode && <div className="up-next"><div><small>UP NEXT</small><strong>{item.next_episode.title}</strong></div>
      <button className="button" onClick={() => navigate(`play/${item.next_episode!.id}`)}><SkipForward size={18} />Next episode</button></div>}
    {ended && !item.next_episode && <div className="button-row player-replay"><button className="button" onClick={() => { if (native) { startingProgress.current = undefined; setNonce(n => n + 1); } else { const node = video.current!; node.currentTime = 0; void node.play(); } setEnded(false); }}><Play size={18} />Watch again</button></div>}
    <p className="player-shortcuts">Space: play / pause . Arrows: seek 10 seconds . M: mute . F: fullscreen</p>
  </Modal>;
}
