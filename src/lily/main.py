"""Lily's entry point — parses CLI args and dispatches to daemon/console/voice/do."""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time

from .config import LilySettings, load_settings
from .core.agent import Agent
from .core.memory import Memory
from .core.permissions import PermissionManager
from .core.planner import LLMPlanner
from .core.router import Router
from .core.state import ComputerState
from .ipc import NamedPipeServer, is_running, send_request
from .logging import get_logger, setup_logging
from .skills.windows import register_windows_skill
from .skills.windows.skill import WindowsDeps
from .tools.registry import ToolRegistry
from .tts import make_speaker
from .ui.console import parse_cli_utterance, run_console
from .ui.tray import Tray

log = get_logger("main")


def _init_com_for_thread() -> bool:
    """CoInitialize the current thread so pycaw / SAPI COM calls work.

    pycaw uses comtypes → COM must be initialized in whatever thread runs the
    volume tools. asyncio runs sync tools inline on the loop thread, so we
    initialize once at the entry point.
    """
    if sys.platform != "win32":
        return False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        return True
    except Exception:
        log.exception("main: CoInitialize failed")
        return False


# ── shared setup ──────────────────────────────────────────────────────
def _build_agent(settings: LilySettings, *, confirm_fn=None,
                 speaker=None) -> tuple[Agent, Memory]:
    memory = Memory(settings.log_dir / "lily.sqlite3")
    state = ComputerState()
    permissions = PermissionManager(
        confirm_destructive=settings.confirm_destructive,
        confirm_fn=confirm_fn,
    )
    registry = ToolRegistry()
    router = Router()
    register_windows_skill(registry, router, WindowsDeps(settings=settings))
    if speaker is None:
        speaker = make_speaker(settings)

    # LLM planner: routes what the fast path doesn't cover.
    planner: LLMPlanner | None = None

    async def _permit(tool):
        if permissions.requires_confirmation(tool.permission):
            return await permissions.confirm(
                f"{tool.name} ({tool.permission.value}) — confirm?"
            )
        return True

    if settings.llm_provider not in ("none", "") and settings.llm_api_key:
        planner = LLMPlanner(settings, registry, permit_fn=_permit)
        log.info("planner ready — provider=%s model=%s",
                 settings.llm_provider, settings.llm_model)
    else:
        log.info("planner disabled — set LILY_LLM_PROVIDER + LILY_LLM_API_KEY to enable")

    agent = Agent(
        settings=settings,
        registry=registry,
        router=router,
        memory=memory,
        state=state,
        permissions=permissions,
        speaker=speaker,
        planner=planner,
    )
    log.info("agent ready — %d tools, %d routes, llm=%s",
             len(registry.list_all()), router.size(),
             "on" if planner else "off")
    return agent, memory


def _console_confirm(prompt: str) -> bool:
    try:
        ans = input(f"[confirm] {prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in {"y", "yes"}


def _deny_confirm_with_notice(speaker) -> callable:
    def _deny(prompt: str) -> bool:
        msg = "That needs confirmation. Please run it from the console."
        log.warning("denying %s in headless mode", prompt)
        if speaker is not None:
            speaker.speak(msg)
        return False
    return _deny


# ── subcommands ───────────────────────────────────────────────────────
def cmd_console(settings: LilySettings) -> int:
    setup_logging(settings.log_level, settings.log_dir)
    _init_com_for_thread()
    speaker = make_speaker(settings)
    agent, memory = _build_agent(settings, confirm_fn=_console_confirm, speaker=speaker)
    try:
        asyncio.run(run_console(agent))
    finally:
        speaker.stop()
        memory.close()
    return 0


def cmd_do(settings: LilySettings, utterance: str) -> int:
    setup_logging(settings.log_level, settings.log_dir)
    if not utterance:
        print("usage: lily do \"<command>\"", file=sys.stderr)
        return 2

    # 1) Try the running daemon.
    reply = send_request(settings.ipc_pipe, {"cmd": "do", "utterance": utterance})
    if reply is not None:
        print(json.dumps(reply, indent=2, default=str))
        return 0 if reply.get("ok") else 1

    # 2) Standalone: build agent, run once, exit.
    _init_com_for_thread()
    speaker = make_speaker(settings)
    agent, memory = _build_agent(settings, confirm_fn=_console_confirm, speaker=speaker)
    try:
        result = asyncio.run(agent.handle(utterance, source="cli"))
        payload = {
            "ok": result.ok,
            "tool": result.tool,
            "args": result.args,
            "data": result.result.data,
            "error": result.result.error,
            "duration_ms": round(result.duration_ms, 1),
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0 if result.ok else 1
    finally:
        speaker.stop()
        memory.close()


def cmd_status(settings: LilySettings) -> int:
    reply = send_request(settings.ipc_pipe, {"cmd": "status"})
    if reply is None:
        print("lily is not running")
        return 1
    print(json.dumps(reply, indent=2, default=str))
    return 0


def cmd_stop(settings: LilySettings) -> int:
    reply = send_request(settings.ipc_pipe, {"cmd": "stop"})
    if reply is None:
        print("lily is not running")
        return 1
    print("stop requested:", reply)
    return 0


def cmd_logs(settings: LilySettings, n: int = 20) -> int:
    setup_logging(settings.log_level, settings.log_dir)
    memory = Memory(settings.log_dir / "lily.sqlite3")
    try:
        rows = memory.recent_actions(n)
    finally:
        memory.close()
    for row in reversed(rows):
        ts = time.strftime("%H:%M:%S", time.localtime(row["ts"]))
        ok = "OK " if row["ok"] else "ERR"
        line = f"{ts} {ok} {row['source']:<7} {row['tool'] or '-':<24} {row['utterance']!r}"
        if row["error"]:
            line += f"  err={row['error']}"
        print(line)
    return 0


def cmd_privacy(settings: LilySettings) -> int:
    """What Lily has stored locally, and what leaves this machine."""
    setup_logging(settings.log_level, settings.log_dir)
    db_path = settings.log_dir / "lily.sqlite3"
    memory = Memory(db_path)
    try:
        inv = memory.inventory()
    finally:
        memory.close()
    db_size_kb = db_path.stat().st_size / 1024 if db_path.exists() else 0

    print("Lily privacy summary")
    print("=" * 60)
    print("\nStored locally on this machine (SQLite):")
    print(f"  file:     {db_path}")
    print(f"  size:     {db_size_kb:.1f} KB")
    for table, n in inv.items():
        print(f"  {table:<18} {n} rows")
    screenshots = settings.log_dir / "screenshots"
    if screenshots.exists():
        pngs = list(screenshots.glob("*.png"))
        print(f"  screenshots       {len(pngs)} files in {screenshots}")

    print("\nLeaves this machine (only when router misses + LLM is on):")
    if settings.llm_provider in ("none", ""):
        print(f"  provider:  {settings.llm_provider or 'none'} — nothing sent")
    else:
        print(f"  provider:  {settings.llm_provider}")
        print(f"  endpoint:  {settings.llm_base_url}")
        print(f"  model:     {settings.llm_model}")
        print("  what:      the router-missed utterance text (not audio) plus tool schemas")
        print("  when:      only for utterances the fast router can't resolve")

    print("\nAudio / voice:")
    print(f"  STT:       {settings.stt_engine} model={settings.stt_model} device={settings.stt_device}")
    print("             runs 100% locally after model is cached; no audio uploaded")
    print(f"  TTS:       {settings.tts_engine} — 100% local (Windows SAPI)")

    print("\nControls:")
    print("  lily logs -n 20            show recent actions")
    print("  lily forget -n 100         keep only the most recent 100 actions")
    print("  lily forget --all          delete all action history (asks first)")
    return 0


def cmd_forget(settings: LilySettings, *, keep_last: int | None,
               all_: bool, yes: bool) -> int:
    setup_logging(settings.log_level, settings.log_dir)
    memory = Memory(settings.log_dir / "lily.sqlite3")
    try:
        if all_:
            keep = 0
            prompt = "Delete ALL action history? [y/N] "
        elif keep_last is not None and keep_last >= 0:
            keep = keep_last
            prompt = f"Keep only the most recent {keep} actions and delete the rest? [y/N] "
        else:
            print("usage: lily forget --all   OR   lily forget -n <keep_last>",
                  file=sys.stderr)
            return 2

        if not yes:
            try:
                ans = input(prompt).strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans not in {"y", "yes"}:
                print("cancelled")
                return 1

        deleted = memory.forget_actions(keep_last=keep)
        print(f"deleted {deleted} rows.")
    finally:
        memory.close()
    return 0


def cmd_download(settings: LilySettings) -> int:
    """Pre-cache the STT model so first `lily start` doesn't pause to download.

    Downloads the whisper model to the HuggingFace cache (~/.cache/huggingface/hub).
    Total footprint: tiny.en ≈ 40 MB, base.en ≈ 150 MB, small.en ≈ 500 MB.
    Once cached the model works fully offline.
    """
    setup_logging(settings.log_level, settings.log_dir)
    from .voice.stt import FasterWhisperSTT
    print(f"Downloading whisper model: {settings.stt_model} "
          f"({settings.stt_device}/{settings.stt_compute_type})")
    print("Cache: ~/.cache/huggingface/hub — once here, no network needed.")
    stt = FasterWhisperSTT(settings)
    try:
        stt.preload()
    except Exception as e:
        print(f"Download failed: {e}", file=sys.stderr)
        return 1
    print("Done. Model is cached and ready.")
    return 0


def cmd_start(settings: LilySettings, *, with_console: bool = False,
              no_voice: bool = False, no_tray: bool = False) -> int:
    setup_logging(settings.log_level, settings.log_dir)
    _init_com_for_thread()

    if is_running(settings.ipc_pipe):
        print("lily is already running (found existing IPC pipe).", file=sys.stderr)
        return 1

    speaker = make_speaker(settings)
    # In daemon mode, sensitive/destructive tools require confirmation; without a
    # console we deny by default (with an audible hint). If --with-console is on,
    # we prompt the running REPL.
    confirm_fn = _console_confirm if with_console else _deny_confirm_with_notice(speaker)
    agent, memory = _build_agent(settings, confirm_fn=confirm_fn, speaker=speaker)

    asyncio.run(_run_daemon(
        settings=settings, agent=agent, memory=memory, speaker=speaker,
        with_console=with_console, no_voice=no_voice, no_tray=no_tray,
    ))
    return 0


async def _run_daemon(*, settings: LilySettings, agent: Agent, memory: Memory,
                       speaker, with_console: bool, no_voice: bool, no_tray: bool
                       ) -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    # ── IPC handler ─────────────────────────────────────────────
    async def ipc_handler(request: dict) -> dict:
        cmd = request.get("cmd")
        if cmd == "status":
            return {
                "ok": True,
                "running": True,
                "uptime_s": round(time.time() - started_at, 1),
                "tools": len(agent.registry.list_all()),
                "routes": agent.router.size(),
                "state": agent.state.snapshot(),
            }
        if cmd == "stop":
            loop.call_soon(stop_event.set)
            return {"ok": True, "stopping": True}
        if cmd == "do":
            utterance = request.get("utterance", "")
            result = await agent.handle(utterance, source="ipc")
            return {
                "ok": result.ok, "tool": result.tool, "args": result.args,
                "data": result.result.data, "error": result.result.error,
                "duration_ms": round(result.duration_ms, 1),
            }
        return {"ok": False, "error": f"unknown cmd: {cmd}"}

    started_at = time.time()
    ipc = NamedPipeServer(settings.ipc_pipe, ipc_handler, loop)
    ipc.start()

    # ── Tray ────────────────────────────────────────────────────
    tray = None
    if settings.tray_enabled and not no_tray:
        tray = Tray(
            name=settings.name,
            on_status=lambda: _fmt_status(agent, started_at),
            on_quit=lambda: loop.call_soon_threadsafe(stop_event.set),
        )
        tray.start()

    # ── Orb ─────────────────────────────────────────────────────
    orb = None
    if settings.orb_enabled and not no_tray:
        try:
            from .ui.orb import Orb
            orb = Orb(name=settings.name)
            orb.start()
        except Exception:
            log.exception("orb failed to start; continuing without visual UI")
            orb = None

    # ── Voice ───────────────────────────────────────────────────
    voice_listener = None
    stt = None
    if not no_voice and settings.stt_engine == "faster-whisper":
        from .voice import ContinuousListener, FasterWhisperSTT, VoiceListener

        stt = FasterWhisperSTT(settings)

        # Preload the model in a background thread so the daemon is responsive
        # immediately but the first spoken command doesn't pay the cold-load cost.
        def _preload_stt():
            try:
                stt.preload()
                speaker.speak(f"{settings.name} is listening")
            except Exception:
                log.exception("stt preload failed")
        import threading as _threading
        _threading.Thread(target=_preload_stt, name="lily-preload",
                          daemon=True).start()

        # Wire the orb states to the voice pipeline events.
        def _on_speech_start() -> None:
            if orb is not None:
                orb.show_listening()

        def _on_transcribed(raw_text, extracted_command) -> None:
            if orb is None:
                return
            if extracted_command:
                orb.show_transcript(extracted_command)
            else:
                orb.hide(200)

        async def _run_agent_with_orb(text: str) -> None:
            result = await agent.handle(text, source="voice")
            if orb is None:
                return
            if not result.ok:
                orb.show_error(result.result.error or "that didn't work")
                orb.hide(2500)
                return
            summary = _spoken_summary(result)
            orb.show_speaking(summary)
            orb.hide(2000)

        def on_utt(text: str) -> None:
            loop.create_task(_run_agent_with_orb(text))

        if settings.activation == "wake":
            voice_listener = ContinuousListener(
                settings, stt, on_utt, loop=loop,
                on_speech_start=_on_speech_start,
                on_transcribed=_on_transcribed,
            )
        else:
            voice_listener = VoiceListener(settings, stt, on_utt, loop=loop)
        voice_listener.start()

    # ── Signal handling ─────────────────────────────────────────
    def _sig(*_a):
        loop.call_soon_threadsafe(stop_event.set)

    try:
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)
    except (ValueError, AttributeError):
        pass  # can't set handlers in some contexts

    # ── Optional inline console ─────────────────────────────────
    console_task = None
    if with_console:
        console_task = asyncio.create_task(run_console(agent, stop_event=stop_event))

    log.info("lily is running — Ctrl+C to quit")
    if not no_voice:
        if settings.activation == "wake":
            log.info("say '%s, ...' any time. no hotkey. warming STT in background.",
                     settings.wake_word)
        else:
            log.info("hold %s to talk", settings.hotkey)
    speaker.speak(f"{settings.name} is ready")

    try:
        await stop_event.wait()
    finally:
        log.info("shutting down")
        if console_task is not None:
            console_task.cancel()
        if voice_listener is not None:
            voice_listener.stop()
        if stt is not None:
            stt.unload()
        if orb is not None:
            orb.stop()
        if tray is not None:
            tray.stop()
        ipc.stop()
        speaker.stop()
        memory.close()


def _spoken_summary(agent_result) -> str:
    """Concise phrase for the orb + TTS after a tool runs."""
    data = agent_result.result.data or {}
    tool = agent_result.tool or ""
    # LLM already produced a natural reply — use it directly.
    spoken = data.get("spoken") or data.get("reply")
    if spoken:
        return spoken
    if tool == "apps.launch":
        return f"opened {data.get('launched') or 'app'}"
    if tool == "apps.close":
        n = data.get("count", 0)
        return "closed" if n else "nothing was open"
    if tool in ("windows.volume_set", "windows.volume_adjust"):
        return f"volume {data.get('volume', '?')}"
    if tool == "windows.mute":
        return "muted"
    if tool == "windows.unmute":
        return "unmuted"
    if tool == "windows.screenshot":
        return "screenshot saved"
    if tool == "windows.foreground":
        return data.get("title") or "no window"
    if tool == "system.time":
        return f"it's {data.get('time', '')}"
    if tool == "system.date":
        return f"today is {data.get('date', '')}"
    if tool == "system.day":
        return f"it's {data.get('day', '')}"
    return "done"


def _fmt_status(agent: Agent, started_at: float) -> str:
    s = agent.state.snapshot()
    up = time.time() - started_at
    last = s.get("last_tool") or "-"
    return f"up {up:.0f}s · last: {last} · tools: {len(agent.registry.list_all())}"


# ── entry ─────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lily", description="Lily — local-first Windows assistant")
    sub = p.add_subparsers(dest="cmd")

    start = sub.add_parser("start", help="run the tray+voice+IPC daemon (default)")
    start.add_argument("--with-console", action="store_true",
                       help="also run the typed REPL in this terminal")
    start.add_argument("--no-voice", action="store_true", help="skip STT/hotkey setup")
    start.add_argument("--no-tray", action="store_true", help="skip system tray icon")

    sub.add_parser("console", help="typed REPL only (no voice, no tray)")

    do = sub.add_parser("do", help="run one command (via daemon if up, else standalone)")
    do.add_argument("utterance", nargs=argparse.REMAINDER,
                    help="the command, e.g. lily do open notepad")

    sub.add_parser("status", help="query the running daemon")
    sub.add_parser("stop", help="ask the running daemon to quit")

    logs = sub.add_parser("logs", help="show recent action log entries")
    logs.add_argument("-n", type=int, default=20)

    sub.add_parser("privacy",
                   help="show what's stored locally + what leaves the machine")

    forget = sub.add_parser("forget",
                            help="delete action history (keep last N or all)")
    forget.add_argument("-n", type=int, default=None, dest="keep_last",
                        help="keep only the most recent N actions")
    forget.add_argument("--all", action="store_true", dest="all_",
                        help="delete ALL action history")
    forget.add_argument("-y", "--yes", action="store_true",
                        help="skip the confirmation prompt")

    sub.add_parser("download",
                   help="pre-cache the whisper model so first run is fast")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()

    cmd = args.cmd or "start"
    if cmd == "start":
        return cmd_start(
            settings,
            with_console=getattr(args, "with_console", False),
            no_voice=getattr(args, "no_voice", False),
            no_tray=getattr(args, "no_tray", False),
        )
    if cmd == "console":
        return cmd_console(settings)
    if cmd == "do":
        utterance = parse_cli_utterance(args.utterance or [])
        return cmd_do(settings, utterance)
    if cmd == "status":
        return cmd_status(settings)
    if cmd == "stop":
        return cmd_stop(settings)
    if cmd == "logs":
        return cmd_logs(settings, args.n)
    if cmd == "download":
        return cmd_download(settings)
    if cmd == "privacy":
        return cmd_privacy(settings)
    if cmd == "forget":
        return cmd_forget(settings,
                          keep_last=getattr(args, "keep_last", None),
                          all_=getattr(args, "all_", False),
                          yes=getattr(args, "yes", False))

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
