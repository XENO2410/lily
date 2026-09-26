"""Master audio volume via pycaw (Windows Core Audio).

All functions return dict payloads with `ok`. If pycaw isn't available (e.g. running
on non-Windows during tests) they degrade to a clear error rather than raising.
"""
from __future__ import annotations

import sys
from typing import Any

from ..logging import get_logger

log = get_logger("volume")

_available = False
_endpoint = None  # cached pycaw endpoint

try:  # pragma: no cover - platform specific
    if sys.platform == "win32":
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
        _available = True
except Exception as e:  # pragma: no cover
    log.warning("pycaw unavailable: %s", e)


def _get_endpoint():  # pragma: no cover - platform specific
    global _endpoint
    if _endpoint is None:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        _endpoint = cast(interface, POINTER(IAudioEndpointVolume))
    return _endpoint


def is_available() -> bool:
    return _available


def get_volume() -> dict[str, Any]:
    if not _available:
        return {"ok": False, "error": "audio control unavailable"}
    try:
        ep = _get_endpoint()
        level = ep.GetMasterVolumeLevelScalar()  # 0.0 - 1.0
        return {"ok": True, "volume": round(level * 100)}
    except Exception as e:
        log.exception("get_volume failed")
        return {"ok": False, "error": str(e)}


def set_volume(level: int) -> dict[str, Any]:
    if not _available:
        return {"ok": False, "error": "audio control unavailable"}
    level = max(0, min(100, int(level)))
    try:
        ep = _get_endpoint()
        ep.SetMasterVolumeLevelScalar(level / 100.0, None)
        # Unmute if we're raising volume from muted state.
        if level > 0 and ep.GetMute():
            ep.SetMute(0, None)
        return {"ok": True, "volume": level}
    except Exception as e:
        log.exception("set_volume failed")
        return {"ok": False, "error": str(e)}


def adjust(delta: int) -> dict[str, Any]:
    cur = get_volume()
    if not cur["ok"]:
        return cur
    return set_volume(int(cur["volume"]) + int(delta))


def mute() -> dict[str, Any]:
    if not _available:
        return {"ok": False, "error": "audio control unavailable"}
    try:
        _get_endpoint().SetMute(1, None)
        return {"ok": True, "muted": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def unmute() -> dict[str, Any]:
    if not _available:
        return {"ok": False, "error": "audio control unavailable"}
    try:
        _get_endpoint().SetMute(0, None)
        return {"ok": True, "muted": False}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def is_muted() -> bool:
    if not _available:
        return False
    try:
        return bool(_get_endpoint().GetMute())
    except Exception:
        return False
