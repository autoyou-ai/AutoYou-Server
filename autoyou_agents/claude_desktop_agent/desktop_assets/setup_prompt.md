# Claude Desktop local pack setup

You are preparing an AutoYou desktop asset pack for the user's own installed Claude Desktop app. Use the generated setup values below as the requested calibration target.

- Agent: `{{AGENT_NAME}}`
- Platform: `{{PLATFORM}}`
- Installed app version: `{{APP_VERSION}}`
- App appearance: `{{THEME}}`
- Display scale: `{{DISPLAY_SCALE}}`

Use the embedded template and schema included below. If an AutoYou source checkout is open in this workspace, inspect the local agent code read-only to confirm supported target IDs and actions. The installed AutoYou application bundle may be read-only; do not edit it. Create a new writable workspace folder named `autoyou_desktop_asset_setup_claude` containing a root `manifest.json` and only the small PNG sprites that manifest references. Create a ZIP from those files. The user will import that ZIP from AutoYou's local Agents setup screen.

Use UI accessibility information or platform automation APIs first when available. Use window-relative normalized coordinates as fallback. Create a separate pack for each meaningful app version, appearance, and display scale. Pack IDs must be unique. Keep any image path relative to the bundle root, such as `sprites/composer_box.png`.

Only calibrate controls in a blank or synthetic app state. Do not include account names, prompts, responses, conversation history, files, full-window captures, or personal content in the bundle. If a screenshot is needed, crop it locally to the smallest control region before using it as a sprite. Do not upload screenshots or the finished bundle to any service. Do not collect, copy, or automate Claude prompts or outputs as part of this pack-generation task. Leave the app in its original state and ask the user before any test that would submit text or change settings.

Before finishing, validate the JSON against the schema, confirm every referenced sprite exists and is a cropped control image, then create a ZIP with `manifest.json` at the ZIP root. Report the app version, platform, appearance, display scale, included target IDs, and the ZIP path. Do not claim a control works unless the user approved and observed a safe test.
