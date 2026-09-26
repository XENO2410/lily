"""Screenshots via mss. Saved under <log_dir>/screenshots/ by default."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..logging import get_logger

log = get_logger("screen")


def take_screenshot(target: Path | None = None,
                    log_dir: Path | None = None) -> dict[str, Any]:
    try:
        import mss
        import mss.tools
    except ImportError as e:
        return {"ok": False, "error": f"mss not installed: {e}"}

    if target is None:
        base = (log_dir or Path("./logs")) / "screenshots"
        base.mkdir(parents=True, exist_ok=True)
        target = base / f"{time.strftime('%Y%m%d-%H%M%S')}.png"

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[0]  # all monitors combined
            img = sct.grab(monitor)
            mss.tools.to_png(img.rgb, img.size, output=str(target))
        return {"ok": True, "path": str(target)}
    except Exception as e:
        log.exception("screenshot failed")
        return {"ok": False, "error": str(e)}
