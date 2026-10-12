# AutoYou Game Input for Unreal

This source plugin receives AutoYou's local `autoyou_game_v1` control stream. It uses Unreal's built-in WebSockets and Json modules. Install it under `<YourProject>/Plugins/AutoYouGameInput/`, regenerate project files, then enable the plugin. Unreal Engine 5 is required.

1. On the AutoYou host, enable Remote Desktop video, screen control, and Game mode. Use an admin authenticated request to `http://127.0.0.1:8001/api/webrtc/game-input/connection` to obtain the process token. The stream accepts one local engine subscriber.
2. Start the Unreal process with `AUTOYOU_GAME_STREAM_TOKEN` in its environment. Keep the token out of Blueprints, source control, logs, and game assets. If the admin server uses another port, pass that port to `Start`.
3. From a Game Instance Blueprint, get **AutoYou Game Input Subsystem**, bind **On Frame**, **On Session Reset**, and **On Connection Changed**, then call **Start**. Stop the subsystem when the game no longer needs controls.

`On Frame` includes a `SessionId` for each player and typed fields for touch points, sensor x/y/z, axis names/values, button down/up, mouse movement/buttons, and keyboard action/key/text/state. Touch `Points` is the **complete** active pointer set; replace the player's previous points each time. Values are normalized to 0–1 for touch and absolute mouse coordinates, and −1 to 1 for axes. For `remote_desktop_input`, use `CoordinateMode` to distinguish absolute x/y from relative dx/dy, and `bHasMousePosition` before using x/y on a button frame. Map names and actions to your own Unreal input or gameplay code. The plugin does not inject events into Unreal's global input device.

`On Session Reset` means release **all held controls** for that player: buttons, keys, axes, and touches. It fires on `session_end`, stream backlog reset, disconnect, or Stop. The plugin reconnects with a short backoff while running; reconnect starts with released state. A new process token is needed after an AutoYou server restart.

The plugin has not been compiled on this workstation because Unreal Engine is not installed here. Build it in the Unreal Editor before shipping a game.
