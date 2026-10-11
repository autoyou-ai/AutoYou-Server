# ADK 2.11 and MCP SDK 2.3 validation

Local Windows validation on 2026-10-10:

- Installed ADK 2.11.0, MCP/mcp-types 2.3.0, and httpx2/httpcore2 2.13.1.
  `pip check` reports no broken requirements.
- 205 focused server tests passed: native SQLite persistence and user/app
  isolation, factory URI paths, native abort/history sealing, provider cleanup,
  worker launch, MCP REST cancellation, staged frontend copies, and build contracts.
- 160 repository focused tests passed, with 11 platform skips and 10 subtests:
  MCP modern/legacy clients, real ADK-to-adapter stdio, legal inventory,
  v2 packaging, and the existing Prompts Agent changes.
- The tracked `python scripts/e2e_validate.py changed` regression gate passed:
  195 v2 checks (14 skips), 5 website checks, and 4 registry checks (1 skip).
- The `.llm` governance validator passed for 117 files.
- A fresh staged ADK 2.11 frontend passed sanitization and verification;
  all 108 JavaScript chunks and injected inline script passed Node syntax checks.
  The upstream browser package remained unchanged and patching was idempotent.
- AutoYou process trees were stopped with `taskkill`, then the actual full
  launcher restarted using `run_autoyou.bat --profile full --skip-install --host 0.0.0.0`.
  Existing full-profile dependencies were reused. The browser unlocked the
  admin console and showed loaded services. The AI worker reports a native
  SQLite memory URI, and its dev UI reports ADK 2.11.0.
- Fresh Chromium contexts showed zero Usage Metrics rows, zero consent modals,
  and zero `/config/telemetry` requests, including the options menu.
  The in-app browser transport was unavailable, so browser verification used
  an isolated Playwright Chromium session.
- Configuration/keystore/environment fingerprints remained unchanged. Full
  startup updated the server's agent installation/frontend registries for the
  newly included agent. These startup changes are separate from isolated tests.

Native memory ingests sessions explicitly. Existing history is not automatically
migrated. Real cloud-client accounts, private tunnels, macOS signing, and final
release binaries were not certified by these local checks. Client setup and
later upgrades retain their documented prerequisites.
