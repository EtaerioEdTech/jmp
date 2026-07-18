"""Runtime platform detection.

Used to pick the audio backend and decide whether to mount the on-screen touch
controls. Android/Termux is the only special case today; everything else is
treated as "desktop" and keeps the pygame backend and keyboard-only UI.
"""

from __future__ import annotations

import os
import shutil


def is_termux() -> bool:
    """True when running inside Termux on Android.

    Termux sets PREFIX to a path under its private app data dir
    (``/data/data/com.termux/files/usr``) and also exports TERMUX_VERSION.
    We check both so a stray PREFIX on some other system doesn't misfire, and
    fall back to the presence of the Termux-specific bin dir.
    """
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        return True
    if os.environ.get("TERMUX_VERSION"):
        return True
    return os.path.isdir("/data/data/com.termux/files/usr")


def use_touch_controls() -> bool:
    """Whether to mount the on-screen touch d-pad and buttons.

    On by default under Termux (touch-only devices need it); never on desktop.
    ``JMP_TOUCH`` overrides either way: ``JMP_TOUCH=1`` forces it on (handy for
    developing the overlay on a desktop with a mouse), ``JMP_TOUCH=0`` off.
    """
    override = os.environ.get("JMP_TOUCH")
    if override is not None:
        return override.strip() not in ("", "0", "false", "no")
    return is_termux()


def mpv_available() -> bool:
    """True if an ``mpv`` binary is on PATH (the Termux audio backend)."""
    return shutil.which("mpv") is not None


def use_mpv_backend() -> bool:
    """Whether to use the mpv audio backend instead of pygame.

    On under Termux (pygame's SDL audio doesn't work headless there). Can be
    forced with ``JMP_AUDIO=mpv`` / ``JMP_AUDIO=pygame`` for testing on desktop.
    """
    override = os.environ.get("JMP_AUDIO")
    if override is not None:
        return override.strip().lower() == "mpv"
    return is_termux()
