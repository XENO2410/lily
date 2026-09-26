"""Central configuration. All values come from environment / .env with LILY_ prefix."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class LilySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LILY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    name: str = "Lily"
    log_level: str = "INFO"
    log_dir: Path = Path("./logs")

    stt_engine: Literal["faster-whisper", "none"] = "faster-whisper"
    stt_model: str = "base.en"
    stt_device: Literal["auto", "cpu", "cuda"] = "cpu"
    stt_compute_type: str = "int8"
    stt_language: str = "en"
    # 0 = never unload. Model stays warm for instant response.
    stt_warm_ttl_s: int = 0
    # Biases whisper toward Lily's command vocabulary. Kills "note pad" / "vs code" errors
    # and pushes it toward "Lily" over common mishearings like "Haley".
    stt_initial_prompt: str = (
        "You are transcribing voice commands for Lily, a Windows assistant. "
        "Every command is addressed to Lily. The name is spelled L-I-L-Y. "
        "Examples: 'Lily, open notepad.' 'Lily, close Chrome.' "
        "'Lily, volume up.' 'Lily, what time is it?' "
        "'Lily, what is the date today?' 'Lily, take a screenshot.' "
        "'Lily, mute.' 'Hey Lily, open Brave.' 'Lily, open Spotify.' "
        "'Lily, minimize.' 'Lily, focus VSCode.'"
    )

    # 'wake' = always listening, triggered by "Hey Lily" prefix (Siri-style).
    # 'hotkey' = push-to-talk (hold hotkey to speak).
    activation: Literal["wake", "hotkey"] = "wake"
    hotkey: str = "<ctrl>+<alt>+<space>"
    wake_word: str = "lily"
    # VAD tuning for continuous mode.
    wake_silence_ms: int = 1000          # trailing silence that ends an utterance
    wake_min_speech_ms: int = 300        # ignore anything shorter than this
    wake_energy_floor: float = 200.0     # min RMS to consider speech (adapts to noise)

    tts_engine: Literal["pyttsx3", "piper", "none"] = "pyttsx3"
    tts_voice: str = ""
    tts_gender: Literal["female", "male", "any"] = "female"
    tts_rate: int = 190
    tts_enabled: bool = True

    # Floating "Hey Lily" orb: appears when she's listening/thinking/speaking.
    orb_enabled: bool = True

    llm_provider: Literal["none", "openrouter", "ollama", "openai_compat"] = "none"
    llm_model: str = "openai/gpt-4o-mini"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_timeout_s: int = 30

    confirm_destructive: bool = True

    spotify_enabled: bool = False
    browser_default: Literal["brave", "chrome", "edge", "firefox"] = "brave"
    tray_enabled: bool = True

    game_mode: Literal["auto", "off", "on"] = "auto"

    ipc_pipe: str = "lily"


def find_env_file() -> Path | None:
    """Locate .env regardless of the current working directory.

    Search order:
      1. $LILY_ENV_FILE   (explicit override)
      2. ./.env           (current working directory — original behavior)
      3. Repo-adjacent .env at <src/../..>/.env  (works for editable pipx installs)
      4. %APPDATA%\\Lily\\.env      (Windows user config)
         or ~/.config/lily/.env     (POSIX)
      5. ~/.lily.env      (user home dotfile)

    Returns the first path that exists, or None.
    """
    # 1. Explicit
    explicit = os.environ.get("LILY_ENV_FILE")
    if explicit:
        p = Path(explicit).expanduser()
        if p.exists():
            return p

    # 2. cwd
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env

    # 3. Repo-adjacent (this file: src/lily/config.py → repo root two levels up)
    try:
        repo_env = Path(__file__).resolve().parents[2] / ".env"
        if repo_env.exists():
            return repo_env
    except (OSError, IndexError):
        pass

    # 4. User config directory
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            p = Path(appdata) / "Lily" / ".env"
            if p.exists():
                return p
    else:
        p = Path.home() / ".config" / "lily" / ".env"
        if p.exists():
            return p

    # 5. Home dotfile
    home_env = Path.home() / ".lily.env"
    if home_env.exists():
        return home_env

    return None


def load_settings() -> LilySettings:
    env_path = find_env_file()
    if env_path is not None:
        settings = LilySettings(_env_file=str(env_path))
        # Emit via print (logging isn't configured yet at settings-load time).
        print(f"lily: loaded config from {env_path}", file=sys.stderr)
    else:
        settings = LilySettings()
        print("lily: no .env found — using defaults + environment variables",
              file=sys.stderr)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings

