"""Command-line entry point for Jet Music Player.

Exposed as the `jmp` console script (see pyproject.toml) and runnable as
`python -m jet_music_player`. `run.py` at the repo root also delegates here so
the `python run.py` invocation keeps working.

Usage:
    jmp [MUSIC_DIRECTORY]

Running `jmp` launches the player. If no directory is given it defaults to
~/Music.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .app import JetMusicPlayerApp
from .audio import ffmpeg_available


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="jmp",
        description="Jet Music Player — a terminal music player with a live visualizer.",
    )
    parser.add_argument(
        "music_dir",
        nargs="?",
        default="~/Music",
        help="Directory to scan for music (default: ~/Music).",
    )
    args = parser.parse_args(argv)

    music_dir = Path(args.music_dir).expanduser()

    if not music_dir.exists():
        print(f"Music directory not found: {music_dir}")
        print("Pass a path as an argument: jmp /path/to/music")
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

    app = JetMusicPlayerApp(music_dir)
    app.run()


if __name__ == "__main__":
    main()
