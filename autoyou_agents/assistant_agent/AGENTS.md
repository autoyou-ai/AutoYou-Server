# Assistant Agent — Agent Design, Skin & Customizable Pattern Specification

> **For Coding Agents & Developers (Antigravity, Claude, ChatGPT, Cursor)**
>
> This document specifies the mechanical body hierarchy, spring-damper physics,
> emote state machine, audio reactivity, and skin pack format for the AutoYou
> Assistant Agent across the web frontend, iOS client (`AutoYouApp`), and
> Android client (`com.autoyou.app`).
>
> Coding agents generating skins, patterns, animations, or alternative chassis
> MUST adhere to the schema and constraints defined herein.

---

## 1. Robot Anatomy & Mechanical Hierarchy

The Assistant Robot is modeled as a 2D mechanical puppet with articulated joints,
spring-damper physics, and dynamic facial optics:

```
[bot] (root container, origin: 200, 512, scaled by squash sx, sy)
  ├── [shadow] (ellipse at cx:200, cy:522, rx:96, ry:12, opacity: 0.28)
  ├── [legs] (leg-left and leg-right pivot independently at 171,437 and 229,437 for steps and idle motion)
  └── [upper] (leans around hip pivot: 200, 448; translates vertically bodyY)
        ├── [arm-left] (rotates around shoulder ball center: 116, 322)
        │     └── [forearm-left] (rotates around elbow ball center: 116, 388)
        ├── [arm-right] (rotates around shoulder ball center: 284, 322)
        │     └── [forearm-right] (rotates around elbow ball center: 284, 388)
        ├── [torso] (chest plate cx:200 cy:378 rx:82 ry:86)
        │     ├── [reactor] (cx:200, cy:365, radius 31 outer, 26.5 middle, 23 inner)
        │     └── [core] (pulsing SVG emblem: cloud arch, hexagon, circuit legs, glowing nodes)
        └── [head] (rotates around neck pivot: 200, 292; translates headX, headY, tilts)
              ├── [antenna] (curved arc from 140,84 to 260,84) & [antenna-glow]
              ├── [ear-l] (cx:57, cy:176) & [ear-r] (cx:343, cy:176) (audio-reactive glow)
              ├── [visor] (rounded rect: x:88, y:100, w:224, h:148, rx:70)
              └── [face] (clipped to visor bounds)
                    ├── [brow-l] (translate: 155, 126, rotate: browLTilt)
                    ├── [brow-r] (translate: 245, 126, rotate: browRTilt)
                    ├── [eye-l] (translate: 155+gazeX, 168+gazeY; ring/happy/sleep layers)
                    ├── [eye-r] (translate: 245+gazeX, 168+gazeY; ring/happy/sleep layers)
                    ├── [mouth-smile] (quadratic bezier smile path)
                    ├── [mouth-open] (dynamic speech-reactive mouth opening)
                    ├── [eq] (7-band audio equalizer on visor during command listening)
                    ├── [think] (3 orbiting cyan dots during inference thinking)
                    └── [zzz] (sleeping zzz characters floating above head when off)
```

---

## 2. Physics & Dynamics Equations

Every joint is governed by an underdamped or critically damped spring equation:

$$a = k \cdot (x_{\text{target}} - x) - c \cdot v$$
$$v \gets v + a \cdot \Delta t$$
$$x \gets x + v \cdot \Delta t$$

where:
- $k$: Spring stiffness ($5.0 \le k \le 500.0$)
- $c$: Damping coefficient ($0.05 \le c \le 3.0$)
- $x$: Current joint position/angle
- $v$: Joint angular or linear velocity
- $x_{\text{target}}$: Target position derived from active state, audio, and gaze

### Default Joint Tuning Constants

| Joint | Variable | Target Source | Default $k$ | Default $c$ | Range Clamp |
|---|---|---|---|---|---|
| Lean | `lean` | State + audio + pointer | 38.0 | 0.72 | $[-12^\circ, 12^\circ]$ |
| Body Vertical | `bodyY` | Breathing + perk + sleep | 95.0 | 0.55 | $[-20\text{px}, 20\text{px}]$ |
| Squash | `squash` | Tap boop impulse ($v \mathrel{-}= 2.6$) | 240.0 | 0.30 | $[-5\text{px}, 5\text{px}]$ |
| Head Horizontal | `headX` | Pointer gaze + error shake | 110.0 | 0.62 | $[-15\text{px}, 15\text{px}]$ |
| Head Vertical | `headY` | Breathing + speech lift | 120.0 | 0.50 | $[-25\text{px}, 25\text{px}]$ |
| Head Tilt | `tilt` | Thinking (+8°) / Command (-4°) | 55.0 | 0.60 | $[-25^\circ, 25^\circ]$ |
| Arm Left | `armL` | Resting (+6°) / Wave / Talk | 80.0 | 0.55 | $[-10^\circ, 142^\circ]$ |
| Arm Right | `armR` | Resting (-6°) / Wave (-104°) | 80.0 | 0.55 | $[-142^\circ, 10^\circ]$ |
| Forearm Left | `foreL` | Arm inertia + talk gesture | 150.0 | 0.38 | $[-22^\circ, 78^\circ]$ |
| Forearm Right | `foreR` | Arm inertia + wave gesture | 150.0 | 0.38 | $[-78^\circ, 22^\circ]$ |
| Brow Y (L/R) | `browLY`, `browRY` | Speech lift + attention | 110.0 | 0.60 | $[-20\text{px}, 15\text{px}]$ |
| Brow Tilt (L/R) | `browLTilt`, `browRTilt` | Happy (-4°/+4°) / Thinking | 85.0 | 0.60 | $[-15^\circ, 15^\circ]$ |

### Inertia Trailing
Forearms trail upper arm velocity so rapid arm movements feel natural:
$$\text{target}_{\text{foreL}} \gets \text{target}_{\text{foreL}} - v_{\text{armL}} \cdot 0.05$$
$$\text{target}_{\text{foreR}} \gets \text{target}_{\text{foreR}} - v_{\text{armR}} \cdot 0.05$$

---

## 3. Emotes, States & Behavioral Modes

| State (`phase`) | Visual Cues | Brow / Eye State | Mouth / Visor | Arms |
|---|---|---|---|---|
| `off` / `paused` | Dim halo, dim core, sleep eyes, floating 'z's | Brow relaxed down, sleep eyelids (opacity: 1) | Smile dimmed | Folded inward gently |
| `waiting` | Ambient antenna glow (18%), blinking active | Ring eyes awake, saccadic gaze wandering | Subtle smile | Idle breathing sway |
| `command` (listening) | Ears glow with mic level, antenna bright (100%) | Eyes scale up +12% with mic loudness | 7-band Equalizer animated on visor | Arms attentive (R: -16°, ForeR: -30°) |
| `thinking` | Antenna pulses at 6 Hz (sine), gaze looks up-right | Brow asymmetric tilt (L: -8°, R: -5°) | 3 cyan dots orbiting horizontally | Arm L raised to chin (L: 22°, ForeL: 48°) |
| `speaking` | Core & antenna glow 40-100% with voice RMS | Happy eye arc blinks periodically | Dynamic open mouth tracking voice energy | Conversational gesturing with arm/forearm springs |
| `greeting` (wave) | Quick perk up ($S_{\text{perk}} \gets 1.0$) | Happy curved eyes, lifted brows | Bright smile | Right arm waves beside head ($R_{\text{arm}} \approx -104^\circ$, $R_{\text{fore}} \approx -55^\circ \pm 18^\circ$) |
| `error` | Head rapid horizontal shake ($f = 30\text{ Hz}$) | Furrowed brows (inward tilt +9°/-9°) | Inverted frown | Arms drop down |

---

## 4. Skin & Customization Pack Schema (`.ayskin.json`)

A skin pack is a portable JSON document (file extension `.ayskin` or `.ayskin.json`).

```json
{
  "format": "autoyou-assistant-skin-v1",
  "skin": {
    "id": "neon_cyber",
    "name": "Neon Cyber",
    "author": "AutoYou AI",
    "version": "1.0.0",
    "description": "High-contrast dark synthwave cyberpunk aesthetic.",
    "palette": {
      "shell_gradient": ["#1e2230", "#141724", "#0c0e17"],
      "shell_head_gradient": ["#1e2230", "#141724", "#0b0d15"],
      "visor_gradient": ["#1f0a29", "#0e0317", "#05010a"],
      "accent_gradient": ["#ff2a85", "#d9146c", "#850541"],
      "glow_primary": "#00f0ff",
      "glow_secondary": "#ff2a85",
      "core_color": "#00f0ff",
      "halo_color": "#ff2a85"
    },
    "physics": {
      "springs": {
        "lean": { "k": 38.0, "c": 0.72 },
        "bodyY": { "k": 95.0, "c": 0.55 },
        "squash": { "k": 240.0, "c": 0.30 },
        "headX": { "k": 110.0, "c": 0.62 },
        "headY": { "k": 120.0, "c": 0.50 },
        "tilt": { "k": 55.0, "c": 0.60 },
        "armL": { "k": 80.0, "c": 0.55 },
        "armR": { "k": 80.0, "c": 0.55 },
        "foreL": { "k": 150.0, "c": 0.38 },
        "foreR": { "k": 150.0, "c": 0.38 },
        "browLY": { "k": 110.0, "c": 0.60 },
        "browRY": { "k": 110.0, "c": 0.60 },
        "browLTilt": { "k": 85.0, "c": 0.60 },
        "browRTilt": { "k": 85.0, "c": 0.60 }
      },
      "squash_impulse": -2.6,
      "gaze_sensitivity": 1.0,
      "breath_speed": 1.9,
      "breath_amplitude": 2.5
    },
    "emotes": {
      "eye_style": "neon",
      "mouth_style": "glow_line",
      "blink_interval_seconds": 3.8,
      "greeting_wave_enabled": true
    },
    "audio_reactivity": {
      "mic_equalizer_bands": 7,
      "speaker_mouth_tracking": true,
      "ear_glow_enabled": true
    },
    "assets": {
      "custom_svg_paths": {
        "reactor_core": "M -28 23.5 C -48 23.5 -60 10.5 -60 -3.5..."
      }
    }
  }
}
```

---

## 5. Built-in Preset Catalog

| Preset ID | Name | Theme Description | Primary Glow | Accent Gradient |
|---|---|---|---|---|
| `autoyou_classic` | AutoYou Classic | Pearl white chassis, cobalt joints, cyan core | `#3fd8ff` | `#5b86ff` → `#1b2f8c` |
| `neon_cyber` | Neon Cyber | Obsidian shell, hot magenta accents, electric cyan optics | `#00f0ff` | `#ff2a85` → `#850541` |
| `solar_amber` | Solar Amber | Warm golden titanium chassis, solar flare amber optics | `#ffd24d` | `#ffaa00` → `#993d00` |
| `emerald_tech` | Emerald Tech | Brushed titanium shell, glowing matrix emerald optics | `#34d399` | `#10b981` → `#047857` |
| `cosmic_violet` | Cosmic Violet | Astral violet chassis, luminous ultraviolet core energy | `#c084fc` | `#a855f7` → `#581c87` |
| `stealth_dark` | Stealth Dark | Matte graphite composite, tactical crimson optics | `#f87171` | `#ef4444` → `#991b1b` |

---

## 6. Server Wire API Contract

The AutoYou-Server `assistant_agent` website backend provides the following endpoints:

- `GET /api/assistant/skin`: Fetch current active skin configuration.
- `POST /api/assistant/skin`: Update active skin configuration (JSON body).
- `GET /api/assistant/skin/presets`: Return list of all preset skins with preview swatches.
- `POST /api/assistant/skin/preset/{preset_id}`: Apply one of the preset skins.
- `GET /api/assistant/skin/export`: Download active skin pack as JSON attachment.
- `POST /api/assistant/skin/import`: Upload and apply skin pack JSON.
- `POST /api/assistant/skin/reset`: Reset active skin to `autoyou_classic`.

Both iOS and Android apps communicate with these endpoints over local proxy/WebRTC
when connected, and persist their own local offline copy when offline.

## Portable worlds and native voice interaction

The optional `skin.world` field travels inside the existing skin pack. Its version-1
schema and validation live in `shared/assistant_world.py`, `AssistantWorld.swift`,
and Android `AssistantWorld.kt`. A world has sky/ground colors, robot size, optional
`robot_glow` and `robot_shell` colors (omitting them restores the skin palette), idle,
professional, wandering and sound switches, and up to 96 objects. Objects use
built-in shapes or bounded SVG path coordinates, named animations/reactions and
locally synthesized sounds. Packs contain no executable scripts or remote assets.
A world is limited to 128 KiB; a skin pack remains limited to 256 KiB. Changing an
appearance preset preserves the current world; importing a pack replaces it.

The canonical web renderer supplies all native graphics through
`scripts/sync_assistant_renderer.py`. It follows touch or pointer position, supports
11 idle gestures (0–10), walks between bounded positions and debounces repeated
presses. Professional mode reduces movement; it does not change authentication.
Animation suspends when hidden and runs at a lower rate while idle. Desktop v2
views use this same Assistant website. Native world views can expand into a
landscape presentation with chat alongside the scene.

Native `@world` requests use the selected on-device or API/network model and validate
its complete JSON before saving. Repair attempts are bounded by a user setting
(default 4, maximum 32 per model); Stop cancels the turn. Invalid output leaves the
previous scene unchanged. Connected Server mode remains a separate conversation;
select an on-device or API/network model to edit the phone's world, or edit the
computer's world through `assistant_get_world`/`assistant_update_world` and download
its skin. Appearance packs and trained wake-word models can be uploaded or downloaded
through the paired device transport using the existing editor/admin authorization.

Native interaction mode is local to the device: tap, trained wake word, hold to talk,
or off (microphone button only). Tapped input has adjustable silence processing;
hold input finishes on release. Wake input uses the trained acoustic model and the
configured silence interval. An open native Assistant suppresses computer background
wake capture; calls and Lobby microphone use retain priority. Closing or backgrounding
the native Assistant releases its capture before computer listening resumes. Skin
transfer never enables the computer's global listening switch. Trained model upload
uses authenticated `POST /api/assistant/model`; viewer writes remain forbidden.
