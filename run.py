"""Entry point for the ASCII music player.

Usage:
    python run.py [MUSIC_DIRECTORY]

If no directory is given, defaults to ~/Music.
"""

import sys
from pathlib import Path

from music_player.app import MusicPlayerApp


def main() -> None:
    music_dir = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path("~/Music").expanduser()

    if not music_dir.exists():
        print(f"Music directory not found: {music_dir}")
        print("Pass a path as an argument: python run.py /path/to/music")
        sys.exit(1)

    app = MusicPlayerApp(music_dir)
    app.run()


if __name__ == "__main__":
    main()
