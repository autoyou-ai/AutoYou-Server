# AutoYou TV implementation contract

This is the requested implementation scope. It is not a completion report.

- Open viewing: home, search, movies, episodes, videos, folders, item details,
  watchlist, watch progress, item and folder share links. No OTP at the player.
- Editor sessions: invitation links created by an OTP-authenticated admin.
  Editors add links, upload media, organize folders and correct metadata.
  Editor invitations never unlock Settings. Admin role sessions alone must also
  retain evidence of OTP verification to enter Settings.
- OTP Settings: reuse AutoYou per-agent or server TOTP verification. Standalone
  mode accepts an explicitly configured TOTP secret. Fail closed when no OTP
  is configured. All new authentication state lives inside the TV data store.
- Persistent library: SQLite catalog, mount roots, virtual folders, source paths,
  title/year/episode parsing, subtitles, technical media details and jobs.
- Media delivery: bounded-memory streaming, full and partial byte requests,
  suffix ranges, HEAD, ETags, invalid-range 416, HLS manifest and segment proxy,
  disconnect cleanup and compatibility with AutoYou's path/data proxy.
- Imports: direct HTTP(S) streams, uploads and optional yt-dlp downloads.
  Website uploads use durable, session-owned 2 MB requests so a forwarding
  transport never needs to hold a full movie request in memory.
  Server fetches reject private/loopback destinations and recheck redirects and
  DNS results. Background jobs show durable status and report missing tools.
- Metadata: automatic OMDb enrichment using title/year or IMDb ID, cached results,
  editor rematching, retryable provider failures and no fabricated IMDb ratings.
- Browser compatibility: native video controls and hls.js; resume, fullscreen,
  keyboard shortcuts, next episode and WebVTT subtitles. Optional FFmpeg creates
  a browser-compatible H.264/AAC rendition for incompatible source formats.
- Interface: match the approved cinematic design; responsive phone navigation,
  semantic controls, visible focus, reduced motion, working empty/error states.
- Native packaging: FastAPI backend in the built-in agent factory and website
  registry, prebuilt frontend assets retained in the package, optional external
  downloader/FFmpeg executables kept separate from the server compiler.
- Verification: isolated pytest media/auth/catalog/job coverage, frontend
  typecheck/build, desktop/mobile browser checks with synthetic media, compile
  integration checks and `python scripts/e2e_validate.py changed`.

All mutable TV data must honor AUTOYOU_TEST_ROOT first. Tests may only use
synthetic credentials, identifiers and temporary media. Existing operator
configuration, live registries and libraries must remain unchanged during QA.
