import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Maximize, Play, RotateCcw, SkipForward } from 'lucide-react';
import type Hls from 'hls.js';
import { api, apiUrl, artwork, assetUrl, authHeaders, clock, errorMessage, navigate } from './api';
import { Modal } from './components';
import type { Config, Item, Notify, Progress } from './types';

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

  useEffect(() => {
    const node = video.current!;
    const controller = new AbortController();
    let cancelled = false;
    setError(''); setReady(false); resumed.current = false;
    async function load() {
      try {
        let isHls = item.is_hls;
        if (item.is_remote && !isHls) {
          // Some signed or query-based HLS URLs have no .m3u8 extension.
          const head = await fetch(assetUrl(item.stream_url), { method: 'HEAD', credentials: 'same-origin', headers: authHeaders(), signal: controller.signal });
          isHls = (head.headers.get('Content-Type') || '').toLowerCase().includes('mpegurl');
        }
        if (cancelled) return;
        const source = assetUrl(item.stream_url);
        if (isHls && !node.canPlayType('application/vnd.apple.mpegurl')) {
          const { default: Hls } = await import('hls.js');
          if (cancelled) return;
          if (!Hls.isSupported()) { setError('This browser does not support HLS. Try a current browser or a downloaded MP4 version.'); return; }
          const player = new Hls({ enableWorker: true, backBufferLength: 30 });
          hlsRef.current = player;
          player.on(Hls.Events.MANIFEST_PARSED, () => {
            setLevels(player.levels.map((level, index) => ({ index, height: level.height })));
            void node.play().catch(() => {});
          });
          player.on(Hls.Events.ERROR, (_event, data) => { if (data.fatal) setError('The HLS stream could not be played. Check the source or try again.'); });
          player.attachMedia(node); player.loadSource(source);
        } else node.src = source;
      } catch (error) { if (!cancelled) setError(errorMessage(error)); }
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
      if (resumed.current) record(item.id, node.currentTime, node.duration);
      window.removeEventListener('keydown', keys);
      hlsRef.current?.destroy(); hlsRef.current = null;
      node.pause(); node.removeAttribute('src'); node.load();
    };
  }, [item.id, nonce, record]);

  function loaded() {
    const node = video.current!;
    const old = startingProgress.current;
    if (!resumed.current && old && !old.completed && old.position > 5 && old.position < node.duration - 5) node.currentTime = old.position;
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
    try { await api(`items/${item.id}/transcode`, { method: 'POST', json: {} }); setRenditionMessage('A browser version is being prepared in Studio.'); notify('A browser version is being prepared in Studio.'); }
    catch (error) { setRenditionMessage(errorMessage(error)); }
  }
  return <Modal title={item.title} onClose={() => navigate(`item/${item.id}`)} wide className="player-modal">
    <div className="player-header"><button className="text-button" onClick={() => navigate(`item/${item.id}`)}><ArrowLeft size={19} />Back to title</button>
      <span>{item.series ? `${item.series} . S${item.season} E${item.episode}` : 'Now playing'}</span></div>
    <div className="player-screen">
      <video ref={video} controls autoPlay playsInline preload="metadata" poster={artwork(item)} aria-label={`Video player for ${item.title}`}
        onLoadedMetadata={loaded} onTimeUpdate={() => update()} onPause={() => update(true)} onEnded={complete}
        onError={() => { if (video.current?.error) setError(video.current.error.code === 4 ? 'This video format is not supported by your browser. An editor can create a browser version.' : 'The video could not be loaded. Check its source and try again.'); }}>
        {(item.subtitles || []).map(track => <track key={track.id} kind="subtitles" src={apiUrl(`subtitles/${item.id}/${track.id}`)} label={track.label} srcLang={track.language} />)}
      </video>
      {error && <div className="player-error" role="alert"><p>{error}</p><div className="button-row">
        <button className="button" onClick={() => { setEnded(false); setNonce(n => n + 1); }}><RotateCcw size={18} />Try again</button>
        {config.auth.role !== 'viewer' && !item.is_remote && config.capabilities.transcode && <button className="button primary" onClick={rendition}>Create browser version</button>}
      </div>{renditionMessage && <p role="status">{renditionMessage}</p>}</div>}
    </div>
    <div className="player-tools"><span className="muted">{ready ? clock(position) : 'Preparing your stream...'}</span>
      <label>Speed<select aria-label="Playback speed" defaultValue="1" onChange={event => { if (video.current) video.current.playbackRate = Number(event.target.value); }}>
        {[.5, .75, 1, 1.25, 1.5, 2].map(rate => <option key={rate} value={rate}>{rate}x</option>)}</select></label>
      {!!levels.length && <label>Quality<select aria-label="Video quality" value={quality} onChange={event => { const level = Number(event.target.value); setQuality(level); if (hlsRef.current) hlsRef.current.currentLevel = level; }}>
        <option value={-1}>Auto</option>{levels.map(level => <option key={level.index} value={level.index}>{level.height}p</option>)}</select></label>}
      <button className="icon-button" aria-label="Fullscreen" onClick={() => { void video.current?.requestFullscreen?.().catch(() => {}); }}><Maximize size={20} /></button>
    </div>
    {item.next_episode && <div className="up-next"><div><small>UP NEXT</small><strong>{item.next_episode.title}</strong></div>
      <button className="button" onClick={() => navigate(`play/${item.next_episode!.id}`)}><SkipForward size={18} />Next episode</button></div>}
    {ended && !item.next_episode && <div className="button-row player-replay"><button className="button" onClick={() => { const node = video.current!; node.currentTime = 0; void node.play(); setEnded(false); }}><Play size={18} />Watch again</button></div>}
    <p className="player-shortcuts">Space: play / pause . Arrows: seek 10 seconds . M: mute . F: fullscreen</p>
  </Modal>;
}
