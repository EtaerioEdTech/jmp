"""Command-line entry point for the ASCII music player.

Exposed as the `tt` console script (see pyproject.toml) and runnable as
`python -m music_player`. `run.py` at the repo root also delegates here so the
old `python run.py` invocation keeps working.

Usage:
    tt [MUSIC_DIRECTORY]

If no directory is given, defaults to ~/Music.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .app import MusicPlayerApp
from .audio import ffmpeg_available


def main() -> None:
    music_dir = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path("~/Music").expanduser()

    if not music_dir.exists():
        print(f"Music directory not found: {music_dir}")
        print("Pass a path as an argument: tt /path/to/music")
        sys.exit(1)

    if not ffmpeg_available():
        # Not fatal: mp3/ogg/wav/flac still play. But m4a/opus won't, and the
        # visualizer can't analyze compressed audio. Warn before the TUI takes
        # over the screen and hides ordinary stdout.
        print("Warning: ffmpeg not found on PATH.")
        print("  m4a/opus tracks won't play and the visualizer will be limited.")
        print("  Install it: apt install ffmpeg | brew install ffmpeg | winget install ffmpeg")
        print()
        try:
            input("Press Enter to continue anyway, or Ctrl-C to quit... ")
        except (KeyboardInterrupt, EOFError):
            sys.exit(1)

    app = MusicPlayerApp(music_dir)
    app.run()


if __name__ == "__main__":
    main()
