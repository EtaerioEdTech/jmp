"""Command-line entry point for Jet Music Player.

Exposed as the `jmp` console script (see pyproject.toml) and runnable as
`python -m jet_music_player`. `run.py` at the repo root also delegates here so
the `python run.py` invocation keeps working.

Usage:
    jmp                          # browse ~/Music
    jmp /path/to/music           # browse a different directory
    jmp vampire weekend          # find and play, in ~/Music
    jmp -d /path/to/music radiohead

Running `jmp` bare launches the browser. Trailing words are treated as a
search query: an unambiguous hit starts playing immediately (an artist is
shuffled), anything ambiguous opens the browser filtered to the matches.
If no directory is given it defaults to ~/Music.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .app import JetMusicPlayerApp
from .audio import ffmpeg_available

DEFAULT_MUSIC_DIR = "~/Music"


def _looks_like_path(word: str) -> bool:
    """True when a bare first argument should be read as a directory, not as
    the start of a search query.

    A leading `/`, `~`, or `.` is unambiguously a path — `jmp /bad/path` should
    report a missing directory rather than silently searching for it. An
    existing directory is a path too. Anything else is a query, including names
    that merely contain a slash ("AC/DC"), which would otherwise be unsearchable.
    """
    if word.startswith(("/", "~", ".")):
        return True
    return Path(word).expanduser().is_dir()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="jmp",
        description="Jet Music Player — a terminal music player with a live visualizer.",
        epilog="Examples: jmp | jmp ~/Media/music | jmp vampire weekend",
    )
    parser.add_argument(
        "-d",
        "--dir",
        dest="music_dir",
        default=None,
        metavar="MUSIC_DIRECTORY",
        help=f"Directory to scan for music (default: {DEFAULT_MUSIC_DIR}).",
    )
    parser.add_argument(
        "words",
        nargs="*",
        metavar="QUERY",
        help=(
            "An artist, album, or song to play right away. With no -d and no "
            "query, a single path argument is taken as the music directory."
        ),
    )
    args = parser.parse_args(argv)

    words: list[str] = list(args.words)
    music_dir_arg = args.music_dir

    # Backward compatibility: `jmp /path/to/music` (no -d) still means "browse
    # this directory". Only a lone leading argument that looks like a path is
    # claimed as the directory; everything else stays part of the query.
    if music_dir_arg is None and words and _looks_like_path(words[0]):
        music_dir_arg = words.pop(0)

    music_dir = Path(music_dir_arg or DEFAULT_MUSIC_DIR).expanduser()
    query = " ".join(words).strip()

    if not music_dir.exists():
        print(f"Music directory not found: {music_dir}")
        print("Pass a path as an argument: jmp /path/to/music")
        print("Or search a different library: jmp -d /path/to/music vampire weekend")
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

    app = JetMusicPlayerApp(music_dir, query=query or None)
    app.run()


if __name__ == "__main__":
    main()
