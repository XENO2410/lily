"""Application launch / close / resolve.

Strategy: alias table first (fast, deterministic), then `os.startfile` fallback
which lets Windows resolve names via file associations and Start Menu.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil

from ..logging import get_logger

log = get_logger("apps")


@dataclass(frozen=True)
class AppSpec:
    """A known application. Any of `candidates`, `command`, or `uri` may succeed."""
    key: str
    display: str
    exe_names: tuple[str, ...]              # process names for close/is_running (case-insensitive)
    candidates: tuple[str, ...] = ()        # absolute paths tried in order (env vars expanded)
    command: str | None = None              # PATH-resolvable command (e.g. 'brave')
    uri: str | None = None                  # protocol like 'spotify:'


# Curated aliases. Extend freely.
APPS: dict[str, AppSpec] = {
    "brave": AppSpec(
        key="brave", display="Brave",
        exe_names=("brave.exe",),
        candidates=(
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%PROGRAMFILES(X86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
        ),
        command="brave",
    ),
    "chrome": AppSpec(
        key="chrome", display="Google Chrome",
        exe_names=("chrome.exe",),
        candidates=(
            r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
            r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ),
        command="chrome",
    ),
    "edge": AppSpec(
        key="edge", display="Microsoft Edge",
        exe_names=("msedge.exe",),
        candidates=(
            r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
            r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
        ),
        command="msedge",
    ),
    "firefox": AppSpec(
        key="firefox", display="Firefox",
        exe_names=("firefox.exe",),
        candidates=(
            r"%PROGRAMFILES%\Mozilla Firefox\firefox.exe",
            r"%PROGRAMFILES(X86)%\Mozilla Firefox\firefox.exe",
        ),
        command="firefox",
    ),
    "spotify": AppSpec(
        key="spotify", display="Spotify",
        exe_names=("Spotify.exe",),
        candidates=(
            r"%APPDATA%\Spotify\Spotify.exe",
            r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
        ),
        uri="spotify:",
    ),
    "vscode": AppSpec(
        key="vscode", display="Visual Studio Code",
        exe_names=("Code.exe",),
        candidates=(
            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
            r"%PROGRAMFILES%\Microsoft VS Code\Code.exe",
        ),
        command="code",
    ),
    "notepad": AppSpec(
        key="notepad", display="Notepad",
        exe_names=("notepad.exe",), command="notepad",
    ),
    "calculator": AppSpec(
        key="calculator", display="Calculator",
        exe_names=("Calculator.exe", "CalculatorApp.exe"),
        uri="calculator:",
    ),
    "explorer": AppSpec(
        key="explorer", display="File Explorer",
        exe_names=("explorer.exe",), command="explorer",
    ),
    "settings": AppSpec(
        key="settings", display="Settings",
        exe_names=("SystemSettings.exe",),
        uri="ms-settings:",
    ),
    "terminal": AppSpec(
        key="terminal", display="Windows Terminal",
        exe_names=("WindowsTerminal.exe",), command="wt",
    ),
    "powershell": AppSpec(
        key="powershell", display="PowerShell",
        exe_names=("powershell.exe", "pwsh.exe"), command="powershell",
    ),
    "task_manager": AppSpec(
        key="task_manager", display="Task Manager",
        exe_names=("Taskmgr.exe",), command="taskmgr",
    ),
}

# Additional aliases that map to the same spec.
_EXTRA_ALIASES: dict[str, str] = {
    "code": "vscode",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "browser": "brave",  # user's default, overridable via settings.browser_default
    "google chrome": "chrome",
    "microsoft edge": "edge",
    "file explorer": "explorer",
    "task manager": "task_manager",
    "windows terminal": "terminal",
    "calc": "calculator",
}


def resolve_spec(name: str, default_browser: str = "brave") -> AppSpec | None:
    n = name.strip().lower()
    if n == "browser":
        return APPS.get(default_browser)
    if n in APPS:
        return APPS[n]
    if n in _EXTRA_ALIASES:
        return APPS.get(_EXTRA_ALIASES[n])
    return None


def _expand_first_existing(candidates: tuple[str, ...]) -> Path | None:
    for c in candidates:
        p = Path(os.path.expandvars(c))
        if p.exists():
            return p
    return None


def launch(name: str, *, default_browser: str = "brave",
           extra_args: list[str] | None = None) -> dict[str, object]:
    """Launch an application by alias or raw name.

    Returns a dict describing what was launched, or raises on total failure.
    """
    spec = resolve_spec(name, default_browser=default_browser)
    args = extra_args or []

    if spec is not None:
        exe = _expand_first_existing(spec.candidates)
        if exe is not None:
            subprocess.Popen([str(exe), *args], close_fds=True)
            log.info("launched %s via candidate: %s", spec.display, exe)
            return {"launched": spec.display, "via": "path", "path": str(exe)}

        if spec.command and shutil.which(spec.command):
            subprocess.Popen([spec.command, *args], close_fds=True, shell=False)
            log.info("launched %s via command: %s", spec.display, spec.command)
            return {"launched": spec.display, "via": "command", "command": spec.command}

        if spec.uri:
            os.startfile(spec.uri)  # type: ignore[attr-defined]
            log.info("launched %s via uri: %s", spec.display, spec.uri)
            return {"launched": spec.display, "via": "uri", "uri": spec.uri}

    # Unknown alias — trust Windows to resolve it.
    try:
        os.startfile(name)  # type: ignore[attr-defined]
        log.info("launched %s via os.startfile fallback", name)
        return {"launched": name, "via": "os.startfile"}
    except OSError as e:
        raise RuntimeError(f"could not launch {name!r}: {e}") from e


def close(name: str, *, default_browser: str = "brave") -> dict[str, object]:
    """Terminate every process whose image name matches the alias."""
    spec = resolve_spec(name, default_browser=default_browser)
    target_exes = {e.lower() for e in (spec.exe_names if spec else (f"{name}.exe",))}

    killed = 0
    for proc in psutil.process_iter(attrs=["name", "pid"]):
        try:
            pname = (proc.info.get("name") or "").lower()
            if pname in target_exes:
                proc.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    log.info("close %s: terminated %d processes", name, killed)
    if killed == 0:
        return {"closed": name, "count": 0, "message": "no matching processes were running"}
    return {"closed": spec.display if spec else name, "count": killed}


def is_running(name: str, *, default_browser: str = "brave") -> bool:
    spec = resolve_spec(name, default_browser=default_browser)
    target_exes = {e.lower() for e in (spec.exe_names if spec else (f"{name}.exe",))}
    for proc in psutil.process_iter(attrs=["name"]):
        try:
            if (proc.info.get("name") or "").lower() in target_exes:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def known_apps() -> list[str]:
    return sorted(APPS)
