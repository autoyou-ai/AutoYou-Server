# AutoYou TV

A personal movie, episode and video library under `/agent/tv_agent/`, with a
cinematic React interface, responsive phone navigation and a FastAPI backend.
The built frontend is included, so the running native server does not need Node.

## Access

Viewing, search, folders, title links and playback are open. Studio requires an
editor or admin session. OTP-authenticated Settings creates expiring editor and
admin invitations. An invitation with the admin role still cannot open Settings
without verifying OTP. Revoking an invitation ends its sessions immediately.

Settings uses the TV agent's assigned AutoYou 2FA profile, falling back to the
server authenticator. Standalone deployments can set `AUTOYOU_TV_TOTP_SECRET`.
No configured authenticator means Settings stays locked. Settings unlock lasts
30 minutes; role sessions last 8 hours. Invitations can last 1 to 30 days.

## Storage and media

Settings mounts existing server folders. Indexing preserves their directory
tree, recognizes titles/years and `S01E02` episode names, and discovers SRT/VTT
sidecar subtitles. Studio creates virtual folders, uploads media, adds public
HTTP(S) media links and queues downloads. Removing a catalog entry never deletes
the source file. Scans and imports retain status across restart.

The website uploads files in 2 MB requests, with durable offsets, exact chunk
retry checks and resume after a page reload when the same file is selected in
the same editor session. Each file can be up to 20 GB. Cancellation removes the
partial upload; unfinished uploads expire after 24 hours and are cleaned on the
next upload. Technical inspection, artwork and metadata run in background jobs.
The raw `/api/uploads` endpoint remains available for direct HTTP clients.

Media streams use bounded reads and support HEAD, byte ranges, suffix ranges,
ETags and 416 responses. Remote HLS playlists, nested playlists, encryption keys
and segments are rewritten through signed, item-scoped resource URLs. Remote
fetches validate DNS addresses and redirect targets; private destinations are
rejected. AutoYou's existing `/api/stream/` proxy path carries these streams.

Viewer links address the library, a folder or a title. The receiver needs a route
to the host. Paired data-channel and home-network access retain AutoYou's outer
pairing and remote-role policies; TV invitations do not widen those permissions.
Watchlists and playback progress are kept in the viewer's browser, scoped to the
library URL. They are not synchronized across devices.

Optional host programs:

- `yt-dlp`: download from YouTube, Vimeo and archive.org. Public media links from
  other hosts can be streamed directly. Configure `AUTOYOU_TV_YT_DLP` if needed.
- `ffprobe`: duration, codec and resolution inspection. Configure
  `AUTOYOU_TV_FFPROBE` if it is not on PATH.
- `ffmpeg`: create H.264/AAC MP4 browser renditions. Configure
  `AUTOYOU_TV_FFMPEG` if it is not on PATH.

Browser support depends on source codecs. An MKV/HEVC file may require a browser
rendition. Downloads and renditions report unavailable tools through their jobs.
No media download or FFmpeg process runs until an editor requests it.

## IMDb details

Automatic title/year and episode matching uses [OMDb](https://www.omdbapi.com/).
Set its API key in OTP-protected Settings or `AUTOYOU_TV_OMDB_API_KEY`. Saved keys
use AutoYou's OS credential-store APIs, with a service name scoped to this library
and its test root. Saving fails closed when secure credential storage is
unavailable; the host environment variable remains an option. Provider results
are cached for 30 days. Studio
can correct title/year and rematch by IMDb ID. Missing keys, absent matches and
provider failures remain visible rather than inventing film information.

## Development and native packaging

The backend uses AutoYou's existing FastAPI, aiohttp and SQLite runtime. It is a
built-in, opt-in agent with a statically imported factory and managed website
entry on port 8112. Native server packaging compiles its Python modules.

Run from AutoYou-Server:

```powershell
cd autoyou_agents/tv_agent/website/frontend
npm ci
npm run build
cd ../../../..
python -m uvicorn autoyou_agents.tv_agent.website.backend.app:app --host 127.0.0.1 --port 8112
```

Open `http://127.0.0.1:8112/`. In the managed AutoYou installation, enable the
opt-in TV agent and use its registered website. Settings verifies the operator's
authenticator; Studio can then be delegated with an expiring invitation.

`AUTOYOU_TV_DATA_DIR` overrides the normal AutoYou mutable-data location.
`AUTOYOU_TEST_ROOT` always wins over that override. Tests and preview media must
use a temporary test root and synthetic credentials, never operator state.

The TypeScript build uses `frontend/web/`, which native packaging keeps.
Directories named `dist` are excluded by the runtime packager.

Verification on Windows, 2026-10-13:

- 135 focused TV, auth, registry, manifest, website-proxy and data transport tests
  passed. This includes real test-owned HTTP listeners, byte-exact range delivery
  through the path proxy and bounded data-channel response frames.
- All six backend modules compiled with Python 3.12, Nuitka 4.1.3 and GCC 15.2.
  All 45 TV tests then passed with those Windows `.pyd` modules loaded, including
  serving the frontend assets from the compiled module directory. The ADK agent
  factory also compiled successfully.
- The runtime packaging plan includes the TV Python modules and built web assets,
  and excludes node_modules. Frontend typechecking and production build passed.
  HLS support is loaded on demand; Vite reports its larger optional chunk.
- Browser QA checked desktop and phone layouts, decoded local H.264/AAC media,
  resume/seek, automatic episode advance, subtitle tracks, watchlists, folder creation,
  nested browsing, sharing and a 27 MB chunked upload. The
  [hls.js public demo stream](https://github.com/video-dev/hls.js/blob/master/demo/basic-usage.html)
  also decoded through the guarded HLS proxy.
- A real FFmpeg rendition job completed against synthetic media; the original
  source file fingerprint was unchanged.
- `python scripts/e2e_validate.py changed` passed the portable fallback: four
  registry checks passed and one existing opt-in check was skipped.
- All test media, credentials, catalogs and HTTP listeners used isolated test
  roots. Operator configuration and live registries were not changed.

External OMDb credentials and provider accounts were not used in QA; matching
was verified against synthetic provider responses. Full release builds on other
platforms and physical phone testing remain release validation work.

```powershell
python -m pytest AutoYou-Server/tests/agents/public/test_tv_agent.py AutoYou-Server/tests/agents/public/test_tv_agent_proxy.py -q
python scripts/e2e_validate.py changed
```

Run these from the parent AutoYou checkout, whose conftest isolates runtime state.
