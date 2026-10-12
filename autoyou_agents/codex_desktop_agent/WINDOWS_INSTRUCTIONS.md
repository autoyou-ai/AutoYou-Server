# Codex Desktop Bridge - your Windows playbook

Personal, step-by-step notes for moving the Codex bridge to your Windows machine and firing it
for real work. Everything below is already done on the Mac and committed to the repo, so it
travels with the code.

## What's already built (no need to redo)
- `codex_desktop_agent` has the full 16-tool set (select model/effort/permissions, build prompt
  across messages, attach images, send, queue, copy, **`ask_codex_and_return`** async loop).
- Async return-to-origin is wired through your existing scheduler: `ask_codex_and_return` →
  run-once `desktop_response_return` task → poll-until-idle + copy → delivered to the
  originating client+session. Validated deterministically + the macOS pack is live.
- A reusable skill: `.claude/skills/codex-desktop-bridge/SKILL.md`.

## 1. Install the skill on Windows
Copy the skill folder into your Windows Claude Code skills directory:
```powershell
# from the repo root on Windows
mkdir $HOME\.claude\skills\codex-desktop-bridge -Force
Copy-Item .claude\skills\codex-desktop-bridge\SKILL.md $HOME\.claude\skills\codex-desktop-bridge\
```
Then in Claude Code type `/codex-desktop-bridge` to fire it. (It also lives in the repo's
`.claude/skills/` so it auto-loads when you run Claude Code from the repo.)

> **ChatGPT Codex rename (26.707+):** the app is now titled *ChatGPT Codex* with a
> Model/Effort/Speed/Advanced menu (models 5.6 Sol/Terra/Luna/5.5/5.4/5.4 Mini/5.3 Codex Spark;
> effort Light→Ultra). Detection + the `codex-windows-26.707` pack already handle both the new
> and legacy apps. Rebuild the backend (Nuitka) so the compiled host ships the new logic, and
> live-calibrate the reference-estimated 26.707 menu rows below.

## 2. One-time: calibrate the Windows pack
The composer row is validated; the 26.707 model-menu rows are **reference-estimated** (and the
older Windows pack shipped as bootstrap). Calibrate once - and on Windows you get the better
deal: **image-match works** (no Retina doubling like the Mac).

1. Start the AutoYou server and open Codex desktop (a thread with the composer at the bottom).
2. From the terminal that launched the server:
   ```powershell
   .venv\Scripts\python autoyou_agents\codex_desktop_agent\desktop_assets\tools\capture_sprites.py --version <codex_version>
   ```
   (Find `<codex_version>` from Codex → About, e.g. `26.616`.)
3. In `autoyou_agents\codex_desktop_agent\desktop_assets\manifest.json` add a Windows asset pack
   (copy the macOS pack block, set `platform: "windows"`, `process_names: ["Codex.exe"]`). For
   each target add `expected_image_path` pointing at the captured sprite
   (`windows/<ver>/sprites/<target_id>.png`) + `image_match_confidence: 0.8`, keeping a
   `click_point` fallback. Especially wire **`copy_response_button`** to its sprite - image-match
   tracks it even though its position moves.
4. Regenerate the reference (agent tool `refresh_codex_desktop_llm_reference`).
Details: `autoyou_agents/codex_desktop_agent/desktop_assets/CONTRIBUTING.md`.

## 3. Restart the AI runtime (whenever you change agent/engine/scheduler code)
The rotating **Show Current Code** is the credential (no server password). In the Admin UI
(`http://localhost:8001` → Security → Show Current Code), read the 6-digit code, then:
```powershell
$code = "PUT_THE_6_DIGITS_HERE"
$tok  = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/api/admin/session/verify -ContentType application/json -Body (@{totp_code=$code}|ConvertTo-Json)).token
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/api/ai/restart -Headers @{Authorization="Bearer $tok"}
```
(Adjust `8001` to your `--admin` port.) On Windows you *may* also just relaunch the server -
the Screen-Recording caveat is macOS-only.

## 4. Fire work at Codex
- **Get the answer back automatically:** initiate from an **AutoYou client (WebRTC) or a
  messaging partner** (Telegram/WhatsApp/Signal) - say *"Use Codex to <task> and send me the
  result."* The async return needs a push channel; a bare `POST /api/chat` won't push back
  (answer only lands in session history).
- **Quick local smoke test** (drives Codex, no client):
  ```powershell
  .venv\Scripts\python -c "from pathlib import Path; from autoyou_agents.shared_tools.desktop_app_control import send_prompt_to_desktop_app as s; print(s(Path('autoyou_agents/codex_desktop_agent'), prompt='current usage of model'))"
  ```

## 5. The goal-loop test
From a client/partner: *"Use Codex to report its current usage of model and current model used,
and send me the result."* → you should get an immediate "Sent to Codex…" then the copied answer
when Codex finishes. (Locally you can reproduce the build step with
`add_to_codex_desktop_prompt` ×2 then `ask_codex_and_return("")`.)

## Things to remember
- **macOS vs Windows copy:** Mac uses coordinates for `copy_response_button` (Retina breaks
  image-match); on Windows wire the sprite to `expected_image_path` - far more reliable.
- **`copy_response_button` Y drifts** with response length; the engine scrolls to bottom +
  hover-sweeps. If it ever misses, `ask_codex_and_return` still delivers an "open Codex to read
  it" notice instead of silence.
- **Two Codex layouts:** thread view (bottom composer = the pack) vs new-chat/home (centered).
  Operate in a thread.
- **Persistence:** the deferred return is a real scheduler task (`cron_tasks.json`); it survives
  a runtime restart.
