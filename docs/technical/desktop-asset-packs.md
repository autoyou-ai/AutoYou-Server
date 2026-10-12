# User-local desktop asset packs

AutoYou can control supported desktop applications through shared automation
code and app-specific asset packs. The shared code is distributed by AutoYou;
each operator creates and installs their own visual calibration pack for the
app version, platform, appearance, and display scale on the machine AutoYou
controls.

## What ships

The source tree and Windows/macOS backend bundles contain only:

- `autoyou_agents/shared_tools/desktop_asset_schema.json`;
- `manifest.template.json` and `setup_prompt.md` for the built-in Codex Desktop
  and Claude Desktop agents;
- the generic desktop control code.

The runtime packager explicitly selects those two desktop setup files. It does
not package `manifest.json`, screenshots, sprites, generated notes, or capture
tools from either agent's `desktop_assets` folder. The source `.gitignore`
also ignores all other direct children of those two asset folders. Public
source export allows the schema, templates, and setup prompts, while rejecting
image files and generated pack data.

## Create and install a pack

1. Open **Agents > Desktop app control assets** in the local AutoYou admin UI.
2. Choose the app, target platform, appearance, and display scale. The app
   version can be left blank for the coding assistant to identify.
3. Prepare the setup prompt and copy it into the user's own Claude, ChatGPT, or
   Codex workspace. AutoYou does not send it to those services.
4. Ask the assistant to use a blank or synthetic app state and create a ZIP
   containing `manifest.json` and only referenced, cropped control sprites.
5. Import that ZIP in the same Agents panel. AutoYou validates it and stores
   only referenced, cleaned PNG sprites and the pack manifest.

The ZIP importer accepts one root manifest or one manifest under a top-level
folder. It rejects path traversal, symbolic links, encrypted entries, duplicate
paths, missing sprites, unsupported image formats, and oversized archives.
Image metadata is stripped during PNG conversion. The importer ignores
unreferenced files.

To migrate an existing local checkout pack, create a ZIP from its legacy
`manifest.json` and the sprite files it references, then import that ZIP in the
Agents panel. The importer copies only referenced sprites into private app data;
the runtime no longer reads user packs directly from the source checkout.

## Local storage and selection

Pack data is written to the current AutoYou user's application data directory:

- Windows: `%LOCALAPPDATA%\AutoYou\autoyou_agents\<agent>\desktop_assets\`;
- macOS: `~/Library/Application Support/AutoYou/autoyou_agents/<agent>/desktop_assets/`;
- Linux: `${XDG_DATA_HOME:-~/.local/share}/AutoYou/autoyou_agents/<agent>/desktop_assets/`.

Pack manifests and sprites are under `user_packs/`; appearance and scale
preferences are in `preferences.json`. Desktop automation screenshots are
stored beside `autoyou_agents`, under `desktop_automation/`, outside the source
checkout. `AUTOYOU_TEST_ROOT` and `AUTOYOU_RUNTIME_ROOT` override these roots.

Development runs and compiled Windows/macOS backends use the same user-data
loader. A code-owned `manifest.template.json` takes precedence over any old
checkout-local `manifest.json`; user packs are merged from application data.
Version bounds, platform, architecture, theme (including `custom:<name>`), and
display scale determine which pack the controller selects. Rebuilding AutoYou
does not remove the installed pack data.

## Rights and safe capture

This design avoids redistributing per-user screenshots and generated sprites.
It cannot guarantee that a user's capture, automation, or use of an app is free
from copyright, trademark, contract, or other restrictions. Users must have the
necessary rights and follow the app provider's current terms. Calibration
should use blank or synthetic UI state, crop only the smallest control region,
and exclude account details, conversation text, files, and other personal
content. AutoYou's setup prompt does not claim that a pack is legally cleared.
