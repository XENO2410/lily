# Lily

Lily is a local-first personal AI computer assistant for Windows.
This repository is the working codebase — not a chatbot, not a wrapper, an actual
assistant that operates the machine.

> Status: **V0.1** — deterministic Windows control, local STT, push-to-talk activation,
> typed-command console, tray UI, tool registry, permission checks. LLM planner is a
> defined interface but not wired to a model yet.

## What Lily does today (V0.1)

Voice or console, hitting deterministic tools with **no LLM in the path** for common
commands:

- `open <app>` / `launch <app>` — chrome, spotify, vscode, notepad, calculator, …
- `close <app>` — kills the process by image name.
- `volume up`, `volume down`, `volume up by 20`
- `set volume to 40`, `volume 40`
- `mute`, `unmute`
- `minimize`, `maximize`
- `switch to <window>` / `focus <window>`
- `screenshot`

The router strips leading `"Lily,"` / `"Hey Lily,"` addressing before matching.

## Architecture (V0.1)

```
       voice ────►┐
                  ├──► Fast Router ──► Tool Registry ──► Windows Controller
       text ─────►┘             │
                                └── Planner (LLM) ── not loaded in V0.1
```

- `core.router` — regex rules → `Intent(tool, args)`; zero-latency happy path.
- `tools.registry` — typed Pydantic args, permission level, timeout, structured result.
- `computer.*` — volume (pycaw), window mgmt (ctypes/user32), apps, screenshots (mss).
- `voice.*` — `faster-whisper` STT held warm, push-to-talk via pynput, energy VAD.
- `core.agent` — orchestrates router → planner → permissions → tool → memory log.
- `core.memory` — SQLite action log + preferences.
- `ui.console` — typed REPL. `ui.tray` — pystray icon.

See [docs/architecture.md](docs/architecture.md) for the full picture and roadmap.

## Hardware target

- Windows 10/11.
- 16 GB RAM, RTX 3050 laptop (4 GB VRAM), modern laptop CPU.
- STT defaults to `faster-whisper` **`tiny.en`** at **int8 on CPU** — ~40 MB warm,
  no VRAM used. Model unloads after `LILY_STT_WARM_TTL_S` seconds idle.
- TTS defaults to `pyttsx3` (Windows SAPI) — zero downloads, zero models. Piper
  is a config flip when you want a nicer voice (V0.2).
- The LLM planner (V0.3) runs 100% remote via **OpenRouter**, so 0 VRAM/0 local
  RAM for the brain. Set `LILY_STT_DEVICE=cuda` only if you want STT on the GPU.
- **Game mode:** when a fullscreen game is active, warm models unload
  automatically to give you back your RAM/CPU.

## Install

**Recommended (global, no venv needed daily):**

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

Close and reopen the terminal so `pipx` is on `PATH`, then:

```powershell
pipx install --editable D:\Github\lily
```

After this, `lily` is on your PATH forever. Open any PowerShell / Command Prompt
and just type `lily` — no `cd`, no venv, no VS Code.

If you later edit code, refresh the pipx install with:

```powershell
pipx reinstall lily
```

**Alternative (repo-local venv, for development):**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
copy .env.example .env
```

First voice run downloads the whisper model (~40 MB for `tiny.en`) into the HF cache.

## Run

Lily has a few personalities depending on how you invoke her:

```powershell
lily                       # default: start tray + voice + IPC daemon (stays running)
lily start --with-console  # daemon PLUS a typed REPL in this terminal
lily console               # typed REPL only, no voice, no tray
lily do "open notepad"     # send one command (to the daemon if up, standalone if not)
lily status                # is the daemon running? what's it doing?
lily stop                  # ask the daemon to quit
lily logs -n 20            # show the last 20 actions (from SQLite)
```

**Auto-start on Windows login** (Siri-like — always in the tray):

```powershell
.\scripts\install-autostart.ps1              # user Startup folder
.\scripts\install-autostart.ps1 -Method Task # or Task Scheduler
.\scripts\uninstall-autostart.ps1            # remove
```

The autostart shortcut launches `lilyw.exe` (windowless), so no console pops up
at login — she just appears in the tray.

Console session example:

```
Lily console — type a command, or 'exit' to quit.
> volume 25
OK (18ms) {'volume': 25}
> open notepad
OK (42ms) {'launched': 'Notepad', 'via': 'command', 'command': 'notepad'}
> what is the active window
OK (1ms)  {'hwnd': 265232, 'title': 'Untitled - Notepad', 'process': 'notepad.exe'}
> minimize
OK (3ms)  {'action': 'minimize'}
```

## Configuration

All settings are `LILY_*` environment variables (or `.env`). See
[.env.example](.env.example). Nothing hard-coded, nothing secret in the repo.

## Adding a skill

1. Create `src/lily/skills/<name>/skill.py` — define Pydantic arg models, handler
   functions, and a `register_*_skill(registry, deps)` function that calls
   `registry.register(Tool(...))`.
2. Create `src/lily/skills/<name>/routes.py` — call `router.add(...)` for each
   natural-language pattern that should fast-path to the tool.
3. Wire it into `src/lily/main.py`.

See [docs/skills.md](docs/skills.md) for a worked example and the Tool contract.

## Security & permissions

- Every tool declares a `PermissionLevel` (`SAFE`, `SENSITIVE`, `DESTRUCTIVE`).
- `SAFE` runs immediately. `SENSITIVE`/`DESTRUCTIVE` go through `PermissionManager`
  and require a confirmation via the registered `confirm_fn`.
- Lily never executes free-form code. The LLM planner (when introduced) can only
  emit typed calls to registered tools; args are validated by Pydantic before
  execution.
- SQLite action log lives locally under `logs/lily.sqlite3`.

## Troubleshooting

- **`pycaw` import fails** — confirm you're on Windows and `pip install pycaw comtypes`
  completed. Volume tools return a clear "unavailable" error otherwise.
- **No mic input in voice mode** — check `python -c "import sounddevice as sd; print(sd.query_devices())"` and set the default input in Windows Sound settings.
- **STT is slow first time** — the model is downloading. Subsequent starts are warm.
- **Hotkey doWake word ("Hey Lily") via openWakeWord; piper TTS voice.
- **V0.3** — **OpenRouter planner** (GPT-4o) with strict function-calling — turns
  arbitrary natural language into validated tool calls. Router still handles
  common commands with zero LLM cost.
- **V0.4** — Spotify skill (official Web API): search, play, like, playlists.
- **V0.5** — Brave/Chrome skill: profile-aware launch + Playwright for DOM automation.
- **V0.6** — **Playbook cache**: successful LLM plans get compiled into replayable
  fast paths. Repeated commands stop hitting the LLM. This is what makes Lily
  feel like she's *learning* your workflows.
- **V0.7** — UI Automation (`pywinauto` accessibility tree). Lily can now drive
  any Windows app with accessible controls (Outlook, Teams, Excel, Settings…).
- **V0.8** — Vision fallback for apps with custom-drawn UIs — last resort only.
- **V0.9** — User-taught macros: `"Lily, learn a skill called 'start work setup'..."`I).
- **V0.3** — Chrome skill (Playwright + profile-aware launch).
- **V0.4** — Local LLM planner with strict structured outputs.
- **V0.5** — Context-aware pronoun resolution (`"like it"`, `"open the second one"`).
- **V0.6** — UI Automation (accessibility tree) via pywinauto.
- **V0.7** — Vision fallback (last resort).

See [docs/development.md](docs/development.md) for contributor setup and testing.